"""The second bug review's findings, each held by a test on the synthetic household or on rows made here."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models import Account, Advance, Bill, Debt, Income, InstallmentPlan, Payment, RecurringSeries, Transaction

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def _restore_points_left_as_found():
    """Restore points live beside the database, not in it: the ones a test here makes are removed after it, so the
    other files' counts of them still hold."""
    from app import restore

    before = set(restore.folder().glob("*"))
    yield
    for path in set(restore.folder().glob("*")) - before:
        path.unlink(missing_ok=True)


def _checking(session: Session, balance: float, name: str = "Checking") -> Account:
    account = Account(name=name, institution="Example Bank", kind="checking", balance=balance, connector="manual", external_id=f"manual:{name.lower()}")
    session.add(account)
    session.flush()
    return account


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Shopping", **flags) -> Transaction:
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, row_hash=f"t-{account.id}-{when}-{merchant}-{amount}", **flags)
    session.add(row)
    return row


# ---- High ---------------------------------------------------------------------------------------------------------


def test_a_bill_paid_today_or_early_is_not_counted_as_due_again():
    from app.engine.payments import match_payments
    from app.engine.snapshot import finance_snapshot

    today = date(2026, 10, 20)
    with Session(engine) as session:
        chk = _checking(session, 1000.0)
        card = Account(name="Visa", institution="Example Bank", kind="card", balance=17.0, connector="manual", external_id="manual:visa")
        session.add(card)
        session.flush()
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 30), account_id=chk.id))
        session.add(Bill(name="Electric Co", amount=150.0, due_day=20, autopay=True, pay_from_account_id=chk.id, category="Bills & Utilities"))
        session.add(Bill(name="Phone Co", amount=80.0, due_day=22, pay_from_account_id=chk.id, category="Bills & Utilities"))
        session.add(Bill(name="Streaming Co", kind="subscription", amount=17.0, due_day=20, autopay=True, pay_from_account_id=card.id))
        # Electric posted today, Phone two days early; both are out of the 1000 already. Streaming was charged to the card today.
        _tx(session, chk, today, "Electric Co", 150.0, "Bills & Utilities")
        _tx(session, chk, today - timedelta(days=1), "Phone Co", 80.0, "Bills & Utilities")
        _tx(session, card, today, "Streaming Co", 17.0, "Entertainment")
        session.commit()
        match_payments(session, today)
        snap = finance_snapshot(session, today)
    assert snap["due_before_payday"]["items"] == [] and snap["safe_to_spend"]["amount"] == 800.0
    assert {e["name"] for e in snap["board"]["paid"]["items"]} >= {"Electric Co", "Phone Co"}
    assert not any(e["name"] in ("Electric Co", "Phone Co") for e in snap["board"]["due_7"]["items"] + snap["board"]["later"]["items"])
    # The card already holds today's charge in its balance: its payment carries the balance, not the charge again.
    visa = next(e for e in snap["card_payments"] if e["source"] == f"account:{card.id}")
    assert visa["parts"]["bills"] == 0.0 and sum(e["parts"]["balance"] for e in snap["card_payments"] if e["source"] == f"account:{card.id}") == 17.0


def test_a_monthly_paycheck_on_the_31st_keeps_month_end(owner: TestClient):
    from app.engine.cadence import MONTH_END, next_unpaid_payday, pay_day_from, paydays_through
    from app.engine.snapshot import finance_snapshot
    from app.jobs.detect import run_detection

    assert pay_day_from(date(2026, 1, 31)) == MONTH_END and pay_day_from(date(2026, 4, 30)) == MONTH_END and pay_day_from(date(2026, 4, 15)) == 15
    assert paydays_through(date(2026, 2, 28), "monthly", date(2026, 5, 31), pay_day=MONTH_END) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]
    with Session(engine) as session:
        chk = _checking(session, 1000.0)
        income = Income(name="Employer", cadence="monthly", expected_net=3000.0, next_date=date(2026, 1, 31), account_id=chk.id)
        session.add(income)
        session.commit()
        assert [next_unpaid_payday(session, income, d) for d in (date(2026, 2, 27), date(2026, 3, 30), date(2026, 4, 15), date(2026, 8, 1))] == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 8, 31)]
        run_detection(session, date(2026, 3, 1))  # the date it saves never drifts to the 28th
        session.refresh(income)
        assert income.next_date == date(2026, 3, 31) and income.pay_day == MONTH_END
        run_detection(session, date(2026, 4, 1))
        session.refresh(income)
        assert income.next_date == date(2026, 4, 30)
        assert [p["date"] for p in finance_snapshot(session, date(2026, 4, 1))["paydays"]] == ["2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31"]
        income_id = income.id
    # The paycheck setup: a date in a short month keeps the day it already pays on; any other date sets the day.
    assert owner.put(f"/api/v1/incomes/{income_id}", json={"next_date": "2026-11-30"}).json()["ok"]
    with Session(engine) as session:
        assert session.get(Income, income_id).pay_day == MONTH_END
    owner.put(f"/api/v1/incomes/{income_id}", json={"next_date": "2026-11-20"})
    with Session(engine) as session:
        assert session.get(Income, income_id).pay_day == 20
    assert owner.put(f"/api/v1/incomes/{income_id}", json={"pay_day": 40}).status_code == 400


def test_a_monthly_paycheck_named_from_its_series_takes_its_day(owner: TestClient):
    from app.engine.cadence import MONTH_END
    from app.engine.recurring import detect

    rows = [{"date": d, "merchant": "EXAMPLE PAYROLL", "amount": -3000.0, "category": "Income"} for d in (date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30))]
    found = detect(rows, TODAY, income=True)
    assert found[0]["cadence"] == "monthly" and found[0]["pay_day"] == MONTH_END and found[0]["next_expected"] == date(2026, 10, 31)
    with Session(engine) as session:
        series = RecurringSeries(merchant_key="example payroll", merchant="EXAMPLE PAYROLL", cadence="monthly", typical_amount=3000.0, latest_amount=3000.0, first_seen=date(2026, 6, 30), last_date=date(2026, 9, 30), next_expected=date(2026, 10, 31), kind="income", suggested_kind="income")
        session.add(series)
        session.commit()
        series_id = series.id
    made = owner.post(f"/api/v1/recurring/{series_id}/classify", json={"kind": "income"}).json()
    with Session(engine) as session:
        assert session.get(Income, made["income"]["id"]).pay_day == MONTH_END


def _two_paychecks(session: Session, incomes: list[dict], balance: float = 1500.0) -> Account:
    chk = _checking(session, balance)
    for spec in incomes:
        session.add(Income(account_id=chk.id, **spec))
    session.add(Bill(name="Rent", amount=1200.0, due_day=1, pay_from_account_id=chk.id, category="Rent"))
    session.commit()
    return chk


def test_every_paycheck_is_on_the_plan_and_each_keeps_its_own_days():
    from app.engine import cadence
    from app.engine.decisions import date_move_check
    from app.engine.scenario import load_inputs
    from app.engine.snapshot import finance_snapshot

    assert not hasattr(cadence, "use_pay_days") and not hasattr(cadence, "_pay_days")  # no module-wide state
    today = date(2026, 10, 16)
    with Session(engine) as session:
        _two_paychecks(session, [dict(name="Main job", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 23)), dict(name="Side gig", cadence="monthly", expected_net=300.0, next_date=date(2026, 10, 20))])
        snap = finance_snapshot(session, today)
        inp = load_inputs(session, today)
        rent = session.exec(select(Bill)).one()
        moved = date_move_check(session, today, rent.id, 5)
    # The union of both, the side gig's day added to the main job's when both pay; the next check is the earliest.
    assert [(p["date"], p["amount"]) for p in snap["paydays"]] == [("2026-10-20", 300.0), ("2026-10-23", 2000.0), ("2026-11-06", 2000.0), ("2026-11-20", 2300.0)]
    assert snap["next_paycheck"]["date"] == "2026-10-20" and snap["next_paycheck"]["amount"] == 300.0
    assert snap["safe_to_spend"]["amount"] > 0  # was -200 when only the side gig's monthly checks were counted
    assert {e["source"] for e in snap["calendar"]["events"] if e["kind"] == "paycheck"} == {"income:1", "income:2"}
    assert inp["paydays"][:4] == [date(2026, 10, 20), date(2026, 10, 23), date(2026, 11, 6), date(2026, 11, 20)] and inp["net_for"](date(2026, 11, 20)) == 2300.0
    assert isinstance(moved["low_before"], float)  # the date-move check runs on the same schedule


def test_a_one_and_fifteen_paycheck_keeps_its_days_beside_another():
    from app.engine.snapshot import finance_snapshot

    today = date(2026, 10, 16)
    with Session(engine) as session:
        _two_paychecks(session, [dict(name="Job A", cadence="semimonthly", pay_days="1,15", expected_net=2000.0, next_date=date(2026, 10, 30)), dict(name="Job B", cadence="biweekly", expected_net=500.0, next_date=date(2026, 11, 6))])
        snap = finance_snapshot(session, today)
    job_a = [e["date"] for e in snap["calendar"]["events"] if e["kind"] == "paycheck" and e["source"] == "income:1"]
    assert job_a[:4] == ["2026-10-30", "2026-11-13", "2026-12-01", "2026-12-15"]  # the 1st-and-15th days (Nov 1 and 15 are Sundays), never Nov 30


def test_another_jobs_deposit_does_not_skip_this_paycheck():
    from app.engine.cadence import next_unpaid_payday

    with Session(engine) as session:
        chk = _checking(session, 500.0)
        other = _checking(session, 50.0, "Joint")
        series = RecurringSeries(merchant_key="main employer payroll", merchant="MAIN EMPLOYER PAYROLL", cadence="biweekly", typical_amount=2000.0, latest_amount=2000.0, first_seen=date(2026, 8, 7), last_date=date(2026, 10, 2), next_expected=date(2026, 10, 16), kind="income", confirmed=True)
        session.add(series)
        session.flush()
        main = Income(name="Main employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16), account_id=chk.id, series_id=series.id)
        side = Income(name="Side job", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=other.id)
        session.add_all([main, side])
        # A tax refund and the side job's check land near payday; neither is the main job's check.
        _tx(session, chk, date(2026, 10, 15), "State Tax Refund", -1800.0, "Income", is_income=True)
        _tx(session, other, date(2026, 10, 15), "Side Job Payroll", -1500.0, "Income", is_income=True)
        session.commit()
        assert next_unpaid_payday(session, main, date(2026, 10, 16)) == date(2026, 10, 16)
        assert next_unpaid_payday(session, side, date(2026, 10, 16)) == date(2026, 10, 30)  # its own account shows its check
        _tx(session, chk, date(2026, 10, 16), "Main Employer Payroll", -2000.0, "Income", is_income=True)
        session.commit()
        assert next_unpaid_payday(session, main, date(2026, 10, 16)) == date(2026, 10, 30)


def test_an_advance_stays_due_on_the_payday_it_was_taken_against():
    from app.engine.borrowing import sync_borrowing
    from app.engine.snapshot import finance_snapshot
    from app.jobs.detect import run_detection

    payday = date(2026, 10, 16)
    with Session(engine) as session:
        chk = _checking(session, 500.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=payday, account_id=chk.id))
        _tx(session, chk, date(2026, 10, 10), "Dave ExtraCash", -100.0, "Transfer")
        session.commit()
        sync_borrowing(session, TODAY)
        assert [a.repay_date for a in session.exec(select(Advance)).all()] == [payday]
        # Payday: the check posts and the jobs run; Dave has not taken its money back yet.
        _tx(session, chk, payday, "Employer Payroll", -2000.0, "Income", is_income=True)
        chk.balance = 2500.0
        session.add(chk)
        session.commit()
        run_detection(session, payday)
        sync_borrowing(session, payday)
        assert [a.repay_date for a in session.exec(select(Advance)).all()] == [payday]
        parts = finance_snapshot(session, payday)["safe_to_spend"]["parts"]
    assert parts["advances"] == 100.0 and parts["advances_on_payday"] == 0.0  # taken from today's money, not the next check


def test_a_pay_later_stream_queued_from_the_lenders_payments_survives_detection():
    from app.engine.borrowing import sync_borrowing
    from app.engine.snapshot import finance_snapshot
    from app.jobs.detect import run_detection

    today = date(2026, 10, 16)
    with Session(engine) as session:
        chk = _checking(session, 800.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 23), account_id=chk.id))
        for i, d in enumerate((date(2026, 9, 25), date(2026, 10, 9))):
            _tx(session, chk, d, f"AFFIRM PAYMENT {1000 + i}", 62.50, "Loan Payment")
        session.commit()
        for _ in range(2):  # the detect job twice: detection, then borrowing
            run_detection(session, today)
            sync_borrowing(session, today)
            rows = session.exec(select(RecurringSeries)).all()
            assert [(r.merchant_key, r.active, r.origin) for r in rows] == [("affirm", True, "borrowing")]
            window = finance_snapshot(session, today)["window"]["items"]
            assert [(e["date"], e["amount"]) for e in window if e.get("pending")] == [("2026-10-23", 62.5)]
        # Retired by an older version: the next run brings it back rather than leaving it out for good.
        rows[0].active = False
        session.add(rows[0])
        session.commit()
        sync_borrowing(session, today)
        assert [(r.active, r.origin) for r in session.exec(select(RecurringSeries)).all()] == [(True, "borrowing")]


def test_a_lenders_plan_and_its_payments_in_the_ledger_count_once():
    from app.engine.borrowing import plan_from_fields, sync_borrowing
    from app.engine.projection import pending_installment_events
    from app.engine.snapshot import finance_snapshot
    from app.jobs.detect import run_detection

    with Session(engine) as session:
        chk = _checking(session, 900.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16), account_id=chk.id))
        for d in (date(2026, 9, 2), date(2026, 9, 16), date(2026, 9, 30), date(2026, 10, 14)):
            _tx(session, chk, d, "AFFIRM PAYMENT", 50.0, "Loan Payment")
        session.commit()
        schedule = [{"date": (date(2026, 9, 2) + timedelta(days=14 * k)).isoformat(), "amount": 50.0} for k in range(8)]
        plan = plan_from_fields(session, {"provider": "Affirm", "merchant": "Couch Store", "loan_id": "L-1", "schedule": schedule}, None, None, TODAY)
        stream = RecurringSeries(merchant_key="affirm payment", merchant="AFFIRM PAYMENT", cadence="biweekly", typical_amount=50.0, latest_amount=50.0, first_seen=date(2026, 9, 2), last_date=date(2026, 10, 14), next_expected=date(2026, 10, 28), suggested_kind="installment")
        assert pending_installment_events([stream], TODAY, plans=[plan]) == []  # the plan's own payments, under the bank's name
        run_detection(session, TODAY)
        sync_borrowing(session, TODAY)
        run_detection(session, TODAY)
        series = session.exec(select(RecurringSeries).where(RecurringSeries.merchant_key == "affirm payment")).one()
        assert series.confirmed and series.kind == "installment"  # linked to the plan by lender and installment
        snap = finance_snapshot(session, TODAY)
    installments = [(e["date"], e["source"]) for e in snap["calendar"]["events"] if e["kind"] == "installment"]
    assert installments == [("2026-10-28", "plan:1"), ("2026-11-11", "plan:1"), ("2026-11-25", "plan:1"), ("2026-12-09", "plan:1")]


def test_a_card_payment_kept_as_a_bill_is_counted_once():
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 1500.0)
        card = Account(name="Visa", institution="Example Bank", kind="card", balance=600.0, connector="manual", external_id="manual:visa")
        session.add(card)
        session.flush()
        session.add(Debt(name="Visa", kind="card", balance=600.0, account_id=card.id))
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.commit()
        alone = finance_snapshot(session, TODAY)
        session.add(Bill(name="Visa payment", amount=600.0, due_day=25, category="Credit Card Payment", pay_from_account_id=chk.id))
        session.commit()
        both = finance_snapshot(session, TODAY)
    out = [round(s["windows"][0]["out"] + s["windows"][1]["out"], 2) for s in (alone, both)]
    assert out == [600.0, 600.0]  # the bill is the card's payment, not a second one
    assert [e["name"] for e in both["windows"][0]["bills"]["items"]] == ["Visa payment"]


def test_the_households_card_minimum_is_counted_once():
    from app.engine.cover import account_cover
    from app.engine.household import seed
    from app.engine.snapshot import finance_snapshot

    # The household keeps its card minimum as a bill: the Rewards Card's own minimum is not reserved beside it,
    # and Cover still names the bill as that card's next payment.
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
        rewards = next(a for a in account_cover(session, TODAY)["accounts"] if a["name"] == "Rewards Card")
    minimums = [(e["name"], e["date"], e["source"]) for e in snap["windows"][0]["bills"]["items"] if "minimum" in e["name"].lower()]
    assert minimums == [("Store Card minimum", "2026-10-20", "debt:3"), ("Card minimum", "2026-10-25", "bill:12")]
    assert rewards["headline"] == "Next payment $45.00 on Oct 25 (the minimum)"


# ---- Medium -------------------------------------------------------------------------------------------------------

BILLS_CSV = "Name,Amount,Frequency,Due day\nCity Water,64.00,Monthly,2\nCar Insurance,138.00,Monthly,3\n"


def test_a_bills_import_restore_point_holds_the_database_before_it():
    import sqlite3

    from app import importlog, restore
    from app.connectors.files import import_bills, parse_bills_rows
    from app.engine.payments import match_payments, payments_overview
    from app.models import ImportBatch

    with Session(engine) as session:
        made = import_bills(session, parse_bills_rows(BILLS_CSV), source_file="bills.csv", today=TODAY)
        batch = session.get(ImportBatch, made.details["import_id"])
        con = sqlite3.connect(str(restore.folder() / batch.restore_point))
        try:
            held = (con.execute("select count(*) from bills").fetchone()[0], con.execute("select count(*) from import_batches").fetchone()[0])
        finally:
            con.close()
        assert held == (0, 0)  # the point is the database before the import, not after it
        # Added today, the bills are not past due for the months before they existed; undone, they show nowhere.
        match_payments(session, TODAY)
        assert payments_overview(session, TODAY)["past_due"] == []
        importlog.undo(session, batch.id, TODAY)
        assert all(not payments_overview(session, TODAY)[k] for k in ("past_due", "late", "scheduled"))


def test_past_due_is_only_for_bills_on_the_register_and_only_since_they_were_added(owner: TestClient):
    from app.engine.payments import match_payments, payments_overview

    made = owner.post("/api/v1/bills", json={"name": "Example Gym", "amount": 30.0, "due_day": 5}).json()  # added on the 14th; the 5th has gone
    with Session(engine) as session:
        match_payments(session, TODAY)
        assert not any(p["bill_id"] == made["id"] for p in payments_overview(session, TODAY)["past_due"])
        old = Bill(name="Example Old Service", amount=20.0, due_day=3)  # from before the day was kept: its periods count
        session.add(old)
        session.commit()
        match_payments(session, TODAY)
        assert [p["due_date"] for p in payments_overview(session, TODAY)["past_due"] if p["bill_id"] == old.id] == ["2026-09-03", "2026-10-03"]
        old_id = old.id
    owner.put(f"/api/v1/bills/{old_id}", json={"active": False})
    with Session(engine) as session:
        assert session.exec(select(Payment).where(Payment.bill_id == old_id)).all()  # the rows are kept
        assert payments_overview(session, TODAY)["past_due"] == []  # but a bill off the register is not past due


def test_payday_holds_all_day_after_the_check_lands():
    from app.engine.cover import account_cover
    from app.engine.household import NET_PAY, seed
    from app.engine.reminders import due_today
    from app.engine.snapshot import finance_snapshot

    payday = date(2026, 10, 16)
    with Session(engine) as session:
        seed(session)
        chk = session.exec(select(Account).where(Account.kind == "checking")).first()
        session.add(Account(name="Savings", kind="savings", balance=100.0, connector="manual", external_id="manual:savings"))
        session.commit()
        before = [(r["kind"], r["amount"]) for r in account_cover(session, payday)["sheet"] if r["date"] == payday.isoformat()]
        # The check posts a day early.
        _tx(session, chk, payday - timedelta(days=1), "Example Employer Payroll", -NET_PAY, "Income", is_income=True)
        chk.balance = (chk.balance or 0.0) + NET_PAY
        session.add(chk)
        session.commit()
        snap = finance_snapshot(session, payday)
        reminders = [r for r in due_today(session, payday) if r["tag"] == "payday"]
        after = [(r["kind"], r["amount"]) for r in account_cover(session, payday)["sheet"] if r["date"] == payday.isoformat()]
    assert snap["next_paycheck"]["date"] == "2026-10-30"  # the plan counts the next check, not this one again
    assert snap["payday"]["today"] and snap["payday"]["landed"] and snap["payday"]["amount"] == NET_PAY
    assert [r["key"] for r in reminders] == ["payday:2026-10-16"] and reminders[0]["title"].endswith(" in")
    assert before and after == before  # the payday moves keep their day and their figures once the check is in


def test_the_payday_review_runs_after_the_check_lands():
    import json

    from app.db import session_scope
    from app.engine.household import NET_PAY, seed
    from app.llm import reviewer, roles
    from app.llm.providers import Event, Scripted

    payday = date(2026, 10, 16)
    with Session(engine) as session:
        seed(session)
        chk = session.exec(select(Account).where(Account.kind == "checking")).first()
        _tx(session, chk, payday, "Example Employer Payroll", -NET_PAY, "Income", is_income=True)
        session.commit()
    roles.set_override("reviewer", Scripted([[Event("text", text=json.dumps({"notes": [{"kind": "ok", "text": "Fine.", "action": {"type": "none"}}]})), Event("done")]]))
    try:
        assert reviewer.run_if_due(session_scope, payday).startswith("payday review: ok")
    finally:
        roles.set_override("reviewer", None)


def _mail(subject: str, sender: str, text: str) -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "owner@example.test", subject, "Fri, 09 Oct 2026 11:12:00 -0500"
    m["Message-ID"] = f"<{abs(hash(subject))}@example.test>"
    m.set_content(text)
    return bytes(m)


BILL_MAIL = _mail("Your statement is ready", "Electric Co <billing@electric.example>", "Amount due: $142.17\nDue date: October 20, 2026\n")
ALERT_MAIL = _mail("You've overdrawn your account", "Example Bank <alerts@bank.example>", "Your account ending in 1234 is overdrawn by $38.20 as of Oct 11.")
PAST_MAIL = _mail("Past due: your loan payment", "Credit Union <loans@cu.example>", "Your payment of $412.00 due October 28, 2026 was not received.")


class RenumberedBox:
    state = {"validity": 100, "messages": {500: BILL_MAIL, 501: ALERT_MAIL}, "fail": False}

    def __init__(self, *_a, **_k) -> None:
        pass

    def connect(self) -> None:
        pass

    def select_readonly(self, folder: str) -> tuple[int, int]:
        return len(self.state["messages"]), max(self.state["messages"]) + 1

    def uidvalidity(self) -> int:
        return self.state["validity"]

    def new_uids(self, since: int) -> list[int]:
        if self.state["fail"]:
            raise TimeoutError("connection dropped")
        return [u for u in sorted(self.state["messages"]) if u > since]

    def fetch_raw(self, uid: int) -> bytes:
        return self.state["messages"][uid]

    def close(self) -> None:
        pass


def test_a_renumbered_folder_is_read_once_and_a_failed_read_is_retried():
    from app import crypto
    from app.connectors.mail import MailConnector, uid_key
    from app.models import ExtractedFact, MailAccount, MessageRef
    from app.store import get_setting

    RenumberedBox.state = {"validity": 100, "messages": {500: BILL_MAIL, 501: ALERT_MAIL}, "fail": False}
    with Session(engine) as session:
        box = MailAccount(label="Box", kind="imap", host="imap.example", port=993, username="u", password_enc=crypto.encrypt("x"), folders="INBOX")
        session.add(box)
        session.commit()
        mail = MailConnector(client_factory=RenumberedBox, fetch=lambda _url: b"")
        mail.sync(session, today=TODAY)
        assert len(session.exec(select(ExtractedFact)).all()) == 2
        # Renumbered, a new message among them, and the connection drops at once: nothing about the folder moves.
        RenumberedBox.state = {"validity": 200, "messages": {1: BILL_MAIL, 2: ALERT_MAIL, 3: PAST_MAIL}, "fail": True}
        mail.sync(session, today=TODAY)
        assert get_setting(session, uid_key(box.id, "INBOX") + ".validity") == 100 and get_setting(session, uid_key(box.id, "INBOX")) == 501
        RenumberedBox.state["fail"] = False
        mail.sync(session, today=TODAY)
        facts = sorted(f.kind for f in session.exec(select(ExtractedFact)).all())
        assert facts == ["bank_alert", "bill_due", "past_due"]  # each message once, the new one read
        assert sorted(r.uid for r in session.exec(select(MessageRef)).all()) == [1, 2, 3]  # the references follow the new numbers
        assert get_setting(session, uid_key(box.id, "INBOX") + ".validity") == 200


def test_a_bank_linked_after_its_history_was_imported_adopts_it():
    from app.connectors.banks import upsert_account, upsert_transaction
    from app.connectors.rocketmoney import import_rows, parse_csv
    from app.engine.snapshot import usual_pace

    lines = ["Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible,Transaction Tags"]
    for i in range(20):
        d = TODAY - timedelta(days=2 + i * 2)
        lines.append(f"{d},{d},Cash,Everyday Checking,1234,Example Bank,EXAMPLE GROCERY,,{40 + i:.2f},POS,Groceries,,,,")
    with Session(engine) as session:
        import_rows(session, parse_csv("\n".join(lines) + "\n"), "rm.csv")
        pace = usual_pace(session, TODAY, set())["per_day"]
        account = upsert_account(session, provider="plaid", external_id="plaid-acc-1", name="Everyday Checking", institution="Example Bank", kind="checking", balance=900.0, last4="1234")
        for i in range(20):
            d = TODAY - timedelta(days=2 + i * 2) + timedelta(days=1)  # the bank dates each a day later: posted, not bought
            upsert_transaction(session, provider="plaid", tx_id=f"tx{i}", account=account, when=d, amount=40 + i, merchant="Example Grocery", raw_name="EXAMPLE GROCERY", category="Groceries")
        upsert_transaction(session, provider="plaid", tx_id="new", account=account, when=TODAY, amount=12.5, merchant="Example Coffee", raw_name="EXAMPLE COFFEE", category="Dining & Drinks")
        session.commit()
        accounts = session.exec(select(Account)).all()
        rows = session.exec(select(Transaction)).all()
        assert [(a.name, a.connector) for a in accounts] == [("Everyday Checking", "plaid")]
        assert len(rows) == 21 and all(t.source == "plaid" and t.import_id is None for t in rows)
        assert usual_pace(session, TODAY, set())["per_day"] == pace  # the same spending, counted once


def test_a_purchase_past_the_end_of_the_line_is_not_a_yes():
    from app.engine.decisions import purchase_check

    with Session(engine) as session:
        chk = _checking(session, 3000.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16), account_id=chk.id))
        session.commit()
        inside = purchase_check(session, TODAY, 1.0)
        later = purchase_check(session, TODAY, 1.0, on=TODAY + timedelta(days=70))
        spread = purchase_check(session, TODAY, 6.0, installments=6)  # the last of six, every fourteen days, is day 70
    assert inside["verdict"] == "yes" and inside["outside"] is None
    assert later["verdict"] == "outside" and later["outside"]["charges"] == [{"date": (TODAY + timedelta(days=70)).isoformat(), "amount": 1.0}]
    assert spread["verdict"] == "outside" and len(spread["outside"]["charges"]) == 1 and spread["outside"]["horizon"] == spread["horizon"]


def test_biweekly_fridays_stay_biweekly_and_pending_streams_keep_their_cadence():
    from app.engine.cadence import is_semimonthly
    from app.engine.projection import pending_installment_events
    from app.engine.recurring import detect

    misread = []
    for offset in range(0, 280, 7):
        last = date(2026, 10, 9) - timedelta(days=offset)
        for n in (4, 5, 6, 7):
            dates = [last - timedelta(days=14 * k) for k in range(n)][::-1]
            found = detect([{"date": d, "merchant": "ACME PAYROLL", "amount": -2000.0, "category": "Income"} for d in dates], last + timedelta(days=3), income=True, include_inactive=True)
            if not found or found[0]["cadence"] != "biweekly":
                misread.append(dates)
    assert misread == [] and not is_semimonthly([date(2026, 9, 4) + timedelta(days=14 * k) for k in range(6)])
    stream = RecurringSeries(id=7, merchant_key="affirm", merchant="Affirm", cadence="semimonthly", typical_amount=40.0, latest_amount=40.0, first_seen=date(2026, 8, 14), last_date=date(2026, 9, 30), next_expected=date(2026, 10, 15), suggested_kind="installment")
    quarterly = RecurringSeries(id=8, merchant_key="klarna", merchant="Klarna", cadence="quarterly", typical_amount=90.0, latest_amount=90.0, first_seen=date(2026, 4, 20), last_date=date(2026, 7, 20), next_expected=date(2026, 10, 20), suggested_kind="installment")
    days = [(e["source"], e["date"]) for e in pending_installment_events([stream, quarterly], TODAY, horizon_days=75)]
    assert days == [("series:7", date(2026, 10, 15)), ("series:7", date(2026, 10, 30)), ("series:7", date(2026, 11, 13)), ("series:7", date(2026, 11, 30)), ("series:7", date(2026, 12, 15)),
                    ("series:8", date(2026, 10, 20))]  # twice a month on its days; a quarter is one charge in the horizon, not three


def test_a_webhook_sync_runs_the_same_chain_as_the_scheduled_one(monkeypatch):
    from app.api import plaid_webhook
    from app.connectors.base import SyncResult
    from app.connectors.plaid import PlaidConnector
    from app.jobs import scheduler

    ran: list[str] = []
    monkeypatch.setattr(PlaidConnector, "sync", lambda self, session, link_id=None, **_: SyncResult(ok=True, summary="2 new", inserted=2))
    monkeypatch.setattr(scheduler, "detect_recurring", lambda: ran.append("detection, borrowing, payments") or "chain")
    assert plaid_webhook.sync_link(1) == "2 new · chain" and ran == ["detection, borrowing, payments"]


def test_a_pending_charge_the_bank_drops_leaves_the_ledger(owner: TestClient):
    import sys

    from app.connectors import CONNECTORS

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_banks import FakeTeller

    fake = FakeTeller()
    CONNECTORS["teller"].client_factory = fake.factory
    try:
        owner.put("/api/v1/banks/teller/settings", json={"application_id": "app_123", "environment": "development"})
        enrolled = owner.post("/api/v1/banks/teller/enroll", json={"access_token": "token_x", "enrollment_id": "enr_1", "institution": "Example Credit Union"}).json()
        with Session(engine) as session:
            assert session.exec(select(Transaction).where(Transaction.source_ref == "txn_2")).one().pending
        original = fake.factory

        def without_the_hold(access_token: str):  # noqa: ANN202 - an httpx client
            import httpx

            inner = original(access_token)

            def handler(request: httpx.Request) -> httpx.Response:
                got = inner.get(request.url.path, params=dict(request.url.params))
                if request.url.path == "/accounts/acc_1/transactions":
                    return httpx.Response(200, json=[t for t in got.json() if t["id"] != "txn_2"])
                return httpx.Response(got.status_code, json=got.json())

            return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.teller.io")

        CONNECTORS["teller"].client_factory = without_the_hold
        assert owner.post(f"/api/v1/banks/{enrolled['link']['id']}/sync").json()["ok"]
        with Session(engine) as session:
            assert session.exec(select(Transaction).where(Transaction.source_ref == "txn_2")).first() is None
            assert {t.source_ref for t in session.exec(select(Transaction)).all()} == {"txn_1", "txn_3", "txn_4"}  # posted rows stay
    finally:
        CONNECTORS["teller"].client_factory = None


def test_one_bad_file_in_the_import_folder_never_loses_the_others(monkeypatch, tmp_path):
    from app.config import settings
    from app.jobs import scheduler
    from app.store import get_setting

    monkeypatch.setattr(settings, "import_dir", tmp_path)
    (tmp_path / "a-bills.csv").write_text(BILLS_CSV)
    (tmp_path / "b-broken.csv").write_text(BILLS_CSV.replace("City Water", "Town Water").replace("Car Insurance", "Boat Insurance"))
    (tmp_path / "c-bills.csv").write_text(BILLS_CSV.replace("City Water", "Lake Water").replace("Car Insurance", "Home Insurance"))
    real = scheduler._import_table

    def table(session, path):  # noqa: ANN001, ANN202
        if path.name == "b-broken.csv":
            raise ValueError("the file could not be read")
        return real(session, path)

    monkeypatch.setattr(scheduler, "_import_table", table)
    with pytest.raises(RuntimeError, match="b-broken.csv"):
        scheduler.import_watch()
    with Session(engine) as session:
        assert sorted(name for name, _ in get_setting(session, "imports.processed", [])) == ["a-bills.csv", "c-bills.csv"]
        assert {b.name for b in session.exec(select(Bill)).all()} == {"City Water", "Car Insurance", "Lake Water", "Home Insurance"}
    monkeypatch.setattr(scheduler, "_import_table", real)
    assert "b-broken.csv" in scheduler.import_watch()  # tried again, and taken now; the others are not taken twice
    with Session(engine) as session:
        assert len(session.exec(select(Bill)).all()) == 6


def test_a_plan_named_after_the_bank_text_ticks_down_by_its_payments():
    from app.engine.borrowing import refresh_plan_progress
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 900.0)
        session.add(Income(name="Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 30), account_id=chk.id))
        session.add(InstallmentPlan(provider="AFFIRM PAYMENT", merchant="AFFIRM PAYMENT", total=300.0, installment=50.0, count=6, remaining=3, next_date=date(2026, 10, 20), cadence_days=14))
        _tx(session, chk, date(2026, 10, 20), "AFFIRM PAYMENT", 50.0, "Borrowing")
        session.commit()
        assert refresh_plan_progress(session, date(2026, 10, 20)) == 1
        plan = session.exec(select(InstallmentPlan)).one()
        assert (plan.remaining, plan.next_date) == (2, date(2026, 11, 3))
        assert finance_snapshot(session, date(2026, 10, 20))["due_before_payday"]["items"] == []


# ---- Low ----------------------------------------------------------------------------------------------------------


def test_every_window_adds_up_and_the_groups_count_a_card_bill_once():
    from app.engine.household import seed
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
    for w in snap["windows"]:
        assert round(w["inflow"] - w["spoken_for"], 2) == w["safe"]  # the buffer is out of what the first window carries in, once
    with Session(engine) as session:
        chk = session.exec(select(Account).where(Account.kind == "checking")).first()
        card = session.exec(select(Account).where(Account.name == "Rewards Card")).first()
        for name, amount, day in (("Music Co", 11.0, 20), ("Video Co", 17.0, 21)):
            session.add(Bill(name=name, kind="subscription", amount=amount, due_day=day, pay_from_account_id=card.id))
        session.commit()
        first = finance_snapshot(session, TODAY)["windows"][0]["bills"]
    assert chk is not None and not set(first["small"]["names"]) & {"Music Co", "Video Co"}  # they ride the card payment; the groups do not count them again


def test_trimming_manual_keys_runs_and_leaves_investments_be():
    from app.db import _trim_manual_keys

    with Session(engine) as session:
        session.add(Account(name="Old manual", kind="checking", connector="manual", external_id="manual:old manual:123456789012"))
        session.add(Account(name="Fidelity 401k", kind="investment", connector="manual", external_id="manual:fidelity 401k:2024"))
        session.add(Account(name="Typed", kind="checking", connector="manual", external_id="manual:typed"))
        session.commit()
    _trim_manual_keys()
    with Session(engine) as session:
        assert sorted(a.external_id for a in session.exec(select(Account)).all()) == ["manual:fidelity 401k:2024", "manual:old manual:9012", "manual:typed"]


def test_a_failing_reviewer_waits_longer_each_time():
    import json
    from datetime import datetime, timezone

    from app.db import session_scope
    from app.engine.household import seed
    from app.llm import reviewer, roles
    from app.llm.providers import Event, Scripted
    from app.models import ReviewRun

    with Session(engine) as session:
        seed(session)
        for hour in (6, 7, 9):  # three failures in a row, the last at 09:00
            session.add(ReviewRun(trigger="change", ok=False, error="ProviderError: down", created_at=datetime(2026, 10, 14, hour, tzinfo=timezone.utc)))
        session.commit()
        reviewer.mark_pending(session, "bill changed")
    roles.set_override("reviewer", Scripted([[Event("text", text=json.dumps({"notes": [{"kind": "ok", "text": "Fine.", "action": {"type": "none"}}]})), Event("done")]]))
    try:
        assert reviewer.run_if_due(session_scope, TODAY, now=datetime(2026, 10, 14, 12, 59, tzinfo=timezone.utc)).startswith("waiting")  # four hours after the third
        assert reviewer.run_if_due(session_scope, TODAY, now=datetime(2026, 10, 14, 13, 1, tzinfo=timezone.utc)).startswith("reviewed after a change: ok")
    finally:
        roles.set_override("reviewer", None)
    with Session(engine) as session:
        assert reviewer.backoff_until(session) is None
        for run in session.exec(select(ReviewRun)).all():
            session.delete(run)
        for k in range(8):
            session.add(ReviewRun(trigger="change", ok=False, created_at=datetime(2026, 10, 15, k, tzinfo=timezone.utc)))
        session.commit()
        assert reviewer.backoff_until(session) == datetime(2026, 10, 16, 7, tzinfo=timezone.utc)  # never more than a day


def test_the_calendar_pays_each_check_at_its_own_net():
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 1000.0)
        session.add(Income(name="Employer", cadence="semimonthly", expected_net=2000.0, expected_net_end=1800.0, next_date=date(2026, 10, 15), account_id=chk.id))
        session.commit()
        paydays = [(e["date"], e["amount"]) for e in finance_snapshot(session, TODAY)["calendar"]["events"] if e["kind"] == "paycheck"][:3]
    assert paydays == [("2026-10-15", -2000.0), ("2026-10-30", -1800.0), ("2026-11-13", -2000.0)]


def test_a_one_and_fifteen_payroll_that_posts_a_day_early_keeps_its_days():
    from app.engine.cadence import semimonthly_days_of

    # The 1st and the 15th (a weekend's moved to the Friday before), the Oct 1 check a day early: within two days of
    # the 15th-and-last days too, but closer to its own.
    early = [date(2026, 7, 1), date(2026, 7, 15), date(2026, 7, 31), date(2026, 8, 14), date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 30), date(2026, 10, 15)]
    assert semimonthly_days_of(early) == "1,15"
    assert semimonthly_days_of([date(2026, 7, 15), date(2026, 7, 31), date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]) == ""


def test_a_monthly_paycheck_releases_its_reserve_on_its_own_paydays():
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        chk = _checking(session, 2000.0)
        session.add(Income(name="Employer", cadence="monthly", expected_net=3000.0, next_date=date(2026, 10, 15), account_id=chk.id))
        session.add(Bill(name="Insurance", amount=1000.0, due_day=5, pay_from_account_id=chk.id, category="Insurance"))
        session.commit()
        snap = finance_snapshot(session, TODAY)
    # Only two paydays (Oct 15, Nov 15) fall inside the sixty days; the Oct 15 check keeps half of the Dec 5 bill,
    # which is held until that bill, not let go on a made-up date two weeks after Nov 15.
    line = {p["date"]: p["available"] for p in snap["projection"]}
    assert snap["set_aside"]["total"] == 500.0
    assert line["2026-11-29"] == line["2026-11-28"] and line["2026-12-04"] == line["2026-11-16"]


def test_a_folder_csv_lands_in_the_account_it_names(monkeypatch, tmp_path):
    from app.config import settings
    from app.jobs import scheduler

    monkeypatch.setattr(settings, "import_dir", tmp_path)
    with Session(engine) as session:
        everyday = _checking(session, 500.0, "Everyday Checking")
        session.commit()
        everyday_id = everyday.id
    (tmp_path / "everyday checking (1).csv").write_text("Date,Description,Amount\n10/12/2026,EXAMPLE GROCER,-20.00\n")
    (tmp_path / "export.csv").write_text("Date,Description,Amount,Account Number\n10/13/2026,EXAMPLE FUEL,-30.00,XXXX9876\n")
    scheduler.import_watch()
    with Session(engine) as session:
        grocer = session.exec(select(Transaction).where(Transaction.merchant == "EXAMPLE GROCER")).one()
        fuel = session.exec(select(Transaction).where(Transaction.merchant == "EXAMPLE FUEL")).one()
        made = session.get(Account, fuel.account_id)
        assert grocer.account_id == everyday_id and len(session.exec(select(Account)).all()) == 2
    (tmp_path / "statement.csv").write_text("Date,Description,Amount,Account Number\n10/14/2026,EXAMPLE PHARMACY,-8.00,9876\n")
    scheduler.import_watch()
    with Session(engine) as session:
        assert session.exec(select(Transaction).where(Transaction.merchant == "EXAMPLE PHARMACY")).one().account_id == made.id  # by its last four


def test_undoing_an_import_takes_its_advances_with_it(owner: TestClient):
    import io

    from app.engine.borrowing import sync_borrowing

    statement = "Date,Description,Amount\n10/10/2026,EARNIN ACTIVEHOURS,100.00\n10/11/2026,EXAMPLE GROCER,-20.00\n"
    made = owner.post("/api/v1/imports/file", data={"account_name": "Checking"}, files={"file": ("oct.csv", io.BytesIO(statement.encode()), "text/plain")}).json()
    with Session(engine) as session:
        sync_borrowing(session, TODAY)
        assert [(a.provider, a.amount) for a in session.exec(select(Advance)).all()] == [("Earnin", 100.0)]
    owner.post(f"/api/v1/imports/{made['import_id']}/undo")
    with Session(engine) as session:
        assert session.exec(select(Advance)).all() == []


def test_one_transaction_settles_one_planned_payment_by_name_then_date():
    from app.engine.planned import settle_planned
    from app.models import PlannedPayment

    with Session(engine) as session:
        chk = _checking(session, 900.0)
        power = PlannedPayment(name="Northern Power", amount=80.0, date=date(2026, 10, 12), kind="bill")
        water = PlannedPayment(name="Lakeside Water", amount=80.0, date=date(2026, 10, 13), kind="bill")
        session.add_all([power, water])
        _tx(session, chk, date(2026, 10, 12), "Corner Grocer", 80.0, "Groceries")
        _tx(session, chk, date(2026, 10, 13), "Northern Power", 80.0, "Bills & Utilities")
        session.commit()
        assert settle_planned(session, TODAY) == 2
        by_name = {p.name: session.get(Transaction, p.transaction_id).merchant for p in session.exec(select(PlannedPayment)).all()}
    assert by_name == {"Northern Power": "Northern Power", "Lakeside Water": "Corner Grocer"}  # the named one first; each transaction once


def test_date_suggestions_move_monthly_bills_only():
    from app.engine.decisions import billing_date_suggestions, date_move_check
    from app.engine.household import seed

    with Session(engine) as session:
        seed(session)
        for b in session.exec(select(Bill)).all():
            b.cadence, b.anchor_date = "quarterly", date(2026, 11, b.due_day)
            session.add(b)
        session.commit()
        out = billing_date_suggestions(session, TODAY)
        rent = session.exec(select(Bill).where(Bill.name == "Car insurance")).one()
        check = date_move_check(session, TODAY, rent.id, 9)
    assert out["movable"] == 0 and out["suggestions"] == [] and out["plan"] == []
    assert "of each month" not in check["draft"] and "each quarter" in check["draft"]
