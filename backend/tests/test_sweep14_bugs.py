"""Sweep 14: crash recovery. Every job, and every multi-step write the owner's taps make, is failed at each of its
commits in turn (the Nth commit raises, as a full disk, a lost connection or a bug would) and then run again
cleanly; the end state must be the state of a run that never failed: no half-applied fact, no orphan row, nothing
twice, the import log matching the ledger, every restore point whole or absent. Then each violation the drill
found, reduced to its own test. The drill's tools are in test_sweep14_bugs_common.py; process death is
test_sweep14_bugs_kill.py, concurrency test_sweep14_bugs_concurrency.py. Synthetic data only."""

from __future__ import annotations

import json
import os
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_sweep14_bugs_common import clean_world, TODAY, Box, Crash, build_world, commits, diff, dump, make_due, orphans, put_back, snapshot  # noqa: F401 - clean_world is a fixture

from app import restore
from app.db import engine
from app.jobs import scheduler
from app.models import ApiKey, Document, ExtractedFact, ImportBatch, Income, InstallmentPlan, Payment, Paystub, Setting
from app.store import get_setting

pytestmark = pytest.mark.usefixtures("clean_world")

JOBS = ("bank_sync", "mail_sync", "import_watch", "detect_recurring", "match_payments", "data_check", "balance_history", "recap_keep", "moves_verify", "prune_job_runs")


def _job(name: str) -> str:
    try:
        return scheduler.jobs.specs[name].func() or ""
    except Exception as error:  # noqa: BLE001 - a failed run is the point here
        return f"failed: {type(error).__name__}: {error}"


def _call(owner: TestClient, method: str, path: str, body: dict | None = None) -> int | str:
    try:
        return owner.request(method, path, json=body).status_code
    except Crash:
        return "crash"


def test_every_job_failed_at_any_commit_ends_as_a_clean_run_once_run_again(owner: TestClient, tmp_path):
    build_world(owner, bills=False)  # the bills list read twice is its own finding below
    base = snapshot(tmp_path / "base")
    findings, points = [], 0
    for name in JOBS:
        put_back(base)
        with commits() as clean:
            _job(name)
        make_due()
        reference = dump()
        for k in range(1, clean.count + 1):
            put_back(base)
            with commits(fail_at=k) as c:
                said = _job(name)
            assert c.fired, (name, k)
            make_due()  # a later hour: a link that failed waits its interval, as it should
            again = _job(name)
            points += 1
            for line in diff(reference, dump()) + orphans():
                findings.append(f"{name}, commit {k} failed ({said[:80]}), then {again[:60]}: {line[:300]}")
    assert points >= 40
    assert not findings, "\n".join(findings)


def test_every_tap_failed_at_any_commit_is_whole_or_absent_or_whole_once_tapped_again(owner: TestClient, tmp_path):
    """A confirm and an undo are tapped again after a failure (they say 409 or 400 once done); "I paid it" is not
    (a second tap is a second payment), so a failure must leave it all done or not done at all."""
    build_world(owner, bills=False)
    scheduler.mail_sync()
    scheduler.import_watch()
    make_due()
    scheduler.bank_sync()
    with Session(engine) as session:
        session.add(ExtractedFact(kind="installment", fields=json.dumps({"sender_name": "Affirm", "provider": "Affirm", "merchant": "Example Typed"}), status="pending", confidence=0.9))
        session.commit()
        facts = [(f.id, f.kind) for f in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()]
        batches = [b.id for b in session.exec(select(ImportBatch)).all()]
        plans = [p.id for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()]  # noqa: E712
    assert {k for _, k in facts} >= {"installment", "paystub"} and batches and plans
    retried = [(f"confirm {k}", "POST", f"/api/v1/facts/{i}/confirm", {"amount": 40.0, "remaining": 4} if k == "installment" else {}) for i, k in facts]
    retried += [("undo", "POST", f"/api/v1/imports/{i}/undo", None) for i in batches]
    once = [("I paid it", "POST", f"/api/v1/borrowing/plans/{i}/paid", {}) for i in plans]
    base = snapshot(tmp_path / "base")
    before = dump()
    findings = []
    for label, method, path, body in retried + once:
        put_back(base)
        with commits(skip=("audit",)) as clean:  # the activity log's own commit comes after the write it records
            assert _call(owner, method, path, body) == 200, label
        reference = dump()
        for k in range(1, clean.count + 1):
            put_back(base)
            with commits(fail_at=k, skip=("audit",)):
                _call(owner, method, path, body)
            if (label, method, path, body) in once:
                now = dump()
                if diff(before, now) and diff(reference, now):
                    findings.append(f"{label} {path}, commit {k} failed: half done: {diff(reference, now)[:3]}")
                continue
            _call(owner, method, path, body)
            for line in diff(reference, dump()) + orphans():
                findings.append(f"{label} {path}, commit {k} failed, tapped again: {line[:300]}")
    assert not findings, "\n".join(findings)


# ---- the violations, one test each ---------------------------------------------------------------------------


def _statement() -> bytes:
    from test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    return pdf_of(affirm_statement_lines(date(2026, 10, 31)))


def test_a_statement_failing_half_way_never_marks_interest_added_that_was_not(owner: TestClient):
    """borrowing.apply_affirm_statement: the "interest of this loan and period is added" mark was committed loan by
    loan, before the interest itself; a failure at the next loan's commit kept the mark and lost the interest, and
    every later read skipped it as added. Each loan's interest paid stayed $2.00 short for good."""
    from app.engine.borrowing import statement_fact_from_file

    with Session(engine) as session, commits() as counted:
        statement_fact_from_file(session, _statement(), "affirm.pdf", TODAY, "test")
        clean = {p.loan_id: p.interest_paid for p in session.exec(select(InstallmentPlan)).all()}
    assert clean and all(v > 0 for v in clean.values())
    for k in range(1, counted.count + 1):
        from sqlmodel import SQLModel

        from app.db import init_db

        SQLModel.metadata.drop_all(engine)
        init_db()
        with Session(engine) as session, commits(fail_at=k):
            with pytest.raises(Crash):
                statement_fact_from_file(session, _statement(), "affirm.pdf", TODAY, "test")
        with Session(engine) as session:
            statement_fact_from_file(session, _statement(), "affirm.pdf", TODAY, "test")
            assert {p.loan_id: p.interest_paid for p in session.exec(select(InstallmentPlan)).all()} == clean, k


def test_i_paid_it_lands_the_claim_and_the_tick_together(owner: TestClient):
    """borrowing.mark_installment_paid: the owner's claim was committed before the plan ticked. A failure between
    kept the claim and not the tick; when the bank's row came, the claim took it, so the plan never ticked and
    showed a payment more left than there was."""
    from app.engine.borrowing import OWNER_PAID, mark_installment_paid

    with Session(engine) as session:
        plan = InstallmentPlan(provider="Example BNPL", merchant="Example Couch", total=248.0, installment=62.0, count=4, remaining=3, next_date=date(2026, 10, 21), cadence_days=14)
        session.add(plan)
        session.commit()
        pid = plan.id
    with Session(engine) as session, commits(fail_at=1):
        with pytest.raises(Crash):
            mark_installment_paid(session, session.get(InstallmentPlan, pid), TODAY, TODAY)
    with Session(engine) as session:
        assert not (get_setting(session, OWNER_PAID, {}) or {}).get(str(pid)), "a claim was kept for a payment the plan did not count"
        assert session.get(InstallmentPlan, pid).remaining == 3


def test_a_typed_plan_confirmed_again_after_a_failure_is_one_plan(owner: TestClient):
    """borrowing.plan_from_fields committed the plan before the confirm route marked its fact applied. A failure in
    between (here the fact's own commit) left the plan with the fact still waiting; confirming again made a second
    plan, and the debt counted twice."""
    with Session(engine) as session:
        fact = ExtractedFact(kind="installment", fields=json.dumps({"sender_name": "Affirm", "provider": "Affirm", "merchant": "Example Typed"}), status="pending", confidence=0.9)
        session.add(fact)
        session.commit()
        fid = fact.id
    with commits(fail_at=1, skip=("audit",)):
        assert _call(owner, "POST", f"/api/v1/facts/{fid}/confirm", {"amount": 40.0, "remaining": 4}) == "crash"
    assert _call(owner, "POST", f"/api/v1/facts/{fid}/confirm", {"amount": 40.0, "remaining": 4}) == 200
    with Session(engine) as session:
        assert len(session.exec(select(InstallmentPlan).where(InstallmentPlan.merchant == "Example Typed")).all()) == 1


def test_a_paystub_file_read_again_is_one_waiting_fact_and_one_kept_copy(owner: TestClient):
    """paystubs.paystub_fact_from_file kept the file, committed, then queued the fact. The import folder notes a
    file as read once a run, so a run that died after the fact (or failed between the copy and the fact) read it
    again: a second copy, and a second paystub waiting on Needs you."""
    from test_sweep12_bugs_mail_quarter import paystub_lines, pdf_of

    from app.engine.paystubs import paystub_fact_from_file

    data = pdf_of(paystub_lines(date(2026, 11, 13), 2))
    with Session(engine) as session, commits(fail_at=2):
        with pytest.raises(Crash):
            paystub_fact_from_file(session, data, "stub.pdf", TODAY, "import-folder")  # the copy kept, the fact not
    with Session(engine) as session:
        first = paystub_fact_from_file(session, data, "stub.pdf", TODAY, "import-folder")
        again = paystub_fact_from_file(session, data, "stub.pdf", TODAY, "import-folder")
        assert first.id == again.id
        assert len(session.exec(select(ExtractedFact).where(ExtractedFact.kind == "paystub")).all()) == 1
        assert len(session.exec(select(Document).where(Document.kind == "paystub")).all()) == 1


def test_a_paystub_confirmed_again_after_a_failure_keeps_its_file_once(owner: TestClient):
    """documents.save_document kept the same file with the same paystub a second time when the confirmation was
    retried after a failure past the copy's commit."""
    from app.engine.documents import save_document

    raw = b"%PDF-1.4 an example paystub"
    with Session(engine) as session:
        a = save_document(session, raw, filename="stub.pdf", kind="paystub", attach_to="paystub:7", source="mail:5")
        b = save_document(session, raw, filename="stub.pdf", kind="paystub", attach_to="paystub:7", source="mail:5")
        assert a.id == b.id and len(session.exec(select(Document)).all()) == 1


def test_a_paystub_kept_without_its_paycheck_figure_never_happens(owner: TestClient):
    """paystubs.record_paystub committed the stub, then the paycheck's expected net; a failure between kept the
    stub, and every retry found it and returned early, so the expected net never moved."""
    from app.engine.paystubs import record_paystub

    with Session(engine) as session:
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 30)))
        for n, day in enumerate(("2026-10-02", "2026-10-16")):
            session.add(Paystub(pay_date=date.fromisoformat(day), net=2240.0, stub_hash=f"example-{n}"))
        session.commit()
    fields = {"pay_date": "2026-10-30", "net": 2240.0, "gross": 2980.77}
    with Session(engine) as session:
        with commits() as counted:
            record_paystub(session, fields=fields, source="test", fact_id=None, today=TODAY)
        assert session.exec(select(Income)).one().expected_net == 2240.0  # three stubs: the median moves it
        session.delete(session.exec(select(Paystub).where(Paystub.pay_date == date(2026, 10, 30))).one())
        session.exec(select(Income)).one().expected_net = 2000.0
        session.commit()
    with Session(engine) as session, commits(fail_at=counted.count):  # the last of its commits
        with pytest.raises(Crash):
            record_paystub(session, fields=fields, source="test", fact_id=None, today=TODAY)
    with Session(engine) as session:
        record_paystub(session, fields=fields, source="test", fact_id=None, today=TODAY)
        assert session.exec(select(Income)).one().expected_net == 2240.0, "the stub was kept and the paycheck's figure never moved"


def test_a_restore_point_is_a_file_on_its_own_and_goes_whole(owner: TestClient):
    """restore._copy kept the live file's WAL mode in each copy, and every read-only open of a copy (its check, the
    documents it names) left a -wal and a -shm beside it; removing the point left both, for good."""
    made = restore.make_point("manual", "a test point")
    assert sorted(p.name for p in restore.folder().glob(made["name"].removesuffix(".db") + "*")) == [made["name"], made["name"].replace(".db", ".json")]
    restore.remove_point(made["name"])
    assert not list(restore.folder().glob(made["name"].removesuffix(".db") + "*"))


def test_a_copy_killed_half_way_is_never_listed_and_is_swept_later(owner: TestClient):
    """restore.make_point wrote the copy under its final name, then checked it, then wrote its description: a copy
    killed half way was listed as a point, unverified and unreadable, and the drill picked it as the newest."""
    real = restore.check

    def dies(path, quick=False):  # noqa: ANN001, ANN202
        raise Crash("killed")

    listed = restore.list_points()
    restore.check = dies
    try:
        with pytest.raises(Crash):
            restore.make_point("daily", "dies half way")
    finally:
        restore.check = real
    assert restore.list_points() == listed
    restore.sweep_leftovers()
    leftover = restore.folder() / ".20261029-010101-000001-daily.db.partial"
    leftover.write_bytes(b"half")
    journal = leftover.with_name(leftover.name + "-wal")  # a copy killed while its journal was open leaves this too
    journal.write_bytes(b"wal")
    lone = restore.folder() / "20261029-010101-000002-daily.json"
    lone.write_text("{}")
    restore.sweep_leftovers()
    assert leftover.exists() and journal.exists() and lone.exists()  # under an hour old: maybe a copy being made now
    old = time.time() - 2 * restore.LEFTOVER_SECONDS
    for p in (leftover, journal, lone):
        os.utime(p, (old, old))
    assert restore.sweep_leftovers() == 3 and not leftover.exists() and not journal.exists() and not lone.exists()


def test_a_restore_failing_half_way_leaves_the_database_as_it_was(owner: TestClient):
    """restore.restore copied the point over the database, then carried today's keys, sign-ins and links in: a
    restore that failed or was killed in between left the old point in place with a key revoked since working
    again (and every session and approval of that day)."""
    key = owner.post("/api/v1/keys", json={"name": "Example key", "scope": "read"}).json()
    point = restore.make_point("manual", "with the key")
    assert owner.delete(f"/api/v1/keys/{key['id']}").status_code == 200
    real = restore._carry_forward

    def dies(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise Crash("killed while carrying today's keys in")

    restore._carry_forward = dies
    try:
        with pytest.raises(Crash):
            restore.restore(point["name"])
    finally:
        restore._carry_forward = real
    with Session(engine) as session:
        assert session.get(ApiKey, key["id"]).revoked_at is not None
    restore.restore(point["name"])
    with Session(engine) as session:
        assert session.get(ApiKey, key["id"]).revoked_at is not None


def test_a_restore_never_reads_the_import_folder_in_again(owner: TestClient):
    """restore.CARRIED_SETTINGS left out the record of files already read, so a restore to before the folder's
    imports read every file in again within five minutes: the restore undone for them, and the paystub waiting
    twice."""
    build_world(owner, mail=False, bills=False)
    point = restore.make_point("manual", "before the folder was read")
    assert "nothing new" not in scheduler.import_watch()
    restore.restore(point["name"])
    assert scheduler.import_watch() == "nothing new"


@pytest.mark.parametrize(("job", "follow"), [("bank_sync", "detect_recurring"), ("import_watch", "detect_recurring"), ("mail_sync", "match_payments_job")])
def test_what_a_run_stored_is_followed_up_on_the_next_run_when_the_follow_up_failed(owner: TestClient, tmp_path, job: str, follow: str):
    """scheduler.bank_sync and import_watch ran detection (and mail_sync payment matching) only when this run
    stored something. When the rows committed and the follow-up failed, the next run stored nothing new and
    skipped it: bills stayed late and new series unlinked until the six-hourly detection, the plan counting each
    paid bill as still owed."""
    build_world(owner, bills=False)
    base = snapshot(tmp_path / "base")
    _job(job)
    make_due()
    reference = dump()
    put_back(base)
    real = getattr(scheduler, follow)

    def fails():  # noqa: ANN202
        raise Crash("the follow-up failed")

    setattr(scheduler, follow, fails)
    try:
        assert _job(job).startswith("failed")
    finally:
        setattr(scheduler, follow, real)
    make_due()
    _job(job)
    assert not diff(reference, dump()), diff(reference, dump())
    assert not scheduler.behind(scheduler.MATCHED if job == "mail_sync" else scheduler.DETECTED)


# ---- from the frontend's second-person and two-tab sweep --------------------------------------------------


def test_next_steps_counts_the_sends_of_whoever_reads_them(owner: TestClient):
    """engine/next_steps.py counted only the owner's waiting sends: a second person with a send of their own
    waiting saw nothing on their strip."""
    from app.engine.next_steps import next_steps
    from app.models import Approval

    with Session(engine) as session:
        session.add(Approval(to="landlord@example.test", subject="Example", for_person_id=5, created_kind="person"))
        session.commit()
        mine = next((st for st in next_steps(session, TODAY, person_id=5)["steps"] if st["id"] == "approvals"), None)
        owners = next((st for st in next_steps(session, TODAY)["steps"] if st["id"] == "approvals"), None)
    assert mine is not None and mine["text"].startswith("1 send waits for your approval")
    assert owners is None


@pytest.mark.parametrize("answer", ["not_yet", "stopped"])
def test_an_answer_to_a_period_answered_in_another_tab_changes_nothing(owner: TestClient, answer: str):
    """engine/needsyou.answer_payment answered "not yet" (200) for a period another tab had just marked paid, and
    "stopped" overwrote the paid answer and ended the bill. The period is read again under the writer lock; one no
    longer open is refused in a sentence."""
    from app.engine.needsyou import AlreadyAnswered, answer_payment
    from app.models import Bill

    with Session(engine) as session:
        bill = Bill(name="Example Water", amount=41.0, due_day=7)
        session.add(bill)
        session.flush()
        payment = Payment(bill_id=bill.id, due_date=date(2026, 10, 7), amount=41.0, status="past_due")
        session.add(payment)
        session.commit()
        pid, bid = payment.id, bill.id
    with Session(engine) as tab_one, Session(engine) as tab_two:
        stale = tab_two.get(Payment, pid)  # the second tab read the period while it was past due
        answer_payment(tab_one, tab_one.get(Payment, pid), "paid", TODAY, paid_on=date(2026, 10, 9))
        with pytest.raises(AlreadyAnswered, match="already answered: paid on Fri Oct 9"):
            answer_payment(tab_two, stale, answer, TODAY)
    with Session(engine) as session:
        assert session.get(Payment, pid).status == "paid" and session.get(Bill, bid).ends_on is None
    r = owner.post(f"/api/v1/needs-you/payments/{pid}", json={"answer": answer})
    assert r.status_code in (400, 409) and "already answered" in r.json()["detail"]  # 409 once the route maps AlreadyAnswered


# ---- outside this sweep's files: reported, held here until fixed --------------------------------------------


def test_a_message_whose_fetch_failed_is_read_on_the_next_sync(owner: TestClient):
    build_world(owner, files=False)
    Box.fail_fetch = {2, 3, 4, 5}  # the connection is cut after the first message
    _job("mail_sync")
    Box.fail_fetch = set()
    _job("mail_sync")
    from app.models import MessageRef

    with Session(engine) as session:
        assert len(session.exec(select(MessageRef)).all()) == 5


def test_a_mail_read_beside_another_writer_does_not_fail_on_a_locked_database(owner: TestClient):
    import sqlite3
    import threading

    from app.config import settings

    build_world(owner, files=False)
    holding, done = threading.Event(), threading.Event()

    def writer() -> None:
        con = sqlite3.connect(str(settings.db_path), timeout=10)
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO settings (key, value) VALUES ('example.writer', '1')")
        holding.set()
        time.sleep(0.4)
        con.commit()
        con.close()
        done.set()

    real = Box.fetch_raw

    def fetch(self, uid):  # noqa: ANN001, ANN202
        if uid == 3:
            threading.Thread(target=writer, daemon=True).start()
            holding.wait(5)
        return real(self, uid)

    Box.fetch_raw = fetch
    try:
        said = _job("mail_sync")
    finally:
        Box.fetch_raw = real
    done.wait(5)
    assert "locked" not in said, said  # a writer that held the lock under a second waits; busy_timeout is five


def test_erase_everything_does_not_read_the_import_folder_back_in(owner: TestClient):
    build_world(owner, mail=False, bills=False)
    scheduler.import_watch()
    r = owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    assert scheduler.import_watch() == "nothing new"


def test_a_bills_list_read_again_changes_nothing(owner: TestClient):
    build_world(owner, mail=False)
    scheduler.import_watch()
    with Session(engine) as session:
        session.delete(session.get(Setting, "imports.processed"))  # the run died before it noted the files
        session.commit()
    scheduler.import_watch()
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch).where(ImportBatch.kind == "bills")).all()) == 1
