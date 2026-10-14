"""Save-up and pay-down goals: what each takes from a paycheck to land on its date, whether it is on track,
and what is left to spend once the goals are fed. The engine's arithmetic; the owner sets the targets."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Goal

WINDOW_DAYS = 14


def _money(v: float) -> float:
    return round(float(v), 2)


def paychecks_until(today: date, target: date | None, next_payday: date) -> int | None:
    """How many paychecks land on or before the target date, the next one included."""
    if target is None:
        return None
    if target < next_payday:
        return 0
    return (target - next_payday).days // WINDOW_DAYS + 1


def goals_plan(session: Session, today: date, next_payday: date) -> dict:
    goals = session.exec(select(Goal).where(Goal.active == True).order_by(Goal.priority, Goal.id)).all()  # noqa: E712
    items = []
    total = 0.0
    for g in goals:
        remaining = max(0.0, g.target - g.funded)
        left = paychecks_until(today, g.target_date, next_payday)
        needed = _money(remaining / left) if left else (_money(remaining) if left == 0 and remaining > 0 else 0.0)
        per_paycheck = _money(g.pace) if g.pace > 0 else needed
        if remaining <= 0:
            per_paycheck = 0.0
        on_track = remaining <= 0 or left is None or (per_paycheck * max(left, 0) + 0.005 >= remaining)
        done_by = None
        if remaining > 0 and per_paycheck > 0:
            done_by = (next_payday + timedelta(days=WINDOW_DAYS * (int(-(-remaining // per_paycheck)) - 1))).isoformat()
        items.append({
            "id": g.id, "name": g.name, "kind": g.kind, "target": _money(g.target), "funded": _money(g.funded), "remaining": _money(remaining),
            "target_date": g.target_date.isoformat() if g.target_date else None, "paychecks_left": left, "needed_per_paycheck": needed,
            "per_paycheck": per_paycheck, "on_track": on_track, "done_by": done_by, "priority": g.priority, "source": f"goal:{g.id}",
        })
        total += per_paycheck
    return {"per_paycheck": _money(total), "items": items}
