"""Investments and retirement accounts: a vendor through Plaid's investments product with holdings, an account
kept by hand with its balance over time, contributions read off the paystubs, and the overview's arithmetic."""

import json
from datetime import date

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors import CONNECTORS
from app.db import engine
from app.engine.household import seed
from app.engine.investments import investments_overview
from app.models import Account, BalanceSnapshot, Holding, Paystub

TODAY = date(2026, 10, 14)


class FakePlaidInvestments:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, body))
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-inv"})
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-sandbox-inv", "item_id": "item-inv"})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Retirement Co"}, "accounts": [
                {"account_id": "acc-401k", "name": "RETIREMENT SAVINGS PLAN", "mask": "7777", "type": "investment", "subtype": "401k", "balances": {"current": 24810.55}},
                {"account_id": "acc-roth", "name": "Roth IRA", "mask": "8888", "type": "investment", "subtype": "roth", "balances": {"current": 6120.00}},
            ]})
        if path == "/investments/holdings/get":
            return httpx.Response(200, json={
                "holdings": [
                    {"account_id": "acc-401k", "security_id": "sec-target", "quantity": 412.345, "institution_price": 52.10, "institution_value": 21483.17, "cost_basis": 18000.0, "institution_price_as_of": "2026-10-13"},
                    {"account_id": "acc-401k", "security_id": "sec-bond", "quantity": 300.0, "institution_price": 11.09, "institution_value": 3327.38, "cost_basis": 3200.0, "institution_price_as_of": "2026-10-13"},
                    {"account_id": "acc-roth", "security_id": "sec-etf", "quantity": 20.0, "institution_price": 306.0, "institution_value": 6120.0, "cost_basis": 5000.0, "institution_price_as_of": "2026-10-13"},
                ],
                "securities": [
                    {"security_id": "sec-target", "name": "Example Target 2055 Fund", "ticker_symbol": "EXTGT", "type": "mutual fund"},
                    {"security_id": "sec-bond", "name": "Example Bond Index", "ticker_symbol": "EXBND", "type": "fixed income"},
                    {"security_id": "sec-etf", "name": "Example Total Market ETF", "ticker_symbol": "EXTM", "type": "etf"},
                ],
            })
        if path == "/transactions/sync":
            return httpx.Response(200, json={"added": [], "modified": [], "removed": [], "next_cursor": "c1", "has_more": False})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


def test_vendor_through_plaid_with_holdings_and_subtypes(owner: TestClient):
    fake = FakePlaidInvestments()
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        token = owner.post("/api/v1/banks/plaid/link_token", json={"purpose": "investments"}).json()
        assert token["link_token"] == "link-sandbox-inv"
        assert fake.requests[-1][1]["products"] == ["investments"] and fake.requests[-1][1]["optional_products"] == ["transactions"]
        bank = owner.post("/api/v1/banks/plaid/link_token", json={}).json()
        assert bank["link_token"] and fake.requests[-1][1]["products"] == ["transactions"] and "investments" in fake.requests[-1][1]["optional_products"]

        linked = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-inv", "institution": "Example Retirement Co"}).json()
        assert linked["sync"]["ok"], linked
        assert {a["name"]: a["kind"] for a in linked["link"]["accounts"]} == {"RETIREMENT SAVINGS PLAN": "investment", "Roth IRA": "investment"}
        with Session(engine) as session:
            k = session.exec(select(Account).where(Account.external_id == "acc-401k")).one()
            r = session.exec(select(Account).where(Account.external_id == "acc-roth")).one()
            assert k.subtype == "401k" and r.subtype == "roth_ira" and k.balance == 24810.55
            holdings = session.exec(select(Holding).where(Holding.account_id == k.id)).all()
            assert {h.ticker: (h.kind, h.value) for h in holdings} == {"EXTGT": ("mutual fund", 21483.17), "EXBND": ("fixed income", 3327.38)}
            assert session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == k.id)).one().balance == 24810.55
            overview = investments_overview(session, TODAY)
        assert overview["total"] == round(24810.55 + 6120.0, 2) and overview["retirement"] == overview["total"] and overview["taxable"] == 0.0
        by_kind = {a["kind"]: a["value"] for a in overview["allocation"]}
        assert by_kind == {"mutual fund": 21483.17, "etf": 6120.0, "fixed income": 3327.38}
        assert abs(sum(a["share"] for a in overview["allocation"]) - 1.0) < 0.01
        plan = next(i for i in overview["items"] if i["subtype"] == "401k")
        assert plan["what"] == "401(k)" and plan["retirement"] and plan["holdings"][0]["ticker"] == "EXTGT" and plan["holdings_total"] == round(21483.17 + 3327.38, 2)

        # The second sync updates the holdings in place rather than doubling them.
        owner.post(f"/api/v1/banks/{linked['link']['id']}/sync")
        with Session(engine) as session:
            assert len(session.exec(select(Holding)).all()) == 3
        listed = owner.get("/api/v1/investments").json()
        assert listed["total"] == overview["total"] and any(v["via"] == "plaid" for v in listed["vendors"]) and "401k" in listed["subtypes"]
        accounts_page = owner.get("/api/v1/accounts/overview").json()
        assert accounts_page["assets"]["total"] >= overview["total"]  # investments count as what you have
    finally:
        CONNECTORS["plaid"].client_factory = None


def test_account_kept_by_hand_with_balances_over_time_and_contributions(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        session.add(Paystub(pay_date=date(2026, 10, 2), net=2240.0, gross=2780.0, stub_hash="stub-test-1", deductions=json.dumps([{"name": "401(k) pre-tax", "amount": 139.0}, {"name": "Federal income tax", "amount": 210.0}, {"name": "HSA", "amount": 25.0}])))
        session.commit()
    assert owner.post("/api/v1/investments", json={"name": "", "target": 1}).status_code == 400
    assert owner.post("/api/v1/investments", json={"name": "Old pension", "subtype": "lottery"}).status_code == 400
    made = owner.post("/api/v1/investments", json={"name": "Old pension", "institution": "Example Pension Fund", "subtype": "pension", "balance": 9000, "as_of": "2026-09-30"}).json()
    account = next(i for i in made["items"] if i["name"] == "Old pension")
    assert account["connector"] == "manual" and account["what"] == "pension" and account["retirement"] and account["balance"] == 9000.0 and account["balance_at"].startswith("2026-09-30")
    names = {c["name"]: c for c in made["contributions"]["items"]}
    assert names["401(k) pre-tax"]["last"] == 139.0 and names["HSA"]["last"] == 25.0  # the household's own stubs add their lines too
    assert made["contributions"]["per_paycheck"] >= round(139.0 + 25.0, 2)
    later = owner.put(f"/api/v1/investments/{account['id']}", json={"balance": 9250.5, "as_of": "2026-10-14"}).json()
    account = next(i for i in later["items"] if i["id"] == account["id"])
    assert account["balance"] == 9250.5 and [h["balance"] for h in account["history"]] == [9000.0, 9250.5]
    assert later["history"][-1]["balance"] == 9250.5
    held = owner.post(f"/api/v1/investments/{account['id']}/holdings", json={"name": "Stable value fund", "kind": "fixed income", "value": 9250.5}).json()
    holding = next(i for i in held["items"] if i["id"] == account["id"])["holdings"][0]
    assert holding["kind"] == "fixed income" and holding["share"] == 1.0
    gone = owner.delete(f"/api/v1/investments/{account['id']}").json()
    assert all(i["id"] != account["id"] for i in gone["items"])
    with Session(engine) as session:
        assert session.get(Account, account["id"]).active is False
