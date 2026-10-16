"""M36: a home, a car, anything else worth something, in net worth."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.assets import value_now
from app.models import Account, Holding, OtherAsset


def test_a_car_loses_value_between_updates_and_a_home_keeps_it():
    car = OtherAsset(name="Car", kind="car", value=20000.0, value_on=date(2025, 10, 14), yearly_change=-0.15)
    home = OtherAsset(name="Home", kind="home", value=250000.0, value_on=date(2025, 10, 14))
    assert round(value_now(car, date(2026, 10, 14))) == 17002 and value_now(home, date(2026, 10, 14)) == 250000.0


def test_other_assets_count_in_net_worth_and_ask_to_be_updated(owner: TestClient):
    made = owner.post("/api/v1/assets", json={"name": "Car", "kind": "car", "value": 12000, "value_on": "2026-01-01", "update_every_days": 90, "owed": 4000}).json()
    car = made["items"][0]
    assert car["yearly_change"] == -0.15 and car["value_now"] < 12000 and car["equity"] == round(car["value_now"] - 4000, 2) and car["needs_update"]
    worth = owner.get("/api/v1/accounts/overview").json()
    assert any(a["name"] == "Car" and a.get("other") for a in worth["assets"]["items"]) and "Car" not in worth["stale"]
    steps = owner.get("/api/v1/next_steps").json()
    assert any(s["id"].startswith("asset-") for s in steps["steps"])
    assert owner.post("/api/v1/assets", json={"name": "Boat", "kind": "yacht", "value": 5}).status_code == 400
    assert owner.delete(f"/api/v1/assets/{car['id']}").json()["items"] == []


def test_holdings_say_what_they_gained_where_the_cost_is_known(owner: TestClient):
    with Session(engine) as session:
        acct = Account(name="Brokerage", kind="investment", connector="plaid", external_id="inv-1", balance=1500.0)
        session.add(acct)
        session.flush()
        session.add(Holding(account_id=acct.id, name="Index fund", value=1200.0, cost_basis=1000.0))
        session.add(Holding(account_id=acct.id, name="Cash", value=300.0))
        session.commit()
    returns = owner.get("/api/v1/assets").json()["returns"]
    row = returns["accounts"][0]
    assert row["gain"] == 200.0 and row["gain_share"] == 0.2 and row["holdings_without_cost"] == 1
