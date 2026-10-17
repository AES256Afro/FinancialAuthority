"""Watching the app itself (M51): each hour the self-check leaves a sample of how every part was (disk, backups,
each mailbox and bank link, the jobs, the public address, the phone channel); thirty days of them are drawn as one
strip per part, a day per square, the worst hour of the day. A part that breaks is told to the phone once when it
breaks, once a day while it stays broken, and once when it is fixed. What is sent names the part and where to look,
never a job's own error text, which can hold names and figures."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ..models import HealthSample, JobRun

RANK = {"none": 0, "good": 1, "warn": 2, "crit": 3}
MAIL_STALE_HOURS = 3  # mail is read every fifteen minutes
BANK_STALE_HOURS = 26  # a bank link is read at least daily
REMIND_HOURS = 24
# The jobs in plain words, as the Jobs page names them.
JOB_WORDS = {
    "mail_sync": "reading the mailboxes", "bank_sync": "reading the linked banks", "import_watch": "importing from the import folder",
    "detect_recurring": "finding repeating charges", "match_payments": "matching bills to payments", "review_if_due": "the reviewer's notes",
    "morning_brief": "the morning brief", "reminders": "your reminders", "moves_verify": "checking a bank switch", "balance_history": "keeping balances",
    "self_check": "the self-check", "restore_point_daily": "the daily copy", "offbox_copy": "the off-box copy", "restore_drill": "the restore drill",
    "prune_job_runs": "tidying job records", "app_watch": "telling the phone",
}


def _age_hours(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if at.tzinfo is None and now.tzinfo is not None:
        at = at.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - at).total_seconds() / 3600)


def job_checks(session: Session, specs: dict[str, Any], words: dict[str, str] | None = None) -> dict[str, Any]:
    """The jobs as one part: failing when a job's last run failed and it runs rarely (six hours or more apart), or
    its last two runs failed."""
    failing = []
    for name, spec in specs.items():
        runs = session.exec(select(JobRun).where(JobRun.name == name).order_by(JobRun.started_at.desc()).limit(2)).all()
        if not runs or runs[0].ok is not False:
            continue
        if spec.seconds >= 6 * 3600 or (len(runs) > 1 and runs[1].ok is False):
            failing.append((words or JOB_WORDS).get(name, name.replace("_", " ")))
    if not failing:
        return {"key": "jobs", "label": "Jobs", "state": "good", "note": "every job's last run went through"}
    return {"key": "jobs", "label": "Jobs", "state": "crit", "note": f"failing: {', '.join(sorted(failing))}"}


def checks_from_report(report: dict[str, Any], backup_age_hours: float | None, jobs_part: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Every part, good, warn or crit, with a short note in plain words, from the self-check's report."""
    free = float(report.get("free_gb") or 0.0)
    out = [{"key": "disk", "label": "Disk space", "state": "crit" if free < 2 else "warn" if free < 5 else "good", "note": f"{free:,.1f} GB free"}]
    if backup_age_hours is None:
        out.append({"key": "backups", "label": "Restore points", "state": "crit", "note": "no restore point yet"})
    else:
        out.append({"key": "backups", "label": "Restore points", "state": "crit" if backup_age_hours > 48 else "warn" if backup_age_hours > 30 else "good",
                    "note": f"the newest is {backup_age_hours:,.0f} hours old"})
    for s in report.get("sources") or []:
        kind = s.get("kind") or "source"
        mail = kind == "mail"
        label = f"{'Mailbox' if mail else 'Bank link'} · {s.get('name') or kind}"
        limit = MAIL_STALE_HOURS if mail else BANK_STALE_HOURS
        age = _age_hours(s.get("last_ok"), now)
        if s.get("error"):
            state, note = "crit", "could not be read" if mail else "could not be read; it may need a new sign-in"
        elif age is None:
            state, note = "warn", "not read yet"
        elif age > limit:
            state, note = "warn", f"last read {age:,.0f} hours ago"
        else:
            state, note = "good", "read on time"
        out.append({"key": f"source:{kind}:{s.get('name') or ''}", "label": label, "state": state, "note": note})
    out.append(jobs_part)
    public = report.get("public_address") or {}
    if public.get("answers_from_inside") is False and not public.get("last_seen_from_outside"):
        out.append({"key": "public", "label": "Public address", "state": "warn", "note": "did not answer, and no browser reached it in a day"})
    elif public.get("answers_from_inside") is not None or public.get("last_seen_from_outside"):
        out.append({"key": "public", "label": "Public address", "state": "good", "note": "answering"})
    problems = report.get("problems") or []
    pushed = any(p.startswith("push to your phone failed") for p in problems)
    out.append({"key": "push", "label": "Phone notifications", "state": "warn" if pushed else "good", "note": "the last one did not arrive" if pushed else "arriving"})
    return out


def record(session: Session, report: dict[str, Any], checks: list[dict[str, Any]], backup_age_hours: float | None, at: datetime) -> HealthSample:
    sample = HealthSample(at=at, free_gb=float(report.get("free_gb") or 0.0), db_mb=float(report.get("db_mb") or 0.0), backup_age_hours=backup_age_hours, checks=json.dumps(checks))
    session.add(sample)
    session.commit()
    return sample


def history(samples: list[HealthSample], today: date, days: int = 30, tz: Any = None) -> dict[str, Any]:
    """Thirty days as one strip per part, the worst state of each day (in the household's time zone); the disk as
    the least free each day; and what is not good now. A part that appears later (a new mailbox) has empty days
    before it."""
    span = [today - timedelta(days=days - 1 - i) for i in range(days)]
    index = {d: i for i, d in enumerate(span)}
    rows: dict[str, dict[str, Any]] = {}
    disk: list[float | None] = [None] * days
    latest: HealthSample | None = None
    def local(at: datetime) -> datetime:
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)  # stored as UTC; SQLite gives it back naive
        return at.astimezone(tz) if tz else at

    for s in sorted(samples, key=lambda x: local(x.at)):
        day = local(s.at).date()
        if day not in index:
            continue
        i = index[day]
        latest = s
        disk[i] = s.free_gb if disk[i] is None else min(disk[i], s.free_gb)
        for c in json.loads(s.checks or "[]"):
            row = rows.setdefault(c["key"], {"key": c["key"], "label": c["label"], "cells": [{"state": "none", "note": ""} for _ in span]})
            row["label"] = c["label"]
            cell = row["cells"][i]
            if RANK[c["state"]] >= RANK[cell["state"]]:
                row["cells"][i] = {"state": c["state"], "note": c["note"]}
    now_checks = json.loads(latest.checks) if latest else []
    open_now = [c for c in now_checks if c["state"] != "good"]
    order = ["disk", "backups", "jobs", "public", "push"]
    ordered = sorted(rows.values(), key=lambda r: (order.index(r["key"]) if r["key"] in order else 2.5, r["label"]))
    bad_days = sum(1 for i in range(days) if any(r["cells"][i]["state"] == "crit" for r in ordered))
    sampled = sum(1 for g in disk if g is not None)
    if latest is None:
        said = "No health samples yet: the self-check takes one each hour."
    elif not open_now:
        if bad_days:
            said = f"Everything is working now. {bad_days} of the last {days} days had a part that was broken."
        elif sampled < days:
            said = f"Everything is working now, and was on {'the one day' if sampled == 1 else f'each of the {sampled} days'} sampled so far."
        else:
            said = f"Everything is working now, and has been for the last {days} days."
    else:
        said = f"{len(open_now)} part{'s' if len(open_now) != 1 else ''} need{'s' if len(open_now) == 1 else ''} a look now: " + "; ".join(f"{c['label']}, {c['note']}" for c in open_now) + "."
    return {"days": [d.isoformat() for d in span], "rows": ordered, "disk_gb": disk, "open": open_now,
            "latest_at": latest.at.isoformat() if latest else None, "said": said}


WHERE = {"disk": "/settings/jobs", "backups": "/settings/backups", "jobs": "/settings/jobs", "public": "/settings/setup", "push": "/settings/notifications"}


def plan_alerts(prior: dict[str, Any], checks: list[dict[str, Any]], now: datetime) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """What to tell the phone, and the state to keep: a part told once when it breaks (warn or crit), again once a
    day while it stays broken, and once when it is fixed. Pure: the caller sends and saves."""
    state: dict[str, Any] = {}
    messages: list[dict[str, str]] = []
    seen = set()
    for c in checks:
        key = c["key"]
        seen.add(key)
        was = prior.get(key)
        link = WHERE.get(key, "/settings/connectors" if key.startswith("source:") else "/settings/jobs")
        if c["state"] == "good":
            if was:
                messages.append({"title": f"Fixed: {c['label']}", "body": f"{c['label']} is working again.", "link": link})
            continue
        if not was:
            messages.append({"title": f"Needs a look: {c['label']}", "body": f"{c['label']}: {c['note']}.", "link": link})
            state[key] = {"since": now.isoformat(), "told": now.isoformat(), "state": c["state"]}
            continue
        told = _age_hours(was.get("told"), now)
        if told is not None and told >= REMIND_HOURS:
            messages.append({"title": f"Still needs a look: {c['label']}", "body": f"{c['label']}: {c['note']}, since {str(was.get('since', ''))[:10]}.", "link": link})
            state[key] = {**was, "told": now.isoformat(), "state": c["state"]}
        else:
            state[key] = {**was, "state": c["state"]}
    # A part that is gone (a mailbox removed) is simply forgotten, without a message.
    return messages, state
