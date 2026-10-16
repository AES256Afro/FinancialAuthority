"""Sinking funds: what to put aside from each paycheck for the bills that come a few times a year."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.sinking import make_goal, sinking_funds
from .routes import today

router = APIRouter(prefix="/api/v1/goals/sinking", tags=["goals"])


@router.get("")
def list_funds(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return sinking_funds(session, today())


@router.post("/{bill_id}")
def start_fund(bill_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        make_goal(session, today(), bill_id)
    except KeyError as error:
        raise HTTPException(404, "That bill does not come a few times a year") from error
    return sinking_funds(session, today())
