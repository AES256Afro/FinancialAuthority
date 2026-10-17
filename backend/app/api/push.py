"""Push settings: where the morning brief goes, and a test button."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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


# ---- the app's own push, per device ---------------------------------------------------------------------------

class SubscribeIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    label: str = "A device"


@router.get("/devices")
def devices(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import webpush

    try:
        key = webpush.public_key(session)
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    return {"public_key": key, "devices": webpush.subscriptions(session)}


@router.post("/devices")
def subscribe_device(body: SubscribeIn, request: Request, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    from .. import webpush

    origin = str(request.base_url).rstrip("/")
    try:
        row = webpush.subscribe(session, endpoint=body.endpoint, p256dh=body.keys.get("p256dh", ""), auth=body.keys.get("auth", ""), label=body.label, origin=origin,
                                person_id=principal.person_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    return {"device": row, "devices": webpush.subscriptions(session)}


@router.delete("/devices/{device_id}")
def remove_device(device_id: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    from .. import webpush

    webpush.unsubscribe(session, device_id)
    return {"devices": webpush.subscriptions(session)}


# ---- reminders --------------------------------------------------------------------------------------------------

class RemindersIn(BaseModel):
    bills_days: int | None = None
    payday: bool | None = None
    statement: bool | None = None
    checkin: bool | None = None
    hour: int | None = None
    returns_days: int | None = None  # a receipt's last day to return, this many days before (0: off)
    warranty_days: int | None = None
    notice: bool | None = None  # the written notice a lease needs, three days before its deadline


@router.get("/reminders")
def get_reminders(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.reminders import due_today, reminder_settings

    return {"settings": reminder_settings(session), "today": [{k: r[k] for k in ("title", "body", "link")} for r in due_today(session, today())]}


@router.put("/reminders")
def put_reminders(body: RemindersIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.reminders import save

    return {"settings": save(session, body.model_dump(exclude_unset=True))}
