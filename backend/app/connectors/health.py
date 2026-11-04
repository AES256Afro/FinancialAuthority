"""How a source's reads are going (M61): what kind of failure it is in, since when, and when the next scheduled read
may try. Kept in the settings table, one key a source, so a restart forgets nothing. A refused sign-in waits an hour,
then four, then a day, since trying every quarter hour only trips the server's own lockout. A network failure has no
wait: the server never said no, so nothing is locked out by asking again. A read that succeeds clears the record."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from ..models import Setting
from ..store import get_setting, set_setting

SIGNIN = "signin"  # the source answered and said no: only a new password or a new sign-in mends it
NETWORK = "network"  # the source was not reached, or said to come back later: it mends itself
OTHER = "other"  # anything else (a folder that will not open, keys refused): the owner should see it now
BACKOFF_HOURS = (1, 4, 24)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def mail_key(account_id: int) -> str:
    return f"mail.{account_id}.backoff"


def bank_key(link_id: int) -> str:
    return f"bank.{link_id}.backoff"


def moment(value: Any) -> datetime | None:
    """A kept time, or None when there is none or it cannot be read; a time kept without a zone is UTC."""
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def read(session: Session, key: str) -> dict[str, Any]:
    state = get_setting(session, key)
    return state if isinstance(state, dict) else {}


def waiting_until(session: Session, key: str, now: datetime) -> datetime | None:
    """When the next scheduled read of this source may try again, or None when it may try now."""
    until = moment(read(session, key).get("until"))
    return until if until is not None and until > now else None


def note_failure(session: Session, key: str, failure: str, now: datetime, *, back_off: bool = True, **extra: Any) -> datetime | None:
    """One more failed read. Only a refused sign-in waits, and the wait grows along BACKOFF_HOURS; the time it ends is
    returned. An outage keeps the time it began, however its failures change."""
    state = read(session, key)
    fails = int(state.get("fails", 0) or 0)
    until = None
    if failure == SIGNIN and back_off:
        fails += 1
        until = now + timedelta(hours=BACKOFF_HOURS[min(fails, len(BACKOFF_HOURS)) - 1])
    set_setting(session, key, {"fails": fails, "until": until.isoformat() if until else None, "failure": failure, "since": state.get("since") or now.isoformat(), **extra})
    return until


def note_ok(session: Session, key: str) -> None:
    """A read went through: the record goes, leaving nothing behind (a failed run read again ends as a clean one does)."""
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)
        session.commit()


def allow_now(session: Session, key: str) -> None:
    """Something changed that may mend it (a new password, the vendor saying the login is repaired): the next read need
    not wait, and the count starts over. The outage still stands until a read succeeds."""
    state = read(session, key)
    if state.get("until") or state.get("fails"):
        set_setting(session, key, {**state, "until": None, "fails": 0})
