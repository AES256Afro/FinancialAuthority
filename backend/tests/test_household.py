from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.config import settings
from app.engine.household import generate, seed
from app.engine.next_steps import next_steps
from app.engine.snapshot import finance_snapshot
from app.models import Bill

TODAY = date(2026, 10, 14)


def test_generate_is_deterministic():
    a, b = generate(), generate()
    assert len(a.transactions) == len(b.transactions) > 300
    assert [t["amount"] for t in a.transactions[:50]] == [t["amount"] for t in b.transactions[:50]]
    assert generate(seed=7).transactions != a.transactions


def test_seed_then_snapshot_matches_the_mockups():
    with Session(engine) as session:
        result = seed(session)
        assert result["seeded"] and result["bills"] == 13
        assert seed(session)["seeded"] is False
        snap = finance_snapshot(session, TODAY)
    assert snap["next_paycheck"]["date"] == "2026-10-16" and snap["next_paycheck"]["days"] == 2
    assert snap["cash"] == 312.0
    assert snap["due_before_payday"]["total"] == 57.0  # streaming and tolls on the 15th
    assert snap["advances"]["total"] == 231.0
    assert snap["window"]["start"] == "2026-10-16" and snap["window"]["end"] == "2026-10-29"
    assert len(snap["projection"]) == 60
    assert isinstance(snap["safe_to_spend"]["amount"], float)
    assert all(e["source"] for e in snap["window"]["items"])


def test_snapshot_over_the_api(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["version"] == "snapshot.v0"
    found = owner.get("/api/v1/transactions/search", params={"text": "Example Fuel", "limit": 5}).json()
    assert found["returned"] == 5 and all(t["category"] == "Gas" for t in found["transactions"])


def test_seed_refuses_a_database_with_anything_of_the_owners():
    with Session(engine) as session:
        session.add(Bill(name="Internet", amount=80.0, due_day=1))
        session.commit()
        assert seed(session)["seeded"] is False
        assert session.exec(select(Bill)).all()[0].name == "Internet"


def test_example_household_outside_demo_mode_is_a_critical_step(monkeypatch):
    with Session(engine) as session:
        seed(session)
        step = next(s for s in next_steps(session, TODAY)["steps"] if s["id"] == "demo-data")
        assert step["severity"] == "critical" and step["link"] == "/settings/backups"
        monkeypatch.setattr(settings, "demo", True)
        assert all(s["id"] != "demo-data" for s in next_steps(session, TODAY)["steps"])
