"""Scenarios: a what-if run over the real registers, saved by name to compare dates; the readiness list for a
move; the owner's mark that a window was checked by hand."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth
from ..db import get_session
from ..engine.readiness import readiness
from ..engine.scenario import run_scenario
from ..engine.snapshot import finance_snapshot
from ..models import Scenario, utcnow
from ..store import set_setting
from .routes import today

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])


class OneOff(BaseModel):
    name: str = "One-off"
    amount: float
    date: date


class Recurring(BaseModel):
    name: str = "Added bill"
    amount: float
    due_day: int | None = None
    from_: date | None = None

    model_config = {"populate_by_name": True}


class ScenarioIn(BaseModel):
    name: str = "Scenario"
    move_date: date
    new_rent: float | None = None
    rent_bill_id: int | None = None
    overlap_months: int = 0
    one_offs: list[OneOff] = []
    recurring: list[Recurring] = []
    stop_bills: list[int] = []


def _spec(body: ScenarioIn) -> dict:
    if body.new_rent is not None and not 0 <= body.new_rent <= 50_000:
        raise HTTPException(400, "new_rent is dollars a month, up to 50,000")
    if not 0 <= body.overlap_months <= 3:
        raise HTTPException(400, "overlap_months is 0 to 3")
    if len(body.one_offs) > 30 or len(body.recurring) > 30:
        raise HTTPException(400, "at most thirty one-offs and thirty recurring additions")
    return {
        "name": body.name.strip()[:60] or "Scenario", "move_date": body.move_date.isoformat(), "new_rent": body.new_rent, "rent_bill_id": body.rent_bill_id, "overlap_months": body.overlap_months,
        "one_offs": [{"name": o.name[:60], "amount": float(o.amount), "date": o.date.isoformat()} for o in body.one_offs],
        "recurring": [{"name": r.name[:60], "amount": float(r.amount), "due_day": r.due_day, "from": r.from_.isoformat() if r.from_ else None} for r in body.recurring],
        "stop_bills": [int(x) for x in body.stop_bills],
    }


@router.post("/run")
def run(body: ScenarioIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return run_scenario(session, today(), _spec(body))


@router.get("")
def list_saved(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Scenario).order_by(Scenario.created_at.desc())).all()
    return {"items": [{"id": s.id, "name": s.name, "spec": json.loads(s.spec), "created_at": s.created_at.isoformat()} for s in rows]}


@router.post("")
def save(body: ScenarioIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    spec = _spec(body)
    row = Scenario(name=spec["name"], spec=json.dumps(spec))
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "name": row.name, "spec": spec, "created_at": row.created_at.isoformat()}


@router.delete("/{scenario_id}")
def remove(scenario_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    row = session.get(Scenario, scenario_id)
    if row is None:
        raise HTTPException(404, "No such scenario")
    session.delete(row)
    session.commit()
    return {"ok": True}


@router.get("/readiness")
def get_readiness(move_date: date | None = None, new_rent: float | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    snap = finance_snapshot(session, today())
    return readiness(session, today(), move_date=move_date, new_rent=new_rent, safe_to_spend=snap["safe_to_spend"]["amount"])


@router.post("/reconciled")
def mark_reconciled(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """The owner says a paycheck window's lines matched the bank. Recorded with the date; nothing else changes."""
    set_setting(session, "reconciled", {"at": utcnow().isoformat()})
    return {"ok": True, "at": utcnow().isoformat()}
