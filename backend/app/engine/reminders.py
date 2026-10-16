"""Reminders, each a switch the owner sets: a bill a chosen number of days before it is due, payday with the
sheet's moves, a card's statement day when it would report over 30 percent, and the Sunday check-in. Sent once
each, from the chosen hour, through every channel that is set up (the app's own push, ntfy or Gotify). Never an
email: a message to anyone but the owner needs an approval."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session

from ..config import settings
from ..store import get_setting, set_setting

SETTING = "reminders"
SENT = "reminders.sent"
DEFAULT: dict[str, Any] = {"bills_days": 2, "payday": True, "statement": True, "checkin": True, "hour": 9}


def _usd(v: float) -> str:
    return f"${abs(v):,.2f}"


def _short(d: str | date) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d:%a %b} {d.day}"


def reminder_settings(session: Session) -> dict[str, Any]:
    return {**DEFAULT, **(get_setting(session, SETTING, {}) or {})}


def save(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    s = reminder_settings(session)
    if values.get("bills_days") is not None:
        s["bills_days"] = max(0, min(7, int(values["bills_days"])))
    for k in ("payday", "statement", "checkin"):
        if values.get(k) is not None:
            s[k] = bool(values[k])
    if values.get("hour") is not None:
        s["hour"] = max(0, min(23, int(values["hour"])))
    set_setting(session, SETTING, s)
    return s


def due_today(session: Session, today: date) -> list[dict[str, Any]]:
    """Every reminder that applies today, each with a key so it is sent once."""
    from .cover import account_cover
    from .guide import week_key
    from .saving import saving_plan
    from .snapshot import finance_snapshot

    s = reminder_settings(session)
    snap = finance_snapshot(session, today)
    out: list[dict[str, Any]] = []
    if s["bills_days"]:
        on = today + timedelta(days=int(s["bills_days"]))
        for e in snap["calendar"]["events"]:
            if e["date"] != on.isoformat() or e.get("kind") == "paycheck" or e.get("via_card"):
                continue
            how = "comes out automatically" if e.get("autopay") else "needs paying"
            out.append({"key": f"bill:{e.get('source')}:{e['date']}", "title": f"{e['name']} {_usd(e['amount'])} on {_short(e['date'])}", "body": f"It {how}; the plan has it covered from the check that carries it.", "link": "/bills", "tag": "bill"})
    if s["payday"] and snap["payday"]["today"]:  # the scheduled payday, whether or not the check has posted yet
        cov = account_cover(session, today, saving=saving_plan(session, today, snap=snap))
        moves = [r for r in cov["sheet"] if r["date"] == today.isoformat() and not r["done"]]
        body = "; ".join(f"{r['action']} {_usd(r['amount'])}" for r in moves[:3]) or "Nothing to move today."
        word = "in" if snap["payday"]["landed"] else "expected"
        out.append({"key": f"payday:{today.isoformat()}", "title": f"Payday: {_usd(snap['payday']['amount'])} {word}", "body": body, "link": "/cover", "tag": "payday"})
    if s["statement"]:
        cov = account_cover(session, today, saving=saving_plan(session, today, snap=snap))
        for a in cov["accounts"]:
            if a["role"] == "card" and a.get("pay_by") and a.get("to_target") and a["status"] != "good":
                days = (date.fromisoformat(a["pay_by"]) - today).days
                if 0 <= days <= 2:
                    out.append({"key": f"statement:{a['id']}:{a['statement_on']}", "title": f"Pay {_usd(a['to_target'])} on {a['name']} by {_short(a['pay_by'])}", "body": f"The statement on {_short(a['statement_on'])} then reports under 30% used.", "link": "/cover", "tag": "card"})
    from .moves import active_moves, move_plan

    for mv in active_moves(session):
        for al in move_plan(session, mv, today)["alerts"]:
            if al["severity"] in ("crit", "warn") or al["text"].startswith(("Switch ", "Move ")):
                out.append({"key": f"move:{mv.id}:{al['ref']}:{today.isoformat()}", "title": al["text"][:110], "body": f"{mv.name}. The Switching banks page has each step.", "link": "/switch", "tag": "move"})
    if s["checkin"] and today.weekday() == 6:
        log = get_setting(session, "checkin.log", {}) or {}
        if week_key(today) not in log:
            out.append({"key": f"checkin:{week_key(today)}", "title": "Weekly check-in", "body": "Five taps: anything unexpected, one subscription, the card, savings, next week.", "link": "/checkin", "tag": "checkin"})
    return out


def run(session: Session, now: datetime, today: date) -> dict[str, Any]:
    """Send what is due and not yet sent. Returns what went out."""
    from .. import notify

    s = reminder_settings(session)
    if not (notify.push_state(session)["configured"] or notify.has_devices(session)):
        return {"sent": 0, "reason": "no channel is set up"}  # nothing to compute when nothing can be sent
    local = now.astimezone(ZoneInfo(settings.tz) if settings.tz else None)
    if local.hour < int(s["hour"]):
        return {"sent": 0, "reason": "before the hour"}
    sent_log = get_setting(session, SENT, {}) or {}
    sent = 0
    for r in due_today(session, today):
        if r["key"] in sent_log:
            continue
        result = notify.send(session, r["title"], r["body"], link=r["link"], tags=[r["tag"]])
        if result.get("skipped"):
            return {"sent": sent, "reason": "no channel is set up"}
        if result.get("ok"):
            sent_log[r["key"]] = today.isoformat()
            sent += 1
    cutoff = (today - timedelta(days=45)).isoformat()
    set_setting(session, SENT, {k: v for k, v in sent_log.items() if v >= cutoff})
    return {"sent": sent}
