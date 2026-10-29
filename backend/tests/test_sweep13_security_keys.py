"""Security sweep 13, part four: the two keys in the environment. There is no rotation path for FA_DATA_KEY yet
(M8.5); what there must be is a plain answer when it changes, since every `*_enc` column, the owner's second step
and every document file are sealed under it. FA_SESSION_SECRET may change at any time: sessions end, and nothing
the owner needs to get back in may hang on it."""

from __future__ import annotations

import inspect
import time

import pyotp
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crypto, factors, main
from app.config import settings
from app.db import engine
from app.store import get_setting

H = {"X-Requested-With": "FinancialAuthority"}
LOGIN = {"username": "owner", "password": "correct horse battery"}


def test_a_changed_data_key_is_named_at_startup_not_discovered_feature_by_feature(owner: TestClient, monkeypatch):
    """The first start seals a canary; every later start opens it. A key that does not open it stops the start
    with a sentence that says what to do, before anything is sealed under the wrong key."""
    assert crypto.data_key_problem() is None, "the first start writes the canary"
    with Session(engine) as session:
        canary = get_setting(session, "crypto.canary")
    assert crypto.is_encrypted(canary)
    owner.put("/api/v1/models/secrets", json={"name": "anthropic", "value": "sk-ant-sealed-under-the-first-key"})
    monkeypatch.setattr(settings, "data_key", "a-different-key-after-a-reinstall")
    problem = crypto.data_key_problem()
    assert problem and "FA_DATA_KEY" in problem and "sealed" in problem
    # the same canary opens again with the right key
    monkeypatch.setattr(settings, "data_key", "test-data-key")
    assert crypto.data_key_problem() is None
    # an app with no data key at all is a developer's machine: it is told, not stopped
    monkeypatch.setattr(settings, "data_key", "")
    assert crypto.data_key_problem() is None
    source = inspect.getsource(main.lifespan)
    assert "data_key_problem" in source and "raise RuntimeError" in source


def test_the_canary_survives_an_erase(owner: TestClient):
    from app.api.privacy import KEEP_SETTINGS

    assert crypto.data_key_problem() is None
    assert "crypto.canary" in KEEP_SETTINGS
    done = owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": LOGIN["password"]})
    assert done.status_code == 200, done.text
    with Session(engine) as session:
        assert crypto.is_encrypted(get_setting(session, "crypto.canary"))


def test_a_changed_session_secret_ends_every_session_and_keeps_the_recovery_codes(owner: TestClient, monkeypatch):
    started = owner.post("/api/auth/totp/start", headers=H).json()
    codes = owner.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(started["secret"]).now()}, headers=H).json()["recovery_codes"]
    monkeypatch.setattr(settings, "session_secret", "rotated-after-a-leak")
    assert owner.get("/api/v1/snapshot").status_code == 401, "the old cookie is nothing now"
    fresh = TestClient(main.app)
    first = fresh.post("/api/auth/login", json=LOGIN, headers=H).json()
    assert first["ok"] is False and first["second_step"] == "code"
    # the authenticator still works, sealed under the data key
    assert fresh.post("/api/auth/login/second", json={"ticket": first["ticket"], "code": pyotp.TOTP(started["secret"]).at(int(time.time()) + 30)}, headers=H).status_code == 200
    # and so does a recovery code: the phone that is lost is the reason they exist
    rescue = TestClient(main.app)
    ticket = rescue.post("/api/auth/login", json=LOGIN, headers=H).json()["ticket"]
    assert rescue.post("/api/auth/login/second", json={"ticket": ticket, "code": codes[0]}, headers=H).status_code == 200
    assert rescue.get("/api/auth/factors").json()["totp"]["recovery_left"] == 9
    with Session(engine) as session:
        stored = get_setting(session, "auth.totp")["recovery"]
    assert all(h.startswith("scrypt$") for h in stored), "each code is its own salted, slow hash"


def test_recovery_codes_kept_the_old_way_still_sign_in(owner: TestClient):
    """Codes hashed before this change (a keyed digest) keep working while the secret is what it was."""
    started = owner.post("/api/auth/totp/start", headers=H).json()
    owner.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(started["secret"]).now()}, headers=H)
    with Session(engine) as session:
        state = factors._state(session, None)
        factors._save_state(session, None, {**state, "recovery": [factors._legacy_hash("abcd-ef01-2345")]})
    rescue = TestClient(main.app)
    ticket = rescue.post("/api/auth/login", json=LOGIN, headers=H).json()["ticket"]
    assert rescue.post("/api/auth/login/second", json={"ticket": ticket, "code": " ABCD-EF01-2345 "}, headers=H).status_code == 200
    assert rescue.get("/api/auth/factors").json()["totp"]["recovery_left"] == 0
