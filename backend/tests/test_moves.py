"""Switching banks on the synthetic household: everything on the old account is found; nothing switches before
the paycheck is in the new account; the money through the move adds up; a switch moves the register behind it;
the ledger confirms a switch and catches one that still lands on the old account; the API and the alerts."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.guide import one_thing
from app.engine.household import seed
from app.engine.moves import LEAD_DAYS, move_plan, set_item, start_move, verify
from app.engine.snapshot import finance_snapshot
from app.models import Account, Bill, Income, MoveItem, Transaction

TODAY = date(2026, 10, 14)


def _setup(session: Session) -> tuple[Account, Account]:
    seed(session)
    old = session.exec(select(Account).where(Account.kind == "checking")).first()
    new = Account(name="Example Credit Union", kind="checking", institution="Example CU", balance=50.0, connector="manual")
    session.add(new)
    session.commit()
    session.refresh(new)
    return old, new


def _txn(session: Session, account: Account, when: date, merchant: str, amount: float, **flags) -> None:
    session.add(Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant, amount=amount, category=flags.pop("category", "Bills & Utilities"), source="manual",
                            row_hash=f"t-{account.id}-{when}-{merchant}-{amount}", **flags))
    session.commit()


def test_everything_on_the_old_account_is_found_and_ordered_safely():
    with Session(engine) as session:
        old, new = _setup(session)
        move = start_move(session, TODAY, source_id=old.id, target_id=new.id, target_name="", finish_by=date(2027, 1, 31))
        plan = move_plan(session, move, TODAY)
        bills_on_old = session.exec(select(Bill).where(Bill.active == True, Bill.pay_from_account_id == old.id)).all()  # noqa: E712
        assert all(b.pay_from_account_id for b in session.exec(select(Bill).where(Bill.active == True)).all())  # noqa: E712
    items = {i["ref"]: i for ph in plan["phases"] for i in ph["items"]}
    assert any(r.startswith("income:") for r in items) and {f"bill:{b.id}" for b in bills_on_old} <= set(items)
    assert {r for r in items if r.startswith("card:")} == {f"card:{a}" for a in (2, 3)}
    advances = [i for i in items.values() if i["kind"] == "advance"]
    assert advances and all(i["decision"] == "drop" for i in advances)
    first_new = date.fromisoformat(plan["first_new_payday"])
    for i in items.values():
        if i["kind"] in ("bill", "subscription", "card_payment") and i["switch_by"]:
            after, by, eff = (date.fromisoformat(i[k]) for k in ("switch_after", "switch_by", "effective"))
            # nothing switches before the paycheck is in the new account, and each switch clears its next charge
            assert first_new <= after <= by and (eff - by).days == LEAD_DAYS, i
    rent = next(i for i in items.values() if i["name"] == "Rent")
    assert rent["effective"] > plan["first_new_payday"]  # the rent due right after the first new paycheck stays put one more month


def test_the_money_through_the_move_adds_up():
    with Session(engine) as session:
        old, new = _setup(session)
        move = start_move(session, TODAY, source_id=old.id, target_id=new.id, target_name="", finish_by=None)
        for item in session.exec(select(MoveItem).where(MoveItem.kind == "advance")).all():
            set_item(session, move, item, TODAY, decision="move")
        moved = move_plan(session, move, TODAY)
        for item in session.exec(select(MoveItem)).all():
            set_item(session, move, item, TODAY, decision="keep")
        kept = move_plan(session, move, TODAY)
    for a, b in zip(moved["windows"], kept["windows"]):
        assert round(a["source"]["out"] + a["target"]["out"], 2) == round(b["source"]["out"] + b["target"]["out"], 2)
        assert round(a["source"]["in"] + a["target"]["in"], 2) == round(b["source"]["in"] + b["target"]["in"], 2)
    assert all(w["target"]["out"] == 0 for w in kept["windows"][1:] if w["paycheck_in"] == "source")
    # once the paycheck lands in the new account, the old one is sent what still charges there
    assert any(m["to"] == "Everyday Checking" for w in moved["windows"] for m in w["move_money"])


def test_a_switch_moves_the_register_and_the_ledger_confirms_or_catches_it():
    with Session(engine) as session:
        old, new = _setup(session)
        move = start_move(session, TODAY, source_id=old.id, target_id=new.id, target_name="", finish_by=None)
        phone = session.exec(select(MoveItem).where(MoveItem.name == "Phone")).one()
        bill_id = int(phone.ref.split(":")[1])
        set_item(session, move, phone, TODAY, status="switched")
        assert session.get(Bill, bill_id).pay_from_account_id == new.id
        set_item(session, move, phone, TODAY, status="todo")
        assert session.get(Bill, bill_id).pay_from_account_id == old.id
        set_item(session, move, phone, TODAY, status="switched")
        _txn(session, new, TODAY + timedelta(days=8), "Phone", 96.0)
        assert verify(session, move, TODAY + timedelta(days=9))["confirmed"] == 1
        session.refresh(phone)
        assert phone.status == "confirmed" and phone.confirmed_on == TODAY + timedelta(days=8)
        # a charge still landing on the old account after the switch is caught
        _txn(session, old, TODAY + timedelta(days=20), "Phone", 96.0)
        verify(session, move, TODAY + timedelta(days=21))
        plan = move_plan(session, move, TODAY + timedelta(days=21))
        snap = finance_snapshot(session, TODAY + timedelta(days=21))
        thing = one_thing(session, TODAY + timedelta(days=21), snap=snap)
    assert any(a["severity"] == "crit" and a["text"].startswith("Phone still charged") for a in plan["alerts"])
    assert thing["thing"]["source"].startswith("move:") or any(o["source"].startswith("move:") for o in thing["others"])


def test_the_paycheck_landing_in_the_new_account_frees_the_bills():
    with Session(engine) as session:
        old, new = _setup(session)
        move = start_move(session, TODAY, source_id=old.id, target_id=new.id, target_name="", finish_by=None)
        _txn(session, new, TODAY, "Example Employer", -2240.0, is_income=True, category="Income")
        verify(session, move, TODAY)
        inc = session.exec(select(MoveItem).where(MoveItem.kind == "income")).one()
        assert inc.status == "confirmed"
        assert session.exec(select(Income)).first().account_id == new.id
        plan = move_plan(session, move, TODAY)
    items = [i for ph in plan["phases"] for i in ph["items"] if i["kind"] == "bill" and i["switch_after"]]
    assert min(i["switch_after"] for i in items) == TODAY.isoformat()


def test_the_move_over_the_api(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    listed = owner.get("/api/v1/moves").json()
    checking = next(a for a in listed["accounts"] if a["kind"] == "checking")
    plan = owner.post("/api/v1/moves", json={"source_account_id": checking["id"], "target_name": "Example Credit Union", "finish_by": "2027-01-31"}).json()
    mid = plan["move"]["id"]
    assert plan["target"]["in_app"] is False and plan["phases"][0]["steps"][0]["done"] is False
    assert owner.post("/api/v1/moves", json={"source_account_id": checking["id"], "target_name": "Again"}).status_code == 409
    steps = owner.get("/api/v1/next_steps").json()["steps"]
    assert any(s["id"] == f"move-target-{mid}" for s in steps)
    made = owner.post("/api/v1/accounts", json={"name": "Example Credit Union", "kind": "checking", "last4": "0001", "balance": 100}).json()
    plan = owner.put(f"/api/v1/moves/{mid}", json={"target_account_id": made["id"], "debit_card_on": "2026-10-20"}).json()
    assert plan["target"]["in_app"] and plan["move"]["debit_card_on"] == "2026-10-20"
    item = next(i for ph in plan["phases"] for i in ph["items"] if i["kind"] == "subscription")
    after = owner.put(f"/api/v1/moves/{mid}/items/{item['id']}", json={"status": "switched"}).json()
    assert next(i for ph in after["phases"] for i in ph["items"] if i["id"] == item["id"])["status"] == "switched"
    assert owner.put(f"/api/v1/moves/{mid}/items/{item['id']}", json={"status": "confirmed"}).status_code == 400
    assert owner.post(f"/api/v1/moves/{mid}/refresh").json()["added"] == 0
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"]}).json()["token"]
    reader = TestClient(client.app)
    assert reader.get(f"/api/v1/moves/{mid}", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert reader.put(f"/api/v1/moves/{mid}/items/{item['id']}", json={"status": "todo"}, headers={"Authorization": f"Bearer {key}"}).status_code == 403


def test_a_payday_today_opens_the_first_window_with_its_paycheck():
    payday = date(2026, 10, 16)
    with Session(engine) as session:
        old, new = _setup(session)
        move = start_move(session, payday, source_id=old.id, target_id=new.id, target_name="", finish_by=None)
        plan = move_plan(session, move, payday)
    starts = [w["start"] for w in plan["windows"]]
    assert len(starts) == len(set(starts)) and starts[0] == payday.isoformat()
    assert plan["windows"][0]["paycheck_in"] == "source" and plan["windows"][0]["source"]["in"] == 2240.0
