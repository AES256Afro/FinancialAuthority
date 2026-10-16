"""M24: sinking funds for the bills that come a few times a year."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.sinking import make_goal, sinking_funds
from app.models import Bill, Income

TODAY = date(2026, 10, 14)


def test_a_yearly_bill_is_split_across_the_paychecks_before_it_and_becomes_a_goal():
    with Session(engine) as session:
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1800.0, next_date=date(2026, 10, 16)))
        session.add(Bill(name="Car insurance", amount=600.0, due_day=15, cadence="semiannual", anchor_date=date(2026, 12, 15)))
        session.add(Bill(name="Rent", amount=1295.0, due_day=1))
        session.commit()
        funds = sinking_funds(session, TODAY)
        assert [i["name"] for i in funds["items"]] == ["Car insurance"]
        car = funds["items"][0]
        # paychecks before Dec 15: Oct 16, Oct 30, Nov 13, Nov 27, Dec 11
        assert car["due"] == "2026-12-15" and car["paychecks_left"] == 5 and car["per_paycheck"] == 120.0
        goal = make_goal(session, TODAY, car["bill_id"])
        assert goal.target == 600.0 and goal.target_date == date(2026, 12, 15)
        assert sinking_funds(session, TODAY)["items"][0]["goal_id"] == goal.id
