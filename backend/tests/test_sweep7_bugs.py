"""The seventh sweep's findings, each pinned by the smallest case that showed it. The generated invariant tests
(test_sweep7_bugs_pure.py, test_sweep7_bugs_db.py) found them on random inputs; these name them."""

from __future__ import annotations

import math
from datetime import date, timedelta

from sqlmodel import Session, select

from app.db import engine
from app.models import Account, Bill, Income, Payment, Transaction

TODAY = date(2026, 10, 14)


# ---- SWEEP7-01: the month a debt is paid off, the rest of its minimum rolls on ---------------------------------------


def test_the_rest_of_a_minimum_rolls_on_the_month_its_debt_is_paid_off():
    """A small 0% debt whose minimum is 95 dies in its second month owing 5: the other 90 of that month's minimum
    is money the plan said it pays, so it goes to the next debt that month, not nowhere. Without that roll, the
    orders under-paid by one partial minimum per debt, and snowball (which kills small debts with the pot, losing
    nothing) could come out cheaper than avalanche on the same inputs, naming the wrong way as cheapest."""
    from app.engine.payoff import simulate

    debts = [{"name": "Small", "balance": 100.0, "apr": 0.0, "minimum": 95.0, "source": "debt:1"}, {"name": "Card", "balance": 5000.0, "apr": 30.0, "minimum": 200.0, "source": "debt:2"}]
    start = date(2026, 10, 1)
    base = simulate(debts, 0.0, "minimums", start)
    for order in ("avalanche", "snowball"):
        way = simulate(debts, 0.0, order, start)
        assert way["interest"] < base["interest"], (order, way["interest"], base["interest"])  # the 90 rolled on in month two saves interest
        assert way["debts"][0]["paid_on"] == "2026-12-01"
        # Pinned: Card paid 200, then 290 (the 90 Small no longer needs in its last month), then 295 a month. Without
        # the roll the plan pays 200, 200, 295: still cheaper than minimums alone, so the inequality above holds with the
        # fix reverted, but the interest comes out higher than this by the 90 that went nowhere.
        assert math.isclose(way["interest"], 1657.59, abs_tol=0.02), (order, way["interest"])
        assert way["months"] == 23 and way["debt_free"] == "2028-09-01"


# ---- SWEEP7-02: an Affirm schedule missing an entry is not read at the top confidence -------------------------------


def test_a_loan_confirmation_with_a_lost_entry_is_doubted_even_without_a_total_line():
    """With the "Total of payments" label lost in the mail's text, the schedule's own sum stood in for the total and
    checked itself: a schedule missing an entry passed at 0.95 and would have become a two-payment plan. The loan
    amount plus the finance charge is the total when the label is gone; with none of them, never the top mark."""
    from app.parsers.affirm import parse_loan_confirmation

    text = (
        "Your loan confirmation\n\nExample Outfitters has processed your order\n\n$687.65\nLoan amount\n\n$39.29\nFinance charge\n\nYour Payment Schedule\n\n"
        "Oct 23, 2026\n\n$242.32\nPrincipal: $222.75\nInterest: $19.57\n\nNov 23, 2026\n\n$242.32\nPrincipal: $229.09\nInterest: $13.23\n\nLoan ID: 9ABC-DEF2\n"
    )
    got = parse_loan_confirmation(text, TODAY)
    assert got is not None and got["count"] == 2 and got["total"] == 726.94 and got["confidence"] == 0.5
    whole = text.replace("Loan ID", "Dec 23, 2026\n\n$242.30\nPrincipal: $235.81\nInterest: $6.49\n\nLoan ID")
    assert parse_loan_confirmation(whole, TODAY)["confidence"] == 0.95
    bare = whole.replace("$687.65\nLoan amount\n\n$39.29\nFinance charge\n\n", "")
    assert parse_loan_confirmation(bare, TODAY)["confidence"] == 0.8  # nothing to check the schedule against


# ---- SWEEP7-03: a lost name line never hands a charge the mail's heading ---------------------------------------------


def test_an_upcoming_bills_mail_never_names_a_charge_after_its_heading():
    from app.parsers.rocketmoney_bills import parse_upcoming_bills

    text = "You have 2 recurring charges coming up within the next 7 days.\n\nMonday, October 12\n\n$61.40\n\nStreamflix\n\nThursday, October 15\n\n$15.99\n\nOpen Rocket Money\n"
    assert [(c["name"], c["amount"]) for c in parse_upcoming_bills(text, date(2026, 10, 9))] == [("Streamflix", 15.99)]


# ---- SWEEP7-04: a buffer of zero is 0.0 on the steps, never -0.0 ----------------------------------------------------


def test_a_zero_buffer_is_a_plain_zero_on_the_safe_to_spend_steps():
    from app.engine.snapshot import _finance_snapshot
    from app.store import set_setting

    with Session(engine) as session:
        chk = Account(name="Checking", institution="Example Bank", kind="checking", balance=500.0, connector="manual", external_id="chk")
        session.add(chk)
        session.flush()
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        set_setting(session, "buffer", 0.0)
        session.commit()
        snap = _finance_snapshot(session, TODAY)
    step = next(s for s in snap["safe_to_spend"]["steps"] if s["label"].startswith("Your buffer"))
    assert step["amount"] == 0.0 and math.copysign(1.0, step["amount"]) > 0, step


# ---- SWEEP7-05: a payment that paid a period now past the lookback never pays the next one too --------------------


def test_a_transaction_matched_to_an_old_period_is_not_matched_again_once_that_period_leaves_the_lookback():
    """A monthly bill with a thirty-day grace, paid 25 days late on Apr 15, which is also six days before the next
    due date: the first run pays the Mar 21 period with it. Two days later Mar 21 is past the sixty-day lookback,
    and the Apr 21 period took the same transaction, so one payment showed as two periods paid."""
    from app.engine.payments import match_payments

    with Session(engine) as session:
        chk = Account(name="Checking", institution="Example Bank", kind="checking", balance=500.0, connector="manual", external_id="chk")
        session.add(chk)
        session.flush()
        bill = Bill(name="Example Insurer", amount=139.01, due_day=21, grace_days=30, created_on=date(2026, 1, 1), pay_from_account_id=chk.id)
        session.add(bill)
        session.add(Transaction(account_id=chk.id, date=date(2027, 4, 15), merchant="Example Insurer", raw_name="EXAMPLE INSURER", amount=139.01, category="Insurance", row_hash="t-insurer-apr"))
        session.commit()
        match_payments(session, date(2027, 5, 19))
        paid = [(p.due_date, p.transaction_id) for p in session.exec(select(Payment).where(Payment.status == "paid")).all()]
        assert [d for d, _ in paid] == [date(2027, 3, 21)]
        match_payments(session, date(2027, 5, 21))
        rows = session.exec(select(Payment).order_by(Payment.due_date)).all()
        used = [p.transaction_id for p in rows if p.transaction_id]
        assert len(used) == len(set(used)) == 1, [(p.due_date, p.status, p.transaction_id) for p in rows]
        assert next(p for p in rows if p.due_date == date(2027, 4, 21)).status == "late"  # thirty days on, inside its grace, and unpaid
