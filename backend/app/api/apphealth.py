"""Watching the app itself (M51): thirty days of the app's own health, and whether the phone is told."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from .inputs import In
from .. import auth, notify, services
from ..clock import zone
from ..db import get_session
from ..engine.apphealth import history
from ..jobs.scheduler import ALERTS
from ..models import HealthSample, utcnow
from ..store import get_setting, set_setting
from .routes import today

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/history")
def get_history(days: int = Query(30, ge=7, le=90), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    samples = session.exec(select(HealthSample).where(HealthSample.at >= utcnow() - timedelta(days=days + 1))).all()
    out = history(list(samples), today(), days, tz=zone())
    out["alerts_on"] = bool(get_setting(session, ALERTS, True))
    out["push_ready"] = bool(notify.push_state(session)["configured"] or notify.has_devices(session))
    return out


class AlertsIn(In):
    on: bool


@router.put("/alerts")
def set_alerts(body: AlertsIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    set_setting(session, ALERTS, body.on)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="health_alerts", args={"on": body.on}, rows=1)
    return {"alerts_on": body.on}
