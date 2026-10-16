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
            {"added": [], "modified": [], "removed": [{"transaction_id": "t4", "account_id": "acc-card"}], "next_cursor": "c3", "has_more": False},
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
        assert linked["sync"]["ok"] and linked["sync"]["inserted"] == 3  # t3 is pending and skipped; t1, t2, t4 land
        assert {a["name"]: (a["kind"], a["balance"]) for a in link["accounts"]} == {"Everyday Checking": ("checking", 300.0), "Rainy Day": ("savings", 1500.0), "Rewards Card": ("card", 842.1)}  # a spending account holds its available balance; a card owes its current one

        with Session(engine) as session:
            row = session.exec(select(BankLink)).one()
            assert row.cursor == "c2" and row.access_token_enc.startswith("fa1:") and "access-sandbox" not in row.access_token_enc
            by_ref = {t.source_ref: t for t in session.exec(select(Transaction).where(Transaction.source == "plaid")).all()}
            assert set(by_ref) == {"t1", "t2", "t4"}
            assert by_ref["t1"].amount == 55.0 and by_ref["t1"].category == "Groceries" and by_ref["t1"].merchant == "Example Grocer"  # modified in page two
            assert by_ref["t2"].amount == -2240.0 and by_ref["t2"].is_income and by_ref["t2"].category == "Income"
            assert by_ref["t4"].is_card_payment and by_ref["t4"].category == "Credit Card Payment"
            assert by_ref["t4"].account_id == session.exec(select(Account).where(Account.external_id == "acc-card")).one().id

        again = owner.post(f"/api/v1/banks/{link['id']}/sync").json()
        assert again["ok"] and again["details"]["removed"] == 1 and fake.requests[-1][1]["cursor"] == "c2"
        with Session(engine) as session:
            assert session.exec(select(Transaction).where(Transaction.source_ref == "t4")).first() is None
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
        assert enrolled["sync"]["ok"] and enrolled["sync"]["inserted"] == 3
        assert all(tok == "token_x" for tok, _ in fake.seen_auth)
        assert {a["name"]: (a["kind"], a["balance"]) for a in enrolled["link"]["accounts"]} == {"Share Draft": ("checking", 1010.55), "Visa Platinum": ("card", 640.0)}

        with Session(engine) as session:
            by_ref = {t.source_ref: t for t in session.exec(select(Transaction).where(Transaction.source == "teller")).all()}
            assert set(by_ref) == {"txn_1", "txn_3", "txn_4"}  # the pending coffee is skipped
            assert by_ref["txn_1"].amount == 18.4 and by_ref["txn_1"].merchant == "Example Taqueria" and by_ref["txn_1"].category == "Dining & Drinks"
            assert by_ref["txn_3"].amount == -200.0 and by_ref["txn_3"].is_transfer
            assert by_ref["txn_4"].category == "Gas" and by_ref["txn_4"].date == date(2026, 10, 7)
            link = session.exec(select(BankLink).where(BankLink.provider == "teller")).one()
            assert link.accounts == "acc_1,acc_2" and link.access_token_enc.startswith("fa1:")

        second = owner.post(f"/api/v1/banks/{enrolled['link']['id']}/sync").json()
        assert second["ok"] and second["inserted"] == 0 and second["details"]["updated"] == 3
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
