"""Security sweep 11, part two: the published path. The app is started as the tunnel would see it (a real uvicorn
process with FA_PUBLIC_URL=https://money.example.test and FA_TRUSTED_PROXIES naming the proxy), and a loopback
client plays the proxy with X-Forwarded-For and X-Forwarded-Proto: HSTS only on forwarded https, the OAuth issuer
and metadata at the public address, the consent redirect to a claude.ai-shaped https callback and the warning for
another host, the passkey rpId at the public host, the lockout and the feed limit counting the forwarded address
and a spoofed CF-Connecting-IP without the proxy named not resetting them, the MCP protected-resource metadata and
the Secure cookie. The Plaid webhook is signed with a key pair made here, its JWK served from a stub.

The process binds a loopback port; where the sandbox refuses that, the tests are skipped with the reason."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.api import plaid_webhook as webhook_api
from app.connectors import plaid_webhook as pw

PUBLIC = "https://money.example.test"
HOST = "money.example.test"
PW = "correct horse battery"
ROOT = Path(__file__).resolve().parents[1]


def _bindable() -> bool:
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.close()
        return True
    except PermissionError:
        return False


needs_loopback = pytest.mark.skipif(not _bindable(), reason="binding a loopback port is refused here (the sandbox); run outside it")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def published():
    """The app as a process behind a simulated proxy; one per module. Yields (base url, process)."""
    if not _bindable():
        pytest.skip("binding a loopback port is refused here (the sandbox); run outside it")
    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="fa-published-")
    env = {**os.environ, "FA_DATA_DIR": data_dir, "FA_PUBLIC_URL": PUBLIC, "FA_TRUSTED_PROXIES": "127.0.0.1", "FA_PASSWORD": PW, "FA_USERNAME": "owner",
           "FA_SESSION_SECRET": "published-secret", "FA_DATA_KEY": "published-data-key", "FA_JOBS": "false", "FA_DEMO": "false", "FA_INSTALL": "docker", "FA_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
                            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        raise RuntimeError(f"the app did not start: {out[-2000:]}")
    yield base
    proc.kill()
    proc.wait(timeout=10)
    import shutil

    shutil.rmtree(data_dir, ignore_errors=True)


def _carry_cookie(c: httpx.Client, response: httpx.Response) -> None:
    """httpx will not send a Secure cookie back over the loopback's plain http, so the cookie the app set is carried
    by hand, as the browser behind the https tunnel would."""
    raw = response.headers.get("set-cookie", "")
    assert raw.startswith("__Host-fa_session=" if c.headers.get("x-forwarded-proto") == "https" else "fa_session="), raw  # __Host- over https since round twelve
    c.headers["cookie"] = raw.split(";", 1)[0]


def sign_in(c: httpx.Client) -> None:
    r = c.post("/api/auth/login", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text
    _carry_cookie(c, r)
    c.headers["X-Requested-With"] = "FinancialAuthority"


def via_proxy(base: str, address: str = "203.0.113.10", proto: str = "https", **kw) -> httpx.Client:
    """A client whose requests arrive as the tunnel's would: forwarded for `address` over `proto`, at the public host."""
    return httpx.Client(base_url=base, headers={"x-forwarded-for": address, "x-forwarded-proto": proto, "host": HOST, "x-forwarded-host": HOST}, follow_redirects=False, **kw)


@needs_loopback
def test_hsts_only_on_forwarded_https_and_the_cookie_secure_only_then(published: str):
    plain = httpx.get(published + "/api/health")
    assert "strict-transport-security" not in plain.headers
    with via_proxy(published, proto="http") as c:
        assert "strict-transport-security" not in c.get("/api/health").headers
        r = c.post("/api/auth/login", json={"username": "owner", "password": PW})
        assert r.status_code == 200 and "fa_session=" in r.headers.get("set-cookie", "") and "secure" not in r.headers["set-cookie"].lower()
    with via_proxy(published) as c:
        r = c.get("/api/health")
        assert r.headers.get("strict-transport-security", "").startswith("max-age=")
        r = c.post("/api/auth/login", json={"username": "owner", "password": PW})
        cookie = r.headers["set-cookie"].lower()
        assert r.status_code == 200 and "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


@needs_loopback
def test_the_oauth_and_mcp_metadata_name_the_public_address(published: str):
    with via_proxy(published) as c:
        meta = c.get("/.well-known/oauth-authorization-server").json()
        assert meta["issuer"] == PUBLIC
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
            assert meta[key].startswith(PUBLIC + "/"), (key, meta[key])
        resource = c.get("/.well-known/oauth-protected-resource").json()
        assert resource["resource"] == PUBLIC and resource["authorization_servers"] == [PUBLIC]
        challenge = c.post("/mcp", headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert challenge.status_code == 401
        assert PUBLIC + "/.well-known/oauth-protected-resource" in challenge.headers.get("www-authenticate", "")
    # the same documents asked for from inside (no forwarding headers) still name the published address, never the loopback
    inside = httpx.get(published + "/.well-known/oauth-authorization-server").json()
    assert inside["issuer"] == PUBLIC


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _consent(c: httpx.Client, redirect: str, name: str) -> tuple[str, str, dict]:
    reg = c.post("/register", json={"client_name": name, "redirect_uris": [redirect], "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read"})
    assert reg.status_code == 201, reg.text
    verifier, challenge = _pkce()
    start = c.get("/authorize", params={"response_type": "code", "client_id": reg.json()["client_id"], "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1", "scope": "read"})
    assert start.status_code in (302, 303, 307), start.text
    location = start.headers["location"]
    assert location.startswith(PUBLIC + "/oauth/consent?req=") or location.startswith("/oauth/consent?req="), location
    req = parse_qs(urlparse(location).query)["req"][0]
    return req, verifier, reg.json()


@needs_loopback
def test_the_consent_sends_the_owner_back_to_claude_over_https_and_warns_for_anyone_else(published: str):
    with via_proxy(published) as c:
        req, verifier, info = _consent(c, "https://claude.ai/api/mcp/auth_callback", "Claude")
        signed = c.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": PW}, headers={"origin": PUBLIC, "sec-fetch-site": "same-origin"})
        assert signed.status_code == 200 and "Let Claude in?" in signed.text and "which is not Claude" not in signed.text
        assert "secure" in signed.headers.get("set-cookie", "").lower()
        _carry_cookie(c, signed)
        allowed = c.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": ["read"]}, headers={"origin": PUBLIC, "sec-fetch-site": "same-origin"})
        assert allowed.status_code == 302
        back = urlparse(allowed.headers["location"])
        assert back.scheme == "https" and back.netloc == "claude.ai" and back.path == "/api/mcp/auth_callback"
        query = parse_qs(back.query)
        assert query["state"] == ["s1"] and query["code"][0].startswith("fa_oc_")
        token = c.post("/token", data={"grant_type": "authorization_code", "code": query["code"][0], "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": "https://claude.ai/api/mcp/auth_callback"})
        assert token.status_code == 200, token.text
        # another host: the page says so in the warning box
        req2, _, _ = _consent(c, "https://relay.example.net/cb", "Claude")
        page = c.get("/oauth/consent", params={"req": req2})
        assert "relay.example.net" in page.text and "which is not Claude" in page.text
        # a form posted from another site is refused
        foreign = c.post("/oauth/consent", data={"req": req2, "decision": "allow", "scope": ["read"]}, headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"})
        assert "did not come from this app" in foreign.text


@needs_loopback
def test_passkeys_are_made_for_the_public_host(published: str):
    with via_proxy(published) as c:
        sign_in(c)
        r = c.post("/api/auth/passkeys/register/options", json={})
        assert r.status_code == 200, r.text
        options = r.json()["options"]
        assert options["rp"]["id"] == HOST
        login = c.post("/api/auth/passkeys/login/options", json={})
        assert login.status_code == 409 and "No passkey is set up for this address" in login.text  # none made yet: nothing to challenge with
    # reached at a name that is not the published one (a stray Host), passkeys are refused rather than made for it
    with httpx.Client(base_url=published, headers={"x-forwarded-for": "203.0.113.10", "x-forwarded-proto": "https", "host": "other.example.test"}) as c:
        sign_in(c)
        assert c.post("/api/auth/passkeys/register/options", json={}).status_code == 409


@needs_loopback
def test_the_lockout_counts_the_forwarded_address_behind_the_named_proxy(published: str):
    for i in range(auth.LOCK_AFTER):
        with via_proxy(published, address="198.51.100.1") as c:
            assert c.post("/api/auth/login", json={"username": f"nobody{i}", "password": "x" * 12}).status_code == 401
    with via_proxy(published, address="198.51.100.1") as c:
        assert c.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 429
        assert c.post("/api/auth/passkeys/login/options", json={}).status_code == 429
    with via_proxy(published, address="198.51.100.2") as c:
        assert c.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 200
    # a stranger's CF-Connecting-IP rides along with the proxy's X-Forwarded-For: the forwarded chain wins
    with httpx.Client(base_url=published, headers={"x-forwarded-for": "198.51.100.1", "cf-connecting-ip": "198.51.100.3", "x-forwarded-proto": "https", "host": HOST}) as c:
        assert c.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 429


@needs_loopback
def test_the_calendar_feed_limit_counts_the_forwarded_address(published: str):
    with via_proxy(published) as c:
        sign_in(c)
        made = c.post("/api/v1/calendar/feed", json={"amounts": False}).json()
        assert made["url"].startswith(PUBLIC + "/api/calendar/")
        path = "/" + made["url"].split("/", 3)[3]
    from app.api import exports as exports_api

    with via_proxy(published, address="198.51.100.21") as c:
        for _ in range(exports_api.FEED_LIMIT):
            assert c.get(path).status_code == 200
        assert c.get(path).status_code == 429
    with via_proxy(published, address="198.51.100.22") as c:
        assert c.get(path).status_code == 200, "one address's limit closed the feed for another"
    # a guesser behind the proxy is slowed by the same count, whatever token each try carries
    with via_proxy(published, address="198.51.100.23") as c:
        for i in range(exports_api.FEED_LIMIT):
            assert c.get(f"/api/calendar/{secrets.token_urlsafe(32)}.ics").status_code == 404
        assert c.get(f"/api/calendar/{secrets.token_urlsafe(32)}.ics").status_code == 429
        assert c.get(path).status_code == 429


def test_a_spoofed_cf_connecting_ip_without_the_proxy_named_does_not_reset_the_counts(client: TestClient, monkeypatch):
    """In-process, with no proxy named: a forwarding header counts as one address, so neither the lockout nor the
    webhook's rate table opens a fresh count per spoofed value."""
    monkeypatch.setattr(auth.settings, "trusted_proxies", "")
    webhook_api._hits.clear()
    for i in range(auth.LOCK_AFTER):
        assert client.post("/api/auth/login", json={"username": f"nobody{i}", "password": "x" * 12}, headers={"cf-connecting-ip": f"203.0.113.{i}"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PW}, headers={"cf-connecting-ip": "203.0.113.250"}).status_code == 429
    for i in range(webhook_api.LIMIT):
        assert client.post("/api/plaid/webhook", content=b"{}", headers={"cf-connecting-ip": f"203.0.113.{i % 250}"}).status_code == 401
    assert client.post("/api/plaid/webhook", content=b"{}", headers={"cf-connecting-ip": "203.0.113.251"}).status_code == 429
    assert len([k for k in webhook_api._hits if k == "forwarded"]) == 1


# ---- the Plaid webhook, signed ---------------------------------------------------------------------------------


class _JwkStub(HTTPServer):
    """Plaid's key endpoint, stubbed: answers one key by id."""

    def __init__(self, jwk: dict) -> None:
        super().__init__(("127.0.0.1", 0), _JwkHandler)
        self.jwk = jwk
        self.asked: list[str] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()


class _JwkHandler(BaseHTTPRequestHandler):
    server: _JwkStub

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0")) or 0) or b"{}")
        kid = body.get("key_id", "")
        self.server.asked.append(kid)
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"key": {**self.server.jwk, "kid": kid}} if kid == "k1" else {"error_code": "INVALID_FIELD"}).encode())

    def log_message(self, *_: object) -> None:
        pass


def _keypair():
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    jwk = {"kty": "EC", "crv": "P-256", "x": b64(nums.x.to_bytes(32, "big")), "y": b64(nums.y.to_bytes(32, "big")), "expired_at": None}
    return key, jwk


def _sign(key, body: bytes, iat: float, kid: str = "k1") -> str:  # noqa: ANN001
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    head = b64(json.dumps({"alg": "ES256", "kid": kid, "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iat": int(iat), "request_body_sha256": hashlib.sha256(body).hexdigest()}).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


@needs_loopback
def test_the_webhook_takes_a_signed_notice_once_and_refuses_the_rest(client: TestClient, monkeypatch):
    from sqlmodel import Session

    from app.db import engine
    from app.models import BankLink

    key, jwk = _keypair()
    stub = _JwkStub(jwk)
    try:
        pw._keys.clear()
        pw._unknown.clear()
        pw._last_sync.clear()
        if hasattr(pw, "_seen"):
            pw._seen.clear()
        webhook_api._hits.clear()

        def fetch_key(kid: str) -> dict:
            r = httpx.post(f"http://127.0.0.1:{stub.server_address[1]}/webhook_verification_key/get", json={"key_id": kid}, timeout=5.0)
            got = r.json()
            if "key" not in got:
                raise RuntimeError(got.get("error_code", "no key"))
            return got["key"]

        monkeypatch.setattr(webhook_api, "_fetch_key", fetch_key)
        started: list[int] = []
        monkeypatch.setattr(webhook_api, "_sync", lambda link_id: started.append(link_id))
        with Session(engine) as session:
            session.add(BankLink(provider="plaid", external_id="item-wire", institution="Example Bank", environment="sandbox", access_token_enc="", active=True))
            session.commit()
        body = json.dumps({"webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE", "item_id": "item-wire"}).encode()
        now = time.time()
        headers = {"Content-Type": "application/json", "x-forwarded-for": "34.0.0.1"}
        good = _sign(key, body, now)
        assert client.post("/api/plaid/webhook", content=body, headers={**headers, "Plaid-Verification": good}).status_code == 200
        assert started == [1] or len(started) == 1
        assert stub.asked == ["k1"]
        # replayed: the same token and body again
        assert client.post("/api/plaid/webhook", content=body, headers={**headers, "Plaid-Verification": good}).status_code == 401, "a replayed notice was accepted"
        # old
        assert client.post("/api/plaid/webhook", content=body, headers={**headers, "Plaid-Verification": _sign(key, body, now - 600)}).status_code == 401
        # wrong kid: the stub has no such key
        assert client.post("/api/plaid/webhook", content=body, headers={**headers, "Plaid-Verification": _sign(key, body, now, kid="k2")}).status_code == 401
        # wrong body: a token over one body sent with another
        other = body.replace(b"item-wire", b"item-other")
        assert client.post("/api/plaid/webhook", content=other, headers={**headers, "Plaid-Verification": _sign(key, body, now)}).status_code == 401
        # a fresh, correctly signed notice is still taken (the one-minute debounce then holds the sync)
        assert client.post("/api/plaid/webhook", content=body, headers={**headers, "Plaid-Verification": _sign(key, body, now + 1)}).status_code == 200
        assert len(started) == 1
        assert stub.asked.count("k1") == 1, "the key was fetched again for every notice"
    finally:
        stub.shutdown()
        stub.server_close()
