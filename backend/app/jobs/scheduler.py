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
        report = {
            "at": utcnow().isoformat(),
            "free_gb": round(usage.free / 1e9, 2),
            "db_mb": round(db_bytes / 1e6, 2),
            "last_transaction": last_tx.isoformat() if last_tx else None,
            "stale_accounts": stale,
            "connectors": sorted(CONNECTORS),
        }
        set_setting(session, "selfcheck", report)
        return f"{report['free_gb']} GB free · db {report['db_mb']} MB · last transaction {report['last_transaction']}"


def import_watch() -> str:
    """Import any new file in the import folder, then re-run detection. Keeps a record of what it has taken."""
    settings.ensure_dirs()
    with session_scope() as session:
        processed = {tuple(p) for p in get_setting(session, "imports.processed", [])}
        taken: list[str] = []
        for path in sorted(settings.import_root.glob("*.csv")):
            stamp = (path.name, int(path.stat().st_mtime))
            if stamp in processed:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            result = CONNECTORS["rocketmoney"].sync(session, text=text, source_file=path.name)
            taken.append(f"{path.name}: {result.summary}")
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
    from .detect import run_detection

    with session_scope() as session:
        result = run_detection(session, _today())
    matched = match_payments_job()
    return f"{result['found']} series: {result['created']} new, {result['updated']} updated, {result['linked']} linked · {matched}"


def match_payments_job() -> str:
    """Match each bill period to its payment, or mark it late, then past due."""
    from ..engine.payments import match_payments

    with session_scope() as session:
        counts = match_payments(session, _today())
        return f"payments: {counts['paid']} paid, {counts['late']} late, {counts['past_due']} past due, {counts['scheduled']} scheduled"


jobs.register("self_check", self_check, 3600, "Connector health, stale data, disk and database size")
jobs.register("import_watch", import_watch, 300, "Import new files from the import folder")
jobs.register("detect_recurring", detect_recurring, 6 * 3600, "Find recurring charges and paychecks in the ledger, then match payments")
jobs.register("match_payments", match_payments_job, 24 * 3600, "Match each bill period to its payment; mark late and past due")
