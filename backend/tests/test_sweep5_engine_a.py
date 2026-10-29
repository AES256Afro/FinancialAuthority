"""The fifth sweep's money-engine findings (area A: the paycheck plan, the projection, bill dates, cover), each held
by a test on rows made here."""

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session

from app.db import engine
from app.models import Account, Bill, ExtractedFact, Income, Transaction

TODAY = date(2026, 10, 14)


def _checking(session: Session, balance: float, name: str = "Checking") -> Account:
    account = Account(name=name, institution="Example Bank", kind="checking", balance=balance, connector="manual", external_id=f"manual:{name.lower()}")
    session.add(account)
    session.flush()
    return account


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Shopping", **flags) -> Transaction:
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, row_hash=f"t-{account.id}-{when}-{merchant}-{amount}", **flags)
    session.add(row)
    return row


def _snapshot(session: Session, today: date) -> dict:
    from app.engine.snapshot import _cache, finance_snapshot

    _cache.clear()
    return finance_snapshot(session, today)


# ---- SWEEP5-A-01: a bill's next due date is its first date -------------------------------------------------------------


def test_a_bill_every_few_months_is_never_due_before_its_next_due_date():
    """Quarterly water, paid today, entered with its next due date three months out: the plan must not count a
    charge today (the one just paid) on the way to that date."""
    from app.engine.billdates import bill_dates

    water = Bill(name="Water quarterly", amount=90.0, cadence="quarterly", due_day=14, anchor_date=date(2027, 1, 14), created_on=TODAY)
    assert bill_dates(water, TODAY, TODAY + timedelta(days=75)) == []
    assert bill_dates(water, TODAY, TODAY + timedelta(days=400)) == [date(2027, 1, 14), date(2027, 4, 14), date(2027, 7, 14), date(2027, 10, 14)]
    insurance = Bill(name="Car insurance", amount=600.0, cadence="yearly", due_day=15, anchor_date=date(2026, 11, 14), created_on=TODAY)
    assert bill_dates(insurance, TODAY, TODAY + timedelta(days=365)) == [date(2026, 11, 15)]  # the day on the anchor's month, as before
    stale = Bill(name="Old quarterly", amount=90.0, cadence="quarterly", due_day=5, anchor_date=date(2026, 1, 5))  # an anchor never moved on
    assert bill_dates(stale, TODAY, TODAY + timedelta(days=200)) == [date(2027, 1, 5), date(2027, 4, 5)]
    with Session(engine) as session:
        chk = _checking(session, 2000.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.add(water)
        session.commit()
        snap = _snapshot(session, TODAY)
    assert [e["name"] for e in snap["due_before_payday"]["items"]] == []
    assert snap["safe_to_spend"]["amount"] == 1800.0


def test_a_bill_every_two_weeks_is_never_due_before_its_next_due_date():
    from app.engine.billdates import bill_dates

    cleaner = Bill(name="Cleaner", amount=80.0, cadence="biweekly", due_day=1, anchor_date=date(2026, 10, 30), created_on=TODAY)
    assert bill_dates(cleaner, TODAY, date(2026, 11, 30)) == [date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27)]
    stale = Bill(name="Cleaner", amount=80.0, cadence="biweekly", due_day=1, anchor_date=date(2026, 10, 2))
    assert bill_dates(stale, TODAY, date(2026, 11, 30)) == [date(2026, 10, 16), date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27)]


# ---- SWEEP5-A-02: an odd due day never stops the plan ------------------------------------------------------------------


def test_a_bill_with_an_odd_due_day_never_stops_the_plan():
    from app.engine.billdates import bill_dates

    for day in (0, -3, 32, 99):
        odd = Bill(name="Odd", amount=10.0, due_day=day, created_on=date(2026, 1, 1))
        assert bill_dates(odd, TODAY, TODAY + timedelta(days=75))  # it still falls somewhere every month
    with Session(engine) as session:
        chk = _checking(session, 500.0)
        session.add(Bill(name="Imported oddly", amount=10.0, due_day=0, created_on=date(2026, 1, 1), pay_from_account_id=chk.id))
        session.commit()
        snap = _snapshot(session, TODAY)
        from app.engine.payments import match_payments

        match_payments(session, TODAY)
    assert snap["safe_to_spend"]["amount"] is not None


# ---- SWEEP5-A-03: the Cover low point ----------------------------------------------------------------------------------


def test_the_cover_low_point_is_never_above_what_the_window_leaves():
    """The window after payday spends the usual pace on every one of its days, the payday too; its lowest point
    is on its last day and cannot sit above what the window says is left."""
    from app.engine.cover import account_cover

    with Session(engine) as session:
        chk = _checking(session, 900.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1000.0, next_date=date(2026, 10, 16), account_id=chk.id))
        for k in range(8):
            _tx(session, chk, TODAY - timedelta(days=7 * k + 1), "Grocer", 70.0, "Groceries")
        session.commit()
        cover = account_cover(session, TODAY)
    checking = next(a for a in cover["accounts"] if a["kind"] == "checking")
    assert checking["pace_per_day"] > 0
    for window in checking["windows"]:
        assert window["low"] <= window["left"] + 0.005, window
        assert window["low_on"] == window["end"]


# ---- SWEEP5-A-04: the snapshot is a function of the injected day ------------------------------------------------------


def test_purchases_told_by_mail_are_judged_by_the_injected_day():
    """A bank's purchase alert from the week before the injected day is in flight; the wall clock is not consulted."""
    long_ago = date(2024, 3, 10)
    with Session(engine) as session:
        _checking(session, 500.0)
        session.add(ExtractedFact(kind="bank_alert", status="auto", fields='{"alert": "purchase", "amount": 42.5, "merchant": "Example Store", "message_date": "2024-03-08"}',
                                  created_at=datetime(2024, 3, 8, 15, 0, tzinfo=timezone.utc)))
        session.commit()
        snap = _snapshot(session, long_ago)
    assert [(s["amount"], s["date"]) for s in snap["in_flight"]["mail"]["items"]] == [(42.5, "2024-03-08")]


def test_a_minimum_under_the_interest_never_shows_billions_of_interest():
    """A $10 minimum on $1,000 at 36% never finishes: the way says the balance grows, with no interest figure, instead of
    the sum to the 50-year cap (tens of billions), and the plan still ranks the two orders."""
    from datetime import date as _date

    from app.engine.payoff import simulate

    debts = [{"name": "Card", "source": "debt:1", "balance": 1000.0, "apr": 36.0, "minimum": 10.0}]
    way = simulate(debts, 0.0, "minimums", _date(2026, 10, 14))
    assert way["months"] is None and way["interest"] is None and way["grows"] is True and way["debts"][0]["interest"] is None
    with_extra = simulate(debts, 100.0, "avalanche", _date(2026, 10, 14))
    assert with_extra["months"] is not None and with_extra["grows"] is False and 0 < with_extra["interest"] < 300
