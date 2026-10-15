"""What the engine says when it does not know: no paycheck on file, no bills, unnamed series. Every figure it
still computes carries those caveats, and the payday is marked as not known rather than invented."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.decisions import billing_date_suggestions
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot

TODAY = date(2026, 10, 14)


def test_an_empty_register_is_said_not_guessed(owner: TestClient):
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["next_paycheck"]["known"] is False and snap["next_paycheck"]["source"] is None
    assert any(c.startswith("No paycheck") for c in snap["caveats"]) and any(c.startswith("No bills") for c in snap["caveats"])
    moves = owner.get("/api/v1/bills/suggestions").json()
    assert any(c.startswith("No paycheck") for c in moves["caveats"])
    check = owner.get("/api/v1/check?amount=50").json()
    assert any(c.startswith("No paycheck") for c in check["caveats"])
    with Session(engine) as session:
        seed(session)
        after = finance_snapshot(session, TODAY)
        assert after["next_paycheck"]["known"] is True and not any(c.startswith("No paycheck") for c in after["caveats"])
        assert not any(c.startswith("No bills") for c in after["caveats"])
        assert not any("No paycheck" in c for c in billing_date_suggestions(session, TODAY)["caveats"])
