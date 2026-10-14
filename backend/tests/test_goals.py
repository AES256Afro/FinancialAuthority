"""Goals: the pace per paycheck to land on the date, on-track or not, what they take from safe-to-spend."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.goals import goals_plan, paychecks_until
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot

TODAY = date(2026, 10, 14)


def test_paychecks_until_counts_the_next_one():
    assert paychecks_until(TODAY, date(2026, 10, 16), date(2026, 10, 16)) == 1
    assert paychecks_until(TODAY, date(2026, 12, 24), date(2026, 10, 16)) == 5  # Oct 16, Oct 30, Nov 13, Nov 27, Dec 11
    assert paychecks_until(TODAY, date(2026, 10, 15), date(2026, 10, 16)) == 0
    assert paychecks_until(TODAY, None, date(2026, 10, 16)) is None


def test_goals_take_from_the_paycheck_and_the_snapshot_shows_it(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        before = finance_snapshot(session, TODAY)
    made = owner.post("/api/v1/goals", json={"name": "Winter tyres", "target": 600, "target_date": "2026-12-24", "funded": 0}).json()
    goal = next(g for g in made["items"] if g["name"] == "Winter tyres")
    others = round(sum(g["per_paycheck"] for g in made["items"] if g["name"] != "Winter tyres"), 2)
    assert goal["paychecks_left"] == 5 and goal["needed_per_paycheck"] == 120.0 and goal["per_paycheck"] == 120.0 and goal["on_track"] is True
    assert made["per_paycheck"] == round(120.0 + others, 2)
    mine = lambda out: next(g for g in out["items"] if g["name"] == "Winter tyres")  # noqa: E731
    paced = mine(owner.put(f"/api/v1/goals/{goal['id']}", json={"pace": 50}).json())
    assert paced["per_paycheck"] == 50.0 and paced["on_track"] is False and paced["done_by"] > "2026-12-24"
    funded = mine(owner.post(f"/api/v1/goals/{goal['id']}/fund", json={"amount": 350}).json())
    assert funded["funded"] == 350.0 and funded["remaining"] == 250.0 and funded["on_track"] is True  # 50 × 5 covers 250
    with Session(engine) as session:
        after = finance_snapshot(session, TODAY)
        plan = goals_plan(session, TODAY, date.fromisoformat(after["next_paycheck"]["date"]))
    assert after["goals"]["per_paycheck"] == round(50.0 + others, 2) == plan["per_paycheck"]
    assert after["safe_to_spend"]["after_goals"] == round(before["safe_to_spend"]["amount"] - after["goals"]["per_paycheck"], 2)
    assert after["safe_to_spend"]["amount"] == before["safe_to_spend"]["amount"]  # the headline is unchanged; goals are shown beside it
    assert any("goal" in b["text"].lower() for b in after["brief"])
    assert owner.post("/api/v1/goals", json={"name": "", "target": 10}).status_code == 400
    gone = owner.delete(f"/api/v1/goals/{goal['id']}").json()
    assert all(g["name"] != "Winter tyres" for g in gone["items"])
