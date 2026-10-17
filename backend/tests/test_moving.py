"""M43: the moving day plan, dated from what Housing holds, ticking itself where the app can see a step done, and
the notice letter leaving the app only as an email the owner approves."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import approvals
from app.db import engine
from app.engine.moving import moving_plan
from app.models import Account, Bill, Income

TODAY = date(2026, 10, 14)


def _move(owner: TestClient) -> tuple[int, int]:
    with Session(engine) as session:
        acct = Account(name="Example Checking", institution="Example Bank", kind="checking", balance=3000.0)
        session.add(acct)
        session.flush()
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1800.0, next_date=date(2026, 10, 16)))
        bill = Bill(name="Example Property LLC", amount=1100.0, due_day=1, category="Rent", pay_from_account_id=acct.id)
        session.add(bill)
        session.commit()
        bill_id, acct_id = bill.id, acct.id
    old = owner.post("/api/v1/housing/adopt", json={"bill_id": bill_id, "name": "Example Court 4"}).json()["homes"][0]
    owner.put(f"/api/v1/housing/{old['id']}", json={"name": "Example Court 4", "payee": "Example Property LLC", "ends_on": "2026-11-30", "notice_days": 30, "deposit": 400, "due_day": 1})
    out = owner.post("/api/v1/housing", json={"name": "Example Flats 12", "payee": "Example Flats", "starts_on": "2026-11-30", "first_due": "2027-01-01", "due_day": 1, "pay_from_account_id": acct_id}).json()
    new = next(h for h in out["homes"] if h["name"] == "Example Flats 12")
    owner.post(f"/api/v1/housing/{new['id']}/charges", json={"name": "Rent", "kind": "rent", "amount": 1350})
    owner.post(f"/api/v1/housing/{new['id']}/charges", json={"name": "Renter's insurance", "kind": "insurance", "pays_to": "other"})
    owner.post(f"/api/v1/housing/{new['id']}/costs", json={"name": "Move-in check", "amount": 1920, "due_on": "2026-11-28"})
    owner.post(f"/api/v1/housing/{new['id']}/costs", json={"name": "Truck and help", "due_on": "2026-11-30"})
    owner.post("/api/v1/utilities", json={"home_id": new["id"], "service": "electric", "provider": "Example Power", "moves_with_you": True})
    owner.post("/api/v1/utilities", json={"home_id": new["id"], "service": "water", "billed": "submetered", "billing_company": "Example Billing Co"})
    owner.post("/api/v1/utilities", json={"home_id": old["id"], "service": "other", "provider": "Example Submeter", "billed": "submetered", "billing_company": "Example Submeter"})
    return old["id"], new["id"]


def test_the_plan_is_dated_from_the_lease_and_the_costs(owner: TestClient):
    old_id, new_id = _move(owner)
    plan = owner.get("/api/v1/move/plan").json()
    by = {s["id"]: s for s in plan["steps"]}
    assert plan["move"] == {"from": "Example Court 4", "to": "Example Flats 12", "on": "2026-11-30", "days_left": 47}
    assert by["notice"]["date"] == "2026-10-30" and by["notice"]["state"] == "later" and by["notice"]["action"] == "letter"
    check = next(s for s in plan["steps"] if s["kind"] == "check")
    assert check["date"] == "2026-11-28" and "$1,920.00" in check["title"] and "from Example Checking" in check["words"]
    assert by["deposit-back"]["date"] == "2026-12-30" and "$400.00 held" in by["deposit-back"]["words"]
    assert any(s["title"] == "Tell Example Power you are moving" for s in plan["steps"])
    assert any(s["title"].startswith("Set up water for Example Flats 12") and "Example Billing Co" in s["words"] for s in plan["steps"])
    assert any(s["title"] == "The last bill from Example Submeter" for s in plan["steps"])
    assert any(s["title"] == "New address at Example Bank" for s in plan["steps"]) and any(s["title"] == "New address for Example Employer" for s in plan["steps"])
    assert by["first-rent"]["date"] == "2027-01-01" and "$1,350.00" in by["first-rent"]["title"]
    assert [s["date"] for s in plan["steps"] if s["date"]] == sorted(s["date"] for s in plan["steps"] if s["date"])
    assert "Notice to vacate, Example Court 4" in plan["letter"] and "Monday, November 30, 2026" in plan["letter"]


def test_steps_tick_themselves_or_by_hand(owner: TestClient):
    old_id, _ = _move(owner)
    owner.post(f"/api/v1/housing/{old_id}/notice", json={"given_on": "2026-10-15"})
    with Session(engine) as session:
        plan = moving_plan(session, TODAY)
    notice = next(s for s in plan["steps"] if s["id"] == "notice")
    assert notice["done"] and notice["done_by"] == "the app"
    plan = owner.post("/api/v1/move/plan/steps/address-mail", json={"done": True}).json()
    mail = next(s for s in plan["steps"] if s["id"] == "address-mail")
    assert mail["done"] and mail["done_by"] == "you"
    assert owner.post("/api/v1/move/plan/steps/not-a-step", json={"done": True}).status_code == 404


def test_the_notice_letter_leaves_only_as_an_email_you_approve(owner: TestClient, monkeypatch):
    monkeypatch.setattr(approvals.notify, "send", lambda *a, **k: {"ok": True})
    _move(owner)
    letter = owner.get("/api/v1/move/plan").json()["letter"]
    r = owner.post("/api/v1/move/plan/letter", json={"to": "office@example-court.test", "body": letter})
    assert r.status_code == 400 and "brackets" in r.json()["detail"]
    filled = letter.replace("[your new address]", "12 Example Way").replace("[your name]", "Sam Example").replace("[your phone number]", "555-0100")
    out = owner.post("/api/v1/move/plan/letter", json={"to": "office@example-court.test", "body": filled}).json()
    assert out["approval"]["status"] == "pending" and out["approval"]["subject"] == "Notice to vacate, Example Court 4" and "only when you approve" in out["said"]


def test_no_move_planned_says_how_to_start(owner: TestClient):
    plan = owner.get("/api/v1/move/plan").json()
    assert plan["move"] is None and plan["steps"] == [] and "Add your next home on Housing" in plan["note"]
