"""The live bank connectors against mocked vendors: Plaid's link, exchange and cursor sync; Teller's enroll
and sync; the ledger's sign and kind conventions; re-login flags; removal. No real bank is touched."""

import json
from datetime import date

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors import CONNECTORS
from app.connectors.banks import bank_settings
from app.db import engine
from app.models import Account, BankLink, Transaction
from app.store import get_setting


class FakePlaid:
    """Answers Plaid's endpoints from a script and records every request body."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.sync_pages = [
            {
                "added": [
                    {"transaction_id": "t1", "account_id": "acc-chk", "amount": 54.12, "date": "2026-10-10", "name": "EXAMPLE GROCER 123", "merchant_name": "Example Grocer", "pending": False, "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"}},
                    {"transaction_id": "t2", "account_id": "acc-chk", "amount": -2240.00, "date": "2026-10-02", "name": "EXAMPLE EMPLOYER PAYROLL", "merchant_name": None, "pending": False, "personal_finance_category": {"primary": "INCOME", "detailed": "INCOME_WAGES"}},
                    {"transaction_id": "t3", "account_id": "acc-card", "amount": 12.99, "date": "2026-10-11", "name": "STREAMING CO", "merchant_name": "Streaming Co", "pending": True, "personal_finance_category": {"primary": "ENTERTAINMENT", "detailed": "ENTERTAINMENT_TV_AND_MOVIES"}},
                ],
                "modified": [], "removed": [], "next_cursor": "c1", "has_more": True,
            },
            {
                "added": [{"transaction_id": "t4", "account_id": "acc-card", "amount": 300.00, "date": "2026-10-12", "name": "PAYMENT THANK YOU", "merchant_name": None, "pending": False, "personal_finance_category": {"primary": "LOAN_PAYMENTS", "detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}}],
                "modified": [{"transaction_id": "t1", "account_id": "acc-chk", "amount": 55.00, "date": "2026-10-10", "name": "EXAMPLE GROCER 123", "merchant_name": "Example Grocer", "pending": False, "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"}}],
                "removed": [], "next_cursor": "c2", "has_more": False,
            },
            {"added": [{"transaction_id": "t5", "account_id": "acc-card", "amount": 12.99, "date": "2026-10-12", "name": "STREAMING CO", "merchant_name": "Streaming Co", "pending": False, "pending_transaction_id": "t3", "personal_finance_category": {"primary": "ENTERTAINMENT", "detailed": "ENTERTAINMENT_TV_AND_MOVIES"}}],
             "modified": [], "removed": [{"transaction_id": "t4", "account_id": "acc-card"}], "next_cursor": "c3", "has_more": False},
        ]
        self.fail_with: dict | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, body))
        assert body["client_id"] == "cid" and body["secret"] == "s3cret-value-77"
        if self.fail_with:
            return httpx.Response(400, json=self.fail_with)
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-123"})
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-sandbox-abc", "item_id": "item-1"})
        if path == "/accounts/get":
            assert body["access_token"] == "access-sandbox-abc"
            return httpx.Response(200, json={
                "item": {"institution_name": "Example Bank"},
                "accounts": [
                    {"account_id": "acc-chk", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": 312.45, "available": 300.0}},
                    {"account_id": "acc-sav", "name": "Rainy Day", "mask": "5678", "type": "depository", "subtype": "savings", "balances": {"current": 1500.0}},
                    {"account_id": "acc-card", "name": "Rewards Card", "mask": "9012", "type": "credit", "subtype": "credit card", "balances": {"current": 842.10}},
                ],
            })
        if path == "/transactions/sync":
            page = self.sync_pages.pop(0)
            return httpx.Response(200, json=page)
        if path == "/item/remove":
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


def test_plaid_settings_link_exchange_sync_cursor_and_removal(owner: TestClient):
    fake = FakePlaid()
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    try:
        before = owner.get("/api/v1/banks").json()
        assert before["providers"]["plaid"]["configured"] is False and before["links"] == []
        assert owner.post("/api/v1/banks/plaid/link_token", json={}).status_code == 409

        saved = owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"}).json()
        assert saved["providers"]["plaid"] == {"configured": True, "environment": "sandbox", "client_id": "cid", "secret_set": True, "keys": {"result": "ok", "message": "Plaid accepts the keys for sandbox.", "environment": "sandbox"}}
        assert "s3cret-value-77" not in json.dumps(saved)
        with Session(engine) as session:
            raw = get_setting(session, "banks.secrets")["plaid"]["secret"]
            assert raw.startswith("fa1:") and bank_settings(session, "plaid")["secret"] == "s3cret-value-77"

        token = owner.post("/api/v1/banks/plaid/link_token", json={}).json()
        assert token["link_token"] == "link-sandbox-123" and token["environment"] == "sandbox"
        assert token["oauth_redirect"] == {"ok": False, "uri": None}  # no https address yet, so no OAuth redirect
        assert fake.requests[-1][1]["products"] == ["transactions"] and fake.requests[-1][1]["user"]["client_user_id"] == "owner"

        linked = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()
        link = linked["link"]
        assert link["provider"] == "plaid" and link["institution"] == "Example Bank" and link["last_error"] == ""
        assert linked["sync"]["ok"] and linked["sync"]["inserted"] == 4  # t3 is pending: kept, marked pending
        assert {a["name"]: (a["kind"], a["balance"]) for a in link["accounts"]} == {"Everyday Checking": ("checking", 300.0), "Rainy Day": ("savings", 1500.0), "Rewards Card": ("card", 842.1)}  # a spending account holds its available balance; a card owes its current one

        with Session(engine) as session:
            row = session.exec(select(BankLink)).one()
            assert row.cursor == "c2" and row.access_token_enc.startswith("fa1:") and "access-sandbox" not in row.access_token_enc
            by_ref = {t.source_ref: t for t in session.exec(select(Transaction).where(Transaction.source == "plaid")).all()}
            assert set(by_ref) == {"t1", "t2", "t3", "t4"} and by_ref["t3"].pending and not by_ref["t1"].pending
            assert by_ref["t1"].amount == 55.0 and by_ref["t1"].category == "Groceries" and by_ref["t1"].merchant == "Example Grocer"  # modified in page two
            assert by_ref["t2"].amount == -2240.0 and by_ref["t2"].is_income and by_ref["t2"].category == "Income"
            assert by_ref["t4"].is_card_payment and by_ref["t4"].category == "Credit Card Payment"
            assert by_ref["t4"].account_id == session.exec(select(Account).where(Account.external_id == "acc-card")).one().id

        again = owner.post(f"/api/v1/banks/{link['id']}/sync").json()
        assert again["ok"] and again["details"]["removed"] == 2 and fake.requests[-1][1]["cursor"] == "c2"
        with Session(engine) as session:
            assert session.exec(select(Transaction).where(Transaction.source_ref == "t4")).first() is None
            # the posted charge replaced its pending one: there once, never twice
            assert session.exec(select(Transaction).where(Transaction.source_ref == "t3")).first() is None
            assert not session.exec(select(Transaction).where(Transaction.source_ref == "t5")).one().pending
            assert session.exec(select(BankLink)).one().cursor == "c3"

        fake.fail_with = {"error_type": "ITEM_ERROR", "error_code": "ITEM_LOGIN_REQUIRED", "error_message": "the login details of this item have changed", "display_message": "Please sign in again."}
        failed = owner.post(f"/api/v1/banks/{link['id']}/sync").json()
        assert failed["ok"] is False and "ITEM_LOGIN_REQUIRED" in failed["summary"] and failed["link"]["needs_login"] is True
        fake.fail_with = None

        update = owner.post("/api/v1/banks/plaid/link_token", json={"link_id": link["id"]}).json()
        assert update["link_token"] and fake.requests[-1][1]["access_token"] == "access-sandbox-abc" and "products" not in fake.requests[-1][1]

        assert owner.delete(f"/api/v1/banks/{link['id']}").json() == {"ok": True}
        assert fake.requests[-1][0] == "/item/remove"
        assert owner.get("/api/v1/banks").json()["links"] == []
        with Session(engine) as session:
            row = session.exec(select(BankLink)).one()
            assert row.active is False and row.access_token_enc == ""
    finally:
        CONNECTORS["plaid"].client_factory = None


class FakeTeller:
    def __init__(self) -> None:
        self.seen_auth: list[tuple[str, str]] = []
        self.paths: list[str] = []

    def factory(self, access_token: str) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)
            self.seen_auth.append((access_token, request.headers.get("authorization", "")))
            path = request.url.path
            if path == "/accounts":
                return httpx.Response(200, json=[
                    {"id": "acc_1", "enrollment_id": "enr_1", "institution": {"id": "example_cu", "name": "Example Credit Union"}, "name": "Share Draft", "type": "depository", "subtype": "checking", "last_four": "4321", "status": "open"},
                    {"id": "acc_2", "enrollment_id": "enr_1", "institution": {"id": "example_cu", "name": "Example Credit Union"}, "name": "Visa Platinum", "type": "credit", "subtype": "credit_card", "last_four": "8765", "status": "open"},
                ])
            if path == "/accounts/acc_1/balances":
                return httpx.Response(200, json={"account_id": "acc_1", "ledger": "1010.55", "available": "990.00"})
            if path == "/accounts/acc_2/balances":
                return httpx.Response(200, json={"account_id": "acc_2", "ledger": "-640.00", "available": "2360.00"})
            if path == "/accounts/acc_1/transactions":
                assert request.url.params["count"] == "500"
                return httpx.Response(200, json=[
                    {"id": "txn_1", "account_id": "acc_1", "date": "2026-10-09", "description": "EXAMPLE TAQUERIA", "amount": "-18.40", "status": "posted", "type": "card_payment", "details": {"category": "dining", "counterparty": {"name": "Example Taqueria", "type": "organization"}}},
                    {"id": "txn_2", "account_id": "acc_1", "date": "2026-10-13", "description": "PENDING COFFEE", "amount": "-4.00", "status": "pending", "type": "card_payment", "details": {"category": "dining"}},
                    {"id": "txn_3", "account_id": "acc_1", "date": "2026-10-08", "description": "TRANSFER FROM SAVINGS", "amount": "200.00", "status": "posted", "type": "transfer", "details": {"category": "transfer"}},
                ])
            if path == "/accounts/acc_2/transactions":
                return httpx.Response(200, json=[{"id": "txn_4", "account_id": "acc_2", "date": "2026-10-07", "description": "FUEL STOP 55", "amount": "-41.10", "status": "posted", "type": "card_payment", "details": {"category": "fuel", "counterparty": {"name": "Fuel Stop"}}}])
            return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.teller.io")


def test_teller_enroll_and_sync_with_sign_and_kind_conventions(owner: TestClient):
    fake = FakeTeller()
    CONNECTORS["teller"].client_factory = fake.factory
    try:
        assert owner.post("/api/v1/banks/teller/enroll", json={"access_token": "token_x", "enrollment_id": "enr_1", "institution": "Example Credit Union"}).status_code == 409
        saved = owner.put("/api/v1/banks/teller/settings", json={"application_id": "app_123", "environment": "development"}).json()
        assert saved["providers"]["teller"] == {"configured": True, "environment": "development", "application_id": "app_123", "certificate_set": False, "private_key_set": False}

        enrolled = owner.post("/api/v1/banks/teller/enroll", json={"access_token": "token_x", "enrollment_id": "enr_1", "institution": "Example Credit Union"}).json()
        assert enrolled["sync"]["ok"] and enrolled["sync"]["inserted"] == 4
        assert all(tok == "token_x" for tok, _ in fake.seen_auth)
        assert {a["name"]: (a["kind"], a["balance"]) for a in enrolled["link"]["accounts"]} == {"Share Draft": ("checking", 990.0), "Visa Platinum": ("card", 640.0)}  # checking holds what is available; a card owes its ledger

        with Session(engine) as session:
            by_ref = {t.source_ref: t for t in session.exec(select(Transaction).where(Transaction.source == "teller")).all()}
            assert set(by_ref) == {"txn_1", "txn_2", "txn_3", "txn_4"} and by_ref["txn_2"].pending  # the pending coffee shows, marked pending
            assert by_ref["txn_1"].amount == 18.4 and by_ref["txn_1"].merchant == "Example Taqueria" and by_ref["txn_1"].category == "Dining & Drinks"
            assert by_ref["txn_3"].amount == -200.0 and by_ref["txn_3"].is_transfer
            assert by_ref["txn_4"].category == "Gas" and by_ref["txn_4"].date == date(2026, 10, 7)
            link = session.exec(select(BankLink).where(BankLink.provider == "teller")).one()
            assert link.accounts == "acc_1,acc_2" and link.access_token_enc.startswith("fa1:")

        second = owner.post(f"/api/v1/banks/{enrolled['link']['id']}/sync").json()
        assert second["ok"] and second["inserted"] == 0 and second["details"]["updated"] == 4
        assert owner.delete(f"/api/v1/banks/{enrolled['link']['id']}").json() == {"ok": True}
    finally:
        CONNECTORS["teller"].client_factory = None


def test_bank_secrets_are_never_listed_and_connectors_are_registered(owner: TestClient):
    listed = {c["id"]: c for c in owner.get("/api/v1/connectors").json()["connectors"]}
    assert "plaid" in listed and "teller" in listed
    assert all(s.get("secret") for s in listed["teller"]["settings"] if s["name"] in ("certificate", "private_key"))
    owner.put("/api/v1/banks/teller/settings", json={"application_id": "app_123", "certificate": "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----", "private_key": "-----BEGIN PRIVATE KEY-----\nxyz\n-----END PRIVATE KEY-----"})
    got = owner.get("/api/v1/banks").json()
    assert got["providers"]["teller"]["certificate_set"] and got["providers"]["teller"]["private_key_set"]
    assert "BEGIN" not in json.dumps(got)
    # Saving the form again without the secrets leaves them in place.
    owner.put("/api/v1/banks/teller/settings", json={"application_id": "app_124", "certificate": "", "private_key": ""})
    with Session(engine) as session:
        s = bank_settings(session, "teller")
        assert s["application_id"] == "app_124" and s["certificate"].startswith("-----BEGIN CERTIFICATE")


def test_link_token_falls_back_when_the_redirect_uri_is_not_registered(owner: TestClient, monkeypatch):
    """An OAuth redirect URI Plaid does not know yet must not block banks that sign in without OAuth."""
    from app.config import settings

    monkeypatch.setattr(settings, "public_url", "https://money.example.test")
    seen: list[dict] = []
    registered = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if request.url.path != "/link/token/create":
            return httpx.Response(404, json={"error_code": "NOT_FOUND"})
        if "redirect_uri" in body and not registered["ok"]:
            return httpx.Response(400, json={"error_type": "INVALID_REQUEST", "error_code": "INVALID_FIELD", "error_message": "OAuth redirect URI must be configured in the developer dashboard."})
        return httpx.Response(200, json={"link_token": "link-sandbox-ok"})

    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        made = owner.post("/api/v1/banks/plaid/link_token", json={}).json()
        assert made["link_token"] == "link-sandbox-ok" and made["oauth_redirect"]["ok"] is False
        assert made["oauth_redirect"]["uri"] == "https://money.example.test/settings/connectors" and "Register it" in made["oauth_redirect"]["note"]
        assert "redirect_uri" in seen[-2] and "redirect_uri" not in seen[-1]  # tried with it, then without
        steps = {s["id"]: s for s in owner.get("/api/v1/next_steps").json()["steps"]}
        assert "plaid-redirect" in steps and "money.example.test/settings/connectors" in steps["plaid-redirect"]["text"]
        assert "plaid-sandbox" in steps  # sandbox reaches test banks only; production needs Plaid's go-ahead
        registered["ok"] = True
        again = owner.post("/api/v1/banks/plaid/link_token", json={}).json()
        assert again["oauth_redirect"]["ok"] is True and "redirect_uri" in seen[-1]
        assert "plaid-redirect" not in {s["id"] for s in owner.get("/api/v1/next_steps").json()["steps"]}
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_saving_plaid_keys_names_a_sandbox_secret_set_to_production(owner: TestClient):
    """Plaid issues one secret per environment; the sandbox secret with the environment on production is the usual
    slip, and the page and the next steps must say so instead of a bare 'invalid keys'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/link/token/create":
            return httpx.Response(404, json={"error_code": "NOT_FOUND"})
        if request.url.host.startswith("production"):
            return httpx.Response(400, json={"error_type": "INVALID_INPUT", "error_code": "INVALID_API_KEYS", "error_message": "invalid client_id or secret provided"})
        return httpx.Response(200, json={"link_token": "link-sandbox-ok"})

    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    try:
        out = owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "sandbox-secret-value", "environment": "production"}).json()
        keys = out["providers"]["plaid"]["keys"]
        assert keys["result"] == "sandbox_secret" and "production secret" in keys["message"] and keys["environment"] == "production"
        refused = owner.post("/api/v1/banks/plaid/link_token", json={})
        assert refused.status_code == 502 and "sandbox one" in refused.json()["detail"]
        steps = {s["id"]: s for s in owner.get("/api/v1/next_steps").json()["steps"]}
        assert steps["plaid-keys"]["severity"] == "warning" and steps["plaid-keys"]["link"] == "/settings/connectors#banks"
        assert "plaid-sandbox" not in steps  # the environment is production; the key verdict is the one step that matters
        out = owner.put("/api/v1/banks/plaid/settings", json={"environment": "sandbox"}).json()
        assert out["providers"]["plaid"]["keys"]["result"] == "ok"
        assert "plaid-keys" not in {s["id"] for s in owner.get("/api/v1/next_steps").json()["steps"]}
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_freshness_counts_calls_and_forecasts_the_cost(owner: TestClient):
    from datetime import date, datetime, timedelta, timezone

    from app import usage
    from app.jobs.scheduler import bank_sync
    from app.models import BankLink

    usage.record_call("plaid", "/transactions/sync", today=date(2026, 10, 14))
    usage.record_call("plaid", "/transactions/sync", today=date(2026, 10, 14))
    usage.record_call("plaid", "/accounts/get", today=date(2026, 10, 14))
    with Session(engine) as session:
        counted = usage.calls(session, date(2026, 10, 14))
        session.add(BankLink(provider="plaid", external_id="item-1", institution="Example Bank", environment="production", last_sync_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
        session.commit()
    assert counted["by_day"][-1]["plaid"] == 3 and counted["by_endpoint"]["plaid:/transactions/sync"] == 2
    got = owner.get("/api/v1/banks/freshness").json()
    assert got["sync_every_hours"] == 4 and got["plaid"]["items_made"] == 1 and got["cost"]["monthly"] is None and "Trial" in got["cost"]["plan_note"]
    assert owner.put("/api/v1/banks/freshness", json={"sync_every_hours": 3}).status_code == 400
    priced = owner.put("/api/v1/banks/freshness", json={"sync_every_hours": 1, "price_per_item": 0.3}).json()
    assert priced["sync_every_hours"] == 1 and priced["cost"]["monthly"] == 0.3 and priced["cost"]["yearly"] == 3.6
    owner.put("/api/v1/banks/freshness", json={"sync_every_hours": 4, "clear_price": True})
    assert bank_sync() == "not due yet"  # read half an hour ago; the next read is in three and a half hours


def test_a_withdrawn_transaction_that_paid_a_bill_is_removed_without_stalling_the_link():
    from datetime import date as _date

    from app.connectors.banks import remove_transaction, tx_hash
    from app.models import Account, Bill, Payment, Transaction

    with Session(engine) as session:
        a = Account(name="Checking", kind="checking", connector="plaid")
        b = Bill(name="Phone", amount=96.0, due_day=5)
        session.add_all([a, b])
        session.flush()
        t = Transaction(account_id=a.id, date=_date(2026, 10, 5), merchant="Phone", amount=96.0, row_hash=tx_hash("plaid", "t-1"), source="plaid")
        session.add(t)
        session.flush()
        session.add(Payment(bill_id=b.id, due_date=_date(2026, 10, 5), amount=96.0, status="paid", transaction_id=t.id))
        session.commit()
        assert remove_transaction(session, "plaid", "t-1")
        session.commit()
        p = session.exec(select(Payment)).one()
        assert p.transaction_id is None and p.status == "scheduled"


def test_a_plaid_notice_is_verified_before_it_starts_anything():
    import base64 as _b64
    import hashlib as _h
    import time as _t

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from app.connectors import plaid_webhook as pw

    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()

    def b64(raw: bytes) -> str:
        return _b64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {"kty": "EC", "crv": "P-256", "x": b64(nums.x.to_bytes(32, "big")), "y": b64(nums.y.to_bytes(32, "big")), "expired_at": None}

    def token(body: bytes, iat: float, alg: str = "ES256", kid: str = "k1") -> str:
        head = b64(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
        claims = b64(json.dumps({"iat": int(iat), "request_body_sha256": _h.sha256(body).hexdigest()}).encode())
        r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"{head}.{claims}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    body = json.dumps({"webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE", "item_id": "item-1"}).encode()
    now = _t.time()
    fetch = lambda kid: jwk  # noqa: E731
    assert pw.verify(token(body, now), body, fetch, now)["request_body_sha256"]
    for bad in (token(body, now - 600), token(body + b" ", now), token(body, now, alg="HS256")):
        try:
            pw.verify(bad, body, fetch, now)
            raise AssertionError("accepted a bad notice")
        except pw.Refused:
            pass
    tampered = token(body, now)
    try:
        pw.verify(tampered, body.replace(b"item-1", b"item-2"), fetch, now)
        raise AssertionError("accepted another body")
    except pw.Refused:
        pass


def test_the_webhook_route_refuses_what_does_not_verify(client: TestClient):
    r = client.post("/api/plaid/webhook", content=b'{"webhook_type":"TRANSACTIONS","item_id":"x"}', headers={"Plaid-Verification": "not.a.token", "Content-Type": "application/json"})
    assert r.status_code == 401 and r.json() == {"ok": False}
    assert client.post("/api/plaid/webhook", content=b"{}").status_code == 401


class TwoSignIns(FakePlaid):
    """The same bank sign-in linked twice: a second item, its own token and account ids, the same accounts by
    their last four digits. `/item/get` says when Plaid last heard from the bank."""

    def __init__(self) -> None:
        super().__init__()
        self.sync_pages += [{"added": [], "modified": [], "removed": [], "next_cursor": f"e{i}", "has_more": False} for i in range(6)]
        self.exchanges = 0
        self.bank_updated: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        if path == "/item/public_token/exchange":
            self.requests.append((path, body))
            self.exchanges += 1
            return httpx.Response(200, json={"access_token": f"access-sandbox-{'abc' if self.exchanges == 1 else 'two'}", "item_id": f"item-{self.exchanges}"})
        if path == "/item/get":
            self.requests.append((path, body))
            status = {"transactions": {"last_successful_update": self.bank_updated}} if self.bank_updated else {}
            return httpx.Response(200, json={"item": {}, "status": status})
        if path == "/accounts/get" and body["access_token"] == "access-sandbox-two":
            self.requests.append((path, body))
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": [
                {"account_id": "two-chk", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": 99.0}},
                {"account_id": "two-sav", "name": "Rainy Day", "mask": "5678", "type": "depository", "subtype": "savings", "balances": {"current": 99.0}},
                {"account_id": "two-card", "name": "Rewards Card", "mask": "9012", "type": "credit", "subtype": "credit card", "balances": {"current": 99.0}},
            ]})
        if path == "/transactions/sync" and body["access_token"] == "access-sandbox-two":
            self.requests.append((path, body))
            return httpx.Response(200, json={"added": [], "modified": [], "removed": [], "next_cursor": "z", "has_more": False})
        return super().handler(request)


def test_the_same_sign_in_linked_twice_is_undone_and_says_why(owner: TestClient):
    """A debit card is part of its checking account: linking 'the card' signs in to the same bank again. That second
    link brings nothing new, so it is undone at Plaid at once and the owner is told in words; nothing is doubled."""
    fake = TwoSignIns()
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        first = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "p1", "institution": "Example Bank"}).json()
        assert "duplicate" not in first and len(first["link"]["accounts"]) == 3
        second = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "p2", "institution": "Example Bank"}).json()
        assert second["duplicate"] is True and "same Example Bank sign-in" in second["said"] and "debit card" in second["said"]
        assert fake.requests[-1] == ("/item/remove", fake.requests[-1][1]) and fake.requests[-1][1]["access_token"] == "access-sandbox-two"
        assert [l["id"] for l in owner.get("/api/v1/banks").json()["links"]] == [first["link"]["id"]]
        with Session(engine) as session:
            assert len(session.exec(select(Account)).all()) == 3
            assert session.exec(select(Account).where(Account.external_id == "acc-chk")).one().balance == 300.0  # the second link wrote nothing
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_a_balance_read_off_the_bank_stands_until_the_bank_sends_newer(owner: TestClient):
    """A bank's feed can run a day behind and leave holds out. The owner types what the bank's own screen shows; a
    sync of older bank data leaves it standing, and the balance is dated when the bank sent it, not when the app
    asked. Once the bank sends newer data, the bank's balance is the balance again."""
    from datetime import UTC, datetime, timedelta

    fake = TwoSignIns()
    fake.bank_updated = (datetime.now(UTC) - timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        link = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "p1", "institution": "Example Bank"}).json()["link"]
        assert link["bank_updated_at"] and link["bank_updated_at"].startswith(fake.bank_updated[:16])
        with Session(engine) as session:
            chk = session.exec(select(Account).where(Account.external_id == "acc-chk")).one()
            assert chk.balance == 300.0 and chk.balance_at.isoformat()[:16] == fake.bank_updated[:16]  # dated by the bank
            chk_id = chk.id
        out = owner.post(f"/api/v1/banks/accounts/{chk_id}/shows", json={"amount": 215.80}).json()
        assert "$215.80 in Everyday Checking" in out["said"] and "newer data" in out["said"]
        owner.post(f"/api/v1/banks/{link['id']}/sync")  # the bank has sent nothing newer
        with Session(engine) as session:
            assert session.get(Account, chk_id).balance == 215.80
        told = next(a for a in owner.get("/api/v1/snapshot").json()["fresh"]["accounts"] if a["id"] == chk_id)
        assert told["told"]["amount"] == 215.80
        steps = owner.get("/api/v1/snapshot").json()["safe_to_spend"]["steps"]
        assert any(st.get("note") == "as you read it off your bank" for st in steps)
        fake.bank_updated = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        owner.post(f"/api/v1/banks/{link['id']}/sync")
        with Session(engine) as session:
            assert session.get(Account, chk_id).balance == 300.0  # the bank's newer balance takes over
        assert "told" not in next(a for a in owner.get("/api/v1/snapshot").json()["fresh"]["accounts"] if a["id"] == chk_id)
        assert owner.post("/api/v1/banks/accounts/999999/shows", json={"amount": 1}).status_code == 404
        # a slip of the keys is taken back: the bank's own figure returns, however many times it was typed over
        owner.post(f"/api/v1/banks/accounts/{chk_id}/shows", json={"amount": 21580})
        owner.post(f"/api/v1/banks/accounts/{chk_id}/shows", json={"amount": 2158})
        mine = next(a for a in owner.get("/api/v1/snapshot").json()["fresh"]["accounts"] if a["id"] == chk_id)
        assert mine["balance"] == 2158 and mine["told"]["bank"] == 300.0
        out = owner.delete(f"/api/v1/banks/accounts/{chk_id}/shows").json()
        assert "back to what the bank last sent: $300.00" in out["said"]
        with Session(engine) as session:
            assert session.get(Account, chk_id).balance == 300.0
        assert owner.delete(f"/api/v1/banks/accounts/{chk_id}/shows").status_code == 404
    finally:
        CONNECTORS["plaid"].client_factory = None


class FlakyPlaid(FakePlaid):
    """Refuses chosen calls once each, the way Plaid does: `refuse` maps a path to the error it answers first."""

    def __init__(self, refuse: dict[str, list[dict]]) -> None:
        super().__init__()
        self.refuse = refuse

    def handler(self, request: httpx.Request) -> httpx.Response:
        queued = self.refuse.get(request.url.path)
        if queued:
            self.requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(400, json=queued.pop(0))
        return super().handler(request)


def _link_with(owner: TestClient, fake: FakePlaid) -> dict:
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
    return owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()


def test_a_sync_whose_rows_change_mid_read_reads_again_from_its_cursor(owner: TestClient):
    # The second page is refused once: the read starts over from the first page, not from the page it reached.
    mutation = {"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "error_message": "Underlying transaction data changed"}
    fake = FlakyPlaid({})
    first = fake.sync_pages[0]
    fake.sync_pages.insert(1, None)  # the refused call takes no page
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/transactions/sync" and fake.sync_pages and fake.sync_pages[0] is None:
            fake.sync_pages[0] = first  # the read begins again with the first page
            fake.requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(400, json=mutation)
        return original(request)

    fake.handler = handler  # type: ignore[method-assign]
    try:
        linked = _link_with(owner, fake)
        assert linked["sync"]["ok"] and linked["sync"]["inserted"] == 4 and linked["link"]["last_error"] == ""
        cursors = [body.get("cursor", "") for path, body in fake.requests if path == "/transactions/sync"]
        assert cursors == ["", "c1", "", "c1"]
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_an_item_with_no_accounts_asks_for_a_sign_in_that_picks_accounts_again(owner: TestClient):
    fake = FlakyPlaid({})
    try:
        link = _link_with(owner, fake)["link"]
        fake.refuse["/accounts/get"] = [{"error_code": "NO_ACCOUNTS", "error_message": "No valid accounts were found at the financial institution."}]
        synced = owner.post(f"/api/v1/banks/{link['id']}/sync").json()
        assert not synced["ok"] and synced["link"]["needs_login"] is True
        steps = {s["id"]: s for s in owner.get("/api/v1/next_steps").json()["steps"]}
        assert steps[f"link-signin-{link['id']}"]["link"] == f"/settings/connectors?signin={link['id']}"
        owner.post("/api/v1/banks/plaid/link_token", json={"link_id": link["id"]})
        body = fake.requests[-1][1]
        assert body["access_token"] == "access-sandbox-abc" and body["update"] == {"account_selection_enabled": True}
    finally:
        CONNECTORS["plaid"].client_factory = None
