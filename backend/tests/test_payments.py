from datetime import date

from sqlmodel import Session, select

from app.db import engine
from app.engine.brief import build_brief
from app.engine.household import seed
from app.engine.payments import match_payments, payments_overview
from app.engine.snapshot import finance_snapshot
from app.models import Bill, Payment

TODAY = date(2026, 10, 14)


def test_matching_finds_paid_bills_and_flags_the_missing_one():
    with Session(engine) as session:
        seed(session)
        session.add(Bill(name="Ghost Service", amount=33.0, due_day=10, category="Bills & Utilities"))
        session.commit()
        counts = match_payments(session, TODAY)
        assert counts["paid"] >= 10 and counts["scheduled"] >= 5
        rent = session.exec(select(Bill).where(Bill.name == "Rent")).one()
        rent_oct = session.exec(select(Payment).where(Payment.bill_id == rent.id, Payment.due_date == date(2026, 10, 1))).one()
        assert rent_oct.status == "paid" and rent_oct.transaction_id is not None and rent_oct.paid_date == date(2026, 10, 1)
        card = session.exec(select(Bill).where(Bill.name == "Card minimum")).one()
        card_oct = session.exec(select(Payment).where(Payment.bill_id == card.id, Payment.due_date == date(2026, 9, 25))).one()
        assert card_oct.status == "paid"  # matched by the exact amount of the card payment
        ghost = session.exec(select(Bill).where(Bill.name == "Ghost Service")).one()
        ghost_oct = session.exec(select(Payment).where(Payment.bill_id == ghost.id, Payment.due_date == date(2026, 10, 10))).one()
        assert ghost_oct.status == "past_due"
        overview = payments_overview(session, TODAY)
        assert any(p["name"] == "Ghost Service" for p in overview["past_due"])
        assert all(p["source"].startswith("payment:") for p in overview["paid"])
        # Idempotent.
        again = match_payments(session, TODAY)
        assert again["paid"] == counts["paid"]


def test_snapshot_carries_payments_funded_lines_and_a_brief():
    with Session(engine) as session:
        seed(session)
        match_payments(session, TODAY)
        snap = finance_snapshot(session, TODAY)
    first = snap["windows"][0]
    assert all("funded" in e for e in first["bills"]["items"])
    assert all(e["line"] in ("housing", "utilities", "debt") for e in snap["calendar"]["events"] if e["kind"] != "paycheck")
    rent = next(e for e in snap["calendar"]["events"] if e["name"] == "Rent")
    assert rent["line"] == "housing"
    assert snap["pending"]["past_due"] == 0
    brief = snap["brief"]
    assert brief and brief[0]["severity"] in ("info", "warning", "critical")
    texts = " ".join(b["text"] for b in brief)
    assert "clears before payday" in texts and "Safe to spend" in texts
    assert build_brief(snap) == brief


def test_a_yearly_bill_comes_due_once_a_year_not_every_month():
    from datetime import date

    from app.engine.billdates import bill_dates
    from app.models import Bill

    yearly = Bill(name="Annual Membership", amount=139.0, cadence="yearly", due_day=20, anchor_date=date(2026, 11, 20))
    assert bill_dates(yearly, date(2026, 10, 14), date(2027, 12, 31)) == [date(2026, 11, 20), date(2027, 11, 20)]
    quarterly = Bill(name="Water", amount=60.0, cadence="quarterly", due_day=5, anchor_date=date(2026, 9, 5))
    assert bill_dates(quarterly, date(2026, 10, 14), date(2027, 6, 30)) == [date(2026, 12, 5), date(2027, 3, 5), date(2027, 6, 5)]
    biweekly = Bill(name="Lawn", amount=40.0, cadence="biweekly", due_day=1, anchor_date=date(2026, 10, 2))
    assert bill_dates(biweekly, date(2026, 10, 14), date(2026, 11, 14)) == [date(2026, 10, 16), date(2026, 10, 30), date(2026, 11, 13)]
    monthly = Bill(name="Phone", amount=96.0, due_day=31)
    assert bill_dates(monthly, date(2027, 1, 30), date(2027, 3, 31)) == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
