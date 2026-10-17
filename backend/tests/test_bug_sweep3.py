"""The third bug review's money-engine findings (BUGENG) and the engine side of the screen findings (UIA, UIB), each
held by a test on the synthetic household or on rows made here."""

import json
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import generate, seed
from app.models import (
    Account,
    Bill,
    ExtractedFact,
    Home,
    HomeCost,
    Income,
    InstallmentPlan,
    Payment,
    Paystub,
    RecurringSeries,
    Transaction,
    Utility,
)

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def _restore_points_left_as_found():
    """Restore points live beside the database, not in it: the ones a test here makes are removed after it."""
    from app import restore

    before = set(restore.folder().glob("*"))
    yield
    for path in set(restore.folder().glob("*")) - before:
        path.unlink(missing_ok=True)


def _checking(session: Session, balance: float, name: str = "Checking", **extra) -> Account:
    account = Account(name=name, institution="Example Bank", kind="checking", balance=balance, connector="manual", external_id=f"manual:{name.lower()}", **extra)
    session.add(account)
    session.flush()
    return account


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Shopping", **flags) -> Transaction:
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, row_hash=f"t-{account.id}-{when}-{merchant}-{amount}", **flags)
    session.add(row)
    return row


def _household(session: Session) -> None:
    seed(session, generate(TODAY))


def _home(owner: TestClient, name: str, **fields) -> int:
    out = owner.post("/api/v1/housing", json={"name": name, "kind": "rent", "due_day": 1, **fields})
    assert out.status_code == 200, out.text
    return next(h["id"] for h in out.json()["homes"] if h["name"] == name)


def _bill_dates(name_start: str, start: date, end: date) -> list[str]:
    from app.engine.billdates import bill_dates

    with Session(engine) as session:
        rows = [b for b in session.exec(select(Bill).where(Bill.active == True)).all() if b.name.startswith(name_start)]
        return sorted(d.isoformat() for b in rows for d in bill_dates(b, start, end))


# ---- BUGENG-01 ------------------------------------------------------------------------------------------------------


def test_a_bill_due_yesterday_that_has_not_posted_is_still_counted():
    from app.engine.payments import match_payments
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 2000.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 30), account_id=chk.id))
        session.add(Bill(name="Rent", amount=800.0, due_day=17, autopay=True, category="Rent", created_on=date(2026, 1, 1), pay_from_account_id=chk.id))
        _tx(session, chk, date(2026, 9, 17), "Rent", 800.0, "Rent")
        session.commit()
        safe = {}
        for day in (date(2026, 10, 17), date(2026, 10, 18)):  # due Saturday; the autopay posts Monday
            match_payments(session, day)
            snap = finance_snapshot(session, day)
            safe[day] = snap["safe_to_spend"]["amount"]
        assert safe[date(2026, 10, 17)] == safe[date(2026, 10, 18)] == 1000.0
        rent = [e for e in snap["due_before_payday"]["items"] if e["name"] == "Rent"]
        assert len(rent) == 1 and rent[0]["late"] and rent[0]["due"] == "2026-10-17" and rent[0]["date"] == "2026-10-18"
        # Monday the payment posts; even before the matching job runs, it is out of checking and counted once.
        chk.balance = 1200.0
        session.add(chk)
        _tx(session, chk, date(2026, 10, 19), "Rent", 800.0, "Rent")
        session.commit()
        monday = finance_snapshot(session, date(2026, 10, 19))
    assert monday["safe_to_spend"]["amount"] == 1000.0
    assert not any(e["name"] == "Rent" for e in monday["due_before_payday"]["items"])


# ---- BUGENG-02 and BUGENG-08 ----------------------------------------------------------------------------------------


def test_adding_the_next_home_ends_the_one_lived_in_and_utilities_follow_their_homes(owner: TestClient):
    with Session(engine) as session:
        chk = _checking(session, 3000.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2200.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.commit()
    old = _home(owner, "Old Place", payee="Old Property")
    assert owner.post(f"/api/v1/housing/{old}/charges", json={"name": "Rent", "kind": "rent", "amount": 1200}).status_code == 200
    assert owner.post("/api/v1/utilities", json={"home_id": old, "service": "electric", "provider": "Old Power Co", "billed": "direct", "amount": 90, "due_day": 12}).status_code == 200
    added = owner.post("/api/v1/housing", json={"name": "New Place", "kind": "rent", "payee": "New Property", "starts_on": "2026-12-01", "first_due": "2026-12-01", "due_day": 1}).json()
    assert added["said"] and "Old Place now ends on Tue Dec 1, 2026" in added["said"][0]
    new = next(h["id"] for h in added["homes"] if h["name"] == "New Place")
    owner.post(f"/api/v1/housing/{new}/charges", json={"name": "Rent", "kind": "rent", "amount": 1500})
    owner.post("/api/v1/utilities", json={"home_id": new, "service": "electric", "provider": "New Power Co", "billed": "direct", "amount": 110, "due_day": 10})
    end = date(2027, 1, 31)
    assert _bill_dates("Rent · Old Place", TODAY, end) == ["2026-11-01"]
    assert _bill_dates("Rent · New Place", TODAY, end) == ["2026-12-01", "2027-01-01"]
    assert _bill_dates("Electricity · Old Power Co", TODAY, end) == ["2026-11-12"]
    assert _bill_dates("Electricity · New Power Co", TODAY, end) == ["2026-12-10", "2027-01-10"]
    rents = _bill_dates("Rent ·", TODAY, end)
    assert [d[:7] for d in rents] == ["2026-11", "2026-12", "2027-01"]  # one rent a month across the move
    assert owner.get("/api/v1/housing").json()["move"]["old_ends_on"] == "2026-12-01"
    # The move called off: the old home goes on, and the removed home's utility stops with it (BUGENG-08).
    gone = owner.delete(f"/api/v1/housing/{new}").json()
    assert gone["said"] and "Old Place no longer ends" in gone["said"][0]
    assert _bill_dates("Rent · Old Place", TODAY, end) == ["2026-11-01", "2026-12-01", "2027-01-01"]
    assert _bill_dates("Electricity · New Power Co", TODAY, end) == [] and _bill_dates("Rent · New Place", TODAY, end) == []
    with Session(engine) as session:
        assert not session.exec(select(Utility).where(Utility.home_id == new)).one().active


def test_removing_a_home_stops_its_utilities_bills(owner: TestClient):
    home = _home(owner, "Only Place")
    owner.post(f"/api/v1/housing/{home}/charges", json={"name": "Rent", "kind": "rent", "amount": 1200})
    owner.post("/api/v1/utilities", json={"home_id": home, "service": "electric", "provider": "Power Co", "billed": "direct", "amount": 120, "due_day": 12})
    assert _bill_dates("Electricity", TODAY, TODAY + timedelta(days=60))
    out = owner.delete(f"/api/v1/housing/{home}").json()
    assert out["homes"] == [] and out["utilities_unassigned"] == []
    with Session(engine) as session:
        assert [b.name for b in session.exec(select(Bill).where(Bill.active == True)).all()] == []


# ---- BUGENG-03 ------------------------------------------------------------------------------------------------------


def test_a_due_day_move_starts_next_cycle_and_never_drops_a_charge():
    from app.engine.decisions import billing_date_suggestions, date_move_check
    from app.engine.projection import build_events

    judge = date(2026, 12, 11)  # the last payday inside the sixty days: Oct 16, Oct 30, Nov 13, Nov 27, Dec 11
    with Session(engine) as session:
        _household(session)
        bills = [b for b in session.exec(select(Bill).where(Bill.active == True)).all() if b.movable and (b.cadence or "monthly") == "monthly"]
        assert bills
        for b in bills:
            old = [e["date"] for e in build_events([b], [], [], TODAY)]
            for day in range(1, 29):
                if day == b.due_day:
                    continue
                moved = [e["date"] for e in build_events([b], [], [], TODAY, due_days={b.id: day}, judge_until=judge)]
                assert moved[0] == old[0], (b.name, day)  # this cycle's bill is owed on its old day
                before, after = sum(1 for d in old if d < judge), sum(1 for d in moved if d < judge)
                # Never a charge fewer before the last payday; one more only when a later charge is pulled in.
                assert before <= after <= before + 1, (b.name, day, old, moved)
        streaming = next(b for b in bills if b.name == "Streaming")
        check = date_move_check(session, TODAY, streaming.id, 13)
        assert check["change"] < streaming.amount - 0.005  # moving to the 13th does not "save" this month's charge
        result = billing_date_suggestions(session, TODAY)
    suggestions = result["suggestions"]
    assert not any(abs(s["improvement"] - s["amount"]) < 0.005 for s in suggestions)
    assert not (suggestions and all(s["to_day"] == TODAY.day - 1 for s in suggestions))
    if not suggestions:  # the lowest point (Oct 29) is before any change could start: said, not implied to be the best dates
        assert result["note"] and "Thu Oct 29" in result["note"]


# ---- BUGENG-04 ------------------------------------------------------------------------------------------------------


def test_a_rent_bill_already_listed_is_the_homes_rent_not_a_second_one(owner: TestClient):
    with Session(engine) as session:
        rent = Bill(name="RENT PAYMENT MAPLE", amount=1295.0, due_day=1, category="Rent", created_on=date(2026, 1, 1))
        session.add(rent)
        session.commit()
        rent_id = rent.id
    home = _home(owner, "Maple St")
    out = owner.post(f"/api/v1/housing/{home}/charges", json={"name": "Rent", "kind": "rent", "amount": 1295}).json()
    assert out["rent_bills_to_adopt"] == [] and out["warnings"] == []
    with Session(engine) as session:
        rents = [b for b in session.exec(select(Bill).where(Bill.active == True)).all() if (b.category or "").lower() == "rent"]
        assert [b.id for b in rents] == [rent_id] and rents[0].name == "Rent · Maple St" and rents[0].home_id == home
        assert "RENT PAYMENT MAPLE" in json.loads(rents[0].match_names)  # its payments keep matching under the bank's name


def test_a_different_rent_bill_beside_a_home_is_said_and_can_be_made_the_homes(owner: TestClient):
    with Session(engine) as session:
        rent = Bill(name="Rent", amount=1000.0, due_day=1, category="Rent", created_on=date(2026, 1, 1))
        session.add(rent)
        session.commit()
        rent_id = rent.id
    home = _home(owner, "Maple St")
    out = owner.post(f"/api/v1/housing/{home}/charges", json={"name": "Rent", "kind": "rent", "amount": 1295}).json()
    assert [b["id"] for b in out["rent_bills_to_adopt"]] == [rent_id]
    assert out["warnings"] and "counted twice" in out["warnings"][0]
    steps = owner.get("/api/v1/next_steps").json()["steps"]
    assert any(s["id"] == f"home-adopt-{rent_id}" and s["severity"] == "warning" for s in steps)
    out = owner.post("/api/v1/housing/adopt", json={"bill_id": rent_id, "home_id": home}).json()
    assert out["rent_bills_to_adopt"] == [] and out["warnings"] == []
    with Session(engine) as session:
        rents = [b for b in session.exec(select(Bill).where(Bill.active == True)).all() if (b.category or "").lower() == "rent"]
        assert [b.id for b in rents] == [rent_id] and rents[0].amount == 1295.0


# ---- BUGENG-06 ------------------------------------------------------------------------------------------------------


def test_a_yearly_bill_needs_its_next_due_date(owner: TestClient):
    from app.engine.billdates import bill_dates
    from app.engine.snapshot import finance_snapshot

    assert bill_dates(Bill(name="Car insurance", amount=600.0, cadence="yearly", due_day=15), TODAY, TODAY + timedelta(days=400)) == []
    refused = owner.post("/api/v1/bills", json={"name": "Car insurance", "amount": 600, "due_day": 15, "cadence": "yearly"})
    assert refused.status_code == 400 and "next due date" in refused.json()["detail"]
    with Session(engine) as session:
        chk = _checking(session, 1000.0)
        _tx(session, chk, date(2025, 11, 14), "Car insurance", 600.0, "Insurance")
        session.add(Bill(name="Water quarterly", amount=90.0, cadence="quarterly", due_day=5))  # as an old import left it
        session.commit()
        caveats = finance_snapshot(session, TODAY)["caveats"]
    assert any("no next due date" in c for c in caveats)
    made = owner.post("/api/v1/bills", json={"name": "Car insurance", "amount": 600, "due_day": 15, "cadence": "yearly"})
    assert made.status_code == 200 and made.json()["anchor_date"] == "2026-11-14"  # its last payment, a year on
    assert _bill_dates("Car insurance", TODAY, TODAY + timedelta(days=365)) == ["2026-11-15"]


# ---- BUGENG-07 ------------------------------------------------------------------------------------------------------


def test_a_bill_linked_to_a_utility_keeps_matching_under_the_banks_name(owner: TestClient):
    from app.engine.payments import match_payments

    with Session(engine) as session:
        chk = _checking(session, 2000.0)
        bill = Bill(name="CPL ENERGY PMT", amount=120.0, amount_mode="estimate", due_day=20, category="Bills & Utilities", created_on=date(2026, 1, 1))
        session.add(bill)
        _tx(session, chk, date(2026, 9, 21), "CPL ENERGY PMT", 108.75, "Bills & Utilities")
        session.commit()
        bill_id = bill.id
    assert owner.post("/api/v1/utilities", json={"service": "electric", "provider": "City Power and Light", "billed": "direct", "bill_id": bill_id}).status_code == 200
    with Session(engine) as session:
        chk = session.exec(select(Account)).first()
        assert session.get(Bill, bill_id).name == "Electricity · City Power and Light"
        _tx(session, chk, date(2026, 10, 20), "CPL ENERGY PMT", 97.10, "Bills & Utilities")
        session.commit()
        match_payments(session, date(2026, 10, 30))
        paid = session.exec(select(Payment).where(Payment.bill_id == bill_id, Payment.due_date == date(2026, 10, 20))).one()
    assert paid.status == "paid"


# ---- BUGENG-09 ------------------------------------------------------------------------------------------------------


def test_the_move_check_reaches_a_move_past_the_usual_horizon():
    from app.engine import housing as hs

    with Session(engine) as session:
        chk = _checking(session, 1500.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1000.0, next_date=date(2026, 10, 16), account_id=chk.id))
        home = Home(name="Far Place", starts_on=TODAY + timedelta(days=200), due_day=1)
        session.add(home)
        session.commit()
        session.add(HomeCost(home_id=home.id, name="Move-in check", amount=50000.0, due_on=home.starts_on))
        session.commit()
        hs.sync_bills(session, home, TODAY)
        out = hs.move_check(session, TODAY, home.id)
    assert out["path"][-1]["date"] >= (home.starts_on + timedelta(days=31)).isoformat()
    assert out["holds"] is False and out["status"] == "crit" and out["word"].startswith("Short")
    assert "balance" not in out["word"]  # UIB-09: the figure is what is left to spend, not a bank balance


# ---- BUGENG-10 ------------------------------------------------------------------------------------------------------


def test_bills_a_month_leaves_out_one_time_costs_and_leases_not_started(owner: TestClient):
    with Session(engine) as session:
        _household(session)
    before = owner.get("/api/v1/bills").json()
    home = _home(owner, "Next Place", starts_on="2027-02-01")
    owner.post(f"/api/v1/housing/{home}/charges", json={"name": "Rent", "kind": "rent", "amount": 1500})
    owner.post(f"/api/v1/housing/{home}/costs", json={"name": "Move-in check", "amount": 3000, "due_on": "2027-02-01"})
    owner.post(f"/api/v1/housing/{home}/costs", json={"name": "Truck", "amount": 400, "due_on": "2027-01-31"})
    after = owner.get("/api/v1/bills").json()
    assert after["monthly_total"] == before["monthly_total"]
    assert after["one_time"]["total"] == 3400.0 and after["starting_later"]["total"] == 1500.0
    assert after["monthly_sources"] and all(s.startswith("bill:") for s in after["monthly_sources"])


# ---- BUGENG-11 ------------------------------------------------------------------------------------------------------


def test_the_shared_page_counts_bills_as_the_plan_pays_them():
    from app.engine.payments import match_payments
    from app.engine.shared import shared_view

    with Session(engine) as session:
        chk = _checking(session, 2500.0, name="Joint Checking", shared=True)
        card = Account(name="Joint Card", kind="card", connector="manual", external_id="m:card", balance=-40.0, shared=True)
        session.add(card)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=1800.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.add(Bill(name="Daycare", amount=900.0, due_day=20, created_on=date(2026, 1, 1)))  # no paying account: paid where the paycheck lands
        session.add(Bill(name="Water", amount=60.0, due_day=18, created_on=date(2026, 1, 1), pay_from_account_id=chk.id))
        _tx(session, chk, date(2026, 10, 13), "Water", 60.0, "Bills & Utilities")  # paid early
        session.commit()
        match_payments(session, TODAY)
        view = shared_view(session, TODAY)
    assert [(b["name"], b["date"]) for b in view["bills"]] == [("Daycare", "2026-10-20")]
    assert view["cards_owed"] == 0.0


# ---- BUGENG-12 ------------------------------------------------------------------------------------------------------


def test_a_pay_later_plan_every_two_weeks_is_paid_off_by_its_own_last_payment():
    from app.engine.payoff import payoff_plan
    from app.engine.reports import debt_mountain

    with Session(engine) as session:
        _household(session)
        laptop = session.exec(select(InstallmentPlan).where(InstallmentPlan.merchant == "Laptop plan")).one()
        mountain = debt_mountain(session, TODAY)
        plan = payoff_plan(session, TODAY, extra=0.0)
    row = next(d for d in mountain["items"] if d["source"] == f"plan:{laptop.id}")
    assert row["installment"] == 38.0 and row["minimum"] == round(38.0 * 30 / 14, 2) and row["every_days"] == 14
    last = laptop.next_date + timedelta(days=14 * (laptop.remaining - 1))  # Nov 24
    paid_on = next(d for d in plan["minimums_only"]["debts"] if d["source"] == f"plan:{laptop.id}")["paid_on"]
    assert paid_on <= (last.replace(day=1) + timedelta(days=31)).replace(day=1).isoformat()  # the month after its last payment, not months later


# ---- BUGENG-13 ------------------------------------------------------------------------------------------------------


def test_the_needs_you_count_counts_every_fact_waiting():
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        _checking(session, 500.0)
        for i in range(45):
            session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"amount": 10 + i}), status="pending"))
        session.commit()
        snap = finance_snapshot(session, TODAY)
    assert snap["pending"]["facts"] == 45 and len(snap["mail"]["pending"]) == 30


# ---- BUGENG-14 ------------------------------------------------------------------------------------------------------


def test_borrowing_returns_the_affirm_statement_not_a_cards_date():
    from app.engine.borrowing import borrowing_overview
    from app.store import set_setting

    statement = {"period_start": "2026-09-01", "period_end": "2026-09-30", "interest_period": 4.12, "interest_ytd": 31.0, "total_closing": 812.0}
    with Session(engine) as session:
        _household(session)
        set_setting(session, "affirm", statement)
        out = borrowing_overview(session, TODAY)
    assert out["statement"] == statement
    assert all(c["next_statement"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["next_statement"]) for c in out["credit"])


# ---- BUGENG-15 ------------------------------------------------------------------------------------------------------


def test_housing_bills_are_made_on_the_owners_day(owner: TestClient):
    assert owner.post("/api/v1/utilities", json={"service": "water", "provider": "City Water", "billed": "direct", "amount": 40, "due_day": 14}).status_code == 200
    home = _home(owner, "Maple St")
    owner.post(f"/api/v1/housing/{home}/charges", json={"name": "Rent", "kind": "rent", "amount": 1200})
    with Session(engine) as session:
        made = session.exec(select(Bill)).all()
    assert len(made) == 2 and {b.created_on for b in made} == {TODAY}  # the app's today, not the server's clock


# ---- UIB-01 ---------------------------------------------------------------------------------------------------------


def test_a_paycheck_can_be_added_by_hand_and_with_words_when_refused(owner: TestClient):
    steps = owner.get("/api/v1/next_steps").json()["steps"]
    assert any(s["id"] == "paycheck-none" and s["link"] == "/paystubs#paycheck" for s in steps)
    base = {"name": "Example Employer", "cadence": "biweekly", "expected_net": 1850, "next_date": "2026-10-30"}
    for bad, word in (({"cadence": "fortnightly"}, "How often"), ({"expected_net": None}, "take-home"), ({"expected_net": "abc"}, "take-home"),
                      ({"next_date": "2026-10-01"}, "today or later"), ({"next_date": "10/30/2026"}, "like 2026-10-30"), ({"cadence": "semimonthly", "pay_days": "5,20"}, "Twice a month")):
        r = owner.post("/api/v1/incomes", json={**base, **bad})
        assert r.status_code == 400 and word in r.json()["detail"], (bad, r.text)
    made = owner.post("/api/v1/incomes", json=base)
    assert made.status_code == 200
    listed = owner.get("/api/v1/incomes").json()["incomes"]
    assert listed == [made.json()] and made.json()["next_date"] == "2026-10-30" and made.json()["expected_net"] == 1850.0
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["next_paycheck"]["known"] and snap["next_paycheck"]["date"] == "2026-10-30"
    assert not any(s["id"] == "paycheck-none" for s in owner.get("/api/v1/next_steps").json()["steps"])
    monthly = owner.post("/api/v1/incomes", json={**base, "name": "Side job", "cadence": "monthly", "next_date": "2026-10-31", "pay_day": 31}).json()
    assert monthly["pay_day"] == 31 and monthly["pay_days"] == ""
    semi = owner.post("/api/v1/incomes", json={**base, "name": "Third job", "cadence": "semimonthly", "next_date": "2026-11-01", "pay_days": "1,15"}).json()
    assert semi["pay_days"] == "1,15" and semi["pay_day"] == 0


def test_a_paystub_can_become_the_paycheck(owner: TestClient):
    with Session(engine) as session:
        stub = Paystub(employer="Example Employer", pay_date=date(2026, 10, 2), net=1850.0, stub_hash="bug-sweep-3-stub")
        session.add(stub)
        session.commit()
        stub_id = stub.id
    assert owner.post("/api/v1/incomes/from-paystub/99999", json={}).status_code == 404
    made = owner.post(f"/api/v1/incomes/from-paystub/{stub_id}", json={"cadence": "biweekly"}).json()
    assert made["name"] == "Example Employer" and made["expected_net"] == 1850.0 and made["next_date"] == "2026-10-16" and made["cadence"] == "biweekly"
    assert owner.get("/api/v1/incomes").json()["incomes"] == [made]


# ---- UIA-02 ---------------------------------------------------------------------------------------------------------


def test_the_home_debts_total_is_the_debts_pages_total(owner: TestClient):
    with Session(engine) as session:
        _household(session)
    snap = owner.get("/api/v1/snapshot").json()
    debts = owner.get("/api/v1/debts").json()
    assert snap["debts_total"] == debts["total"] == owner.get("/api/v1/accounts/overview").json()["debts"]["total"]
    assert {d["kind"] for d in snap["debts"]} >= {"card", "loan", "bnpl", "advance"} and snap["debts_words"]
    assert snap["debts_cards_loans_total"] < snap["debts_total"]


# ---- UIA-03 ---------------------------------------------------------------------------------------------------------


def test_an_installment_needs_the_payments_left(owner: TestClient):
    with Session(engine) as session:
        s = RecurringSeries(merchant_key="example storage unit", merchant="Example Storage Unit", category="Shopping", cadence="monthly", typical_amount=89.0, latest_amount=89.0,
                            charges_seen=3, first_seen=date(2026, 7, 12), last_date=date(2026, 10, 12), next_expected=date(2026, 11, 12))
        session.add(s)
        session.commit()
        sid = s.id
    for body in ({"kind": "installment"}, {"kind": "installment", "remaining": 0}, {"kind": "installment", "remaining": -2}):
        r = owner.post(f"/api/v1/recurring/{sid}/classify", json=body)
        assert r.status_code == 400 and "payments are left" in r.json()["detail"]
    ok = owner.post(f"/api/v1/recurring/{sid}/classify", json={"kind": "installment", "remaining": 4}).json()
    assert ok["installment_plan"]["remaining"] == 4


# ---- UIA-04 ---------------------------------------------------------------------------------------------------------


def test_an_unanchored_bill_can_still_be_removed_and_edited(owner: TestClient):
    with Session(engine) as session:
        old = Bill(name="Water quarterly", amount=90.0, cadence="quarterly", due_day=5)  # left without a date by an old import
        session.add(old)
        session.commit()
        bill_id = old.id
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"amount": 95}).status_code == 200
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"cadence": "quarterly"}).status_code == 400  # touching how often it comes asks for the date
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"anchor_date": "2026-12-05"}).json()["anchor_date"] == "2026-12-05"
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"active": False}).status_code == 200


def test_paid_from_offers_only_the_checks_the_schedule_has(owner: TestClient):
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 3000.0)
        income = Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16), account_id=chk.id)
        bill = Bill(name="Phone", amount=80.0, due_day=19, pay_from_account_id=chk.id, created_on=date(2026, 1, 1))
        session.add_all([income, bill])
        session.commit()
        income_id, bill_id = income.id, bill.id
    listed = owner.get("/api/v1/bills").json()
    assert [c["value"] for c in listed["paid_from_choices"]] == ["due"]
    refused = owner.put(f"/api/v1/bills/{bill_id}", json={"paid_from": "mid"})
    assert refused.status_code == 400 and "twice a month" in refused.json()["detail"]
    # A bill left on "mid" from before is paid from the check before it is due: no 15th check exists.
    with Session(engine) as session:
        b = session.get(Bill, bill_id)
        b.paid_from = "mid"
        session.add(b)
        session.commit()
        snap = finance_snapshot(session, TODAY)
    assert any(e["name"] == "Phone" for e in snap["window"]["items"]) and not any(e["name"] == "Phone" for e in snap["due_before_payday"]["items"])
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"paid_from": "mid", "amount": 85}).status_code == 200  # unchanged, still saves
    assert owner.put(f"/api/v1/incomes/{income_id}", json={"cadence": "semimonthly", "pay_days": "1,15", "next_date": "2026-10-15"}).status_code == 200
    choices = owner.get("/api/v1/bills").json()["paid_from_choices"]
    assert [(c["value"], c["words"]) for c in choices] == [("due", "the check before it is due"), ("end", "the 1st check"), ("mid", "the 15th check")]
    assert choices[1]["next"] == "2026-10-30" and choices[2]["next"] == "2026-10-15"  # Sun Nov 1 is paid Fri Oct 30
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"paid_from": "end"}).status_code == 200


# ---- UIA-05 ---------------------------------------------------------------------------------------------------------


def test_borrowing_owes_the_advances_the_plan_subtracts(owner: TestClient):
    with Session(engine) as session:
        _household(session)
    snap = owner.get("/api/v1/snapshot").json()
    borrowing = owner.get("/api/v1/borrowing").json()
    assert borrowing["outstanding_total"] == snap["advances"]["total"] == snap["borrowing"]["outstanding_total"] == 231.0
    assert len(borrowing["outstanding"]) == snap["borrowing"]["outstanding"] == 2
    assert all(o["source"].startswith("advance:") and o["amount"] == round(o["advanced"] + o["fee"], 2) for o in borrowing["outstanding"])


# ---- UIA-08 / BUGIO-14 ----------------------------------------------------------------------------------------------


def test_a_bills_paying_account_can_be_cleared(owner: TestClient):
    with Session(engine) as session:
        _checking(session, 1000.0)
        card = Account(name="Rewards Card", kind="card", connector="manual", external_id="m:rewards", balance=100.0)
        session.add(card)
        session.flush()
        bill = Bill(name="Phone", amount=80.0, due_day=19, pay_from_account_id=card.id, created_on=date(2026, 1, 1))
        session.add(bill)
        session.commit()
        bill_id, card_id = bill.id, card.id
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"amount": 81, "autopay": None}).json()["pay_from_account_id"] == card_id  # left out or null: unchanged
    cleared = owner.put(f"/api/v1/bills/{bill_id}", json={"pay_from_account_id": None})
    assert cleared.status_code == 200 and cleared.json()["pay_from_account_id"] is None
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"amount": 82}).json()["pay_from_account_id"] is None
    assert owner.put(f"/api/v1/bills/{bill_id}", json={"pay_from_account_id": 99999}).status_code == 400


# ---- UIA-21 ---------------------------------------------------------------------------------------------------------


def test_readiness_says_dates_in_words_on_the_owners_day(monkeypatch: pytest.MonkeyPatch):
    from app.engine.readiness import readiness
    from app.store import set_setting

    monkeypatch.setenv("TZ", "America/Chicago")
    with Session(engine) as session:
        _household(session)
        set_setting(session, "reconciled", {"at": "2026-10-15T03:00:00+00:00"})  # 10 pm on Oct 14 in Chicago
        out = readiness(session, TODAY, move_date=date(2026, 12, 1), safe_to_spend=100.0)
    checked = next(i for i in out["items"] if i["key"] == "reconciled")
    assert checked["checked_on"] == "2026-10-14" and checked["value"] == "checked on Wed Oct 14"
    cards = [i for i in out["items"] if i["key"].startswith("card:")]
    assert cards and all(i["statement_on"] and not i["note"].startswith(";") for i in cards)
    for i in out["items"]:
        words = " ".join(str(i[k]) for k in ("label", "value", "note", "target"))
        assert not re.search(r"\b(line|window|windows|register|registers)\b", words), words
        assert not re.search(r"(?<![\d-])\d{4}-\d{2}-\d{2}(?![\d-])", words), words  # no raw dates in what is said


# ---- UIB-09 ---------------------------------------------------------------------------------------------------------


def test_the_at_pace_figure_has_a_plain_name():
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        _household(session)
        snap = finance_snapshot(session, TODAY)
    words = snap["projection_words"]
    assert words["at_pace_short"] == "left to spend" and "safety cushion" in words["at_pace"] and words["cash"] == "in checking"
