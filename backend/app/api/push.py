"""Push settings: where the morning brief goes, and a test button."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, crypto, notify
from ..db import get_session
from ..engine.snapshot import finance_snapshot
from .routes import today

router = APIRouter(prefix="/api/v1/push", tags=["push"])


class PushIn(BaseModel):
    kind: str | None = None
    url: str | None = None
    topic: str | None = None
    token: str | None = None
    brief_hour: int | None = None


@router.get("")
def get_push(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return notify.push_state(session)


@router.put("")
def put_push(body: PushIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        notify.save_push(session, body.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    return notify.push_state(session)


@router.post("/test")
def test_push(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return notify.send(session, "FinancialAuthority", "Push works. The morning brief will arrive here.", tags=["white_check_mark"])


@router.post("/brief")
def send_brief_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    snap = finance_snapshot(session, today())
    worst = "critical" if any(b["severity"] == "critical" for b in snap["brief"]) else "default"
    return notify.send(session, f"Safe to spend {snap['safe_to_spend']['amount']:,.0f}", notify.brief_text(snap["brief"]), priority="high" if worst == "critical" else "default", tags=["moneybag"])
