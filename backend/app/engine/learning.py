"""Suggestions that learn (M55), with no model: every suggestion the app makes belongs to a kind (the next steps, the
watch, the data check, the borrowing plan, the write-off finder), and the owner can answer any of them: Done, Not
useful or Wrong. The answers move the kind's weight: Done raises it a little, Not useful lowers it, Wrong lowers it
more. Suggestions are ranked by severity, then by weight; a kind turned down again and again is quieted (still one
tap away, never deleted); an urgent one is never quieted. A suggestion answered on its own stays away for a while:
a week after Done (its cause should have cleared), two months after Not useful or Wrong.

A connected model, if the owner adds one, can read the weights to word its own suggestions the same way; it never
changes them."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session

from ..store import get_setting, set_setting

FEEDBACK = "learning.feedback"  # {kind: {"done": n, "not_useful": n, "wrong": n}}
ANSWERED = "learning.answered"  # {item key: {"answer": ..., "on": day, "kind": ...}}
ANSWERS = ("done", "not_useful", "wrong")
QUIET_BELOW = 0.35
AWAY_DAYS = {"done": 7, "not_useful": 60, "wrong": 60}
WORDS = {"done": "Done", "not_useful": "Not useful", "wrong": "Wrong"}


def kind_of(key: str) -> str:
    """The kind a suggestion's key belongs to: 'selfcheck-2' and 'selfcheck-0' are one kind; 'move-notice' is 'move'."""
    head = re.split(r"[-:]", key, maxsplit=1)[0]
    return head or key


def weight(counts: dict[str, int] | None) -> float:
    c = counts or {}
    w = 1.0 + 0.15 * c.get("done", 0) - 0.35 * c.get("not_useful", 0) - 0.6 * c.get("wrong", 0)
    return round(max(0.0, min(2.0, w)), 2)


def answer(session: Session, key: str, reply: str, today: date, kind: str | None = None) -> str:
    if reply not in ANSWERS:
        raise ValueError("The answer is done, not_useful or wrong")
    if not key or len(key) > 120:
        raise ValueError("A suggestion's key")
    k = kind or kind_of(key)
    feedback = {kk: dict(v) for kk, v in (get_setting(session, FEEDBACK, {}) or {}).items()}
    feedback.setdefault(k, {})
    feedback[k][reply] = int(feedback[k].get(reply, 0)) + 1
    set_setting(session, FEEDBACK, feedback)
    answered = dict(get_setting(session, ANSWERED, {}) or {})
    answered[key] = {"answer": reply, "on": today.isoformat(), "kind": k}
    if len(answered) > 3000:  # the oldest answers go first
        answered = dict(sorted(answered.items(), key=lambda kv: kv[1]["on"])[-3000:])
    set_setting(session, ANSWERED, answered)
    w = weight(feedback[k])
    away = AWAY_DAYS[reply]
    return (f"{WORDS[reply]}: kept. This one stays away for {away} days" + (f"; suggestions of this kind are quieted now (one tap brings them back)." if w < QUIET_BELOW else
            f"; suggestions of this kind now weigh {w:.2f} (1 is where they start)."))


def bring_back(session: Session, kind: str) -> str:
    feedback = {kk: dict(v) for kk, v in (get_setting(session, FEEDBACK, {}) or {}).items()}
    feedback.pop(kind, None)
    set_setting(session, FEEDBACK, feedback)
    return f"Suggestions of the kind “{kind}” are back, starting fresh."


def rank(session: Session, items: list[dict[str, Any]], today: date, *, key: str = "id", severity: str = "severity",
         order: dict[str, int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The suggestions to show, best first, and the ones quieted. Each shown one carries its kind and weight."""
    order = order or {"critical": 0, "crit": 0, "warning": 1, "warn": 1, "info": 2, "good": 3}
    feedback = get_setting(session, FEEDBACK, {}) or {}
    answered = get_setting(session, ANSWERED, {}) or {}
    shown, quiet = [], []
    for item in items:
        k = kind_of(str(item.get(key, "")))
        a = answered.get(str(item.get(key, "")))
        if a and date.fromisoformat(a["on"]) + timedelta(days=AWAY_DAYS.get(a["answer"], 7)) > today and item.get(severity) not in ("critical", "crit"):
            continue
        w = weight(feedback.get(k))
        out = {**item, "kind": k, "weight": w}
        if w < QUIET_BELOW and item.get(severity) not in ("critical", "crit"):
            quiet.append(out)
        else:
            shown.append(out)
    shown.sort(key=lambda s: (order.get(str(s.get(severity)), 9), -s["weight"]))
    return shown, quiet


def overview(session: Session) -> dict[str, Any]:
    feedback = get_setting(session, FEEDBACK, {}) or {}
    kinds = sorted(({"kind": k, **{a: int(v.get(a, 0)) for a in ANSWERS}, "weight": weight(v), "quiet": weight(v) < QUIET_BELOW} for k, v in feedback.items()),
                   key=lambda r: r["weight"])
    answered = len(get_setting(session, ANSWERED, {}) or {})
    said = ("No answers yet: every kind of suggestion starts at the same weight." if not kinds else
            f"{answered} answer{'s' if answered != 1 else ''} so far across {len(kinds)} kind{'s' if len(kinds) != 1 else ''}"
            + (f"; {sum(1 for k in kinds if k['quiet'])} quieted." if any(k["quiet"] for k in kinds) else "."))
    return {"kinds": kinds, "answered": answered, "said": said, "how": __doc__.split("\n\n")[0]}
