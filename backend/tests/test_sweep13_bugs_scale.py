"""Sweep 13: scale (the roadmap's M8.6). Five years of rows in the ledger (about 25,000 over the household's four
accounts), a hundred thousand message references with twenty thousand facts (five hundred still pending), 300
recurring series, 60 bills, five years of balance history, the job record as the prune job leaves it, and five years
of audit rows. Every GET route and every job is timed once; a route over 1.5 s or a job over 30 s is a finding, as
is a scan that grows with the mailbox or the ledger where an index would not.

The rows are written with executemany, so the generation itself takes a second or two; the routes and the jobs
run once each. Synthetic data only."""

from __future__ import annotations

import json
import random
import time
import tracemalloc
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.api import routes
from app.db import engine
from app.engine.household import seed
from app.engine.recurring import merchant_key
from app.jobs.scheduler import jobs
from app.main import app
from app.models import (
    Account,
    AuditEntry,
    BalanceSnapshot,
    Bill,
    ExtractedFact,
    HealthSample,
    JobRun,
    MailAccount,
    MessageRef,
    Payment,
    RecurringSeries,
    Transaction,
)

TODAY = date(2026, 10, 14)
YEARS = 5
ROUTE_LIMIT = 1.5  # seconds a screen's read may take
JOB_LIMIT = 30.0  # seconds one run of a job may take
SENDERS = [f"billing{n}@example.test" for n in range(40)] + [f"alerts{n}@bank{n}.example.test" for n in range(10)]
KINDS = ["bill_due", "statement", "receipt", "bank_alert", "payment_confirmation", "", "", "newsletter"]
# Routes that need a body or a vendor, or stream a file; they are not screens.
SKIP = {"/api/v1/exports/file", "/api/v1/reports/custom/tax/pack", "/api/v1/calendar/feed", "/api/v1/exports/rocketmoney.csv", "/api/v1/ha", "/api/v1/business/invoices/{invoice_id}/print",
        "/api/v1/documents/{doc_id}/file", "/api/v1/documents/{doc_id}/pages/{page}", "/api/v1/mail/messages/{message_id}/attachments/{name}", "/oauth/consent", "/authorize"}
PARAMS = {"/api/v1/reports/custom": {"rows": "category", "period": "month"}, "/api/v1/bills/{bill_id}/move_check": {"day": 5}, "/api/v1/check": {"amount": 40}, "/api/v1/receipts/candidates": {"amount": 40, "bought_on": TODAY.isoformat()}, "/api/v1/transactions/search": {"q": "example"}, "/api/v1/documents/search": {"q": "example"}}


def _hash(i: int, salt: str) -> str:
    return sha256(f"{salt}|{i}".encode()).hexdigest()


def fill(session: Session) -> dict[str, int]:
    """Five years of everything, written in bulk."""
    rng = random.Random(13)
    ids = {a.external_id: a.id for a in session.exec(select(Account)).all()}
    chk, card = ids["checking"], ids["card"]
    start = TODAY - timedelta(days=365 * YEARS)
    seeded_from = TODAY - timedelta(days=366)  # the household's own rows cover the last year
    # 300 recurring merchants, each a monthly charge on its own day for five years, then everyday spending.
    merchants = [(f"Example Service {n:03d}", round(5 + (n * 7.31) % 140, 2), 1 + n % 28, chk if n % 3 else card) for n in range(300)]
    rows: list[dict] = []
    n = 0

    def add(d: date, account: int, merchant: str, amount: float, category: str, **flags) -> None:
        nonlocal n
        n += 1
        rows.append({"account_id": account, "date": d, "posted_date": None, "merchant": merchant, "raw_name": merchant.upper(), "description": "", "amount": amount, "category": category, "note": "",
                     "is_transfer": flags.get("is_transfer", False), "is_card_payment": flags.get("is_card_payment", False), "is_income": flags.get("is_income", False), "is_ignored": False, "pending": False, "tags": "",
                     "source": "csv", "source_ref": "", "row_hash": _hash(n, "tx"), "import_id": None, "business_id": None, "business_share": 1.0, "tax_line": "", "created_at": datetime(d.year, d.month, d.day, 6, tzinfo=UTC)})

    month = date(start.year, start.month, 1)
    while month <= TODAY:
        for name, amount, day, account in merchants:
            d = date(month.year, month.month, day)
            if start <= d <= TODAY:
                add(d, account, name, amount, "Bills & Utilities" if account == chk else "Software")
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    d = start
    payday = start + timedelta(days=(4 - start.weekday()) % 7)
    while d < seeded_from:
        for _ in range(rng.choice([2, 3, 4, 5])):
            add(d, rng.choice([chk, chk, card]), rng.choice(["Example Grocery", "Example Market", "Example Fuel", "Example Coffee", "Example Online Store", "Example Pharmacy"]), round(rng.uniform(6, 90), 2), rng.choice(["Groceries", "Gas", "Dining & Drinks", "Shopping"]))
        if d == payday:
            add(d, chk, "Example Employer Payroll", -2240.0, "Income", is_income=True)
            payday += timedelta(days=14)
        d += timedelta(days=1)
    session.connection().execute(Transaction.__table__.insert(), rows)  # type: ignore[attr-defined]
    # 300 series, 60 bills (20 of them from a series), five years of their periods.
    series = [{"merchant_key": merchant_key(name), "merchant": name, "category": "Bills & Utilities", "cadence": "monthly", "typical_amount": amount, "latest_amount": amount, "tolerance": 0.15, "charges_seen": 60,
               "first_seen": start, "last_date": TODAY - timedelta(days=30 - day), "next_expected": TODAY + timedelta(days=day), "active": True, "kind": "bill" if n < 60 else "subscription" if n < 200 else "unknown",
               "confirmed": n < 200, "suggested_kind": "", "origin": ""} for n, (name, amount, day, _) in enumerate(merchants)]
    session.connection().execute(RecurringSeries.__table__.insert(), series)  # type: ignore[attr-defined]
    first_series = session.exec(select(RecurringSeries.id).where(RecurringSeries.merchant_key == merchant_key("Example Service 000"))).one()
    bills = [{"name": name, "kind": "bill", "amount": amount, "amount_mode": "fixed", "cadence": "monthly", "due_day": day, "anchor_date": None, "autopay": n % 2 == 0, "pay_from_account_id": chk, "grace_days": 0, "late_fee": 0.0,
              "movable": False, "paid_from": "due", "move_window": "", "category": "Bills & Utilities", "series_id": first_series + n if n < 20 else None, "active": True, "created_on": start, "starts_on": None, "ends_on": None,
              "home_id": None, "pays_to": "", "utility_id": None, "match_names": ""} for n, (name, amount, day, _) in enumerate(merchants[:60])]
    session.connection().execute(Bill.__table__.insert(), bills)  # type: ignore[attr-defined]
    first_bill = session.exec(select(Bill.id).where(Bill.name == "Example Service 000")).one()
    periods = []
    for n, (_, amount, day, _) in enumerate(merchants[:60]):
        month = date(start.year, start.month, 1)
        while month < TODAY - timedelta(days=70):
            due = date(month.year, month.month, day)
            if due >= start:
                periods.append({"bill_id": first_bill + n, "due_date": due, "amount": amount, "status": "paid", "paid_date": due, "transaction_id": None, "evidence": "transaction:0"})
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    session.connection().execute(Payment.__table__.insert(), periods)  # type: ignore[attr-defined]
    # A mailbox with a hundred thousand messages over five years, twenty thousand facts, five hundred pending.
    box = MailAccount(label="Example Mail", kind="imap", host="mail.example.test", port=993, username="owner@example.test", active=False, messages_seen=100_000)
    session.add(box)
    session.commit()
    refs = []
    for i in range(100_000):
        when = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(minutes=i * (YEARS * 365 * 24 * 60 // 100_000))
        refs.append({"account_id": box.id, "folder": "INBOX", "uid": i + 1, "message_id": f"<m{i}@example.test>", "sender": SENDERS[i % len(SENDERS)], "sender_name": "Example Sender", "subject": f"Your statement {i}",
                     "date": when, "has_attachments": i % 7 == 0, "attachment_names": "statement.pdf" if i % 7 == 0 else "", "snippet": "Your statement is ready.", "kind": KINDS[i % len(KINDS)], "created_at": when, "trust": "ok", "trust_why": ""})
    session.connection().execute(MessageRef.__table__.insert(), refs)  # type: ignore[attr-defined]
    first_ref = session.exec(select(MessageRef.id).where(MessageRef.uid == 1, MessageRef.account_id == box.id)).one()
    facts = []
    for i in range(20_000):
        ref = i * 5
        when = refs[ref]["date"]
        pending = i >= 19_500
        fields = {"amount": round(20 + (i % 90), 2), "due_date": (when + timedelta(days=9)).date().isoformat(), "biller": f"Example Biller {i % 60}", "account_last4": "1234"}
        facts.append({"kind": KINDS[i % 5] or "bill_due", "message_ref_id": first_ref + ref, "fields": json.dumps(fields), "confidence": 0.9, "status": "pending" if pending else ("applied" if i % 3 else "rejected"),
                      "excerpt": "Amount due", "produced_by": "rules", "applied_to": "" if pending else "bill:1", "created_at": when, "resolved_at": None if pending else when + timedelta(hours=3)})
    session.connection().execute(ExtractedFact.__table__.insert(), facts)  # type: ignore[attr-defined]
    # Balance history: every account, every day for five years.
    balances = [{"account_id": a, "date": start + timedelta(days=k), "balance": round(300 + 50 * ((k * a) % 17), 2)} for a in ids.values() for k in range(365 * YEARS)]
    session.connection().execute(BalanceSnapshot.__table__.insert(), balances)  # type: ignore[attr-defined]
    # The job record as the prune job leaves it: every run for two days, the runs that did something for thirty.
    now = datetime.now(UTC)
    runs = []
    for name, spec in jobs.specs.items():
        per_day = max(1, 86400 // spec.seconds)
        for day_back in range(30):
            for k in range(per_day):
                at = now - timedelta(days=day_back, seconds=k * spec.seconds)
                idle = (k + day_back) % 4 != 0
                if idle and day_back >= 2:
                    continue
                runs.append({"name": name, "started_at": at, "finished_at": at + timedelta(seconds=2), "ok": True, "summary": "nothing new" if idle else "3 series: 1 new"})
    session.connection().execute(JobRun.__table__.insert(), runs)  # type: ignore[attr-defined]
    session.connection().execute(HealthSample.__table__.insert(), [{"at": now - timedelta(hours=h), "free_gb": 40.0, "db_mb": 300.0, "backup_age_hours": 3.0, "checks": "[]"} for h in range(24 * 90)])  # type: ignore[attr-defined]
    session.connection().execute(AuditEntry.__table__.insert(), [{"at": now - timedelta(minutes=i * 131), "client": "owner", "client_kind": "owner", "tool": "finance.snapshot", "args_hash": "", "rows": 1, "redaction": "full"} for i in range(20_000)])  # type: ignore[attr-defined]
    session.commit()
    with engine.begin() as connection:
        connection.execute(text("ANALYZE"))
    return {"transactions": len(session.exec(select(Transaction.id)).all()), "refs": 100_000, "facts": 20_000, "series": 300, "bills": len(session.exec(select(Bill.id)).all()), "job_runs": len(runs)}


def get_routes() -> list[str]:
    paths = app.openapi()["paths"]
    return sorted(p for p, ops in paths.items() if "get" in ops and p not in SKIP)


def with_ids(path: str, session: Session) -> str | None:
    """A route with a path parameter, filled with a row that exists; None when nothing fits."""
    if "{" not in path:
        return path
    bill = session.exec(select(Bill.id)).first()
    ref = session.exec(select(MessageRef.id)).first()
    fills = {"{bill_id}": bill, "{message_id}": ref, "{year}": TODAY.year, "{business_id}": None, "{move_id}": None, "{home_id}": None}
    for key, value in fills.items():
        if key in path:
            if value is None:
                return None
            path = path.replace(key, str(value))
    return path if "{" not in path else None


PEAK_LIMIT_MB = 64.0  # what one read or one run may allocate at its peak (a five-year mailbox read whole was over a hundred)


def measured(fn):
    """The call's result, the seconds it took and the megabytes it held at its peak."""
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        out = fn()
    finally:
        took = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return out, took, peak / (1024 * 1024)


@pytest.fixture
def big(owner: TestClient) -> dict[str, int]:
    routes.set_today_override(TODAY)
    with Session(engine) as session:
        seed(session)
        return fill(session)


def test_five_years_and_a_hundred_thousand_messages_keep_every_screen_and_job_quick(owner: TestClient, big: dict[str, int]):
    assert big["transactions"] >= 23_000 and big["bills"] >= 60, big
    slow: list[str] = []
    unfilled: list[str] = []
    timings: list[tuple[str, float, float]] = []
    with Session(engine) as session:
        targets = [(template, p) for template, p in ((path, with_ids(path, session)) for path in get_routes()) if p]
    for template, path in targets:
        response, took, peak = measured(lambda: owner.get(path, params=PARAMS.get(template)))
        timings.append((f"GET {path}", took, peak))
        assert response.status_code in (200, 204, 404, 400, 409, 422), (path, response.status_code, response.text[:200])
        if response.status_code == 422:
            unfilled.append(f"{path}: {response.text[:120]}")
        if took > ROUTE_LIMIT:
            slow.append(f"GET {path} took {took:.2f}s")
        if peak > PEAK_LIMIT_MB:
            slow.append(f"GET {path} held {peak:.0f} MB at its peak")
    for name in jobs.specs:
        run, took, peak = measured(lambda: jobs.run(name))
        timings.append((f"job {name}", took, peak))
        assert run.ok, (name, run.summary)
        if took > JOB_LIMIT:
            slow.append(f"job {name} took {took:.2f}s: {run.summary[:120]}")
        if peak > PEAK_LIMIT_MB:
            slow.append(f"job {name} held {peak:.0f} MB at its peak: {run.summary[:120]}")
    # The mail sync looks each new message up by its Message-ID (connectors/mail.py) to keep a resent or a forwarded
    # copy from landing twice: at a hundred thousand references that was a scan of the whole table per message.
    with engine.connect() as connection:
        plan = " ".join(str(r[3]) for r in connection.execute(text("EXPLAIN QUERY PLAN SELECT id FROM message_refs WHERE account_id = 1 AND message_id = '<m5@example.test>'")).all())
    assert "ix_message_refs_message_id" in plan, plan
    table = "\n".join(f"{took:7.3f}s {peak:6.1f} MB  {what}" for what, took, peak in sorted(timings, key=lambda t: -t[1])[:20])
    print(f"\nrows {big}\n{table}\nunfilled: {unfilled}")
    assert not unfilled, unfilled
    assert not slow, "\n".join(slow) + "\n\n" + table
