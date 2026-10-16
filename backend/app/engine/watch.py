"""An assistant that watches: each day, the ledger is read for what deserves a look. A charge far above what a
merchant usually takes, the same charge twice within days, a subscription that has just started, and a price that
went up. Each finding carries its rows and one tap to say "this is expected", which keeps it off the list.

It decides nothing and moves nothing; it says what it saw. The figures are the ledger's own. A model may phrase the
morning's summary, but these findings are found here, in plain arithmetic, the same every time."""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, RecurringSeries, Transaction
from ..store import get_setting, set_setting
from .recurring import merchant_key

DISMISSED = "watch.dismissed"
NOTIFIED = "watch.notified"
RECENT_DAYS = 14
HISTORY_DAYS = 180
UNUSUAL_TIMES = 3.0
UNUSUAL_MIN_OVER = 40.0
DOUBLE_DAYS = 3
DOUBLE_MIN = 15.0
NEW_DAYS = 45
RISE_MIN, RISE_SHARE = 1.0, 0.05
NOT_ADVICE = "Found by the app in your transactions. Not financial advice."


def _money(v: float) -> float:
    return round(float(v), 2)


def _row(t: Transaction, names: dict[int, str]) -> dict[str, Any]:
    return {"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": _money(t.amount), "account": names.get(t.account_id, ""), "pending": t.pending, "source": f"transaction:{t.id}"}


def findings(session: Session, today: date, *, include_dismissed: bool = False) -> list[dict[str, Any]]:
    names = {a.id: a.name for a in session.exec(select(Account)).all()}
    rows = [t for t in session.exec(select(Transaction).where(Transaction.date >= today - timedelta(days=HISTORY_DAYS), Transaction.date <= today, Transaction.amount > 0)).all() if t.is_spending]
    recent_from = today - timedelta(days=RECENT_DAYS)
    by_key: dict[str, list[Transaction]] = {}
    for t in rows:
        by_key.setdefault(merchant_key(t.merchant), []).append(t)
    out: list[dict[str, Any]] = []

    for key, group in by_key.items():
        group.sort(key=lambda t: t.date)
        recent = [t for t in group if t.date >= recent_from]
        earlier = [t.amount for t in group if t.date < recent_from]
        if len(earlier) >= 3 and recent:
            usual = statistics.median(earlier)
            for t in recent:
                if t.amount >= usual * UNUSUAL_TIMES and t.amount - usual >= UNUSUAL_MIN_OVER:
                    out.append({"key": f"unusual:{t.id}", "kind": "unusual", "severity": "warning", "title": f"{t.merchant}: far more than usual",
                                "text": f"{t.merchant} took ${t.amount:,.2f} on {t.date:%a %b} {t.date.day}; it usually takes about ${usual:,.2f}.",
                                "amount": _money(t.amount - usual), "rows": [_row(t, names)], "link": "/transactions"})
        for a, b in zip(recent, recent[1:]):
            if a.account_id == b.account_id and abs(a.amount - b.amount) <= 0.01 and a.amount >= DOUBLE_MIN and (b.date - a.date).days <= DOUBLE_DAYS and not (a.pending or b.pending):
                out.append({"key": f"double:{a.id}:{b.id}", "kind": "double", "severity": "warning", "title": f"{a.merchant}: the same charge twice",
                            "text": f"{a.merchant} charged ${a.amount:,.2f} on {a.date:%a %b} {a.date.day} and again on {b.date:%a %b} {b.date.day}. If you bought once, ask them about the second.",
                            "amount": _money(b.amount), "rows": [_row(a, names), _row(b, names)], "link": "/transactions"})

    for s in session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).all():  # noqa: E712
        if s.kind in ("income", "ignored", "advance") or s.typical_amount <= 0:
            continue
        if not s.confirmed and s.first_seen >= today - timedelta(days=NEW_DAYS):
            out.append({"key": f"new:{s.id}", "kind": "new", "severity": "info", "title": f"{s.merchant}: a new regular charge",
                        "text": f"{s.merchant} has charged ${s.typical_amount:,.2f} {s.cadence} since {s.first_seen:%b} {s.first_seen.day}. Name it on Needs you if you meant to sign up; cancel it if not.",
                        "amount": _money(s.typical_amount), "rows": [], "link": "/confirm", "source": f"series:{s.id}"})
        rise = s.latest_amount - s.typical_amount
        if rise >= max(RISE_MIN, s.typical_amount * RISE_SHARE):
            out.append({"key": f"rise:{s.id}:{s.latest_amount:.2f}", "kind": "rise", "severity": "info", "title": f"{s.merchant}: the price went up",
                        "text": f"{s.merchant} now takes ${s.latest_amount:,.2f}, up from the usual ${s.typical_amount:,.2f}.",
                        "amount": _money(rise), "rows": [], "link": "/bills", "source": f"series:{s.id}"})

    dismissed = get_setting(session, DISMISSED, {}) or {}
    if not include_dismissed:
        out = [f for f in out if f["key"] not in dismissed]
    order = {"warning": 0, "info": 1}
    out.sort(key=lambda f: (order.get(f["severity"], 2), -f["amount"]))
    return out


def overview(session: Session, today: date) -> dict[str, Any]:
    items = findings(session, today)
    return {"as_of": today.isoformat(), "items": items, "count": len(items), "not_advice": NOT_ADVICE}


def dismiss(session: Session, key: str, today: date) -> None:
    """The owner says it is expected: it stays off the list. The record keeps a year of answers."""
    if not key or len(key) > 80:
        raise ValueError("no such finding")
    dismissed = get_setting(session, DISMISSED, {}) or {}
    dismissed[key] = today.isoformat()
    cutoff = (today - timedelta(days=365)).isoformat()
    set_setting(session, DISMISSED, {k: v for k, v in dismissed.items() if v >= cutoff})


def to_announce(session: Session, today: date) -> list[dict[str, Any]]:
    """What has not been told yet: each finding is announced once, then remembered."""
    told = get_setting(session, NOTIFIED, {}) or {}
    new = [f for f in findings(session, today) if f["key"] not in told]
    if new:
        told.update({f["key"]: today.isoformat() for f in new})
        cutoff = (today - timedelta(days=365)).isoformat()
        set_setting(session, NOTIFIED, {k: v for k, v in told.items() if v >= cutoff})
    return new
