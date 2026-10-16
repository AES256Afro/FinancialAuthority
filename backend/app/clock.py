"""The owner's clock: today and now in the owner's time zone (TZ), the same everywhere, never the server's UTC
date. A payday, a due date or a day's bucket of calls belongs to the owner's day."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings


def zone():  # noqa: ANN201
    try:
        return ZoneInfo(settings.tz) if settings.tz else None
    except ZoneInfoNotFoundError:
        return None


def local_now() -> datetime:
    z = zone()
    return datetime.now(z) if z else datetime.now().astimezone()


def local_today() -> date:
    return local_now().date()
