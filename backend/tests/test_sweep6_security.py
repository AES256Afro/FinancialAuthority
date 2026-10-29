"""Security sweep 6: double actions and races (an approval approved twice at once, a fact confirmed twice at once,
a join link redeemed twice at once, an authorization code exchanged twice), an erase that waits for the writer and
forgets the Teller certificate on disk, a push address that never carries the old token to a new server, and a
Plaid link that cannot be reached saying so in plain words."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, notify
from app.connectors import CONNECTORS
from app.db import engine
from app.main import app
from app.models import Approval, BankLink, Bill, ExtractedFact, MailAccount, OAuthClient, OAuthCode, Person, utcnow

H = {"X-Requested-With": "FinancialAuthority"}
GOOD = "plum river lantern seven"
OTHER = "maple orchard twelve stones"


def _owner_client() -> TestClient:
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).status_code == 200
    c.headers.update(H)
    return c


def _run_together(workers: list) -> list:
    """Run each callable in its own thread, released together; return what each returned or raised."""
    barrier = threading.Barrier(len(workers))
    out: list = [None] * len(workers)

    def go(i: int, fn) -> None:  # noqa: ANN001
        barrier.wait()
        try:
            out[i] = fn()
        except Exception as error:  # noqa: BLE001 - the test reads it
            out[i] = error

    threads = [threading.Thread(target=go, args=(i, fn)) for i, fn in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return out


def test_an_approval_approved_twice_at_once_is_sent_once(owner: TestClient, monkeypatch):
    """Two requests that both read the approval as pending and both approve it must not both send: the second finds
    it decided."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: (time.sleep(0.05), sent.append(kw["to"]))[0] or "<id@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test", smtp_port=465))
        session.commit()
    queued = owner.post("/api/v1/approvals", json={"to": "billing@example-power.test", "subject": "Move my date", "body": "Please move it."}).json()
    first, second = Session(engine), Session(engine)
    rows = [first.get(Approval, queued["id"]), second.get(Approval, queued["id"])]
    assert all(r.status == "pending" for r in rows)  # both requests saw it pending before either decided
    results = _run_together([lambda: approvals.approve(first, rows[0], decided_by="owner"), lambda: approvals.approve(second, rows[1], decided_by="owner")])
    first.close(), second.close()
    assert sent == ["billing@example-power.test"], sent
    assert sum(isinstance(r, ValueError) for r in results) == 1 and sum(isinstance(r, Approval) for r in results) == 1
    with Session(engine) as session:
        assert session.get(Approval, queued["id"]).status == "sent"


def test_a_fact_confirmed_twice_at_once_makes_one_bill(owner: TestClient, monkeypatch):
    """apply_fact is slowed so both requests have read the fact as pending before either writes: without the lock
    the race is real but the window is a millisecond, and the test would pass with the fix reverted most runs."""
    from app.api import mail as mail_api

    real = mail_api.apply_fact
    monkeypatch.setattr(mail_api, "apply_fact", lambda *a, **k: (time.sleep(0.15), real(*a, **k))[1])
    with Session(engine) as session:
        fact = ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Water", "amount": 41.0, "due_date": "2026-10-28"}), status="pending")
        session.add(fact)
        session.commit()
        fact_id = fact.id
    other = _owner_client()
    results = _run_together([lambda: owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}), lambda: other.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True})])
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.name == "Example Water")).all()) == 1
        assert session.get(ExtractedFact, fact_id).status == "applied"


def test_a_join_link_redeemed_twice_at_once_sets_one_password(owner: TestClient):
    made = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "shared"}).json()
    token = made["invite"]["link"].split("#", 1)[1]
    a, b = TestClient(app), TestClient(app)
    a.headers.update(H), b.headers.update(H)
    results = _run_together([lambda: a.post("/api/auth/join", json={"token": token, "password": GOOD}), lambda: b.post("/api/auth/join", json={"token": token, "password": OTHER})])
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 400], [r.text for r in results]
    winner = GOOD if results[0].status_code == 200 else OTHER
    loser = OTHER if winner == GOOD else GOOD
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"username": "sam", "password": winner}).status_code == 200
    assert fresh.post("/api/auth/login", json={"username": "sam", "password": loser}).status_code == 401
    with Session(engine) as session:
        assert session.exec(select(Person).where(Person.username == "sam")).one().invite_hash == ""


def test_an_authorization_code_is_exchanged_once(client: TestClient):
    """The SDK checks a code is unused before the exchange; two exchanges in the same instant both pass that check,
    so the exchange itself claims the code and the second one is refused."""
    from mcp.server.auth.provider import AuthorizationCode, TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from app import oauth

    with Session(engine) as session:
        session.add(OAuthClient(client_id="app-1", name="An app", redirect_uris='["http://localhost:9999/callback"]'))
        session.add(OAuthCode(code_hash=oauth._hash("fa_oc_once"), client_id="app-1", redirect_uri="http://localhost:9999/callback", code_challenge="x", scopes="read", expires_at=utcnow() + timedelta(minutes=5)))
        session.commit()
    info = OAuthClientInformationFull(client_id="app-1", redirect_uris=[AnyUrl("http://localhost:9999/callback")])
    code = AuthorizationCode(code="fa_oc_once", scopes=["read"], expires_at=time.time() + 300, client_id="app-1", code_challenge="x", redirect_uri=AnyUrl("http://localhost:9999/callback"), redirect_uri_provided_explicitly=True)

    async def twice():
        first = await oauth.provider.exchange_authorization_code(info, code)
        try:
            await oauth.provider.exchange_authorization_code(info, code)
        except TokenError as error:
            return first, error
        return first, None

    tokens, refused = asyncio.run(twice())
    assert tokens.access_token.startswith("fa_oa_") and refused is not None and refused.error == "invalid_grant"
    with Session(engine) as session:
        from app.models import OAuthToken

        assert len(session.exec(select(OAuthToken).where(OAuthToken.client_id == "app-1")).all()) == 2  # one access, one refresh: never a second pair


def test_erase_waits_for_the_writer_and_forgets_the_teller_certificate(owner: TestClient):
    from pathlib import Path

    from app.connectors import teller
    from app.jobs.writing import one_writer

    cert, key = teller.cert_files("-----BEGIN CERTIFICATE-----\nexample\n-----END CERTIFICATE-----", "-----BEGIN PRIVATE KEY-----\nexample\n-----END PRIVATE KEY-----")
    assert Path(cert).exists() and Path(key).exists()
    release = threading.Event()
    held = threading.Event()

    def hold() -> None:
        with one_writer():  # a job mid-write
            held.set()
            release.wait(timeout=20)

    threading.Thread(target=hold).start()
    held.wait(timeout=5)
    done: list = []
    runner = threading.Thread(target=lambda: done.append(owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": "correct horse battery"})))
    runner.start()
    runner.join(timeout=1.0)
    assert done == [], "the erase ran beside a writer"
    release.set()
    runner.join(timeout=30)
    assert done and done[0].status_code == 200, done and done[0].text
    assert not Path(cert).exists() and not Path(key).exists() and not Path(cert).parent.exists()


def test_a_new_push_address_never_carries_the_old_token(owner: TestClient):
    """The push token is sent as a bearer to the address on file. Moving the address to another server needs the
    token typed again, so a person with the Everything role cannot read the owner's token off their own server."""
    assert owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "fa", "token": "tk_secret_one"}).json()["token_set"] is True
    moved = owner.put("/api/v1/push", json={"url": "https://elsewhere.example.test"}).json()
    assert moved["url"] == "https://elsewhere.example.test" and moved["token_set"] is False
    assert owner.put("/api/v1/push", json={"url": "https://ntfy.example.test", "token": "tk_secret_two"}).json()["token_set"] is True
    assert owner.put("/api/v1/push", json={"topic": "other"}).json()["token_set"] is True  # the same server: the token stays
    assert owner.put("/api/v1/push", json={"url": "https://ntfy.example.test/"}).json()["token_set"] is True  # a trailing slash is the same server
    assert owner.put("/api/v1/push", json={"kind": "gotify", "url": "https://gotify.example.test"}).json()["token_set"] is False


def test_a_plaid_link_that_cannot_be_reached_says_so_in_plain_words(owner: TestClient):
    from tests.test_banks import FakePlaid

    fake = FakePlaid()
    down = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if down["on"] and request.url.path in ("/accounts/get", "/item/get", "/transactions/sync"):
            raise httpx.ConnectError("[Errno 111] Connection refused")
        return fake.handler(request)

    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        link = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()["link"]
        down["on"] = True
        result = owner.post(f"/api/v1/banks/{link['id']}/sync").json()
        assert result["ok"] is False
        with Session(engine) as session:
            row = session.get(BankLink, link["id"])
            assert row.last_error and "ConnectError" not in row.last_error and "Errno" not in row.last_error
            assert row.last_error.startswith("Plaid could not be reached") and row.needs_login is False
        assert "Plaid could not be reached" in result["summary"]
        down["on"] = False
        assert owner.post(f"/api/v1/banks/{link['id']}/sync").json()["ok"] is True
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_the_buffer_setting_takes_only_a_finite_amount(owner: TestClient):
    """The JSON parser accepts NaN and 1e400; stored, they would poison every figure the engine works out."""
    for raw in ('{"value": NaN}', '{"value": 1e400}', '{"value": -5}', '{"value": true}', '{"value": "200"}'):
        r = owner.put("/api/v1/settings/buffer", content=raw, headers={"Content-Type": "application/json"})
        assert r.status_code == 400, (raw, r.status_code, r.text)
    assert owner.put("/api/v1/settings/buffer", json={"value": 250}).json()["buffer"] == 250
    assert owner.get("/api/v1/settings").json()["buffer"] == 250
