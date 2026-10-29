"""The owner's clock: today and now in the owner's time zone (TZ), the same everywhere, never the server's UTC
date. A payday, a due date or a day's bucket of calls belongs to the owner's day."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings


def zone():  # noqa: ANN201
    try:
        return ZoneInfo(settings.tz) if settings.tz else None
    except (ZoneInfoNotFoundError, ValueError):  # a path such as /etc/localtime is not a zone name
        return None


def local_now() -> datetime:
    z = zone()
    return datetime.now(z) if z else datetime.now().astimezone()


def local_today() -> date:
    return local_now().date()


def local_date(when: datetime) -> date:
    """The owner's date of a stored moment (UTC; SQLite gives it back naive): a balance read at 9 pm in Chicago is
    stamped the next day in UTC and is still today's, never tomorrow's."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    z = zone()
    return (when.astimezone(z) if z else when.astimezone()).date()
