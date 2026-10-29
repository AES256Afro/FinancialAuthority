"""Goals: save up for a thing or pay a debt down, with a target, a date and a pace per paycheck."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.goals import goals_plan
from ..engine.snapshot import finance_snapshot
from ..llm.reviewer import mark_pending
from ..models import Goal
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


class GoalIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    target: float | None = None
    target_date: str | None = None
    pace: float | None = None
    priority: int | None = None
    funded: float | None = None
    active: bool | None = None


class FundIn(BaseModel):
    amount: float


def _check_figures(body: GoalIn) -> None:
    """A goal's money is dollars toward it: the target above zero, the pace and what is funded never below it."""
    if body.target is not None and not 0 < body.target <= 10_000_000:
        raise HTTPException(400, "target is dollars above zero, up to 10,000,000")
    if body.pace is not None and not 0 <= body.pace <= 1_000_000:
        raise HTTPException(400, "pace is dollars a paycheck, 0 or more")
    if body.funded is not None and not 0 <= body.funded <= 10_000_000:
        raise HTTPException(400, "funded is dollars put toward it, 0 or more")
    if body.priority is not None and not 1 <= body.priority <= 100:
        raise HTTPException(400, "priority is 1 (first) to 100")


def _plan(session: Session) -> dict:
    snap = finance_snapshot(session, today())
    return {**goals_plan(session, today(), date.fromisoformat(snap["next_paycheck"]["date"]), snap["next_paycheck"].get("cadence") or "biweekly"), "safe_to_spend": snap["safe_to_spend"]}


@router.get("")
def list_goals(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return _plan(session)


@router.post("")
@under_writer
def create_goal(body: GoalIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if not body.name or body.target is None or body.target <= 0:
        raise HTTPException(400, "A goal needs a name and a target above zero")
    _check_figures(body)
    if body.kind not in (None, "save", "paydown"):
        raise HTTPException(400, "kind is save or paydown")
    try:
        when = date.fromisoformat(body.target_date) if body.target_date else None
    except ValueError as error:
        raise HTTPException(400, "target_date must be YYYY-MM-DD") from error
    goal = Goal(name=body.name.strip(), kind=body.kind or "save", target=float(body.target), target_date=when, pace=float(body.pace or 0.0), priority=body.priority or 1, funded=float(body.funded or 0.0))
    session.add(goal)
    session.commit()
    mark_pending(session, f"goal added: {goal.name}")
    return _plan(session)


@router.put("/{goal_id}")
@under_writer
def update_goal(goal_id: int, body: GoalIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "No such goal")
    _check_figures(body)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field == "target_date":
            try:
                value = date.fromisoformat(value) if value else None
            except ValueError as error:
                raise HTTPException(400, "target_date must be YYYY-MM-DD") from error
        if field == "kind" and value not in ("save", "paydown"):
            raise HTTPException(400, "kind is save or paydown")
        setattr(goal, field, value)
    session.add(goal)
    session.commit()
    mark_pending(session, f"goal changed: {goal.name}")
    return _plan(session)


@router.post("/{goal_id}/fund")
@under_writer
def fund_goal(goal_id: int, body: FundIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "No such goal")
    goal.funded = max(0.0, goal.funded + float(body.amount))
    session.add(goal)
    session.commit()
    return _plan(session)


@router.delete("/{goal_id}")
@under_writer
def remove_goal(goal_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "No such goal")
    goal.active = False
    session.add(goal)
    session.commit()
    return _plan(session)
