"""Sweep 5, the API edge: figures that cannot be negative are refused with a 400, not stored; a bill's paying
account is honoured on creation; a paycheck's cadence is checked before it can break detection; an investment
account is not made twice under one name; a plan keeps the APR typed; the move list reads its items in one go;
routes nothing called are gone; a flood of passkey challenges holds no memory and a locked address gets none."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth, factors
from app.db import engine
from app.engine.household import seed
from app.models import Account, Bill, Goal, Income, InstallmentPlan, MoveItem, RecurringSeries

H = {"X-Requested-With": "FinancialAuthority"}
TODAY = date(2026, 10, 14)


def _seeded(owner: TestClient) -> TestClient:
    with Session(engine) as s:
        seed(s)
    return owner


def test_a_bill_keeps_the_paying_account_it_was_made_with_and_refuses_negative_money(owner: TestClient):
    _seeded(owner)
    with Session(engine) as s:
        checking = s.exec(select(Account).where(Account.kind == "checking", Account.active == True)).first()
    made = owner.post("/api/v1/bills", json={"name": "Water", "amount": 40, "due_day": 9, "pay_from_account_id": checking.id})
    assert made.status_code == 200 and made.json()["pay_from_account_id"] == checking.id
    assert owner.post("/api/v1/bills", json={"name": "Water", "amount": 40, "due_day": 9, "pay_from_account_id": 999_999}).status_code == 400
    assert owner.post("/api/v1/bills", json={"name": "Gym", "amount": -25, "due_day": 1}).status_code == 400
    assert owner.post("/api/v1/bills", json={"name": "Gym", "amount": 25, "due_day": 1, "late_fee": -5}).status_code == 400
    assert owner.post("/api/v1/bills", json={"name": "Gym", "amount": 25, "due_day": 1, "kind": "loan"}).status_code == 400
    assert owner.put(f"/api/v1/bills/{made.json()['id']}", json={"amount": -40}).status_code == 400
    assert owner.put(f"/api/v1/bills/{made.json()['id']}", json={"grace_days": -3}).status_code == 400
    with Session(engine) as s:
        assert s.get(Bill, made.json()["id"]).amount == 40


def test_a_paychecks_cadence_and_pay_are_checked_before_they_can_break_detection(owner: TestClient):
    _seeded(owner)
    with Session(engine) as s:
        income = s.exec(select(Income).where(Income.active == True)).first()
    assert owner.put(f"/api/v1/incomes/{income.id}", json={"cadence": "fortnightly"}).status_code == 400
    assert owner.put(f"/api/v1/incomes/{income.id}", json={"expected_net": -1850}).status_code == 400
    assert owner.put(f"/api/v1/incomes/{income.id}", json={"expected_net": 0}).status_code == 400
    assert owner.put(f"/api/v1/incomes/{income.id}", json={"cadence": "monthly", "expected_net": 1850.25}).status_code == 200
    with Session(engine) as s:
        kept = s.get(Income, income.id)
        assert kept.cadence == "monthly" and kept.expected_net == 1850.25
    assert owner.post("/api/v1/recurring/detect").status_code == 200  # the cadence kept is one detection can step


def test_a_goals_figures_are_checked_on_change_as_on_creation(owner: TestClient):
    _seeded(owner)
    owner.post("/api/v1/goals", json={"name": "Trip", "target": 900})
    with Session(engine) as s:
        goal = s.exec(select(Goal).where(Goal.name == "Trip")).first()
    for bad in ({"target": -900}, {"target": 0}, {"pace": -50}, {"funded": -1}, {"priority": 0}):
        assert owner.put(f"/api/v1/goals/{goal.id}", json=bad).status_code == 400, bad
    assert owner.put(f"/api/v1/goals/{goal.id}", json={"pace": 50, "funded": 100}).status_code == 200
    with Session(engine) as s:
        kept = s.get(Goal, goal.id)
        assert (kept.target, kept.pace, kept.funded) == (900, 50, 100)


def test_an_investment_account_is_not_made_twice_under_one_name(owner: TestClient):
    assert owner.post("/api/v1/investments", json={"name": "Roth IRA", "subtype": "roth_ira", "balance": 1200}).status_code == 200
    assert owner.post("/api/v1/investments", json={"name": "roth ira", "balance": 50}).status_code == 409
    with Session(engine) as s:
        rows = s.exec(select(Account).where(Account.kind == "investment", Account.active == True)).all()
        assert len(rows) == 1 and rows[0].balance == 1200
    held = owner.post(f"/api/v1/investments/{rows[0].id}/holdings", json={"name": "Index fund", "ticker": "vti", "value": -5})
    assert held.status_code == 400


def test_a_plan_keeps_the_apr_typed_and_leaves_its_end_date_to_the_engine(owner: TestClient):
    _seeded(owner)
    with Session(engine) as s:
        series = RecurringSeries(merchant_key="afterpay", merchant="Afterpay", cadence="biweekly", typical_amount=35.0, latest_amount=35.0, charges_seen=2,
                                 first_seen=TODAY.replace(month=9, day=2), last_date=TODAY.replace(month=9, day=30), next_expected=TODAY, kind="unknown")
        s.add(series)
        s.commit()
        s.refresh(series)
    assert owner.post("/api/v1/borrowing/plan", json={"series_id": series.id, "remaining": 4, "apr": -1}).status_code == 400
    made = owner.post("/api/v1/borrowing/plan", json={"series_id": series.id, "remaining": 4, "apr": 12.5})
    assert made.status_code == 200
    with Session(engine) as s:
        plan = s.get(InstallmentPlan, made.json()["plan"]["id"])
        assert plan.apr == 12.5 and plan.remaining == 4
    loans = owner.get("/api/v1/borrowing").json()["loans"]
    assert made.json()["plan"]["ends"] == next(l["ends"] for l in loans if l["id"] == plan.id)  # the end date is the engine's, the one every screen shows


def test_the_move_list_counts_items_from_one_read(owner: TestClient):
    _seeded(owner)
    with Session(engine) as s:
        old = s.exec(select(Account).where(Account.kind == "checking")).first()
        new = Account(name="Example Credit Union", kind="checking", institution="Example CU", balance=50.0, connector="manual", external_id="manual:ecu:0001")
        s.add(new)
        s.commit()
        old_id, new_id = old.id, new.id
    started = owner.post("/api/v1/moves", json={"source_account_id": old_id, "target_account_id": new_id})
    assert started.status_code == 200
    with Session(engine) as s:  # a second, finished move with items of its own: the list must read all items once, not once per move
        from datetime import date as _date

        from app.models import AccountMove

        older = AccountMove(name="An earlier move", source_account_id=new_id, target_account_id=old_id, started_on=_date(2026, 1, 5), status="done")
        s.add(older)
        s.commit()
        s.add(MoveItem(move_id=older.id, ref="bill:1", kind="bill", name="Example Water", amount=40.0, decision="move", status="confirmed"))
        s.add(MoveItem(move_id=older.id, ref="bill:2", kind="bill", name="Example Gas", amount=30.0, decision="keep"))
        s.commit()
    from sqlalchemy import event

    statements: list[str] = []
    listener = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        listed = owner.get("/api/v1/moves").json()["moves"]
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    item_reads = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "move_items" in s]
    assert len(item_reads) == 1, item_reads  # one read for every move, not one a move
    assert len(listed) == 2
    newest = next(m for m in listed if m["status"] == "moving")
    with Session(engine) as s:
        moving = s.exec(select(MoveItem).where(MoveItem.move_id == newest["id"], MoveItem.decision == "move")).all()
    assert newest["moving"] == len(moving) > 0 and newest["confirmed"] == len([i for i in moving if i.status == "confirmed"])
    earlier = next(m for m in listed if m["status"] == "done")
    assert earlier["moving"] == 1 and earlier["confirmed"] == 1  # the kept item is not counted as moving


def test_routes_nothing_called_are_gone(owner: TestClient):
    assert owner.post("/api/v1/payments/match").status_code in (404, 405)  # 405: a payment's own route answers the path's shape
    assert owner.post("/api/v1/borrowing/sync").status_code in (404, 405)


def test_passkey_challenges_are_capped_and_a_locked_address_gets_none(owner: TestClient):
    factors._challenges.clear()
    try:
        keys = [factors._remember("login", b"c", "localhost") for _ in range(factors.CHALLENGES_MAX + 200)]
        assert len(factors._challenges) == factors.CHALLENGES_MAX
        assert keys[-1] in factors._challenges and keys[0] not in factors._challenges  # the newest stays, the oldest went
    finally:
        factors._challenges.clear()
    from app.main import app

    local = TestClient(app, base_url="http://localhost")
    for _ in range(auth.LOCK_AFTER):
        auth._record_failure("passkey:testclient")
    assert local.post("/api/auth/passkeys/login/options", headers=H).status_code == 429
    auth.reset_lockouts()
    assert local.post("/api/auth/passkeys/login/options", headers=H).status_code == 409  # unlocked: no passkey yet, as before
