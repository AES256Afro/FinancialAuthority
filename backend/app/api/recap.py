"""Plan against what happened, paycheck by paycheck, and the week's recap."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.recap import recap, week_recap
from .routes import today

router = APIRouter(prefix="/api/v1/recap", tags=["recap"])


@router.get("")
def get_recap(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {**recap(session, today()), "week": week_recap(session, today())}
