"""Sweep 9: what the wider quarter (test_sweep8_bugs_quarter.py, with a second person, a home, a business, goals,
a scenario, the copies and the reviewer) found, reduced to the engine."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.db import bump_version, engine
from app.engine.decisions import date_move_check
from app.engine.household import seed
from app.engine.payments import match_payments
from app.engine.scenario import run_scenario
from app.engine.snapshot import finance_snapshot
from app.models import Account, Bill, Payment, Transaction

TODAY = date(2026, 10, 14)


def test_a_second_persons_own_account_is_not_the_owners_cash():
    """Found on every day of the quarter (from 2026-10-15): the second person's own checking, marked theirs on
    Settings › People, sat in the owner's safe to spend ($747.42 of cash where the owner's and shared accounts held
    $207.42), in the scenario's cash and in the billing-date check's low point. A shared account counts; theirs does not."""
    with Session(engine) as session:
        seed(session)
        before = finance_snapshot(session, TODAY)["safe_to_spend"]["parts"]["cash"]
        session.add(Account(name="Example Partner Checking", kind="checking", balance=540.0, person_id=7))
        session.add(Account(name="Example Joint Checking", kind="checking", balance=100.0, shared=True, person_id=7))
        session.commit()
        bump_version()
        snap = finance_snapshot(session, TODAY)
        assert snap["safe_to_spend"]["parts"]["cash"] == round(before + 100.0, 2)
        assert run_scenario(session, TODAY, {"name": "x", "move_date": "2026-12-01"})["cash"] == round(before + 100.0, 2)
        bill = session.exec(select(Bill).where(Bill.name == "Phone")).one()
        check = date_move_check(session, TODAY, bill.id, 5)
        session.add(Account(name="Example Partner Savings", kind="checking", balance=5000.0, person_id=7))
        session.commit()
        bump_version()
        assert date_move_check(session, TODAY, bill.id, 5)["low_before"] == check["low_before"]  # theirs moves no figure of the owner's


def _phone_rows(session: Session, *days: date) -> Bill:
    checking = session.exec(select(Account).where(Account.kind == "checking")).first()
    for d in days:
        session.add(Transaction(account_id=checking.id, date=d, merchant="Phone", raw_name="PHONE", amount=96.0, category="Bills & Utilities", row_hash=f"phone-{d.isoformat()}"))
    session.commit()
    return session.exec(select(Bill).where(Bill.name == "Phone")).one()


def test_moving_a_bills_due_day_keeps_the_months_paid_on_the_old_day():
    """Found on 2026-11-23 and every day after: the owner had the phone bill's day moved from the 22nd to the 5th
    (from December, as the request to the biller asks); the register then held a period due Nov 5 with "no payment
    of $96.00 seen", past due on Today, Needs you and the badge for the rest of the quarter, while the bank showed
    November's payment on the 22nd. A monthly bill is paid once a month: the month's payment on the old day pays
    the period on the new one, whether the old period is kept or (as the Bills page does today) remade."""
    with Session(engine) as session:
        seed(session)
        phone = _phone_rows(session, date(2026, 10, 22), date(2026, 11, 22))
        match_payments(session, date(2026, 11, 22))
        kept = {(p.due_date, p.status) for p in session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.due_date >= date(2026, 10, 1))).all()}
        assert kept == {(date(2026, 10, 22), "paid"), (date(2026, 11, 22), "paid")}  # December's period is past the three weeks looked ahead
        # The Bills page's way: the day changes and every period is remade.
        phone.due_day = 5
        for p in session.exec(select(Payment).where(Payment.bill_id == phone.id)).all():
            session.delete(p)
        session.add(phone)
        session.commit()
        match_payments(session, date(2026, 11, 23))
        rows = session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.due_date >= date(2026, 10, 1))).all()
        assert {(p.due_date, p.status, p.paid_date) for p in rows} == {(date(2026, 10, 5), "paid", date(2026, 10, 22)), (date(2026, 11, 5), "paid", date(2026, 11, 22)), (date(2026, 12, 5), "scheduled", None)}
        # December's payment lands on the new day and pays December; nothing is past due.
        _phone_rows(session, date(2026, 12, 5))
        match_payments(session, date(2026, 12, 7))
        rows = session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.due_date >= date(2026, 10, 1))).all()
        assert {(p.due_date, p.status) for p in rows} == {(date(2026, 10, 5), "paid"), (date(2026, 11, 5), "paid"), (date(2026, 12, 5), "paid")}


def test_a_period_on_the_new_day_is_not_owed_in_a_month_already_paid_on_the_old_one():
    """The same move with the paid periods kept: November is paid on the 22nd, so no period is due on Nov 5."""
    with Session(engine) as session:
        seed(session)
        phone = _phone_rows(session, date(2026, 10, 22), date(2026, 11, 22))
        match_payments(session, date(2026, 11, 22))
        phone.due_day = 5
        session.add(phone)
        for p in session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.status != "paid")).all():
            session.delete(p)
        session.commit()
        match_payments(session, date(2026, 11, 23))
        rows = session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.due_date >= date(2026, 10, 1))).all()
        assert {(p.due_date, p.status) for p in rows} == {(date(2026, 10, 22), "paid"), (date(2026, 11, 22), "paid"), (date(2026, 12, 5), "scheduled")}
        assert not [p for p in rows if p.status in ("late", "past_due")]
