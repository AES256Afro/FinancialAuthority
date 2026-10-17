"""Needs you that empties (M44): what waits for the owner, grouped so alike items are answered together, and each
bill the bank shows no payment for asked plainly: is it paid?

Facts from mail and documents are grouped by kind, each group with how many are complete enough to confirm in one
tap. A bill marked past due comes with the payments that might be it: a transaction near its due date with an
amount close to it, not already matched to another bill. The owner answers: paid (this one, or paid another way),
not yet, or the bill has stopped."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Bill, ExtractedFact, Payment, Transaction

GROUP_WORDS = {
    "bill_due": "Bills from your mail", "past_due": "Past-due notices", "paystub": "Paystubs", "statement": "Statements", "installment": "Pay-later plans",
    "advance": "Advances", "price_change": "Price changes", "receipt": "Receipts", "document_terms": "Terms from documents", "export": "Rocket Money exports",
    "investment_balance": "Investment balances", "payment_confirmation": "Payment confirmations",
}
# What a fact needs before it can be confirmed without the owner typing anything
COMPLETE = {
    "bill_due": ("amount", "due_date"), "past_due": ("amount",), "paystub": ("net", "pay_date"), "receipt": ("total",), "investment_balance": ("balance",),
    "statement": (), "price_change": ("amount",), "payment_confirmation": (),
}
NEVER_IN_BULK = {"export", "document_terms", "installment", "advance"}  # each needs the owner's eye, one at a time


def complete(fact: ExtractedFact) -> bool:
    if fact.kind in NEVER_IN_BULK or fact.kind not in COMPLETE:
        return False
    fields = json.loads(fact.fields or "{}")
    return all(fields.get(k) not in (None, "") for k in COMPLETE[fact.kind])


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

    pending = list_facts_rows(session, "pending", 500)
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
