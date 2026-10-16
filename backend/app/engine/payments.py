"""Payment matching: each bill period finds its payment in the ledger, or becomes late, then past due.

A match is a transaction near the due date whose merchant looks like the bill, or whose amount is the bill's
exact amount. Card payments count: a card's minimum is paid by a transfer, not a purchase.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Bill, Payment, RecurringSeries, Transaction
from .recurring import merchant_key
from .snapshot import next_due

LOOKBACK_DAYS = 60
AHEAD_DAYS = 21
BEFORE_DAYS = 6
AFTER_DAYS = 4


def _due_dates(bill: Bill, start: date, end: date) -> list[date]:
    from .billdates import bill_dates

    return bill_dates(bill, start, end)


def _looks_like(bill_key: str, series_key: str | None, t_key: str) -> bool:
    if not t_key:
        return False
    if series_key and (t_key == series_key or t_key.startswith(series_key) or series_key.startswith(t_key)):
        return True
    return t_key == bill_key or t_key.startswith(bill_key) or bill_key.startswith(t_key)


def match_payments(session: Session, today: date) -> dict:
    start = today - timedelta(days=LOOKBACK_DAYS)
    end = today + timedelta(days=AHEAD_DAYS)
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    series_keys = {s.id: s.merchant_key for s in session.exec(select(RecurringSeries)).all()}
    transactions = session.exec(select(Transaction).where(Transaction.date >= start - timedelta(days=BEFORE_DAYS), Transaction.date <= today, Transaction.amount > 0, Transaction.pending == False)).all()  # noqa: E712
    existing = {(p.bill_id, p.due_date): p for p in session.exec(select(Payment).where(Payment.due_date >= start)).all()}
    used = {p.transaction_id for p in existing.values() if p.transaction_id}
    counts = {"paid": 0, "late": 0, "past_due": 0, "scheduled": 0}
    for bill in bills:
        bill_key = merchant_key(bill.name)
        series_key = series_keys.get(bill.series_id) if bill.series_id else None
        # A named match may drift by the detector's own 15% (a price change); an estimate by 40%.
        tolerance = max(2.0, bill.amount * (0.4 if bill.amount_mode == "estimate" else 0.15))
        # A bill is looked for from the day it was added, never in the months before it existed.
        for due in _due_dates(bill, max(start, bill.created_on or start), end):
            payment = existing.get((bill.id, due))
            if payment is not None and payment.status == "paid":
                counts["paid"] += 1
                continue
            match = None
            for t in transactions:
                if t.id in used or not (due - timedelta(days=BEFORE_DAYS) <= t.date <= due + timedelta(days=max(AFTER_DAYS, bill.grace_days))):
                    continue
                close_amount = abs(t.amount - bill.amount) <= tolerance
                exact_amount = abs(t.amount - bill.amount) < 0.01
                if (close_amount and _looks_like(bill_key, series_key, merchant_key(t.merchant))) or (exact_amount and not t.is_transfer):
                    match = t
                    break
            if match is not None:
                status, paid_date, tx_id, amount, evidence = "paid", match.date, match.id, match.amount, f"transaction:{match.id}"
                used.add(match.id)
            elif due < today:
                days_late = (today - due).days
                status = "late" if days_late <= max(3, bill.grace_days) else "past_due"
                paid_date, tx_id, amount, evidence = None, None, bill.amount, ""
            else:
                status, paid_date, tx_id, amount, evidence = "scheduled", None, None, bill.amount, ""
            if payment is None:
                payment = Payment(bill_id=bill.id, due_date=due, amount=amount, status=status, paid_date=paid_date, transaction_id=tx_id, evidence=evidence)
                existing[(bill.id, due)] = payment
            else:
                payment.status, payment.paid_date, payment.transaction_id, payment.amount, payment.evidence = status, paid_date, tx_id, amount, evidence
            session.add(payment)
            counts[status] += 1
    session.commit()
    return counts


def paid_periods(session: Session, today: date) -> set[tuple[int, date]]:
    """The (bill, due date) periods from today on whose payment the ledger already shows, paid today or early.
    The plan leaves them out: that money has left the balance, or rides the card that was charged."""
    rows = session.exec(select(Payment.bill_id, Payment.due_date).where(Payment.status == "paid", Payment.due_date >= today)).all()
    return {(bill_id, due) for bill_id, due in rows}


def payments_overview(session: Session, today: date) -> dict:
    """Each period of the bills on the register: past due, late, paid and coming. A bill that is no longer on it
    (undone, or turned off) has no periods here; its rows are kept, unshown."""
    bills = {b.id: b for b in session.exec(select(Bill).where(Bill.active == True)).all()}  # noqa: E712
    rows = [p for p in session.exec(select(Payment).where(Payment.due_date >= today - timedelta(days=45)).order_by(Payment.due_date)).all() if p.bill_id in bills]

    def out(p: Payment) -> dict:
        b = bills.get(p.bill_id)
        return {"bill_id": p.bill_id, "name": b.name if b else "?", "kind": b.kind if b else "bill", "due_date": p.due_date.isoformat(), "amount": p.amount, "status": p.status,
                "paid_date": p.paid_date.isoformat() if p.paid_date else None, "evidence": p.evidence, "autopay": b.autopay if b else False, "source": f"payment:{p.id}"}

    return {
        "past_due": [out(p) for p in rows if p.status == "past_due"],
        "late": [out(p) for p in rows if p.status == "late"],
        "paid": [out(p) for p in rows if p.status == "paid" and p.due_date >= today - timedelta(days=45)],
        "scheduled": [out(p) for p in rows if p.status == "scheduled"],
    }
