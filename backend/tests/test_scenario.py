"""A scenario over the real registers: nothing changed, it equals the plain projection; a deposit on a day
lowers the honest line from that day by that much; a rent change moves the monthly figure; stopping a bill
removes it; the first date that passes is found; the readiness list says what is met and what is not. Every
figure is synthetic."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.readiness import readiness
from app.engine.scenario import run_scenario
from app.engine.snapshot import finance_snapshot
from app.models import Account, Bill, Income

TODAY = date(2026, 10, 14)


def test_an_empty_scenario_is_the_plain_projection_and_a_deposit_lowers_it():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
        empty = run_scenario(session, TODAY, {"move_date": "2026-11-15"})
        assert [p["at_pace"] for p in empty["scenario"]["path"][:60]] == [p["at_pace"] for p in snap["projection"]]
        assert empty["verdict"] in ("passes", "short") and empty["monthly_change"] == 0.0 and empty["one_offs_total"] == 0.0
        deposit = run_scenario(session, TODAY, {"name": "Voyager", "move_date": "2026-11-15", "one_offs": [{"name": "Deposit", "amount": 800.0, "date": "2026-11-15"}]})
        before = {p["date"]: p["at_pace"] for p in empty["scenario"]["path"]}
        after = {p["date"]: p["at_pace"] for p in deposit["scenario"]["path"]}
        assert after["2026-11-14"] == before["2026-11-14"] and round(before["2026-11-15"] - after["2026-11-15"], 2) == 800.0
        assert deposit["one_offs_total"] == 800.0 and deposit["scenario"]["low"]["value"] <= empty["scenario"]["low"]["value"]
        day = next(w for w in deposit["windows"] if w["start"] <= "2026-11-15" <= w["end"])
        assert any(i["name"] == "Deposit" for i in day["items"]) and day["delta"] == 800.0
        rent = run_scenario(session, TODAY, {"move_date": "2026-11-15", "new_rent": 1495.0, "overlap_months": 1, "recurring": [{"name": "Parking", "amount": 75.0, "due_day": 1}]})
        assert rent["old_rent"] == 1295.0 and rent["new_rent"] == 1495.0 and rent["monthly_change"] == 275.0
        names = {i["name"] for w in rent["windows"] for i in w["items"]}
        assert any("overlap" in n for n in names) and "Parking" in names
        stop = run_scenario(session, TODAY, {"move_date": "2026-11-15", "stop_bills": [b.id for b in session.exec(__import__("sqlmodel").select(Bill)).all() if b.name == "Rent"]})
        assert stop["monthly_change"] == -1295.0 and not any(i["name"] == "Rent" and i["date"] >= "2026-11-15" for w in stop["windows"] for i in w["items"])


def test_the_first_passing_date_moves_out_when_the_move_is_too_big(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        big = run_scenario(session, TODAY, {"move_date": "2026-10-20", "one_offs": [{"name": "Deposit and first month", "amount": 2600.0, "date": "2026-10-20"}]})
    assert big["verdict"] == "short" and big["scenario"]["shortfall"] > 0 and (big["first_passes"] is None or big["first_passes"] > "2026-10-20")
    via = owner.post("/api/v1/scenarios/run", json={"name": "Voyager", "move_date": "2026-11-15", "new_rent": 1495, "one_offs": [{"name": "Deposit", "amount": 800, "date": "2026-11-15"}]}).json()
    assert via["name"] == "Voyager" and via["new_rent"] == 1495.0 and "not_advice" in via
    saved = owner.post("/api/v1/scenarios", json={"name": "Voyager", "move_date": "2026-11-15", "new_rent": 1495}).json()
    assert saved["id"] and owner.get("/api/v1/scenarios").json()["items"][0]["name"] == "Voyager"
    assert owner.delete(f"/api/v1/scenarios/{saved['id']}").json()["ok"] and owner.get("/api/v1/scenarios").json()["items"] == []
    assert owner.post("/api/v1/scenarios/run", json={"move_date": "2026-11-15", "overlap_months": 9}).status_code == 400


def test_readiness_says_what_is_met(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        session.add(Income(name="Example Staffing", cadence="semimonthly", expected_net=2200.0, expected_net_end=2000.0, next_date=date(2026, 10, 30)))
        session.commit()
        out = readiness(session, TODAY, move_date=date(2026, 12, 1), new_rent=1495.0, safe_to_spend=900.0)
    keys = [i["key"] for i in out["items"]]
    assert keys[:2] == ["buffer", "no_advances"] and "plans" in keys and "rent_to_gross" in keys and "mailbox" in keys and "reconciled" in keys
    by = {i["key"]: i for i in out["items"]}
    assert by["buffer"]["met"] is True and by["buffer"]["target"] >= 500.0
    assert by["no_advances"]["met"] is False and by["no_advances"]["projected"] is not None  # the household carries advances
    assert by["mailbox"]["met"] is False and by["reconciled"]["met"] is False and out["ready"] is False
    assert owner.post("/api/v1/scenarios/reconciled").json()["ok"]
    again = owner.get("/api/v1/scenarios/readiness?move_date=2026-12-01&new_rent=1495").json()
    assert {i["key"]: i["met"] for i in again["items"]}["reconciled"] is True and again["of"] == len(again["items"])
