"""The published address: a signed-in page load that arrives there is recorded, and the self-check takes that
as proof the address answers when its own probe from inside the container cannot reach it."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.jobs.scheduler import self_check
from app.store import get_setting


def test_a_browser_at_the_public_address_satisfies_the_self_check(owner: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://money.example.test:8430")
    owner.get("/api/auth/me", headers={"host": "testserver"})
    with Session(engine) as session:
        assert get_setting(session, "public.last_seen", None) is None
    assert owner.get("/api/auth/me", headers={"host": "money.example.test:8430"}).status_code == 200
    with Session(engine) as session:
        seen = get_setting(session, "public.last_seen", {})
        assert seen["host"] == "money.example.test:8430"
    summary = self_check()  # the probe to money.example.test fails here; the sighting stands in for it
    assert "public address" not in summary
    with Session(engine) as session:
        report = get_setting(session, "selfcheck", {})
        assert report["public_address"]["last_seen_from_outside"] == seen["at"] and report["public_address"]["answers_from_inside"] is False
