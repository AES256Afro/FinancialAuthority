"""Security sweep 5, the sign-in and credentials area: a second person's key stays theirs, a wildcard CORS origin
is never honoured with the owner's cookie, and an app cannot register a script-scheme callback."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, auth, notify
from app.db import engine
from app.main import app, cors_origins
from app.models import ApiKey, Approval, MailAccount

H = {"X-Requested-With": "FinancialAuthority"}
GOOD = "plum river lantern seven"


def _join_as(owner: TestClient, username: str, role: str) -> TestClient:
    made = owner.post("/api/v1/people", json={"username": username, "display_name": "Alex Example", "role": role})
    assert made.status_code == 200, made.text
    token = made.json()["invite"]["link"].split("#", 1)[1]
    person = TestClient(app)
    person.headers.update(H)
    assert person.post("/api/auth/join", json={"token": token, "password": GOOD}).status_code == 200
    return person


def test_a_key_made_by_a_person_carries_that_person(owner: TestClient):
    """A key is the person's credential: the principal it yields names them, so what it queues is theirs."""
    person = _join_as(owner, "alex", "everything")
    pid = person.get("/api/auth/me").json()["person_id"]
    token = person.post("/api/v1/keys", json={"name": "theirs", "scopes": ["read", "confirm"]}).json()["token"]
    with Session(engine) as session:
        row = session.exec(select(ApiKey).where(ApiKey.person_id == pid)).one()
        assert row.name == "theirs"
        from starlette.requests import Request

        scope = {"type": "http", "method": "GET", "path": "/api/v1/snapshot", "headers": [(b"authorization", f"Bearer {token}".encode())], "query_string": b""}
        principal = auth.principal_from_request(Request(scope), session)
    assert principal is not None and principal.kind == "key" and principal.person_id == pid
    owner_key = owner.post("/api/v1/keys", json={"name": "owners", "scopes": ["read"]}).json()["token"]
    with Session(engine) as session:
        scope = {"type": "http", "method": "GET", "path": "/api/v1/snapshot", "headers": [(b"authorization", f"Bearer {owner_key}".encode())], "query_string": b""}
        principal = auth.principal_from_request(Request(scope), session)
    assert principal is not None and principal.person_id is None


def test_a_persons_key_queues_their_own_sends_never_the_owners(owner: TestClient, monkeypatch):
    """The owner's standing allowance covers the owner's sends. A send queued with a second person's key is that
    person's: it waits for them, and the owner's allowance never sends it."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: sent.append(kw["to"]) or "<id@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test"))
        session.commit()
        approvals.allow(session, "billing@example-power.test")  # the owner's own standing allowance
    person = _join_as(owner, "alex", "everything")
    pid = person.get("/api/auth/me").json()["person_id"]
    token = person.post("/api/v1/keys", json={"name": "theirs", "scopes": ["read", "confirm"]}).json()["token"]
    reader = TestClient(app)
    queued = reader.post("/api/v1/approvals", headers={"Authorization": f"Bearer {token}"}, json={"to": "billing@example-power.test", "subject": "Move my date", "body": "Please move it."})
    assert queued.status_code == 200, queued.text
    assert queued.json()["for_person_id"] == pid
    assert queued.json()["status"] == "pending" and sent == []
    with Session(engine) as session:
        row = session.get(Approval, queued.json()["id"])
        assert row.for_person_id == pid and row.status == "pending"


def test_a_wildcard_cors_origin_is_never_honoured_with_the_owners_cookie(caplog):
    """Starlette answers a wildcard with the asking origin whenever the request carries a cookie, which would let
    every site read the API as the owner. The wildcard is dropped; named origins stay."""
    assert cors_origins(["*"]) == []
    assert cors_origins(["https://app.example.test", "*", " "]) == ["https://app.example.test"]
    assert cors_origins([]) == []
    assert "wildcard" in caplog.text


REGISTRATION = {"client_name": "An app", "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read"}


def test_an_app_cannot_register_a_script_scheme_callback(client: TestClient):
    """A callback is where the browser is sent after Allow: https, a local http address or an app's own scheme,
    never a script, a data address or a file."""
    for bad in ("javascript:alert(1)", "data:text/html,hello", "vbscript:x", "file:///etc/hosts", "about:blank", "blob:https://x/y"):
        r = client.post("/register", json={**REGISTRATION, "redirect_uris": [bad]})
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert r.json()["error"] == "invalid_redirect_uri"
    mixed = client.post("/register", json={**REGISTRATION, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback", "javascript:alert(1)"]})
    assert mixed.status_code == 400
    for good in ("https://claude.ai/api/mcp/auth_callback", "http://localhost:9999/callback", "http://127.0.0.1:9999/callback", "financialauthority://callback", "http://openwebui.lan:3000/oauth/callback"):
        r = client.post("/register", json={**REGISTRATION, "redirect_uris": [good]})
        assert r.status_code == 201, (good, r.status_code, r.text)
