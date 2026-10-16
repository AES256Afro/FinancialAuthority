"""Sinking funds: a bill that comes a few times a year (car insurance every six months, a yearly subscription, a
quarterly water bill) is easier to meet a little from each paycheck than all at once. For each such bill, what to
set aside from each check until it is due, and one tap to make that a goal that fills on the Goals card."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Bill, Goal, Income
from .billdates import bill_dates
from .cadence import next_unpaid_payday, paydays_through

RARE = ("quarterly", "semiannual", "yearly")
LOOK_DAYS = 400


def _money(v: float) -> float:
    return round(float(v), 2)


def sinking_funds(session: Session, today: date) -> dict[str, Any]:
    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    upcoming = sorted(((next_unpaid_payday(session, i, today), i) for i in incomes), key=lambda p: p[0])
    goals = {g.name.lower(): g for g in session.exec(select(Goal).where(Goal.active == True)).all()}  # noqa: E712
    items = []
    for b in session.exec(select(Bill).where(Bill.active == True)).all():  # noqa: E712
        if (b.cadence or "monthly") not in RARE or b.amount <= 0:
            continue
        due = next(iter(bill_dates(b, today, today + timedelta(days=LOOK_DAYS))), None)
        if due is None:
            continue
        if upcoming:
            first, income = upcoming[0]
            checks = len([d for d in paydays_through(first, income.cadence or "biweekly", due - timedelta(days=1), at_least=0) if d < due])
        else:
            checks = max(0, math.floor((due - today).days / 14))
        per_check = _money(b.amount / checks) if checks else _money(b.amount)
        name = f"{b.name} fund"
        goal = goals.get(name.lower())
        items.append({
            "bill_id": b.id, "name": b.name, "cadence": b.cadence, "amount": _money(b.amount), "due": due.isoformat(), "paychecks_left": checks,
            "per_paycheck": per_check, "goal_name": name, "goal_id": goal.id if goal else None, "funded": _money(goal.funded) if goal else 0.0,
            "ready_in_time": checks > 0, "source": f"bill:{b.id}",
        })
    items.sort(key=lambda i: i["due"])
    return {"as_of": today.isoformat(), "items": items, "per_paycheck_total": _money(sum(i["per_paycheck"] for i in items if i["goal_id"] is None)),
            "note": "Each is the bill divided by the paychecks before it is due. Make one a goal and it fills on the Goals card as you put money aside."}


def make_goal(session: Session, today: date, bill_id: int) -> Goal:
    fund = next((i for i in sinking_funds(session, today)["items"] if i["bill_id"] == bill_id), None)
    if fund is None:
        raise KeyError(bill_id)
    if fund["goal_id"]:
        return session.get(Goal, fund["goal_id"])
    goal = Goal(name=fund["goal_name"], kind="save", target=fund["amount"], funded=0.0, target_date=date.fromisoformat(fund["due"]), pace=0.0, priority=5)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal
