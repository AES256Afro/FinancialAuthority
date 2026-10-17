"""M51: watching the app itself. Each part is judged from the self-check, kept hourly, drawn a day per square, and
told to the phone once when it breaks, daily while it stays broken, and once when fixed, never with a job's own
error text."""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.apphealth import checks_from_report, history, job_checks, plan_alerts
from app.models import HealthSample, JobRun

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
GOOD_JOBS = {"key": "jobs", "label": "Jobs", "state": "good", "note": "every job's last run went through"}


def _report(**over) -> dict:
    base = {"free_gb": 40.0, "db_mb": 3.0, "sources": [], "problems": [], "public_address": {"answers_from_inside": None, "last_seen_from_outside": None}}
    return {**base, **over}


def test_each_part_is_judged_in_plain_words():
    report = _report(free_gb=1.5, sources=[
        {"kind": "mail", "name": "Example Mailbox", "last_ok": None, "error": "login refused"},
        {"kind": "plaid", "name": "Example Bank", "last_ok": (NOW - timedelta(hours=30)).isoformat(), "error": ""},
    ])
    parts = {c["key"]: c for c in checks_from_report(report, 50.0, GOOD_JOBS, NOW)}
    assert parts["disk"] == {"key": "disk", "label": "Disk space", "state": "crit", "note": "1.5 GB free"}
    assert parts["backups"]["state"] == "crit" and parts["backups"]["note"] == "the newest is 50 hours old"
    assert parts["source:mail:Example Mailbox"]["state"] == "crit" and parts["source:mail:Example Mailbox"]["note"] == "could not be read"
    assert parts["source:plaid:Example Bank"] == {"key": "source:plaid:Example Bank", "label": "Bank link · Example Bank", "state": "warn", "note": "last read 30 hours ago"}
    assert "public" not in parts and parts["push"]["state"] == "good"


def test_a_failing_job_is_named_but_its_error_text_never_leaves(client: TestClient):
    from app.jobs.scheduler import jobs

    with Session(engine) as session:
        for minutes in (30, 10):
            session.add(JobRun(name="mail_sync", started_at=NOW - timedelta(minutes=minutes), finished_at=NOW, ok=False, summary="Example Mailbox: password for owner@example.test refused"))
        session.commit()
        part = job_checks(session, jobs.specs, {"mail_sync": "reading mail"})
    assert part == {"key": "jobs", "label": "Jobs", "state": "crit", "note": "failing: reading mail"}
    messages, _ = plan_alerts({}, [part], NOW)
    assert messages == [{"title": "Needs a look: Jobs", "body": "Jobs: failing: reading mail.", "link": "/settings/jobs"}]
    assert "example.test" not in json.dumps(messages)


def test_the_phone_is_told_once_daily_while_broken_and_once_when_fixed():
    broken = [{"key": "backups", "label": "Restore points", "state": "crit", "note": "the newest is 50 hours old"}]
    first, state = plan_alerts({}, broken, NOW)
    assert [m["title"] for m in first] == ["Needs a look: Restore points"]
    hour_later, state = plan_alerts(state, broken, NOW + timedelta(hours=1))
    assert hour_later == []
    next_day, state = plan_alerts(state, broken, NOW + timedelta(hours=25))
    assert next_day[0]["title"] == "Still needs a look: Restore points" and "since 2026-10-14" in next_day[0]["body"]
    fixed, state = plan_alerts(state, [{**broken[0], "state": "good", "note": "the newest is 2 hours old"}], NOW + timedelta(hours=26))
    assert fixed == [{"title": "Fixed: Restore points", "body": "Restore points is working again.", "link": "/settings/backups"}] and state == {}


def test_thirty_days_are_drawn_a_day_per_square_the_worst_hour_of_each():
    def sample(at: datetime, state: str) -> HealthSample:
        return HealthSample(at=at, free_gb=20.0 if state == "good" else 1.0, checks=json.dumps([{"key": "disk", "label": "Disk space", "state": state, "note": "x"}]))

    samples = [sample(datetime(2026, 10, 12, 9, tzinfo=timezone.utc), "good"), sample(datetime(2026, 10, 12, 10, tzinfo=timezone.utc), "crit"),
               sample(datetime(2026, 10, 14, 9, tzinfo=timezone.utc), "good")]
    out = history(samples, date(2026, 10, 14), days=7)
    cells = out["rows"][0]["cells"]
    assert [c["state"] for c in cells] == ["none", "none", "none", "none", "crit", "none", "good"]
    assert out["disk_gb"][4] == 1.0 and out["open"] == []
    assert out["said"] == "Everything is working now. 1 of the last 7 days had a part that was broken."
    calm = history(samples[2:], date(2026, 10, 14), days=7)
    assert calm["said"] == "Everything is working now, and was on the one day sampled so far."


def test_the_self_check_keeps_a_sample_and_the_screen_reads_it(owner: TestClient):
    from app.jobs.scheduler import app_watch, self_check

    self_check()
    with Session(engine) as session:  # the tests' today is Oct 14; the sample was stamped with the real clock
        sample = session.exec(select(HealthSample)).one()
        sample.at = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        session.add(sample)
        session.commit()
    out = owner.get("/api/v1/health/history").json()
    assert out["latest_at"] and {r["key"] for r in out["rows"]} >= {"disk", "backups", "jobs", "push"} and out["alerts_on"] is True
    assert app_watch().startswith(("not due", "nothing new", "told the phone"))
    assert owner.put("/api/v1/health/alerts", json={"on": False}).json() == {"alerts_on": False}
    assert app_watch() == "not due: telling the phone is off"
