"""The scheduler and the first jobs: the hourly self-check and the import-folder watch."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from ..config import settings
from ..connectors import CONNECTORS
from ..db import session_scope
from ..models import Account, JobRun, Transaction, utcnow
from ..store import get_setting, set_setting

log = logging.getLogger("fa.jobs")


@dataclass
class JobSpec:
    name: str
    func: Callable[[], str]
    seconds: int
    description: str


class Jobs:
    def __init__(self) -> None:
        self.specs: dict[str, JobSpec] = {}
        self.scheduler: BackgroundScheduler | None = None

    def register(self, name: str, func: Callable[[], str], seconds: int, description: str) -> None:
        self.specs[name] = JobSpec(name, func, seconds, description)

    def run(self, name: str) -> JobRun:
        spec = self.specs[name]
        with session_scope() as session:
            run = JobRun(name=name)
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
        ok, summary = True, ""
        try:
            summary = spec.func() or ""
        except Exception as error:  # noqa: BLE001  (a job must never take the scheduler down)
            ok, summary = False, f"{type(error).__name__}: {error}"
            log.exception("job %s failed", name)
        with session_scope() as session:
            run = session.get(JobRun, run_id)
            run.finished_at, run.ok, run.summary = utcnow(), ok, summary[:2000]
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def start(self) -> None:
        if self.scheduler is not None:
            return
        self.scheduler = BackgroundScheduler(timezone="UTC")
        for spec in self.specs.values():
            self.scheduler.add_job(lambda n=spec.name: self.run(n), IntervalTrigger(seconds=spec.seconds), id=spec.name, max_instances=1, coalesce=True)
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
        accounts = session.exec(select(Account)).all()
        stale = []
        now = datetime.now(timezone.utc)
        for a in accounts:
            known = a.balance_at
            if known is not None and known.tzinfo is None:
                known = known.replace(tzinfo=timezone.utc)
            if known is not None and (now - known).days > 2:
                stale.append(a.name)
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
        if usage.free < 2e9:
            problems.append("under 2 GB free")
        report = {
            "at": utcnow().isoformat(),
            "free_gb": round(usage.free / 1e9, 2),
            "db_mb": round(db_bytes / 1e6, 2),
            "last_transaction": last_tx.isoformat() if last_tx else None,
            "public_address": {"answers_from_inside": reachable, "last_seen_from_outside": seen_outside},
            "stale_accounts": stale,
            "connectors": sorted(CONNECTORS),
            "sources": sources,
            "public_url_reachable": reachable,
            "problems": problems,
        }
        set_setting(session, "selfcheck", report)
        return f"{report['free_gb']} GB free · db {report['db_mb']} MB · last transaction {report['last_transaction']} · {len(problems)} problem(s)" + (": " + "; ".join(problems) if problems else "")


def import_watch() -> str:
    """Import any new file in the import folder, then re-run detection. Keeps a record of what it has taken."""
    settings.ensure_dirs()
    with session_scope() as session:
        processed = {tuple(p) for p in get_setting(session, "imports.processed", [])}
        taken: list[str] = []
        for path in sorted(settings.import_root.rglob("*.pdf")):
            stamp = (str(path.relative_to(settings.import_root)), int(path.stat().st_mtime))
            if stamp in processed:
                continue
            from ..engine.borrowing import statement_fact_from_file
            from ..engine.paystubs import paystub_fact_from_file

            data = path.read_bytes()
            applied = statement_fact_from_file(session, data, path.name, _today(), "import-folder")
            if applied:
                taken.append(f"{path.name}: an Affirm statement, {applied['loans']} loans landed")
            else:
                fact = paystub_fact_from_file(session, data, path.name, _today(), "import-folder")
                taken.append(f"{path.name}: {'a paystub, on Needs you to confirm' if fact else 'not read as a paystub or a statement'}")
            processed.add(stamp)
        for path in sorted(list(settings.import_root.glob("*.csv")) + list(settings.import_root.glob("*.ofx")) + list(settings.import_root.glob("*.qfx")) + list(settings.import_root.glob("*.qbo"))):
            stamp = (path.name, int(path.stat().st_mtime))
            if stamp in processed:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            from ..connectors.files import detect, import_bills, import_rows, parse_bills_rows, parse_csv_rows, parse_ofx
            from ..connectors.rocketmoney import parse_csv as parse_rocketmoney

            if path.suffix.lower() == ".csv" and parse_rocketmoney(text, path.name) is not None:
                result = CONNECTORS["rocketmoney"].sync(session, text=text, source_file=path.name)
            else:
                found = detect(text)
                if found["kind"] == "ofx":
                    parsed = parse_ofx(text)
                    rows = parsed["rows"]
                    name = f"{parsed['vendor']} {parsed['account_type'] or 'account'}".strip() if parsed["vendor"] else path.stem
                    result = import_rows(session, rows, source_file=path.name, account_name=name, institution=parsed["vendor"]) if rows else None
                elif found["kind"] == "bills":
                    result = import_bills(session, parse_bills_rows(text, found["mapping"]), source_file=path.name)
                else:
                    rows = parse_csv_rows(text) if found["ready"] else []
                    result = import_rows(session, rows, source_file=path.name, account_name=path.stem) if rows else None
            taken.append(f"{path.name}: {result.summary if result else 'no rows found'}")
            processed.add(stamp)
        set_setting(session, "imports.processed", sorted(processed))
    if taken:
        detect_recurring()
    return "; ".join(taken) if taken else "nothing new"


def _today():
    from ..api.routes import today

    return today()


def detect_recurring() -> str:
    """Find recurring series in the ledger and link them to the bills and incomes already known."""
    from ..engine.borrowing import sync_borrowing
    from .detect import run_detection

    with session_scope() as session:
        result = run_detection(session, _today())
        sync_borrowing(session, _today())
    matched = match_payments_job()
    return f"{result['found']} series: {result['created']} new, {result['updated']} updated, {result['linked']} linked · {matched}"


def match_payments_job() -> str:
    """Match each bill period to its payment, or mark it late, then past due."""
    from ..engine.payments import match_payments

    with session_scope() as session:
        counts = match_payments(session, _today())
        return f"payments: {counts['paid']} paid, {counts['late']} late, {counts['past_due']} past due, {counts['scheduled']} scheduled"


def mail_sync() -> str:
    """Read every connected mailbox for new messages, read-only."""
    with session_scope() as session:
        result = CONNECTORS["mail"].sync(session, today=_today())
    if result.inserted:
        with session_scope() as session:
            from ..engine.payments import match_payments

            match_payments(session, _today())
    return result.summary


jobs.register("mail_sync", mail_sync, 900, "Read every connected mailbox for new messages")
jobs.register("self_check", self_check, 3600, "Connector health, stale data, disk and database size")
jobs.register("import_watch", import_watch, 300, "Import new files from the import folder")
jobs.register("detect_recurring", detect_recurring, 6 * 3600, "Find recurring charges and paychecks in the ledger, then match payments")
jobs.register("match_payments", match_payments_job, 24 * 3600, "Match each bill period to its payment; mark late and past due")


def review_if_due() -> str:
    from ..api.routes import today
    from ..llm.reviewer import run_if_due

    return run_if_due(session_scope, today())


jobs.register("review_if_due", review_if_due, 3600, "Ask the reviewer for notes on payday and after a confirmed change")


def bank_sync() -> str:
    """Every live bank link, through its connector; a link that fails does not stop the others."""
    from ..models import BankLink

    notes: list[str] = []
    inserted = 0
    with session_scope() as session:
        links = session.exec(select(BankLink).where(BankLink.active == True)).all()  # noqa: E712
        if not links:
            return "no bank links"
        for link in links:
            result = CONNECTORS[link.provider].sync(session, link_id=link.id)
            notes.append(result.summary)
            inserted += result.inserted
        from ..engine.planned import settle_planned

        settled = settle_planned(session, _today())
        if settled:
            notes.append(f"{settled} planned payment{'s' if settled != 1 else ''} posted")
    if inserted:
        notes.append(detect_recurring())  # new rows may complete a series; the owner should not wait six hours
    return "; ".join(notes)


jobs.register("bank_sync", bank_sync, 4 * 3600, "Read every linked bank for new transactions and balances")


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
        return f"not sent: {result.get('detail') or result.get('reason')}"


jobs.register("morning_brief", morning_brief, 600, "Send the brief to the phone once a day, from the chosen hour")


def balance_history() -> str:
    """Today's balance of every account with one, so net worth and each investment have a line over time."""
    from ..connectors.banks import record_balance

    with session_scope() as session:
        accounts = [a for a in session.exec(select(Account).where(Account.active == True)).all() if a.balance is not None]  # noqa: E712
        for a in accounts:
            record_balance(session, a, a.balance, utcnow().date(), commit=False)
        session.commit()
        return f"{len(accounts)} balances recorded"


jobs.register("balance_history", balance_history, 24 * 3600, "Keep today's balance of every account, for net worth over time")
