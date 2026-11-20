"""Confirm less, trust more (M62): the rules that answer what the mail says, so the owner is asked only what is a
real question. One path per kind of fact:

- a receipt whose total, day and store match one ledger row is kept against that row;
- a statement within a dollar of what the bank link read for the same account, about the same day, is kept as a reading;
- a payment confirmation that a paid bill period already explains is filed;
- a bill notice older than the newest from the same biller, at the same domain, expires;
- an advance or an installment the ledger's own rows already show is linked to them;
- a sender the owner answered the same way three times has what it says filed, where that changes nothing.

Mail is written by anyone. So a rule acts only on a message the trust check calls "ok" that came through the owner's
own mailbox, and only where the fact changes nothing the plan counts or an independent source (a ledger row, a bank
link's balance, a paid period) already says the same. No rule makes a bill, a plan, a paystub or an account, moves a
bill's amount, or writes an account's balance. A receipt adds a file to a row; a statement adds one reading, within a
dollar of the bank's, to the balance's history and never to the balance.

Earned auto-confirm is the one path with no second source, so it only ever files: a notice that says exactly what the
bill on file says, or a kind that changes nothing when it is confirmed. What the sender usually says comes from the
owner's own answers and never from a rule's, so a figure cannot walk upward through what the rule itself let by, and a
figure well outside it waits. Only the owner's answers earn: a second person's, a key's, an app's and a rule's do not.

Nothing is hidden: each answer carries its rule (resolved_by rule:<name>) and the reason (rule_why), is listed on
Settings › Rules, and can be undone. Undone, the fact waits again and no rule touches it again, and undoing an earned
one turns that sender off. A rule switched off stops at once. Only the owner turns a rule or a sender on or off."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from ..mail.trust import org_domain
from ..models import Account, Advance, BalanceSnapshot, Bill, Document, ExtractedFact, InstallmentPlan, MailAccount, MessageRef, Payment, Transaction
from ..store import get_setting, set_setting

SWITCHES = "autokeep.switches"  # {rule: false}: what the owner turned off. Not in store.DEFAULTS, so PUT /settings cannot write it
SENDERS_OFF = "autokeep.senders_off"  # {address: why}: the senders switched off, by the owner or by an undo
TOLD = "autokeep.told"  # the senders the owner has been told earned their way, so it is said once

RECEIPT = "receipt_matches_row"
STATEMENT = "statement_matches_balance"
CONFIRMATION = "confirmation_explained"
OLDER = "older_bill_notice"
BORROWING = "borrowing_in_ledger"
EARNED = "earned_sender"
RULES: dict[str, dict[str, str]] = {
    RECEIPT: {"title": "Receipts that match one purchase", "does": "A receipt whose total, day and store match exactly one row in your ledger, within a day, is kept with that row."},
    STATEMENT: {"title": "Statements that match the bank", "does": "A statement within a dollar of what your bank link read for the same account, about the same day, is kept as a balance reading. Your balance itself stays the bank's."},
    CONFIRMATION: {"title": "Payment confirmations already explained", "does": "A payment confirmation for a bill period already marked paid is filed. It changes nothing."},
    OLDER: {"title": "Bill notices with a newer one", "does": "A bill notice older than a newer one from the same biller and the same domain expires. A past-due notice expires only for a newer past-due notice."},
    BORROWING: {"title": "Advances and pay-later the ledger shows", "does": "An advance or an installment your ledger's own rows already show is linked to them. It changes nothing."},
    EARNED: {"title": "Senders you answered three times", "does": "After you answer a sender the same way three times, what it says is filed where that changes nothing: never a new bill, and never an amount well outside what it usually says."},
}

WITHIN_DOLLAR = 1.0
RECEIPT_DAYS = 1
READING_DAYS = 3  # a bank link's reading this close to a statement's day is about the same day
PAID_DAYS = 7
LEDGER_DAYS = 5
FUTURE_GRACE = 1  # a message dated past tomorrow says nothing about when it came
EARN_AFTER = 3
USUAL_OVER = 12  # how many of the owner's answers say what a sender usually says
USUAL_SLACK = 0.25  # how far past them a figure may go and still be the usual
USUAL_FLOOR = 5.0  # a small figure may move this much in dollars
OLD_DAYS = 60
LIVE_CONNECTORS = ("plaid", "teller")
READ = "read: changes nothing in the plan"
SKIP_MARK = "skipped: older than two months, batch "
BILL_NOTICES = ("bill_due", "past_due", "price_change")
EARNED_KINDS = ("statement", "advance", "payment_confirmation", "bill_due", "price_change", "installment")


@dataclass
class Act:
    """What one rule would do with one fact."""

    rule: str
    status: str  # confirmed | applied | rejected
    applied_to: str
    why: str
    keep_receipt: int | None = None  # the row a receipt goes with
    reading: tuple[int, float, date] | None = None  # the account, the figure and the day of a reading


def switches(session: Session) -> dict[str, bool]:
    off = get_setting(session, SWITCHES, {}) or {}
    return {name: off.get(name) is not False for name in RULES}


def senders_off(session: Session) -> dict[str, str]:
    return dict(get_setting(session, SENDERS_OFF, {}) or {})


def set_rule(session: Session, name: str, on: bool) -> None:
    """The caller has checked this is the owner."""
    if name not in RULES:
        raise ValueError("There is no such rule")
    off = dict(get_setting(session, SWITCHES, {}) or {})
    if on:
        off.pop(name, None)
    else:
        off[name] = False
    set_setting(session, SWITCHES, off)


def set_sender(session: Session, sender: str, on: bool, why: str = "you switched it off") -> None:
    """The caller has checked this is the owner, or is turning a sender off."""
    address = _address(sender)
    if not address:
        raise ValueError("Say which sender")
    off = senders_off(session)
    if on:
        off.pop(address, None)
    else:
        off[address] = why[:120]
    set_setting(session, SENDERS_OFF, off)


def rule_of(fact: ExtractedFact) -> str | None:
    """The rule that answered a fact, if a named one did."""
    kind, _, name = (fact.resolved_by or "").partition(":")
    return name if kind == "rule" and name in RULES else None


def _address(sender: str) -> str:
    return (sender or "").strip().lower()


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _message_day(ref: MessageRef | None, today: date) -> date | None:
    """When a message came, never a date it merely says something is due. A date past tomorrow says nothing: it is the
    sender's own header, and one in the year 2099 must not make every other notice the older."""
    if ref is None or ref.date is None:
        return None
    day = ref.date.date()
    return day if day <= today + timedelta(days=FUTURE_GRACE) else None


def _said(kind: str, fields: dict[str, Any]) -> float | None:
    """The figure a fact states."""
    return _money(fields.get("total" if kind == "receipt" else "amount"))


def _named(a: str, b: str) -> bool:
    """A store's name against a ledger row's: one begins with the other, on at least three letters."""
    from .recurring import merchant_key

    x, y = merchant_key(a), merchant_key(b)
    return bool(len(x) >= 3 and len(y) >= 3 and (x.startswith(y[:6]) or y.startswith(x[:6])))


class Look:
    """What a pass over the waiting facts reads once."""

    def __init__(self, session: Session, today: date) -> None:
        self.session, self.today = session, today
        self.mine = set(session.exec(select(MailAccount.id).where(MailAccount.person_id == None)).all())  # noqa: E711
        self._refs: dict[int, MessageRef | None] = {}
        self.bills = list(session.exec(select(Bill).where(Bill.active == True)).all())  # noqa: E712
        self._newest: dict[tuple[str, str], tuple[date, int]] | None = None
        self._newest_past_due: dict[tuple[str, str], tuple[date, int]] | None = None
        self._earned: dict[tuple[str, str], dict[str, Any]] | None = None
        self._claims: dict[int, list[int]] | None = None
        self._rows: tuple[list[dict], list[dict]] | None = None

    def ref(self, fact: ExtractedFact) -> MessageRef | None:
        rid = fact.message_ref_id
        if rid is None:
            return None
        if rid not in self._refs:
            self._refs[rid] = self.session.get(MessageRef, rid)
        return self._refs[rid]

    def vouched(self, ref: MessageRef | None) -> bool:
        """Only this lets a rule act: the trust check said "ok" (not "warn", "crit", "unknown" or nothing), and it came
        through a mailbox of the owner's. A message with no reference at all has nothing to vouch for it."""
        return ref is not None and ref.trust == "ok" and ref.account_id in self.mine

    def ledger(self) -> tuple[list[dict], list[dict]]:
        if self._rows is None:
            from .borrowing import _rows

            self._rows = _rows(self.session, self.today)
        return self._rows

    def _notices(self) -> None:
        from .needsyou import notice_name
        from .recurring import merchant_key

        newest: dict[tuple[str, str], tuple[date, int]] = {}
        past_due: dict[tuple[str, str], tuple[date, int]] = {}
        rows = self.session.exec(select(ExtractedFact).where(ExtractedFact.kind.in_(BILL_NOTICES), ExtractedFact.status != "rejected")).all()  # type: ignore[attr-defined]
        for fact in rows:
            ref = self.ref(fact)
            day = _message_day(ref, self.today)
            if day is None or not self.vouched(ref):
                continue  # a notice nobody can vouch for never makes a real one the older
            key = _biller(notice_name(_fields(fact), ref), ref, merchant_key)
            if key not in newest or (day, fact.id or 0) > newest[key]:
                newest[key] = (day, fact.id or 0)
            if fact.kind == "past_due" and (key not in past_due or (day, fact.id or 0) > past_due[key]):
                past_due[key] = (day, fact.id or 0)
        self._newest, self._newest_past_due = newest, past_due

    def superseded(self, fact: ExtractedFact, ref: MessageRef | None) -> date | None:
        """The day of the newer notice that makes this one the older, or None."""
        from .needsyou import notice_name
        from .recurring import merchant_key

        if self._newest is None:
            self._notices()
        day = _message_day(ref, self.today)
        if fact.kind not in BILL_NOTICES or day is None or ref is None or not self.vouched(ref):
            return None
        table = self._newest_past_due if fact.kind == "past_due" else self._newest
        top = (table or {}).get(_biller(notice_name(_fields(fact), ref), ref, merchant_key))
        return top[0] if top is not None and top[0] > day else None

    def earned(self) -> dict[tuple[str, str], dict[str, Any]]:
        if self._earned is None:
            self._earned = earned_senders(self.session)
        return self._earned

    def receipt_claims(self) -> dict[int, list[int]]:
        """Every waiting vouched receipt, by the one row it points to: two receipts for one row wait for a person."""
        if self._claims is None:
            claims: dict[int, list[int]] = defaultdict(list)
            rows = self.session.exec(select(ExtractedFact).where(ExtractedFact.kind == "receipt", ExtractedFact.status == "pending")).all()
            for fact in rows:
                if not self.vouched(self.ref(fact)):
                    continue
                row = _receipt_row(self.session, _fields(fact))
                if row is not None:
                    claims[row.id or 0].append(fact.id or 0)
            self._claims = dict(claims)
        return self._claims


def _fields(fact: ExtractedFact) -> dict[str, Any]:
    fields = json.loads(fact.fields or "{}")
    return fields if isinstance(fields, dict) else {}


def _biller(name: str, ref: MessageRef, key_of: Any) -> tuple[str, str]:
    """A biller is its name and the domain that vouched for the mail: a domain somebody else registered and signed, under
    the same name, expires nothing of the real one's."""
    return key_of(name), org_domain(ref.sender or "")


def _receipt_row(session: Session, fields: dict[str, Any]) -> Transaction | None:
    """The one posted purchase this receipt can be: its total, within a day, under its store's name."""
    total, bought = _money(fields.get("total")), _day(fields.get("bought_on"))
    merchant = str(fields.get("merchant") or "")
    if total is None or bought is None or not merchant:
        return None
    rows = session.exec(select(Transaction).where(Transaction.date >= bought - timedelta(days=RECEIPT_DAYS), Transaction.date <= bought + timedelta(days=RECEIPT_DAYS),
                                                  Transaction.amount > 0, Transaction.is_transfer == False, Transaction.is_ignored == False, Transaction.pending == False)).all()  # noqa: E712
    named = [t for t in rows if abs(t.amount - total) <= 0.01 and _named(merchant, t.merchant)]
    return named[0] if len(named) == 1 else None


def _receipt(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    row = _receipt_row(look.session, fields)
    if row is None or look.receipt_claims().get(row.id or 0, []) != [fact.id]:
        return None
    if look.session.exec(select(Document.id).where(Document.kind == "receipt", Document.attach_to == f"transaction:{row.id}")).first() is not None:
        return None  # that row has its receipt
    return Act(RECEIPT, "applied", "", f"Its total ${row.amount:,.2f} and store match the {row.merchant} row on {row.date.isoformat()}, the only one that fits.", keep_receipt=row.id)


def _reading(session: Session, account: Account, day: date) -> float | None:
    """What the bank link read for an account nearest a day, if it read anything close enough to be about that day."""
    best: tuple[int, float] | None = None
    if account.balance is not None and account.balance_at is not None:
        gap = abs((account.balance_at.date() - day).days)
        if gap <= READING_DAYS:
            best = (gap, account.balance)
    near = session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == account.id, BalanceSnapshot.date >= day - timedelta(days=READING_DAYS), BalanceSnapshot.date <= day + timedelta(days=READING_DAYS))).all()
    for row in near:
        gap = abs((row.date - day).days)
        if best is None or gap < best[0]:
            best = (gap, row.balance)
    return best[1] if best is not None else None


def _statement(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    from .needsyou import _words

    said, day = _said("statement", fields), _message_day(ref, look.today)
    if said is None or day is None:
        return None
    name = _words(str(fields.get("sender_name") or ref.sender_name or ""))
    if sum(len(w) for w in name) < 4:
        return None
    fits = []
    for account in look.session.exec(select(Account).where(Account.active == True, Account.connector.in_(LIVE_CONNECTORS))).all():  # type: ignore[attr-defined]  # noqa: E712
        words = _words(account.institution) or _words(account.name)
        if not words or not (words <= name or name <= words):
            continue
        reading = _reading(look.session, account, day)
        if reading is not None and abs(abs(reading) - said) <= WITHIN_DOLLAR:
            fits.append((account, reading))
    if len(fits) != 1:
        return None  # none, or two accounts it could be: a person says which
    account, reading = fits[0]
    return Act(STATEMENT, "applied", f"account:{account.id}", f"${said:,.2f} is within a dollar of the ${abs(reading):,.2f} your bank link read for {account.name} about {day.isoformat()}.", reading=(account.id or 0, said, day))


def _confirmation(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    from .datacheck import alike
    from .needsyou import notice_name

    name = notice_name(fields, ref)
    bills = [b for b in look.bills if alike(b.name, name) or (b.pays_to and alike(b.pays_to, name))]
    day = _day(fields.get("paid_date")) or _message_day(ref, look.today)
    if len(bills) != 1 or day is None:
        return None
    said = _said(fact.kind, fields)
    best = None
    for p in look.session.exec(select(Payment).where(Payment.bill_id == bills[0].id, Payment.status == "paid")).all():
        if p.transaction_id is None and not (p.evidence or "").startswith("the owner"):
            continue  # paid by the ledger or the owner's word, or it explains nothing
        gap = abs(((p.paid_date or p.due_date) - day).days)
        if gap <= PAID_DAYS and (said is None or abs(p.amount - said) <= WITHIN_DOLLAR) and (best is None or gap < best[0]):
            best = (gap, p)
    if best is None:
        return None
    p = best[1]
    return Act(CONFIRMATION, "confirmed", f"payment:{p.id}", f"{bills[0].name}'s period due {p.due_date.isoformat()} is already marked paid.")


def _older(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    from .needsyou import notice_name

    newer = look.superseded(fact, ref)
    if newer is None:
        return None
    name = notice_name(fields, ref)
    return Act(OLDER, "rejected", f"set aside: an older notice for {name}; the newest one counts"[:200], f"A newer notice for {name} came on {newer.isoformat()} from the same domain.")


def _advance(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    from .borrowing import ADVANCE_PROVIDERS, provider_of

    provider = provider_of(f"{fields.get('sender_name', '')} {ref.sender_name or ''} {fields.get('sender', '')}", ADVANCE_PROVIDERS)
    said, day = _said("advance", fields), _message_day(ref, look.today)
    if provider is None or said is None or day is None:
        return None
    rows = [r for r in look.ledger()[0] if r["provider"] == provider and abs(abs(r["amount"]) - said) <= WITHIN_DOLLAR and abs((r["date"] - day).days) <= LEDGER_DAYS]
    if not rows:
        return None
    for a in look.session.exec(select(Advance).where(Advance.provider == provider)).all():
        amount_fits = abs(a.amount - said) <= WITHIN_DOLLAR or abs(a.amount + (a.fee or 0.0) - said) <= WITHIN_DOLLAR
        if amount_fits and min(abs((a.taken_on - day).days), abs((a.repay_date - day).days)) <= LEDGER_DAYS:
            return Act(BORROWING, "confirmed", f"advance:{a.id}", f"Your ledger shows {provider} ${abs(rows[0]['amount']):,.2f} on {rows[0]['date'].isoformat()}, and the advance is on Borrowing.")
    return None


def _installment(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef) -> Act | None:
    from .borrowing import INSTALLMENT_PROVIDERS, plan_for_notice, plan_provider

    if fields.get("notice") == "autopay":
        return None  # it says something the plan does not know yet
    day = _message_day(ref, look.today)
    plan = next((p for p in look.session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True, InstallmentPlan.loan_id != "")).all() if fields.get("loan_id") and p.loan_id == str(fields["loan_id"])), None)  # noqa: E712
    if plan is None and _money(fields.get("installment")) is not None:
        plan = plan_for_notice(look.session, fields)
    if plan is None or day is None:
        return None
    if fields.get("schedule") and (plan.schedule_as_of is None or day > plan.schedule_as_of):
        return None  # it is newer than what the plan knows: linking it would lose what it says
    lender = plan_provider(plan)
    if lender not in INSTALLMENT_PROVIDERS.values():
        return None
    rows = [r for r in look.ledger()[1] if r["provider"] == lender and abs(r["amount"] - plan.installment) <= WITHIN_DOLLAR and abs((r["date"] - day).days) <= 35]
    if not rows:
        return None
    return Act(BORROWING, "confirmed", f"plan:{plan.id}", f"Your ledger shows the {lender} payment of ${rows[0]['amount']:,.2f} on {rows[0]['date'].isoformat()}, and the plan is on Borrowing.")


def _answer_of(fact: ExtractedFact) -> str:
    """What an answer was, so that answers can be called identical: "no", "read", the bill it went to, or what it made."""
    if fact.status == "rejected":
        return "no"
    head = (fact.applied_to or "").split(":", 1)[0]
    if fact.status == "confirmed" and head in ("", "read"):
        return "read"
    return fact.applied_to if head == "bill" else head


def earned_senders(session: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """Each (sender, kind) whose last three answers from the owner were the same yes, on three different verified
    messages through the owner's own mailbox, with what those answers said as the usual. Worked out from the answers
    themselves, so one taken back no longer counts and a "no" ends it at once. Set aside and skipped are not answers."""
    mine = set(session.exec(select(MailAccount.id).where(MailAccount.person_id == None)).all())  # noqa: E711
    rows = session.exec(select(ExtractedFact, MessageRef).join(MessageRef, ExtractedFact.message_ref_id == MessageRef.id).where(  # type: ignore[arg-type]
        ExtractedFact.resolved_by == "owner", ExtractedFact.kind.in_(EARNED_KINDS), ExtractedFact.status.in_(("applied", "confirmed", "rejected")),  # type: ignore[attr-defined]
        MessageRef.trust == "ok").order_by(ExtractedFact.resolved_at, ExtractedFact.id)).all()
    seen: dict[tuple[str, str], list[tuple[str, int, float | None, str]]] = defaultdict(list)
    for fact, ref in rows:
        if ref.account_id not in mine or (fact.status == "rejected" and fact.applied_to):
            continue
        seen[(_address(ref.sender), fact.kind)].append((_answer_of(fact), ref.id or 0, _said(fact.kind, _fields(fact)), ref.sender_name or ""))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for (sender, kind), answers in seen.items():
        last = answers[-EARN_AFTER:]
        same = {a[0] for a in last}
        if not sender or len(last) < EARN_AFTER or len(same) != 1 or "no" in same or len({a[1] for a in last}) < EARN_AFTER:
            continue
        answer = last[0][0]
        run: list[tuple[str, int, float | None, str]] = []
        for a in reversed(answers):
            if a[0] != answer:
                break
            run.append(a)
        figures = [a[2] for a in run[:USUAL_OVER] if a[2] is not None]
        out[(sender, kind)] = {"sender": sender, "kind": kind, "answer": answer, "answers": len(run), "name": last[-1][3], "low": min(figures) if figures else None, "high": max(figures) if figures else None}
    return out


def within_usual(entry: dict[str, Any], said: float | None) -> bool:
    """A figure against what the sender usually says, a quarter either way (five dollars for a small one). One that says
    a figure when the sender never did, or none when it always has, is not the usual either."""
    low, high = entry.get("low"), entry.get("high")
    if low is None or high is None:
        return said is None
    if said is None:
        return False
    return min(low * (1 - USUAL_SLACK), low - USUAL_FLOOR) <= said <= max(high * (1 + USUAL_SLACK), high + USUAL_FLOOR)


def _earned(look: Look, fact: ExtractedFact, fields: dict[str, Any], ref: MessageRef, off: dict[str, str]) -> Act | None:
    from .borrowing import older_than_listing
    from .needsyou import RECORD_ONLY, notice_name
    from .recurring import bill_named, merchant_key

    sender = _address(ref.sender)
    entry = look.earned().get((sender, fact.kind))
    if entry is None or sender in off:
        return None
    said = _said(fact.kind, fields)
    if not within_usual(entry, said):
        return None
    why = f"You answered {sender} the same way {entry['answers']} times, and this is within what it usually says."
    if fact.kind in RECORD_ONLY and entry["answer"] == "read":
        return Act(EARNED, "confirmed", READ, why)
    if fact.kind == "installment" and entry["answer"] == "plan" and older_than_listing(look.session, fields):
        return Act(EARNED, "confirmed", READ, why)
    if fact.kind in ("bill_due", "price_change") and said is not None:
        bill = bill_named(look.bills, merchant_key(notice_name(fields, ref)))
        due = _day(fields.get("due_date"))
        # Exactly what the bill on file says, or it moves the plan: that is a question for the owner, whatever the amount.
        if bill is not None and entry["answer"] == f"bill:{bill.id}" and not bill.home_id and abs(bill.amount - said) <= 0.005 and (due is None or due.day == bill.due_day):
            return Act(EARNED, "confirmed", f"bill:{bill.id}", f"{why} It says exactly what {bill.name} already says.")
    return None


PATHS = {"receipt": (_receipt,), "statement": (_statement,), "payment_confirmation": (_confirmation,), "bill_due": (_older,), "past_due": (_older,), "price_change": (_older,), "advance": (_advance,), "installment": (_installment,)}
RULE_OF_PATH = {_receipt: RECEIPT, _statement: STATEMENT, _confirmation: CONFIRMATION, _older: OLDER, _advance: BORROWING, _installment: BORROWING}


def judge(look: Look, fact: ExtractedFact, on: dict[str, bool], off: dict[str, str]) -> Act | None:
    """What the rules would do with one waiting fact, or None: it waits for a person."""
    ref = look.ref(fact)
    if fact.status != "pending" or fact.rule_hold or ref is None or not look.vouched(ref):
        return None
    fields = _fields(fact)
    for path in PATHS.get(fact.kind, ()):
        if on[RULE_OF_PATH[path]]:
            act = path(look, fact, fields, ref)
            if act is not None:
                return act
    return _earned(look, fact, fields, ref, off) if on[EARNED] and fact.kind in EARNED_KINDS else None


def _keep_receipt(session: Session, fact: ExtractedFact, ref: MessageRef, fields: dict[str, Any], row_id: int) -> str:
    """The receipt as a file with its row, from the words the app kept: a rule opens no mailbox. Commits."""
    from .documents import save_text

    merchant = str(fields.get("merchant") or "").strip()
    title = f"Receipt · {merchant or 'store'}" + (f" · order {fields['order_no']}" if fields.get("order_no") else "")
    doc = save_text(session, fact.excerpt or title, filename=f"receipt-{fact.id}", title=title, kind="receipt", attach_to=f"transaction:{row_id}",
                    note="Kept by a rule, from the words the app kept. Open it in Mail to see it whole.", merchant=merchant, amount=_money(fields.get("total")),
                    bought_on=_day(fields.get("bought_on")), return_by=_day(fields.get("return_by")), warranty_until=_day(fields.get("warranty_until")), source=f"mail:{ref.id}")
    return f"document:{doc.id}"


def _keep_reading(session: Session, reading: tuple[int, float, date]) -> str:
    """One reading on the statement's day, where that day has none: a day the bank already read stays the bank's."""
    account_id, figure, day = reading
    if session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == account_id, BalanceSnapshot.date == day)).first() is not None:
        return ""
    row = BalanceSnapshot(account_id=account_id, date=day, balance=round(figure, 2))
    session.add(row)
    session.flush()
    return f"reading:{row.id}:{row.balance}"


def apply(session: Session, fact: ExtractedFact, act: Act, ref: MessageRef) -> None:
    from .facts import resolve_fact

    applied_to, made = act.applied_to, ""
    if act.keep_receipt is not None:
        applied_to = made = _keep_receipt(session, fact, ref, _fields(fact), act.keep_receipt)
    elif act.reading is not None:
        made = _keep_reading(session, act.reading)
    resolve_fact(session, fact, act.status, applied_to=applied_to, by=f"rule:{act.rule}")
    fact.made, fact.rule_why = made, act.why[:300]
    session.add(fact)


def run(session: Session, today: date) -> dict[str, Any]:
    """One pass over what waits. Idempotent: what a rule answered has left the queue, and a pass with nothing to do
    writes nothing. One fact that cannot be read never stops the rest, and is counted."""
    on = switches(session)
    off = senders_off(session)
    look = Look(session, today)
    kept: dict[str, int] = defaultdict(int)
    failed = 0
    waiting = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending", ExtractedFact.rule_hold == False, ExtractedFact.message_ref_id != None).order_by(ExtractedFact.id)).all()  # noqa: E711, E712
    for fact in waiting:
        try:
            act = judge(look, fact, on, off)
            if act is None:
                continue
            apply(session, fact, act, look.ref(fact))  # type: ignore[arg-type]
            session.commit()
        except (ValueError, TypeError, KeyError, AttributeError):
            session.rollback()
            failed += 1
            continue
        kept[act.rule] += 1
    _tell_of_earned(session, look)
    return {"kept": dict(kept), "total": sum(kept.values()), "failed": failed}


def _tell_of_earned(session: Session, look: Look) -> None:
    """Once, with no address in it: a push leaves the box. The sender is named on Settings › Rules."""
    now = sorted({sender for sender, _ in look.earned()})
    told = set(get_setting(session, TOLD, []) or [])
    new = [s for s in now if s not in told]
    if new and switches(session)[EARNED]:
        from ..auth import tell_owner

        set_setting(session, TOLD, sorted(told | set(new)))
        tell_owner(f"{len(new)} sender{'s' if len(new) != 1 else ''} earned auto-confirm", "You answered them the same way three times, so what they say is filed where that changes nothing. See them, and switch any off, on Settings › Rules.")


def undo(session: Session, fact: ExtractedFact) -> str:
    """Take out what a rule's answer added. Raises ValueError with the sentence to show where something happened to it
    since. The caller puts the fact back to waiting and commits."""
    from .documents import remove_document

    kind, _, rest = (fact.made or "").partition(":")
    said = "It waits for your answer again, and no rule will answer it."
    if kind == "document" and rest.isdigit():
        doc = session.get(Document, int(rest))
        if doc is not None:
            if not doc.attach_to.startswith("transaction:") or doc.kind != "receipt" or doc.source != f"mail:{fact.message_ref_id}":
                raise ValueError("That receipt was moved or changed since; it stays. Change it on Files.")
            remove_document(session, doc)
        said = "The receipt it kept is gone. " + said
    elif kind == "reading":
        ident, _, value = rest.partition(":")
        row = session.get(BalanceSnapshot, int(ident)) if ident.isdigit() else None
        if row is not None and f"{row.balance}" == value:
            session.delete(row)
            said = "The reading it added is gone. " + said
        elif row is not None:
            said = "Your bank link has read that day since, so its reading stays. " + said
    if rule_of(fact) == EARNED and fact.message_ref_id is not None:
        ref = session.get(MessageRef, fact.message_ref_id)
        if ref is not None:
            set_sender(session, ref.sender, False, "you undid what it filed")
            said += " This sender is off until you turn it back on."
    return said


def _words_of(kind: str) -> str:
    from .needsyou import GROUP_WORDS

    return GROUP_WORDS.get(kind, kind.replace("_", " ").capitalize())


def sender_rows(session: Session) -> list[dict[str, Any]]:
    """Each earned sender with its kinds, what it usually says, what would wait, and its switch."""
    off = senders_off(session)
    acted: dict[str, int] = defaultdict(int)
    rows = session.exec(select(MessageRef.sender).join(ExtractedFact, ExtractedFact.message_ref_id == MessageRef.id).where(ExtractedFact.resolved_by == f"rule:{EARNED}")).all()  # type: ignore[arg-type]
    for sender in rows:
        acted[_address(sender)] += 1
    by: dict[str, dict[str, Any]] = {}
    for (sender, kind), entry in sorted(earned_senders(session).items()):
        row = by.setdefault(sender, {"sender": sender, "name": entry["name"], "on": sender not in off, "off_why": off.get(sender, ""), "filed": acted.get(sender, 0), "kinds": []})
        row["kinds"].append({"kind": kind, "words": _words_of(kind), "answers": entry["answers"], "usual_low": entry["low"], "usual_high": entry["high"]})
    for sender, why in off.items():  # switched off and no longer earned is still shown: the switch is where it was left
        by.setdefault(sender, {"sender": sender, "name": "", "on": False, "off_why": why, "filed": acted.get(sender, 0), "kinds": []})
    return list(by.values())


def actions(session: Session, limit: int = 100) -> list[dict[str, Any]]:
    """What the rules did, newest first."""
    rows = session.exec(select(ExtractedFact).where(ExtractedFact.resolved_by.like("rule:%")).order_by(ExtractedFact.resolved_at.desc(), ExtractedFact.id.desc()).limit(limit)).all()  # type: ignore[attr-defined]
    out = []
    for fact in rows:
        name = rule_of(fact)
        if name is None:
            continue
        ref = session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None
        fields = _fields(fact)
        out.append({"id": fact.id, "kind": fact.kind, "words": _words_of(fact.kind), "rule": name, "rule_title": RULES[name]["title"], "why": fact.rule_why, "status": fact.status, "applied_to": fact.applied_to,
                    "sender": ref.sender if ref else "", "sender_name": (ref.sender_name if ref else "") or str(fields.get("sender_name") or ""), "subject": (ref.subject if ref else "") or "",
                    "amount": _said(fact.kind, fields), "at": fact.resolved_at.isoformat() if fact.resolved_at else None, "source": f"fact:{fact.id}"})
    return out


def overview(session: Session, today: date) -> dict[str, Any]:
    """Settings › Rules: every rule with its switch and how much it did, the senders, and what was done."""
    on = switches(session)
    counts = dict(session.exec(select(ExtractedFact.resolved_by, func.count()).where(ExtractedFact.resolved_by.like("rule:%")).group_by(ExtractedFact.resolved_by)).all())  # type: ignore[attr-defined]
    rules = [{"name": name, **words, "on": on[name], "kept": int(counts.get(f"rule:{name}", 0))} for name, words in RULES.items()]
    kept = sum(r["kept"] for r in rules)
    waiting = int(session.exec(select(func.count()).select_from(ExtractedFact).where(ExtractedFact.status == "pending")).one())
    said = (f"Rules have answered {kept} so far; {waiting} wait for you." if kept else f"No rule has answered anything yet; {waiting} wait for you.")
    return {"as_of": today.isoformat(), "rules": rules, "senders": sender_rows(session), "actions": actions(session), "kept": kept, "waiting": waiting, "said": said,
            "earn_after": EARN_AFTER, "not_advice": "These rules file what the mail says; none of them moves money or changes what the plan counts."}


def kept_count(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(ExtractedFact).where(ExtractedFact.resolved_by.like("rule:%"))).one())  # type: ignore[attr-defined]


# ---- older than two months ---------------------------------------------------------------------------------------


def _waiting_day(fact: ExtractedFact, ref: MessageRef | None, today: date) -> date:
    """How old a waiting fact is: the message's day, else when the app read it (a future date is the sender's fancy)."""
    return _message_day(ref, today) or (fact.created_at.date() if isinstance(fact.created_at, datetime) else today)


def older_facts(session: Session, today: date) -> list[ExtractedFact]:
    """What has waited more than sixty days with nothing newer superseding it. One the owner must see on its own (an
    export, a document's terms) is never in the batch."""
    from .needsyou import NEVER_IN_BULK

    look = Look(session, today)
    out = []
    for fact in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending").order_by(ExtractedFact.id)).all():
        if fact.kind in NEVER_IN_BULK:
            continue
        ref = look.ref(fact)
        if (today - _waiting_day(fact, ref, today)).days <= OLD_DAYS or look.superseded(fact, ref) is not None:
            continue
        out.append(fact)
    return out


def _token(ids: list[int]) -> str:
    return hashlib.sha256(",".join(map(str, sorted(ids))).encode()).hexdigest()[:16]


def older_batch(session: Session, today: date) -> dict[str, Any] | None:
    """The batch as it is shown: how many of each kind, and a token that names exactly these, so Skip all takes what the
    owner saw and nothing that came since."""
    rows = older_facts(session, today)
    if not rows:
        return None
    by_kind: dict[str, int] = defaultdict(int)
    days = []
    for fact in rows:
        by_kind[fact.kind] += 1
        days.append(_waiting_day(fact, session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None, today))
    n = len(rows)
    return {"count": n, "token": _token([f.id or 0 for f in rows]), "oldest": min(days).isoformat(), "newest": max(days).isoformat(),
            "kinds": [{"kind": k, "words": _words_of(k), "count": c} for k, c in sorted(by_kind.items(), key=lambda kv: -kv[1])],
            "said": f"Older than two months: {n} fact{'s' if n != 1 else ''}, with nothing newer to replace {'them' if n != 1 else 'it'}.",
            "asks": f"Skip all {n}? Nothing read from {'them' if n != 1 else 'it'} is used, and you can bring {'them' if n != 1 else 'it'} back here afterwards."}


def _batches(session: Session) -> dict[int, list[ExtractedFact]]:
    out: dict[int, list[ExtractedFact]] = defaultdict(list)
    rows = session.exec(select(ExtractedFact).where(ExtractedFact.status == "rejected", ExtractedFact.applied_to.like(f"{SKIP_MARK}%"))).all()  # type: ignore[attr-defined]
    for fact in rows:
        tail = fact.applied_to[len(SKIP_MARK):]
        if tail.isdigit():
            out[int(tail)].append(fact)
    return out


def skipped_batches(session: Session) -> list[dict[str, Any]]:
    """Each Skip all that can still be brought back, newest first."""
    out = []
    for batch, rows in _batches(session).items():
        at = max((f.resolved_at for f in rows if f.resolved_at), default=None)
        out.append({"batch": batch, "count": len(rows), "at": at.isoformat() if at else None})
    return sorted(out, key=lambda b: -b["batch"])


def skip_older(session: Session, token: str, by: str, today: date) -> dict[str, Any]:
    """Skip all: rejects exactly what the owner was shown, marked so it can be brought back. Raises LookupError when the
    list changed since. A skip is not an answer about a sender: it earns nothing and ends nothing. Commits."""
    from .facts import resolve_fact

    rows = older_facts(session, today)
    if not rows or token != _token([f.id or 0 for f in rows]):
        raise LookupError("The list changed since it was shown: nothing was skipped. Look at it again.")
    batch = max(_batches(session), default=0) + 1
    for fact in rows:
        resolve_fact(session, fact, "rejected", applied_to=f"{SKIP_MARK}{batch}", by=by)
    session.commit()
    n = len(rows)
    return {"batch": batch, "count": n, "said": f"Skipped {n}. Nothing read from {'them' if n != 1 else 'it'} is used. You can bring {'them' if n != 1 else 'it'} back from this page."}


def bring_back(session: Session, batch: int) -> dict[str, Any]:
    """Every fact of one Skip all waits again, as it was. No rule answers them afterwards: the owner asked for them."""
    rows = _batches(session).get(batch, [])
    if not rows:
        raise LookupError("Nothing from that batch is left to bring back.")
    for fact in rows:
        fact.status, fact.applied_to, fact.resolved_at, fact.resolved_by, fact.made, fact.rule_hold = "pending", "", None, "", "", True
        session.add(fact)
    session.commit()
    n = len(rows)
    return {"batch": batch, "count": n, "said": f"Brought back {n}: {'they wait' if n != 1 else 'it waits'} for your answer again."}
