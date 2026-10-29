"""Sweep 14: concurrency. A simulated week in which, each day, two job threads run every registered job in their
own shuffled order (as the scheduler's threads and a "Run now" would) while the owner taps through the routes at
the same time: confirms what waits, says "I paid it", adds a bill, rejects a fact, undoes an import, reads the
home screen. Every write must either succeed or say in a sentence why not (a 4xx with a detail); never a 500 and
never "database is locked" reaching a reply or a job's record. At the week's end the registers keep the quarter
harness's invariants: no orphans, no bill twice, no period paid twice, no series split. Synthetic data only."""

from __future__ import annotations

import random
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_sweep14_bugs_common import clean_world, EXTERNAL, TODAY, Box, build_world, make_due, orphans  # noqa: F401 - clean_world is a fixture

from app.api import routes
from app.db import engine
from app.jobs.scheduler import jobs
from app.main import app
from app.models import Bill, ExtractedFact, ImportBatch, InstallmentPlan, JobRun, Payment, RecurringSeries

pytestmark = pytest.mark.usefixtures("clean_world")

DAYS = 7
ROUNDS = 3  # each thread runs every job three times a day, so the taps meet them mid-write
# The reviewer needs a model and writes nothing the registers hold. The mail read runs alone before the threads: beside
# another writer its per-message savepoint fails at once with "database is locked" (test_sweep14_bugs.py, outside
# this sweep's files), which is reported, not counted twice here.
SKIP_JOBS = {"review_if_due", "mail_sync"}


def _week_rows(day) -> list[dict]:  # noqa: ANN001 - a date
    from test_sweep8_bugs_quarter import future_rows

    rows = future_rows(TODAY - timedelta(days=1), TODAY + timedelta(days=DAYS + 1)).get(day, [])
    return [{"transaction_id": f"week-{day.isoformat()}-{n}", "account_id": EXTERNAL[r["account"]], "amount": r["amount"], "date": r["date"].isoformat(), "name": r["merchant"].upper(),
             "merchant_name": r["merchant"], "pending": False, "category": [r["category"]]} for n, r in enumerate(rows)]


def _sentence(response) -> bool:  # noqa: ANN001
    try:
        detail = response.json().get("detail")
    except ValueError:
        return False
    return isinstance(detail, str) and len(detail.split()) >= 3 and "locked" not in detail.lower()


def test_a_week_of_jobs_and_taps_at_once_keeps_every_invariant(owner: TestClient):
    vendor = build_world(owner)
    owner_raw = TestClient(app, raise_server_exceptions=False)
    owner_raw.cookies.update(owner.cookies)
    owner_raw.headers.update(owner.headers)
    rng = random.Random(14)
    names = [n for n in jobs.specs if n not in SKIP_JOBS]
    bad: list[str] = []
    taps = 0
    codes: dict[int, int] = {}
    started = time.time()
    for k in range(DAYS):
        day = TODAY + timedelta(days=k)
        routes.set_today_override(day)
        vendor.rows = vendor.rows + _week_rows(day - timedelta(days=1))
        if k % 3 == 1:
            from test_sweep8_bugs_quarter import raw_mail, upcoming_bills_mail

            text = upcoming_bills_mail(day)
            if text:
                Box.messages[max(Box.messages) + 1] = raw_mail("This week's upcoming bills", text, day)
        make_due()
        jobs.run("mail_sync")
        orders = [rng.sample(names, len(names)) * ROUNDS for _ in range(2)]
        threads = [threading.Thread(target=lambda order=order: [jobs.run(n) for n in order], daemon=True) for order in orders]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads) or taps < 12 * (k + 1):
            taps += 1
            for method, path, body in _taps(rng, k, taps):
                r = owner_raw.request(method, path, json=body)
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
                if r.status_code >= 500 or (r.status_code >= 400 and not _sentence(r)) or "database is locked" in r.text:
                    bad.append(f"day {k} {method} {path}: {r.status_code} {r.text[:160]}")
        for t in threads:
            t.join(timeout=120)
    with Session(engine) as session:
        failed = [f"{r.name}: {r.summary[:160]}" for r in session.exec(select(JobRun).where(JobRun.ok == False)).all()]  # noqa: E712
        bills = [(b.name, round(b.amount, 2)) for b in session.exec(select(Bill).where(Bill.active == True)).all()]  # noqa: E712
        periods = [(p.bill_id, p.due_date) for p in session.exec(select(Payment)).all()]
        series = [(s.merchant_key, s.cadence) for s in session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).all() if s.kind != "ignored"]  # noqa: E712
    locked = [f for f in failed if "locked" in f.lower() or "busy" in f.lower()]
    assert not bad, "\n".join(bad[:20])
    assert not locked, locked
    assert not orphans(), orphans()
    assert len(bills) == len(set(bills)), sorted(b for b in bills if bills.count(b) > 1)
    assert len(periods) == len(set(periods)), "a period holds two payments"
    assert len(series) == len(set(series)), sorted(s for s in series if series.count(s) > 1)
    assert taps >= 12 * DAYS and codes.get(200, 0) >= taps and time.time() - started < 50


def _taps(rng: random.Random, day: int, n: int) -> list[tuple[str, str, dict | None]]:
    """One round of the owner's taps, picked from what is on the registers now."""
    out: list[tuple[str, str, dict | None]] = [("GET", "/api/v1/snapshot", None)]
    with Session(engine) as session:
        facts = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()
        plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
        kept = session.exec(select(ImportBatch).where(ImportBatch.status == "kept")).all()
    if facts:
        f = rng.choice(facts)
        if n % 5 == 0:
            out.append(("POST", f"/api/v1/facts/{f.id}/reject", None))
        else:
            out.append(("POST", f"/api/v1/facts/{f.id}/confirm", {"amount": 40.0, "remaining": 4} if f.kind == "installment" else {}))
    if plans and n % 4 == 0:
        out.append(("POST", f"/api/v1/borrowing/plans/{rng.choice(plans).id}/paid", {}))
    if n % 6 == 0:
        out.append(("POST", "/api/v1/bills", {"name": f"Example Tap Bill {day}-{n}", "amount": 20.0 + n, "due_day": 1 + n % 28}))
    if kept and n % 9 == 0:
        out.append(("POST", f"/api/v1/imports/{rng.choice(kept).id}/undo", None))
    return out
