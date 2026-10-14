"""SQLite through SQLModel, one file under the data directory."""

from __future__ import annotations

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
    cursor.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers the tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """Columns a newer version added to an existing table are created in place, with the model's default.

    SQLite's `create_all` only creates tables that do not exist, so an upgrade would otherwise fail on the first
    query that names a new column. Only additions are handled; nothing is dropped or retyped."""
    from sqlalchemy import inspect, text

    added: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
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
