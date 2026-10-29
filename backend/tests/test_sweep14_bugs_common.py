"""Sweep 14 helpers: a small world every job has something to do in, a snapshot of it, a commit that fails on its
Nth call, and the end state as a comparable value.

No tests here; the crash, process-death and concurrency drills (test_sweep14_bugs*.py) share these.

The world: the synthetic household, one Plaid link behind a scripted vendor whose answers depend only on the cursor
it is given (so a sync that never stored its cursor is answered the same again), one mailbox behind a scripted IMAP
box (the weekly bills list, a lender's loan confirmation, a paystub by mail), and an import folder holding a bank
CSV, a bills list, a lender's statement PDF and a paystub PDF. Synthetic data only."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, SQLModel, select

from app import crypto, restore
from app.api import routes
from app.config import settings
from app.connectors import CONNECTORS
from app.db import bump_version, engine
from app.engine.household import seed
from app.models import Account, MailAccount

START = date(2026, 10, 14)  # the household's own today
TODAY = START + timedelta(days=21)  # the day the jobs run: three weeks of rows wait at the bank
EXTERNAL = {"checking": "acc-chk", "card": "acc-card", "store": "acc-store", "loan": "acc-loan"}
PAGE = 20  # rows per /transactions/sync page

# Rows read by none of the comparisons: the record of runs, health samples, the audit log, sign-ins.
NOISE_TABLES = {"job_runs", "health_samples", "audit_entries", "web_sessions", "oauth_tokens", "oauth_codes", "oauth_clients", "api_keys", "review_runs", "passkeys", "people"}
NOISE_SETTINGS = {"jobs.detected_through", "jobs.matched_through", "selfcheck", "usage.calls", "apphealth.last", "review.pending", "datacheck.last", "public.last_seen"}


class Crash(Exception):
    """The injected failure: what a full disk, a lost connection or a bug raises at a commit."""


# ---- the scripted vendors ----------------------------------------------------------------------------------


def bank_rows() -> list[dict]:
    """Three weeks of the household's rows after its own today, as Plaid sends them, in a fixed order."""
    from test_sweep8_bugs_quarter import future_rows

    by_day = future_rows(START, TODAY)
    out = []
    n = 0
    for day in sorted(by_day):
        if day >= TODAY:
            continue
        for r in by_day[day]:
            n += 1
            out.append({"transaction_id": f"crash-{n}", "account_id": EXTERNAL[r["account"]], "amount": r["amount"], "date": r["date"].isoformat(), "name": r["merchant"].upper(),
                        "merchant_name": r["merchant"], "pending": False, "category": [r["category"]]})
    return out


class CursorPlaid:
    """Plaid's endpoints, answered from the cursor alone: cursor "" is the start, "p<k>" is after row k. A sync that
    crashed before storing its cursor is answered exactly as before."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        path = request.url.path
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-sandbox-abc", "item_id": "item-1"})
        if path == "/item/get":
            return httpx.Response(200, json={"status": {"transactions": {"last_successful_update": datetime(TODAY.year, TODAY.month, TODAY.day, 6, tzinfo=timezone.utc).isoformat()}}})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": [
                {"account_id": "acc-chk", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": 812.0, "available": 812.0}},
                {"account_id": "acc-card", "name": "Rewards Card", "mask": "9012", "type": "credit", "subtype": "credit card", "balances": {"current": 2310.0}},
                {"account_id": "acc-store", "name": "Store Card", "mask": "5678", "type": "credit", "subtype": "credit card", "balances": {"current": 640.0}},
                {"account_id": "acc-loan", "name": "Auto Loan", "mask": "7777", "type": "loan", "subtype": "auto", "balances": {"current": 18400.0}},
            ]})
        if path == "/transactions/sync":
            body = json.loads(request.content or b"{}")
            cursor = str(body.get("cursor") or "")
            at = int(cursor[1:]) if cursor.startswith("p") else 0
            page = self.rows[at:at + PAGE]
            nxt = at + len(page)
            return httpx.Response(200, json={"added": page, "modified": [], "removed": [], "next_cursor": f"p{nxt}", "has_more": nxt < len(self.rows)})
        if path in ("/item/remove", "/item/webhook/update", "/link/token/create"):
            return httpx.Response(200, json={"removed": True, "link_token": "link-sandbox-1"})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


class Box:
    """An IMAP box from a fixed dict of messages; read-only, like the real one."""

    messages: dict[int, bytes] = {}
    fail_fetch: set[int] = set()  # uids whose fetch fails the way a dropped connection does

    def __init__(self, host, port, username, password, use_ssl=True, starttls=False):  # noqa: ANN001
        self.folder = None

    def connect(self) -> None:
        pass

    def folders(self) -> list[str]:
        return ["INBOX"]

    def select_readonly(self, folder: str) -> tuple[int, int]:
        self.folder = folder
        return (len(self.messages), max(self.messages, default=0) + 1)

    def uidvalidity(self) -> int:
        return 7

    def new_uids(self, since_uid: int) -> list[int]:
        return [u for u in sorted(self.messages) if u > since_uid]

    def fetch_raw(self, uid: int) -> bytes:
        if uid in self.fail_fetch:
            raise TimeoutError("the mail server took more than 120 seconds to answer one command")
        return self.messages[uid]

    def close(self) -> None:
        pass


def mail_messages() -> dict[int, bytes]:
    from email.message import EmailMessage

    from test_sweep8_bugs_quarter import raw_mail, upcoming_bills_mail
    from test_sweep12_bugs_mail_quarter import PLANS, loan_confirmation, paystub_lines, pdf_of

    out: dict[int, bytes] = {}
    uid = 0
    for k in range(3):
        day = START + timedelta(days=2 + 7 * k)
        text = upcoming_bills_mail(day)
        if text:
            uid += 1
            out[uid] = raw_mail("This week's upcoming bills", text, day)
    plan = PLANS["A"]
    m = EmailMessage()
    m["From"] = "Affirm <notifications@affirm.test>"
    m["To"] = "owner@example.test"
    m["Subject"] = "Your loan confirmation"
    m["Date"] = "Fri, 16 Oct 2026 08:00:00 +0000"
    m["Message-ID"] = "<loan-a@affirm.test>"
    m["Authentication-Results"] = "mx.example.test; dmarc=pass header.from=affirm.test; dkim=pass; spf=pass"
    m.set_content(loan_confirmation(plan))
    uid += 1
    out[uid] = bytes(m)
    stub = EmailMessage()
    stub["From"] = "Example Payroll <payroll@example.test>"
    stub["To"] = "owner@example.test"
    stub["Subject"] = "Your pay statement"
    stub["Date"] = "Fri, 30 Oct 2026 06:00:00 +0000"
    stub["Message-ID"] = "<stub-1030@example.test>"
    stub.set_content("Your pay statement is attached.")
    stub.add_attachment(pdf_of(paystub_lines(date(2026, 10, 30), 1)), maintype="application", subtype="pdf", filename="paystub-2026-10-30.pdf")
    uid += 1
    out[uid] = bytes(stub)
    return out


def import_files(root: Path, *, bills: bool = True) -> None:
    from test_sweep12_bugs_mail_quarter import affirm_statement_lines, paystub_lines, pdf_of

    root.mkdir(parents=True, exist_ok=True)
    (root / "Everyday Checking.csv").write_text("Date,Description,Amount\n2026-10-20,Example Farm Stand,-23.40\n2026-10-22,Example Hardware,-61.15\n2026-10-25,Example Bakery,-8.75\n", encoding="utf-8")
    if bills:
        (root / "bills.csv").write_text("Name,Amount,Due Day,Frequency\nExample Gym,35.00,9,monthly\nExample Storage Unit,89.00,12,monthly\n", encoding="utf-8")
    (root / "affirm-2026-10.pdf").write_bytes(pdf_of(affirm_statement_lines(date(2026, 10, 31))))
    (root / "paystub-2026-11-13.pdf").write_bytes(pdf_of(paystub_lines(date(2026, 11, 13), 2)))


# ---- the world ---------------------------------------------------------------------------------------------


@pytest.fixture
def clean_world():
    """The import folder and the scripted connectors are the process's, not the test's: put back as found."""
    plaid, mail = CONNECTORS["plaid"].client_factory, CONNECTORS["mail"].client_factory
    yield
    CONNECTORS["plaid"].client_factory, CONNECTORS["mail"].client_factory = plaid, mail
    Box.messages, Box.fail_fetch = {}, set()
    root = settings.import_root
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink()


def build_world(owner, *, files: bool = True, mail: bool = True, bills: bool = True) -> CursorPlaid:  # noqa: ANN001 - a signed-in TestClient
    """The household linked to the scripted bank, the mailbox connected, the import folder filled. The link's first
    sync is answered with no rows, so every row waits for the job."""
    routes.set_today_override(TODAY)
    with Session(engine) as session:
        seed(session)
        for a in session.exec(select(Account)).all():
            a.connector, a.external_id = "plaid", EXTERNAL[a.external_id]
            session.add(a)
        if mail:
            session.add(MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), use_ssl=False, starttls=True, folders="INBOX"))
        session.commit()
    vendor = CursorPlaid([])
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(vendor.handler))
    Box.messages = mail_messages() if mail else {}
    Box.fail_fetch = set()
    CONNECTORS["mail"].client_factory = Box
    assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"}).status_code == 200
    linked = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()
    assert linked["sync"]["ok"], linked
    from app.models import BankLink

    with Session(engine) as session:
        link = session.exec(select(BankLink)).one()
        link.cursor, link.last_sync_at = "", None  # nothing read yet: the job reads every row
        session.add(link)
        session.commit()
    from app.jobs.scheduler import detect_recurring

    detect_recurring()  # the steady state: detection has run over the ledger the household already had
    vendor.rows = bank_rows()
    if files:
        import_files(settings.import_root, bills=bills)
    return vendor


# ---- snapshots ---------------------------------------------------------------------------------------------

FOLDERS = ("restore-points", "documents", "imports")


def _backup(src: Path, dst: Path) -> None:
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def snapshot(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    engine.dispose()
    _backup(settings.db_path, where / "db.sqlite")
    for name in FOLDERS:
        src = settings.data_dir / name
        if (where / name).exists():
            shutil.rmtree(where / name)
        if src.exists():
            shutil.copytree(src, where / name)
    return where


def put_back(where: Path) -> None:
    engine.dispose()
    _backup(where / "db.sqlite", settings.db_path)
    for name in FOLDERS:
        dst = settings.data_dir / name
        if dst.exists():
            shutil.rmtree(dst)
        if (where / name).exists():
            shutil.copytree(where / name, dst)
    bump_version()
    restore._live.clear()


# ---- the injected failure ----------------------------------------------------------------------------------


class Commits:
    """Counts every ORM commit; with `fail_at`, the Nth raises Crash instead of committing."""

    def __init__(self, fail_at: int | None = None) -> None:
        self.fail_at, self.count, self.fired = fail_at, 0, False


@contextmanager
def commits(fail_at: int | None = None, skip: tuple[str, ...] = ()):
    """`skip`: commits made directly by functions of these names are neither counted nor failed."""
    real = OrmSession.commit
    state = Commits(fail_at)

    def commit(self):  # noqa: ANN001, ANN202
        if skip and sys._getframe(1).f_code.co_name in skip:
            return real(self)
        state.count += 1
        if state.fail_at is not None and state.count == state.fail_at:
            state.fired = True
            raise Crash(f"injected at commit {state.count}")
        return real(self)

    OrmSession.commit = commit
    try:
        yield state
    finally:
        OrmSession.commit = real


# ---- the end state -----------------------------------------------------------------------------------------


def _skip_column(name: str) -> bool:
    # Ids (and the ids other rows hold) depend on the order rows were written in, which a retry may change; what
    # they point at is compared through the views in `dump`. Clock stamps and restore point names are the clock's.
    return name == "id" or name.endswith("_id") or name.endswith("_at") or name in ("resolved_at", "undone_at", "created_on", "restore_point", "messages_seen")


IDS = re.compile(r'(\b(?:bill|plan|paystub|transaction|account|import|mail|home|payment|series|fact|document):)\d+|("(?:document_id|id)": )\d+')


def _plain(v):  # noqa: ANN001, ANN202
    if isinstance(v, str):
        if v.startswith("fa1:"):
            return "<sealed>"  # sealed with a fresh nonce each time
        return IDS.sub(lambda m: (m.group(1) or m.group(2)) + "#", v)
    return v


VIEWS = {
    # which row paid which period, by the row's own key
    "payments by row": "SELECT b.name, p.due_date, p.status, p.amount, t.row_hash FROM payments p JOIN bills b ON b.id = p.bill_id LEFT JOIN transactions t ON t.id = p.transaction_id",
    "bills by series": "SELECT b.name, s.merchant_key, s.cadence FROM bills b LEFT JOIN recurring_series s ON s.id = b.series_id",
    "rows by account": "SELECT a.external_id, t.row_hash FROM transactions t JOIN accounts a ON a.id = t.account_id",
    "facts by message": "SELECT m.uid, f.kind, f.status FROM extracted_facts f LEFT JOIN message_refs m ON m.id = f.message_ref_id",
}


def dump() -> dict[str, Counter]:
    """Every table the engine reads, as a multiset of rows without ids and clock stamps, and the views that say
    what the ids pointed at."""
    from sqlalchemy import text

    out: dict[str, Counter] = {}
    with engine.connect() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if table.name in NOISE_TABLES:
                continue
            cols = [c.name for c in table.columns if not _skip_column(c.name)]
            rows = connection.execute(text(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table.name}"')).all()
            if table.name == "settings":
                rows = [r for r in rows if r[0] not in NOISE_SETTINGS and not str(r[0]).startswith("watch.")]
            out[table.name] = Counter(tuple(_plain(v) for v in r) for r in rows)
        for name, sql in VIEWS.items():
            out[name] = Counter(tuple(r) for r in connection.execute(text(sql)).all())
    return out


def make_due() -> None:
    """What a later hour finds: the bank link due again (a failed sync waits its interval, as it should)."""
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text("UPDATE bank_links SET last_sync_at = NULL"))


def files_state() -> dict[str, list[str]]:
    """The restore points (complete ones only count: a .db with its description) and the kept documents."""
    points = sorted(p.name for p in restore.folder().glob("*"))
    docs = sorted(p.name for p in restore.documents_folder().glob("*.bin")) if restore.documents_folder().exists() else []
    return {"points": points, "documents": docs}


def diff(a: dict[str, Counter], b: dict[str, Counter], limit: int = 4) -> list[str]:
    out = []
    for table in sorted(set(a) | set(b)):
        x, y = a.get(table, Counter()), b.get(table, Counter())
        if x != y:
            extra, missing = list((y - x).elements()), list((x - y).elements())
            out.append(f"{table}: +{len(extra)} {extra[:limit]} / -{len(missing)} {missing[:limit]}")
    return out


def orphans() -> list[str]:
    """Rows that point at nothing: what the foreign keys the schema does not declare must still keep."""
    from sqlalchemy import text

    checks = {
        "splits of a missing transaction": "SELECT count(*) FROM transaction_splits WHERE transaction_id NOT IN (SELECT id FROM transactions)",
        "pairs of a missing transaction": "SELECT count(*) FROM transaction_pairs WHERE a_id NOT IN (SELECT id FROM transactions) OR b_id NOT IN (SELECT id FROM transactions)",
        "rows of a missing import": "SELECT count(*) FROM transactions WHERE import_id IS NOT NULL AND import_id NOT IN (SELECT id FROM import_batches)",
        "rows of an undone import": "SELECT count(*) FROM transactions WHERE import_id IN (SELECT id FROM import_batches WHERE status = 'undone')",
        "a kept import that added nothing": "SELECT count(*) FROM import_batches b WHERE status = 'kept' AND inserted > 0 AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.import_id = b.id)",
        "an applied fact whose bill is gone": "SELECT count(*) FROM extracted_facts WHERE status = 'applied' AND applied_to LIKE 'bill:%' AND CAST(substr(applied_to, 6) AS INTEGER) NOT IN (SELECT id FROM bills)",
        "an applied fact whose plan is gone": "SELECT count(*) FROM extracted_facts WHERE status = 'applied' AND applied_to LIKE 'plan:%' AND CAST(substr(applied_to, 6) AS INTEGER) NOT IN (SELECT id FROM installment_plans)",
        "a paystub of a pending fact": "SELECT count(*) FROM paystubs p JOIN extracted_facts f ON f.id = p.fact_id WHERE f.status = 'pending'",
        "two payments for one period": "SELECT count(*) FROM (SELECT bill_id, due_date FROM payments GROUP BY bill_id, due_date HAVING count(*) > 1)",
        "two plans for one loan": "SELECT count(*) FROM (SELECT loan_id FROM installment_plans WHERE loan_id != '' GROUP BY loan_id HAVING count(*) > 1)",
        "a document of a missing transaction": "SELECT count(*) FROM documents WHERE attach_to LIKE 'transaction:%' AND CAST(substr(attach_to, 13) AS INTEGER) NOT IN (SELECT id FROM transactions)",
        "a link's cursor past its rows": "SELECT count(*) FROM bank_links WHERE cursor LIKE 'p%' AND CAST(substr(cursor, 2) AS INTEGER) > (SELECT count(*) FROM transactions WHERE source = 'plaid' AND row_hash IS NOT NULL)",
    }
    found = []
    with engine.connect() as connection:
        for what, sql in checks.items():
            n = connection.execute(text(sql)).scalar()
            if n:
                found.append(f"{what}: {n}")
        fk = connection.execute(text("PRAGMA foreign_key_check")).all()
        if fk:
            found.append(f"foreign key check: {fk[:4]}")
    for p in restore.folder().glob("*.db"):
        if not p.with_suffix(".json").exists():
            found.append(f"a restore point without its description: {p.name}")
    for p in restore.folder().glob("*"):
        if p.name.startswith("."):
            continue  # a copy made half way, under a name nothing lists; restore.sweep_leftovers removes it after an hour
        if p.suffix not in (".db", ".json", ".log") and p.name != "last-check.json":
            found.append(f"a stray file among the restore points: {p.name}")
    return found
