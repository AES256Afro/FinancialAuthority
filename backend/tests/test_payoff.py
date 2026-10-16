"""M25: debt payoff with dates and interest saved, advances by their cost."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.payoff import payoff_plan, simulate
from app.models import Advance, Debt

TODAY = date(2026, 10, 14)


def test_extra_pays_debts_off_sooner_and_avalanche_costs_least():
    debts = [{"name": "Card A", "balance": 2000.0, "apr": 29.0, "minimum": 60.0, "source": "debt:1"}, {"name": "Card B", "balance": 500.0, "apr": 12.0, "minimum": 25.0, "source": "debt:2"}]
    start = date(2026, 10, 1)
    base = simulate(debts, 0.0, "minimums", start)
    ava = simulate(debts, 200.0, "avalanche", start)
    snow = simulate(debts, 200.0, "snowball", start)
    assert ava["months"] < base["months"] and ava["interest"] < base["interest"]
    assert ava["interest"] <= snow["interest"]
    assert [d["name"] for d in sorted(snow["debts"], key=lambda d: d["paid_on"])][0] == "Card B"  # smallest first


def test_the_plan_reads_the_debts_and_prices_the_advances():
    with Session(engine) as session:
        session.add(Debt(name="Store card", balance=800.0, apr=24.0, minimum=35.0))
        session.add(Advance(provider="Example Advance", amount=100.0, fee=5.0, taken_on=date(2026, 10, 1), repay_date=date(2026, 10, 15)))
        session.commit()
        got = payoff_plan(session, TODAY, extra=100.0)
    assert got["avalanche"]["debt_free"] and got["avalanche"]["interest_saved"] > 0 and got["cheapest"] in ("avalanche", "snowball")
    adv = got["advances"][0]
    assert adv["fee_per_100"] == 5.0 and adv["days_held"] == 14.0 and adv["as_yearly_rate"] > 100  # $5 per $100 for two weeks is over 100% a year
