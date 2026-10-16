"""Setup that checks itself: every step has a state and, when it is not done, a fix and a link."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed


def test_a_new_install_lists_what_to_do(owner: TestClient):
    got = owner.get("/api/v1/setup/check").json()
    by_id = {c["id"]: c for c in got["checks"]}
    assert by_id["paycheck"]["state"] == "todo" and by_id["mail"]["state"] == "todo" and by_id["bank"]["state"] == "todo"
    assert all(c["fix"] and c["link"] for c in got["checks"] if c["state"] != "ok")
    assert sum(got["summary"].values()) == len(got["checks"])


def test_the_household_shows_its_mailboxes_and_test_runs(owner: TestClient, monkeypatch):
    import app.connectors.mail as mail

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(mail, "test_account", refused)  # no network in tests: the failure is explained, not raised
    with Session(engine) as session:
        seed(session)
    owner.post("/api/v1/mail/accounts", json={"kind": "gmail", "label": "Gmail", "username": "owner@example.test", "password": "not-a-real-password"})
    got = owner.get("/api/v1/setup/check").json()
    by_id = {c["id"]: c for c in got["checks"]}
    assert by_id["paycheck"]["state"] == "ok" and any(k.startswith("mail-") for k in by_id)
    tested = owner.post("/api/v1/setup/test", json={}).json()
    gmail = next(t for t in tested["tests"] if t["title"] == "Mail: Gmail")
    assert gmail["ok"] is False and gmail["detail"]
