"""M28: what leaves the box, a switch per model, and deleting everything."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import seed
from app.models import Account, Transaction


def test_the_privacy_page_says_what_leaves_and_each_model_turns_off(owner: TestClient):
    got = owner.get("/api/v1/privacy").json()
    chat = next(m for m in got["models"] if m["role"] == "advisor")
    assert chat["on"] and chat["local"] and "your mail and your documents" in chat["sees"]  # at full, when asked
    owner.put("/api/v1/models/advisor", json={"sees": "aggregates"})
    chat = next(m for m in owner.get("/api/v1/privacy").json()["models"] if m["role"] == "advisor")
    assert "Never a name, mail, documents" in chat["sees"]
    assert {l["service"] for l in got["leaves"]} >= {"Plaid", "Teller", "Anthropic", "ntfy or Gotify"}
    off = owner.post("/api/v1/privacy/models/advisor", json={"on": False}).json()
    assert next(m for m in off["models"] if m["role"] == "advisor")["on"] is False
    assert owner.post("/api/v1/privacy/models/reviewer", json={"on": True}).status_code == 409


def test_deleting_everything_asks_three_times_and_keeps_only_the_sign_in(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    assert owner.post("/api/v1/privacy/erase", json={"phrase": "delete", "password": "correct horse battery"}).status_code == 400
    assert owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": "wrong"}).status_code == 401
    key = owner.post("/api/v1/keys", json={"name": "k", "scopes": ["read", "confirm"]}).json()
    reader = TestClient(client.app)
    assert reader.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": "correct horse battery"}, headers={"Authorization": f"Bearer {key['token']}"}).status_code == 403
    done = owner.post("/api/v1/privacy/erase", json={"phrase": "Delete everything", "password": "correct horse battery"}).json()
    assert done["ok"] and done["rows"] > 0
    with Session(engine) as session:
        assert session.exec(select(Transaction)).first() is None and session.exec(select(Account)).first() is None
    assert owner.get("/api/v1/snapshot").status_code == 200  # still signed in, with an empty plan
