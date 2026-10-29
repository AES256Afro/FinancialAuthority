"""Sweep 15, part 2: write a register through the screens' routes, read it back through the GET the screens use.

Every register was written with typed values (cents, dates, booleans, optional fields), changed, cleared and partly
updated, then read back. What did not come back as typed is reduced here."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed


def test_a_car_whose_yearly_change_is_cleared_keeps_its_value_as_set(owner: TestClient):
    """The Accounts page sends an emptied "loses a year" box as null and says such an asset keeps its value. An edit
    with null put the car's usual 15% back, so the page read "loses 15% a year" again and its value kept falling."""
    made = owner.post("/api/v1/assets", json={"name": "Example Car", "kind": "car", "value": 9000.99, "value_on": "2026-10-01", "yearly_change": -0.2, "update_every_days": 90, "owed": 1200.5, "note": "x"})
    assert made.status_code == 200, made.text
    car = next(a for a in made.json()["items"] if a["name"] == "Example Car")
    assert car["yearly_change"] == -0.2
    cleared = owner.put(f"/api/v1/assets/{car['id']}", json={"name": "Example Car", "kind": "car", "value": 9000.99, "value_on": "2026-10-01", "yearly_change": None, "update_every_days": 90, "owed": 0, "note": ""})
    assert cleared.status_code == 200, cleared.text
    car = next(a for a in owner.get("/api/v1/assets").json()["items"] if a["id"] == car["id"])
    assert car["yearly_change"] is None and car["value_now"] == 9000.99 and car["owed_against"] == 0
    # A new car typed without a rate still gets the usual one.
    fresh = owner.post("/api/v1/assets", json={"name": "Example Second Car", "kind": "car", "value": 5000.0, "value_on": "2026-10-01"}).json()
    assert next(a for a in fresh["items"] if a["name"] == "Example Second Car")["yearly_change"] == -0.15


def test_a_semimonthly_end_check_cleared_reads_back_as_the_same_as_the_mid_check(owner: TestClient):
    """Paycheck setup: the end-of-month check's own figure, emptied ("same"), stayed on file, so every window kept
    planning the old end check."""
    with Session(engine) as session:
        seed(session)
    made = owner.post("/api/v1/incomes", json={"name": "Example Job", "cadence": "semimonthly", "expected_net": "1850.25", "next_date": "2026-10-30"})
    assert made.status_code == 200, made.text
    iid = made.json()["id"]
    body = {"cadence": "semimonthly", "pay_days": "", "expected_net": 1850.25, "next_date": "2026-10-30"}
    assert owner.put(f"/api/v1/incomes/{iid}", json={**body, "expected_net_end": 1900.01}).status_code == 200
    assert owner.put(f"/api/v1/incomes/{iid}", json={**body, "expected_net_end": None}).status_code == 200
    income = next(i for i in owner.get("/api/v1/incomes").json()["incomes"] if i["id"] == iid)
    assert income["expected_net_end"] is None


def test_a_saved_scenario_keeps_the_start_of_a_recurring_addition(owner: TestClient):
    spec = {"name": "Example Move", "move_date": "2027-01-15", "recurring": [{"name": "Example Parking", "amount": 25.0, "due_day": 3, "from": "2027-02-01"}]}
    saved = owner.post("/api/v1/scenarios", json=spec)
    assert saved.status_code == 200, saved.text
    row = next(s for s in owner.get("/api/v1/scenarios").json()["items"] if s["id"] == saved.json()["id"])
    assert row["spec"]["recurring"][0]["from"] == "2027-02-01"
