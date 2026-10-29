"""Security sweep 10: the black-box round, run against the real server from outside (sign-in on, an installed copy,
the synthetic household). What held is listed in docs/SECURITY.md; what did not is a test here, written to fail
first, with the fix beside it in backend/app.

Found: uvicorn believes X-Forwarded-For from a loopback peer by default and rewrites the request's client address
before the app sees it, so when the app is reached through anything on the same host (the dev launcher, a reverse
proxy on localhost, host networking) every wrong sign-in could carry a fresh address and the per-address lockout
never closed; the passkey routes kept their own count rather than sharing the sign-in's; and every response carried
the server's banner."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import auth
from app.main import app

PW = "correct horse battery"
ROOT = Path(__file__).resolve().parents[2]


def _from(address: str) -> TestClient:
    """A client whose connection the server sees as coming from `address`: what uvicorn's proxy-headers middleware
    produces when it believes a forwarding header."""
    return TestClient(app, client=(address, 40000))


def test_a_forwarding_header_does_not_hand_each_wrong_sign_in_a_fresh_address():
    """No proxy is named in FA_TRUSTED_PROXIES, so a forwarding header is nobody's word; when the server itself
    has already rewritten the peer from one, the lockout must still close after ten wrong tries."""
    for i in range(auth.LOCK_AFTER):
        r = _from(f"203.0.113.{i}").post("/api/auth/login", json={"username": f"nobody{i}", "password": "x" * 12}, headers={"x-forwarded-for": f"203.0.113.{i}"})
        assert r.status_code == 401, r.text
    r = _from("203.0.113.200").post("/api/auth/login", json={"username": "owner", "password": PW}, headers={"x-forwarded-for": "203.0.113.200"})
    assert r.status_code == 429, "a fresh forwarded address reset the lockout"
    # the same headers in the other spellings proxies use
    for header in ("cf-connecting-ip", "x-real-ip", "forwarded"):
        r = _from("198.51.100.7").post("/api/auth/login", json={"username": "owner", "password": PW}, headers={header: "for=198.51.100.7" if header == "forwarded" else "198.51.100.7"})
        assert r.status_code == 429, header
    # a plain connection with no forwarding header keeps its own count, as before
    r = _from("192.0.2.9").post("/api/auth/login", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text


def test_a_named_proxy_still_tells_the_callers_apart(monkeypatch):
    """When the owner names the proxy, the address behind it is believed, and one caller's failures do not lock
    another out."""
    monkeypatch.setattr(auth.settings, "trusted_proxies", "10.0.0.0/8")
    for i in range(auth.LOCK_AFTER):
        r = _from("10.0.0.2").post("/api/auth/login", json={"username": f"nobody{i}", "password": "x" * 12}, headers={"x-forwarded-for": "203.0.113.1"})
        assert r.status_code == 401
    assert _from("10.0.0.2").post("/api/auth/login", json={"username": "owner", "password": PW}, headers={"x-forwarded-for": "203.0.113.1"}).status_code == 429
    assert _from("10.0.0.2").post("/api/auth/login", json={"username": "owner", "password": PW}, headers={"x-forwarded-for": "203.0.113.2"}).status_code == 200


def test_the_passkey_routes_share_the_sign_in_lockout(client: TestClient):
    """An address locked out of the password is not handed passkey challenges either (PLAN section 12: the second
    step and passkeys are under the sign-in lockout)."""
    for i in range(auth.LOCK_AFTER):
        assert client.post("/api/auth/login", json={"username": f"nobody{i}", "password": "x" * 12}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 429
    assert client.post("/api/auth/passkeys/login/options", json={}).status_code == 429
    assert client.post("/api/auth/passkeys/login", json={"key": "x", "credential": {"id": "x"}}).status_code == 429
    assert client.post("/api/auth/login/second", json={"ticket": "x", "code": "123456"}).status_code == 429
    assert client.post("/api/auth/join/check", json={"token": "x" * 43}).status_code == 429


def test_the_server_banner_is_off_where_the_app_is_started():
    """uvicorn adds `Server: uvicorn` to every response unless told not to; the app's own middleware cannot remove
    it, so the launch commands must. (The response headers themselves are checked against the published address.)"""
    cmd = re.search(r'^CMD \[(.*)\]', (ROOT / "docker" / "Dockerfile").read_text(), re.M)
    assert cmd and "--no-server-header" in cmd.group(1), "the image's uvicorn command must carry --no-server-header"
    assert "server_header=False" in (ROOT / "backend" / "scripts" / "dev_signin.py").read_text()
