"""What the app asks of the paid-for services, counted: every call to Plaid and Teller, by day and by endpoint,
so the owner can see how often the banks are read and what that would cost on any plan. A call is noted in
memory as it happens and written later in the session that is already open (the sync's own, or the page that
shows the counts), so counting never waits on the database or holds a sync up."""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session

log = logging.getLogger(__name__)
SETTING = "usage.calls"
KEEP_DAYS = 120


_pending: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def note(provider: str, endpoint: str, *, today: date | None = None) -> None:
    """Count one call, in memory; flush() writes it."""
    day = (today or local_today()).isoformat()
    with _lock:
        _pending[(day, f"{provider}:{endpoint}")] = _pending.get((day, f"{provider}:{endpoint}"), 0) + 1


def flush(session: Session) -> int:
    """Write the noted calls through an open session (committed by the caller's commit or here)."""
    from .store import get_setting, set_setting

    with _lock:
        taken = dict(_pending)
        _pending.clear()
    if not taken:
        return 0
    try:
        calls: dict[str, dict[str, int]] = get_setting(session, SETTING, {}) or {}
        for (day, key), n in taken.items():
            calls.setdefault(day, {})[key] = calls.get(day, {}).get(key, 0) + n
        cutoff = (local_today() - timedelta(days=KEEP_DAYS)).isoformat()
        set_setting(session, SETTING, {d: v for d, v in calls.items() if d >= cutoff})
    except Exception as error:  # noqa: BLE001 - a count is never worth a failed sync; keep it for next time
        log.warning("usage counts not written yet: %s", error)
        with _lock:
            for k, n in taken.items():
                _pending[k] = _pending.get(k, 0) + n
        return 0
    return sum(taken.values())


def record_call(provider: str, endpoint: str, *, today: date | None = None) -> None:
    """Count a call and write it now, in a session of its own (tests and one-off calls)."""
    from .db import engine

    note(provider, endpoint, today=today)
    with Session(engine) as session:
        flush(session)


def local_today() -> date:
    from .clock import local_today as _today

    return _today()


def calls(session: Session, today: date, days: int = 30) -> dict[str, Any]:
    from .store import get_setting

    stored: dict[str, dict[str, int]] = get_setting(session, SETTING, {}) or {}
    span = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
    by_day = [{"date": d.isoformat(), **{p: sum(n for k, n in stored.get(d.isoformat(), {}).items() if k.startswith(f"{p}:")) for p in ("plaid", "teller")}} for d in span]
    month = today.isoformat()[:7]
    by_endpoint: dict[str, int] = {}
    for d, row in stored.items():
        if d.startswith(month):
            for k, n in row.items():
                by_endpoint[k] = by_endpoint.get(k, 0) + n
    return {"by_day": by_day, "month": month, "by_endpoint": dict(sorted(by_endpoint.items(), key=lambda kv: -kv[1]))}
