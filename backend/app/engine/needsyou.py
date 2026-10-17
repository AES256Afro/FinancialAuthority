"""Needs you that empties (M44): what waits for the owner, grouped so alike items are answered together, and each
bill the bank shows no payment for asked plainly: is it paid?

Facts from mail and documents are grouped by kind, each group with how many are complete enough to confirm in one
tap. A bill marked past due comes with the payments that might be it: a transaction near its due date with an
amount close to it, not already matched to another bill. The owner answers: paid (this one, or paid another way),
not yet, or the bill has stopped.

Answering many together follows one plan (plan_many), shown before it is done: a bill's notices count only by the
newest (an older one is set aside, so an old amount never replaces a newer one, in whatever order they are sent),
and a notice more than sixty days old never starts a new bill (it is most likely a service that has stopped). A notice
from someone already counted another way (the landlord on Housing, a card or loan on Debts, an advance or a pay-later
lender on Borrowing) never becomes a bill of its own in bulk, since it would be counted twice; it waits on its card,
which says where it is counted."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Debt, ExtractedFact, Home, InstallmentPlan, MessageRef, Payment, Transaction

GROUP_WORDS = {
    "bill_due": "Bills from your mail", "past_due": "Past-due notices", "paystub": "Paystubs", "statement": "Statements", "installment": "Pay-later plans",
    "advance": "Advances", "price_change": "Price changes", "receipt": "Receipts", "document_terms": "Terms from documents", "export": "Rocket Money exports",
    "investment_balance": "Investment balances", "payment_confirmation": "Payment confirmations",
}
# What a fact needs before it can be confirmed without the owner typing anything
COMPLETE = {
    "bill_due": ("amount", "due_date"), "past_due": ("amount",), "paystub": ("net", "pay_date"), "receipt": ("total",), "investment_balance": ("balance",),
    "statement": (), "price_change": ("amount",), "payment_confirmation": (), "advance": ("amount",),
    "installment": ("schedule", "loan_id"),  # the lender's own schedule and loan number: one plan per loan, however many emails
}
NEVER_IN_BULK = {"export", "document_terms"}  # each needs the owner's eye, one at a time
# Kinds whose confirmation changes nothing in the plan: the email is kept as read, as a record.
RECORD_ONLY = {"statement", "advance", "payment_confirmation"}
AUTOPAY = re.compile(r"\bauto[\s-]?pay\b", re.I)  # a lender's "AutoPay is on" notice changes nothing either
BILL_NOTICES = ("bill_due", "past_due", "price_change")  # each names a bill; only its newest notice counts
OLD_NOTICE_DAYS = 60  # older than this, a notice for a bill not on file starts nothing


def complete(fact: ExtractedFact) -> bool:
    if fact.kind in NEVER_IN_BULK or fact.kind not in COMPLETE:
        return False
    fields = json.loads(fact.fields or "{}")
    if fact.kind == "installment" and fields.get("notice") == "autopay":
        return "autopay" in fields
    if fact.kind == "installment" and fields.get("notice") == "approved":
        return all(fields.get(k) for k in ("installment", "count", "merchant"))
    return all(fields.get(k) not in (None, "", [], {}) for k in COMPLETE[fact.kind])


def notice_name(fields: dict[str, Any], ref: MessageRef | None, override: str | None = None) -> str:
    """The bill a notice is about, as confirming it would name it."""
    return (override or fields.get("name") or fields.get("sender_name") or (ref.sender_name if ref else "") or "Bill").strip()


def _notice_day(fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef | None) -> date | None:
    """When a notice is for: its due date, else the day the message came."""
    try:
        if fields.get("due_date"):
            return date.fromisoformat(str(fields["due_date"])[:10])
    except ValueError:
        pass
    if ref is not None and ref.date is not None:
        return ref.date.date()
    return fact.created_at.date() if fact.created_at else None


# Words that do not tell one payee from another: "Voyager at the Space Center" and "Voyager at Space Center Leasing"
# are the same office.
FILLER = {"the", "at", "of", "and", "a", "an", "llc", "inc", "co", "corp", "company", "ltd", "lp", "leasing", "apartments", "apts",
          "homes", "services", "service", "bill", "billing", "payment", "payments", "statement", "online", "team"}


def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in FILLER)


def elsewhere_index(session: Session) -> list[tuple[frozenset[str], str]]:
    """Who is already counted another way, each with where: (the words of the name, sentence)."""
    out: list[tuple[frozenset[str], str]] = []
    for h in session.exec(select(Home).where(Home.active == True)).all():  # noqa: E712
        for n in (h.name, h.payee):
            out.append((_words(n), f"the rent for {h.name} is counted from Housing"))
    for d in session.exec(select(Debt).where(Debt.active == True)).all():  # noqa: E712
        out.append((_words(d.name), f"{d.name} is counted on Debts"))
    for a in session.exec(select(Account).where(Account.active == True, Account.kind.in_(("card", "loan", "bnpl", "advance")))).all():  # type: ignore[attr-defined]  # noqa: E712
        for n in (a.name, a.institution):
            out.append((_words(n), f"{a.name} is counted on Debts"))
    for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all():  # noqa: E712
        out.append((_words(p.provider), f"pay-later plans with {p.provider} are counted on Borrowing"))
    for v in session.exec(select(Advance)).all():
        out.append((_words(v.provider), f"advances from {v.provider} are counted on Borrowing"))
    return [(w, said) for w, said in out if sum(len(x) for x in w) >= 4]


def counted_elsewhere(index: list[tuple[frozenset[str], str]], name: str) -> str | None:
    """Where a notice's sender is already counted, when it is: every telling word of one name is in the other."""
    words = _words(name)
    if sum(len(x) for x in words) < 4:
        return None
    for known, said in index:
        if known <= words or words <= known:
            return said
    return None


def _names(xs: list[str]) -> str:
    return ", ".join(xs[:4]) + (f" and {len(xs) - 4} more" if len(xs) > 4 else "")


def plan_many(session: Session, facts: list[ExtractedFact], today: date, *, loose: bool = True) -> dict[str, Any]:
    """What confirming these together would do, each one: apply it, set it aside (with why), or leave it for its own
    card. Newest is judged against every notice for the same bill, waiting or already answered, so sending them in
    parts gives the same result as sending them at once."""
    from .recurring import bill_named, merchant_key

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    elsewhere = elsewhere_index(session)
    refs: dict[int, MessageRef | None] = {}

    def ref_of(f: ExtractedFact) -> MessageRef | None:
        if f.message_ref_id is None:
            return None
        if f.message_ref_id not in refs:
            refs[f.message_ref_id] = session.get(MessageRef, f.message_ref_id)
        return refs[f.message_ref_id]

    # The newest day seen for each bill, across every notice the app holds (rejected ones aside).
    newest: dict[str, tuple[date, int]] = {}
    for other in session.exec(select(ExtractedFact).where(ExtractedFact.kind.in_(BILL_NOTICES), ExtractedFact.status != "rejected")).all():  # type: ignore[attr-defined]
        fields = json.loads(other.fields or "{}")
        day = _notice_day(other, fields, ref_of(other))
        key = merchant_key(notice_name(fields, ref_of(other)))
        if day is not None and (key not in newest or (day, other.id or 0) > newest[key]):
            newest[key] = (day, other.id or 0)

    steps: list[dict[str, Any]] = []
    for f in facts:
        fields = json.loads(f.fields or "{}")
        step: dict[str, Any] = {"id": f.id, "kind": f.kind}
        ref = ref_of(f)
        if f.status != "pending":
            step.update(do="skip", why="already answered")
        elif ref is not None and ref.trust == "crit":
            step.update(do="own_card", why=f"check the sender first: {ref.trust_why}")
        elif f.kind == "installment" and fields.get("notice") == "autopay":
            from .borrowing import plan_for_notice

            match = plan_for_notice(session, fields)
            if match is None:
                step.update(do="own_card", why="no plan on file matches this AutoPay notice yet")
            else:
                step.update(do="apply", autopay=f"{match.merchant or match.provider} ${match.installment:,.2f}", lender=str(fields.get("provider") or "the lender"))
        elif f.kind == "installment" and fields.get("notice") == "approved":
            from .borrowing import approval_has_ended, plan_for_notice

            match = plan_for_notice(session, fields)
            label = f"{fields.get('merchant') or 'a store'} ${float(fields.get('installment') or 0):,.2f}"
            if match is not None and abs(match.installment - float(fields.get("installment") or 0)) <= 0.02:
                step.update(do="apply", approved_known=label)
            elif approval_has_ended(fields, today):
                step.update(do="apply", record=True)  # every payment has fallen by now: history, it starts nothing
            else:
                step.update(do="apply", approved_new=label)
        elif f.kind == "installment" and not fields.get("schedule") and not fields.get("notice") and AUTOPAY.search(str(fields.get("subject") or "")):
            step.update(do="apply", record=True)  # an older AutoPay notice read before the dates were taken from it
        elif not complete(f):
            step.update(do="own_card", why="no schedule in it: answer it on its card with the payments left" if f.kind == "installment" else "needs your answer on its own card")
        elif f.kind in RECORD_ONLY:
            step.update(do="apply", record=True)
        elif f.kind == "installment":
            step.update(do="apply", plan=str(fields.get("loan_id")), lender=str(fields.get("provider") or fields.get("sender_name") or "the lender"))
        elif f.kind in BILL_NOTICES:
            name = notice_name(fields, ref_of(f))
            key = merchant_key(name)
            day = _notice_day(f, fields, ref_of(f))
            bill = bill_named(bills, key, loose=loose)
            top = newest.get(key)
            if top is not None and day is not None and (day, f.id or 0) < top:
                step.update(do="set_aside", why=f"an older notice for {name}; the newest one counts", name=name)
            elif bill is None and (day is None or day < today - timedelta(days=OLD_NOTICE_DAYS)):
                step.update(do="set_aside", why=f"over {OLD_NOTICE_DAYS} days old, and no bill named {name} is on file: most likely a service that has stopped", name=name)
            elif bill is None and f.kind != "bill_due":
                step.update(do="own_card", why=f"no bill named {name} is on file to change", name=name)
            elif bill is not None and bill.home_id:
                step.update(do="own_card", why=f"{bill.name} is made from Housing; change the rent there", name=name)
            elif bill is None and (where := counted_elsewhere(elsewhere, name)):
                step.update(do="own_card", why=f"{where}; as a bill it would count twice", name=name)
            else:
                step.update(do="apply", name=bill.name if bill else name, bill_id=bill.id if bill else None, new=bill is None)
        else:
            step.update(do="apply")
        steps.append(step)

    applying = [s for s in steps if s["do"] == "apply"]
    updates = sorted({s["name"] for s in applying if s.get("name") and not s.get("new")})
    adds = sorted({s["name"] for s in applying if s.get("new")})
    aside = sum(1 for s in steps if s["do"] == "set_aside")
    own = sum(1 for s in steps if s["do"] == "own_card")
    records = sum(1 for s in applying if s.get("record"))
    autopays = sorted({s["autopay"] for s in applying if s.get("autopay")})
    known = sum(1 for s in applying if s.get("approved_known"))
    new_plans = sorted({s["approved_new"] for s in applying if s.get("approved_new")})
    plans = sorted({s["plan"] for s in applying if s.get("plan") and s["plan"] != "None"})
    lenders = sorted({s["lender"] for s in applying if s.get("lender")})
    others = sum(1 for s in applying if not any(s.get(k) for k in ("name", "record", "plan", "autopay", "approved_known", "approved_new")))
    parts = []
    if plans:
        parts.append(f"sets up or updates {len(plans)} pay-later plan{'s' if len(plans) != 1 else ''} with {_names(lenders)}, one per loan")
    if known:
        parts.append(f"matches {known} approval email{'s' if known != 1 else ''} to plans on file")
    if new_plans:
        parts.append(f"adds {len(new_plans)} plan{'s' if len(new_plans) != 1 else ''} from approvals, dated by estimate until the lender sets the dates ({_names(new_plans)})")
    if autopays:
        parts.append(f"marks AutoPay on {len(autopays)} plan{'s' if len(autopays) != 1 else ''} with the lender's dates ({_names(autopays)})")
    if records:
        parts.append(f"marks {records} as read (they change nothing in the plan)")
    if updates:
        parts.append(f"brings {len(updates)} bill{'s' if len(updates) != 1 else ''} up to date ({_names(updates)})")
    if adds:
        parts.append(f"adds {len(adds)} new bill{'s' if len(adds) != 1 else ''} ({_names(adds)})")
    if others:
        parts.append(f"keeps {others}")
    if aside:
        parts.append(f"sets aside {aside} older notice{'s' if aside != 1 else ''}")
    if parts:
        said = f"Confirming {'these ' + str(len(steps)) if len(steps) != 1 else 'this one'} " + (", ".join(parts[:-1]) + " and " if len(parts) > 1 else "") + parts[-1] + "."
    else:
        said = "Nothing here can be confirmed together."
    if own:
        said += f" {own} {'stays' if own == 1 else 'stay'} for {'its' if own == 1 else 'their'} own card."
    return {"steps": steps, "apply": len(applying), "set_aside": aside, "own_card": own, "updates": updates, "adds": adds, "said": said}


def payment_candidates(session: Session, payment: Payment, bill: Bill, limit: int = 3) -> list[dict[str, Any]]:
    """Transactions that might be this payment: ten days before the due date to thirty after, within 15% of the bill
    (40% for a bill whose amount varies), not already matched to another bill, nearest amount and date first."""
    used = {p.transaction_id for p in session.exec(select(Payment).where(Payment.transaction_id != None)).all()}  # noqa: E711
    tolerance = max(2.0, bill.amount * (0.4 if bill.amount_mode == "estimate" else 0.15))
    rows = session.exec(select(Transaction, Account).join(Account, Account.id == Transaction.account_id).where(
        Transaction.date >= payment.due_date - timedelta(days=10), Transaction.date <= payment.due_date + timedelta(days=30),
        Transaction.amount > 0, Transaction.is_transfer == False, Transaction.pending == False)).all()  # noqa: E712
    found = []
    for t, a in rows:
        if t.id in used or abs(t.amount - bill.amount) > tolerance:
            continue
        found.append((abs(t.amount - bill.amount), abs((t.date - payment.due_date).days), t, a))
    found.sort(key=lambda x: (round(x[0], 2), x[1]))
    return [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": t.amount, "account": a.name, "source": f"transaction:{t.id}"} for _, _, t, a in found[:limit]]


def triage(session: Session, today: date) -> dict[str, Any]:
    from ..api.mail import list_facts_rows

    pending = list_facts_rows(session, "pending", 1000)
    by_id = {f.id: f for f in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()}
    groups: dict[str, dict[str, Any]] = {}
    for row in pending:
        fact = by_id.get(row["id"])
        if fact is None:
            continue
        g = groups.setdefault(fact.kind, {"kind": fact.kind, "words": GROUP_WORDS.get(fact.kind, fact.kind.replace("_", " ").capitalize()), "items": [], "complete": 0,
                                          "one_at_a_time": fact.kind in NEVER_IN_BULK})
        row["complete"] = complete(fact)
        g["complete"] += 1 if row["complete"] else 0
        g["items"].append(row)
    out_groups = sorted(groups.values(), key=lambda g: (g["one_at_a_time"], -len(g["items"])))
    for g in out_groups:
        g["count"] = len(g["items"])

    bills = {b.id: b for b in session.exec(select(Bill).where(Bill.active == True)).all()}  # noqa: E712
    past = []
    for p in session.exec(select(Payment).where(Payment.status == "past_due", Payment.due_date >= today - timedelta(days=90)).order_by(Payment.due_date)).all():
        b = bills.get(p.bill_id)
        if b is None:
            continue
        past.append({"payment_id": p.id, "bill_id": b.id, "name": b.name, "amount": p.amount, "due_date": p.due_date.isoformat(), "days_late": (today - p.due_date).days,
                     "autopay": b.autopay, "candidates": payment_candidates(session, p, b), "source": f"payment:{p.id}"})
    read = sum(g["count"] for g in out_groups)
    total = read + len(past)
    parts = ([f"{read} from your mail and documents"] if read else []) + ([f"{len(past)} bill{'s' if len(past) != 1 else ''} with no payment seen"] if past else [])
    return {
        "as_of": today.isoformat(), "groups": out_groups, "past_due": past, "total": total,
        "word": ("Nothing from your mail, documents or bills waits." if total == 0 else
                 f"{total} {'thing waits' if total == 1 else 'things wait'} here: {' and '.join(parts)}."),
    }


def answer_payment(session: Session, payment: Payment, answer: str, today: date, *, transaction_id: int | None = None, paid_on: date | None = None) -> str:
    """The owner's answer about a bill marked past due. Returns what changed, in words."""
    bill = session.get(Bill, payment.bill_id)
    if answer == "paid":
        tx = session.get(Transaction, transaction_id) if transaction_id else None
        if transaction_id and tx is None:
            raise ValueError("No such transaction")
        if paid_on and paid_on > today:
            raise ValueError("The day you paid cannot be after today")
        payment.status = "paid"
        payment.transaction_id = tx.id if tx else None
        payment.paid_date = tx.date if tx else (paid_on or today)
        payment.amount = tx.amount if tx else payment.amount
        payment.evidence = f"transaction:{tx.id}" if tx else "the owner: paid another way"
        session.add(payment)
        session.commit()
        return f"{bill.name if bill else 'The bill'} for {payment.due_date:%a %b} {payment.due_date.day} is marked paid" + (f" with the {tx.merchant} payment on {tx.date:%a %b} {tx.date.day}." if tx else ".")
    if answer == "not_yet":
        payment.evidence = f"the owner, {today.isoformat()}: not paid yet"
        session.add(payment)
        session.commit()
        return "Kept as not paid yet. Pay it, or ask the biller for a new date; it stays here until a payment is seen."
    if answer == "stopped":
        if bill is not None:
            bill.ends_on = payment.due_date - timedelta(days=1)
            session.add(bill)
        payment.status, payment.evidence = "missed", "the owner: this bill has stopped"
        session.add(payment)
        session.commit()
        return f"{bill.name if bill else 'The bill'} is no longer counted from {payment.due_date:%a %b} {payment.due_date.day} on."
    raise ValueError("The answer is paid, not_yet or stopped")
