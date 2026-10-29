"""The data check (M52): a job that looks over the registers for what may be counted twice or may not be genuine,
so nothing pollutes the plan unnoticed. It runs on its own, every six hours and on demand, with no model.

What it looks for, each a finding with the records side by side, their sources, and the answers that settle it:
- the same thing counted twice across registers: a bill that is also the rent Housing makes, a card or loan's
  minimum on Debts, a pay-later plan's installment;
- bills that look alike (three "YouTube" bills): the same one, or different ones;
- the same purchase from two sources (a bank link and an import) in one account;
- two accounts that look like one;
- a payment matched to two bills;
- mail that may not be from who it says (mail/trust.py), whose waiting items should not be confirmed.

It changes nothing on its own except where the answer is certain: the same email kept in two mailboxes yields its
items once, and a bill a lender's plan already pays steps aside (engine/borrowing.py). Each of those is kept in a
log, shown with its reason. Everything else waits for the owner; "these are different" is remembered, so a finding
the owner settled never comes back."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date
from itertools import combinations
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Bill, Debt, ExtractedFact, Home, MessageRef, Payment, Transaction, utcnow
from ..store import get_setting, set_setting

LOG = "datacheck.log"  # what the app set right on its own, newest first
DECIDED = "datacheck.decided"  # findings the owner settled as "different": never raised again
LAST = "datacheck.last"  # the last run: when, and how many of each kind
TRUSTED = "datacheck.trusted"  # {brand: [domain]}: senders the owner said are genuine
KEEP_LOG = 200

WORDS_KIND = {
    "counted_twice": "Counted twice", "alike_bills": "Bills that look alike", "duplicate_rows": "The same purchase twice",
    "alike_accounts": "Accounts that look alike", "paid_twice": "A payment matched twice", "sender": "Mail that may not be genuine",
}


def _words(name: str) -> frozenset[str]:
    from .needsyou import _words as words

    return words(name)


def alike(a: str, b: str) -> bool:
    """Every telling word of one name is in the other ("YouTube" and "YouTube Premium"; not "Rent" and "Power")."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb and (wa <= wb or wb <= wa))


def note_fixed(session: Session, today: date, *, what: str, why: str, sources: list[str]) -> None:
    log = list(get_setting(session, LOG, []) or [])
    log.insert(0, {"on": today.isoformat(), "what": what, "why": why, "sources": sources})
    set_setting(session, LOG, log[:KEEP_LOG])


_learned: tuple[float, dict[str, set[str]]] = (0.0, {})


def learned_senders(session: Session) -> dict[str, set[str]]:
    """Each common sender's domains as the owner's own mail shows them: earlier messages the mail server confirmed,
    and senders the owner marked genuine. Kept two minutes, since a sync judges many messages in a row."""
    global _learned
    if time.monotonic() - _learned[0] < 120:
        return _learned[1]
    from ..mail.trust import brand_of, org_domain

    out: dict[str, set[str]] = defaultdict(set)
    for name, sender in session.exec(select(MessageRef.sender_name, MessageRef.sender).where(MessageRef.trust == "ok")).all():
        brand = brand_of(name or "")
        if brand:
            out[brand].add(org_domain(sender or ""))
    for brand, domains in (get_setting(session, TRUSTED, {}) or {}).items():
        out[brand].update(domains)
    _learned = (time.monotonic(), dict(out))
    return _learned[1]


def _forget_learned() -> None:
    global _learned
    _learned = (0.0, {})


def _item(label: str, detail: str, source: str, link: str = "") -> dict[str, str]:
    return {"label": label, "detail": detail, "source": source, "link": link}


def _bill_item(b: Bill) -> dict[str, str]:
    return _item(b.name, f"${b.amount:,.2f} due on the {b.due_day}{'th' if 4 <= b.due_day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(b.due_day % 10, 'th')}"
                 + (", paid automatically" if b.autopay else ""), f"bill:{b.id}", "/bills")


def findings(session: Session, today: date) -> list[dict[str, Any]]:
    """Everything that looks doubled or doubtful now, minus what the owner settled."""
    decided = set(get_setting(session, DECIDED, []) or [])
    out: list[dict[str, Any]] = []
    bills = [b for b in session.exec(select(Bill).where(Bill.active == True)).all()]  # noqa: E712
    homes = session.exec(select(Home).where(Home.active == True)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712

    # The same thing counted twice across registers
    for b in bills:
        if b.home_id or b.utility_id:
            continue
        for h in homes:
            if alike(b.name, h.name) or (h.payee and (alike(b.name, h.payee) or alike(b.pays_to or "", h.payee))):
                out.append({"key": f"twice:bill:{b.id}:home:{h.id}", "kind": "counted_twice", "severity": "crit",
                            "title": f"“{b.name}” may be the rent for {h.name}",
                            "why": f"Housing already makes the rent for {h.name} and counts it. A bill for the same place counts it again.",
                            "items": [_bill_item(b), _item(f"Rent for {h.name}", f"paid to {h.payee or h.name}, from Housing", f"home:{h.id}", "/housing")],
                            "actions": [{"id": "stop_bill", "label": f"Stop counting “{b.name}”", "question": f"Stop counting “{b.name}”? Housing keeps counting the rent."},
                                        {"id": "different", "label": "They are different"}]})
        for d in debts:
            if alike(b.name, d.name) and d.minimum and abs(b.amount - d.minimum) <= max(5.0, 0.25 * d.minimum):
                out.append({"key": f"twice:bill:{b.id}:debt:{d.id}", "kind": "counted_twice", "severity": "crit",
                            "title": f"“{b.name}” may be the minimum on {d.name}",
                            "why": f"Debts already counts {d.name}'s ${d.minimum:,.2f} minimum each month. A bill of ${b.amount:,.2f} for it counts it again.",
                            "items": [_bill_item(b), _item(d.name, f"${d.minimum:,.2f} minimum, ${d.balance:,.2f} owed, on Debts", f"debt:{d.id}", "/debts")],
                            "actions": [{"id": "stop_bill", "label": f"Stop counting “{b.name}”", "question": f"Stop counting “{b.name}”? Debts keeps counting the minimum."},
                                        {"id": "different", "label": "They are different"}]})

    # Bills that look alike
    groups: list[set[int]] = []
    by_id = {b.id: b for b in bills}
    for a, b in combinations(bills, 2):
        if (a.home_id and a.home_id == b.home_id) or (a.utility_id and a.utility_id == b.utility_id):
            continue  # one home's or one utility's own lines
        if alike(a.name, b.name):
            for g in groups:
                if a.id in g or b.id in g:
                    g.update({a.id, b.id})
                    break
            else:
                groups.append({a.id or 0, b.id or 0})
    for g in groups:
        ids = sorted(g)
        group = [by_id[i] for i in ids]
        out.append({"key": "alike:" + ",".join(map(str, ids)), "kind": "alike_bills", "severity": "warn",
                    "title": f"{len(group)} bills named like “{group[0].name}”",
                    "why": "They may be one bill entered twice, or separate ones (two subscriptions from the same company). Each is counted.",
                    "items": [_bill_item(b) for b in group],
                    "actions": [*({"id": f"keep:{b.id}", "label": f"The same: keep only the ${b.amount:,.2f} one", "question": f"Keep only “{b.name}” at ${b.amount:,.2f} and stop counting the others?"} for b in group),
                                {"id": "different", "label": "They are different"}]})

    # The same purchase from two sources in one account
    rows = session.exec(select(Transaction).where(Transaction.is_ignored == False, Transaction.pending == False)).all()  # noqa: E712
    by_key: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in rows:
        by_key[(t.account_id, round(t.amount, 2))].append(t)
    for txs in by_key.values():
        if len(txs) < 2:
            continue
        txs.sort(key=lambda t: t.date)
        for x, y in zip(txs, txs[1:]):
            if x.source != y.source and abs((y.date - x.date).days) <= 3 and alike(x.merchant, y.merchant):
                out.append({"key": f"rows:{x.id}:{y.id}", "kind": "duplicate_rows", "severity": "crit",
                            "title": f"{x.merchant} ${x.amount:,.2f} may be in twice",
                            "why": f"The same amount at the same place in one account, {abs((y.date - x.date).days)} day(s) apart, from two sources ({x.source} and {y.source}).",
                            "items": [_item(t.merchant, f"{t.date:%a %b} {t.date.day}, ${t.amount:,.2f}, from {t.source}", f"transaction:{t.id}", "/transactions") for t in (x, y)],
                            "actions": [{"id": f"hide:{y.id}", "label": f"The same: hide the {y.source} copy"}, {"id": f"hide:{x.id}", "label": f"The same: hide the {x.source} copy"},
                                        {"id": "different", "label": "Two purchases"}]})

    # Accounts that look alike
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    for a, b in combinations(accounts, 2):
        if a.kind == b.kind and a.connector != b.connector and (alike(a.name, b.name) or (a.institution and a.institution == b.institution and alike(a.name, b.name))):
            out.append({"key": f"accounts:{a.id}:{b.id}", "kind": "alike_accounts", "severity": "warn",
                        "title": f"“{a.name}” and “{b.name}” may be one account",
                        "why": "Two accounts of the same kind with alike names, from different sources. If they are one, its balance and purchases count twice.",
                        "items": [_item(x.name, f"{x.kind}, from {x.connector}" + (f", ${x.balance:,.2f}" if x.balance is not None else ""), f"account:{x.id}", "/accounts") for x in (a, b)],
                        "actions": [{"id": "different", "label": "They are different"}], "fix_on": "/settings/banks"})

    # A payment matched to two bills
    matched: dict[int, list[Payment]] = defaultdict(list)
    for p in session.exec(select(Payment).where(Payment.transaction_id != None)).all():  # noqa: E711
        matched[p.transaction_id].append(p)  # type: ignore[index]
    for tid, ps in matched.items():
        if len(ps) > 1:
            out.append({"key": f"paid:{tid}", "kind": "paid_twice", "severity": "crit", "title": "One payment is matched to two bills",
                        "why": "A single transaction is counted as paying two bills, so one of them may not really be paid.",
                        "items": [_item(by_id[p.bill_id].name if p.bill_id in by_id else "A bill", f"due {p.due_date:%a %b} {p.due_date.day}", f"payment:{p.id}", "/bills") for p in ps],
                        "actions": [{"id": "different", "label": "Both are right"}]})

    # Mail that may not be genuine, with items waiting
    pending = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending", ExtractedFact.message_ref_id != None)).all()  # noqa: E711
    waiting: dict[int, list[ExtractedFact]] = defaultdict(list)
    for f in pending:
        waiting[f.message_ref_id].append(f)  # type: ignore[index]
    if waiting:
        for ref in session.exec(select(MessageRef).where(MessageRef.id.in_(list(waiting)), MessageRef.trust == "crit")).all():  # type: ignore[union-attr]
            out.append({"key": f"sender:{ref.id}", "kind": "sender", "severity": "crit",
                        "title": f"Check the sender: {ref.sender_name or ref.sender}",
                        "why": f"{ref.trust_why} Its {len(waiting[ref.id])} waiting item{'s are' if len(waiting[ref.id]) != 1 else ' is'} kept out of “answer together”.",
                        "items": [_item(ref.subject or "(no subject)", f"from {ref.sender}" + (f", {ref.date:%a %b} {ref.date.day}" if ref.date else ""), f"message:{ref.id}", f"/mail?message={ref.id}")],
                        "actions": [{"id": "discard", "label": "Not genuine: discard what it says", "question": "Discard everything read from this email? Nothing from it is used."},
                                    {"id": "trust", "label": "It is genuine"}]})
    return [f for f in out if f["key"] not in decided]


def same_email_twice(session: Session, today: date) -> int:
    """The same email kept in two mailboxes (or a forward of it) yields its items once: the later copy's items are set
    aside with the reason. Certain, so done on its own and logged."""
    from sqlalchemy import func

    # Only the ids seen more than once are read back as rows: every reference of a five-year mailbox was loaded here
    # on each run (a hundred thousand rows, over a hundred megabytes) to find the handful kept twice.
    doubled = list(session.exec(select(MessageRef.message_id).where(MessageRef.message_id != "").group_by(MessageRef.message_id).having(func.count(MessageRef.id) > 1)).all())
    by_mid: dict[str, list[MessageRef]] = defaultdict(list)
    for k in range(0, len(doubled), 500):
        for r in session.exec(select(MessageRef).where(MessageRef.message_id.in_(doubled[k:k + 500]))).all():  # type: ignore[attr-defined]
            by_mid[r.message_id].append(r)
    aside = 0
    for copies in by_mid.values():
        if len(copies) < 2:
            continue
        copies.sort(key=lambda r: r.id or 0)
        first = copies[0]
        kept = {(f.kind, f.fields) for f in session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == first.id)).all()}
        for later in copies[1:]:
            for f in session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == later.id, ExtractedFact.status == "pending")).all():
                if (f.kind, f.fields) in kept:
                    f.status, f.applied_to, f.resolved_at, f.resolved_by = "rejected", f"set aside: the same email as message {first.id}, kept in two mailboxes", utcnow(), "datacheck"
                    session.add(f)
                    aside += 1
    if aside:
        note_fixed(session, today, what=f"Set aside {aside} waiting item{'s' if aside != 1 else ''} read twice",
                   why="The same email was kept in two mailboxes (or folders), so what it says waited twice. One copy of each stays.", sources=[])
        session.commit()
    return aside


def run(session: Session, today: date) -> dict[str, Any]:
    """One pass: the certain fixes, then the findings, kept for the screen and the health strip."""
    aside = same_email_twice(session, today)
    found = findings(session, today)
    by_kind: dict[str, int] = defaultdict(int)
    for f in found:
        by_kind[f["kind"]] += 1
    last = {"at": utcnow().isoformat(), "count": len(found), "crit": sum(1 for f in found if f["severity"] == "crit"), "by_kind": dict(by_kind), "set_aside": aside}
    set_setting(session, LAST, last)
    return last


def overview(session: Session, today: date) -> dict[str, Any]:
    found = findings(session, today)
    kinds = [{"kind": k, "words": w, "count": sum(1 for f in found if f["kind"] == k)} for k, w in WORDS_KIND.items()]
    crit = sum(1 for f in found if f["severity"] == "crit")
    said = ("Nothing looks doubled or doubtful." if not found else
            f"{len(found)} thing{'s' if len(found) != 1 else ''} to look at" + (f", {crit} that change{'s' if crit == 1 else ''} what the plan counts" if crit else "") + ". Nothing changes until you answer.")
    return {"as_of": today.isoformat(), "findings": found, "kinds": kinds, "said": said, "fixed": (get_setting(session, LOG, []) or [])[:30],
            "last": get_setting(session, LAST, None), "decided": len(get_setting(session, DECIDED, []) or [])}


def answer(session: Session, key: str, action: str, today: date) -> str:
    """The owner's answer to one finding, which must be one the check raises now (no answer acts on anything else)."""
    finding = next((f for f in findings(session, today) if f["key"] == key), None)
    if finding is None:
        raise LookupError("That is no longer raised: it was answered, or the records changed")
    if action not in {a["id"] for a in finding["actions"]}:
        raise ValueError("That answer is not one this finding offers")
    if action == "different":
        decided = list(get_setting(session, DECIDED, []) or [])
        decided.append(key)
        set_setting(session, DECIDED, decided[-2000:])
        return "Kept both. This is not raised again."
    if action == "stop_bill":
        bill = session.get(Bill, int(key.split(":")[2]))
        bill.active = False  # type: ignore[union-attr]
        session.add(bill)
        session.commit()
        return f"Stopped counting “{bill.name}”. The other register keeps counting it."  # type: ignore[union-attr]
    if action.startswith("keep:"):
        keep = int(action.split(":", 1)[1])
        ids = [int(i) for i in key.split(":", 1)[1].split(",")]
        stopped = []
        for i in ids:
            if i != keep:
                b = session.get(Bill, i)
                if b is not None:
                    b.active = False
                    session.add(b)
                    stopped.append(b.name)
        session.commit()
        kept = session.get(Bill, keep)
        return f"Kept “{kept.name}” at ${kept.amount:,.2f}; stopped counting {len(stopped)} other{'s' if len(stopped) != 1 else ''}."  # type: ignore[union-attr]
    if action.startswith("hide:"):
        t = session.get(Transaction, int(action.split(":", 1)[1]))
        t.is_ignored = True  # type: ignore[union-attr]
        t.note = (t.note + " " if t.note else "") + "Hidden on the data check: the same purchase from another source."  # type: ignore[union-attr]
        session.add(t)
        session.commit()
        return f"Hid the {t.source} copy. It stays in the ledger as hidden and can be shown again on Transactions."  # type: ignore[union-attr]
    if action == "discard":
        ref_id = int(key.split(":")[1])
        n = 0
        for f in session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref_id, ExtractedFact.status == "pending")).all():
            f.status, f.applied_to, f.resolved_at, f.resolved_by = "rejected", "discarded on the data check: the sender could not be confirmed", utcnow(), "datacheck"
            session.add(f)
            n += 1
        session.commit()
        return f"Discarded {n} item{'s' if n != 1 else ''} read from it. Nothing from it is used."
    if action == "trust":
        from ..mail.trust import brand_of, org_domain

        ref = session.get(MessageRef, int(key.split(":")[1]))
        brand = brand_of(ref.sender_name or "") if ref else None  # type: ignore[union-attr]
        if ref is not None:
            if brand:
                trusted = {k: list(v) for k, v in (get_setting(session, TRUSTED, {}) or {}).items()}
                trusted.setdefault(brand, [])
                if org_domain(ref.sender) not in trusted[brand]:
                    trusted[brand].append(org_domain(ref.sender))
                set_setting(session, TRUSTED, trusted)
                _forget_learned()
            ref.trust, ref.trust_why = "ok", "You marked this sender as genuine."
            session.add(ref)
            session.commit()
        return "Marked genuine. Mail from this address is trusted from now on."
    raise ValueError("Unknown answer")


def recheck_senders(session: Session, *, limit: int = 300, client_factory: Any = None) -> int:
    """Judge the senders of waiting items that arrived before this check existed: their headers only, read-only."""
    from ..api.mail import mail_connector
    from ..connectors.mail import account_password
    from ..mail.trust import auth_header, judge
    from ..models import MailAccount
    import email
    from email import policy

    pending_refs = {f.message_ref_id for f in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending", ExtractedFact.message_ref_id != None)).all()}  # noqa: E711
    todo = session.exec(select(MessageRef).where(MessageRef.id.in_(list(pending_refs)), MessageRef.trust == "", MessageRef.uid > 0)).all()[:limit]  # type: ignore[union-attr]
    by_box: dict[tuple[int, str], list[MessageRef]] = defaultdict(list)
    for r in todo:
        by_box[(r.account_id, r.folder)].append(r)
    judged = 0
    factory = client_factory or mail_connector().client_factory
    learned = learned_senders(session)
    for (account_id, folder), refs in by_box.items():
        account = session.get(MailAccount, account_id)
        if account is None or not account.active:
            continue
        client = factory(account.host, account.port, account.username, account_password(account), account.use_ssl, account.starttls)
        try:
            client.connect()
            client.select_readonly(folder)
            for r in refs:
                try:
                    raw = client.fetch_headers(r.uid) if hasattr(client, "fetch_headers") else client.fetch_raw(r.uid)
                except Exception:  # noqa: BLE001 - a message that cannot be read is judged next time
                    continue
                msg = email.message_from_bytes(raw, policy=policy.default)
                results, reply_to = auth_header(msg)
                r.trust, r.trust_why = judge(r.sender, r.sender_name, results, reply_to, learned)
                r.trust = r.trust or "unknown"
                r.trust_why = r.trust_why[:300]
                session.add(r)
                judged += 1
        except Exception:  # noqa: BLE001 - a mailbox that is away is tried next run
            continue
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    session.commit()
    _forget_learned()
    return judged
