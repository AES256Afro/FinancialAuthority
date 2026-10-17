"""M46: leaving the advance apps. Each paycheck takes only the advance its shortfall needs, paid back with its fee at
the next, and the plan says from which paycheck none is needed and what that saves."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.advance_exit import exit_plan
from app.engine.household import seed

TODAY = date(2026, 10, 14)


def test_no_advances_means_nothing_to_leave(owner: TestClient):
    assert owner.get("/api/v1/borrowing/exit").json()["state"] == "none"


def test_each_paycheck_borrows_only_its_shortfall_and_the_parts_add_up():
    with Session(engine) as session:
        seed(session)
        plan = exit_plan(session, TODAY)
    assert plan["state"] in ("exits", "short") and plan["steps"]
    prev = 0.0
    for s in plan["steps"]:
        assert abs(s["pays_back"] - round(prev * (1 + plan["fee_per_100"] / 100), 2)) <= 0.05
        assert s["borrow"] >= 0 and s["borrow"] % 5 == 0
        assert abs(s["left"] - (s["left_with_no_new_advance"] - s["pays_back"] + s["borrow"])) <= 0.05
        assert s["left"] >= -0.01  # never short
        prev = s["borrow"]
    if plan["state"] == "exits":
        assert plan["free_from"] and plan["fees_saved"] >= 0
