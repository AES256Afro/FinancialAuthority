"""M22: a budget for each paycheck, on the owner's own pay days."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.cadence import previous_paydays
from app.models import Account, Income, Transaction

TODAY = date(2026, 10, 14)


def test_paydays_step_back_by_their_own_cadence():
    assert previous_paydays(date(2026, 10, 30), "biweekly", 2) == [date(2026, 10, 16), date(2026, 10, 2)]
    assert previous_paydays(date(2026, 10, 23), "weekly", 1) == [date(2026, 10, 16)]
    assert previous_paydays(date(2026, 10, 30), "monthly", 2) == [date(2026, 9, 30), date(2026, 8, 30)]
    assert previous_paydays(date(2026, 10, 30), "semimonthly", 2) == [date(2026, 10, 15), date(2026, 9, 30)]
    assert previous_paydays(date(2026, 10, 15), "semimonthly", 1, "1,15") == [date(2026, 10, 1)]


def _household(session: Session) -> Account:
    chk = Account(name="Checking", kind="checking", connector="plaid", external_id="a-chk", balance=1200.0)
    session.add(chk)
    session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1800.0, next_date=date(2026, 10, 16)))
    session.flush()
    rows = [(date(2026, 9, 20), 150.0, "Groceries"), (date(2026, 10, 3), 90.0, "Groceries"), (date(2026, 10, 9), 70.0, "Groceries"), (date(2026, 10, 12), 45.0, "Dining & Drinks")]
    for i, (when, amount, category) in enumerate(rows):
        session.add(Transaction(account_id=chk.id, date=when, merchant=f"Shop {i}", amount=amount, category=category, source="plaid", row_hash=f"b-{i}"))
    session.commit()
    return chk


def test_a_budget_counts_from_the_last_payday_and_carries_what_was_left(owner: TestClient):
    with Session(engine) as session:
        _household(session)
    plan = owner.put("/api/v1/budgets/Groceries", json={"per_paycheck": 200, "rollover": "next"}).json()
    span = plan["span"]
    assert span["start"] == "2026-10-02" and span["end"] == "2026-10-15" and span["next_payday"] == "2026-10-16"
    g = next(i for i in plan["items"] if i["category"] == "Groceries")
    # spent this paycheck: 90 + 70; the paycheck before (Sep 18 to Oct 1) spent 150 of 200, so 50 carries in
    assert g["spent"] == 160.0 and g["carried"] == 50.0 and g["allowed"] == 250.0 and g["left"] == 90.0
    assert plan["unbudgeted"]["top"] == [{"category": "Dining & Drinks", "spent": 45.0}]
    assert isinstance(plan["fits"], bool) and plan["safe_to_spend"] == owner.get("/api/v1/snapshot").json()["safe_to_spend"]["amount"]
    over = owner.put("/api/v1/budgets/Groceries", json={"per_paycheck": 100, "rollover": "none"}).json()
    g = next(i for i in over["items"] if i["category"] == "Groceries")
    assert g["status"] == "crit" and g["word"].startswith("Over by") and g["carried"] == 0.0
    assert owner.put("/api/v1/budgets/Groceries", json={"per_paycheck": -5}).status_code == 400
    gone = owner.delete("/api/v1/budgets/Groceries").json()
    assert all(i["category"] != "Groceries" for i in gone["items"])
