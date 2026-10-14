"""Push through ntfy and Gotify against a mocked server; the morning brief once a day; the settings route."""

from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import notify
from app.db import engine
from app.engine.household import seed


def test_ntfy_and_gotify_requests_and_the_daily_gate(owner: TestClient, monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")  # the brief hour is local to the owner
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731

    assert owner.get("/api/v1/push").json()["configured"] is False
    saved = owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "money", "token": "tk_secret_123", "brief_hour": 7}).json()
    assert saved["configured"] and saved["token_set"] and saved["brief_hour"] == 7 and "tk_secret" not in str(saved)
    assert owner.put("/api/v1/push", json={"kind": "pigeon"}).status_code == 400

    with Session(engine) as session:
        result = notify.send(session, "Hello", "body text", priority="high", tags=["moneybag"], link="https://app.example.test/today", client=client())
        assert result["ok"]
        req = seen[-1]
        assert str(req.url) == "https://ntfy.example.test/money" and req.headers["Title"] == "Hello" and req.headers["Priority"] == "high"
        assert req.headers["Authorization"] == "Bearer tk_secret_123" and req.headers["Tags"] == "moneybag" and req.headers["Click"].endswith("/today") and req.content == b"body text"

        notify.save_push(session, {"kind": "gotify", "url": "https://gotify.example.test/", "token": "gk_456"})
        result = notify.send(session, "Hi", "there", client=client())
        req = seen[-1]
        assert str(req.url) == "https://gotify.example.test/message" and req.headers["X-Gotify-Key"] == "gk_456"
        assert req.headers["content-type"].startswith("application/json") and b'"title":"Hi"' in req.content

        # The morning brief: not before the hour, once from the hour on, again the next day.
        early = datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)  # 06:00 in America/Chicago
        late = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)  # 08:30
        assert notify.morning_brief_due(session, early) is False
        assert notify.morning_brief_due(session, late) is True
        notify.mark_brief_sent(session, late)
        assert notify.morning_brief_due(session, late) is False
        assert notify.morning_brief_due(session, datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc)) is True

        notify.save_push(session, {"kind": "off"})
        assert notify.send(session, "x", "y", client=client())["skipped"] is True


def test_brief_push_carries_the_engine_sentences(owner: TestClient, monkeypatch):
    with Session(engine) as session:
        seed(session)
    sent: dict = {}

    def fake_send(session, title, body, **kwargs):  # noqa: ANN001
        sent.update({"title": title, "body": body, **kwargs})
        return {"ok": True, "status": 200, "detail": ""}

    monkeypatch.setattr(notify, "send", fake_send)
    owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "money"})
    assert owner.post("/api/v1/push/brief").json()["ok"]
    assert sent["title"].startswith("Safe to spend") and "clears before payday" in sent["body"]
