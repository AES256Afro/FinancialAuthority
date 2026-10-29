"""SQLite through SQLModel, one file under the data directory."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

settings.ensure_dirs()
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")  # safe under WAL: a power cut can lose the last commit, never corrupt
    cursor.execute("PRAGMA cache_size=-32000")  # 32 MB of page cache
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_transactions_account_date ON transactions (account_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions (date)",
    "CREATE INDEX IF NOT EXISTS ix_job_runs_started_at ON job_runs (started_at)",
    "CREATE INDEX IF NOT EXISTS ix_job_runs_name_started ON job_runs (name, started_at)",
)


def ensure_indexes() -> list[str]:
    """The named indexes above, and every index a model declares (`Field(index=True)`). `create_all` makes a
    table's indexes only with the table, so one declared later never reached an older database; here each is
    created when missing. One that cannot be made (a unique index over rows that already repeat) is logged and
    skipped: an index is never a reason the app does not start."""
    import logging

    from sqlalchemy import inspect, text

    made: list[str] = []
    with engine.begin() as connection:
        for ddl in INDEXES:
            connection.execute(text(ddl))
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in tables:
                continue
            present = {i["name"] for i in inspector.get_indexes(table.name)}
            for index in table.indexes:
                if index.name in present:
                    continue
                try:
                    index.create(connection)
                    made.append(str(index.name))
                except Exception as error:  # noqa: BLE001 - the rows it covers are still there; only the lookup is slower
                    logging.getLogger("fa.db").warning("index %s was not made: %s", index.name, error)
        connection.execute(text("PRAGMA optimize"))
    return made


def init_db() -> None:
    from . import models  # noqa: F401  (registers the tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns()
    _trim_manual_keys()
    ensure_indexes()
    bump_version()


# What the engine computes depends on the database; this number moves whenever the data it reads may have
# changed, so a computed plan can be kept until then.
_version = [0]


def bump_version() -> None:
    _version[0] += 1


def data_version() -> int:
    return _version[0]


# Rows the engine never reads: writing them leaves a computed plan as it was.
QUIET_TABLES = {"audit_entries", "job_runs", "web_sessions", "oauth_tokens", "oauth_codes", "oauth_clients", "api_keys", "review_runs", "message_refs", "passkeys", "documents", "people", "health_samples"}
QUIET_SETTINGS = {"usage.calls", "selfcheck", "push.brief_day", "reminders.sent", "imports.processed", "review.pending", "auth.totp", "auth.totp.last_step", "jobs.detected_through", "jobs.matched_through"}


def _engine_reads(obj) -> bool:  # noqa: ANN001
    table = getattr(obj, "__tablename__", "")
    if table in QUIET_TABLES:
        return False
    if table == "settings" and getattr(obj, "key", "") in QUIET_SETTINGS:
        return False
    return True


@event.listens_for(Session, "after_flush")
def _after_flush(session, _context) -> None:  # noqa: ANN001
    if any(_engine_reads(o) for o in (*session.new, *session.dirty, *session.deleted)):
        bump_version()
        session.info["engine_write"] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session) -> None:  # noqa: ANN001
    # A plan worked out between the flush and the commit read the old rows; moving again on commit drops it.
    if session.info.pop("engine_write", False):
        bump_version()


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session, _previous) -> None:  # noqa: ANN001
    if session.info.pop("engine_write", False):
        bump_version()


@event.listens_for(Session, "do_orm_execute")
def _bulk_write(state) -> None:  # noqa: ANN001
    # A bulk update or delete never passes through a flush.
    if state.is_update or state.is_delete or state.is_insert:
        tables = {getattr(m.class_, "__tablename__", "") for m in state.all_mappers} if state.all_mappers else set()
        if not tables or tables - QUIET_TABLES:
            bump_version()


def _trim_manual_keys() -> None:
    """Manual accounts once kept whatever number was typed in their key; keep only the last four digits. An
    investment account's key is its name ("manual:fidelity 401k"), which trimming would make collide; it is left be."""
    from sqlalchemy import inspect, text

    if "accounts" not in inspect(engine).get_table_names():
        return  # a fresh database has nothing to trim
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT id, external_id FROM accounts WHERE connector = 'manual' AND kind != 'investment' AND external_id LIKE 'manual:%:%'")).all()
        keys = {str(key) for _, key in rows}
        for row_id, key in rows:
            head, _, tail = str(key).rpartition(":")  # manual:<name>:<number>
            digits = "".join(ch for ch in tail if ch.isdigit())[-4:]
            if tail != digits and head and f"{head}:{digits}" not in keys:
                connection.execute(text("UPDATE accounts SET external_id = :k WHERE id = :i"), {"k": f"{head}:{digits}", "i": row_id})
                keys.add(f"{head}:{digits}")


def add_missing_columns(target=None) -> list[str]:  # noqa: ANN001 - an Engine; the app's own when not given
    """Columns a newer version added to an existing table are created in place, with the model's default.

    SQLite's `create_all` only creates tables that do not exist, so an upgrade would otherwise fail on the first
    query that names a new column. Only additions are handled; nothing is dropped or retyped."""
    from sqlalchemy import inspect, text

    target = target if target is not None else engine
    added: list[str] = []
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    with target.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(target.dialect)}'
                default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                kind = str(column.type).upper()
                if default is not None:
                    literal = "1" if default is True else "0" if default is False else repr(default) if isinstance(default, str) else str(default)
                    ddl += f" DEFAULT {literal}"
                elif not column.nullable:
                    # A date or a datetime column must hold a date the rows can be read back with: the moment of this
                    # upgrade (SQLite's CURRENT_TIMESTAMP is not allowed on an added column). A 0 there would make
                    # every read of the table fail.
                    if "DATETIME" in kind or "TIMESTAMP" in kind:
                        ddl += f" DEFAULT {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')!r}"
                    elif "DATE" in kind:
                        ddl += f" DEFAULT {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')!r}"
                    elif "CHAR" in kind or "TEXT" in kind:
                        ddl += " DEFAULT ''"
                    else:
                        ddl += " DEFAULT 0"
                connection.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
    return added


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as session:
        yield session
