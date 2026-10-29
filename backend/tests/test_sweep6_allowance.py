"""A standing allowance sends what a person queues; what a model or key queues always waits for a tap."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import approvals
from app.db import engine
from app.store import set_setting


def test_a_model_queued_draft_waits_even_for_an_allowed_address(monkeypatch):
    sent = []
    monkeypatch.setattr(approvals, "send", lambda session, row: sent.append(row.to))
    with Session(engine) as session:
        set_setting(session, "send.allowances", ["landlord@example.test"])
        session.commit()
        existing = approvals.allowances(session)
        assert "landlord@example.test" in existing
        mine = approvals.create(session, to="landlord@example.test", subject="Notice", body="Moving out on the 30th.", created_by="owner", created_kind="owner")
        assert mine.status == "approved" and mine.decided_by == "allowance" and sent == ["landlord@example.test"]
        theirs = approvals.create(session, to="landlord@example.test", subject="Cancel", body="Please cancel.", reason="asked in chat", created_by="claude", created_kind="key")
        assert theirs.status == "pending" and sent == ["landlord@example.test"]
        assert "standing allowance for this address was not used" in theirs.reason
