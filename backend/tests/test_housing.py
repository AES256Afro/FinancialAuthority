"""M37 Housing and M38 utilities and documents: a home's charges make the bills every figure counts, the costs of
getting in are one-time bills, the old lease's notice is a dated step, and a lease's terms wait for the owner."""

import io
import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.billdates import bill_dates
from app.engine.documents import read_pages, terms_of
from app.engine.next_steps import next_steps
from app.models import Bill, ExtractedFact, Income

TODAY = date(2026, 10, 14)
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

LEASE = """RESIDENTIAL LEASE AGREEMENT
Example Flats, Unit 12
Monthly rent of $1,250.00 is due on or before the 1st day of each month.
Pet rent $20.00 per month.
A late fee of $75.00 is charged after the 3rd.
Security deposit: $300.00
Lease ends on March 31, 2027. Resident must give 60 days written notice to vacate.
"""


def _home(owner: TestClient, **kw) -> dict:
    body = {"name": "Example Flats 12", "kind": "rent", "starts_on": "2026-11-30", "first_due": "2027-01-01", "due_day": 1, **kw}
    out = owner.post("/api/v1/housing", json=body).json()
    return next(h for h in out["homes"] if h["name"] == body["name"])


def test_a_bill_comes_due_only_between_its_first_and_last_dates():
    bill = Bill(name="Lease", amount=100.0, due_day=1, starts_on=date(2027, 1, 1), ends_on=date(2027, 3, 31))
    assert bill_dates(bill, date(2026, 11, 1), date(2027, 6, 30)) == [date(2027, 1, 1), date(2027, 2, 1), date(2027, 3, 1)]
    assert bill_dates(bill, date(2026, 11, 1), date(2026, 12, 31)) == []


def test_a_homes_charges_make_one_rent_bill_and_unknown_amounts_are_named_not_counted(owner: TestClient):
    home = _home(owner)
    for charge in (
        {"name": "Rent", "kind": "rent", "amount": 1250.0},
        {"name": "Pet rent", "kind": "pet", "amount": 20.0},
        {"name": "Trash", "kind": "fee", "amount": 30.0},
        {"name": "Renter's insurance", "kind": "insurance", "pays_to": "other", "payee": "Example Insurer"},
    ):
        out = owner.post(f"/api/v1/housing/{home['id']}/charges", json=charge).json()
    home = next(h for h in out["homes"] if h["id"] == home["id"])
    assert home["to_landlord_monthly"] == 1300.0 and home["missing"] == ["Renter's insurance"] and home["state"] == "upcoming"
    with Session(engine) as session:
        bill = session.get(Bill, home["bill_id"])
        assert bill.name == "Rent · Example Flats 12" and bill.amount == 1300.0 and bill.starts_on == date(2027, 1, 1) and bill.active
        assert bill_dates(bill, TODAY, date(2027, 1, 31)) == [date(2027, 1, 1)]  # the move-in check covers December
        assert session.exec(select(Bill).where(Bill.name == "Renter's insurance")).first() is None  # no amount, no bill
    # The insurer's amount arrives: its own bill, on its own day
    charge_id = next(c["id"] for c in home["charges"] if c["name"] == "Renter's insurance")
    out = owner.put(f"/api/v1/housing/charges/{charge_id}", json={"name": "Renter's insurance", "kind": "insurance", "pays_to": "other", "amount": 14.0, "due_day": 5}).json()
    home = next(h for h in out["homes"] if h["id"] == home["id"])
    assert home["missing"] == [] and home["own_bills_monthly"] == 14.0 and home["monthly_total"] == 1314.0 and home["to_landlord_monthly"] == 1300.0
    with Session(engine) as session:
        ins = session.exec(select(Bill).where(Bill.name == "Renter's insurance")).one()
        assert ins.category == "Insurance" and ins.due_day == 5 and ins.home_id == home["id"]


def test_move_in_costs_are_one_time_bills_and_a_cost_paid_already_is_shown_not_counted(owner: TestClient):
    home = _home(owner)
    parts = [{"name": "Deposit", "amount": 300.0, "refundable": True}, {"name": "December rent", "amount": 650.0}]
    owner.post(f"/api/v1/housing/{home['id']}/costs", json={"name": "Move-in check", "amount": 950.0, "due_on": "2026-11-29", "parts": parts})
    owner.post(f"/api/v1/housing/{home['id']}/costs", json={"name": "Application fee", "amount": 75.0, "paid": True})
    out = owner.post(f"/api/v1/housing/{home['id']}/costs", json={"name": "Truck", "due_on": "2026-11-30"}).json()
    home = next(h for h in out["homes"] if h["id"] == home["id"])
    assert home["costs_total"] == 950.0 and home["costs_paid_before"] == 75.0 and home["costs_missing"] == ["Truck"] and home["refundable_in_costs"] == 300.0
    with Session(engine) as session:
        check = session.exec(select(Bill).where(Bill.name == "Move-in check")).one()
        assert check.category == "Moving" and bill_dates(check, TODAY, date(2027, 6, 30)) == [date(2026, 11, 29)]
        assert session.exec(select(Bill).where(Bill.name == "Application fee")).first() is None
    move = owner.get(f"/api/v1/housing/{home['id']}/move").json()
    assert move["move_on"] == "2026-11-30" and move["status"] in ("good", "crit") and move["path"]


def test_the_old_lease_ends_on_the_move_with_its_notice_a_dated_step(owner: TestClient):
    with Session(engine) as session:
        session.add(Income(name="Example Employer", cadence="semimonthly", expected_net=1800.0, next_date=date(2026, 10, 15)))
        session.add(Bill(name="Rent", amount=1100.0, due_day=1, category="Rent"))
        session.commit()
    out = owner.get("/api/v1/housing").json()
    assert [b["name"] for b in out["rent_bills_to_adopt"]] == ["Rent"]
    with Session(engine) as session:
        assert any(s["id"].startswith("home-adopt-") for s in next_steps(session, TODAY)["steps"])
    out = owner.post("/api/v1/housing/adopt", json={"bill_id": out["rent_bills_to_adopt"][0]["id"], "name": "Example Court 4"}).json()
    old = out["homes"][0]
    assert old["state"] == "current" and old["to_landlord_monthly"] == 1100.0 and out["rent_bills_to_adopt"] == []
    body = {"name": "Example Court 4", "ends_on": "2026-11-30", "notice_days": 30, "due_day": 1}
    old = next(h for h in owner.put(f"/api/v1/housing/{old['id']}", json=body).json()["homes"] if h["id"] == old["id"])
    assert old["notice"]["by"] == "2026-10-30" and old["notice"]["status"] == "warn" and old["notice"]["days_left"] == 16
    with Session(engine) as session:
        rent = session.get(Bill, old["bill_id"])
        assert bill_dates(rent, TODAY, date(2027, 2, 1)) == [date(2026, 11, 1)]  # nothing on December 1
        step = next(s for s in next_steps(session, TODAY)["steps"] if s["id"] == f"home-notice-{old['id']}")
        assert step["severity"] == "warning" and "Oct 30" in step["text"] and step["link"] == f"/housing#home-{old['id']}"
    _home(owner, name="Example Flats 12")
    over = owner.get("/api/v1/housing").json()
    assert over["move"]["from"] == "Example Court 4" and over["move"]["to"] == "Example Flats 12" and over["move"]["on"] == "2026-11-30"
    given = owner.post(f"/api/v1/housing/{old['id']}/notice", json={"given_on": "2026-10-20"}).json()
    assert next(h for h in given["homes"] if h["id"] == old["id"])["notice"]["status"] == "good"
    with Session(engine) as session:
        assert not any(s["id"] == f"home-notice-{old['id']}" for s in next_steps(session, TODAY)["steps"])


def test_a_utility_says_where_it_comes_from_and_a_known_amount_makes_its_bill(owner: TestClient):
    home = _home(owner)
    out = owner.post("/api/v1/utilities", json={"home_id": home["id"], "service": "water", "billed": "submetered", "billing_company": "Example Billing Co", "starts_on": "2026-11-30"}).json()
    water = out["utilities"][0]
    assert water["billed_word"].startswith("measured at your unit") and water["known"] is False and water["bill_id"] is None
    over = owner.get("/api/v1/housing").json()
    assert "Water" in next(h for h in over["homes"] if h["id"] == home["id"])["missing"]
    out = owner.put(f"/api/v1/utilities/{water['id']}", json={**{k: water[k] for k in ("home_id", "service", "billed", "billing_company", "starts_on")}, "amount": 40.0, "due_day": 15, "account_last4": "acct 1234"}).json()
    water = out["utilities"][0]
    assert water["known"] and water["account_last4"] == "set"
    with Session(engine) as session:
        bill = session.get(Bill, water["bill_id"])
        assert bill.name == "Water · Example Billing Co" and bill.category == "Utilities" and bill.utility_id == water["id"]
        assert bill_dates(bill, TODAY, date(2026, 12, 31)) == [date(2026, 12, 15)]
    assert owner.post("/api/v1/utilities", json={"service": "plasma"}).status_code == 400
    owner.delete(f"/api/v1/utilities/{water['id']}")
    with Session(engine) as session:
        assert session.get(Bill, water["bill_id"]).active is False


def test_a_leases_terms_are_read_and_wait_for_the_owner(owner: TestClient):
    terms = terms_of([LEASE])
    assert terms["rent"]["value"] == 1250.0 and terms["pet_rent"]["value"] == 20.0 and terms["late_fee"]["value"] == 75.0
    assert terms["deposit"]["value"] == 300.0 and terms["notice_days"]["value"] == 60 and terms["due_day"]["value"] == 1 and terms["ends_on"]["value"] == "2027-03-31"
    home = _home(owner)
    up = owner.post("/api/v1/documents", files={"file": ("lease.txt", LEASE.encode(), "text/plain")}, data={"title": "Lease", "kind": "lease", "attach_to": f"home:{home['id']}"})
    assert up.status_code == 200, up.text
    doc = up.json()
    assert doc["pages"] == 1 and doc["has_text"] and doc["source"] == f"document:{doc['id']}"
    with Session(engine) as session:
        fact = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "document_terms")).one()
        assert fact.status == "pending" and json.loads(fact.fields)["attach_to"] == f"home:{home['id']}"
        fact_id = fact.id
    over = owner.get("/api/v1/housing").json()
    assert next(h for h in over["homes"] if h["id"] == home["id"])["charges"] == []  # nothing changes before the owner says so
    # A key holding the confirm scope cannot apply a document's terms
    key = owner.post("/api/v1/keys", json={"name": "helper", "scopes": ["read", "confirm"]}).json()["token"]
    refused = TestClient(owner.app).post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}, headers={"Authorization": f"Bearer {key}"})
    assert refused.status_code == 403
    assert owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}).status_code == 200
    home = next(h for h in owner.get("/api/v1/housing").json()["homes"] if h["id"] == home["id"])
    assert home["late_fee"] == 75.0 and home["notice_days"] == 60 and home["deposit"] == 300.0 and home["grace_days"] == 2  # late after the 3rd: two days past the 1st
    assert home["lease_ends_on"] == "2027-03-31" and home["ends_on"] is None  # the term ends; nobody said you move out, so rent goes on
    assert "To leave then, give written notice by Fri Jan 29, 2027" in home["lease_word"]
    assert {c["name"]: c["amount"] for c in home["charges"]} == {"Rent": 1250.0, "Pet rent": 20.0} and home["to_landlord_monthly"] == 1270.0
    assert home["documents"][0]["title"] == "Lease"


def test_documents_are_searched_read_and_kept_for_the_owner(owner: TestClient, live: TestClient):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Move-in"
    wb.active.append(["Charge", "Amount"])
    wb.active.append(["Example deposit", 300])
    raw = io.BytesIO()
    wb.save(raw)
    assert read_pages(raw.getvalue(), "xlsx")[0].splitlines()[:2] == ["Move-in", "Charge  Amount"]
    sheet = owner.post("/api/v1/documents", files={"file": ("move-in.xlsx", raw.getvalue(), "application/octet-stream")}, data={"kind": "charges"}).json()
    lease = owner.post("/api/v1/documents", files={"file": ("lease.txt", LEASE.encode(), "text/plain")}, data={"kind": "lease"}).json()
    assert owner.post("/api/v1/documents", files={"file": ("run.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert owner.post("/api/v1/documents", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert owner.post("/api/v1/documents", files={"file": ("a.txt", b"x", "text/plain")}, data={"attach_to": "../etc"}).status_code == 400
    hits = owner.get("/api/v1/documents/search", params={"q": "late fee"}).json()["hits"]
    assert hits[0]["document_id"] == lease["id"] and "$75.00" in hits[0]["snippet"]
    page = owner.get(f"/api/v1/documents/{lease['id']}/pages/1").json()
    assert page["of"] == 1 and page["text"] == LEASE and owner.get(f"/api/v1/documents/{lease['id']}/pages/2").status_code == 404
    got = owner.get(f"/api/v1/documents/{lease['id']}/file")
    assert got.status_code == 200 and got.content == LEASE.encode() and got.headers["cache-control"] == "no-store" and "attachment" in got.headers["content-disposition"]
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    bearer = {"Authorization": f"Bearer {key}"}
    assert TestClient(owner.app).get(f"/api/v1/documents/{lease['id']}/file", headers=bearer).status_code == 403  # the file itself: the owner only
    first = live.post("/mcp", headers={**bearer, **MCP_HEADERS}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
    sid = first.headers["mcp-session-id"]
    live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r = live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "document_read", "arguments": {"document_id": lease["id"]}}})
    assert "Untrusted message" in r.text and "60 days written notice" in r.text
    r = live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "documents_search", "arguments": {"query": "deposit"}}})
    assert "Untrusted message" in r.text and "Example deposit" in r.text
    assert owner.delete(f"/api/v1/documents/{sheet['id']}").json() == {"ok": True}
    assert [d["id"] for d in owner.get("/api/v1/documents").json()["documents"]] == [lease["id"]]


def test_a_bill_made_part_of_a_home_keeps_matching_the_payments_the_bank_shows(owner: TestClient):
    from app.engine.payments import match_payments
    from app.models import Account, Payment, Transaction

    with Session(engine) as session:
        account = Account(name="Example Checking", kind="checking", balance=2000.0)
        session.add(account)
        session.flush()
        bill = Bill(name="Example Property LLC", amount=1100.0, due_day=1, category="Rent", created_on=date(2026, 9, 1))
        session.add(bill)
        session.add(Transaction(account_id=account.id, date=date(2026, 10, 1), merchant="Example Property LLC", amount=1100.4, row_hash="t-rent-oct"))
        session.commit()
        bill_id = bill.id
    out = owner.post("/api/v1/housing/adopt", json={"bill_id": bill_id, "name": "Example Court 4"}).json()
    assert out["homes"][0]["payee"] == "Example Property LLC"
    with Session(engine) as session:
        bill = session.get(Bill, bill_id)
        assert bill.name == "Rent · Example Court 4" and bill.pays_to == "Example Property LLC"
        match_payments(session, TODAY)
        paid = session.exec(select(Payment).where(Payment.bill_id == bill_id, Payment.due_date == date(2026, 10, 1))).one()
        assert paid.status == "paid"  # matched by the property's name, not only by an exact amount


def test_a_utility_takes_a_bill_already_on_the_list_and_one_that_moves_with_you_is_not_missing(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Example Internet", amount=60.0, due_day=12, category="Internet"))
        session.commit()
    home = _home(owner)
    listed = owner.get("/api/v1/utilities").json()
    internet = next(b for b in listed["bills_to_link"] if b["name"] == "Example Internet")
    out = owner.post("/api/v1/utilities", json={"home_id": home["id"], "service": "internet", "provider": "Example Internet", "bill_id": internet["id"]}).json()
    u = out["utilities"][0]
    assert u["bill_id"] == internet["id"] and u["amount"] == 60.0 and u["due_day"] == 12
    assert all(b["id"] != internet["id"] for b in out["bills_to_link"])
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.active == True)).all()) == 1  # noqa: E712 - the same bill, not a second
        bill = session.get(Bill, internet["id"])
        assert bill.utility_id == u["id"] and bill.category == "Internet" and bill.pays_to == "Example Internet"
    owner.post("/api/v1/utilities", json={"home_id": home["id"], "service": "electric", "provider": "Example Power", "moves_with_you": True})
    h = next(x for x in owner.get("/api/v1/housing").json()["homes"] if x["id"] == home["id"])
    assert "Electricity" not in h["missing"] and next(x for x in h["utilities"] if x["service"] == "electric")["known"]


def test_a_cost_with_no_date_is_named_and_not_counted(owner: TestClient):
    home = _home(owner)
    owner.post(f"/api/v1/housing/{home['id']}/costs", json={"name": "Move-in check", "amount": 950.0, "due_on": "2026-11-29"})
    out = owner.post(f"/api/v1/housing/{home['id']}/costs", json={"name": "Application fee", "amount": 75.0}).json()
    h = next(x for x in out["homes"] if x["id"] == home["id"])
    assert h["costs_undated"] == ["Application fee"] and h["costs_counted"] == 950.0 and h["costs_total"] == 1025.0
    with Session(engine) as session:
        assert any(s["id"] == f"home-undated-{home['id']}" for s in next_steps(session, TODAY)["steps"])
