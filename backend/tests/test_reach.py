"""Reach: the app's own push to a subscribed device (the address kept encrypted, a gone device dropped), the
reminders sent once each from the chosen hour, and the glance a Shortcut, a widget or a watch shows."""

import json
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import notify, webpush
from app.db import engine
from app.engine.household import seed
from app.engine.reminders import due_today, run
from app.models import Setting
from app.store import get_setting

TODAY = date(2026, 10, 14)
ENDPOINT = "https://push.example.test/send/abc123"


def _subscribe(owner: TestClient) -> dict:
    return owner.post("/api/v1/push/devices", json={"endpoint": ENDPOINT, "keys": {"p256dh": "BEl6" + "A" * 80, "auth": "secretauth"}, "label": "Phone"}).json()


def test_a_device_subscribes_and_gets_the_push(owner: TestClient, client: TestClient, monkeypatch):
    info = owner.get("/api/v1/push/devices").json()
    assert len(info["public_key"]) >= 86 and info["devices"] == []
    made = _subscribe(owner)
    assert made["device"]["label"] == "Phone" and len(made["devices"]) == 1
    with Session(engine) as session:
        raw = session.get(Setting, "webpush.subs").value
        assert ENDPOINT not in raw and "secretauth" not in raw  # kept encrypted
        assert "BEGIN PRIVATE KEY" not in session.get(Setting, "webpush.vapid").value
    calls = []
    monkeypatch.setattr(webpush, "webpush", lambda **kw: calls.append(kw))
    with Session(engine) as session:
        out = notify.send(session, "Safe to spend $55", "Payday tomorrow.", link="/today", tags=["moneybag"])
    assert out["ok"] and out["devices"]["sent"] == 1 and not out["skipped"] and out["channel"]["skipped"]
    sent = calls[0]
    assert sent["subscription_info"]["endpoint"] == ENDPOINT and sent["subscription_info"]["keys"]["auth"] == "secretauth"
    assert json.loads(sent["data"]) == {"title": "Safe to spend $55", "body": "Payday tomorrow.", "link": "/today", "tag": "moneybag"}
    assert sent["vapid_claims"]["sub"].startswith("http")
    # with only a device and no ntfy, the morning brief is due
    with Session(engine) as session:
        assert notify.morning_brief_due(session, datetime(2026, 10, 14, 23, 0, tzinfo=timezone.utc))

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def gone(**kw):
        raise Gone()

    monkeypatch.setattr(webpush, "webpush", gone)
    with Session(engine) as session:
        notify.send(session, "x", "y")
    assert owner.get("/api/v1/push/devices").json()["devices"] == []
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"]}).json()["token"]
    reader = TestClient(client.app)
    assert reader.post("/api/v1/push/devices", json={"endpoint": ENDPOINT, "keys": {"p256dh": "x", "auth": "y"}}, headers={"Authorization": f"Bearer {key}"}).status_code == 403
    assert owner.post("/api/v1/push/devices", json={"endpoint": "http://plain.test/x", "keys": {"p256dh": "x", "auth": "y"}}).status_code == 400


def test_reminders_are_sent_once_from_the_hour(owner: TestClient, monkeypatch):
    with Session(engine) as session:
        seed(session)
    _subscribe(owner)
    owner.put("/api/v1/push/reminders", json={"bills_days": 2, "hour": 0, "checkin": True})
    with Session(engine) as session:
        items = due_today(session, TODAY)
    # two days on is payday, when the two advances repay
    assert any(i["key"].startswith("bill:advance:") for i in items) and all(i["link"].startswith("/") for i in items)
    with Session(engine) as session:
        sunday = due_today(session, date(2026, 10, 18))
    assert any(i["key"] == "checkin:2026-W42" for i in sunday)
    with Session(engine) as session:
        payday = due_today(session, date(2026, 10, 16))
    assert any(i["key"] == "payday:2026-10-16" for i in payday)
    calls = []
    monkeypatch.setattr(webpush, "webpush", lambda **kw: calls.append(kw))
    with Session(engine) as session:
        first = run(session, datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc), TODAY)
        again = run(session, datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc), TODAY)
        sent_log = get_setting(session, "reminders.sent", {})
    assert first["sent"] == len(items) > 0 and again["sent"] == 0 and len(calls) == len(items) and len(sent_log) == len(items)
    shown = owner.get("/api/v1/push/reminders").json()
    assert shown["settings"]["bills_days"] == 2 and len(shown["today"]) == len(items)


def test_the_glance_says_it_in_one_line(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    g = owner.get("/api/v1/glance").json()
    assert g["safe_to_spend"] == 55.0 and g["through"] == "2026-10-15" and g["payday"]["days"] == 2
    assert g["text"].startswith("Safe to spend $55.00 through Oct 15. Payday in 2 days, $2,240.00.") and g["one_thing"] in g["text"]
    assert g["next_bill"]["date"] > "2026-10-14" and g["status"] in ("good", "warn", "crit")
