"""Debt payoff with dates and the interest each way; advance apps by what they cost; money between people."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.payoff import payoff_plan, people, save_person_loan
from .routes import today

router = APIRouter(prefix="/api/v1/debts/payoff", tags=["debts"])


@router.get("")
def plan(extra: float = 0.0, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    try:
        return payoff_plan(session, today(), extra)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


class PersonLoanIn(BaseModel):
    person: str
    direction: str
    amount: float
    on: date | None = None
    note: str = ""
    settled: bool = False


@router.post("/people")
def add_person_loan(body: PersonLoanIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        return {"people": save_person_loan(session, person=body.person, direction=body.direction, amount=body.amount, on=body.on or today(), note=body.note, settled=body.settled)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.delete("/people/{index}")
def remove_person_loan(index: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.payoff import PEOPLE
    from ..store import set_setting

    rows = people(session)
    if not 0 <= index < len(rows):
        raise HTTPException(404, "No such entry")
    rows.pop(index)
    set_setting(session, PEOPLE, rows)
    return {"people": rows}


@router.put("/people/{index}")
def change_person_loan(index: int, body: PersonLoanIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if not 0 <= index < len(people(session)):
        raise HTTPException(404, "No such entry")
    try:
        return {"people": save_person_loan(session, person=body.person, direction=body.direction, amount=body.amount, on=body.on or today(), note=body.note, settled=body.settled, index=index)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
