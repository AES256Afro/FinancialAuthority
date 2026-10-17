"""M48: credit health from your own data: each card's share of its limit and what to pay before its statement to
report under 30% and 10%, the overall share, and the on-time record and streak from matched payments."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.models import Account, Bill, Debt, Payment


def test_cards_say_what_to_pay_before_the_statement_and_the_record_counts_on_time(owner: TestClient):
    with Session(engine) as session:
        a = Account(name="Example Card", kind="card", balance=600.0, credit_limit=1000.0)
        b = Account(name="Example Store Card", kind="card", balance=50.0, credit_limit=1000.0)
        session.add_all([a, b])
        session.flush()
        session.add(Debt(name="Example Card", kind="card", balance=600.0, apr=24.0, minimum=35.0, statement_day=20, due_day=15, account_id=a.id))
        bill = Bill(name="Example Power", amount=80.0, due_day=3)
        session.add(bill)
        session.flush()
        for month, paid_on, status in ((7, date(2026, 7, 3), "paid"), (8, date(2026, 8, 20), "paid"), (9, date(2026, 9, 2), "paid"), (10, date(2026, 10, 3), "paid")):
            session.add(Payment(bill_id=bill.id, due_date=date(2026, month, 3), amount=80.0, status=status, paid_date=paid_on))
        session.commit()
    got = owner.get("/api/v1/credit").json()
    card = next(c for c in got["cards"] if c["name"] == "Example Card")
    assert card["share"] == 0.6 and card["pay_for_30"] == 300.0 and card["pay_for_10"] == 500.0 and card["status"] == "crit"
    assert "Pay $300.00 before its statement closes on Tue Oct 20" in card["word"]
    store = next(c for c in got["cards"] if c["name"] == "Example Store Card")
    assert store["status"] == "good" and store["pay_for_30"] == 0.0
    assert got["overall"]["share"] == 0.325 and got["overall"]["status"] == "crit"
    p = got["payments"]
    assert p["on_time"] == 3 and p["late"] == 1 and p["streak"] == 2 and p["total"] == 4
    assert "3 of 4 bill payments" in got["word"] and "the last 2 in a row" in got["word"]
    assert "your score itself" in got["cannot_see"]
