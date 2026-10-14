from fastapi.testclient import TestClient


def test_health_is_public(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_protected_without_session(client: TestClient):
    assert client.get("/api/v1/accounts").status_code == 401


def test_login_and_me(client: TestClient):
    bad = client.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert good.status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["kind"] == "owner" and "admin" in me["scopes"]
    assert client.get("/api/v1/accounts").status_code == 200


def test_lockout_after_ten_failures(client: TestClient):
    for _ in range(10):
        client.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    r = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert r.status_code == 429


def test_csrf_header_required_for_cookie_writes(owner: TestClient):
    owner.headers.pop("X-Requested-With")
    r = owner.put("/api/v1/settings/buffer", json={"value": 300})
    assert r.status_code == 403
    owner.headers["X-Requested-With"] = "FinancialAuthority"
    r = owner.put("/api/v1/settings/buffer", json={"value": 300})
    assert r.status_code == 200 and r.json()["buffer"] == 300


def test_api_keys_scopes(owner: TestClient, client: TestClient):
    created = owner.post("/api/v1/keys", json={"name": "claude-code", "scopes": ["read"], "redaction": "full"}).json()
    token = created["token"]
    assert token.startswith("fa_")
    anon = TestClient(client.app)
    assert anon.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert anon.get("/api/v1/keys", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert anon.get("/api/v1/snapshot", headers={"Authorization": "Bearer fa_not_a_key"}).status_code == 401
    forbidden = owner.post("/api/v1/keys", json={"name": "x", "scopes": ["approve"]})
    assert forbidden.status_code == 400
    owner.delete(f"/api/v1/keys/{created['id']}")
    assert anon.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {token}"}).status_code == 401
