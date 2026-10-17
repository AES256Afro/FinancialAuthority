"""The data check (M52): what may be counted twice or may not be genuine, and the owner's answer to each."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, services
from ..db import get_session
from ..engine import datacheck
from .routes import today

router = APIRouter(prefix="/api/v1/data-check", tags=["data check"])


@router.get("")
def get_check(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return datacheck.overview(session, today())


@router.post("/run")
def run_check(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.borrowing import sync_borrowing

    sync_borrowing(session, today())
    last = datacheck.run(session, today())
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="data_check_run", args={}, rows=last["count"])
    return datacheck.overview(session, today())


class AnswerIn(BaseModel):
    key: str
    action: str


@router.post("/answer")
def answer(body: AnswerIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Settle one finding. Only an answer the finding offers now is accepted, so nothing else can be changed here."""
    try:
        said = datacheck.answer(session, body.key, body.action, today())
    except LookupError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="data_check_answer", args={"key": body.key, "action": body.action}, rows=1)
    datacheck.run(session, today())
    return {**datacheck.overview(session, today()), "said": said}
