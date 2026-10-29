"""Sweep 5, the data layer and the jobs: an older database gains a column it can read back and the indexes the
models declare; a review the provider refused is a failed run; one bank link's crash never stops the others; the
week recap's marks are pruned; the scheduler finds each job's last run without loading the whole record."""

from datetime import date, timedelta

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, select

from app.connectors import CONNECTORS
from app.connectors.base import SyncResult
from app.db import engine, ensure_indexes, init_db
from app.models import BankLink, JobRun, ReviewRun, Scenario, Setting, utcnow
from app.store import get_setting, set_setting

TODAY = date(2026, 10, 14)


def test_an_older_database_reads_back_a_datetime_column_added_at_upgrade():
    SQLModel.metadata.drop_all(engine)
    with engine.begin() as c:
        # The scenarios table as an older version made it: no created_at yet, one saved scenario in it.
        c.execute(text("CREATE TABLE scenarios (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, spec VARCHAR NOT NULL)"))
        c.execute(text("INSERT INTO scenarios (name, spec) VALUES ('kept from before', '{}')"))
    init_db()
    with Session(engine) as s:
        rows = s.exec(select(Scenario)).all()  # a DEFAULT 0 here raised TypeError on every read of the table
        assert [r.name for r in rows] == ["kept from before"]
        assert rows[0].created_at.date() >= date(2026, 1, 1)


def test_an_index_a_model_declares_reaches_an_older_table():
    SQLModel.metadata.drop_all(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE message_refs (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, folder VARCHAR NOT NULL, uid INTEGER NOT NULL)"))
    init_db()
    names = {i["name"] for i in inspect(engine).get_indexes("message_refs")}
    assert {"ix_message_refs_sender", "ix_message_refs_date", "ix_message_refs_account_id"} <= names


def test_an_index_that_cannot_be_made_is_skipped_and_the_rest_still_are(caplog):
    with engine.begin() as c:
        c.execute(text("DROP INDEX ix_transactions_row_hash"))
        c.execute(text("DROP INDEX ix_transactions_merchant"))
        c.execute(text("INSERT INTO accounts (name, institution, kind, subtype, last4_enc, connector, external_id, active, created_at, shared) VALUES ('a', '', 'checking', '', '', 'manual', 'x', 1, '2026-01-01 00:00:00', 0)"))
        for _ in range(2):
            c.execute(text("INSERT INTO transactions (account_id, date, merchant, raw_name, description, amount, category, note, is_transfer, is_card_payment, is_income, is_ignored, pending, tags, source, source_ref, row_hash, business_share, tax_line, created_at) "
                           "VALUES (1, '2026-10-01', 'm', 'm', '', 1.0, 'Uncategorized', '', 0, 0, 0, 0, 0, '', 'manual', '', 'same', 1.0, '', '2026-01-01 00:00:00')"))
    made = ensure_indexes()
    names = {i["name"] for i in inspect(engine).get_indexes("transactions")}
    assert "ix_transactions_merchant" in made and "ix_transactions_merchant" in names
    assert "ix_transactions_row_hash" not in names and "ix_transactions_row_hash" in caplog.text


def test_a_review_the_provider_refused_is_a_failed_run(monkeypatch):
    from app.jobs.scheduler import jobs
    from app.llm import reviewer

    def refused(session_factory, today, now=None):
        with session_factory() as s:
            s.add(ReviewRun(trigger="payday", provider="anthropic", model="x", ok=False, error="401 invalid api key"))
            s.commit()
        return "payday review: 401 invalid api key"

    monkeypatch.setattr(reviewer, "run_if_due", refused)
    run = jobs.run("review_if_due")
    assert run.ok is False and "invalid api key" in run.summary
    monkeypatch.setattr(reviewer, "run_if_due", lambda session_factory, today, now=None: "nothing due")
    assert jobs.run("review_if_due").ok is True


def test_one_bank_links_crash_never_stops_the_others(monkeypatch):
    from app.jobs.scheduler import jobs

    with Session(engine) as s:
        s.add(BankLink(provider="plaid", external_id="one", institution="First Bank", active=True))
        s.add(BankLink(provider="plaid", external_id="two", institution="Second Bank", active=True))
        s.commit()
    seen: list[int] = []

    class Flaky:
        def sync(self, session, link_id=None, **_):
            seen.append(link_id)
            if len(seen) == 1:
                raise ConnectionError("the vendor hung up")
            return SyncResult(ok=True, summary="Second Bank: nothing new")

    monkeypatch.setitem(CONNECTORS, "plaid", Flaky())
    run = jobs.run("bank_sync")
    assert len(seen) == 2, "the second link was read after the first crashed"
    assert run.ok is False and "ConnectionError" in run.summary and "Second Bank: nothing new" in run.summary


def test_old_week_recap_marks_are_pruned_with_the_job_runs():
    from app.jobs.scheduler import jobs

    old = (utcnow() - timedelta(days=200)).date().isoformat()
    recent = (utcnow() - timedelta(days=7)).date().isoformat()
    with Session(engine) as s:
        set_setting(s, f"recap.week.{old}", True)
        set_setting(s, f"recap.week.{recent}", True)
        set_setting(s, "recap.other", {"kept": True})
    assert jobs.run("prune_job_runs").ok is True
    with Session(engine) as s:
        assert s.get(Setting, f"recap.week.{old}") is None
        assert get_setting(s, f"recap.week.{recent}") is True and get_setting(s, "recap.other") == {"kept": True}


def test_the_scheduler_places_a_daily_job_after_its_last_run_from_one_grouped_read():
    from app.jobs.scheduler import jobs

    with Session(engine) as s:
        for days in (5, 3):
            s.add(JobRun(name="match_payments", started_at=utcnow() - timedelta(days=days), finished_at=utcnow(), ok=True, summary="ok"))
        s.add(JobRun(name="self_check", started_at=utcnow() - timedelta(hours=1), finished_at=utcnow(), ok=True, summary="ok"))
        s.commit()
    assert jobs.scheduler is None
    from sqlalchemy import event

    statements: list[str] = []
    listener = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        jobs.start()
        event.remove(engine, "before_cursor_execute", listener)
        reads = [s for s in statements if "job_runs" in s]
        assert len(reads) == 1 and "GROUP BY" in reads[0].upper() and "max(" in reads[0].lower(), reads  # one grouped read, not one row per run
        job = jobs.scheduler.get_job("match_payments")
        # Three days since the last daily run: due now, so two minutes (plus its stagger) after start, not a day away.
        assert job.next_run_time - utcnow() < timedelta(minutes=15)
        hourly = jobs.scheduler.get_job("self_check")
        assert hourly.next_run_time - utcnow() > timedelta(minutes=30)  # an hourly job waits its interval
    finally:
        if event.contains(engine, "before_cursor_execute", listener):
            event.remove(engine, "before_cursor_execute", listener)
        jobs.stop()
