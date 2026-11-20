"""Watching the app itself (M51): thirty days of the app's own health, and whether the phone is told."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from .. import auth, notify, services
from ..clock import zone
from ..db import get_session
from ..engine.apphealth import history
from ..jobs.scheduler import ALERTS
from ..models import HealthSample
from ..store import get_setting, set_setting
from .inputs import In
from .routes import today

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/history")
def get_history(days: int = Query(30, ge=7, le=90), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    day = today()  # the window and its days from one clock: the wall clock dropped the injected day's samples
    since = datetime.combine(day - timedelta(days=days + 1), datetime.min.time(), tzinfo=UTC)
    samples = session.exec(select(HealthSample).where(HealthSample.at >= since)).all()
    out = history(list(samples), day, days, tz=zone())
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
