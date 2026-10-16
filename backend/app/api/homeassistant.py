"""Home Assistant and widgets: the few figures a dashboard shows, flat, so a REST sensor reads each one by name.

Read with an API key (Settings › Apps and keys). A key that hides names (merchants or totals only) is served here
too, with the bill's name held back; nothing else on /api answers such a key. The setup is in docs/HOMEASSISTANT.md."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import auth, services
from ..db import get_session
from ..engine.cover import account_cover
from ..engine.guide import glance
from ..engine.saving import saving_plan
from ..engine.snapshot import finance_snapshot
from .routes import today

router = APIRouter(prefix="/api/v1/ha", tags=["homeassistant"])
WORDS = {"good": "On track", "warn": "Watch", "crit": "Act"}


@router.get("")
def sensors(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    day = today()
    snap = finance_snapshot(session, day)
    cover = account_cover(session, day, saving=saving_plan(session, day, snap=snap))
    g = glance(session, day, snap=snap, cover=cover)
    bill = g.get("next_bill") or {}
    hide_names = principal.redaction == "aggregates"
    out = {
        "as_of": day.isoformat(),
        "safe_to_spend": g["safe_to_spend"],
        "safe_through": g["through"],
        "payday": g["payday"]["date"],
        "days_to_payday": g["payday"]["days"],
        "payday_amount": g["payday"]["amount"],
        "next_bill_name": ("A bill" if hide_names else bill.get("name")) if bill else None,
        "next_bill_amount": bill.get("amount"),
        "next_bill_date": bill.get("date"),
        "next_bill_days": (date.fromisoformat(bill["date"]) - day).days if bill.get("date") else None,
        "needs_you": snap["pending"].get("total", 0),
        "status": g["status"],
        "status_word": WORDS.get(g["status"], g["status"]),
        "sentence": None if hide_names else g["text"],
    }
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="ha_sensors", rows=1, redaction=principal.redaction)
    return out
