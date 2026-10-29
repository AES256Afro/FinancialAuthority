"""The sixth sweep's findings (the engine, the jobs, the parsers and the models), each held by a test on rows
made here: flows across modules, the owner's time zone, parsers on odd input, detection as amounts drift."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import Account, Bill, Income, RecurringSeries, Transaction

TODAY = date(2026, 10, 14)


def _checking(session: Session, balance: float = 2000.0, name: str = "Checking", **kw) -> Account:
    account = Account(name=name, institution="Example Bank", kind=kw.pop("kind", "checking"), balance=balance, connector="manual", external_id=f"manual:{name.lower()}", **kw)
    session.add(account)
    session.flush()
    return account


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Entertainment", **flags) -> Transaction:
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, row_hash=f"t-{account.id}-{when}-{merchant}-{amount}", **flags)
    session.add(row)
    return row


# ---- SWEEP6-01: a series whose price rises over a whole dollar stays one series ------------------------------------


def test_a_price_rise_over_the_rounding_line_does_not_make_a_second_series():
    """A streaming charge at 15.49 confirmed as a subscription, then 15.99 from April on. By October the typical
    amount rounds to 16, not 15: the series' key moves, and detection must move the confirmed series with it, not
    add an unconfirmed twin beside it (which the owner would be asked to name again, and name into a second bill)."""
    from app.jobs.detect import run_detection

    with Session(engine) as session:
        chk = _checking(session)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        for k in range(13):  # the 14th of each month, October 2025 to October 2026: seven at the old price, six at the new
            when = date(2025 + (9 + k) // 12, (9 + k) % 12 + 1, 14)
            _tx(session, chk, when, "Streamflix", 15.49 if k < 7 else 15.99)
        session.commit()
        run_detection(session, TODAY)
        rows = session.exec(select(RecurringSeries).where(RecurringSeries.merchant_key == "streamflix")).all()
        assert len(rows) == 1 and rows[0].typical_amount == 15.49
        series = rows[0]
        series.kind, series.confirmed = "subscription", True
        session.add(series)
        session.add(Bill(name="Shows", kind="subscription", amount=15.99, due_day=14, autopay=True, series_id=series.id, created_on=TODAY, pay_from_account_id=chk.id))
        session.commit()
        first_id = series.id
        # Two more months at the new price: the median crosses from 15.49 to 15.99.
        for when in (date(2026, 11, 14), date(2026, 12, 14)):
            _tx(session, chk, when, "Streamflix", 15.99)
        session.commit()
        run_detection(session, date(2026, 12, 15))
        rows = session.exec(select(RecurringSeries).where(RecurringSeries.merchant_key == "streamflix", RecurringSeries.active == True)).all()  # noqa: E712
        assert [r.id for r in rows] == [first_id], [(r.id, r.typical_amount, r.confirmed) for r in rows]
        assert rows[0].confirmed and rows[0].typical_amount == 15.99 and rows[0].last_date == date(2026, 12, 14)


# ---- SWEEP6-02: a balance read this evening is not "tomorrow's" --------------------------------------------------------


def test_a_balance_read_this_evening_is_not_in_the_future(monkeypatch):
    """Chicago, 9 pm on the 14th: the bank's balance is stamped 02:00 UTC on the 15th. In the owner's day it is
    zero days old, never minus one."""
    from app.engine.investments import investments_overview
    from app.engine.reports import _account_out

    monkeypatch.setenv("TZ", "America/Chicago")
    with Session(engine) as session:
        chk = _checking(session)
        fund = _checking(session, 5000.0, "Brokerage", kind="investment", subtype="brokerage")
        fund.balance_at = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
        chk.balance_at = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
        session.add_all([chk, fund])
        session.commit()
        inv = investments_overview(session, TODAY)
        assert [i["stale_days"] for i in inv["items"]] == [0]
        assert _account_out(chk, TODAY)["age_days"] == 0


# ---- SWEEP6-03: an advance on the register reaches the cycle and the app's figures ---------------------------------


def test_an_advance_on_the_register_shows_in_the_cycle_and_per_app():
    """An advance confirmed from mail or typed (no bank row the ledger pairs) is owed from the next check. The
    cycle and the per-app figures on the same page must show it taken, not "no advance taken"."""
    from app.engine.borrowing import borrowing_overview, borrowing_summary
    from app.models import Advance

    with Session(engine) as session:
        chk = _checking(session)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.add(Advance(provider="Example Advance", amount=150.0, fee=6.0, taken_on=date(2026, 10, 8), repay_date=date(2026, 10, 16)))
        session.add(Advance(provider="Example Advance", amount=100.0, fee=5.0, taken_on=date(2026, 9, 25), repay_date=date(2026, 10, 2), repaid=True))
        session.commit()
        over = borrowing_overview(session, TODAY)
        summary = borrowing_summary(session, TODAY)
    assert over["outstanding_total"] == 156.0
    assert [p["provider"] for p in over["providers"]] == ["Example Advance"]
    app = over["providers"][0]
    assert app["advances"] == 2 and app["advanced"] == 250.0 and app["fees"] == 11.0 and app["outstanding"] == 156.0
    on_payday = {c["payday"]: c for c in over["cycle"]}
    assert on_payday["2026-10-16"]["advances"] == 1 and on_payday["2026-10-16"]["advanced"] == 150.0
    assert on_payday["2026-10-02"]["repaid_on_payday"] == 105.0 and on_payday["2026-10-02"]["fees"] == 5.0
    assert summary["advances_90d"] == 2 and summary["since_payday"]["advanced"] == 150.0


# ---- SWEEP6-04: the Needs you badge and the page count the same things -------------------------------------------------


def test_the_needs_you_badge_and_the_page_count_the_same_things():
    """Two charges to name and three bills with no payment seen: the badge (the snapshot's confirmations) and the
    page's own count both say five."""
    from app.engine.needsyou import triage
    from app.engine.payments import match_payments
    from app.engine.snapshot import _cache, finance_snapshot

    with Session(engine) as session:
        chk = _checking(session)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        for name, day in (("Water", 1), ("Power", 3), ("Internet", 5)):
            session.add(Bill(name=name, amount=60.0, due_day=day, created_on=date(2026, 9, 20), pay_from_account_id=chk.id))
        for merchant in ("Gymco", "Streamflix"):
            session.add(RecurringSeries(merchant_key=merchant.lower(), merchant=merchant, category="Entertainment", cadence="monthly", typical_amount=20.0, latest_amount=20.0, charges_seen=4,
                                        first_seen=date(2026, 6, 14), last_date=date(2026, 9, 14), next_expected=date(2026, 10, 14), active=True, kind="unknown"))
        session.commit()
        match_payments(session, TODAY)
        _cache.clear()
        snap = finance_snapshot(session, TODAY)
        tri = triage(session, TODAY)
    assert snap["pending"]["past_due"] == 3 and snap["pending"]["series"] == 2
    assert snap["pending"]["confirmations"] == tri["total"] == 5
    assert tri["word"] == "5 things wait here: 3 bills with no payment seen and 2 recurring charges to name."


# ---- SWEEP6-05: a paystub's health savings line is the HSA, not the health plan ----------------------------------------


def test_a_health_savings_line_is_the_hsa_not_the_health_plan():
    from app.parsers.paystub import parse_paystub_text

    text = "Acme Payroll\nPay Date: 10/09/2026\nGross Pay 2,980.77\nFederal Income Tax 301.11\nHealth Savings Account 50.00\nMedical 88.00\nNet Pay 2,100.00\n"
    found = {d["name"]: d["amount"] for d in parse_paystub_text(text)["deductions"]}
    assert found == {"Federal income tax": 301.11, "HSA": 50.0, "Health insurance": 88.0}


# ---- SWEEP6-06: a payment whose bank row changed afterwards says what the row says now ----------------------------


def test_a_paid_period_follows_its_transaction_when_the_bank_changes_it():
    """Plaid sends a "modified" row: the electric bill's payment posts a day later at 142.37, not 140.00 as first
    reported. The period stays paid, by that row, and says the row's amount and day, not the first report's."""
    from app.engine.payments import match_payments
    from app.models import Payment

    with Session(engine) as session:
        chk = _checking(session)
        session.add(Bill(name="Electricity", amount=140.0, due_day=10, created_on=date(2026, 9, 1), pay_from_account_id=chk.id))
        row = _tx(session, chk, date(2026, 10, 9), "Electricity", 140.0, "Bills & Utilities")
        session.commit()
        match_payments(session, TODAY)
        paid = session.exec(select(Payment).where(Payment.due_date == date(2026, 10, 10))).one()
        assert paid.status == "paid" and paid.transaction_id == row.id and paid.amount == 140.0
        row.amount, row.date = 142.37, date(2026, 10, 10)  # the bank's modified row
        session.add(row)
        session.commit()
        match_payments(session, TODAY)
        session.refresh(paid)
        assert (paid.status, paid.transaction_id, paid.amount, paid.paid_date) == ("paid", row.id, 142.37, date(2026, 10, 10))

