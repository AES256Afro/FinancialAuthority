import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

REDIRECT = "http://localhost:9999/callback"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client: TestClient) -> dict:
    r = client.post("/register", json={
        "client_name": "Claude on the phone", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read confirm",
    })
    assert r.status_code == 201, r.text
    return r.json()


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _initialize(client: TestClient, bearer: dict[str, str]):
    return client.post("/mcp", headers={**bearer, **MCP_HEADERS}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})


def test_metadata_and_registration(client: TestClient):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"].endswith("/authorize") and meta["token_endpoint"].endswith("/token")
    assert "S256" in meta["code_challenge_methods_supported"]
    resource = client.get("/.well-known/oauth-protected-resource")
    assert resource.status_code == 200
    info = register(client)
    assert info["client_id"] and info["client_name"] == "Claude on the phone"


def test_full_flow_with_consent_sign_in_refresh_and_revoke(client: TestClient, live: TestClient):
    info = register(client)
    verifier, challenge = pkce()
    anon = TestClient(client.app, follow_redirects=False)
    start = anon.get("/authorize", params={"response_type": "code", "client_id": info["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", "scope": "read confirm"})
    assert start.status_code in (302, 303, 307), start.text
    consent_url = start.headers["location"]
    assert "/oauth/consent?req=" in consent_url
    req = parse_qs(urlparse(consent_url).query)["req"][0]
    page = anon.get(consent_url)
    assert page.status_code == 200 and "Sign in to continue" in page.text
    wrong = anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": "nope"})
    assert "Wrong username" in wrong.text
    signed = anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": "correct horse battery"})
    assert signed.status_code == 200 and "Let Claude on the phone in?" in signed.text and "fa_session" in signed.headers.get("set-cookie", "")
    allowed = anon.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": ["read", "confirm"]})
    assert allowed.status_code == 302
    back = urlparse(allowed.headers["location"])
    assert back.netloc == "localhost:9999"
    query = parse_qs(back.query)
    assert query["state"] == ["xyz"] and query["code"][0].startswith("fa_oc_")

    token = anon.post("/token", data={"grant_type": "authorization_code", "code": query["code"][0], "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": REDIRECT})
    assert token.status_code == 200, token.text
    tokens = token.json()
    assert tokens["access_token"].startswith("fa_oa_") and tokens["refresh_token"].startswith("fa_or_") and "confirm" in tokens["scope"]
    replay = anon.post("/token", data={"grant_type": "authorization_code", "code": query["code"][0], "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": REDIRECT})
    assert replay.status_code == 400

    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert anon.get("/api/v1/snapshot", headers=bearer).status_code == 200
    assert anon.get("/api/v1/keys", headers=bearer).status_code == 403  # read and confirm, not admin
    opened = _initialize(live, bearer)
    assert opened.status_code == 200, opened.text
    assert _initialize(live, {}).status_code == 401

    refreshed = anon.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": info["client_id"]})
    assert refreshed.status_code == 200, refreshed.text
    new_tokens = refreshed.json()
    assert new_tokens["access_token"] != tokens["access_token"]
    stale = anon.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": info["client_id"]})
    assert stale.status_code == 400

    owner = TestClient(client.app)
    owner.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    owner.headers["X-Requested-With"] = "FinancialAuthority"
    listed = owner.get("/api/v1/oauth/clients").json()["clients"]
    assert listed[0]["name"] == "Claude on the phone" and listed[0]["sessions"] == 1
    revoked = owner.delete(f"/api/v1/oauth/clients/{info['client_id']}").json()
    assert revoked["tokens_revoked"] >= 2
    assert anon.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}).status_code == 401


def test_deny_sends_an_error_back(client: TestClient):
    info = register(client)
    _, challenge = pkce()
    anon = TestClient(client.app, follow_redirects=False)
    start = anon.get("/authorize", params={"response_type": "code", "client_id": info["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1"})
    req = parse_qs(urlparse(start.headers["location"]).query)["req"][0]
    anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": "correct horse battery"})
    denied = anon.post("/oauth/consent", data={"req": req, "decision": "deny"})
    assert denied.status_code == 302 and "error=access_denied" in denied.headers["location"] and "state=s1" in denied.headers["location"]


def test_api_key_reaches_mcp_and_lists_tools(owner: TestClient, live: TestClient):
    made = owner.post("/api/v1/keys", json={"name": "script", "scopes": ["read"], "redaction": "full"}).json()
    bearer = {"Authorization": f"Bearer {made['token']}"}
    first = _initialize(live, bearer)
    assert first.status_code == 200, first.text
    session_id = first.headers.get("mcp-session-id")
    assert session_id
    live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": session_id}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": session_id}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert listed.status_code == 200, listed.text
    assert "finance_snapshot" in listed.text and "brief_today" in listed.text
    assert _initialize(live, {"Authorization": "Bearer fa_nope"}).status_code == 401


def test_issuer_takes_https_or_the_loopback_and_never_a_plain_address(monkeypatch):
    from app import oauth as oauth_module
    from app.config import settings

    monkeypatch.setattr(settings, "public_url", "https://money.example.test/")
    assert oauth_module.issuer() == "https://money.example.test"
    monkeypatch.setattr(settings, "public_url", "http://localhost:8430")
    assert oauth_module.issuer() == "http://localhost:8430"
    monkeypatch.setattr(settings, "public_url", "http://10.0.0.5:8430")
    assert oauth_module.issuer() == f"http://localhost:{settings.port}"  # the app keeps running; OAuth waits for https
    monkeypatch.setattr(settings, "public_url", "")
    assert oauth_module.issuer() == f"http://localhost:{settings.port}"


def test_the_native_app_signs_in_with_its_own_callback_and_reads_the_glance(client: TestClient):
    from app.engine.household import seed
    from sqlmodel import Session
    from app.db import engine

    with Session(engine) as session:
        seed(session)
    anon = TestClient(client.app)
    callback = "financialauthority://oauth/callback"
    r = anon.post("/register", json={"client_name": "FinancialAuthority for iPhone", "redirect_uris": [callback], "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read"})
    assert r.status_code == 201, r.text
    info = r.json()
    verifier, challenge = pkce()
    start = anon.get("/authorize", params={"response_type": "code", "client_id": info["client_id"], "redirect_uri": callback, "code_challenge": challenge, "code_challenge_method": "S256", "state": "app", "scope": "read"}, follow_redirects=False)
    req = parse_qs(urlparse(start.headers["location"]).query)["req"][0]
    page = anon.get(start.headers["location"])
    assert "financialauthority:" in page.headers["content-security-policy"] and "fonts.googleapis" not in page.text
    anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": "correct horse battery"})
    allowed = anon.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": ["read"]}, follow_redirects=False)
    assert allowed.status_code == 302 and allowed.headers["location"].startswith(callback + "?")
    code = parse_qs(urlparse(allowed.headers["location"]).query)["code"][0]
    tokens = anon.post("/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": callback}).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    glance = anon.get("/api/v1/glance", headers=bearer)
    assert glance.status_code == 200 and glance.json()["text"].startswith("Safe to spend")
    assert anon.get("/api/v1/cover", headers=bearer).status_code == 200 and anon.get("/api/v1/paychecks", headers=bearer).status_code == 200
    assert anon.post("/api/v1/checkin", headers=bearer, json={}).status_code == 403  # read only: it cannot write
