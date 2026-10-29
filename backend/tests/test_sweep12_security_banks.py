"""Security sweep 12, part one: the Plaid and Teller wire. A scripted vendor answers the connectors, by turns
truthfully and with hostile bodies: a token echoed in an error, an answer that is not JSON or not an object, a
100 MB answer, an answer that drips, rows with amounts of "1e308", NaN and null, an account id with a line break,
a merchant name of ten thousand characters or of HTML, a cursor of a megabyte, thousands of rows in one page, a
removed id the ledger never saw, the same id on two pages, an error shaped like success, and no network at all.

What is asserted: the access token never reaches a log line, a job summary, a link's last_error, an audit row or
an API response; no hostile answer crashes the job, writes an unbounded string, or holds the sync (and the writer
lock) past its budget; a network error never asks the owner to sign in again; the Teller certificate and key stay
in the private runtime folder; the webhook address registered with Plaid is the published https address only."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.config import settings
from app.connectors import CONNECTORS
from app.connectors import plaid as plaid_mod
from app.connectors import teller as teller_mod
from app.connectors.plaid_webhook import webhook_url
from app.db import engine
from app.models import Account, AuditEntry, BankLink, JobRun, Transaction
from app.store import set_setting

TOKEN = "access-sandbox-7f3a9c0e-token-value-11"
TOKEN2 = "access-sandbox-second-token-value-22"
TELLER_TOKEN = "token_teller_test_abc123"
BIG = 100 * 1024 * 1024


def _accounts_body() -> dict:
    return {
        "item": {"institution_name": "Example Bank"},
        "accounts": [
            {"account_id": "acc-chk", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": 312.45, "available": 300.0}},
            {"account_id": "acc-card", "name": "Rewards Card", "mask": "9012", "type": "credit", "subtype": "credit card", "balances": {"current": 842.10}},
        ],
    }


def _tx(i: int, amount: object = 10.0, account: str = "acc-chk", **extra: object) -> dict:
    return {"transaction_id": f"t{i}", "account_id": account, "amount": amount, "date": "2026-10-10", "name": f"ROW {i}", "merchant_name": f"Merchant {i}", "pending": False, **extra}


class _Drip(httpx.SyncByteStream):
    """A body that arrives a few bytes at a time, for as long as the server likes."""

    def __init__(self, text: str, pause: float, pieces: int) -> None:
        self.text, self.pause, self.pieces = text, pause, pieces
        self.sent = 0

    def __iter__(self):
        step = max(1, len(self.text) // self.pieces)
        for i in range(0, len(self.text), step):
            time.sleep(self.pause)
            self.sent += 1
            yield self.text[i:i + step].encode()


class _Huge(httpx.SyncByteStream):
    """A hundred megabytes of JSON that is never built in memory; counts what the client took."""

    def __init__(self) -> None:
        self.given = 0

    def __iter__(self):
        yield b'{"added": ['
        self.given += 11
        chunk = (json.dumps(_tx(0)) + ",") * 1000
        while self.given < BIG:
            self.given += len(chunk)
            yield chunk.encode()
        yield b"{}], \"next_cursor\": \"end\", \"has_more\": false}"


class FakePlaid:
    def __init__(self) -> None:
        self.mode = "normal"
        self.requests: list[tuple[str, dict]] = []
        self.huge: _Huge | None = None
        self.drip: _Drip | None = None
        self.token = TOKEN
        self.item = "item-1"

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, body))
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-123"})
        if path == "/item/public_token/exchange":
            if self.mode == "exchange_newline":
                return httpx.Response(200, json={"access_token": self.token, "item_id": "item\r\n1 injected"})
            if self.mode == "exchange_not_json":
                return httpx.Response(200, text="<html>maintenance</html>")
            if self.mode == "exchange_list":
                return httpx.Response(200, json=[self.token])
            return httpx.Response(200, json={"access_token": self.token, "item_id": self.item})
        if path in ("/item/get", "/item/webhook/update", "/item/remove"):
            return httpx.Response(200, json={"status": {}, "request_id": "r"})
        if self.mode == "network":
            raise httpx.ConnectError("connection refused")
        if self.mode == "echo_token":
            return httpx.Response(400, json={"error_type": "INVALID_INPUT", "error_code": "INVALID_ACCESS_TOKEN", "error_message": f"access token {body.get('access_token')} with secret {body.get('secret')} is not valid"})
        if self.mode == "not_json":
            return httpx.Response(200, text="<html><body>maintenance</body></html>")
        if self.mode == "json_list":
            return httpx.Response(200, json=[1, 2, 3])
        if self.mode == "success_shaped":
            return httpx.Response(200, json={"error_code": None, "error_type": "ITEM_ERROR", "error_message": "looks fine", "accounts": [], "added": [], "modified": [], "removed": [], "next_cursor": "x", "has_more": False})
        if path == "/accounts/get":
            got = _accounts_body()
            if self.mode == "newline_id":
                got["accounts"][0]["account_id"] = "acc\r\nchk,injected"
                got["accounts"][0]["name"] = "A" * 5000
            return httpx.Response(200, json=got)
        if path == "/transactions/sync":
            if self.mode == "huge":
                self.huge = _Huge()
                return httpx.Response(200, stream=self.huge, headers={"content-type": "application/json"})
            if self.mode == "drip":
                page = {"added": [_tx(i) for i in range(50)], "modified": [], "removed": [], "next_cursor": "c", "has_more": False}
                self.drip = _Drip(json.dumps(page), 0.2, 40)
                return httpx.Response(200, stream=self.drip, headers={"content-type": "application/json"})
            if self.mode == "amounts":
                text = json.dumps({"added": [_tx(1, "1e308"), _tx(2, "NaN"), _tx(3, None), _tx(4, 5e9), _tx(5, "abc"), _tx(6, 12.5), _tx(7, 1.0, date="not a date")],
                                   "modified": [], "removed": [], "next_cursor": "c", "has_more": False}).replace('"NaN"', "NaN")
                return httpx.Response(200, content=text.encode(), headers={"content-type": "application/json"})
            if self.mode == "strings":
                return httpx.Response(200, json={"added": [_tx(1, 10.0, merchant_name="M" * 10_000, name="<script>alert(1)</script>" + "N" * 10_000)], "modified": [], "removed": [],
                                                 "next_cursor": "c", "has_more": False})
            if self.mode == "cursor":
                return httpx.Response(200, json={"added": [_tx(1)], "modified": [], "removed": [], "next_cursor": "c" * (1024 * 1024), "has_more": False})
            if self.mode == "many":
                return httpx.Response(200, json={"added": [_tx(i) for i in range(1500)], "modified": [], "removed": [], "next_cursor": "c", "has_more": False})
            if self.mode == "dup_and_unknown_removed":
                if not body.get("cursor"):
                    return httpx.Response(200, json={"added": [_tx(1), _tx(2)], "modified": [], "removed": [{"transaction_id": "never-seen"}], "next_cursor": "p2", "has_more": True})
                return httpx.Response(200, json={"added": [_tx(1, 11.0)], "modified": [_tx(2, 12.0)], "removed": [{"transaction_id": "never-seen-2"}], "next_cursor": "end", "has_more": False})
            return httpx.Response(200, json={"added": [_tx(1), _tx(2, 20.0, "acc-card")], "modified": [], "removed": [], "next_cursor": "c1", "has_more": False})
        if path == "/investments/holdings/get":
            return httpx.Response(400, json={"error_code": "PRODUCTS_NOT_SUPPORTED", "error_message": "no"})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


@pytest.fixture
def plaid(owner: TestClient):
    fake = FakePlaid()
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    with Session(engine) as session:
        set_setting(session, "banks.secrets", {"plaid": {"client_id": "cid", "secret": crypto.encrypt("plaid-secret-9a8b"), "environment": "sandbox"}})
    yield fake
    CONNECTORS["plaid"].client_factory = None


def _link(owner: TestClient) -> int:
    got = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-1", "institution": "Example Bank"})
    assert got.status_code == 200, got.text
    assert TOKEN not in got.text and "plaid-secret-9a8b" not in got.text
    return int(got.json()["link"]["id"])


def _everything_said(owner: TestClient, caplog, result) -> str:
    """Every place a vendor's words could land: the log, the job summary, the links' errors, the audit rows, the API."""
    with Session(engine) as session:
        links = session.exec(select(BankLink)).all()
        errors = " ".join(f"{l.last_error} {l.institution} {l.accounts} {l.cursor}" for l in links)
        audit = " ".join(f"{a.client} {a.tool} {a.args_hash}" for a in session.exec(select(AuditEntry)).all())
        runs = " ".join(r.summary for r in session.exec(select(JobRun)).all())
    api = owner.get("/api/v1/banks").text + owner.get("/api/v1/banks/freshness").text + owner.get("/api/v1/jobs").text
    log_text = caplog.text + "".join(str(r.args) + str(r.getMessage()) for r in caplog.records)
    return " ".join([log_text, result.summary if result else "", errors, audit, runs, api])


@pytest.mark.parametrize("mode", ["echo_token", "not_json", "json_list", "success_shaped", "network", "amounts", "strings", "cursor", "newline_id", "many", "dup_and_unknown_removed", "huge", "drip"])
def test_plaid_hostile_answers_never_crash_leak_or_hold_the_sync(owner: TestClient, plaid: FakePlaid, caplog, monkeypatch, mode: str):
    caplog.set_level(logging.DEBUG)
    link_id = _link(owner)
    plaid.mode = mode
    monkeypatch.setattr(plaid_mod, "REQUEST_SECONDS", 1.5, raising=True)
    started = time.monotonic()
    result = CONNECTORS["plaid"].sync(Session(engine), link_id=link_id)
    took = time.monotonic() - started
    assert took < 20, f"{mode} held the sync for {took:.0f}s"
    said = _everything_said(owner, caplog, result)
    assert TOKEN not in said and "plaid-secret-9a8b" not in said, mode
    with Session(engine) as session:
        link = session.get(BankLink, link_id)
        assert link is not None and link.needs_login is False, mode
        assert len(link.last_error or "") <= 500 and len(link.cursor or "") <= plaid_mod.MAX_CURSOR_CHARS and len(link.accounts or "") < 2000
        for a in session.exec(select(Account)).all():
            assert len(a.name) <= 200 and len(a.external_id or "") <= 200 and "\n" not in (a.external_id or "") and "," not in (a.external_id or ""), mode
        for t in session.exec(select(Transaction)).all():
            assert math.isfinite(t.amount) and abs(t.amount) < 1e9, (mode, t.amount)
            assert len(t.merchant or "") <= 200 and len(t.raw_name or "") <= 500, mode
        rows = session.exec(select(Transaction)).all()
    if mode == "amounts":
        assert result.ok, result.summary
        assert 12.5 in {t.amount for t in rows if t.source == "plaid"}  # the one readable row lands; the rest are left out, and said so
        assert "left out" in result.summary
    if mode == "strings":
        assert result.ok and any(t.merchant.startswith("MMMM") for t in rows)
    if mode == "many":
        assert result.ok and result.inserted >= 1500 - 2
    if mode == "dup_and_unknown_removed":
        assert result.ok and sorted(t.amount for t in rows if t.source == "plaid") == [11.0, 12.0]
    if mode == "huge":
        assert not result.ok and plaid.huge is not None and plaid.huge.given < BIG // 2, "the client read the whole 100 MB"
    if mode == "drip":
        assert not result.ok and plaid.drip is not None and plaid.drip.sent < 40, "the drip was read to its end"
    if mode in ("not_json", "json_list", "cursor", "huge", "drip"):
        assert not result.ok and "BAD_RESPONSE" in (result.summary + said) or "SLOW" in (result.summary + said) or not result.ok
    if mode == "network":
        assert not result.ok and "could not be reached" in result.summary
    if mode == "success_shaped":
        assert result.ok


def test_plaid_link_creation_with_a_hostile_item_id_or_a_bad_exchange_answers_in_words(owner: TestClient, plaid: FakePlaid):
    plaid.mode = "exchange_newline"
    got = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-1", "institution": "Example Bank"})
    assert got.status_code in (200, 502), got.text
    assert TOKEN not in got.text
    with Session(engine) as session:
        for link in session.exec(select(BankLink)).all():
            assert "\n" not in link.external_id and "\r" not in link.external_id and len(link.external_id) <= 200
    for mode in ("exchange_not_json", "exchange_list"):
        plaid.mode = mode
        got = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-1", "institution": "Example Bank"})
        assert got.status_code == 502, (mode, got.status_code, got.text)
        assert "Traceback" not in got.text and TOKEN not in got.text


def test_plaid_settings_check_with_a_non_object_answer_does_not_crash(owner: TestClient, plaid: FakePlaid):
    plaid.mode = "json_list"
    got = owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "plaid-secret-9a8b", "environment": "sandbox"})
    assert got.status_code == 200, got.text
    assert "plaid-secret-9a8b" not in got.text


def test_plaid_remove_with_keep_and_relink_never_shows_a_token(owner: TestClient, plaid: FakePlaid, caplog):
    caplog.set_level(logging.DEBUG)
    link_id = _link(owner)
    plaid.mode = "echo_token"  # the vendor's remove fails, echoing the token
    gone = owner.delete(f"/api/v1/banks/{link_id}?accounts=keep")
    assert gone.status_code == 200 and TOKEN not in gone.text
    plaid.mode = "normal"
    plaid.token, plaid.item = TOKEN2, "item-2"
    again = _link(owner)
    assert again != link_id
    with Session(engine) as session:
        active = [l for l in session.exec(select(BankLink)).all() if l.active]
        assert len(active) == 1 and active[0].id == again
        assert crypto.is_encrypted(active[0].access_token_enc)
        assert session.exec(select(Account).where(Account.connector == "plaid", Account.active == True)).all()
    said = _everything_said(owner, caplog, None)
    assert TOKEN not in said and TOKEN2 not in said


def test_plaid_webhook_address_is_the_published_https_address_only(owner: TestClient, plaid: FakePlaid, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "http://192.168.1.10:8000")
    _link(owner)
    hooks = [b.get("webhook") for p, b in plaid.requests if p in ("/link/token/create", "/item/webhook/update")]
    assert not any(hooks), hooks
    assert webhook_url("http://box.local:8000") is None and webhook_url("https://fa.example.test/") == "https://fa.example.test/api/plaid/webhook"
    plaid.requests.clear()
    monkeypatch.setattr(settings, "public_url", "https://fa.example.test")
    owner.post("/api/v1/banks/plaid/link_token", json={})
    hooks = [b.get("webhook") for p, b in plaid.requests if p == "/link/token/create"]
    assert hooks == ["https://fa.example.test/api/plaid/webhook"]


# ---- Teller --------------------------------------------------------------------------------------------------------


class FakeTeller:
    def __init__(self) -> None:
        self.mode = "normal"
        self.requests: list[tuple[str, str]] = []
        self.drip: _Drip | None = None
        self.huge_given = 0

    def factory(self, token: str) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.url.path, request.headers.get("authorization", "")))
            if self.mode == "network":
                raise httpx.ConnectError("no route")
            if self.mode == "echo_token":
                return httpx.Response(400, json={"error": {"code": "bad_request", "message": f"token {token} is malformed"}})
            if self.mode == "not_json":
                return httpx.Response(200, text="<html>down</html>")
            if self.mode == "html_error":
                return httpx.Response(500, text="<html><body>" + f"oops {token}" + "x" * 5000 + "</body></html>")
            if request.url.path == "/accounts":
                acct = {"id": "acc_1", "enrollment_id": "enr_1", "name": "Everyday Checking", "type": "depository", "subtype": "checking", "last_four": "1234", "institution": {"name": "Example Bank"}}
                if self.mode == "newline_id":
                    acct["id"] = "acc\r\n1,injected"
                    acct["name"] = "N" * 5000
                return httpx.Response(200, json=[acct])
            if request.url.path.endswith("/balances"):
                return httpx.Response(200, json={"available": "300.00", "ledger": "312.45"})
            if request.url.path.endswith("/transactions"):
                if self.mode == "amounts":
                    text = json.dumps([{"id": "x1", "amount": "1e308", "date": "2026-10-10", "description": "a", "status": "posted"},
                                       {"id": "x2", "amount": "NaN", "date": "2026-10-10", "description": "b", "status": "posted"},
                                       {"id": "x3", "amount": None, "date": "2026-10-10", "description": "c", "status": "posted"},
                                       {"id": "x4", "amount": "-12.50", "date": "2026-10-10", "description": "<b>" + "d" * 10_000, "status": "posted"},
                                       {"id": "x5", "amount": "-1.00", "date": "2026-13-45", "description": "e", "status": "posted"}]).replace('"NaN"', "NaN")
                    return httpx.Response(200, content=text.encode(), headers={"content-type": "application/json"})
                if self.mode == "drip":
                    self.drip = _Drip(json.dumps([{"id": f"x{i}", "amount": "-1.00", "date": "2026-10-10", "description": "d", "status": "posted"} for i in range(50)]), 0.2, 40)
                    return httpx.Response(200, stream=self.drip, headers={"content-type": "application/json"})
                return httpx.Response(200, json=[{"id": "x1", "amount": "-12.50", "date": "2026-10-10", "description": "EXAMPLE GROCER", "status": "posted", "details": {"category": "groceries"}}])
            return httpx.Response(404, json={"error": {"code": "not_found", "message": request.url.path}})

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.teller.io", auth=(token, ""))


@pytest.fixture
def teller(owner: TestClient):
    fake = FakeTeller()
    CONNECTORS["teller"].client_factory = fake.factory
    with Session(engine) as session:
        set_setting(session, "banks.secrets", {"teller": {"application_id": "app_123", "environment": "development"}})
    yield fake
    CONNECTORS["teller"].client_factory = None


@pytest.mark.parametrize("mode", ["echo_token", "not_json", "html_error", "network", "amounts", "newline_id", "drip"])
def test_teller_hostile_answers_never_crash_leak_or_hold_the_sync(owner: TestClient, teller: FakeTeller, caplog, monkeypatch, mode: str):
    caplog.set_level(logging.DEBUG)
    got = owner.post("/api/v1/banks/teller/enroll", json={"access_token": TELLER_TOKEN, "enrollment_id": "enr_1", "institution": "Example Bank"})
    assert got.status_code == 200, got.text
    assert TELLER_TOKEN not in got.text
    link_id = int(got.json()["link"]["id"])
    teller.mode = mode
    monkeypatch.setattr(teller_mod, "REQUEST_SECONDS", 1.5, raising=True)
    started = time.monotonic()
    result = CONNECTORS["teller"].sync(Session(engine), link_id=link_id)
    assert time.monotonic() - started < 20, mode
    said = _everything_said(owner, caplog, result)
    assert TELLER_TOKEN not in said, mode
    assert "<html>" not in said.replace("<html>down</html>", ""), mode  # nothing raw from the body lands in a record
    with Session(engine) as session:
        link = session.get(BankLink, link_id)
        assert link is not None and link.needs_login is False, mode
        assert len(link.last_error or "") <= 500
        for a in session.exec(select(Account)).all():
            assert len(a.name) <= 200 and "\n" not in (a.external_id or "") and "," not in (a.external_id or ""), mode
        rows = session.exec(select(Transaction).where(Transaction.source == "teller")).all()
        for t in rows:
            assert math.isfinite(t.amount) and abs(t.amount) < 1e9 and len(t.merchant) <= 200 and len(t.raw_name) <= 500, mode
    if mode == "amounts":
        assert result.ok and {t.amount for t in rows} == {12.5} and "left out" in result.summary
    if mode == "network":
        assert not result.ok and "could not be reached" in result.summary
    if mode == "drip":
        assert not result.ok and teller.drip is not None and teller.drip.sent < 40
    if mode == "echo_token":
        assert not result.ok and "[token]" in result.summary or "hidden" in result.summary or TELLER_TOKEN not in result.summary


def test_teller_certificate_and_key_stay_in_the_private_runtime_folder(tmp_path: Path):
    cert = "-----BEGIN CERTIFICATE-----\nMIIBsweep12cert\n-----END CERTIFICATE-----"
    key = "-----BEGIN PRIVATE KEY-----\nMIIBsweep12key\n-----END PRIVATE KEY-----"
    paths = teller_mod.cert_files(cert, key)
    assert paths is not None
    for p in paths:
        path = Path(p)
        assert path.is_file()
        assert not str(path.resolve()).startswith(str(Path(settings.data_dir).resolve())), "a PEM under the data folder rides along in backups"
        assert not str(path.resolve()).startswith(str(settings.import_root.resolve()))
        assert (path.stat().st_mode & 0o077) == 0, oct(path.stat().st_mode)
    folder = Path(paths[0]).parent
    assert (folder.stat().st_mode & 0o077) == 0
    assert not list(Path(settings.data_dir).rglob("*.pem"))
    teller_mod.forget_cert_files()
    assert not folder.exists()
