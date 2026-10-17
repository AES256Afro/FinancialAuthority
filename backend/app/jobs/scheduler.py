"""The scheduler and the first jobs: the hourly self-check and the import-folder watch."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from ..config import settings
from ..connectors import CONNECTORS
from ..db import session_scope
from ..models import Account, JobRun, Transaction, utcnow
from ..store import get_setting, set_setting
from .writing import one_writer

log = logging.getLogger("fa.jobs")


@dataclass
class JobSpec:
    name: str
    func: Callable[[], str]
    seconds: int
    description: str
    writes: bool = True  # rebuilds what the app works out from the ledger: runs alone (jobs/writing.py)


BUSY_SECONDS = 600  # how long "Run now" waits for the same job's scheduled run to finish


class Jobs:
    def __init__(self) -> None:
        self.specs: dict[str, JobSpec] = {}
        self.scheduler: BackgroundScheduler | None = None
        self.running: dict[str, threading.Lock] = {}

    def register(self, name: str, func: Callable[[], str], seconds: int, description: str, *, writes: bool = True) -> None:
        self.specs[name] = JobSpec(name, func, seconds, description, writes)
        self.running.setdefault(name, threading.Lock())

    def run(self, name: str) -> JobRun:
        """One run of a job, recorded. The same job never runs twice at once ("Run now" waits for the scheduled run,
        then runs), and a job that writes waits for any other writer (jobs/writing.py)."""
        spec = self.specs[name]
        started = utcnow()
        ok, summary = True, ""
        mine = self.running.setdefault(name, threading.Lock())
        if not mine.acquire(timeout=BUSY_SECONDS):
            ok, summary = False, f"the run before this one was still going after {BUSY_SECONDS // 60} minutes; this one did not start"
        else:
            try:
                if spec.writes:
                    with one_writer():
                        summary = spec.func() or ""
                else:
                    summary = spec.func() or ""
            except Exception as error:  # noqa: BLE001  (a job must never take the scheduler down)
                ok, summary = False, f"{type(error).__name__}: {error}"
                log.exception("job %s failed", name)
            finally:
                mine.release()
        # The run is written once, after the work, so a busy database at the start never loses the record.
        with session_scope() as session:
            run = JobRun(name=name, started_at=started, finished_at=utcnow(), ok=ok, summary=summary[:2000])
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def start(self) -> None:
        if self.scheduler is not None:
            return
        self.scheduler = BackgroundScheduler(timezone="UTC")
        last: dict[str, Any] = {}
        try:
            with session_scope() as session:
                for r in session.exec(select(JobRun).order_by(JobRun.started_at)).all():
                    last[r.name] = r.started_at
        except Exception:  # noqa: BLE001 - without the history every job simply starts on its interval
            last = {}
        now = datetime.now(timezone.utc)
        for k, spec in enumerate(self.specs.values()):
            first = None
            if spec.seconds >= 6 * 3600:
                # The daily and six-hourly jobs: due again one interval after their last run, or two minutes
                # after start (a little apart) when that has passed, so a daily restart never starves them.
                ran = last.get(spec.name)
                due = ran.replace(tzinfo=timezone.utc) + timedelta(seconds=spec.seconds) if ran else now
                first = max(due, now + timedelta(seconds=120 + 30 * k))
            self.scheduler.add_job(lambda n=spec.name: self.run(n), IntervalTrigger(seconds=spec.seconds), id=spec.name, max_instances=1, coalesce=True,
                                   **({"next_run_time": first} if first else {}))
        self.scheduler.start()
        log.info("scheduler started with %d jobs", len(self.specs))

    def stop(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    def recent(self, limit: int = 50) -> list[JobRun]:
        with session_scope() as session:
            return list(session.exec(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)).all())


jobs = Jobs()


def self_check() -> str:
    """Each connector's last success, stale data, disk space, database size."""
    with session_scope() as session:
        usage = shutil.disk_usage(settings.data_dir)
        db_bytes = settings.db_path.stat().st_size if settings.db_path.exists() else 0
        last_tx = session.exec(select(Transaction.date).order_by(Transaction.date.desc())).first()
        from ..connectors.banks import LIVE_PROVIDERS

        accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
        stale = []
        typed: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for a in accounts:
            known = a.balance_at
            if known is not None and known.tzinfo is None:
                known = known.replace(tzinfo=timezone.utc)
            if known is None or (now - known).days <= 2:
                continue
            if a.connector in LIVE_PROVIDERS:
                stale.append(a.name)  # a link should have refreshed it: that is a problem
            else:
                typed.append({"name": a.name, "days": (now - known).days})  # typed or imported: only the owner can update it
        from ..models import BankLink, MailAccount

        sources = []
        for m in session.exec(select(MailAccount).where(MailAccount.active == True)).all():  # noqa: E712
            sources.append({"kind": "mail", "name": m.label, "last_ok": m.last_sync_at.isoformat() if m.last_sync_at and not m.last_error else None, "error": m.last_error})
        for l in session.exec(select(BankLink).where(BankLink.active == True)).all():  # noqa: E712
            sources.append({"kind": l.provider, "name": l.institution, "last_ok": l.last_sync_at.isoformat() if l.last_sync_at and not l.last_error else None, "error": l.last_error or ("needs a new sign-in" if l.needs_login else "")})
        reachable = None
        seen_outside = None
        if settings.public_url:
            try:
                import httpx

                reachable = httpx.get(settings.public_url.rstrip("/") + "/api/health", timeout=8.0).status_code == 200
            except Exception:  # noqa: BLE001 - unreachable is an answer
                reachable = False
            sighting = get_setting(session, "public.last_seen", {}) or {}
            try:
                if sighting.get("at") and (utcnow() - datetime.fromisoformat(sighting["at"])).total_seconds() < 86400:
                    seen_outside = sighting["at"]
            except ValueError:
                seen_outside = None
        problems = [f"{s['name']}: {s['error']}" for s in sources if s["error"]]
        if reachable is False and seen_outside is None:
            # From inside the container a tailnet-served address never answers; a browser reaching it does.
            problems.append("the public address did not answer, and no browser has reached it in the last day")
        if stale:
            problems.append(f"stale balances: {', '.join(stale)}")
        pushed = get_setting(session, "push.last_error", None)
        if pushed and (pushed.get("detail") or pushed.get("at")):
            problems.append(f"push to your phone failed: {str(pushed.get('detail') or 'no detail')[:120]}")
        if usage.free < 2e9:
            problems.append("under 2 GB free")
        from .. import restore

        backup_age: float | None = None
        try:
            backups = restore.status(fresh=True)
            backup_age = backups["latest_age_hours"]
            if backups["latest_age_hours"] is None or backups["latest_age_hours"] > 48:
                problems.append("no restore point in the last two days")
            if not backups["database"]["ok"]:
                problems.append(f"the database did not pass its check: {backups['database']['result']}")
            drilled = get_setting(session, "backups.drill", None)
            if drilled and not drilled.get("ok"):
                problems.append(f"the last restore drill failed: {drilled.get('detail', '')[:120]}")
            off = get_setting(session, "backups.offbox", {}) or {}
            if off.get("last_error"):
                problems.append(f"the off-box copy failed: {off['last_error'][:120]}")
        except Exception as error:  # noqa: BLE001 - a check that cannot run is itself the problem
            problems.append(f"restore points could not be read: {error}")
        report = {
            "at": utcnow().isoformat(),
            "free_gb": round(usage.free / 1e9, 2),
            "db_mb": round(db_bytes / 1e6, 2),
            "last_transaction": last_tx.isoformat() if last_tx else None,
            "public_address": {"answers_from_inside": reachable, "last_seen_from_outside": seen_outside},
            "stale_accounts": stale,
            "typed_balances": typed,
            "connectors": sorted(CONNECTORS),
            "sources": sources,
            "public_url_reachable": reachable,
            "problems": problems,
        }
        set_setting(session, "selfcheck", report)
        # The hour's sample of every part, for the health strips and for telling the phone (app_watch).
        from ..engine import apphealth

        checks = apphealth.checks_from_report(report, backup_age, apphealth.job_checks(session, jobs.specs), now)
        apphealth.record(session, report, checks, backup_age, utcnow())
        return f"{report['free_gb']} GB free · db {report['db_mb']} MB · last transaction {report['last_transaction']} · {len(problems)} problem(s)" + (": " + "; ".join(problems) if problems else "")


def import_watch() -> str:
    """Import any new file in the import folder, then re-run detection. Each file is recorded as taken the moment
    it is, so a later failure never loses the record of the ones before it; a file that fails is said so, the
    others still import, and it is tried again on the next run."""
    settings.ensure_dirs()
    taken: list[str] = []
    failed: list[str] = []
    with session_scope() as session:
        processed = {tuple(p) for p in get_setting(session, "imports.processed", [])}
        pdfs = [(path, (str(path.relative_to(settings.import_root)), int(path.stat().st_mtime))) for path in sorted(settings.import_root.rglob("*.pdf"))]
        tables = [(path, (path.name, int(path.stat().st_mtime))) for path in sorted(list(settings.import_root.glob("*.csv")) + list(settings.import_root.glob("*.ofx")) + list(settings.import_root.glob("*.qfx")) + list(settings.import_root.glob("*.qbo")))]
        for path, stamp in pdfs + tables:
            if stamp in processed:
                continue
            try:
                note = _import_pdf(session, path) if path.suffix.lower() == ".pdf" else _import_table(session, path)
            except Exception as error:  # noqa: BLE001 - one file's failure is its own; the others still import
                session.rollback()
                log.exception("import of %s failed", path.name)
                failed.append(f"{path.name}: not imported ({type(error).__name__}: {str(error)[:120]})")
                continue
            taken.append(note)
            processed.add(stamp)
            set_setting(session, "imports.processed", sorted(processed))  # this file's record lands now, not at the end
    if taken:
        detect_recurring()
    if failed:
        raise RuntimeError("; ".join(failed + taken))  # a file that would not import is a failed run, never a quiet one
    return "; ".join(taken) if taken else "nothing new"


def _import_pdf(session, path) -> str:  # noqa: ANN001 - a Session and a Path
    """A PDF in the folder: an Affirm statement, applied at once, or a paystub, which waits on Needs you."""
    from ..engine.borrowing import statement_fact_from_file
    from ..engine.paystubs import paystub_fact_from_file

    data = path.read_bytes()
    applied = statement_fact_from_file(session, data, path.name, _today(), "import-folder")
    if applied:
        return f"{path.name}: an Affirm statement, {applied['loans']} loans landed"
    fact = paystub_fact_from_file(session, data, path.name, _today(), "import-folder")
    return f"{path.name}: {'a paystub, on Needs you to confirm' if fact else 'not read as a paystub or a statement'}"


def _import_table(session, path) -> str:  # noqa: ANN001 - a Session and a Path
    """A CSV or a statement file in the folder: a Rocket Money export, an OFX statement, a bills list, or a bank's
    CSV, which lands in the account the file names (its number, else an account its name starts with)."""
    from ..connectors.files import detect, folder_account, import_bills, import_rows, parse_bills_rows, parse_csv_rows, parse_ofx
    from ..connectors.rocketmoney import parse_csv as parse_rocketmoney

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".csv" and parse_rocketmoney(text, path.name) is not None:
        result = CONNECTORS["rocketmoney"].sync(session, text=text, source_file=path.name, via="folder")
    else:
        found = detect(text)
        if found["kind"] == "ofx":
            parsed = parse_ofx(text)
            rows = parsed["rows"]
            name = f"{parsed['vendor']} {parsed['account_type'] or 'account'}".strip() if parsed["vendor"] else path.stem
            result = import_rows(session, rows, source_file=path.name, account_name=name, institution=parsed["vendor"], account_number=parsed["account_id"], via="folder") if rows else None
        elif found["kind"] == "bills":
            result = import_bills(session, parse_bills_rows(text, found["mapping"]), source_file=path.name, today=_today())
        else:
            rows = parse_csv_rows(text) if found["ready"] else []
            result = import_rows(session, rows, source_file=path.name, via="folder", **folder_account(session, path.name, rows)) if rows else None
    return f"{path.name}: {result.summary if result else 'no rows found'}"


def _today():
    from ..api.routes import today

    return today()


def detect_recurring() -> str:
    """Find recurring series in the ledger and link them to the bills and incomes already known."""
    from ..engine.borrowing import sync_borrowing
    from .detect import run_detection

    with one_writer(), session_scope() as session:
        result = run_detection(session, _today())
        sync_borrowing(session, _today())
    matched = match_payments_job()
    return f"{result['found']} series: {result['created']} new, {result['updated']} updated, {result['linked']} linked · {matched}"


def match_payments_job() -> str:
    """Match each bill period to its payment, or mark it late, then past due."""
    from ..engine.payments import match_payments

    with one_writer(), session_scope() as session:
        counts = match_payments(session, _today())
        return f"payments: {counts['paid']} paid, {counts['late']} late, {counts['past_due']} past due, {counts['scheduled']} scheduled"


def mail_sync() -> str:
    """Read every connected mailbox for new messages, read-only."""
    with session_scope() as session:
        result = CONNECTORS["mail"].sync(session, today=_today())
    if result.inserted:
        match_payments_job()  # under the write lock: never beside a detection matching the same periods
    if not result.ok:
        # A mailbox that could not be read is a failed run on the Jobs page, never a quiet "ok".
        raise RuntimeError(result.summary or "a mailbox could not be read")
    return result.summary


jobs.register("mail_sync", mail_sync, 900, "Read every connected mailbox for new messages", writes=False)  # the mailbox read takes long; its matching takes the lock
jobs.register("self_check", self_check, 3600, "Connector health, stale data, disk and database size", writes=False)
jobs.register("import_watch", import_watch, 300, "Import new files from the import folder")
jobs.register("detect_recurring", detect_recurring, 6 * 3600, "Find recurring charges and paychecks in the ledger, then match payments")
jobs.register("match_payments", match_payments_job, 24 * 3600, "Match each bill period to its payment; mark late and past due")


def review_if_due() -> str:
    from ..api.routes import today
    from ..llm.reviewer import run_if_due

    return run_if_due(session_scope, today())


jobs.register("review_if_due", review_if_due, 3600, "Ask the reviewer for notes on payday and after a confirmed change", writes=False)


def bank_sync() -> str:
    """Every live bank link whose interval has passed, through its connector; a link that fails does not stop the
    others. The interval is the owner's (Connectors › Banks › How fresh); reading more often costs nothing more,
    since Plaid bills transactions by the month per bank login, not by the read."""
    from datetime import UTC, datetime, timedelta

    from ..models import BankLink
    from ..store import get_setting

    notes: list[str] = []
    inserted = 0
    failed = False
    with session_scope() as session:
        links = session.exec(select(BankLink).where(BankLink.active == True)).all()  # noqa: E712
        if not links:
            return "no bank links"
        every = timedelta(hours=float((get_setting(session, "banks.freshness", {}) or {}).get("sync_every_hours", 4)))
        now = datetime.now(UTC)
        due = [link for link in links if link.last_sync_at is None or now - (link.last_sync_at if link.last_sync_at.tzinfo else link.last_sync_at.replace(tzinfo=UTC)) >= every - timedelta(minutes=5)]
        if not due:
            return "not due yet"
        for link in due:
            result = CONNECTORS[link.provider].sync(session, link_id=link.id)
            notes.append(result.summary)
            inserted += result.inserted
            failed = failed or not result.ok
        from ..engine.planned import settle_planned

        settled = settle_planned(session, _today())
        if settled:
            notes.append(f"{settled} planned payment{'s' if settled != 1 else ''} posted")
    if inserted:
        notes.append(detect_recurring())  # new rows may complete a series; the owner should not wait six hours
    if failed:
        raise RuntimeError("; ".join(notes))
    return "; ".join(notes)


jobs.register("bank_sync", bank_sync, 3600, "Read each linked bank for new transactions and balances, as often as Connectors › Banks says (every four hours unless changed)")


def morning_brief() -> str:
    """The brief to the phone once a day from the chosen hour, when push is configured."""
    from datetime import datetime, timezone

    from .. import notify
    from ..api.routes import today
    from ..engine.snapshot import finance_snapshot

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        if not notify.morning_brief_due(session, now):
            return "not due"
        snap = finance_snapshot(session, today())
        critical = any(b["severity"] == "critical" for b in snap["brief"])
        result = notify.send(session, f"Safe to spend {snap['safe_to_spend']['amount']:,.0f}", notify.brief_text(snap["brief"]), priority="high" if critical else "default", tags=["moneybag"])
        if result.get("ok"):
            notify.mark_brief_sent(session, now)
            return f"sent {len(snap['brief'])} lines"
        if result.get("skipped"):
            return "not due: no channel is set up"
        # A channel that refused is a failed run on the Jobs page, never a quiet one; the next run tries again.
        raise RuntimeError(f"not sent: {result.get('detail') or result.get('reason') or 'the push channel refused it'}")


jobs.register("morning_brief", morning_brief, 600, "Send the brief to the phone once a day, from the chosen hour", writes=False)


def reminders() -> str:
    """Bills due soon, payday, a card's statement day and the Sunday check-in, each once, from the chosen hour."""
    from datetime import datetime, timezone

    from ..api.routes import today
    from ..engine.reminders import run

    with session_scope() as session:
        out = run(session, datetime.now(timezone.utc), today())
    if out.get("failed"):
        # A reminder that is due and could not be delivered fails the run; it is tried again on the next one.
        raise RuntimeError(f"sent {out['sent']}, {out['failed']} not delivered: {out.get('detail') or 'the push channel refused them'}")
    return f"sent {out['sent']}" + (f" ({out['reason']})" if out.get("reason") else "")


def moves_verify() -> str:
    """Read the ledger for every move under way: confirm each switch the new account shows, note what still lands
    on the old one, and add any new recurring charge found on the old account."""
    from ..api.routes import today
    from ..engine.moves import active_moves, refresh_items, verify

    with session_scope() as session:
        n = c = a = 0
        for mv in active_moves(session):
            a += refresh_items(session, mv, today())
            c += verify(session, mv, today())["confirmed"]
            n += 1
        return f"{n} move{'s' if n != 1 else ''} read; {c} confirmed, {a} new"


jobs.register("moves_verify", moves_verify, 6 * 3600, "Confirm each switch of a bank move from the ledger, and find what still uses the old account")


jobs.register("reminders", reminders, 900, "Send the reminders you switched on, once each, from the chosen hour", writes=False)


def balance_history() -> str:
    """Today's balance of every account with one, so net worth and each investment have a line over time."""
    from ..connectors.banks import record_balance

    with session_scope() as session:
        accounts = [a for a in session.exec(select(Account).where(Account.active == True)).all() if a.balance is not None]  # noqa: E712
        for a in accounts:
            record_balance(session, a, a.balance, _today(), commit=False)
        session.commit()
        return f"{len(accounts)} balances recorded"


jobs.register("balance_history", balance_history, 24 * 3600, "Keep today's balance of every account, for net worth over time")


def restore_point_daily() -> str:
    """A copy of the whole database once a day; the last seven are kept."""
    from .. import restore

    from datetime import UTC, datetime, timedelta

    newest = next((p for p in restore.list_points() if p["kind"] == "daily"), None)
    if newest and newest["created_at"] and datetime.now(UTC) - datetime.fromisoformat(newest["created_at"]) < timedelta(hours=20):
        return f"today's copy is there: {newest['name']}"
    made = restore.make_point("daily", "Daily copy")
    return f"copied the database: {made['name']} ({made['bytes'] // 1024} KB)"


jobs.register("restore_point_daily", restore_point_daily, 6 * 3600, "Keep a copy of the whole database once a day, the last seven kept")


ALERTS = "watch.app.alerts"  # telling the phone about the app's own health: on unless the owner turns it off
OPEN = "watch.app.open"  # what is broken now and when the phone was last told


def app_watch() -> str:
    """Tell the phone when a part of the app breaks, again once a day while it stays broken, and when it is fixed.
    Only the part and where to look are sent, never a job's own error text."""
    from .. import notify
    from ..engine import apphealth
    from ..models import HealthSample

    with session_scope() as session:
        if not get_setting(session, ALERTS, True):
            return "not due: telling the phone is off"
        latest = session.exec(select(HealthSample).order_by(HealthSample.at.desc())).first()
        if latest is None:
            return "not due: no health sample yet"
        prior = get_setting(session, OPEN, {}) or {}
        messages, state = apphealth.plan_alerts(prior, json.loads(latest.checks or "[]"), datetime.now(timezone.utc))
        if not messages:
            set_setting(session, OPEN, state)
            return f"nothing new: {len(state)} part{'s' if len(state) != 1 else ''} still need{'s' if len(state) == 1 else ''} a look" if state else "nothing new"
        if len(messages) > 3:
            messages = [{"title": f"{len(messages)} changes in the app's health", "body": " ".join(m["body"] for m in messages), "link": "/settings/jobs"}]
        sent = 0
        for m in messages:
            result = notify.send(session, m["title"], m["body"], link=m["link"], tags=["warning"])
            if result.get("skipped"):
                # With nothing to send to, nothing counts as told: setting push up later tells what is open then.
                return "not due: push is off"
            sent += 1 if result.get("ok") else 0
        set_setting(session, OPEN, state)
        return f"told the phone {sent} of {len(messages)}: " + "; ".join(m["title"] for m in messages)


jobs.register("app_watch", app_watch, 3600, "Tell your phone when a part of the app itself breaks, daily while it stays broken, and when it is fixed", writes=False)

IDLE = ("nothing new", "not due", "reviewer off", "sent 0", "today's copy is there", "no off-box folder", "not due yet")


def prune_job_runs() -> str:
    """Keep the job record useful and small: runs that did nothing for two days, every other run for thirty."""
    from datetime import timedelta as _td

    from sqlmodel import delete

    now = utcnow()
    with session_scope() as session:
        old = session.exec(delete(JobRun).where(JobRun.started_at < now - _td(days=30))).rowcount or 0
        from ..models import HealthSample

        session.exec(delete(HealthSample).where(HealthSample.at < now - _td(days=90)))
        idle = 0
        for r in session.exec(select(JobRun).where(JobRun.started_at < now - _td(days=2), JobRun.ok == True)).all():  # noqa: E712
            if (r.summary or "").startswith(IDLE):
                session.delete(r)
                idle += 1
        session.commit()
    from ..db import engine as _engine
    from sqlalchemy import text as _text

    with _engine.begin() as connection:
        connection.execute(_text("PRAGMA optimize"))
    return f"removed {old + idle} old job record{'s' if old + idle != 1 else ''}"


jobs.register("prune_job_runs", prune_job_runs, 24 * 3600, "Keep job records for thirty days, and quiet runs that did nothing for two")


def offbox_copy() -> str:
    """The sealed copy in the folder off the data disk, daily or weekly as the owner chose; silent only when off."""
    from .. import offbox

    with session_scope() as session:
        if not offbox.due(session):
            return "not due, or not set up"
        made = offbox.copy_now(session)
        missing = f"; {made['documents_missing']} document file(s) were not on disk" if made.get("documents_missing") else ""
        return f"sealed {made['file']} ({made['bytes'] // 1024} KB, {made['documents']} document file(s)) into {made['folder']}{missing}"


def restore_drill() -> str:
    """Once a week, open the newest copy on its own and prove it holds what it should."""
    from datetime import UTC, datetime, timedelta

    from .. import offbox
    from ..store import get_setting

    with session_scope() as session:
        last = get_setting(session, offbox.DRILL, None)
        if last and datetime.now(UTC) - datetime.fromisoformat(last["at"]) < timedelta(days=6, hours=23):
            return f"last drill {last['at'][:10]}: {'passed' if last['ok'] else 'failed'}"
        result = offbox.drill(session)
        if not result["ok"]:
            raise RuntimeError(result["detail"])
        return result["detail"]


jobs.register("offbox_copy", offbox_copy, 3600, "Seal a copy of the database and the kept documents into the off-box folder, daily or weekly as set on Backups")
jobs.register("restore_drill", restore_drill, 6 * 3600, "Once a week, open the newest copy on its own and prove it holds what it should", writes=False)



def watch_daily() -> str:
    """The assistant's daily look at the ledger: what it finds new is told once, through push when it is set up."""
    from .. import notify
    from ..engine.watch import to_announce

    with session_scope() as session:
        new = to_announce(session, _today())
        if not new:
            return "nothing new"
        sent = notify.send(session, f"{len(new)} thing{'s' if len(new) != 1 else ''} worth a look", "\n".join(f["text"] for f in new[:4]), link="/today", tags=["eyes"])
        if sent.get("ok") or sent.get("skipped"):
            return f"{len(new)} found; " + ("told" if sent.get("ok") else "not sent: no channel is set up (they are on the Watch page)")
        raise RuntimeError(f"{len(new)} found, not told: {sent.get('detail') or sent.get('reason') or 'the push channel refused it'} (they are on the Watch page)")


jobs.register("watch_daily", watch_daily, 6 * 3600, "Look over the ledger for unusual or double charges, new subscriptions and price rises; tell each once", writes=False)


def recap_keep() -> str:
    """Keep the plan for the coming paycheck, so it can be set beside what happened."""
    from ..engine.recap import record_plan

    with session_scope() as session:
        kept = record_plan(session, _today())
        return f"kept the plan for {kept['start']}" if kept else "not due"


jobs.register("recap_keep", recap_keep, 6 * 3600, "Keep the plan for the coming paycheck, to compare with what happened")


def week_recap_push() -> str:
    """Sunday evening: the week in one line on the phone."""
    from .. import notify
    from ..clock import local_now
    from ..engine.recap import week_recap

    now = local_now()
    if now.weekday() != 6 or now.hour < 18:
        return "not due"
    with session_scope() as session:
        key = f"recap.week.{now.date().isoformat()}"
        if get_setting(session, key, None):
            return "not due"
        recap = week_recap(session, _today())
        sent = notify.send(session, "Your week", recap["text"], link="/recap", tags=["calendar"])
        if not (sent.get("ok") or sent.get("skipped")):
            raise RuntimeError(f"not sent: {sent.get('detail') or sent.get('reason') or 'the push channel refused it'}")  # tried again next hour
        set_setting(session, key, True)
        return "sent" if sent.get("ok") else "not sent: no channel is set up"


jobs.register("week_recap_push", week_recap_push, 3600, "Sunday evening, the week's spending in one line on the phone", writes=False)
