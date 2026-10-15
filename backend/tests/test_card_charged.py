"""A bill charged to the card does not leave checking on its due date: it rides the card payment it is paid
from, with the card's balance and the usual card spend until then. The pace is split by what pays it, a bill
learns its paying account from its own charges, and the owner can change it. Every figure is synthetic."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors.banks import upsert_account
from app.db import engine
from app.engine.snapshot import finance_snapshot, usual_pace
from app.jobs.detect import run_detection
from app.models import Account, Bill, Income, RecurringSeries, Transaction
from app.store import set_setting

TODAY = date(2026, 10, 15)


def _setup(session: Session) -> tuple[Account, Account]:
    chk = Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=3000.0, connector="demo", external_id="cc:chk")
    session.add(chk)
    session.flush()
    card = upsert_account(session, provider="demo", external_id="cc:card", name="Everyday Card", institution="Example Bank", kind="card", balance=1000.0, credit_limit=1650.0)
    session.add(Income(name="Example Staffing", cadence="semimonthly", expected_net=2200.0, expected_net_end=2000.0, next_date=date(2026, 10, 30)))
    set_setting(session, "cards", {"pay_from": "mid"})  # the card is paid in full from the 15th check
    # eight weeks of everyday spending: $20 a day on the card, $10 a day from checking
    for i in range(1, 57):
        d = TODAY - timedelta(days=i)
        session.add(Transaction(account_id=card.id, date=d, merchant="Example Grocer", raw_name="GROCER", description="", amount=20.0, category="Groceries", source="demo", source_ref=f"c{i}", row_hash=f"cc-card-{i}"))
        session.add(Transaction(account_id=chk.id, date=d, merchant="Example Fuel", raw_name="FUEL", description="", amount=10.0, category="Gas", source="demo", source_ref=f"k{i}", row_hash=f"cc-cash-{i}"))
    session.commit()
    return chk, card


def test_a_card_charged_bill_rides_the_card_payment(owner: TestClient):
    with Session(engine) as session:
        chk, card = _setup(session)
        session.add(Bill(name="Example Streaming", kind="subscription", amount=80.0, amount_mode="fixed", cadence="monthly", due_day=20, autopay=True, pay_from_account_id=card.id, category="Streaming"))
        session.add(Bill(name="Example Apartments", kind="bill", amount=1500.0, amount_mode="fixed", cadence="monthly", due_day=2, paid_from="end", pay_from_account_id=chk.id, category="Rent"))
        session.commit()
        pace = usual_pace(session, TODAY, set())
        assert pace["per_day_card"] == 20.0 and pace["per_day_cash"] == 10.0 and pace["per_day"] == 30.0
        snap = finance_snapshot(session, TODAY)
    before = snap["due_before_payday"]
    streaming = next(e for e in before["items"] if e["name"] == "Example Streaming")
    assert streaming["via_card"] is True and before["total"] == 0.0  # listed, not taken from checking before payday
    assert snap["on_card"]["total"] == 80.0 and snap["on_card"]["paid_on"] == "2026-11-13"
    payoff = next(e for e in snap["windows"][1]["bills"]["items"] if e["kind"] == "card_payment")
    assert payoff["date"] == "2026-11-13" and payoff["parts"]["balance"] == 1000.0 and payoff["parts"]["bills"] == 80.0 and payoff["parts"]["spend"] == 20.0 * 29
    assert payoff["amount"] == round(1000.0 + 80.0 + 580.0, 2)
    assert not any(e["kind"] == "card_payment" for e in snap["windows"][0]["bills"]["items"])  # the end-of-month check does not pay the card
    assert snap["safe_to_spend"]["parts"]["due_before_payday"] == 0.0 and snap["usual_pace"]["per_day_cash"] == 10.0
    assert snap["projection"][1]["at_pace"] == round(snap["projection"][0]["at_pace"] - 10.0, 2)  # the daily line spends only what leaves checking
    rent = next(e for e in snap["window"]["items"] if e["name"] == "Example Apartments")
    assert rent.get("via_card") is None
    bills = {b["name"]: b for b in owner.get("/api/v1/bills").json()["bills"]}
    moved = owner.put(f"/api/v1/bills/{bills['Example Streaming']['id']}", json={"pay_from_account_id": chk.id}).json()
    assert moved["pay_from_account_id"] == chk.id
    with Session(engine) as session:
        again = finance_snapshot(session, TODAY)
    assert again["due_before_payday"]["total"] == 80.0 and again["on_card"]["total"] == 0.0


def test_a_bill_learns_its_paying_account_from_its_charges():
    with Session(engine) as session:
        chk, card = _setup(session)
        for i in range(4):
            session.add(Transaction(account_id=card.id, date=date(2026, 6, 20) + timedelta(days=30 * i), merchant="Streamflix", raw_name="STREAMFLIX", description="", amount=15.99, category="Streaming", source="demo", source_ref=f"s{i}", row_hash=f"cc-stream-{i}"))
        session.commit()
        run_detection(session, TODAY)
        series = session.exec(select(RecurringSeries).where(RecurringSeries.merchant == "Streamflix")).one()
        session.add(Bill(name="Streamflix", kind="subscription", amount=15.99, amount_mode="fixed", cadence="monthly", due_day=20, series_id=series.id))
        session.commit()
        out = run_detection(session, TODAY)
        assert out["paying_accounts_filled"] == 1
        bill = session.exec(select(Bill).where(Bill.name == "Streamflix")).one()
        assert bill.pay_from_account_id == card.id
