"""The hardening sweep, as tests: headers on every surface, a body ceiling, malformed uploads refused without a
crash, the session cookie's flags, no secret in the logs after the jobs run, and the calendar's edge days."""

import logging
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crypto
from app.db import engine
from app.engine.cadence import next_paydays, payday_for, payday_position, semimonthly_anchors
from app.engine.household import seed
from app.engine.projection import next_due
from app.models import MailAccount
from app.security import APP_CSP
from app.store import set_setting


def test_every_surface_carries_its_headers(owner: TestClient, client: TestClient):
    api = owner.get("/api/v1/snapshot")
    for h in ("x-content-type-options", "referrer-policy", "x-frame-options", "permissions-policy"):
        assert h in api.headers, h
    assert "content-security-policy" not in api.headers  # JSON needs none; the pages carry it
    health = TestClient(client.app).get("/api/health")
    assert health.headers["x-frame-options"] == "DENY" and "strict-transport-security" not in health.headers
    https = TestClient(client.app).get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age" in https.headers["strict-transport-security"]
    consent = TestClient(client.app).get("/oauth/consent?req=nope")
    csp = consent.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "form-action 'self' https:" in csp
    assert "script-src 'self' https://cdn.plaid.com" in APP_CSP and "'unsafe-eval'" not in APP_CSP and "object-src 'none'" in APP_CSP


def test_a_body_over_the_ceiling_is_refused_before_it_is_read(owner: TestClient):
    r = owner.post("/api/v1/imports/file", headers={"Content-Length": str(70 * 1024 * 1024), "Content-Type": "application/octet-stream"}, content=b"x")
    assert r.status_code == 413


def test_malformed_uploads_are_refused_not_crashed(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    junk = [b"", b"%PDF-1.4\n%garbage\x00\x01\x02 not a pdf", b"\xff\xfe\x00\x00" * 200, b"date,amount\n2026-13-45,abc\n,,,\n", b"<html><script>alert(1)</script></html>"]
    for blob in junk:
        for path, field in (("/api/v1/imports/paystub", "file"), ("/api/v1/borrowing/statement", "file"), ("/api/v1/imports/file", "file"), ("/api/v1/imports/rocketmoney", "file"), ("/api/v1/imports/preview", "file")):
            r = owner.post(path, files={field: ("x.pdf" if blob.startswith(b"%PDF") else "x.csv", blob, "application/octet-stream")})
            assert r.status_code < 500, (path, blob[:20], r.status_code, r.text[:200])


def test_the_session_cookie_is_httponly_and_secure_over_https(client: TestClient):
    plain = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    cookie = plain.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    secure = TestClient(client.app).post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in secure.headers["set-cookie"].lower()


SECRETS = ["correct horse battery", "test-session-secret", "test-data-key", "mailbox-pass-7731", "plaid-secret-9a8b"]


def test_no_secret_reaches_the_logs_when_the_jobs_run(owner: TestClient, caplog):
    from app.jobs.scheduler import jobs

    with Session(engine) as session:
        seed(session)
        session.add(MailAccount(label="Proton", kind="proton", host="bridge.invalid", port=143, username="owner@example.test", password_enc=crypto.encrypt("mailbox-pass-7731"), use_ssl=False, starttls=True, folders="INBOX"))
        set_setting(session, "banks.secrets", {"plaid": {"client_id": "cid", "secret": crypto.encrypt("plaid-secret-9a8b"), "environment": "sandbox"}})
        session.commit()
    caplog.set_level(logging.DEBUG)
    for name in ("mail_sync", "self_check", "detect_recurring", "match_payments", "balance_history", "import_watch"):
        try:
            jobs.run(name)
        except Exception:  # noqa: BLE001 - a job may fail here; what matters is what it wrote
            pass
    owner.post("/api/v1/mail/accounts/test", json={"kind": "proton", "host": "bridge.invalid", "username": "owner@example.test", "password": "mailbox-pass-7731"})
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    for secret in SECRETS:
        assert secret not in text, secret
    runs = owner.get("/api/v1/jobs").json()
    assert all(s not in str(runs) for s in SECRETS)


def test_the_calendar_edges():
    # the 31st in a short month is the last day it has; a year turns over cleanly
    assert next_due(31, date(2027, 2, 2)) == date(2027, 2, 28)
    assert next_due(31, date(2028, 2, 2)) == date(2028, 2, 29)
    assert next_due(30, date(2026, 12, 31)) == date(2027, 1, 30)
    assert next_due(1, date(2026, 12, 31)) == date(2027, 1, 1)
    # a semi-monthly payday on a weekend moves to the Friday before; the end-of-month check too
    mid, end = semimonthly_anchors(2026, 11)
    assert mid == date(2026, 11, 13) and end == date(2026, 11, 30)
    mid, end = semimonthly_anchors(2027, 1)
    assert mid == date(2027, 1, 15) and end == date(2027, 1, 29)
    seq = next_paydays(date(2026, 12, 15), "semimonthly", 4)
    assert seq == [date(2026, 12, 15), date(2026, 12, 31), date(2027, 1, 15), date(2027, 1, 29)]
    assert payday_position(date(2026, 11, 13)) == "mid" and payday_position(date(2027, 1, 29)) == "end"
    # rent due on the 1st is paid from the end-of-month check before it, across the year
    assert payday_for(date(2027, 1, 1), "end") == date(2026, 12, 31)
    # daylight saving changes no date: paydays step by days, not hours
    assert next_paydays(date(2026, 10, 30), "biweekly", 3) == [date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27)]
