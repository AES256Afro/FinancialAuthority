"""What the app asks of the paid-for services, counted: every call to Plaid and Teller, by day and by endpoint,
so the owner can see how often the banks are read and what that would cost on any plan. Counting never stops a
call: a count that cannot be written is skipped."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session

log = logging.getLogger(__name__)
SETTING = "usage.calls"
KEEP_DAYS = 120


def record_call(provider: str, endpoint: str, *, today: date | None = None) -> None:
    from .db import engine
    from .store import get_setting, set_setting

    day = (today or datetime.now(UTC).date()).isoformat()
    try:
        with Session(engine) as session:
            calls: dict[str, dict[str, int]] = get_setting(session, SETTING, {}) or {}
            key = f"{provider}:{endpoint}"
            calls.setdefault(day, {})[key] = calls.get(day, {}).get(key, 0) + 1
            cutoff = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()
            set_setting(session, SETTING, {d: v for d, v in calls.items() if d >= cutoff})
    except Exception as error:  # noqa: BLE001 - a count is never worth a failed sync
        log.warning("usage count skipped: %s", error)


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
