"""Leaving the advance apps (M46): the smallest advance each coming paycheck needs so nothing falls short, shrinking
to none, with the fees that saves against taking the usual amount every time.

The plan's paychecks already pay back the advances on file and take no new ones. When a paycheck would then be
short, the smallest advance that covers it is taken at that payday and paid back, with its fee, at the next one.
That is the exit: each new advance only as large as the shortfall, never the habit's size. Figures are the engine's;
the plan moves no money and takes no advance. Not financial advice."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..models import Advance
from .payoff import advance_costs
from .snapshot import finance_snapshot


def _money(v: float) -> float:
    return round(float(v), 2)


def exit_plan(session: Session, today: date) -> dict[str, Any]:
    snap = finance_snapshot(session, today)
    windows = snap["windows"]
    costs = advance_costs(session, today)
    taken = sum(c["taken"] for c in costs)
    fees = sum(c["fees"] for c in costs)
    rate = fees / taken if taken else 0.0  # fee per dollar per paycheck, from the last ninety days
    open_now = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    owed = _money(sum(a.amount + a.fee for a in open_now))
    if not open_now and not taken:
        return {"as_of": today.isoformat(), "state": "none", "steps": [], "providers": [],
                "word": "No advance on file in the last ninety days: nothing to leave.", "not_advice": "Not financial advice."}
    # The habit: how much a paycheck usually takes, from the last ninety days' paydays
    paydays_90 = max(1, round(90 / max(1, _days_between(windows))))
    usual = _money(taken / paydays_90) if taken else _money(sum(a.amount for a in open_now))
    steps: list[dict[str, Any]] = []
    prev = 0.0
    for w in windows:
        base = float(w["safe"])  # pays back what is on file, takes nothing new
        repay = prev * (1 + rate)
        after = base - repay
        need = math.ceil(max(0.0, -after) / 5) * 5 if after < 0 else 0.0  # advances come in round amounts; five dollars
        steps.append({"payday": w["start"], "paycheck": w["paycheck"], "left_with_no_new_advance": _money(base), "pays_back": _money(repay),
                      "borrow": _money(need), "fee": _money(need * rate), "left": _money(after + need)})
        prev = need
    last = steps[-1] if steps else None
    free_from = next((s["payday"] for i, s in enumerate(steps) if all(x["borrow"] == 0 for x in steps[i:])), None)
    fees_plan = _money(sum(s["fee"] for s in steps))
    fees_habit = _money(usual * rate * len(steps))
    if free_from:
        d = date.fromisoformat(free_from)
        saving = f"That saves about ${max(0.0, fees_habit - fees_plan):,.2f} in fees over these {len(steps)} paychecks against taking ${usual:,.2f} every time."
        if free_from == steps[0]["payday"]:
            word = f"None of the next {len(steps)} paychecks needs a new advance: pay back what is on file on its day, and take nothing new. {saving}"
        else:
            word = f"From the {d:%a %b} {d.day} paycheck on, no new advance is needed; each one before it is only as large as that paycheck's shortfall. {saving}"
        state = "exits"
    else:
        short = _money(sum(s["borrow"] for s in steps))
        word = (f"At your usual spending these {len(steps)} paychecks stay short: the smallest advances add up to ${short:,.2f}. "
                "To leave them, a bill has to move, a goal pause, or everyday spending come down; Cover and the purchase check show where.")
        state = "short"
    return {
        "as_of": today.isoformat(), "state": state, "word": word, "free_from": free_from, "steps": steps, "owed_now": owed,
        "usual_per_paycheck": usual, "fee_per_100": _money(rate * 100), "fees_on_plan": fees_plan, "fees_if_unchanged": fees_habit,
        "fees_saved": _money(max(0.0, fees_habit - fees_plan)), "providers": costs, "last": last,
        "source": "snapshot.windows + advances (last 90 days)",
        "not_advice": "The smallest advance each paycheck needs, from the plan's figures. It takes no advance and moves no money. Not financial advice.",
    }


def _days_between(windows: list[dict[str, Any]]) -> int:
    if len(windows) < 2:
        return 14
    a, b = date.fromisoformat(windows[0]["start"]), date.fromisoformat(windows[1]["start"])
    return max(1, (b - a).days)
