"""The reviewer: a second opinion on the plan, with notes the owner can apply with one tap.

It receives the engine's plan, the owner's rules and goals, the recent alerts and the previous notes: never
mail, attachments or account names. It returns notes, each with a kind (disagree, tighten, ask, ok) and an
action (a rule to add, a buffer to propose, a question, or none). It cannot change a number itself. It runs
on payday, after a confirmed change, and on request; each run records its tokens and, when the owner has
entered prices, its cost.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import AbstractContextManager
from datetime import date
from typing import Any, Callable

from sqlmodel import Session, select

from ..engine.snapshot import finance_snapshot
from ..models import ReviewRun, utcnow
from ..store import get_setting, set_setting
from .advisor import compact_plan
from .citations import engine_numbers, unverified
from .providers import Provider, ProviderError
from .roles import cost_of, provider_for, role_settings

log = logging.getLogger("fa.reviewer")

KINDS = ("disagree", "tighten", "ask", "ok")
ACTIONS = ("rule", "buffer", "ask", "none")

PROMPT = """You are the reviewer inside FinancialAuthority, the owner's own financial planner. A deterministic engine computed the plan below from the owner's registers; the advisor explains it. You are the second opinion: say where you would decide differently, what you would tighten, and what you would ask the owner.

Return JSON only, no prose around it, of this shape:
{{"notes": [{{"kind": "disagree|tighten|ask|ok", "text": "one or two plain sentences", "action": {{"type": "rule", "text": "a standing rule in one sentence"}} | {{"type": "buffer", "value": 300}} | {{"type": "ask", "text": "the question"}} | {{"type": "none"}}}}]}}

Rules:
- One to six notes. Order them by what matters most.
- Every amount you mention must appear in the plan below. Never compute a figure of your own.
- A "rule" action proposes a standing rule the owner can adopt with one tap; a "buffer" action proposes a new buffer in whole dollars; an "ask" action is a question only the owner can answer; "none" is for an observation.
- Say what you would do and why, in plain words. No disclaimers; the app adds its own. This is {trigger}.

The owner's standing rules:
{rules}

The owner's goals:
{goals}

Recent alerts from the engine:
{alerts}

The previous review's notes:
{previous}

The plan, as of {as_of}:
{plan}"""


def mark_pending(session: Session, reason: str) -> None:
    set_setting(session, "review.pending", {"reason": reason, "at": utcnow().isoformat()})


def packet(session: Session, today: date) -> dict:
    snap = finance_snapshot(session, today)
    plan = compact_plan(snap, names=False)
    last = session.exec(select(ReviewRun).where(ReviewRun.ok == True).order_by(ReviewRun.created_at.desc())).first()  # noqa: E712
    previous = [n["text"] for n in json.loads(last.notes)] if last else []
    return {
        "plan": plan,
        "rules": (get_setting(session, "rules", "") or "").strip(),
        "goals": (get_setting(session, "goals", "") or "").strip(),
        "alerts": [b["text"] for b in snap["brief"] if b["severity"] != "info"],
        "previous": previous,
        "as_of": snap["as_of"],
    }


def prompt_for(pack: dict, trigger: str) -> str:
    trigger_text = {"payday": "the payday review", "change": "the review after a confirmed change", "request": "a review the owner asked for"}.get(trigger, "a review")
    return PROMPT.format(
        trigger=trigger_text,
        rules=pack["rules"] or "(none yet)",
        goals=pack["goals"] or "(none written)",
        alerts="\n".join(f"- {a}" for a in pack["alerts"]) or "(none)",
        previous="\n".join(f"- {p}" for p in pack["previous"]) or "(none)",
        as_of=pack["as_of"],
        plan=json.dumps(pack["plan"], separators=(",", ":")),
    )


def parse_notes(text: str, allowed: set[float]) -> list[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON in the reply")
    data = json.loads(text[start : end + 1])
    raw_notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(raw_notes, list) or not raw_notes:
        raise ValueError("no notes in the reply")
    notes = []
    for i, n in enumerate(raw_notes[:6]):
        if not isinstance(n, dict) or not str(n.get("text", "")).strip():
            continue
        action = n.get("action") if isinstance(n.get("action"), dict) else {"type": "none"}
        kind = n.get("kind") if n.get("kind") in KINDS else "ok"
        a_type = action.get("type") if action.get("type") in ACTIONS else "none"
        clean: dict[str, Any] = {"type": a_type}
        if a_type in ("rule", "ask"):
            clean["text"] = str(action.get("text", "")).strip() or str(n["text"]).strip()
        if a_type == "buffer":
            try:
                clean["value"] = float(action.get("value"))
            except (TypeError, ValueError):
                clean = {"type": "none"}
        text_value = str(n["text"]).strip()
        # The note's text must cite the plan; the action is a proposal, so its value is the reviewer's own.
        notes.append({"n": i, "kind": kind, "text": text_value, "action": clean, "status": "open", "unverified": unverified(text_value, allowed)})
    if not notes:
        raise ValueError("the notes were empty")
    return notes


async def review(session_factory: Callable[[], AbstractContextManager[Session]], today: date, trigger: str, *, provider: Provider | None = None) -> ReviewRun:
    with session_factory() as session:
        conf = role_settings(session)["reviewer"]
        chosen = provider or provider_for(session, "reviewer")
        pack = packet(session, today)
    run = ReviewRun(trigger=trigger, provider=chosen.id if chosen else conf["provider"], model=chosen.model if chosen else conf["model"], packet_hash=hashlib.sha256(json.dumps(pack["plan"], sort_keys=True).encode()).hexdigest()[:16])
    if chosen is None:
        run.ok, run.error = False, "no reviewer is configured; choose one on Settings › Models"
    else:
        allowed = engine_numbers(pack["plan"])
        try:
            text, inp, out = await chosen.complete([{"role": "user", "content": prompt_for(pack, trigger)}], max_tokens=1500)
            run.raw = text[:20000]
            run.input_tokens, run.output_tokens = inp, out
            run.cost = cost_of(conf, inp, out)
            run.notes = json.dumps(parse_notes(text, allowed))
            run.ok = True
        except (ProviderError, ValueError, json.JSONDecodeError) as error:
            run.ok, run.error = False, f"{type(error).__name__}: {error}"[:500]
        except Exception as error:  # noqa: BLE001 - a review failing must never take a job down
            log.exception("review failed")
            run.ok, run.error = False, f"{type(error).__name__}: {error}"[:500]
    with session_factory() as session:
        session.add(run)
        set_setting(session, "review.pending", None)
        session.commit()
        session.refresh(run)
        return run


def review_sync(session_factory: Callable[[], AbstractContextManager[Session]], today: date, trigger: str) -> ReviewRun:
    return asyncio.run(review(session_factory, today, trigger))


def run_if_due(session_factory: Callable[[], AbstractContextManager[Session]], today: date) -> str:
    """The hourly job: review on payday once, and after a confirmed change, when a reviewer is configured."""
    with session_factory() as session:
        if provider_for(session, "reviewer") is None:
            return "reviewer off"
        pending = get_setting(session, "review.pending", None)
        snap = finance_snapshot(session, today)
        payday = snap["next_paycheck"]["days"] == 0 and snap["next_paycheck"]["amount"] > 0
        from datetime import timezone as _tz

        from ..clock import local_now

        midnight = local_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz.utc)  # the owner's midnight, not UTC's
        today_runs = session.exec(select(ReviewRun).where(ReviewRun.created_at >= midnight)).all()
        ran_today = any(r.trigger == "payday" for r in today_runs)
    if pending:
        run = review_sync(session_factory, today, "change")
        return f"reviewed after a change: {'ok' if run.ok else run.error}"
    if payday and not ran_today:
        run = review_sync(session_factory, today, "payday")
        return f"payday review: {'ok' if run.ok else run.error}"
    return "nothing due"


def apply_note(session: Session, run: ReviewRun, n: int, *, answer: str | None = None, dismiss: bool = False) -> dict:
    notes = json.loads(run.notes)
    note = next((x for x in notes if x["n"] == n), None)
    if note is None:
        raise KeyError("no such note")
    action = note["action"]
    if dismiss:
        note["status"] = "dismissed"
    elif action["type"] == "rule":
        rules = (get_setting(session, "rules", "") or "").rstrip()
        set_setting(session, "rules", (rules + "\n" if rules else "") + "- " + action["text"])
        note["status"] = "applied"
    elif action["type"] == "buffer":
        set_setting(session, "buffer", float(action["value"]))
        note["status"] = "applied"
    elif action["type"] == "ask":
        if not (answer or "").strip():
            raise ValueError("an answer is needed")
        goals = (get_setting(session, "goals", "") or "").rstrip()
        set_setting(session, "goals", (goals + "\n" if goals else "") + f"- Asked: {action['text']} Answered: {answer.strip()}")
        note["status"] = "applied"
    else:
        note["status"] = "applied"
    run.notes = json.dumps(notes)
    session.add(run)
    session.commit()
    return note


def run_out(run: ReviewRun) -> dict:
    return {
        "id": run.id, "created_at": run.created_at.isoformat(), "trigger": run.trigger, "provider": run.provider, "model": run.model,
        "ok": run.ok, "error": run.error, "notes": json.loads(run.notes or "[]"), "input_tokens": run.input_tokens, "output_tokens": run.output_tokens, "cost": run.cost,
    }
