"""Sweep 11, part one: the upgrade drill (roadmap M9.8), what the production box does when it moves from 0.9.1 to
0.10.0.

A database is made with an older release's own `models.py` (`git show vX.Y.Z:backend/app/models.py`, run in a
subprocess so its tables never meet the current ones), filled with the synthetic household and the rest of what a
running box holds (a bank link, a mailbox with its stored uid, job runs, facts of every status, an approval, an
API key, an OAuth client with tokens, a signed-in browser, settings under 0.9.1's names) through the old schema's
columns only. Then the current code opens it (init_db: create_all, add_missing_columns, ensure_indexes), every
job runs once, every GET route is read as the owner, and the old rows are checked against what was put in.

v0.9.1 and v0.9.3 share models.py and db.py, and differ from the current release only in db.py (ensure_indexes
now makes the indexes a model declares, which create_all never made on a table that already existed), so the 0.9.1
drill is the 0.9.3 drill. v0.8.2 is the older schema (no businesses, invoices or trips; no trust on a message, no
business share on a row). The chain 0.8.2 > 0.9.1 > now is what the production box did: the 0.9.1 step is that
release's own add_missing_columns, which added the columns without their indexes.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlmodel import Session, SQLModel

from app import auth, crypto, db
from app.config import settings
from app.connectors import CONNECTORS
from app.db import engine
from app.engine.household import seed
from app.jobs.scheduler import jobs
from app.models import (
    Account,
    ApiKey,
    Approval,
    BankLink,
    Bill,
    ExtractedFact,
    JobRun,
    MailAccount,
    MessageRef,
    OAuthClient,
    OAuthToken,
    WebSession,
    utcnow,
)
from app.oauth import _hash as oauth_hash
from app.store import set_setting
from tests.test_sweep8_bugs_quarter import EXTERNAL, ScriptedMailbox, ScriptedPlaid
from tests.test_sweep11_bugs_common import failures, read_every_route, slow

REPO = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 10, 14)
OLD_SETTINGS = {  # keys as 0.9.1 wrote them (git grep set_setting v0.9.1)
    "buffer": 250.0, "appearance": {"view": "river", "look": "dark"}, "banks.freshness": {"sync_every_hours": 4}, "push": {"url": "", "topic": ""},
    "imports.processed": [["old.csv", 1700000000]], "models": {"reviewer": {"provider": "none"}}, "reconciled": {}, "checkin.log": [], "goals": {},
    "banks.plaid": {"keys": {"result": "ok"}}, "review.pending": None, "selfcheck": {"at": "2026-10-13T06:00:00+00:00", "problems": []},
}
BUILD = '''
import datetime as dt, json, sys
old_dir, db_path, rows_path = sys.argv[1:4]
sys.path.insert(0, old_dir)
import oldmodels  # noqa: F401  (registers the old tables)
from sqlmodel import SQLModel, create_engine
engine = create_engine(f"sqlite:///{db_path}")
SQLModel.metadata.create_all(engine)
rows = json.load(open(rows_path, encoding="utf-8"))
dropped = {}
with engine.begin() as c:
    for table in SQLModel.metadata.sorted_tables:
        for row in rows.get(table.name, []):
            out = {}
            for col in table.columns:
                if col.name not in row:
                    continue
                v = row[col.name]
                if v is not None:
                    kind = str(col.type).upper()
                    if "DATETIME" in kind or "TIMESTAMP" in kind:
                        v = dt.datetime.fromisoformat(v)
                    elif "DATE" in kind:
                        v = dt.date.fromisoformat(v)
                out[col.name] = v
            for extra in set(row) - set(out):
                dropped.setdefault(table.name, set()).add(extra)
            c.execute(table.insert().values(**out))
print(json.dumps({"tables": [t.name for t in SQLModel.metadata.sorted_tables], "dropped": {k: sorted(v) for k, v in dropped.items()}}))
'''
STEP = '''
import sys
old_dir, db_path = sys.argv[1:3]
sys.path.insert(0, old_dir)
import oldmodels  # noqa: F401
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine
engine = create_engine(f"sqlite:///{db_path}")
SQLModel.metadata.create_all(engine)
inspector = inspect(engine)
with engine.begin() as c:  # v0.9.1's add_missing_columns, as it stood
    for table in SQLModel.metadata.sorted_tables:
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
            default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
            if default is not None:
                literal = "1" if default is True else "0" if default is False else repr(default) if isinstance(default, str) else str(default)
                ddl += f" DEFAULT {literal}"
            elif not column.nullable:
                ddl += " DEFAULT ''" if "CHAR" in str(column.type).upper() or "TEXT" in str(column.type).upper() else " DEFAULT 0"
            c.execute(text(ddl))
    c.execute(text("CREATE INDEX IF NOT EXISTS ix_transactions_account_date ON transactions (account_id, date)"))
'''


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def _json_value(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def dump_rows() -> dict[str, list[dict]]:
    """Every row of every table, as the current schema holds it."""
    out: dict[str, list[dict]] = {}
    with engine.connect() as c:
        for table in SQLModel.metadata.sorted_tables:
            out[table.name] = [{k: _json_value(v) for k, v in r._mapping.items()} for r in c.execute(select(table)).all()]
    return out


def fill_running_box(owner: TestClient) -> dict:
    """The synthetic household plus what a box that has run for a while holds. Returns what to check for after."""
    keep: dict = {}
    with Session(engine) as session:
        seed(session)
        accounts = session.exec(select(Account)).scalars().all()
        for a in accounts:
            a.connector, a.external_id, a.balance, a.balance_at = "plaid", EXTERNAL[a.external_id], {"checking": 312.0, "card": 2310.0, "store": 640.0, "loan": 18400.0}[a.external_id], utcnow() - dt.timedelta(hours=3)
            session.add(a)
        session.add(BankLink(provider="plaid", external_id="item-1", environment="sandbox", institution="Example Bank", access_token_enc=crypto.encrypt("access-sandbox-abc"),
                             cursor="c1", accounts=",".join(EXTERNAL.values()), last_sync_at=utcnow() - dt.timedelta(hours=5)))
        mailbox = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), use_ssl=False, starttls=True,
                              folders="INBOX", last_sync_at=utcnow() - dt.timedelta(hours=1), messages_seen=11999)
        session.add(mailbox)
        session.flush()
        for n in range(1, 7):
            session.add(MessageRef(account_id=mailbox.id, folder="INBOX", uid=11990 + n, message_id=f"<old-{n}@example.test>", sender="billing@example-power.test", sender_name="Example Power",
                                   subject=f"Your bill is ready {n}", date=utcnow() - dt.timedelta(days=n), snippet="Your bill of $142.00 is due", kind="bill_due"))
        session.flush()
        refs = session.exec(select(MessageRef.id)).scalars().all()
        for status, ref in zip(("pending", "pending", "confirmed", "rejected", "applied", "auto"), refs):
            session.add(ExtractedFact(kind="bill_due", message_ref_id=ref, fields=json.dumps({"name": "Example Power", "amount": 142.0, "due_date": "2026-10-20"}), confidence=0.9,
                                      status=status, excerpt="Your bill of $142.00 is due Oct 20", produced_by="rules", resolved_at=None if status == "pending" else utcnow()))
        for name, ok, ago in (("mail_sync", True, 1), ("bank_sync", True, 2), ("self_check", True, 3), ("detect_recurring", False, 7), ("prune_job_runs", True, 25)):
            session.add(JobRun(name=name, started_at=utcnow() - dt.timedelta(hours=ago), finished_at=utcnow() - dt.timedelta(hours=ago, minutes=-1), ok=ok, summary="old run" if ok else "ValueError: old failure"))
        session.add(Approval(kind="send", status="pending", to="landlord@example.test", subject="Notice", body="Hello", reason="the owner asked", source="chat", account_id=mailbox.id))
        key = "fa_" + "k" * 40
        session.add(ApiKey(name="Reviewer key", prefix=key[:10], key_hash=auth.hash_key(key), scopes="read", redaction="full"))
        session.add(OAuthClient(client_id="client-1", name="Claude", redirect_uris=json.dumps(["https://example.test/cb"]), scopes="read", registration="{}"))
        session.add(OAuthToken(token_hash=oauth_hash("fa_oa_access1"), kind="access", client_id="client-1", scopes="read", expires_at=utcnow() + dt.timedelta(hours=1)))
        session.add(OAuthToken(token_hash=oauth_hash("fa_oa_refresh1"), kind="refresh", client_id="client-1", scopes="read", expires_at=utcnow() + dt.timedelta(days=30)))
        sid = "s" * 43
        session.add(WebSession(sid_hash=auth.hash_key(sid), user_agent="drill", ip="127.0.0.1", last_seen_at=utcnow() - dt.timedelta(minutes=10)))
        session.commit()
        for k, v in OLD_SETTINGS.items():
            set_setting(session, k, v)
        set_setting(session, f"mail.{mailbox.id}.INBOX.last_uid", 11996)
        keep.update(mailbox_id=mailbox.id, key=key, cookie=auth._serializer().dumps({"u": settings.username, "sid": sid, "pw": auth._password_mark(), "iat": int(time.time())}),
                    balances={a.name: a.balance for a in accounts}, bills=len(session.exec(select(Bill).where(Bill.active == True)).scalars().all()))
    assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"}).status_code == 200
    return keep


def build_old_database(tag: str, rows: dict, work: Path, then: str = "") -> tuple[Path, dict]:
    """The old release's models.py makes the schema in a subprocess, and the rows land through its columns only.
    With `then`, that release's own upgrade step runs over it next (its models, its add_missing_columns)."""
    old_dir = work / f"old-{tag}"
    old_dir.mkdir()
    (old_dir / "oldmodels.py").write_text(_git("show", f"{tag}:backend/app/models.py"), encoding="utf-8")
    (old_dir / "build.py").write_text(BUILD, encoding="utf-8")
    (old_dir / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    old_db = old_dir / "old.db"
    done = subprocess.run([sys.executable, str(old_dir / "build.py"), str(old_dir), str(old_db), str(old_dir / "rows.json")], capture_output=True, text=True, cwd=str(old_dir), check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    built = json.loads(done.stdout.strip().splitlines()[-1])
    if then:
        step_dir = work / f"step-{then}"
        step_dir.mkdir()
        (step_dir / "oldmodels.py").write_text(_git("show", f"{then}:backend/app/models.py"), encoding="utf-8")
        (step_dir / "step.py").write_text(STEP, encoding="utf-8")
        done = subprocess.run([sys.executable, str(step_dir / "step.py"), str(step_dir), str(old_db)], capture_output=True, text=True, cwd=str(step_dir), check=False)
        assert done.returncode == 0, done.stderr[-2000:]
    return old_db, built


def replace_live_database(old_db: Path) -> None:
    engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
    shutil.copy(old_db, settings.db_path)


def schema_sql() -> list[str]:
    with engine.connect() as c:
        return sorted(str(s) for (s,) in c.execute(text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")).all())


def column_checks(built: dict) -> list[str]:
    """Every column the model declares is there; NOT NULL columns hold no NULL; a datetime column never holds 0;
    the columns the upgrade added carry the model's own default on every old row."""
    problems: list[str] = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.connect() as c:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in tables:
                problems.append(f"table {table.name} is missing")
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            total = c.execute(text(f'SELECT count(*) FROM "{table.name}"')).scalar()
            for col in table.columns:
                if col.name not in present:
                    problems.append(f"{table.name}.{col.name} is missing")
                    continue
                if not total:
                    continue
                if not col.nullable and c.execute(text(f'SELECT count(*) FROM "{table.name}" WHERE "{col.name}" IS NULL')).scalar():
                    problems.append(f"{table.name}.{col.name} holds NULL though NOT NULL")
                kind = str(col.type).upper()
                if ("DATE" in kind or "TIME" in kind) and c.execute(text(f'SELECT count(*) FROM "{table.name}" WHERE "{col.name}" = 0 OR "{col.name}" = \'0\'')).scalar():
                    problems.append(f"{table.name}.{col.name} holds a 0 for a date")
                if col.name in built["dropped"].get(table.name, []):
                    default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                    if default is not None:
                        n = c.execute(text(f'SELECT count(*) FROM "{table.name}" WHERE "{col.name}" IS NOT :d'), {"d": default}).scalar()
                    elif col.nullable:
                        n = c.execute(text(f'SELECT count(*) FROM "{table.name}" WHERE "{col.name}" IS NOT NULL')).scalar()
                    else:
                        n = 0
                    if n:
                        problems.append(f"{table.name}.{col.name} was added without its default on {n} rows")
    return problems


def index_checks() -> list[str]:
    with engine.connect() as c:
        have = {name for (name,) in c.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'")).all()}
    wanted = {str(i.name) for t in SQLModel.metadata.sorted_tables for i in t.indexes} | {ddl.split("EXISTS ")[1].split(" ON")[0] for ddl in db.INDEXES}
    return [f"index {name} is missing" for name in sorted(wanted - have)]


@pytest.fixture
def vendors():
    plaid = ScriptedPlaid()
    plaid.day = TODAY
    CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(plaid.handler))
    ScriptedMailbox.messages = {}
    CONNECTORS["mail"].client_factory = ScriptedMailbox
    yield plaid
    CONNECTORS["plaid"].client_factory = None
    from app.mail.imap import ImapClient

    CONNECTORS["mail"].client_factory = ImapClient


@pytest.mark.parametrize("path", ["v0.8.2", "v0.9.1", "v0.8.2>v0.9.1"])
def test_a_database_from_an_older_release_opens_runs_and_reads(owner: TestClient, vendors: ScriptedPlaid, path: str, tmp_path: Path):
    tag, _, then = path.partition(">")
    for t in (tag, then):
        if t and _git("tag", "--list", t).strip() != t:
            pytest.skip(f"no tag {t} in this checkout")
    assert _git("diff", "v0.9.1", "v0.9.3", "--", "backend/app/models.py", "backend/app/db.py") == "", "v0.9.3's schema differs from v0.9.1's: drill it on its own"
    keep = fill_running_box(owner)
    rows = dump_rows()
    old_db, built = build_old_database(tag, rows, tmp_path, then)
    if tag == "v0.8.2":
        assert "business_id" in built["dropped"].get("transactions", []) and "trust" in built["dropped"].get("message_refs", []) and "businesses" not in built["tables"]
    else:
        assert not built["dropped"], built["dropped"]
    replace_live_database(old_db)

    # The upgrade: what the app does at start.
    db.init_db()
    first = schema_sql()
    problems = column_checks(built) + index_checks()
    assert not problems, problems
    # A second open changes nothing.
    assert db.add_missing_columns() == []
    assert db.ensure_indexes() == []
    db.init_db()
    assert schema_sql() == first, "a second open changed the schema"

    # Every job once, on the vendors' scripts: no failure.
    runs = [jobs.run(name) for name in jobs.specs]
    failed = [(r.name, r.summary[:200]) for r in runs if not r.ok]
    assert not failed, failed

    # Every GET route as the owner: no 500, and the screens that matter answer.
    got = read_every_route(owner, TODAY, {"message_id": 1, "bill_id": 1})
    assert not failures(got), failures(got)
    for screen in ("/api/v1/snapshot", "/api/v1/today", "/api/v1/needs-you", "/api/v1/bills", "/api/v1/accounts", "/api/v1/jobs", "/api/v1/mail/accounts", "/api/v1/banks", "/api/v1/facts", "/api/v1/setup/check", "/api/v1/settings"):
        assert got[screen][0] == 200, (screen, got[screen][0], got[screen][2])
    assert not slow(got), slow(got)

    # The old rows still read.
    accounts = {a["name"]: a for a in got["/api/v1/accounts"][2]["accounts"]}
    for name, balance in keep["balances"].items():
        assert name in accounts and abs(float(accounts[name]["balance"] or 0.0) - balance) < 0.01, (name, accounts.get(name))
    assert len(got["/api/v1/bills"][2]["bills"]) >= keep["bills"]
    assert got["/api/v1/facts"][2]["pending"] == 2, got["/api/v1/facts"][2]["pending"]
    with Session(engine) as session:
        statuses = sorted(session.exec(select(ExtractedFact.status)).scalars().all())
        assert statuses == ["applied", "auto", "confirmed", "pending", "pending", "rejected"], statuses
        assert session.exec(select(Approval.status)).scalars().all() == ["pending"]
        from app.store import get_setting

        assert get_setting(session, f"mail.{keep['mailbox_id']}.INBOX.last_uid") == 11996, "the mailbox's stored uid moved"
        assert get_setting(session, "buffer") == 250.0 and get_setting(session, "appearance")["look"] == "dark"
        assert len(session.exec(select(JobRun.id)).scalars().all()) >= 5 + len(jobs.specs)
    # The browser that was signed in before the upgrade is still signed in; the key and the OAuth token still work.
    fresh = TestClient(app_of(owner))
    fresh.cookies.set(auth.COOKIE, keep["cookie"])
    assert fresh.get("/api/auth/me", headers={"X-Requested-With": "FinancialAuthority"}).status_code == 200
    assert TestClient(app_of(owner)).get("/api/v1/snapshot", headers={"Authorization": f"Bearer {keep['key']}"}).status_code == 200
    assert TestClient(app_of(owner)).get("/api/v1/snapshot", headers={"Authorization": "Bearer fa_oa_access1"}).status_code == 200
    assert TestClient(app_of(owner)).get("/api/v1/snapshot", headers={"Authorization": "Bearer fa_oa_refresh1"}).status_code in (401, 403), "a refresh token read the API"


def app_of(client: TestClient):
    return client.app
