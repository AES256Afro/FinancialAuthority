"""Credit health from your own data (M48)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.credit import credit_health
from .routes import today

router = APIRouter(prefix="/api/v1/credit", tags=["credit"])


@router.get("")
def get_credit(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return credit_health(session, today())
