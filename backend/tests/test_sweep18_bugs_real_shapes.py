"""Sweep 18: the owner's real data, checked on a copy on the box (aggregates only left it), and each shape it showed
rebuilt here with synthetic values.

What the copy showed: on nine monthly bills whose due day a confirmed notice had moved by one to three days (the
notice sets the day without the Bills page's rebuild of the periods), the register held a period on the old day and
one on the new day for the same month. The old day's period held the month's bank row, so the new day's period stood
past due: 13 of the 23 past-due periods on Needs you and the badge were months already paid, the old day's future
periods stayed "scheduled" after their date had passed, and the payments list showed two coming periods for one bill.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api import routes
from app.db import engine
from app.engine.payments import match_payments
from app.models import Account, Bill, Payment, Transaction


def _setup(session: Session, name: str, amount: float, due_day: int, paid_on: list[date]) -> Bill:
    checking = session.exec(select(Account).where(Account.kind == "checking")).first()
    if checking is None:
        checking = Account(name="Example Checking", kind="checking", balance=2000.0)
        session.add(checking)
        session.flush()
    bill = Bill(name=name, amount=amount, due_day=due_day, pay_from_account_id=checking.id, category="Bills & Utilities")
    session.add(bill)
    for d in paid_on:
        session.add(Transaction(account_id=checking.id, date=d, merchant=name, raw_name=name.upper(), amount=amount, category="Bills & Utilities", row_hash=f"{name}-{d.isoformat()}"))
    session.commit()
    session.refresh(bill)
    return bill


def _periods(session: Session, bill: Bill | int) -> list[tuple[date, str]]:
    bill_id = bill if isinstance(bill, int) else bill.id
    return sorted((p.due_date, p.status) for p in session.exec(select(Payment).where(Payment.bill_id == bill_id)).all())


def test_a_notice_that_moves_the_day_across_a_month_end_leaves_no_month_past_due(owner: TestClient):
    """The 30th moved to the 1st: the payments on Sep 1 and Sep 30 had paid the old periods (Aug 30, Sep 30); on the
    new day they pay Sep 1 and Oct 1. Nothing is past due, no period sits on the old day, one row per payment."""
    with Session(engine) as session:
        bill = _setup(session, "Example Gym", 40.0, 30, [date(2026, 9, 1), date(2026, 9, 30)])
        match_payments(session, date(2026, 10, 10))
        assert _periods(session, bill) == [(date(2026, 8, 30), "paid"), (date(2026, 9, 30), "paid"), (date(2026, 10, 30), "scheduled")]
        bill.due_day = 1  # as confirming a notice does (api/mail.py), with no rebuild of the periods
        session.add(bill)
        session.commit()
        match_payments(session, date(2026, 10, 29))
        rows = session.exec(select(Payment).where(Payment.bill_id == bill.id)).all()
        assert sorted((p.due_date, p.status) for p in rows) == [(date(2026, 9, 1), "paid"), (date(2026, 10, 1), "paid"), (date(2026, 11, 1), "scheduled")]
        held = [p.transaction_id for p in rows if p.transaction_id]
        assert len(held) == len(set(held)) == 2
    routes.set_today_override(date(2026, 10, 29))
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["pending"]["past_due"] == 0 and owner.get("/api/v1/needs-you").json()["past_due"] == []


def test_periods_made_on_both_days_before_the_guard_are_healed(owner: TestClient):
    """The box's own state, made by matchers before 0.10.0: the 25th moved to the 24th, the old day's periods paid by
    the rows on the 24th (Aug, Sep) and the new day's standing past due; October's old period still "scheduled" four
    days after its date. Past due periods older than the matcher's sixty days are healed too, as Needs you shows them
    for ninety."""
    with Session(engine) as session:
        bill = _setup(session, "Example Water", 55.0, 24, [date(2026, 8, 24), date(2026, 9, 23), date(2026, 10, 22)])
        tx = {t.date: t.id for t in session.exec(select(Transaction)).all()}
        session.add_all([
            Payment(bill_id=bill.id, due_date=date(2026, 8, 25), amount=55.0, status="paid", paid_date=date(2026, 8, 24), transaction_id=tx[date(2026, 8, 24)], evidence="transaction"),
            Payment(bill_id=bill.id, due_date=date(2026, 9, 25), amount=55.0, status="paid", paid_date=date(2026, 9, 23), transaction_id=tx[date(2026, 9, 23)], evidence="transaction"),
            Payment(bill_id=bill.id, due_date=date(2026, 10, 25), amount=55.0, status="scheduled"),
            Payment(bill_id=bill.id, due_date=date(2026, 8, 24), amount=55.0, status="past_due"),
            Payment(bill_id=bill.id, due_date=date(2026, 9, 24), amount=55.0, status="past_due"),
            Payment(bill_id=bill.id, due_date=date(2026, 10, 24), amount=55.0, status="paid", paid_date=date(2026, 10, 22), transaction_id=tx[date(2026, 10, 22)], evidence="transaction"),
        ])
        session.commit()
        bill_id = bill.id
    routes.set_today_override(date(2026, 10, 29))
    assert owner.get("/api/v1/snapshot").json()["pending"]["past_due"] == 2  # the shape as found
    with Session(engine) as session:
        match_payments(session, date(2026, 10, 29))
        assert _periods(session, bill_id) == [(date(2026, 8, 24), "paid"), (date(2026, 9, 24), "paid"), (date(2026, 10, 24), "paid")]
    assert owner.get("/api/v1/snapshot").json()["pending"]["past_due"] == 0


def test_a_month_paid_on_the_old_day_out_of_reach_of_the_new_day_stays_paid_once():
    """The 5th moved to the 22nd with the old periods kept: no row on the 22nd can take the payment of the 5th, so the
    month stays paid on its old day and an old past-due period on the new day is not owed again."""
    with Session(engine) as session:
        bill = _setup(session, "Example Phone", 60.0, 22, [date(2026, 9, 5), date(2026, 10, 5)])
        tx = {t.date: t.id for t in session.exec(select(Transaction)).all()}
        session.add_all([
            Payment(bill_id=bill.id, due_date=date(2026, 9, 5), amount=60.0, status="paid", paid_date=date(2026, 9, 5), transaction_id=tx[date(2026, 9, 5)], evidence="transaction"),
            Payment(bill_id=bill.id, due_date=date(2026, 10, 5), amount=60.0, status="paid", paid_date=date(2026, 10, 5), transaction_id=tx[date(2026, 10, 5)], evidence="transaction"),
            Payment(bill_id=bill.id, due_date=date(2026, 9, 22), amount=60.0, status="past_due"),
        ])
        session.commit()
        match_payments(session, date(2026, 10, 29))
        rows = _periods(session, bill)
        assert not [r for r in rows if r[1] in ("late", "past_due")], rows
        assert [r for r in rows if r[0].month in (9, 10)] == [(date(2026, 9, 5), "paid"), (date(2026, 10, 5), "paid")]


def test_a_row_another_bill_also_holds_is_left_to_the_data_check():
    """One $10 row held by two bills' periods (made by a matcher before 0.10.0; the data check shows it as "a payment
    matched twice"): the move's healing never lends it to a third period."""
    with Session(engine) as session:
        a = _setup(session, "Example Music", 10.0, 22, [date(2026, 9, 22)])
        b = _setup(session, "Example Video", 10.0, 28, [])
        tid = session.exec(select(Transaction.id)).first()
        session.add_all([
            Payment(bill_id=a.id, due_date=date(2026, 9, 19), amount=10.0, status="paid", paid_date=date(2026, 9, 22), transaction_id=tid, evidence="transaction"),
            Payment(bill_id=b.id, due_date=date(2026, 9, 28), amount=10.0, status="paid", paid_date=date(2026, 9, 22), transaction_id=tid, evidence="transaction"),
        ])
        session.commit()
        match_payments(session, date(2026, 10, 29))
        holders = [p for p in session.exec(select(Payment).where(Payment.transaction_id == tid)).all()]
        assert sorted((p.bill_id, p.due_date) for p in holders) == [(a.id, date(2026, 9, 19)), (b.id, date(2026, 9, 28))]
