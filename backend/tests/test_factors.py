"""M26: the second step at sign-in, and where passkeys can be made."""

import time

import pyotp
from fastapi.testclient import TestClient

from app import auth
from app.main import app

H = {"X-Requested-With": "FinancialAuthority"}
LOGIN = {"username": "owner", "password": "correct horse battery"}


def _code(secret: str, offset: int = 0) -> str:
    return pyotp.TOTP(secret).at(int(time.time()) + offset)


def test_the_second_step_turns_on_and_the_password_alone_no_longer_signs_in(owner: TestClient):
    started = owner.post("/api/auth/totp/start", headers=H).json()
    assert started["qr_svg"].startswith("<svg") and started["uri"].startswith("otpauth://totp/")
    secret = started["secret"]
    assert owner.post("/api/auth/totp/confirm", json={"code": "000000"}, headers=H).status_code == 400
    confirmed = owner.post("/api/auth/totp/confirm", json={"code": _code(secret)}, headers=H).json()
    codes = confirmed["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert owner.get("/api/auth/factors").json()["totp"] == {"enabled": True, "recovery_left": 10, "since": owner.get("/api/auth/factors").json()["totp"]["since"]}

    fresh = TestClient(app)
    first = fresh.post("/api/auth/login", json=LOGIN, headers=H).json()
    assert first["ok"] is False and first["second_step"] == "code" and auth.COOKIE not in fresh.cookies
    assert fresh.post("/api/auth/login/second", json={"ticket": first["ticket"], "code": "123456"}, headers=H).status_code == 401
    assert fresh.post("/api/auth/login/second", json={"ticket": "forged", "code": _code(secret)}, headers=H).status_code == 401
    # The current code may already have been used to confirm; the next one is always new.
    ok = fresh.post("/api/auth/login/second", json={"ticket": first["ticket"], "code": _code(secret, 30)}, headers=H)
    assert ok.status_code == 200 and fresh.get("/api/v1/snapshot").status_code == 200
    replay = TestClient(app)
    again = replay.post("/api/auth/login", json=LOGIN, headers=H).json()
    assert replay.post("/api/auth/login/second", json={"ticket": again["ticket"], "code": _code(secret, 30)}, headers=H).status_code == 401  # a code works once

    auth.reset_lockouts()
    rescue = TestClient(app)
    ticket = rescue.post("/api/auth/login", json=LOGIN, headers=H).json()["ticket"]
    assert rescue.post("/api/auth/login/second", json={"ticket": ticket, "code": codes[0]}, headers=H).status_code == 200
    auth.reset_lockouts()
    reuse = TestClient(app)
    ticket = reuse.post("/api/auth/login", json=LOGIN, headers=H).json()["ticket"]
    assert reuse.post("/api/auth/login/second", json={"ticket": ticket, "code": codes[0]}, headers=H).status_code == 401  # a recovery code works once
    assert owner.get("/api/auth/factors").json()["totp"]["recovery_left"] == 9

    assert owner.post("/api/auth/totp/disable", json={"code": "nope"}, headers=H).status_code == 400
    assert owner.post("/api/auth/totp/disable", json={"code": codes[1]}, headers=H).json() == {"ok": True}
    auth.reset_lockouts()
    assert TestClient(app).post("/api/auth/login", json=LOGIN, headers=H).json()["ok"] is True


def test_passkeys_are_offered_only_where_browsers_allow_them(owner: TestClient):
    here = owner.get("/api/auth/factors").json()
    assert here["passkeys_here"] is False and "https" in here["passkeys_note"]
    assert owner.post("/api/auth/passkeys/register/options", headers=H).status_code == 409
    local = TestClient(app, base_url="http://localhost")
    local.post("/api/auth/login", json=LOGIN, headers=H)
    made = local.post("/api/auth/passkeys/register/options", headers=H).json()
    assert made["options"]["rp"]["id"] == "localhost" and made["options"]["authenticatorSelection"]["userVerification"] == "required"
    assert local.post("/api/auth/passkeys/login/options", headers=H).status_code == 409  # none made yet
    assert local.post("/api/auth/passkeys/register", json={"key": "reg:forged", "credential": {}}, headers=H).status_code == 400
    assert TestClient(app).post("/api/auth/totp/start", headers=H).status_code == 401  # only the owner
