"""Sweep 21: every documented claim about the engine's behaviour, tested.

README.md, docs/FEATURES.md, docs/PLAN.md sections 2, 5, 7 and 8, and the done items of docs/ROADMAP.md (with their
dated notes) promise specific behaviour. Each test below quotes one claim and the file and line it is written on, and
fails if the claim is false. Four groups: A, the jobs, the data check, the self-check and the restore points; B, the
plan, the checks and the guides; C, borrowing, payments, planned payments and imports; D, facts from mail, the brief,
paystubs, cadences, files, the outlook, exports and the business and taxes. Where a claim failed, either the engine
was fixed (a one-line comment says which claim) or the document's wording was corrected in place.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select, SQLModel

from app import crypto
from app.connectors import CONNECTORS
from app.connectors.base import SyncResult
from app.db import engine
from app.engine import apphealth, datacheck, borrowing_plan as bp
from app.engine.advance_exit import exit_plan
from app.engine.billdates import bill_dates
from app.engine.borrowing import BORROWING_CATEGORY, apply_lender_notice, mark_installment_paid, older_than_listing, pair_advances, plan_from_fields, refresh_plan_progress, sync_borrowing
from app.engine.budgets import budget_plan, save_budget
from app.engine.cover import account_cover
from app.engine.credit import credit_health
from app.engine.decisions import billing_date_suggestions, date_move_check, purchase_check
from app.engine.guide import one_thing
from app.engine.household import seed
from app.engine.learning import answer, bring_back, rank, weight
from app.engine.needsyou import answer_payment, plan_many
from app.engine.pairs import decide, find_pairs
from app.engine.paychecks import paycheck_views
from app.engine.payments import match_payments
from app.engine.payoff import payoff_plan
from app.engine.planned import planned_events, planned_to_cards, settle_planned
from app.engine.readiness import readiness
from app.engine.recap import recap, record_plan
from app.engine.saving import saving_plan
from app.engine.scenario import run_scenario
from app.engine.snapshot import finance_snapshot, usual_pace
from app.engine.watchlist import watchlist
from app.jobs import scheduler
from app.jobs.scheduler import jobs
from app.llm.citations import engine_numbers, unverified
from app.models import Account, BankLink, Bill, ExtractedFact, Home, JobRun, MailAccount, MessageRef, Payment, Transaction, Advance, Debt, Goal, HomeCharge, Income, InstallmentPlan, MoveItem, RecurringSeries, PlannedPayment, TransactionPair, Approval, Business, Document, Paystub, Trip
from app.store import get_setting, set_setting

from tests.test_sweep15_bugs_undo import household, tables
from tests.test_affirm import CONFIRMATION


# === Group A: the jobs, the data check, the self-check and the restore points =======================================

TODAY = date(2026, 10, 14)


def _a_checking(session: Session, balance: float = 1500.0) -> Account:
    a = Account(name="Example Checking", kind="checking", balance=balance)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _a_row(session: Session, account: Account, on: date, name: str, amount: float, *, source: str = "manual", tag: str = "") -> Transaction:
    t = Transaction(account_id=account.id, date=on, merchant=name, raw_name=name.upper(), amount=amount, category="Bills & Utilities", source=source,
                    row_hash=f"{name}-{on.isoformat()}-{amount}-{source}-{tag}")
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _a_registers() -> dict[str, list]:
    """What the plan counts: bills, rows, facts, payments; compared before and after a job."""
    with Session(engine) as s:
        return {
            "bills": sorted((b.id, b.name, b.amount, b.active) for b in s.exec(select(Bill)).all()),
            "rows": sorted((t.id, t.amount, t.is_ignored) for t in s.exec(select(Transaction)).all()),
            "facts": sorted((f.id, f.status) for f in s.exec(select(ExtractedFact)).all()),
            "payments": sorted((p.bill_id, p.due_date, p.status, p.transaction_id) for p in s.exec(select(Payment)).all()),
        }


def _a_mailbox(session: Session, label: str) -> MailAccount:
    m = MailAccount(label=label, kind="imap", host="mail.example.test", port=993, username=f"{label}@example.test", password_enc="", folders="INBOX")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


# --- the data check ----------------------------------------------------------------------------------------------


def test_a_the_data_check_runs_on_its_own_every_six_hours():
    """"The data check runs on its own every six hours" (README.md:101); "A job every six hours (and Check again)"
    (docs/ROADMAP.md:286)."""
    assert jobs.specs["data_check"].seconds == 6 * 3600


def test_a_the_data_check_uses_no_ai_model(monkeypatch):
    """"The data check runs on its own every six hours, with no AI model." (README.md:101). With every outward call
    refused, the job still runs over the household; nothing in its modules reaches a model."""
    import inspect
    import re

    import httpx

    def refuse(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("the data check reached the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    household()
    summary = scheduler.data_check()
    assert "finding" in summary
    model = re.compile(r"from \.+llm|app\.llm|import llm")
    assert not model.search(inspect.getsource(datacheck)) and not model.search(inspect.getsource(scheduler.data_check))


def test_a_the_data_check_finds_doubles_alike_bills_and_one_purchase_from_two_sources():
    """"It looks for anything counted twice, bills that look alike, the same purchase arriving from two sources"
    (README.md:101; docs/ROADMAP.md:286)."""
    with Session(engine) as s:
        chk = _a_checking(s)
        s.add(Home(name="Example Flat", payee="Example Lettings", rent=900.0, due_day=1))
        s.add(Bill(name="Example Lettings", amount=900.0, due_day=1))
        s.add(Bill(name="Example Water", amount=40.0, due_day=9))
        s.add(Bill(name="Example Water Co", amount=41.0, due_day=10))
        s.commit()
        _a_row(s, chk, date(2026, 10, 5), "Example Grocer", 54.12, source="plaid")
        _a_row(s, chk, date(2026, 10, 6), "Example Grocer", 54.12, source="csv")
        kinds = datacheck.run(s, TODAY)["by_kind"]
    assert kinds.get("counted_twice") and kinds.get("alike_bills") and kinds.get("duplicate_rows"), kinds


def test_a_the_data_check_finds_alike_accounts_a_payment_matched_twice_and_a_doubtful_sender():
    """"accounts that look alike, a payment matched twice, and mail whose sender may not be genuine"
    (docs/ROADMAP.md:286)."""
    with Session(engine) as s:
        s.add(Account(name="Everyday Checking", kind="checking", connector="plaid"))
        s.add(Account(name="Everyday Checking", kind="checking", connector="csv"))
        chk = _a_checking(s)
        t = _a_row(s, chk, date(2026, 10, 3), "Example Power", 80.0)
        b1, b2 = Bill(name="Example Power", amount=80.0, due_day=3), Bill(name="Example Gas", amount=80.0, due_day=3)
        s.add(b1)
        s.add(b2)
        s.commit()
        s.add(Payment(bill_id=b1.id, due_date=date(2026, 10, 3), status="paid", transaction_id=t.id, amount=80.0))
        s.add(Payment(bill_id=b2.id, due_date=date(2026, 10, 3), status="paid", transaction_id=t.id, amount=80.0))
        box = _a_mailbox(s, "one")
        ref = MessageRef(account_id=box.id, folder="INBOX", uid=1, message_id="<a@example.test>", sender="billing@examp1e-bank.test", sender_name="Example Bank", trust="crit", trust_why="Look-alike domain.")
        s.add(ref)
        s.commit()
        s.add(ExtractedFact(kind="bill_due", message_ref_id=ref.id, fields=json.dumps({"name": "Example Bank", "amount": 10.0})))
        s.commit()
        kinds = datacheck.run(s, TODAY)["by_kind"]
    assert kinds.get("alike_accounts") and kinds.get("paid_twice") and kinds.get("sender"), kinds


def test_a_the_data_check_changes_nothing_until_answered():
    """"Nothing changes until you answer, and every answer teaches it." (README.md:101). A run over doubled bills
    and doubled rows leaves every register as it was."""
    with Session(engine) as s:
        chk = _a_checking(s)
        s.add(Bill(name="Example Water", amount=40.0, due_day=9))
        s.add(Bill(name="Example Water Co", amount=41.0, due_day=10))
        s.commit()
        _a_row(s, chk, date(2026, 10, 5), "Example Grocer", 54.12, source="plaid")
        _a_row(s, chk, date(2026, 10, 6), "Example Grocer", 54.12, source="csv")
    before = _a_registers()
    with Session(engine) as s:
        assert datacheck.run(s, TODAY)["count"] >= 2
    assert _a_registers() == before


def test_a_they_are_different_is_remembered():
    """""they are different" is remembered" (docs/ROADMAP.md:286); "every answer teaches it" (README.md:101)."""
    with Session(engine) as s:
        s.add(Bill(name="Example Water", amount=40.0, due_day=9))
        s.add(Bill(name="Example Water Co", amount=41.0, due_day=10))
        s.commit()
        key = next(f["key"] for f in datacheck.findings(s, TODAY) if f["kind"] == "alike_bills")
        datacheck.answer(s, key, "different", TODAY)
        assert key not in {f["key"] for f in datacheck.findings(s, TODAY)}
        assert datacheck.run(s, TODAY)["by_kind"].get("alike_bills") is None
        assert len(s.exec(select(Bill).where(Bill.active == True)).all()) == 2  # noqa: E712  (both still counted)


def test_a_the_same_email_in_two_mailboxes_is_set_aside_on_its_own_and_logged():
    """"Only what is certain is set right on its own and logged: the same email in two mailboxes" (docs/ROADMAP.md:286)."""
    with Session(engine) as s:
        one, two = _a_mailbox(s, "one"), _a_mailbox(s, "two")
        refs = []
        for box in (one, two):
            r = MessageRef(account_id=box.id, folder="INBOX", uid=7, message_id="<same@example.test>", sender="bills@example.test")
            s.add(r)
            s.commit()
            refs.append(r)
            s.add(ExtractedFact(kind="bill_due", message_ref_id=r.id, fields=json.dumps({"name": "Example Power", "amount": 80.0})))
            s.commit()
        out = datacheck.run(s, TODAY)
        statuses = {f.message_ref_id: f.status for f in s.exec(select(ExtractedFact)).all()}
        assert out["set_aside"] == 1 and statuses == {refs[0].id: "pending", refs[1].id: "rejected"}
        assert any("read twice" in e["what"] for e in get_setting(s, datacheck.LOG, []))


def test_a_a_bill_a_lenders_plan_already_pays_steps_aside_and_is_logged():
    """"Only what is certain is set right on its own and logged: ... a bill a lender's own plan already pays"
    (docs/ROADMAP.md:286)."""
    from app.engine.borrowing import sync_borrowing
    from app.models import InstallmentPlan

    with Session(engine) as s:
        _a_checking(s)
        s.add(Bill(name="Affirm", amount=45.0, due_day=12))
        s.add(InstallmentPlan(provider="Affirm", merchant="Example Desk", total=270.0, installment=45.0, count=6, remaining=4, next_date=date(2026, 11, 12)))
        s.commit()
        sync_borrowing(s, TODAY)
        assert s.exec(select(Bill).where(Bill.name == "Affirm")).one().active is False
        assert any("Affirm" in e["what"] for e in get_setting(s, datacheck.LOG, []))


# --- the schedules -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, seconds, source", [
    ("mail_sync", 900, "Mail is read every fifteen minutes (docs/PLAN.md:36; docs/PLAN.md:154; docs/ROADMAP.md:61)"),
    ("self_check", 3600, "an hourly self-check (docs/ROADMAP.md:13; docs/PLAN.md:160; docs/ROADMAP.md:92)"),
    ("import_watch", 300, "a five-minute import-folder watch (docs/ROADMAP.md:13)"),
    ("detect_recurring", 6 * 3600, "a six-hourly job (docs/ROADMAP.md:26; docs/ROADMAP.md:84)"),
    ("match_payments", 24 * 3600, "payment matching daily (docs/ROADMAP.md:84)"),
    ("review_if_due", 3600, "the reviewer hourly (docs/ROADMAP.md:84)"),
    ("moves_verify", 6 * 3600, "A job reads the ledger every six hours (docs/ROADMAP.md:206)"),
    ("app_watch", 3600, "An hourly sample of every part (docs/ROADMAP.md:278)"),
    ("watch_daily", 6 * 3600, "Daily checks for unusual charges (docs/ROADMAP.md:252): looked at every six hours, each told once"),
])
def test_a_each_job_runs_as_often_as_the_documents_say(name, seconds, source):
    """Each schedule the documents state, against the registered job (sources in the parameter)."""
    assert jobs.specs[name].seconds == seconds, source


class _A_FakeBank:
    def __init__(self) -> None:
        self.calls = 0

    def sync(self, session: Session, link_id: int) -> SyncResult:
        self.calls += 1
        link = session.get(BankLink, link_id)
        link.last_sync_at = datetime.now(UTC)
        session.add(link)
        session.commit()
        return SyncResult(ok=True, summary="read", inserted=0)


def test_a_bank_data_syncs_every_four_hours_unless_changed(monkeypatch):
    """"banks every four hours and on demand" (docs/ROADMAP.md:84) and "four-hourly and on-demand pulls"
    (docs/ROADMAP.md:19). docs/PLAN.md:36 said "daily" and docs/PLAN.md:155 "Daily at 05:30"; both corrected to this."""
    fake = _A_FakeBank()
    monkeypatch.setitem(CONNECTORS, "fakebank", fake)
    with Session(engine) as s:
        link = BankLink(provider="fakebank", institution="Example Bank", last_sync_at=datetime.now(UTC) - timedelta(hours=3))
        s.add(link)
        s.commit()
        link_id = link.id
    assert scheduler.bank_sync() == "not due yet" and fake.calls == 0
    with Session(engine) as s:
        link = s.get(BankLink, link_id)
        link.last_sync_at = datetime.now(UTC) - timedelta(hours=4)
        s.add(link)
        s.commit()
    scheduler.bank_sync()
    assert fake.calls == 1


def test_a_a_bank_sync_that_brings_rows_runs_detection_and_matching(monkeypatch):
    """"Bank sync ... de-duplicate against exports; recurring detection; payment matching." (docs/PLAN.md:155);
    "Bills are marked paid when the matching transaction clears." (docs/PLAN.md:36)."""

    class Bringing(_A_FakeBank):
        def sync(self, session: Session, link_id: int) -> SyncResult:
            chk = session.exec(select(Account).where(Account.kind == "checking")).one()
            _a_row(session, chk, date(2026, 10, 9), "Example Power", 80.0, source="fakebank")
            super().sync(session, link_id)
            return SyncResult(ok=True, summary="one row", inserted=1)

    monkeypatch.setitem(CONNECTORS, "fakebank", Bringing())
    with Session(engine) as s:
        chk = _a_checking(s)
        bill = Bill(name="Example Power", amount=80.0, due_day=9, pay_from_account_id=chk.id)
        s.add(bill)
        s.add(BankLink(provider="fakebank", institution="Example Bank"))
        s.commit()
        bill_id = bill.id
    scheduler.bank_sync()
    with Session(engine) as s:
        paid = s.exec(select(Payment).where(Payment.bill_id == bill_id, Payment.due_date == date(2026, 10, 9))).one()
        assert paid.status == "paid" and paid.transaction_id is not None


def test_a_a_dropped_file_is_taken_on_the_next_watch_and_detection_and_matching_run_after(monkeypatch, tmp_path):
    """"a six-hourly job and a run after every import" (docs/ROADMAP.md:26); files picked up from the folder
    (docs/PLAN.md:36, :156: corrected from "the moment they arrive" / "On file arrival" to within five minutes)."""
    from app.config import settings

    monkeypatch.setattr(settings, "import_dir", tmp_path)
    with Session(engine) as s:
        chk = _a_checking(s)
        bill = Bill(name="Example Power", amount=80.0, due_day=9, pay_from_account_id=chk.id)
        s.add(bill)
        s.commit()
        bill_id = bill.id
    (tmp_path / "Example Checking.csv").write_text("Date,Description,Amount\n10/09/2026,EXAMPLE POWER,-80.00\n")
    assert "Example Checking.csv" in scheduler.import_watch()
    with Session(engine) as s:
        assert s.exec(select(Payment).where(Payment.bill_id == bill_id, Payment.due_date == date(2026, 10, 9))).one().status == "paid"
        assert int(get_setting(s, scheduler.DETECTED, 0) or 0) > 0  # detection finished over the new row
    assert scheduler.import_watch() == "nothing new"  # taken once


def test_a_the_plan_is_worked_out_again_as_soon_as_the_paycheck_lands():
    """"The plan is recomputed the morning a paycheck lands." (docs/PLAN.md:36). There is no 06:00 re-plan job
    (docs/PLAN.md:157, corrected): the plan is computed from the registers and a cached one lasts only until the
    next write, so the balance the paycheck brings is in the very next answer."""
    from app.engine.snapshot import finance_snapshot

    household()
    payday = date(2026, 10, 16)
    with Session(engine) as s:
        before = finance_snapshot(s, payday)
        chk = s.exec(select(Account).where(Account.kind == "checking")).first()
        chk.balance = float(chk.balance or 0) + 2240.0
        s.add(chk)
        s.commit()
        after = finance_snapshot(s, payday)
    assert after["safe_to_spend"]["amount"] != before["safe_to_spend"]["amount"]
    assert "replan" not in " ".join(jobs.specs) and "re_plan" not in " ".join(jobs.specs)


def test_a_jobs_are_idempotent():
    """"Jobs, each idempotent, logged, visible on the Jobs page" (docs/PLAN.md:150). Detection, matching and the
    data check run twice leave the registers as one run left them."""
    household()
    scheduler.detect_recurring()
    scheduler.data_check()
    once = _a_registers()
    scheduler.detect_recurring()
    scheduler.match_payments_job()
    scheduler.data_check()
    assert _a_registers() == once


def test_a_every_run_is_logged_and_a_failure_is_never_quiet(monkeypatch):
    """"Jobs, each idempotent, logged, visible on the Jobs page and through jobs.status" (docs/PLAN.md:150)."""

    def broken() -> str:
        raise ValueError("the source refused")

    jobs.register("sweep21_broken", broken, 3600, "A job that fails")
    try:
        run = jobs.run("sweep21_broken")
        assert run.ok is False and "the source refused" in run.summary
        with Session(engine) as s:
            assert s.exec(select(JobRun).where(JobRun.name == "sweep21_broken")).one().ok is False
    finally:
        jobs.specs.pop("sweep21_broken", None)


def test_a_the_brief_reaches_the_phone_once_a_day_from_seven():
    """"Brief | 07:00 daily" (docs/PLAN.md:158); "the brief to the phone once a day from the chosen hour"
    (docs/ROADMAP.md:84)."""
    from app import notify
    from app.clock import zone

    with Session(engine) as s:
        notify.save_push(s, {"kind": "ntfy", "url": "https://push.example.test", "topic": "t"})
        assert notify.push_state(s)["brief_hour"] == 7
        day = datetime(2026, 10, 14, tzinfo=zone())
        assert not notify.morning_brief_due(s, day.replace(hour=6, minute=59).astimezone(UTC))
        at_seven = day.replace(hour=7).astimezone(UTC)
        assert notify.morning_brief_due(s, at_seven)
        notify.mark_brief_sent(s, at_seven)
        assert not notify.morning_brief_due(s, day.replace(hour=21).astimezone(UTC))
        assert notify.morning_brief_due(s, (day + timedelta(days=1)).replace(hour=7).astimezone(UTC))


def test_a_reminders_are_sent_once_each_from_the_chosen_hour(monkeypatch):
    """"reminder switches for due dates, paydays and statement days ... sent once each from the chosen hour"
    (docs/ROADMAP.md:188)."""
    from app import notify
    from app.clock import zone
    from app.engine import reminders

    household()
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body, **k: sent.append(title) or {"ok": True})
    with Session(engine) as s:
        notify.save_push(s, {"kind": "ntfy", "url": "https://push.example.test", "topic": "t"})
        hour = int(reminders.reminder_settings(s)["hour"])
        day = datetime(2026, 10, 14, tzinfo=zone())
        assert reminders.run(s, day.replace(hour=max(0, hour - 1)).astimezone(UTC), TODAY)["sent"] == 0 or hour == 0
        due = reminders.due_today(s, TODAY)
        first = reminders.run(s, day.replace(hour=hour).astimezone(UTC), TODAY)
        again = reminders.run(s, day.replace(hour=min(23, hour + 2)).astimezone(UTC), TODAY)
    assert first["sent"] == len(due) and again["sent"] == 0 and len(sent) == len(due)


def test_a_one_writer_at_a_time_across_jobs():
    """"One writer at a time across jobs" (docs/ROADMAP.md:268)."""
    from app.jobs.writing import one_writer

    order: list[str] = []
    held = threading.Event()

    def first() -> None:
        with one_writer():
            held.set()
            time.sleep(0.2)
            order.append("first done")

    def second() -> None:
        held.wait()
        with one_writer():
            order.append("second in")

    a, b = threading.Thread(target=first), threading.Thread(target=second)
    a.start()
    b.start()
    a.join()
    b.join()
    assert order == ["first done", "second in"]


# --- the self-check, the app's own health and the restore points --------------------------------------------------


def test_a_the_self_check_names_stale_balances_disk_database_and_each_source():
    """"Self-check | Hourly | Each connector's last success; stale data warnings; ... disk and database size"
    (docs/PLAN.md:160); "stale balances, disk and database size, summarised as a list of problems" (docs/ROADMAP.md:92)."""
    with Session(engine) as s:
        s.add(Account(name="Example Linked Checking", kind="checking", connector="plaid", balance=100.0, balance_at=datetime.now(UTC) - timedelta(days=4)))
        s.add(BankLink(provider="plaid", institution="Example Bank", last_error="the bank asked for a new sign-in"))
        s.commit()
    scheduler.self_check()
    with Session(engine) as s:
        report = get_setting(s, "selfcheck", {})
    assert "Example Linked Checking" in report["stale_accounts"]
    assert any("stale balances" in p for p in report["problems"]) and any("new sign-in" in p for p in report["problems"])
    assert report["free_gb"] > 0 and report["db_mb"] >= 0 and report["sources"][0]["name"] == "Example Bank"


def test_a_a_restore_point_before_every_import_and_once_a_day(monkeypatch, tmp_path):
    """"A copy of the whole database before every import, once a day and on request, made with SQLite's online
    backup and read back before it counts" (docs/ROADMAP.md:195); "Restore points daily" (README.md:187)."""
    from app import restore
    from app.connectors.files import import_rows, parse_csv_rows

    def kinds() -> list[str]:
        return [p["kind"] for p in restore.list_points()]

    def newest(kind: str) -> str | None:
        return next((p["name"] for p in restore.list_points() if p["kind"] == kind), None)

    before = newest("import")
    with Session(engine) as s:
        import_rows(s, parse_csv_rows("Date,Description,Amount\n10/09/2026,EXAMPLE GROCER,-12.50\n"), source_file="example.csv", account_name="Example Checking")
        s.commit()
    assert newest("import") not in (None, before)  # a new copy (only the last fifteen are kept, so a count can stand still)
    scheduler.restore_point_daily()
    made = kinds().count("daily")
    assert "today's copy is there" in scheduler.restore_point_daily() and kinds().count("daily") == made  # once a day
    assert all(p.get("verified") for p in restore.list_points())


def test_a_the_self_check_reports_a_copy_older_than_two_days(monkeypatch):
    """"The self-check reports a copy older than two days." (docs/ROADMAP.md:195)."""
    from app import restore

    real = restore.status
    monkeypatch.setattr(restore, "status", lambda fresh=False: {**real(fresh), "latest_age_hours": 49.0, "copy_due": True})
    scheduler.self_check()
    with Session(engine) as s:
        assert "no restore point in the last two days" in get_setting(s, "selfcheck", {})["problems"]
    monkeypatch.setattr(restore, "status", lambda fresh=False: {**real(fresh), "latest_age_hours": 20.0, "copy_due": True})
    scheduler.self_check()
    with Session(engine) as s:
        assert "no restore point in the last two days" not in get_setting(s, "selfcheck", {})["problems"]


def test_a_the_backup_manager_reads_json_and_restoring_stays_in_the_app(capsys):
    """"python -m app.restore status|list|make|check prints JSON, for BoxPilot to run in the container; restoring
    stays in the app." (docs/ROADMAP.md:196)."""
    from app import restore

    for verb in ("status", "list"):
        assert restore.main([verb]) == 0
        json.loads(capsys.readouterr().out)
    assert restore.main(["make", "BoxPilot"]) == 0
    made = json.loads(capsys.readouterr().out)
    assert restore.main(["restore", made["name"]]) == 2


def test_a_the_restore_drill_is_weekly(monkeypatch):
    """"A weekly restore drill of the newest copy" (docs/ROADMAP.md:239)."""
    from app import offbox

    ran: list[int] = []
    monkeypatch.setattr(offbox, "drill", lambda session: ran.append(1) or {"ok": True, "detail": "drilled"})
    with Session(engine) as s:
        set_setting(s, offbox.DRILL, {"at": (datetime.now(UTC) - timedelta(days=6)).isoformat(), "ok": True})
    assert "last drill" in scheduler.restore_drill() and not ran
    with Session(engine) as s:
        set_setting(s, offbox.DRILL, {"at": (datetime.now(UTC) - timedelta(days=7)).isoformat(), "ok": True})
    assert scheduler.restore_drill() == "drilled" and ran == [1]


def test_a_the_phone_is_told_once_daily_while_broken_once_fixed_never_with_the_error_text():
    """"the phone told once when a part breaks, daily while it stays broken, and once when fixed, never with a job's
    own error text" (docs/ROADMAP.md:278)."""
    with Session(engine) as s:
        s.add(JobRun(name="data_check", started_at=datetime.now(UTC), finished_at=datetime.now(UTC), ok=False, summary="OperationalError: secret path /data/x.db"))
        s.commit()
        part = apphealth.job_checks(s, jobs.specs)
    assert part["state"] == "crit" and "secret path" not in part["note"]
    now = datetime.now(UTC)
    told, state = apphealth.plan_alerts({}, [part], now)
    assert len(told) == 1 and "secret path" not in json.dumps(told)
    assert apphealth.plan_alerts(state, [part], now + timedelta(hours=3))[0] == []
    again, state = apphealth.plan_alerts(state, [part], now + timedelta(hours=apphealth.REMIND_HOURS))
    assert len(again) == 1 and again[0]["title"].startswith("Still")
    fixed, state = apphealth.plan_alerts(state, [{**part, "state": "good"}], now + timedelta(hours=30))
    assert len(fixed) == 1 and fixed[0]["title"].startswith("Fixed") and state == {}
    assert apphealth.plan_alerts(state, [{**part, "state": "good"}], now + timedelta(hours=31))[0] == []


def test_a_what_the_watch_finds_is_told_once():
    """"Daily checks for unusual charges, new or doubled subscriptions and price rises" told once (docs/ROADMAP.md:252)."""
    from app.engine.watch import to_announce

    with Session(engine) as s:
        chk = _a_checking(s)
        _a_row(s, chk, date(2026, 10, 12), "Example Electronics", 129.0, tag="a")
        _a_row(s, chk, date(2026, 10, 13), "Example Electronics", 129.0, tag="b")
        first = to_announce(s, TODAY)
        assert to_announce(s, TODAY) == []
    assert [f["kind"] for f in first] == ["double"], first


# --- what the README counts ------------------------------------------------------------------------------------------


def test_a_forty_four_tools_and_the_readme_names_each():
    """"44 tools, each with its scope, a read-only flag where true" (README.md:178) and the tool list (README.md:236)."""
    import re
    from pathlib import Path

    from app.mcp_server import mcp_server

    names = {t.name for t in mcp_server._tool_manager.list_tools()}
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    line = next(x for x in readme.splitlines() if x.startswith("The tools: "))
    listed = set(re.findall(r"`([a-z_]+)`", line))
    assert len(names) == 44 and listed == names


# === Group B: the plan, the checks and the guides ===================================================================

SOURCE_PREFIXES = ("bill:", "plan:", "advance:", "debt:", "series:", "planned:", "income:", "card:", "setting:", "goal:", "home:", "account:")


def _b_snap(today: date = TODAY) -> dict:
    with Session(engine) as s:
        return finance_snapshot(s, today)


def _b_world(cash: float = 1000.0, net: float = 1000.0, cadence: str = "biweekly", next_date: date = date(2026, 10, 16)) -> int:
    """A minimal household: one checking account and one paycheck. Returns the checking account's id."""
    with Session(engine) as s:
        a = Account(name="Example Checking", kind="checking", balance=cash, connector="manual")
        s.add(a)
        s.commit()
        s.refresh(a)
        s.add(Income(name="Example Employer", cadence=cadence, expected_net=net, next_date=next_date, account_id=a.id))
        s.commit()
        return a.id


def _b_tx(account_id: int, on: date, merchant: str, amount: float, category: str = "Groceries", **flags) -> None:
    with Session(engine) as s:
        s.add(Transaction(account_id=account_id, date=on, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, source="manual",
                          row_hash=f"b-{account_id}-{on}-{merchant}-{amount}", **flags))
        s.commit()


# ---- the projection (PLAN section 7, README) ---------------------------------------------------------------------


def test_b_the_daily_line_runs_sixty_days_from_today():
    """'a daily balance for the next 90 days' (docs/PLAN.md:132) and 'sixty days of what is free to spend'
    (README.md:57). The engine's line is sixty days; the PLAN's ninety is corrected in the report."""
    household()
    snap = _b_snap()
    days = [date.fromisoformat(p["date"]) for p in snap["projection"]]
    assert days[0] == TODAY and len(days) == 60 and days == [TODAY + timedelta(days=k) for k in range(60)]
    assert all(p["at_pace"] <= p["available"] + 0.005 for p in snap["projection"])  # the honest line also spends the usual pace


def test_b_a_what_if_adds_events_without_saving_them():
    """'A what-if layer adds hypothetical events without saving them.' (docs/PLAN.md:132)"""
    household()
    before = tables()
    with Session(engine) as s:
        out = run_scenario(s, TODAY, {"move_date": "2026-11-20", "new_rent": 1700.0, "one_offs": [{"name": "Truck", "amount": 300.0, "date": "2026-11-19"}]})
    base = {p["date"]: p["at_pace"] for p in out["baseline"]["path"]}
    assert all(p["at_pace"] <= base[p["date"]] + 0.005 for p in out["scenario"]["path"])
    assert any(base[p["date"]] - p["at_pace"] >= 300.0 for p in out["scenario"]["path"])  # the events were counted
    assert tables() == before  # and nothing was written


def test_b_a_scenario_with_no_entries_is_the_plain_projection():
    """'a property test (a scenario with no entries equals the plain projection)' (docs/ROADMAP.md:166)"""
    household()
    with Session(engine) as s:
        out = run_scenario(s, TODAY, {"move_date": "2026-11-20"})
    assert out["scenario"]["path"] == out["baseline"]["path"] and out["scenario"]["low"] == out["baseline"]["low"]
    assert out["monthly_change"] == 0.0 and out["one_offs_total"] == 0.0


def test_b_a_scenario_returns_its_low_point_and_the_first_date_that_passes():
    """'Returns the low point and its date, the first date the move passes' (docs/ROADMAP.md:166)"""
    _b_world(cash=600.0, net=1500.0)
    with Session(engine) as s:
        s.add(Bill(name="Example Rent", amount=900.0, due_day=1, category="Rent"))
        s.commit()
        spec = {"one_offs": [{"name": "Deposit", "amount": 2500.0}]}  # a one-off with no day lands on the move date
        out = run_scenario(s, TODAY, {**spec, "move_date": "2026-10-20"})
        assert out["scenario"]["low"]["date"] and out["verdict"] == "short"
        assert out["first_passes"] and out["first_passes"] > "2026-10-20"
        later = run_scenario(s, TODAY, {**spec, "move_date": out["first_passes"]})
    assert later["verdict"] == "passes"  # the first date that passes, passes


# ---- the paycheck plan (PLAN section 7) --------------------------------------------------------------------------


def test_b_every_line_of_the_plan_names_its_source():
    """'Every line names its bill, series, advance or rule.' (docs/PLAN.md:138)"""
    household()
    snap = _b_snap()
    lines = list(snap["due_before_payday"]["items"]) + list(snap["advances"]["items"])
    for w in snap["windows"]:
        lines += w["bills"]["items"] + w["advances"]["items"] + w["set_aside"]["items"]
    assert lines
    for e in lines:
        assert str(e.get("source") or "").startswith(SOURCE_PREFIXES), e


def test_b_advances_are_taken_first_then_the_bills_in_date_order():
    """'First the advances that repay themselves on payday, then bills, installments and minimums due inside the
    window' (docs/PLAN.md:138). In a short window the advance is funded and the last bill is not."""
    acc = _b_world(cash=0.0, net=1000.0)
    with Session(engine) as s:
        s.add(Advance(provider="Example Advance", amount=300.0, fee=5.0, taken_on=date(2026, 10, 10), repay_date=date(2026, 10, 16)))
        s.add(Bill(name="Example Phone", amount=400.0, due_day=18, pay_from_account_id=acc))
        s.add(Bill(name="Example Power", amount=400.0, due_day=25, pay_from_account_id=acc))
        s.commit()
    w = _b_snap()["windows"][0]
    assert w["advances"]["total"] == 305.0 and all(e["funded"] for e in w["advances"]["items"])
    funded = {e["name"]: e["funded"] for e in w["bills"]["items"]}
    assert funded["Example Phone"] is True and funded["Example Power"] is False


def test_b_each_window_is_paycheck_less_advances_bills_and_set_aside_and_the_buffer_comes_out_first():
    """'then set-asides for bills larger than one paycheck comfortably carries, then the buffer rule, then goals,
    then safe-to-spend' (docs/PLAN.md:138)"""
    household()
    with Session(engine) as s:
        s.add(Goal(name="Example Fund", target=1000.0, pace=40.0))
        s.commit()
    snap = _b_snap()
    for w in snap["windows"]:
        assert abs(w["safe"] - (w["inflow"] - w["advances"]["total"] - w["bills"]["total"] - w["set_aside"]["total"])) < 0.02
    first = snap["windows"][0]
    assert first["carried_in"] == round(snap["cash"] - snap["due_before_payday"]["total"] - snap["buffer"], 2)
    sts = snap["safe_to_spend"]
    assert sts["after_goals"] == round(sts["amount"] - snap["goals"]["per_paycheck"], 2)  # goals come after safe to spend is found
    assert any(g["name"] == "Example Fund" and g["per_paycheck"] == 40.0 for g in snap["goals"]["items"])


# ---- safe to spend (ROADMAP M2.8, README) -----------------------------------------------------------------------


def test_b_safe_to_spend_is_its_parts():
    """'safe to spend is until payday, from checking only ... less what is due before payday, advances pulled
    before it, the buffer and any shortfall ...; the parts are returned with it' (docs/ROADMAP.md:42)"""
    household()
    snap = _b_snap()
    sts, p = snap["safe_to_spend"], snap["safe_to_spend"]["parts"]
    assert sts["amount"] == round(p["cash"] - p["due_before_payday"] - p["advances"] - p["buffer"] - p["next_window_shortfall"], 2)
    assert sts["through"] == (date.fromisoformat(snap["next_paycheck"]["date"]) - timedelta(days=1)).isoformat()
    assert p["due_before_payday"] > 0 and p["due_before_payday"] == snap["due_before_payday"]["total"]  # 'with the bills already taken off' (README.md:42)


def test_b_savings_is_kept_and_shown_apart_from_safe_to_spend():
    """'from checking only (savings is kept and shown apart)' (docs/ROADMAP.md:42)"""
    household()
    before = _b_snap()["safe_to_spend"]["amount"]
    with Session(engine) as s:
        s.add(Account(name="Example Savings", kind="savings", balance=5000.0, connector="manual"))
        s.commit()
    snap = _b_snap()
    assert snap["safe_to_spend"]["amount"] == before and snap["savings"] == 5000.0 and snap["cash"] == 312.0


def test_b_a_shortfall_the_next_check_leaves_comes_off_safe_to_spend():
    """'any shortfall the next paycheck leaves in its own window' (docs/ROADMAP.md:42)"""
    acc = _b_world(cash=2000.0, net=500.0)
    with Session(engine) as s:
        s.add(Bill(name="Example Rent", amount=900.0, due_day=20, category="Rent", pay_from_account_id=acc))
        s.commit()
    sts = _b_snap()["safe_to_spend"]
    assert sts["parts"]["next_window_shortfall"] == 400.0
    assert sts["amount"] == round(2000.0 - sts["parts"]["due_before_payday"] - 200.0 - 400.0, 2)


def test_b_a_bill_on_the_card_rides_the_card_payment_not_checking():
    """'a bill on the card rides the card payment it is paid from instead of leaving checking on its due date, and
    the card payment carries the balance, the bills that rode it' (docs/ROADMAP.md:42)"""
    household()
    with Session(engine) as s:
        store = s.exec(select(Account).where(Account.name == "Store Card")).one()
        debt = s.exec(select(Debt).where(Debt.account_id == store.id)).one()
        debt.minimum = 0.0  # a card paid off each time: its payment carries what rode it (a carried card owes its minimum)
        s.add(debt)
        streaming = s.exec(select(Bill).where(Bill.name == "Streaming")).one()
        streaming.pay_from_account_id = store.id  # due Oct 15, before payday
        s.add(streaming)
        s.commit()
    snap = _b_snap()
    item = next(e for e in snap["due_before_payday"]["items"] if e["name"] == "Streaming")
    assert item.get("via_card")
    assert snap["due_before_payday"]["total"] == 40.0  # tolls only: the streaming charge does not leave checking
    paid = [c for c in snap["card_payments"] if c["name"] == "Store Card payment"]
    assert paid and paid[0]["parts"]["bills"] == 17.0  # the card payment carries the bill that rode it


# ---- due days and the paycheck that pays (ROADMAP M2.1) ----------------------------------------------------------


def test_b_a_due_day_of_31_falls_on_the_last_day_the_month_has():
    """'Due days run to the 31st (the last day the month has)' (docs/ROADMAP.md:35)"""
    bill = Bill(name="Example Lease", amount=10.0, due_day=31)
    got = bill_dates(bill, date(2026, 10, 1), date(2027, 3, 31))
    assert got == [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]


def test_b_each_bill_is_paid_by_the_check_before_its_due_date():
    """'each bill says which paycheck pays it: the one before its due date' (docs/ROADMAP.md:35)"""
    household()
    snap = _b_snap()
    paydays = [date.fromisoformat(w["start"]) for w in snap["windows"]]
    for w in snap["windows"]:
        start = date.fromisoformat(w["start"])
        for e in w["bills"]["items"]:
            if e.get("kind") == "card_payment" or e.get("via_card"):
                continue
            due = date.fromisoformat(e["date"])
            assert max(d for d in paydays if d <= due) == start, (e["name"], e["date"], w["start"])


def test_b_semimonthly_rent_on_the_1st_rides_the_end_of_month_check():
    """'so rent due on the 1st rides the end-of-month check' (docs/ROADMAP.md:35)"""
    acc = _b_world(cash=3000.0, net=1800.0, cadence="semimonthly", next_date=date(2026, 10, 15))
    with Session(engine) as s:
        s.add(Bill(name="Example Rent", amount=1200.0, due_day=1, category="Rent", pay_from_account_id=acc))
        s.commit()
    snap = _b_snap()
    from app.engine.cadence import payday_position

    seen = []
    for w in snap["windows"]:
        for e in w["bills"]["items"]:
            if e["name"] == "Example Rent":
                seen.append(e["date"])
                assert payday_position(date.fromisoformat(w["start"])) == "end", (e["date"], w["start"])
    assert "2026-11-01" in seen  # Oct 31 is a Saturday: the end-of-month check comes Fri Oct 30 and carries it


def test_b_a_detected_series_suggests_a_kind_from_its_category():
    """'each detected series carries a suggested kind from its category (bill, subscription, or regular spending
    to ignore)' (docs/ROADMAP.md:35)"""
    from app.engine.recurring import suggest_kind

    assert suggest_kind("Bills & Utilities") == "bill" and suggest_kind("Entertainment", "Example Streaming") == "subscription" and suggest_kind("Groceries") == "ignored"
    household()
    with Session(engine) as s:
        rows = s.exec(select(RecurringSeries)).all()
    assert rows and all(r.suggested_kind for r in rows if r.kind != "income" and r.category not in ("Income", "Transfer", "Borrowing"))


# ---- the purchase check (PLAN section 7, ROADMAP M5.3, README) ----------------------------------------------------


def test_b_nothing_costs_nothing():
    """'Property tests: nothing costs nothing; the first passing date passes.' (docs/ROADMAP.md:77)"""
    household()
    with Session(engine) as s:
        out = purchase_check(s, TODAY, 0)
    assert out["verdict"] == "yes" and out["breaks"] is None and out["low_point"]["at_pace"] == out["low_point_before"]["value"]


def test_b_the_verdict_is_yes_wait_or_no_and_the_first_passing_date_passes():
    """'Yes, wait, or no, naming the bill or cushion it would break and the first day it would be fine.'
    (README.md:31; docs/ROADMAP.md:77)"""
    household()
    with Session(engine) as s:
        wait = purchase_check(s, TODAY, 400)
        no = purchase_check(s, TODAY, 50000)
        assert wait["verdict"] == "wait" and wait["first_ok_date"] > TODAY.isoformat()
        assert wait["breaks"]["name"] and wait["breaks"]["source"].startswith(("bill:", "plan:", "advance:", "debt:", "setting:buffer"))
        assert wait["breaks"]["shortfall"] > 0
        assert no["verdict"] == "no" and no["first_ok_date"] is None and no["breaks"]
        again = purchase_check(s, TODAY, 400, on=date.fromisoformat(wait["first_ok_date"]))
    assert again["verdict"] == "yes"


def test_b_a_purchase_passes_when_it_leaves_the_line_above_zero_and_no_deeper_where_under():
    """'a purchase passes when it leaves the line where it is above zero and no deeper where it is already
    under' (docs/ROADMAP.md:77)"""
    household()
    with Session(engine) as s:
        snap = finance_snapshot(s, TODAY)
        line = {p["date"]: p["at_pace"] for p in snap["projection"]}
        for amount in (5.0, 30.0, 120.0, 600.0):
            for on in (TODAY, date(2026, 10, 30), date(2026, 11, 20)):
                out = purchase_check(s, TODAY, amount, on=on)
                deeper = any(v - amount < min(0.0, v) - 0.005 for d, v in line.items() if d >= on.isoformat())
                assert (out["verdict"] == "yes") == (not deeper), (amount, on, out["verdict"])


def test_b_the_cushion_is_named_when_no_bill_follows():
    """'naming the bill or cushion it would break' (README.md:31): with no bill ahead, the buffer is named."""
    _b_world(cash=300.0, net=0.0)
    with Session(engine) as s:
        out = purchase_check(s, TODAY, 250)
    assert out["verdict"] in ("wait", "no") and out["breaks"]["name"] == "the buffer" and out["breaks"]["source"] == "setting:buffer"


def test_b_the_pay_in_four_variant_says_what_it_adds_to_the_next_three_paychecks():
    """'The BNPL variant adds the installments instead of the price and says what the plan adds to the next three
    paychecks.' (docs/PLAN.md:140)"""
    household()
    with Session(engine) as s:
        out = purchase_check(s, TODAY, 400, installments=4)
    assert [c["amount"] for c in out["charges"]] == [100.0, 100.0, 100.0, 100.0]
    assert [c["date"] for c in out["charges"]] == ["2026-10-14", "2026-10-28", "2026-11-11", "2026-11-25"]
    assert [(p["payday"], p["adds"]) for p in out["by_paycheck"]] == [("2026-10-16", 100.0), ("2026-10-30", 100.0), ("2026-11-13", 100.0)]


# ---- the don't-buy list and watchlists (PLAN section 7, ROADMAP M5.4) --------------------------------------------


def test_b_the_dont_buy_list_names_advances_plans_duplicates_prices_and_a_pay_in_full_card():
    """'any active advance, more than N active installment plans, a second subscription in a category that already
    has one, a price increase ..., and a card the owner has said to pay in full that is carrying a balance'
    (docs/PLAN.md:142)"""
    household()
    with Session(engine) as s:
        d = s.exec(select(Debt).where(Debt.name == "Store Card")).one()
        d.pay_in_full = True
        s.add(d)
        series = s.exec(select(RecurringSeries).where(RecurringSeries.merchant == "Streaming")).one()
        series.latest_amount = 17.0  # the rise from Oct 1, as the October charge will show it
        s.add(series)
        s.commit()
        kinds = {i["kind"]: i for i in watchlist(s, TODAY)["items"]}
    assert {"advances", "duplicates", "price", "card"} <= set(kinds), sorted(kinds)
    assert kinds["card"]["sources"] == [f"debt:{d.id}"]
    assert any(n in kinds["duplicates"]["text"] for n in ("Music", "Streaming"))


def test_b_too_many_plans_is_on_the_list():
    """'more than N active installment plans' (docs/PLAN.md:142)"""
    from app.engine.watchlist import MANY_PLANS

    _b_world()
    with Session(engine) as s:
        for k in range(MANY_PLANS):
            s.add(InstallmentPlan(provider="Example BNPL", merchant=f"Example item {k}", total=100.0, installment=25.0, count=4, remaining=2, next_date=date(2026, 10, 20)))
        s.commit()
        kinds = {i["kind"] for i in watchlist(s, TODAY)["items"]}
    assert "plans" in kinds


def test_b_the_watchlist_warns_and_never_blocks():
    """'Watchlists: a soft cap ... they warn and project, never block.' (docs/ROADMAP.md:78)"""
    household()
    with Session(engine) as s:
        assert watchlist(s, TODAY)["items"]  # something is on the list
        out = purchase_check(s, TODAY, 0)
        later = purchase_check(s, TODAY, 400, on=date(2026, 10, 30))
    assert out["verdict"] == "yes" and later["verdict"] == "yes"  # the list has no say in the check


# ---- billing-date suggestions (PLAN section 7, ROADMAP M5.5 and M40, README) --------------------------------------


def _b_suggestion_world() -> None:
    with Session(engine) as s:
        a = Account(name="Example Checking", kind="checking", balance=1000.0, connector="manual")
        s.add(a)
        s.commit()
        s.refresh(a)
        s.add(Income(name="Example Employer", cadence="weekly", expected_net=500.0, next_date=date(2026, 10, 16)))
        s.add(Bill(name="Example Rent", amount=1800.0, due_day=1, movable=True, pay_from_account_id=a.id))
        s.add(Bill(name="Example Insurance", amount=1500.0, cadence="yearly", due_day=28, anchor_date=date(2026, 11, 28), autopay=True))
        s.commit()


def test_b_a_suggested_move_lifts_the_low_point_by_a_dollar_and_yields_what_it_predicts():
    """'the moves that lift the sixty-day low point by a dollar or more, ranked, each judged alone, with the before
    and after ... Property test: a move never lowers the low point, and the predicted low point is the one the move
    yields.' (docs/ROADMAP.md:79; docs/PLAN.md:144)"""
    _b_suggestion_world()
    with Session(engine) as s:
        out = billing_date_suggestions(s, TODAY)
        assert out["suggestions"], out
        assert out["suggestions"] == sorted(out["suggestions"], key=lambda x: (-x["improvement"], x["name"]))
        for sug in out["suggestions"]:
            assert sug["improvement"] >= 1.0 and sug["low_after"] > sug["low_before"]
            assert sug["instruction"].startswith("Ask") and sug["draft"].startswith("Hello")
            bill = s.get(Bill, sug["bill_id"])
            bill.due_day = sug["to_day"]
            s.add(bill)
            s.commit()
            assert abs(billing_date_suggestions(s, TODAY)["baseline"]["low"] - sug["low_after"]) < 0.01


def test_b_the_suggestions_are_deterministic():
    """'Coordinate descent over the movable bills, deterministic.' (docs/PLAN.md:144)"""
    from app.engine import decisions

    _b_suggestion_world()
    with Session(engine) as s:
        first = billing_date_suggestions(s, TODAY)
        decisions._suggested.clear()
        second = billing_date_suggestions(s, TODAY)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_b_a_move_never_lowers_the_low_point_on_any_day():
    """'a move never lowers the low point' (docs/ROADMAP.md:79): every day tried for the household's movable
    bills; any day it offers is at least as good as where the bill is."""
    household()
    with Session(engine) as s:
        out = billing_date_suggestions(s, TODAY)
        offered = {(x["bill_id"], x["to_day"]) for x in out["suggestions"]}
        rent = s.exec(select(Bill).where(Bill.name == "Electricity")).one()
        for day in (1, 10, 27):
            check = date_move_check(s, TODAY, rent.id, day)
            if (rent.id, day) in offered:
                assert check["change"] >= 1.0
    assert all(x["low_after"] >= out["baseline"]["low"] for x in out["suggestions"])


def test_b_suggestions_only_where_a_change_takes_effect_in_time():
    """'billing-date suggestions only where a change takes effect in time' (docs/ROADMAP.md:267). The household's
    low point (Oct 29) comes before any change could start, so nothing is offered, and the note says why."""
    household()
    with Session(engine) as s:
        out = billing_date_suggestions(s, TODAY)
    assert out["baseline"]["low_date"] < out["moves_start"]
    assert out["suggestions"] == [] and out["note"] and "before a billing-date change could start" in out["note"]


# ---- explanations (PLAN section 7) -------------------------------------------------------------------------------


def test_b_the_citation_check_flags_a_number_the_engine_did_not_return():
    """'A citation test checks that every number in an explanation appears in the result it was given.'
    (docs/PLAN.md:146)"""
    household()
    snap = _b_snap()
    allowed = engine_numbers(snap)
    safe = snap["safe_to_spend"]["amount"]
    assert unverified(f"You can spend ${safe:,.2f} until payday.", allowed) == []
    assert unverified("You can spend $4,321.99 until payday.", allowed) == ["$4,321.99"]
    for text in snap["why"].values():  # the engine's own sentences pass their own check
        assert unverified(text, allowed) == [], text


# ---- the River, the month grid, the one thing (README) -----------------------------------------------------------


def test_b_the_month_grid_carries_each_weeks_low_point():
    """'Month grid: the calendar with paydays, due dates and each week's low point.' (README.md:61)"""
    household()
    snap = _b_snap()
    line = {p["date"]: p["at_pace"] for p in snap["projection"]}
    weeks = [w for w in snap["calendar"]["weeks"] if w["low"] is not None]
    assert len(weeks) >= 8
    for w in weeks:
        inside = [v for d, v in line.items() if w["start"] <= d <= w["end"]]
        assert w["low"] == min(inside) and line[w["low_date"]] == w["low"]
    kinds = {e["kind"] for e in snap["calendar"]["events"]}
    assert "paycheck" in kinds and {"bill", "subscription"} & kinds


def test_b_every_morning_it_says_the_one_thing():
    """'Every morning it says the one thing that needs attention.' (README.md:36)"""
    household()
    with Session(engine) as s:
        snap = finance_snapshot(s, TODAY)
        out = one_thing(s, TODAY, snap=snap, cover=account_cover(s, TODAY))
    assert isinstance(out["thing"], dict) and out["thing"]["text"] and out["thing"]["link"].startswith("/")
    assert len(out["others"]) <= 2 and out["thing"] not in out["others"]


# ---- readiness (ROADMAP M10.2) -----------------------------------------------------------------------------------


def test_b_readiness_works_rent_to_gross_from_the_paystubs():
    """'the rent-to-gross ratio from the paystubs, the figure a landlord uses' (docs/ROADMAP.md:167)"""
    household()
    with Session(engine) as s:
        now = next(i for i in readiness(s, TODAY)["items"] if i["key"] == "rent_to_gross")
        dear = next(i for i in readiness(s, TODAY, new_rent=2500.0)["items"] if i["key"] == "rent_to_gross")
    gross = 2980.77 * 26 / 12  # the last stub's gross, 26 checks a year
    assert f"{1295.0 / gross * 100:.0f}%" in now["value"] and now["met"] is True
    assert f"{2500.0 / gross * 100:.0f}%" in dear["value"] and dear["met"] is False


# ---- cover and saving (ROADMAP M11) ------------------------------------------------------------------------------


def test_b_cover_says_for_every_account_what_leaves_before_each_of_the_next_two_paydays():
    """'for every account, what leaves it before each of the next two paydays, what it holds and the difference'
    (docs/ROADMAP.md:184)"""
    household()
    with Session(engine) as s:
        snap = finance_snapshot(s, TODAY)
        cov = account_cover(s, TODAY)
    assert cov["paydays"] == [p["date"] for p in snap["paydays"][:2]]
    spend = [a for a in cov["accounts"] if a["role"] == "spend"]
    assert spend and all(len(a["windows"]) == 2 for a in spend)
    for a in spend:
        for w in a["windows"]:
            assert w["left"] == round(w["has"] + w["inflow"] - w["needs"], 2)
    assert {a["name"] for a in cov["accounts"] if a["role"] == "card"} == {"Rewards Card", "Store Card"}


def test_b_the_ladder_has_its_five_rungs_in_order_and_a_bigger_share_is_never_later():
    """'The saving ladder (a starter, one pay period of must-pays, one, three and six months)' (docs/ROADMAP.md:184)"""
    household()
    with Session(engine) as s:
        sv = saving_plan(s, TODAY)
    names = [r["name"] for r in sv["rungs"]]
    assert names == ["A starter", "One pay period of must-pays", "One month", "Three months", "Six months"]
    t = [r["target"] for r in sv["rungs"]]
    assert t[1] <= t[2] <= t[3] <= t[4] and abs(t[3] - 3 * sv["month"]) <= 10 and abs(t[4] - 6 * sv["month"]) <= 10

    def key(e):
        return e or "9999-12-31"

    for small, big in zip(sv["by_share"], sv["by_share"][1:]):
        assert all(key(b) <= key(a) for a, b in zip(small["etas"], big["etas"]))
    assert all(key(r["eta"]) <= key(r["eta_without_rollover"]) for r in sv["rungs"])  # 'the rollover of a finished debt's payment into the ladder'


def test_b_a_card_over_thirty_percent_at_its_statement_gets_one_early_payment():
    """'A card over 30% at its statement gets one early payment that covers the scheduled one.' (docs/ROADMAP.md:184)"""
    household()
    with Session(engine) as s:
        card = s.exec(select(Account).where(Account.name == "Rewards Card")).one()
        card.credit_limit = 3000.0
        s.add(card)
        s.commit()
        cov = account_cover(s, TODAY)
    c = next(a for a in cov["accounts"] if a["name"] == "Rewards Card")
    assert c["statement_utilisation"] > 0.3
    rows = [r for r in cov["sheet"] if r["to"] == "Rewards Card"]
    assert len([r for r in rows if "under 30%" in r["so_that"]]) == 1 and len(rows) == 1


# ---- the pictures (ROADMAP M12) ----------------------------------------------------------------------------------


def test_b_a_carried_card_owes_its_minimum_every_month_across_the_horizon():
    """'A carried card now owes its minimum every month across the horizon.' (docs/ROADMAP.md:185)"""
    household()
    snap = _b_snap()
    with Session(engine) as s:
        store = s.exec(select(Debt).where(Debt.name == "Store Card")).one()
    months = {c["date"][:7] for c in snap["card_payments"] if c["source"] == f"debt:{store.id}" and c["amount"] >= store.minimum}
    last = max(date.fromisoformat(p["date"]) for p in snap["projection"])
    want = set()
    d = date(TODAY.year, TODAY.month, 1)
    while d <= last:
        if date(d.year, d.month, store.due_day) >= TODAY and date(d.year, d.month, store.due_day) <= last:
            want.add(d.isoformat()[:7])
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    assert want and want <= months


def test_b_each_pictures_parts_add_up():
    """'each picture's parts add up' (docs/ROADMAP.md:274) and the ribbon's parts (docs/ROADMAP.md:185)."""
    household()
    with Session(engine) as s:
        out = paycheck_views(s, TODAY)
    for w in out["windows"]:
        if w.get("no_balance"):
            continue
        total = sum(p["amount"] for p in w["parts"])
        assert abs(w["parts_total"] - total) < 0.02
        assert abs(total - (w["inflow"] + w["short"])) < 0.05, (w["start"], total, w["inflow"], w["short"])


# ---- budgets, plan against what happened, goals (ROADMAP M22 to M24) ---------------------------------------------


def test_b_a_budget_is_per_paycheck_and_what_is_left_rolls_to_the_next_check():
    """'Category amounts per paycheck rather than per month; what is left in each between paydays; leftovers roll to
    the next check' (docs/ROADMAP.md:231)"""
    acc = _b_world(cash=1000.0, net=1500.0, next_date=date(2026, 10, 23))  # last payday Oct 9
    _b_tx(acc, date(2026, 10, 1), "Example Grocer", 50.0)  # the window before: Sep 25 to Oct 8
    _b_tx(acc, date(2026, 10, 12), "Example Grocer", 70.0)  # this window: Oct 9 to Oct 22
    with Session(engine) as s:
        save_budget(s, "Groceries", 200.0, "next")
        save_budget(s, "Dining", 100.0, "none")
        out = budget_plan(s, TODAY)
    g = next(i for i in out["items"] if i["category"] == "Groceries")
    d = next(i for i in out["items"] if i["category"] == "Dining")
    assert out["span"]["start"] == "2026-10-09" and out["span"]["end"] == "2026-10-22"
    assert g["carried"] == 150.0 and g["allowed"] == 350.0 and g["spent"] == 70.0 and g["left"] == 280.0
    assert d["carried"] == 0.0 and d["allowed"] == 100.0


def test_b_the_last_windows_plan_stands_beside_what_happened():
    """'After each payday, the last window's plan beside what happened' (docs/ROADMAP.md:232)"""
    acc = _b_world(cash=1500.0, net=1500.0)
    with Session(engine) as s:
        kept = record_plan(s, date(2026, 10, 15))
    assert kept and kept["start"] == "2026-10-16" and kept["paycheck"] == 1500.0
    _b_tx(acc, date(2026, 10, 16), "Example Employer", -1500.0, "Income", is_income=True)
    _b_tx(acc, date(2026, 10, 18), "Example Grocer", 80.0)
    with Session(engine) as s:
        out = recap(s, date(2026, 10, 20))
    w = out["windows"][0]
    assert w["plan"]["start"] == "2026-10-16" and w["came_in"] == 1500.0 and w["paycheck_word"] == "landed as planned"
    assert w["everyday"] == 80.0 and w["everyday_against_free"] == round(80.0 - kept["free"], 2)


def test_b_a_goal_says_the_date_it_is_reached_at_its_pace():
    """'A progress picture per goal with the date it is reached at the current pace' (docs/ROADMAP.md:233)"""
    _b_world()
    with Session(engine) as s:
        s.add(Goal(name="Example Fund", target=1000.0, funded=100.0, pace=100.0))
        s.commit()
    g = _b_snap()["goals"]["items"][0]
    # $900 left at $100 a check: the ninth check from Oct 16, every two weeks
    assert g["per_paycheck"] == 100.0 and g["done_by"] == (date(2026, 10, 16) + timedelta(days=14 * 8)).isoformat()


# ---- moving banks (ROADMAP M17) ----------------------------------------------------------------------------------


def _b_move(session: Session):
    from app.engine.moves import start_move

    old = session.exec(select(Account).where(Account.kind == "checking")).first()
    new = Account(name="Example Credit Union", kind="checking", institution="Example CU", balance=50.0, connector="manual")
    session.add(new)
    session.commit()
    session.refresh(new)
    return old, new, start_move(session, TODAY, source_id=old.id, target_id=new.id, target_name="", finish_by=None)


def test_b_the_money_through_a_move_adds_up_to_the_same_as_no_move():
    """'Test: the total in and out is the same as with no move.' (docs/ROADMAP.md:205)"""
    from app.engine.moves import move_plan, set_item

    household()
    with Session(engine) as s:
        _old, _new, move = _b_move(s)
        moved = move_plan(s, move, TODAY)
        for item in s.exec(select(MoveItem)).all():
            set_item(s, move, item, TODAY, decision="keep")
        kept = move_plan(s, move, TODAY)
    for a, b in zip(moved["windows"], kept["windows"], strict=True):
        assert round(a["source"]["out"] + a["target"]["out"], 2) == round(b["source"]["out"] + b["target"]["out"], 2)
        assert round(a["source"]["in"] + a["target"]["in"], 2) == round(b["source"]["in"] + b["target"]["in"], 2)


def test_b_an_advance_left_behind_still_repays_from_the_old_account():
    """'An advance left behind still repays from the old account, so its repayment stays in the money table.'
    (docs/ROADMAP.md:209)"""
    from app.engine.moves import move_plan

    household()
    with Session(engine) as s:
        _old, _new, move = _b_move(s)
        plan = move_plan(s, move, TODAY)
    window = next(w for w in plan["windows"] if w["start"] <= "2026-10-16" <= w["end"])
    assert any(x["name"].endswith("repays") for x in window["source"]["items"])


def test_b_the_old_account_can_close_after_forty_five_quiet_days():
    """'The old account can close once everything that moved is confirmed and nothing has landed on it for 45
    days.' (docs/ROADMAP.md:207)"""
    from app.engine.moves import move_plan

    household()
    with Session(engine) as s:
        _old, _new, move = _b_move(s)
        moving = s.exec(select(MoveItem).where(MoveItem.move_id == move.id, MoveItem.decision == "move")).all()
        assert moving
        for k, item in enumerate(moving):
            item.status, item.switched_on, item.confirmed_on = "confirmed", TODAY - timedelta(days=60), TODAY - timedelta(days=60)
            item.last_seen_new = TODAY - timedelta(days=50)
            item.last_seen_old = TODAY - timedelta(days=44) if k == 0 else TODAY - timedelta(days=70)
            s.add(item)
        s.commit()
        assert move_plan(s, move, TODAY)["close"]["ready"] is False  # 44 quiet days
        moving[0].last_seen_old = TODAY - timedelta(days=45)
        s.add(moving[0])
        s.commit()
        assert move_plan(s, move, TODAY)["close"]["ready"] is True


# ---- housing (ROADMAP M37) ---------------------------------------------------------------------------------------


def test_b_a_home_makes_the_rent_bill_every_screen_counts_once():
    """'It produces the rent bills every screen already counts, so housing is entered once' (docs/ROADMAP.md:258)"""
    from app.engine.housing import sync_bills

    household()
    with Session(engine) as s:
        before = _b_snap()["windows"]
        home = Home(name="Example Flat", kind="rent", due_day=1)
        s.add(home)
        s.commit()
        s.refresh(home)
        s.add(HomeCharge(home_id=home.id, name="Base rent", kind="rent", amount=1250.0))
        s.add(HomeCharge(home_id=home.id, name="Pet rent", kind="pet", amount=45.0))
        s.commit()
        sync_bills(s, home, TODAY)
        rents = s.exec(select(Bill).where(Bill.active == True, Bill.category == "Rent")).all()  # noqa: E712
    assert len(rents) == 1 and rents[0].amount == 1295.0 and rents[0].home_id == home.id  # the household's own rent became the home's
    after = _b_snap()["windows"]
    assert [w["bills"]["total"] for w in after] == [w["bills"]["total"] for w in before]


def test_b_a_scenario_verdict_carries_its_sources():
    """'For the chosen date: passes or not, the first date that passes, what has to be true, with sources on every
    figure.' (docs/ROADMAP.md:170)"""
    household()
    with Session(engine) as s:
        out = run_scenario(s, TODAY, {"move_date": "2026-12-01", "new_rent": 1500.0})
    assert out["verdict"] in ("passes", "short") and "snapshot:projection" in out["sources"]
    assert any(x.startswith("bill:") for x in out["sources"]) and out["old_rent"] == 1295.0 and out["monthly_change"] == 205.0
    assert "not a recommendation" in out["not_advice"]


def test_b_a_yearly_bill_has_a_sinking_fund_split_over_the_checks_before_it():
    """'sinking funds for yearly bills' (docs/ROADMAP.md:233)"""
    from app.engine.sinking import sinking_funds

    _b_world()
    with Session(engine) as s:
        s.add(Bill(name="Example Renewal", amount=600.0, cadence="yearly", due_day=15, anchor_date=date(2027, 1, 15)))
        s.commit()
        out = sinking_funds(s, TODAY)
    item = next(i for i in out["items"] if i["name"] == "Example Renewal")
    # checks Oct 16, Oct 30, Nov 13, Nov 27, Dec 11, Dec 25, Jan 8: seven before Jan 15
    assert item["paychecks_left"] == 7 and item["per_paycheck"] == round(600.0 / 7, 2) and item["due"] == "2027-01-15"


def test_b_each_switch_comes_after_the_new_paycheck_with_five_days_before_its_charge():
    """'each bill after its next charge on the old account and after the money is in the new one, with five days
    before its first charge on the new' (docs/ROADMAP.md:204)"""
    from app.engine.moves import move_plan

    household()
    with Session(engine) as s:
        _old, _new, move = _b_move(s)
        plan = move_plan(s, move, TODAY)
    first_new = date.fromisoformat(plan["first_new_payday"])
    checked = 0
    for ph in plan["phases"]:
        for i in ph["items"]:
            if i["kind"] in ("bill", "subscription") and i.get("switch_by"):
                after, by, eff = (date.fromisoformat(i[k]) for k in ("switch_after", "switch_by", "effective"))
                assert first_new <= after <= by and (eff - by).days == 5, i
                checked += 1
    assert checked


# === Group C: borrowing, payments, planned payments and imports =====================================================

C_TODAY = date(2026, 10, 14)


def _c_account(s: Session, name: str = "Example Checking", kind: str = "checking", balance: float | None = 2000.0, **kw) -> Account:
    a = Account(name=name, kind=kind, balance=balance, **kw)
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def _c_tx(s: Session, account: Account, on: date, merchant: str, amount: float, *, category: str = "Shopping", source: str = "manual", tag: str = "", **flags) -> Transaction:
    t = Transaction(account_id=account.id, date=on, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category, source=source,
                    row_hash=f"c|{merchant}|{on.isoformat()}|{amount}|{tag}", **flags)
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def _c_income(s: Session, account: Account, net: float = 2000.0, next_date: date = date(2026, 10, 16)) -> Income:
    i = Income(name="Example Employer", cadence="biweekly", expected_net=net, next_date=next_date, account_id=account.id)
    s.add(i)
    s.commit()
    return i


def _c_bill(s: Session, name: str, amount: float, due_day: int, **kw) -> Bill:
    b = Bill(name=name, amount=amount, due_day=due_day, **kw)
    s.add(b)
    s.commit()
    s.refresh(b)
    return b


def _c_status(s: Session, bill: Bill, due: date) -> str | None:
    p = s.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == due)).first()
    return p.status if p else None


def _c_fact(s: Session, kind: str, fields: dict, status: str = "pending") -> ExtractedFact:
    f = ExtractedFact(kind=kind, fields=json.dumps(fields), confidence=0.9, status=status)
    s.add(f)
    s.commit()
    s.refresh(f)
    return f


# --- README.md: the borrowing plan and "I paid it" ---------------------------------------------------------------


def _c_loan(id_: int, name: str, apr: float, payoff: float) -> dict:
    return {"id": id_, "name": name, "label": name, "apr": apr, "payoff_estimate": payoff, "interest_ahead": 10.0, "source": f"plan:{id_}",
            "payments": [{"date": f"2026-{m:02d}-20", "amount": round(payoff / 3, 2)} for m in (11, 12)] + [{"date": "2027-01-20", "amount": round(payoff / 3, 2)}]}


def test_c_the_borrowing_plan_weighs_both_orders_and_says_why(monkeypatch):
    """"The plan simulates paying the closest-to-done first against paying the highest rate first, and picks with
    its reasons shown." README.md:80. Both ways are worked out, the cheaper wins outside a near tie, and the choice
    is said with the interest it costs or saves."""
    with Session(engine) as s:
        for i, (name, apr, payments) in enumerate([("Example Desk", 10.0, 3), ("Example Bike", 30.0, 10)], start=1):
            sched = [{"date": (date(2026, 11, 20) + timedelta(days=30 * k)).isoformat(), "amount": 100.0 if i == 1 else 300.0} for k in range(payments)]
            s.add(InstallmentPlan(provider="Affirm", merchant=name, total=0, installment=sched[0]["amount"], count=payments, remaining=payments, next_date=date(2026, 11, 20),
                                  schedule=json.dumps(sched), apr=apr, active=True))
        s.commit()
        real = bp.simulate

        def costed(loans, extra, way, today):  # the finish-first way costs $300 more: no near tie
            out = real(loans, extra, way, today)
            if way == "finish_first" and extra == 0.0:
                out["interest"] += 300.0
            return out

        monkeypatch.setattr(bp, "simulate", costed)
        plan = bp.borrowing_plan(s, C_TODAY)
    assert set(plan["ways"]) == {"finish_first", "rate_first"}
    assert plan["chosen"] == "rate_first" and plan["order"][0]["name"] == "Example Bike"  # the highest rate leads
    why = next(st for st in plan["steps"] if st["kind"] == "extra")
    assert "highest rate first" in why["text"] and why["sources"]


def test_c_a_near_tie_finishes_the_small_loans_first(monkeypatch):
    """"both payoff orders simulated month by month; a near tie finishes the small loans first." docs/ROADMAP.md:288.
    The smallest-first way costing $20 more (within $25) is still the one chosen, and the order starts at the smallest."""
    with Session(engine) as s:
        for i, (name, apr, payments, each) in enumerate([("Example Desk", 10.0, 3, 50.0), ("Example Bike", 30.0, 10, 300.0)], start=1):
            sched = [{"date": (date(2026, 11, 20) + timedelta(days=30 * k)).isoformat(), "amount": each} for k in range(payments)]
            s.add(InstallmentPlan(provider="Affirm", merchant=name, total=0, installment=each, count=payments, remaining=payments, next_date=date(2026, 11, 20), schedule=json.dumps(sched), apr=apr, active=True))
        s.commit()
        real = bp.simulate

        def costed(loans, extra, way, today):
            out = real(loans, extra, way, today)
            if way == "finish_first" and extra == 0.0:
                out["interest"] = round(out["interest"] + 20.0, 2)
            return out

        monkeypatch.setattr(bp, "simulate", costed)
        plan = bp.borrowing_plan(s, C_TODAY)
    assert plan["chosen"] == "finish_first"
    assert [o["name"] for o in plan["order"]][0] == "Example Desk"  # the smallest owed first


def test_c_the_finish_first_way_pays_the_smallest_owed_first_month_by_month():
    """"both payoff orders simulated month by month." docs/ROADMAP.md:288. With money beyond the schedule, the
    smallest-first way ends the small loan sooner; the highest-rate way ends the dear one sooner."""
    small = {"id": 1, "payoff_estimate": 300.0, "apr": 5.0, "payments": [{"date": f"2027-{m:02d}-01", "amount": 50.0} for m in range(1, 7)]}
    dear = {"id": 2, "payoff_estimate": 1200.0, "apr": 30.0, "payments": [{"date": f"2027-{m:02d}-01", "amount": 100.0} for m in range(1, 13)]}
    first = bp.simulate([small, dear], 200.0, "finish_first", C_TODAY)
    rate = bp.simulate([small, dear], 200.0, "rate_first", C_TODAY)
    assert first["ended"][1] < rate["ended"][1] and rate["ended"][2] <= first["ended"][2]
    assert rate["interest"] <= first["interest"]


def test_c_the_room_is_safe_to_spend_less_the_usual_everyday_spending():
    """"the room beyond the schedule (safe-to-spend less the usual day-to-day spending)." docs/ROADMAP.md:288"""
    household()
    with Session(engine) as s:
        snap = finance_snapshot(s, C_TODAY)
        plan = bp.borrowing_plan(s, C_TODAY)
    per_day = snap["usual_pace"]["per_day"]
    expected = min(w["safe"] - per_day * ((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1) for w in snap["windows"])
    assert plan["room"]["least_after_a_payday"] == round(expected, 2) and plan["room"]["per_day"] == per_day
    assert plan["extra_per_month"] == (0.0 if expected <= 0 else float(int(expected * 0.5 / 25) * 25))


def test_c_i_paid_it_counts_now_and_the_banks_row_takes_its_place_never_twice():
    """"'I paid it' counts it now, and the bank's own record takes its place when it arrives, never twice." README.md:80"""
    with Session(engine) as s:
        chk = _c_account(s)
        plan = InstallmentPlan(provider="Affirm", merchant="Example Desk", total=200.0, installment=50.0, count=4, remaining=3, next_date=date(2026, 10, 20), cadence_days=30, active=True)
        s.add(plan)
        s.commit()
        s.refresh(plan)
        mark_installment_paid(s, plan, C_TODAY, C_TODAY)
        s.refresh(plan)
        assert plan.remaining == 2 and plan.next_date == date(2026, 11, 19)  # counted at once
        _c_tx(s, chk, date(2026, 10, 15), "Affirm", 50.0, category="Borrowing")  # the bank's row for the same payment
        refresh_plan_progress(s, date(2026, 10, 16))
        s.refresh(plan)
        assert plan.remaining == 2  # the row stood in for the owner's word, not as a second payment
        _c_tx(s, chk, date(2026, 11, 19), "Affirm", 50.0, category="Borrowing", tag="2")  # the next one does count
        refresh_plan_progress(s, date(2026, 11, 20))
        s.refresh(plan)
        assert plan.remaining == 1


def test_c_i_paid_it_on_a_scheduled_plan_counts_once_with_the_banks_row():
    """"'I paid it' counts it now, and the bank's own record takes its place when it arrives, never twice." README.md:80
    (a plan on the lender's own schedule)."""
    from app.parsers.affirm import parse_loan_confirmation

    with Session(engine) as s:
        chk = _c_account(s)
        fields = parse_loan_confirmation(CONFIRMATION, C_TODAY)
        plan = plan_from_fields(s, fields, None, None, C_TODAY)
        s.commit()
        mark_installment_paid(s, plan, date(2026, 10, 20), date(2026, 10, 20))
        s.refresh(plan)
        assert plan.remaining == 2
        _c_tx(s, chk, date(2026, 10, 22), "Affirm", 242.32, category="Borrowing")
        refresh_plan_progress(s, date(2026, 10, 23))
        s.refresh(plan)
        assert plan.remaining == 2 and plan.next_date == date(2026, 11, 23)


# --- docs/PLAN.md:134 and docs/ROADMAP.md:38: payment matching ----------------------------------------------------


def test_c_a_payment_is_found_by_name_and_amount_or_by_exact_amount_alone():
    """"Each bill period finds its payment by name and amount, or by exact amount alone." docs/ROADMAP.md:38"""
    with Session(engine) as s:
        chk = _c_account(s)
        named = _c_bill(s, "Example Power", 100.0, 5)
        exact = _c_bill(s, "Example Water", 64.0, 6)
        neither = _c_bill(s, "Example Phone", 90.0, 7)
        _c_tx(s, chk, date(2026, 10, 5), "Example Power", 108.0)  # by name, within the drift
        _c_tx(s, chk, date(2026, 10, 6), "CITY UTILITIES 0042", 64.0)  # another name, the exact amount
        _c_tx(s, chk, date(2026, 10, 7), "Example Grocer", 95.0)  # neither the name nor the amount
        match_payments(s, C_TODAY)
        assert _c_status(s, named, date(2026, 10, 5)) == "paid"
        assert _c_status(s, exact, date(2026, 10, 6)) == "paid"
        assert _c_status(s, neither, date(2026, 10, 7)) != "paid"


def test_c_unmatched_is_late_then_past_due_after_the_grace():
    """"Matched is paid; past the due day with no match is late; past the grace period is past due." docs/PLAN.md:134"""
    with Session(engine) as s:
        _c_account(s)
        plain = _c_bill(s, "Example Power", 100.0, 10)
        graced = _c_bill(s, "Example Internet", 80.0, 10, grace_days=10)
        match_payments(s, date(2026, 10, 12))
        assert _c_status(s, plain, date(2026, 10, 10)) == "late" and _c_status(s, graced, date(2026, 10, 10)) == "late"
        match_payments(s, date(2026, 10, 18))
        assert _c_status(s, plain, date(2026, 10, 10)) == "past_due" and _c_status(s, graced, date(2026, 10, 10)) == "late"
        match_payments(s, date(2026, 10, 21))
        assert _c_status(s, graced, date(2026, 10, 10)) == "past_due"
        assert _c_status(s, plain, date(2026, 11, 10)) == "scheduled"  # a period still to come


def test_c_a_confirmation_email_or_a_past_due_notice_alone_changes_no_period():
    """"Each bill period looks for its payment: a transaction ... or a confirmation email ... A past-due notice in mail
    marks the bill past due even without a match." docs/PLAN.md:134. Not built: the roadmap's M4.8
    (docs/ROADMAP.md:68) says marking the period from the notice is still left, and M2.5 (docs/ROADMAP.md:38) matches by
    transaction only. A payment confirmation is kept as a record; a past-due notice reaches the brief as critical but
    leaves the period as matching found it. The PLAN wording was corrected to say so."""
    with Session(engine) as s:
        _c_account(s)
        bill = _c_bill(s, "Example Power", 100.0, 13)
        _c_fact(s, "payment_confirmation", {"sender_name": "Example Power", "amount": 100.0, "message_date": "2026-10-13"}, status="auto")
        _c_fact(s, "past_due", {"sender_name": "Example Power", "name": "Example Power", "amount": 100.0, "message_date": "2026-10-14"})
        match_payments(s, C_TODAY)
        assert _c_status(s, bill, date(2026, 10, 13)) == "late"  # neither email paid it nor made it past due
        brief = finance_snapshot(s, C_TODAY)["brief"]
    assert any(b["severity"] == "critical" and "Past-due notice" in b["text"] for b in brief)


def test_c_payment_statuses_are_scheduled_paid_late_past_due_and_missed():
    """"Payment: a bill period settled or not: scheduled, paid ..., late, past due, missed." docs/PLAN.md:79. Missed is
    the owner's "this bill has stopped"."""
    with Session(engine) as s:
        chk = _c_account(s)
        bill = _c_bill(s, "Example Power", 100.0, 1)
        _c_tx(s, chk, date(2026, 9, 1), "Example Power", 100.0)
        match_payments(s, C_TODAY)
        seen = {p.due_date: p.status for p in s.exec(select(Payment).where(Payment.bill_id == bill.id)).all()}
        assert seen[date(2026, 9, 1)] == "paid" and seen[date(2026, 10, 1)] == "past_due" and seen[date(2026, 11, 1)] == "scheduled"
        p = s.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == date(2026, 10, 1))).one()
        answer_payment(s, p, "stopped", C_TODAY)
        s.refresh(p)
        assert p.status == "missed"
        late_bill = _c_bill(s, "Example Gym", 30.0, 12)
        match_payments(s, C_TODAY)
        assert _c_status(s, late_bill, date(2026, 10, 12)) == "late"


def test_c_each_unpaid_bill_is_asked_paid_not_yet_or_stopped():
    """"each bill with no payment seen asked plainly: paid, not yet, or stopped." docs/ROADMAP.md:271"""
    with Session(engine) as s:
        chk = _c_account(s)
        bills = [_c_bill(s, n, 100.0, 1) for n in ("Example Power", "Example Water", "Example Gym")]
        match_payments(s, C_TODAY)
        periods = [s.exec(select(Payment).where(Payment.bill_id == b.id, Payment.due_date == date(2026, 10, 1))).one() for b in bills]
        assert all(p.status == "past_due" for p in periods)
        row = _c_tx(s, chk, date(2026, 10, 3), "Paid by phone", 100.0)
        answer_payment(s, periods[0], "paid", C_TODAY, transaction_id=row.id)
        answer_payment(s, periods[1], "not_yet", C_TODAY)
        answer_payment(s, periods[2], "stopped", C_TODAY)
        for p in periods:
            s.refresh(p)
        assert [p.status for p in periods] == ["paid", "past_due", "missed"]
        assert periods[0].transaction_id == row.id and s.get(Bill, bills[2].id).ends_on == date(2026, 9, 30)
        with pytest.raises(ValueError):
            answer_payment(s, periods[1], "maybe", C_TODAY)


# --- docs/ROADMAP.md:271 (M44): alike items answered together -----------------------------------------------------


def test_c_alike_notices_answered_together_only_complete_ones_and_only_the_newest():
    """"Alike items answered together (only complete ones confirmed)." docs/ROADMAP.md:271; and the code's own
    "only a bill's newest notice counts ... a notice more than sixty days old never starts a new bill"
    (app/engine/needsyou.py:10; the roadmap's M62 at docs/ROADMAP.md:301 plans more, no done item claims it)."""
    with Session(engine) as s:
        newer = _c_fact(s, "bill_due", {"name": "Example Power", "amount": 120.0, "due_date": "2026-10-20"})
        older = _c_fact(s, "bill_due", {"name": "Example Power", "amount": 95.0, "due_date": "2026-09-20"})
        stale = _c_fact(s, "bill_due", {"name": "Example Gym", "amount": 30.0, "due_date": "2026-07-01"})
        partial = _c_fact(s, "bill_due", {"name": "Example Water", "due_date": "2026-10-25"})
        got = {st["id"]: st["do"] for st in plan_many(s, [older, newer, stale, partial], C_TODAY)["steps"]}
    assert got == {newer.id: "apply", older.id: "set_aside", stale.id: "set_aside", partial.id: "own_card"}


# --- docs/ROADMAP.md:36 (M2.2): card debts, planned payments, available balance -----------------------------------


def test_c_planned_payment_counts_from_its_date_nets_off_the_card_and_settles_once():
    """"Planned payments: money the owner says is leaving before the ledger sees it is counted from its date, netted
    off the card it goes to, and settles itself when the transaction posts." docs/ROADMAP.md:36"""
    with Session(engine) as s:
        chk = _c_account(s, balance=3000.0)
        card = _c_account(s, "Example Card", "card", 600.0)
        _c_income(s, chk)
        before = finance_snapshot(s, C_TODAY)
        card_before = sum(e["amount"] for e in before["card_payments"])
        assert card_before == pytest.approx(600.0, abs=0.01)
        s.add(PlannedPayment(name="Example Card payment", amount=600.0, date=date(2026, 10, 15), kind="card_payment", to_account_id=card.id))
        s.commit()
        assert planned_to_cards(s, C_TODAY) == {card.id: 600.0}
        ev = planned_events(s, C_TODAY)
        assert [(e["date"], e["amount"]) for e in ev] == [(date(2026, 10, 15), 600.0)]
        during = finance_snapshot(s, C_TODAY)
        assert sum(e["amount"] for e in during["card_payments"]) == 0.0  # netted off the card: the $600 leaves once
        assert during["safe_to_spend"]["amount"] == pytest.approx(before["safe_to_spend"]["amount"] - 600.0, abs=0.01)
        _c_tx(s, chk, date(2026, 10, 15), "Payment to Example Card", 600.0, category="Credit Card Payment", is_card_payment=True)
        _c_tx(s, card, date(2026, 10, 15), "Payment received", -600.0, category="Credit Card Payment", is_card_payment=True)
        assert settle_planned(s, date(2026, 10, 16)) == 1
        assert planned_events(s, date(2026, 10, 16)) == [] and planned_to_cards(s, date(2026, 10, 16)) == {}
        assert settle_planned(s, date(2026, 10, 16)) == 0  # the one row settles one plan


def test_c_every_card_account_carries_a_debt_row_and_spending_accounts_hold_available(owner):
    """"every card account carries a debt row ... Spending accounts hold their available balance."
    docs/ROADMAP.md:36"""
    import httpx

    from app.connectors import CONNECTORS
    from tests.test_banks import FakePlaid

    fake = FakePlaid()
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    try:
        owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
        link = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()["link"]
        with Session(engine) as s:
            accounts = {a.name: a for a in s.exec(select(Account)).all()}
            assert accounts["Everyday Checking"].balance == 300.0  # the available balance, not the current 312.45
            card = accounts["Rewards Card"]
            debt = s.exec(select(Debt).where(Debt.account_id == card.id)).one()
            assert debt.kind == "card" and debt.balance == card.balance == 842.1
            assert s.exec(select(Debt).where(Debt.account_id == accounts["Everyday Checking"].id)).first() is None
            rows = {t.source_ref: t for t in s.exec(select(Transaction)).all()}
            assert rows["t1"].amount > 0 and rows["t2"].amount < 0 and rows["t2"].is_income  # positive is money out
            assert rows["t3"].pending
        # "Pending charges shown as pending and replaced by the posted ones without counting twice." docs/ROADMAP.md:225
        owner.post(f"/api/v1/banks/{link['id']}/sync")
    finally:
        CONNECTORS["plaid"].client_factory = None
    with Session(engine) as s:
        streaming = s.exec(select(Transaction).where(Transaction.merchant == "Streaming Co")).all()
    assert [(t.source_ref, t.pending) for t in streaming] == [("t5", False)]


def test_c_fact_statuses_are_pending_confirmed_auto_and_rejected(owner):
    """"ExtractedFact: ... status (pending, confirmed, auto-confirmed, rejected)." docs/PLAN.md:85. A record-only
    kind is kept as auto, a notice waits as pending, the owner's answer confirms (or applies) or rejects it."""
    from app.connectors.mail import handle_message
    from app.mail.imap import parse_message
    from app.models import MailAccount
    from tests.test_affirm import _raw

    with Session(engine) as s:
        box = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="o", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
        s.add(box)
        s.commit()
        handle_message(s, box, "INBOX", 1, parse_message(_raw("Thank you for your payment", "We received your payment of $45.00.", "<p1@example.test>")), C_TODAY)
        handle_message(s, box, "INBOX", 2, parse_message(_raw("Your bill is ready", "Amount due $64.00. Due date October 30, 2026.", "<b1@example.test>")), C_TODAY)
        handle_message(s, box, "INBOX", 3, parse_message(_raw("Your bill is ready", "Amount due $30.00. Due date October 28, 2026.", "<b2@example.test>")), C_TODAY)
        s.commit()
        got = {f.kind: f.status for f in s.exec(select(ExtractedFact)).all()}
        bills = [f.id for f in s.exec(select(ExtractedFact).where(ExtractedFact.kind == "bill_due")).all()]
    assert got == {"payment_confirmation": "auto", "bill_due": "pending"}
    owner.post(f"/api/v1/facts/{bills[0]}/confirm", json={"apply": False})
    owner.post(f"/api/v1/facts/{bills[1]}/reject", json={})
    with Session(engine) as s:
        assert [s.get(ExtractedFact, i).status for i in bills] == ["confirmed", "rejected"]


# --- docs/PLAN.md:74: positive is money out --------------------------------------------------------------------------


def test_c_positive_is_money_out_in_the_household_and_in_files():
    """"amount (positive is money out)." docs/PLAN.md:74. The household's pay comes in negative and its bills go out
    positive; a bank's CSV (money in positive, out negative) is turned around on import."""
    from app.connectors.files import import_rows, parse_csv_rows

    household()
    with Session(engine) as s:
        pay = s.exec(select(Transaction).where(Transaction.is_income == True)).all()  # noqa: E712
        rent = s.exec(select(Transaction).where(Transaction.merchant == "Rent")).all()
        assert pay and all(t.amount < 0 for t in pay) and rent and all(t.amount > 0 for t in rent)
        rows = parse_csv_rows("Date,Description,Amount\n2026-10-10,EXAMPLE GROCER,-54.12\n2026-10-11,EXAMPLE EMPLOYER PAYROLL,2240.00\n")
        assert {r["description"]: r["amount"] for r in rows} == {"EXAMPLE GROCER": 54.12, "EXAMPLE EMPLOYER PAYROLL": -2240.0}
        import_rows(s, rows, source_file="bank.csv", account_name="Example Savings")
        got = {t.raw_name: t.amount for t in s.exec(select(Transaction).where(Transaction.source == "file")).all()}
    assert got == {"EXAMPLE GROCER": 54.12, "EXAMPLE EMPLOYER PAYROLL": -2240.0}


# --- docs/ROADMAP.md:37 (M2.3): Affirm's confirmation ---------------------------------------------------------------


def test_c_affirm_confirmation_becomes_one_plan_by_its_loan_id():
    """"Affirm's loan-confirmation email is read deterministically into a plan with the lender's own schedule ...
    and the loan ID, which keeps one email from making two plans; the ledger ticks payments off." docs/ROADMAP.md:37"""
    from app.parsers.affirm import parse_loan_confirmation

    with Session(engine) as s:
        chk = _c_account(s)
        fields = parse_loan_confirmation(CONFIRMATION, C_TODAY)
        first = plan_from_fields(s, fields, None, None, C_TODAY)
        s.commit()
        again = plan_from_fields(s, dict(fields), None, None, C_TODAY)
        s.commit()
        assert first.id == again.id and len(s.exec(select(InstallmentPlan)).all()) == 1
        assert [e["date"] for e in json.loads(first.schedule)] == ["2026-10-23", "2026-11-23", "2026-12-23"] and first.remaining == 3
        _c_tx(s, chk, date(2026, 10, 23), "Affirm", 242.32, category="Borrowing")
        refresh_plan_progress(s, date(2026, 10, 24))
        s.refresh(first)
        assert first.remaining == 2 and first.next_date == date(2026, 11, 23)


def test_c_a_plan_charged_to_a_card_rides_that_card():
    """"A plan knows the account that pays it; one charged to a card rides that card." docs/ROADMAP.md:37"""
    with Session(engine) as s:
        chk = _c_account(s, balance=3000.0)
        card = _c_account(s, "Example Card", "card", 0.0)
        _c_income(s, chk)
        plan = InstallmentPlan(provider="Affirm", merchant="Example Desk", total=200.0, installment=50.0, count=4, remaining=4, next_date=date(2026, 10, 20), cadence_days=30, active=True,
                               pay_from_account_id=card.id)
        s.add(plan)
        s.commit()
        snap = finance_snapshot(s, C_TODAY)
    window = snap["windows"][0]
    rides = [e for e in window["bills"]["items"] if e.get("source") == f"plan:{plan.id}"]
    assert rides and all(e.get("via_card") for e in rides)  # shown, but riding the card
    card_pay = [e for e in snap["card_payments"] if e["source"] == f"account:{card.id}"]
    assert card_pay and card_pay[0]["parts"]["bills"] == pytest.approx(50.0)


# --- docs/ROADMAP.md:39 (M2.4) and docs/PLAN.md:82: advances -------------------------------------------------------


def test_c_an_advance_pairs_with_its_repayment_and_one_still_out_is_due_on_payday():
    """"money in from an advance app paired with the repayment that covers it ... and the fee between them; one still
    out is due on the next payday and held back." docs/ROADMAP.md:39"""
    rows = [
        {"id": 1, "date": date(2026, 9, 25), "amount": -100.0, "provider": "Earnin"},
        {"id": 2, "date": date(2026, 10, 2), "amount": 105.0, "provider": "Earnin"},
        {"id": 3, "date": date(2026, 10, 10), "amount": -75.0, "provider": "Earnin"},
    ]
    got = pair_advances(rows, date(2026, 10, 16), C_TODAY, [date(2026, 10, 2), date(2026, 10, 16)])["pairs"]
    repaid = next(p for p in got if p["taken_on"] == date(2026, 9, 25))
    out = next(p for p in got if p["taken_on"] == date(2026, 10, 10))
    assert repaid["repaid"] and repaid["fee"] == 5.0 and repaid["repay_date"] == date(2026, 10, 2)
    assert not out["repaid"] and out["repay_date"] == date(2026, 10, 16)


def test_c_the_paycheck_plan_takes_the_advances_back_first():
    """"Advance: ... A Debt the paycheck plan subtracts first." docs/PLAN.md:82; "one still out is due on the next
    payday and held back." docs/ROADMAP.md:39"""
    household()
    with Session(engine) as s:
        owed = sum(a.amount + a.fee for a in s.exec(select(Advance).where(Advance.repaid == False)).all() if a.repay_date >= C_TODAY)  # noqa: E712
        snap = finance_snapshot(s, C_TODAY)
    assert owed > 0
    first = snap["windows"][0]
    assert first["start"] == "2026-10-16" and first["advances"]["total"] == pytest.approx(owed)
    assert snap["safe_to_spend"]["parts"]["advances_on_payday"] == pytest.approx(owed)
    assert list(first).index("advances") < list(first).index("bills")  # the plan's order: advances, then bills


def test_c_borrowing_rows_are_filed_under_borrowing_and_never_spending():
    """"Every borrowing row is filed under its own Borrowing category by rule and never counted as spending."
    docs/ROADMAP.md:39"""
    with Session(engine) as s:
        chk = _c_account(s)
        _c_tx(s, chk, date(2026, 10, 1), "Example Grocer", 40.0)
        base = usual_pace(s, C_TODAY, set())["per_day"]
        _c_tx(s, chk, date(2026, 10, 2), "EARNIN ADVANCE", -100.0, category="Transfer")
        _c_tx(s, chk, date(2026, 10, 9), "EARNIN REPAYMENT", 105.0, category="Shopping")
        _c_tx(s, chk, date(2026, 10, 5), "AFFIRM PAYMENT", 50.0, category="Shopping")
        sync_borrowing(s, C_TODAY)
        cats = {t.merchant: t.category for t in s.exec(select(Transaction)).all()}
        assert cats["EARNIN REPAYMENT"] == cats["AFFIRM PAYMENT"] == cats["EARNIN ADVANCE"] == BORROWING_CATEGORY
        assert usual_pace(s, C_TODAY, set())["per_day"] == base
        adv = s.exec(select(Advance)).one()
        assert (adv.amount, adv.fee, adv.repaid) == (100.0, 5.0, True)


# --- docs/ROADMAP.md:234 (M25): payoff with dates, interest saved, the fee per $100 ---------------------------------


def test_c_snowball_and_avalanche_with_dates_and_interest_saved():
    """"Snowball or avalanche with dates and interest saved; advance apps ... with the fee per $100." docs/ROADMAP.md:234"""
    with Session(engine) as s:
        a = _c_account(s, "Example Card", "card", 3000.0)
        b = _c_account(s, "Example Store Card", "card", 500.0)
        for acc, apr, minimum in ((a, 25.0, 120.0), (b, 10.0, 40.0)):
            debt = s.exec(select(Debt).where(Debt.account_id == acc.id)).first() or Debt(name=acc.name, kind="card", balance=acc.balance, account_id=acc.id)
            debt.apr, debt.minimum = apr, minimum
            s.add(debt)
        s.add(Advance(provider="Earnin", amount=200.0, fee=7.0, taken_on=date(2026, 10, 1), repay_date=date(2026, 10, 15), repaid=True))
        s.commit()
        plan = payoff_plan(s, C_TODAY, 100.0)
    for way in ("avalanche", "snowball"):
        w = plan[way]
        assert w["debt_free"] and all(d["paid_on"] for d in w["debts"]) and w["interest_saved"] > 0
        assert w["debt_free"] < plan["minimums_only"]["debt_free"]
    assert plan["avalanche"]["interest"] <= plan["snowball"]["interest"]
    snow = {d["name"]: d["paid_on"] for d in plan["snowball"]["debts"]}
    assert snow["Example Store Card"] < snow["Example Card"]  # the smallest balance goes first
    earnin = next(r for r in plan["advances"] if r["provider"] == "Earnin")
    assert earnin["fee_per_100"] == 3.5


# --- docs/ROADMAP.md:273 (M46): the smallest advance per paycheck --------------------------------------------------


def test_c_the_way_off_advances_borrows_only_each_paychecks_shortfall():
    """"The smallest advance per paycheck, stepping down to none, with the fees it saves." docs/ROADMAP.md:273"""
    household()
    with Session(engine) as s:
        chk = s.exec(select(Account).where(Account.kind == "checking")).first()
        chk.balance = -150.0
        s.add(chk)
        s.commit()
        plan = exit_plan(s, C_TODAY)
    steps = plan["steps"]
    assert any(st["borrow"] > 0 for st in steps)
    for st in steps:
        short = -(st["left_with_no_new_advance"] - st["pays_back"])
        if st["borrow"] > 0:
            assert st["borrow"] >= short and st["borrow"] - 5 < short  # only as large as the shortfall, in fives
        else:
            assert short <= 0
    if plan["state"] == "exits":
        i = [st["payday"] for st in steps].index(plan["free_from"])
        assert all(st["borrow"] == 0 for st in steps[i:])
    assert plan["fees_saved"] == round(max(0.0, plan["fees_if_unchanged"] - plan["fees_on_plan"]), 2)


# --- docs/ROADMAP.md:275 (M48): credit health ---------------------------------------------------------------------


def test_c_each_cards_share_of_its_limit_and_what_to_pay_before_the_statement():
    """"Each card's share of its limit on its statement day, what to pay before then, and the on-time record."
    docs/ROADMAP.md:275"""
    with Session(engine) as s:
        card = _c_account(s, "Example Card", "card", 900.0)
        debt = Debt(name="Example Card", kind="card", balance=900.0, account_id=card.id, credit_limit=2000.0, statement_day=20, apr=24.0)
        s.add(debt)
        s.commit()
        got = credit_health(s, C_TODAY)
    c = got["cards"][0]
    assert c["share"] == 0.45 and c["status"] == "crit" and c["pay_for_30"] == 300.0 and c["pay_for_10"] == 700.0
    assert c["next_statement"] == "2026-10-20" and "Tue Oct 20" in c["word"]


# --- docs/ROADMAP.md:287 (M53): lender mail older than the lender's list is history ---------------------------------


def test_c_lender_mail_older_than_the_lenders_own_list_is_history():
    """"lender mail older than the lender's own latest list of loans (a statement, or the account read on screen) is
    history." docs/ROADMAP.md:287"""
    with Session(engine) as s:
        set_setting(s, "affirm", {"period_end": "2026-10-01"})
        old = {"provider": "Affirm", "notice": "approved", "merchant": "Example Desk", "installment": 50.0, "count": 4, "message_date": "2026-09-20"}
        new = {**old, "message_date": "2026-10-05"}
        assert older_than_listing(s, old) and not older_than_listing(s, new)
        with pytest.raises(ValueError):
            apply_lender_notice(s, old, C_TODAY)
        assert s.exec(select(InstallmentPlan)).all() == []
        made = apply_lender_notice(s, new, C_TODAY)
        assert made.merchant == "Example Desk" and made.dates_estimated


def test_c_a_plan_keeps_the_day_its_schedule_was_last_confirmed():
    """"a plan keeps the day its schedule was last confirmed." docs/ROADMAP.md:287. An email older than that day
    changes nothing but what the plan lacks."""
    from app.parsers.affirm import parse_loan_confirmation

    with Session(engine) as s:
        _c_account(s)
        fields = parse_loan_confirmation(CONFIRMATION, C_TODAY)
        plan = plan_from_fields(s, fields, None, None, C_TODAY)
        plan.schedule_as_of = date(2026, 10, 12)
        newer = [{"date": "2026-10-25", "amount": 242.32}, {"date": "2026-11-25", "amount": 242.32}, {"date": "2026-12-25", "amount": 242.30}]
        plan.schedule = json.dumps(newer)
        s.add(plan)
        s.commit()
        old = {**fields, "message_date": "2026-10-01"}
        plan_from_fields(s, old, None, None, C_TODAY)
        s.commit()
        s.refresh(plan)
        assert json.loads(plan.schedule) == newer and plan.schedule_as_of == date(2026, 10, 12)


# --- docs/ROADMAP.md:289 (M55): suggestions that learn --------------------------------------------------------------


def test_c_answers_move_a_kinds_weight():
    """"Done, Not useful and Wrong move each kind of suggestion's weight." docs/ROADMAP.md:289"""
    assert weight({"done": 1}) > weight({}) > weight({"not_useful": 1}) > weight({"wrong": 1})
    with Session(engine) as s:
        answer(s, "watch-1", "done", C_TODAY)
        answer(s, "datacheck-1", "wrong", C_TODAY)
        shown, _ = rank(s, [{"id": "datacheck-2", "severity": "info"}, {"id": "watch-2", "severity": "info"}], C_TODAY)
    assert [x["id"] for x in shown] == ["watch-2", "datacheck-2"]  # same severity: the heavier kind first


def test_c_a_kind_turned_down_again_and_again_is_quieted_one_tap_back_urgent_never():
    """"a kind turned down again and again is quieted, one tap from coming back; urgent ones never are."
    docs/ROADMAP.md:289"""
    with Session(engine) as s:
        for n in range(3):
            answer(s, f"watch-{n}", "not_useful", C_TODAY)
        items = [{"id": "watch-10", "severity": "info"}, {"id": "watch-11", "severity": "critical"}]
        shown, quiet = rank(s, items, C_TODAY)
        assert [x["id"] for x in quiet] == ["watch-10"] and [x["id"] for x in shown] == ["watch-11"]
        bring_back(s, "watch")
        shown, quiet = rank(s, items, C_TODAY)
    assert quiet == [] and {x["id"] for x in shown} == {"watch-10", "watch-11"}


# --- docs/ROADMAP.md:225 (M19) and :226 (M20): pending, alerts, pairs ---------------------------------------------


def test_c_a_purchase_alert_is_shown_until_the_bank_posts_it_and_never_taken_off():
    """"bank alert emails as provisional transactions within fifteen minutes, matched when the bank posts."
    docs/ROADMAP.md:225"""
    with Session(engine) as s:
        chk = _c_account(s)
        _c_income(s, chk)
        f = _c_fact(s, "bank_alert", {"alert": "purchase", "amount": 42.5, "merchant": "Example Grocer", "message_date": "2026-10-13"}, status="auto")
        f.created_at = f.created_at.replace(year=2026, month=10, day=13)
        s.add(f)
        s.commit()
        before = finance_snapshot(s, C_TODAY)
        assert [i["amount"] for i in before["in_flight"]["mail"]["items"]] == [42.5] and before["in_flight"]["mail"]["counted"] is False
        _c_tx(s, chk, date(2026, 10, 14), "Example Grocer", 42.5)
        after = finance_snapshot(s, C_TODAY)
    assert after["in_flight"]["mail"]["items"] == []


def test_c_a_card_payment_pairs_with_the_cards_credit_and_a_twin_from_two_sources_waits():
    """"a checking payment paired with the card's credit; the same transaction through two sources merged; a review
    list for unsure pairs." docs/ROADMAP.md:226"""
    with Session(engine) as s:
        chk = _c_account(s)
        card = _c_account(s, "Example Card", "card", 500.0)
        out = _c_tx(s, chk, date(2026, 10, 10), "ONLINE PAYMENT TO CARD", 300.0)
        into = _c_tx(s, card, date(2026, 10, 11), "PAYMENT THANK YOU", -300.0)
        bank = _c_tx(s, chk, date(2026, 10, 12), "Example Grocer", 61.25, source="plaid")
        export = _c_tx(s, chk, date(2026, 10, 12), "Example Grocer", 61.25, source="rocketmoney", tag="rm")
        find_pairs(s, C_TODAY)
        pairs = {p.kind: p for p in s.exec(select(TransactionPair)).all()}
        assert pairs["card_payment"].status == "auto"
        s.refresh(out)
        s.refresh(into)
        assert out.is_card_payment and into.is_card_payment
        dup = pairs["duplicate"]
        assert dup.status == "pending" and {dup.a_id, dup.b_id} == {bank.id, export.id}
        decide(s, dup.id, "yes")
        s.refresh(export)
        s.refresh(bank)
        assert export.is_ignored and not bank.is_ignored  # merged: one counts


# --- docs/ROADMAP.md:193 (M18.1) and :194 (M18.2): no doubles across sources, undo -------------------------------


def _c_row(on: date, name: str, amount: float, fitid: str = "") -> dict:
    return {"date": on, "amount": amount, "description": name, "merchant": name, "category": "Uncategorized", "account": "", "fitid": fitid, "posted": None}


def test_c_a_file_row_the_account_already_holds_is_not_added_again():
    """"A file's row the account already holds ... on the same amount within three days by the same name or on the
    same day, is not added again; each held row stands for one file row once." docs/ROADMAP.md:193"""
    from app.connectors.files import import_rows

    with Session(engine) as s:
        chk = _c_account(s, connector="plaid")
        _c_tx(s, chk, date(2026, 10, 10), "Example Grocer", 54.12, source="plaid")
        _c_tx(s, chk, date(2026, 10, 5), "Example Pharmacy", 12.0, source="plaid")
        _c_tx(s, chk, date(2026, 10, 1), "Example Cafe", 8.0, source="plaid")
        rows = [
            _c_row(date(2026, 10, 12), "EXAMPLE GROCER #42", 54.12),  # same name, two days off: held
            _c_row(date(2026, 10, 12), "EXAMPLE GROCER #42", 54.12),  # the same again in the file: one held row stands for one
            _c_row(date(2026, 10, 5), "POS 991 STORE", 12.0),  # another name, the same day: held
            _c_row(date(2026, 10, 3), "ANOTHER SHOP", 8.0),  # another name, two days off: a different purchase
        ]
        got = import_rows(s, rows, source_file="stmt.csv", account_name=chk.name, account_id=chk.id)
        assert got.inserted == 2 and got.details["merged"] == 2
        added = sorted((t.merchant, t.date) for t in s.exec(select(Transaction).where(Transaction.source == "file")).all())
    assert added == [("ANOTHER SHOP", date(2026, 10, 3)), ("EXAMPLE GROCER #42", date(2026, 10, 12))]


def test_c_an_ofx_statement_finds_its_account_by_the_last_four():
    """"an OFX statement finds its account by the last four digits." docs/ROADMAP.md:193"""
    from app.connectors.files import import_rows, parse_ofx

    ofx = ("OFXHEADER:100\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>000099991234<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST>"
           "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261009<TRNAMT>-20.00<FITID>F1<NAME>EXAMPLE CAFE</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
    with Session(engine) as s:
        live = _c_account(s, "Everyday Checking", connector="plaid", external_id="acc-chk", last4_enc=crypto.encrypt("1234"))
        parsed = parse_ofx(ofx)
        import_rows(s, parsed["rows"], source_file="stmt.ofx", account_name="Some Bank 1234", account_number=parsed["account_id"])
        t = s.exec(select(Transaction).where(Transaction.source == "file")).one()
        assert t.account_id == live.id and t.amount == 20.0
        assert len(s.exec(select(Account)).all()) == 1


def test_c_undo_removes_exactly_the_import_and_keeps_everything_since():
    """"Undo removes exactly that and leaves everything since." docs/ROADMAP.md:194"""
    from app import importlog
    from app.connectors.files import import_rows

    with Session(engine) as s:
        chk = _c_account(s)
        before = _c_tx(s, chk, date(2026, 10, 1), "Example Cafe", 8.0)
        first = import_rows(s, [_c_row(date(2026, 10, 5), "FILE ONE", 10.0), _c_row(date(2026, 10, 6), "FILE TWO", 11.0)], source_file="a.csv", account_name=chk.name, account_id=chk.id)
        second = import_rows(s, [_c_row(date(2026, 10, 7), "FILE THREE", 12.0)], source_file="b.csv", account_name=chk.name, account_id=chk.id)
        since = _c_tx(s, chk, date(2026, 10, 8), "Example Grocer", 30.0)
        done = importlog.undo(s, first.details["import_id"], C_TODAY)
        left = sorted(t.merchant for t in s.exec(select(Transaction)).all())
    assert done["removed"] == 2 and left == sorted(["Example Cafe", "FILE THREE", "Example Grocer"])
    assert before.id and since.id and second.inserted == 1


# === Group D: facts from mail, the brief, paystubs, cadences, files, the outlook, exports, the business and taxes ====



# --- helpers ----------------------------------------------------------------------------------------------------------


def _d_mailbox(session: Session) -> MailAccount:
    account = MailAccount(label="Example", kind="imap", host="mail.example.test", port=993, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), folders="INBOX")
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _d_message(sender: str, subject: str, body: str, when: str = "Fri, 09 Oct 2026 11:12:00 -0500") -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "owner@example.test", subject, when
    m["Message-ID"] = f"<{abs(hash((sender, subject, body)))}@example.test>"
    m.set_content(body)
    return bytes(m)


def _d_handle(session: Session, account: MailAccount, uid: int, sender: str, subject: str, body: str) -> ExtractedFact | None:
    from app.connectors.mail import handle_message
    from app.mail.imap import parse_message

    handle_message(session, account, "INBOX", uid, parse_message(_d_message(sender, subject, body)), TODAY)
    session.commit()
    return session.exec(select(ExtractedFact).order_by(ExtractedFact.id.desc())).first()


def _d_checking(session: Session, balance: float | None = 2000.0) -> Account:
    a = Account(name="Example Checking", kind="checking", balance=balance, balance_at=datetime(2026, 10, 14, 6, tzinfo=timezone.utc))
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _d_stub(session: Session, pay: date, net: float, *, hours: float = 80.0, gross: float | None = None, deductions: list | None = None) -> Paystub:
    from app.engine.paystubs import record_paystub

    fields = {"pay_date": pay.isoformat(), "net": net, "gross": gross if gross is not None else round(net * 1.3, 2), "hours": hours,
              "deductions": deductions if deductions is not None else [{"name": "Federal income tax", "amount": round(net * 0.1, 2)}]}
    return record_paystub(session, fields, "file:example", None, pay)


def _d_rows(dates: list[date], merchant: str, amount: float, category: str = "Bills & Utilities") -> list[dict]:
    return [{"date": d, "merchant": merchant, "amount": amount, "category": category} for d in dates]


def _d_every_text_value() -> str:
    out = []
    with engine.connect() as c:
        for t in SQLModel.metadata.sorted_tables:
            for row in c.execute(text(f'SELECT * FROM "{t.name}"')).all():
                out.append(" ".join(str(v) for v in row))
    return "\n".join(out)


# --- PLAN section 2: facts that count on their own, and facts that wait --------------------------------------------


def test_d_facts_from_a_twice_confirmed_sender_still_wait_for_the_owner():
    """Claim: "A fact from a sender whose previous facts the owner confirmed twice is confirmed automatically, with a
    note." docs/PLAN.md:37. Earned auto-confirm per sender is not built (ROADMAP M4.7 lists it as Left; M62 plans it),
    so the document was reworded; this guards what is true: a third bill notice from that sender still waits."""
    with Session(engine) as s:
        box = _d_mailbox(s)
        sender = "Example Power <billing@power.example>"
        for uid in (1, 2):
            f = _d_handle(s, box, uid, sender, "Your bill is ready", f"Amount due: $14{uid}.00\nDue date: October 2{uid}, 2026")
            assert f is not None and f.kind == "bill_due" and f.status == "pending"
            f.status = "confirmed"
            s.add(f)
            s.commit()
        third = _d_handle(s, box, 3, sender, "Your bill is ready", "Amount due: $143.00\nDue date: November 20, 2026")
        assert third is not None and third.kind == "bill_due" and third.status == "pending"


def test_d_informational_mail_counts_without_a_tap_and_a_new_kind_waits():
    """Claim: "Only a new sender, a new kind of fact, or an amount outside its usual range waits for the owner."
    docs/PLAN.md:37. A payment confirmation is informational and counts at once; a bill notice from a new sender waits."""
    with Session(engine) as s:
        box = _d_mailbox(s)
        paid = _d_handle(s, box, 1, "Example Phone <billing@phone.example>", "Thank you for your payment", "We received your payment of $96.00.")
        assert paid is not None and paid.kind == "payment_confirmation" and paid.status == "auto" and paid.resolved_by == "rule"
        bill = _d_handle(s, box, 2, "Example Water <billing@water.example>", "Your bill is ready", "Amount due: $64.00\nDue date: November 2, 2026")
        assert bill is not None and bill.kind == "bill_due" and bill.status == "pending"


def test_d_a_pending_bill_fact_does_not_change_the_plan():
    """Claim: "Bills, statements, paystubs ... become facts that wait for your yes." README.md:165; facts are pending
    until confirmed, docs/PLAN.md:85. A pending notice for a large new bill leaves safe to spend where it was."""
    from app.engine.snapshot import finance_snapshot

    household()
    with Session(engine) as s:
        before = finance_snapshot(s, TODAY)["safe_to_spend"]["amount"]
        s.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Surprise", "amount": 900.0, "due_date": "2026-10-15"}), confidence=0.9, status="pending"))
        s.commit()
        snap = finance_snapshot(s, TODAY)
        assert snap["safe_to_spend"]["amount"] == before
        assert not any("Surprise" in e["name"] for e in snap["due_before_payday"]["items"])
        assert snap["pending"]["facts"] >= 1


# --- the brief ------------------------------------------------------------------------------------------------------


def test_d_the_brief_says_what_clears_before_payday_and_warns_when_it_is_not_covered():
    """Claim: the brief says "what is due in three days and not yet funded". docs/PLAN.md:38 and :158. The brief says
    what clears before payday (a wider window), and warns when checking does not hold it with the buffer."""
    from app.engine.snapshot import finance_snapshot

    cid = household()
    with Session(engine) as s:
        line = next(b for b in finance_snapshot(s, TODAY)["brief"] if "clears before payday" in b["text"])
        a = s.get(Account, cid)
        a.balance = 1.0
        s.add(a)
        s.commit()
        short = next(b for b in finance_snapshot(s, TODAY)["brief"] if "clears before payday" in b["text"])
        assert short["severity"] == "warning"
        assert line["text"] != ""


def test_d_the_brief_says_what_is_past_due():
    """Claim: the brief says "what is past due". docs/PLAN.md:38."""
    from app.engine.snapshot import finance_snapshot

    from app.engine.payments import match_payments

    cid = household()
    with Session(engine) as s:
        s.add(Bill(name="Example Club Dues", amount=50.0, due_day=20, pay_from_account_id=cid, created_on=date(2026, 8, 1)))
        s.commit()
        match_payments(s, TODAY)
        snap = finance_snapshot(s, TODAY)
        assert any(p["name"] == "Example Club Dues" for p in snap["payments"]["past_due"])
        assert any(b["severity"] == "critical" and "past due with no payment seen" in b["text"] for b in snap["brief"])


def test_d_the_brief_says_a_short_paystub_with_the_lines_that_moved():
    """Claim: "a paystub that does not match the expected net" in the brief, docs/PLAN.md:38; "flagged with the lines
    that moved", docs/PLAN.md:136. Before the fix no line that moved was named anywhere."""
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as s:
        _d_checking(s)
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16)))
        s.commit()
        lines = [{"name": "Federal income tax", "amount": 260.0}, {"name": "Dental", "amount": 12.0}]
        _d_stub(s, date(2026, 9, 4), 2000.0, gross=2600.0, deductions=lines)
        _d_stub(s, date(2026, 9, 18), 2000.0, gross=2600.0, deductions=lines)
        _d_stub(s, date(2026, 10, 2), 1800.0, hours=72.0, gross=2340.0, deductions=[{"name": "Federal income tax", "amount": 234.0}, {"name": "Dental", "amount": 12.0}])
        line = next(b for b in finance_snapshot(s, TODAY)["brief"] if "paystub" in b["text"])
        assert "under the usual net" in line["text"] and "hours 80 to 72" in line["text"], line["text"]


def test_d_the_brief_says_sends_waiting_for_approval():
    """Claim: the brief carries "approvals waiting". docs/PLAN.md:158."""
    from app.engine.snapshot import finance_snapshot

    household()
    with Session(engine) as s:
        s.add(Approval(to="billing@example.test", subject="Move my due date", body="Please", reason="test"))
        s.commit()
        assert any("wait" in b["text"] and "approval" in b["text"] for b in finance_snapshot(s, TODAY)["brief"])


def test_d_a_price_change_notice_reaches_the_brief_only_as_mail_to_look_at():
    """Claim: the brief says "a price increase" and "a connector that stopped working". docs/PLAN.md:38. The brief carries
    a price-change notice only as mail that needs a look (the watch job and the app's own health push tell the rest),
    so the document was reworded; this guards the line that is there."""
    from app.engine.snapshot import finance_snapshot

    household()
    with Session(engine) as s:
        s.add(ExtractedFact(kind="price_change", fields=json.dumps({"name": "Example Streaming", "amount": 19.0}), confidence=0.9, status="pending"))
        s.commit()
        assert any("from the mail need a look" in b["text"] for b in finance_snapshot(s, TODAY)["brief"])


# --- paystubs -------------------------------------------------------------------------------------------------------


def test_d_a_paystub_is_matched_to_its_deposit():
    """Claim: "Each paystub is matched to its deposit." docs/PLAN.md:136."""
    with Session(engine) as s:
        a = _d_checking(s)
        t = Transaction(account_id=a.id, date=date(2026, 10, 2), merchant="Example Employer Payroll", amount=-2000.0, category="Income", is_income=True, row_hash="d-pay-1")
        s.add(t)
        s.commit()
        stub = _d_stub(s, date(2026, 10, 2), 2000.0)
        assert stub.transaction_id == t.id


def test_d_expected_net_comes_from_the_last_few_stubs_and_the_stub_keeps_the_difference():
    """Claim: "The expected net (from the last few paystubs) is compared with the actual", docs/PLAN.md:136; a paystub
    carries "the difference between expected and actual net", docs/PLAN.md:77."""
    from app.engine.paystubs import expected_net

    with Session(engine) as s:
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16)))
        s.commit()
        for d, net in ((date(2026, 8, 21), 2000.0), (date(2026, 9, 4), 2010.0), (date(2026, 9, 18), 2020.0)):
            _d_stub(s, d, net)
        assert expected_net(s, date(2026, 10, 2)) == 2010.0  # the median of the stubs, not the income's 1500
        stub = _d_stub(s, date(2026, 10, 2), 1950.0)
        assert stub.expected_net == 2010.0 and stub.difference == -60.0


def test_d_a_difference_beyond_the_tolerance_is_flagged_with_the_lines_that_moved():
    """Claim: "a difference beyond a tolerance is flagged with the lines that moved". docs/PLAN.md:136. On the household
    the Aug 21 stub (8 fewer hours) is short and names the hours; a stub a few dollars under is not flagged."""
    from app.engine.paystubs import paystubs_overview, verdict

    household()
    with Session(engine) as s:
        rows = {r["pay_date"]: r for r in paystubs_overview(s)["recent"]}
        short = rows["2026-08-21"]
        assert short["verdict"]["short"] is True
        moved = {m["name"]: m for m in short["moved"]}
        assert moved["Hours"]["before"] == 80.0 and moved["Hours"]["after"] == 72.0 and "Gross pay" in moved and "Dental" not in moved
        assert rows["2026-09-18"]["moved"] == [] and verdict(-5.0)["short"] is False


def test_d_confirming_stubs_keeps_the_paychecks_expected_net_current():
    """Claim: "confirming records the stub ... and keeps the paycheck's expected net current". docs/ROADMAP.md:40."""
    with Session(engine) as s:
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16)))
        s.commit()
        for d in (date(2026, 9, 4), date(2026, 9, 18), date(2026, 10, 2)):
            _d_stub(s, d, 2150.0)
        assert s.exec(select(Income)).one().expected_net == 2150.0


# --- cadences -------------------------------------------------------------------------------------------------------


def _d_semimonthly_dates() -> list[date]:
    from app.engine.cadence import business_day_on_or_before, month_end

    out = []
    for m in range(4, 11):
        out += [business_day_on_or_before(date(2026, m, 15)), business_day_on_or_before(month_end(date(2026, m, 1)))]
    return [d for d in out if d <= TODAY]


def test_d_semimonthly_pay_is_its_own_cadence_told_from_biweekly():
    """Claim: "semi-monthly pay (the 15th and the last day, moved before a weekend) is its own cadence, told from
    biweekly by the day pattern". docs/ROADMAP.md:27."""
    from app.engine.recurring import detect

    semi = detect(_d_rows(_d_semimonthly_dates(), "Example Payroll", -2000.0, "Income"), TODAY, income=True)
    assert [f["cadence"] for f in semi] == ["semimonthly"]
    fridays = [date(2026, 10, 2) - timedelta(days=14 * k) for k in range(12)]
    bi = detect(_d_rows(fridays, "Example Payroll", -2000.0, "Income"), TODAY, income=True)
    assert [f["cadence"] for f in bi] == ["biweekly"]


def test_d_a_semimonthly_paycheck_keeps_a_usual_net_per_position_of_the_month():
    """Claim: "a semi-monthly paycheck keeps a usual net per position of the month". docs/ROADMAP.md:27."""
    from app.engine.paystubs import expected_net

    with Session(engine) as s:
        s.add(Income(name="Example Employer", cadence="semimonthly", expected_net=1900.0, next_date=date(2026, 10, 15)))
        s.commit()
        _d_stub(s, date(2026, 9, 15), 1900.0)
        _d_stub(s, date(2026, 9, 30), 2300.0)
        inc = s.exec(select(Income)).one()
        assert inc.expected_net == 1900.0 and inc.expected_net_end == 2300.0
        assert expected_net(s, date(2026, 10, 30)) == 2300.0 and expected_net(s, date(2026, 10, 15)) == 1900.0


@pytest.mark.parametrize("cadence,step_days,count", [("weekly", 7, 8), ("biweekly", 14, 8), ("monthly", None, 8), ("quarterly", None, 3), ("yearly", None, 2)])
def test_d_each_cadence_the_plan_names_is_detected(cadence: str, step_days: int | None, count: int):
    """Claim: "RecurringSeries: merchant, cadence (weekly, biweekly, monthly, quarterly, yearly)". docs/PLAN.md:75."""
    from app.engine.cadence import _add_months
    from app.engine.recurring import detect

    last = date(2026, 10, 5)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}
    dates = sorted(last - timedelta(days=step_days * k) if step_days else _add_months(last, -months[cadence] * k) for k in range(count))
    found = detect(_d_rows(dates, f"Example {cadence}", 25.0), TODAY)
    assert [f["cadence"] for f in found] == [cadence]


def test_d_detection_links_series_to_the_bills_and_paycheck_already_known():
    """Claim: "series linked to bills and incomes already known". docs/ROADMAP.md:26."""
    household()
    with Session(engine) as s:
        income = s.exec(select(Income)).one()
        assert income.series_id is not None and s.get(RecurringSeries, income.series_id).kind == "income"
        rent = s.exec(select(Bill).where(Bill.name == "Rent")).one()
        assert rent.series_id is not None and s.get(RecurringSeries, rent.series_id).merchant_key


# --- files ----------------------------------------------------------------------------------------------------------


def test_d_a_banks_csv_has_its_columns_and_sign_worked_out():
    """Claim: "any bank's or vendor's CSV with its columns worked out and its sign convention detected". docs/ROADMAP.md:24;
    README.md:164. A file with money out negative and one with money out positive both land with money out positive."""
    from app.connectors.files import parse_csv_rows, sniff_csv

    bank = "Posting Date,Payee,Amount\n10/02/2026,EXAMPLE PAYROLL DEPOSIT,2240.00\n10/03/2026,EXAMPLE COFFEE,-4.50\n"
    ledger = "Date,Description,Amount\n2026-10-02,EXAMPLE PAYROLL DEPOSIT,-2240.00\n2026-10-03,EXAMPLE COFFEE,4.50\n"
    split = "Date,Description,Debit,Credit\n2026-10-02,EXAMPLE PAYROLL DEPOSIT,,2240.00\n2026-10-03,EXAMPLE COFFEE,4.50,\n"
    for text_ in (bank, ledger, split):
        sniffed = sniff_csv(text_)
        assert sniffed["ready"] and sniffed["mapping"]["date"] and sniffed["mapping"]["description"]
        got = {r["merchant"]: r["amount"] for r in parse_csv_rows(text_)}
        assert got == {"EXAMPLE PAYROLL DEPOSIT": -2240.0, "EXAMPLE COFFEE": 4.5}, (text_, got)


def test_d_ofx_qfx_and_qbo_statements_are_read():
    """Claim: "OFX, QFX and QBO statements, transactions and investment positions". docs/ROADMAP.md:24; README.md:164."""
    from app.connectors.files import detect

    ofx = ("OFXHEADER:100\nDATA:OFXSGML\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>0000004321<ACCTTYPE>CHECKING</BANKACCTFROM>"
           "<BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261003<TRNAMT>-4.50<FITID>A1<NAME>EXAMPLE COFFEE</STMTTRN>"
           "<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261002<TRNAMT>2240.00<FITID>A2<NAME>EXAMPLE PAYROLL</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
    for name in ("statement.ofx", "statement.qfx", "statement.qbo"):  # the three share one format; the reader goes by content
        got = detect(ofx)
        assert got["kind"] == "ofx" and got["ready"], name
        assert {r["merchant"]: r["amount"] for r in got["rows"]} == {"EXAMPLE COFFEE": 4.5, "EXAMPLE PAYROLL": -2240.0}
        assert got["account_id"] == "0000004321"


def test_d_a_bills_list_is_told_apart_and_lands_on_bills():
    """Claim: "a bills list file (name, amount, how often, due day or next date) is told apart from a transactions
    export and lands on the Bills register". docs/ROADMAP.md:24."""
    from app.connectors.files import detect, import_bills, parse_bills_rows

    bills = "Name,Amount,Frequency,Due day\nExample Internet,85.00,Monthly,24\nExample Insurance,138.00,Monthly,3\n"
    assert detect(bills)["kind"] == "bills"
    assert detect("Date,Description,Amount\n2026-10-02,EXAMPLE,4.50\n")["kind"] == "csv"
    with Session(engine) as s:
        import_bills(s, parse_bills_rows(bills), source_file="bills.csv", today=TODAY)
        got = {b.name: (b.amount, b.due_day) for b in s.exec(select(Bill)).all()}
        assert got == {"Example Internet": (85.0, 24), "Example Insurance": (138.0, 3)}


def test_d_a_typed_balance_stands_until_the_bank_sends_something_newer():
    """Claim: "Type what its app says, and the plan uses it until the bank sends something newer." README.md:163. (The
    far-figure check is the page's, frontend/src/slip.ts, tested there.)"""
    from app.connectors.banks import tell_balance, upsert_account

    with Session(engine) as s:
        a = upsert_account(s, provider="plaid", external_id="d-acct", name="Example Checking", institution="Example Bank", kind="checking", balance=500.0,
                           as_of=datetime.now(timezone.utc) - timedelta(hours=6))
        s.commit()
        tell_balance(s, a, 420.0)
        upsert_account(s, provider="plaid", external_id="d-acct", name="Example Checking", institution="Example Bank", kind="checking", balance=500.0,
                       as_of=datetime.now(timezone.utc) - timedelta(hours=5))
        s.commit()
        s.refresh(a)
        assert a.balance == 420.0  # the bank's data is older than the typed figure
        upsert_account(s, provider="plaid", external_id="d-acct", name="Example Checking", institution="Example Bank", kind="checking", balance=455.0,
                       as_of=datetime.now(timezone.utc) + timedelta(minutes=1))
        s.commit()
        s.refresh(a)
        assert a.balance == 455.0


# --- the outlook and where to put it --------------------------------------------------------------------------------


def test_d_compounding_matches_the_textbook():
    """Claim: "Tests: compounding matches the textbook". docs/ROADMAP.md:81."""
    from app.engine.projections import grow

    assert grow(1000.0, 0.06, 0.0, 12)[-1] == pytest.approx(1060.0, abs=1e-6)
    r = 1.06 ** (1 / 12) - 1
    assert grow(0.0, 0.06, 100.0, 24)[-1] == pytest.approx(100.0 * ((1 + r) ** 24 - 1) / r, rel=1e-9)


def test_d_scenarios_keep_their_order_and_todays_dollars_sit_below_nominal():
    """Claim: "scenarios keep their order, today's dollars sit below nominal". docs/ROADMAP.md:81."""
    from app.engine.projections import outlook

    household()
    with Session(engine) as s:
        s.add(Account(name="Example Brokerage", kind="investment", balance=5000.0))
        s.commit()
        inv = outlook(s, TODAY)["investments"]
        by = {sc["id"]: sc["years"] for sc in inv["scenarios"]}
        for y in range(len(by["mid"])):
            assert by["low"][y]["balance"] <= by["mid"][y]["balance"] <= by["high"][y]["balance"]
            assert by["mid"][y]["real"] < by["mid"][y]["balance"]


def test_d_where_extra_money_does_the_most_is_ranked_with_sources_and_steps():
    """Claim: "ranked by what is kept after tax, with whether each is guaranteed and reachable and the step that
    starts it"; "the ranking carries sources and steps". docs/ROADMAP.md:81; README.md:173."""
    from app.engine.where_to_put import where_to_put

    household()
    with Session(engine) as s:
        got = where_to_put(s, TODAY, 50.0)
        kept = [o["after_tax"] for o in got["options"]]
        assert kept == sorted(kept, reverse=True) and got["best"] == got["options"][0]["id"]
        assert all(o.get("action") and o.get("link") and isinstance(o["guaranteed"], bool) and o["reachable"] for o in got["options"])
        assert got["sources"] and "not" in got["not_advice"].lower()


# --- exports --------------------------------------------------------------------------------------------------------


def test_d_exports_come_in_twelve_formats():
    """Claim: "Twelve formats (CSV zip, JSON, ICS, Beancount, Ledger, QIF, OFX; Markdown, one-file HTML, PDF, Word,
    Excel". docs/ROADMAP.md:186; "exports in twelve formats", README.md:180."""
    from app.exports.data import FORMATS

    assert sorted(FORMATS) == sorted(["csv", "json", "ics", "beancount", "ledger", "qif", "ofx", "md", "html", "pdf", "docx", "xlsx"])


def test_d_a_beancount_file_balances():
    """Claim: "a Beancount file balances". docs/ROADMAP.md:186."""
    from app.exports.data import beancount

    household()
    with Session(engine) as s:
        out = beancount(s, TODAY).decode()
    blocks = [b for b in out.split("\n\n") if " * " in b]
    assert len(blocks) > 100
    for b in blocks:
        amounts = [float(m) for m in re.findall(r"  (-?\d+\.\d{2}) USD", b)]
        assert len(amounts) == 2 and round(sum(amounts), 2) == 0.0, b
    opened = set(re.findall(r" open (\S+) USD", out))
    used = set(re.findall(r"^  (\S+)  ", out, re.MULTILINE))
    assert used <= opened


def test_d_every_report_export_ends_with_its_sources_and_the_not_advice_line():
    """Claim: "Every export from the engine's results, with sources and the not-advice line". docs/ROADMAP.md:186."""
    from app.exports.model import build_report
    from app.exports.render import html, markdown

    household()
    with Session(engine) as s:
        report = build_report(s, TODAY)
    assert "advice" in report["not_advice"].lower() and report["sources"]
    for out in (markdown(report), html(report)):
        assert report["not_advice"].split(".")[0] in out and "Sources:" in out


# --- assets, the tax pack, the business ----------------------------------------------------------------------------


def test_d_a_car_depreciates():
    """Claim: "depreciation for a car". docs/ROADMAP.md:254."""
    from app.engine.assets import save_asset, value_now

    with Session(engine) as s:
        car = save_asset(s, asset_id=None, name="Example Car", kind="car", value=20000.0, value_on=date(2025, 10, 14))
        assert car.yearly_change is not None and car.yearly_change < 0
        assert value_now(car, TODAY) == pytest.approx(20000.0 * (1 + car.yearly_change) ** (365 / 365.25), rel=1e-6)
        assert value_now(car, TODAY) < 20000.0


def test_d_the_tax_pack_sums_the_paystubs():
    """Claim: "the paystubs summed (gross, net and each deduction)". docs/ROADMAP.md:277."""
    from app.engine.taxpack import tax_pack

    household()
    with Session(engine) as s:
        stubs = s.exec(select(Paystub)).all()
        pack = tax_pack(s, TODAY, 2026)["paystubs"]
        assert pack["count"] == len(stubs)
        assert pack["gross"] == round(sum(p.gross for p in stubs), 2) and pack["net"] == round(sum(p.net for p in stubs), 2)
        federal = next(d for d in pack["deductions"] if d["name"] == "Federal income tax")
        assert federal["total"] == round(sum(d["amount"] for p in stubs for d in json.loads(p.deductions) if d["name"] == "Federal income tax"), 2)


def test_d_the_ein_is_never_kept(owner: TestClient):
    """Claim: "The EIN is never kept, only whether one exists." docs/ROADMAP.md:290."""
    r = owner.post("/api/v1/business", json={"name": "Example Studio", "kind": "sole_prop", "state": "TX", "ein_on_file": True, "ein": "12-3456789", "EIN": "123456789"})
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        assert s.exec(select(Business)).one().ein_on_file is True
    dump = _d_every_text_value()
    assert "3456789" not in dump


def test_d_mileage_is_at_the_rate_for_the_day_driven():
    """Claim: "the mileage log at the rate for the day driven". docs/ROADMAP.md:290."""
    from app.engine.business import books, trips_year
    from app.engine.taxrules import mileage_rate

    with Session(engine) as s:
        b = Business(name="Example Studio")
        s.add(b)
        s.commit()
        s.add(Trip(business_id=b.id, day=date(2025, 12, 30), miles=100.0, purpose="Client"))
        s.add(Trip(business_id=b.id, day=date(2026, 3, 2), miles=100.0, purpose="Client"))
        s.add(Trip(business_id=b.id, day=date(2026, 9, 2), miles=100.0, purpose="Client"))
        s.commit()
        assert mileage_rate(date(2025, 12, 30)) != mileage_rate(date(2026, 3, 2))
        y26 = trips_year(s, b.id, 2026)
        assert [t["cents"] for t in y26["trips"]] == [mileage_rate(date(2026, 3, 2)), mileage_rate(date(2026, 9, 2))]
        assert y26["amount"] == round(100 * (mileage_rate(date(2026, 3, 2)) + mileage_rate(date(2026, 9, 2))) / 100, 2)
        car = next(line for line in books(s, b, 2025)["lines"] if line["key"] == "car")
        assert car["amount"] == round(100 * mileage_rate(date(2025, 12, 30)) / 100, 2)


def test_d_self_employment_tax_is_on_the_profit():
    """Claim: "the books by the lines of Schedule C with self-employment tax". docs/ROADMAP.md:290."""
    from app.engine.business import books

    with Session(engine) as s:
        b = Business(name="Example Studio")
        s.add(b)
        s.commit()
        a = _d_checking(s)
        s.add(Transaction(account_id=a.id, date=date(2026, 3, 1), merchant="Example Client", amount=-10000.0, category="Income", business_id=b.id, row_hash="d-biz-in"))
        s.add(Transaction(account_id=a.id, date=date(2026, 3, 5), merchant="Example Supplies", amount=1000.0, category="Shopping", business_id=b.id, tax_line="supplies", row_hash="d-biz-out"))
        s.commit()
        got = books(s, b, 2026)
        assert got["income"] == 10000.0 and got["costs"] == 1000.0 and got["profit"] == 9000.0
        assert got["se_tax"] == round(9000.0 * 0.9235 * 0.153, 2)
        small = Business(name="Example Side")
        s.add(small)
        s.commit()
        s.add(Transaction(account_id=a.id, date=date(2026, 4, 1), merchant="Example Client", amount=-420.0, category="Income", business_id=small.id, row_hash="d-biz-small"))
        s.commit()
        assert books(s, small, 2026)["se_tax"] == 0.0  # 92.35% of $420 is under the $400 floor


def test_d_the_tax_year_weighs_the_standard_deduction_names_the_amend_window_and_what_never_counts():
    """Claim: "the tax center (dates, standard deduction against itemizing, ... the amended-return window)";
    docs/ROADMAP.md:290; Taxes shows "what never counts", README.md:116."""
    from app.engine.taxcenter import tax_year
    from app.engine.taxrules import STANDARD_DEDUCTION

    household()
    with Session(engine) as s:
        got = tax_year(s, TODAY, 2025)
    assert got["standard_deduction"] == STANDARD_DEDUCTION[2025]["single"]
    said = " ".join(x for x in got.get("said", []) if x) if isinstance(got.get("said"), list) else json.dumps(got)
    assert ("larger" if got["standard_deduction"] >= got["itemizable"] else "smaller") in said
    assert got["amend"]["years"] == [2022, 2023, 2024]
    assert got["never"] and all(n.get("source") for n in got["never"])


# --- receipts, reports, documents -----------------------------------------------------------------------------------


def test_d_return_and_warranty_dates_become_reminders():
    """Claim: "return and warranty dates as reminders". docs/ROADMAP.md:246."""
    from app.engine.reminders import due_today

    household()
    with Session(engine) as s:
        s.add(Document(title="Example Blender", kind="receipt", filename="r.txt", content_type="text/plain", size=1, sha256="d-r1", merchant="Example Store",
                       amount=60.0, return_by=TODAY + timedelta(days=2), warranty_until=TODAY + timedelta(days=10)))
        s.commit()
        tags = [(r["tag"], r["title"]) for r in due_today(s, TODAY)]
    assert any(tag == "receipt" and "return" in title.lower() for tag, title in tags)
    assert any(tag == "receipt" and "warranty" in title.lower() for tag, title in tags)


def test_d_a_year_in_review_and_tax_time_totals():
    """Claim: "a year in review and tax-time totals". docs/ROADMAP.md:247."""
    from app.engine.custom_reports import tax_totals, year_in_review

    household()
    with Session(engine) as s:
        a = s.exec(select(Account).where(Account.kind == "checking")).first()
        s.add(Transaction(account_id=a.id, date=date(2026, 5, 2), merchant="Example Clinic", amount=120.0, category="Medical", row_hash="d-med"))
        s.commit()
        review = year_in_review(s, TODAY, 2026)
        income = sum(-t.amount for t in s.exec(select(Transaction).where(Transaction.is_income == True, Transaction.date >= date(2026, 1, 1), Transaction.date <= TODAY)).all())  # noqa: E712
        assert review["income"] == round(income, 2) and review["kept"] == round(review["income"] - review["spent"], 2)
        totals = tax_totals(s, TODAY, 2026)
        assert any(c["name"] == "Medical" and c["total"] >= 120.0 for c in totals["categories"])


def test_d_a_documents_terms_are_offered_as_a_fact_and_never_applied_on_their_own():
    """Claim: "the terms it states ... are offered as facts to confirm, never applied on their own". docs/ROADMAP.md:259."""
    from app.engine.documents import save_document

    lease = ("RESIDENTIAL LEASE AGREEMENT\nMonthly rent of $1,250.00 is due on or before the 1st day of each month.\n"
             "A late fee of $75.00 is charged after the 3rd.\nLease ends on March 31, 2027. Resident must give 60 days written notice to vacate.\n")
    with Session(engine) as s:
        home = Home(name="Example Flat")
        s.add(home)
        s.commit()
        bills_before = len(s.exec(select(Bill)).all())
        save_document(s, lease.encode(), filename="lease.txt", title="Lease", kind="lease", attach_to=f"home:{home.id}")
        fact = s.exec(select(ExtractedFact).where(ExtractedFact.kind == "document_terms")).one()
        assert fact.status == "pending" and json.loads(fact.fields)["terms"]["rent"]["value"] == 1250.0
        s.refresh(home)
        assert home.lease_ends_on is None
        assert s.exec(select(HomeCharge).where(HomeCharge.home_id == home.id)).all() == []
        assert len(s.exec(select(Bill)).all()) == bills_before


def test_d_an_export_captured_from_mail_runs_detection_and_matching_after():
    """Claim: "the file imported, with detection and payment matching run after". docs/ROADMAP.md:69."""
    from app.connectors.mail import capture_export

    lines = ["Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible,Transaction Tags"]
    for k in range(6):
        d = (date(2026, 5, 3) + timedelta(days=0)).replace(month=5 + k)
        lines.append(f"{d.isoformat()},{d.isoformat()},Cash,Example Checking,1234,Example Bank,EXAMPLE GYM,,30.00,GYM,Health & Fitness,,,,")
    csv = "\n".join(lines) + "\n"
    with Session(engine) as s:
        result = capture_export(s, "https://files.example/export.csv", lambda url: csv.encode())
        s.commit()
        assert result["imported"] == 6
        assert s.exec(select(RecurringSeries)).all(), "no detection ran after the captured export"


# === The lead: the Oct 16 paycheck on Oct 29 =========================================================================


def test_the_last_payday_before_today_closes_the_history_and_the_plan_starts_at_the_next():
    """The native app on Oct 29 saw history end at the Oct 2 check and the plan start Oct 30, with no Oct 16 check.
    The engine's boundary is right: the seeded ledger ends Oct 13 (the household is dated Oct 14), so the Oct 16 window
    held no deposit and a window with nothing in is left out. With the deposit there, history ends with the Oct 16
    window (still open) and the plan starts at the next payday, with no day in neither."""
    from app.engine.paychecks import paycheck_views

    household()
    late = date(2026, 10, 29)
    with Session(engine) as s:
        assert paycheck_views(s, late)["history"][-1]["start"] == "2026-10-02"  # the fixture: no Oct 16 deposit
        chk = s.exec(select(Account).where(Account.kind == "checking")).first()
        s.add(Transaction(account_id=chk.id, date=date(2026, 10, 16), merchant="Example Employer Payroll", raw_name="EXAMPLE EMPLOYER PAYROLL", amount=-2240.0,
                          category="Income", is_income=True, row_hash="sweep21-oct16-pay"))
        s.commit()
        views = paycheck_views(s, late)
    last = views["history"][-1]
    assert (last["start"], last["end"], last["paycheck"], last["partial"]) == ("2026-10-16", "2026-10-29", 2240.0, True)
    assert views["windows"][0]["start"] == "2026-10-30"
    assert date.fromisoformat(last["end"]) + timedelta(days=1) == date.fromisoformat(views["windows"][0]["start"])


# === One fact, one value: what the frontend's sweep found on the household at Oct 29 ===============================

LATE = date(2026, 10, 29)


def test_one_value_the_brief_says_how_many_are_past_due_as_needs_you_does():
    """The brief named the first three of seven past-due periods with no count, so the brief, Today, Mission control
    and Chat said three where Needs you said seven."""
    from app.engine.needsyou import past_due_waiting

    household(LATE)
    with Session(engine) as s:
        snap = finance_snapshot(s, LATE)
        n = len(past_due_waiting(s, LATE))
    line = next(b["text"] for b in snap["brief"] if "past due with no payment seen" in b["text"])
    assert n == snap["pending"]["past_due"] == 7
    assert line.startswith("7 bill periods past due") and line.rstrip(".").endswith("and 4 more")


def test_one_value_cover_saving_and_the_plan_agree_on_what_a_check_leaves():
    """Cover said the Oct 30 check leaves +$42.88 while Saving said -$431.12: Cover left out the check's set-aside for
    the window after, though its own sheet says to keep it. One definition now: what the check leaves after its own
    window's bills, the set-aside, the cushion and the usual everyday spending; the plan's window `safe` is the same
    before everyday spending."""
    household(LATE)
    with Session(engine) as s:
        snap = finance_snapshot(s, LATE)
        sv = saving_plan(s, LATE, snap=snap)
        cover = account_cover(s, LATE, saving=sv)
    spend = next(a for a in cover["accounts"] if a.get("deposit"))
    w = spend["windows"][1]
    first = next(p for p in sv["per_check"] if p["date"] == w["start"])
    plan = next(x for x in snap["windows"] if x["start"] == w["start"])
    assert w["set_aside"] == plan["set_aside"]["total"] > 0
    assert w["left"] == first["left"]
    assert abs((w["left"] + w["everyday"]) - plan["safe"]) < 0.01
    assert spend["status"] != "good"  # it is short of what it keeps for the window after
    with Session(engine) as s:  # and on a day with usual spending still to come before payday
        sv = saving_plan(s, TODAY)
        early = next(a for a in account_cover(s, TODAY, saving=sv)["accounts"] if a.get("deposit"))["windows"][1]
    assert early["left"] == next(p for p in sv["per_check"] if p["date"] == early["start"])["left"]


def test_one_value_cover_knows_the_card_payment_is_past_due():
    """Cover marked the card good with "Next payment $45.00 on Oct 29" while Needs you listed that minimum as past due
    since Oct 25."""
    household(LATE)
    with Session(engine) as s:
        cover = account_cover(s, LATE)
    card = next(a for a in cover["accounts"] if a["name"] == "Rewards Card")
    assert card["status"] == "crit" and card["headline"].startswith("Past due: $45.00 was due Oct 25")


def test_one_value_a_plan_has_one_monthly_figure_and_running_plans_are_counted():
    """The Laptop plan was $81.43 a month on Debts, while Borrowing counted installments at $0 a month with "0 plans
    running" (plans on the register that no lender-named ledger row shows were left out)."""
    from app.engine.borrowing import borrowing_overview
    from app.engine.borrowing_plan import borrowing_plan
    from app.engine.reports import debt_mountain

    household(LATE)
    with Session(engine) as s:
        debts = {d["source"]: d["minimum"] for d in debt_mountain(s, LATE)["items"] if d["kind"] == "installment" or str(d["source"]).startswith("plan:")}
        over = borrowing_overview(s, LATE)
        plan = borrowing_plan(s, LATE)
    running = {f"plan:{i['plan_id']}": i["per_month"] for i in over["installments"] if i["active"] and i["plan_id"]}
    assert running and running == {k: v for k, v in debts.items() if k in running}
    assert over["cost"]["installments_month"] == round(sum(running.values()), 2)
    assert all(l["per_month"] == debts[l["source"]] for l in plan["loans"])


def test_one_value_money_is_signed_before_the_dollar_in_the_borrowing_plan():
    """The borrowing plan printed "$-383.00"; the recap writes -$383.00."""
    from app.engine.borrowing_plan import borrowing_plan

    household(LATE)
    with Session(engine) as s:
        steps = borrowing_plan(s, LATE)["steps"]
    text = " ".join(x["text"] for x in steps)
    assert "$-" not in text and "-$383.00" in text


def test_one_value_a_rung_date_in_the_printed_report_carries_its_year():
    """The printable report gave later rung dates with no year, though a rung can be years away."""
    from app.exports.model import build_report

    household()
    with Session(engine) as s:
        report = build_report(s, TODAY)
    rows = [r for sec in report["sections"] if sec.get("table") for r in sec["table"].get("rows", []) if str(r[0])[:2] in ("1.", "2.", "3.", "4.", "5.")]
    dated = [r[-1] for r in rows if r[-1] not in ("-", "not within six years") and not str(r[-1]).startswith("$")]
    assert dated and all(re.search(r", 20\d\d$", d) for d in dated), dated


def test_one_value_billing_dates_are_not_judged_from_zero_without_a_balance():
    """GET /api/v1/bills/suggestions worked its low points out from $0 when no balance was known; it says no balance,
    as the snapshot does."""
    with Session(engine) as s:
        chk = Account(name="Example Checking", kind="checking", balance=None)
        s.add(chk)
        s.commit()
        s.add(Bill(name="Example Power", amount=180.0, due_day=3, pay_from_account_id=chk.id, movable=True))
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16), account_id=chk.id))
        s.commit()
        out = billing_date_suggestions(s, TODAY)
    assert out["no_balance"] is True and out["baseline"]["low"] is None and out["suggestions"] == [] and "No balance" in out["note"]
