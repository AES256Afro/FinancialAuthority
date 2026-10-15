"""Borrowing: what advances and installments cost, the cycle, the outstanding, and the steps that end it; a
stream turned into a plan with an end date; the card payoff setting."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.borrowing import borrowing_overview, sync_borrowing
from ..llm.reviewer import mark_pending
from ..models import Bill, InstallmentPlan, RecurringSeries
from ..store import get_setting, set_setting
from .routes import today

router = APIRouter(prefix="/api/v1/borrowing", tags=["borrowing"])
CARD_MODES = {"auto", "split", "next", "mid", "end", "minimum", "off"}


class PlanIn(BaseModel):
    bill_id: int | None = None
    series_id: int | None = None
    remaining: int
    apr: float | None = None


class SettingsIn(BaseModel):
    cards_pay_from: str | None = None  # split | next | mid | end


@router.get("")
def get_borrowing(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    out = borrowing_overview(session, today())
    out["settings"] = {"cards_pay_from": (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")}
    return out


@router.post("/sync")
def sync_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return sync_borrowing(session, today())


@router.post("/plan")
def make_plan(body: PlanIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    """An installment stream becomes a plan with an end date: from a bill that was really a plan, or from a
    pending series. The payments left come from the owner; the lender's app shows them."""
    if body.remaining < 1 or body.remaining > 120:
        raise HTTPException(400, "remaining is the number of payments still to come, 1 to 120")
    if body.bill_id:
        bill = session.get(Bill, body.bill_id)
        if bill is None or not bill.active:
            raise HTTPException(404, "No such bill")
        seen = 0
        series = session.get(RecurringSeries, bill.series_id) if bill.series_id else None
        if series is not None:
            seen = series.charges_seen
            series.kind, series.confirmed = "installment", True
            session.add(series)
        cadence_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(bill.cadence, 30)
        from ..engine.projection import next_due

        plan = InstallmentPlan(provider=bill.name, merchant=bill.name, total=round(bill.amount * (seen + body.remaining), 2), installment=round(bill.amount, 2), count=seen + body.remaining, remaining=body.remaining, next_date=next_due(bill.due_day, today()), cadence_days=cadence_days)
        bill.active = False
        session.add_all([plan, bill])
        what = bill.name
    elif body.series_id:
        series = session.get(RecurringSeries, body.series_id)
        if series is None:
            raise HTTPException(404, "No such series")
        cadence_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(series.cadence, 30)
        next_date = series.next_expected if series.next_expected >= today() else today()
        plan = InstallmentPlan(provider=series.merchant, merchant=series.merchant, total=round(series.typical_amount * (series.charges_seen + body.remaining), 2), installment=round(series.typical_amount, 2), count=series.charges_seen + body.remaining, remaining=body.remaining, next_date=next_date, cadence_days=cadence_days)
        series.kind, series.confirmed = "installment", True
        session.add_all([plan, series])
        what = series.merchant
    else:
        raise HTTPException(400, "give bill_id or series_id")
    session.commit()
    session.refresh(plan)
    mark_pending(session, f"installment plan set: {what}")
    return {"ok": True, "plan": {"id": plan.id, "provider": plan.provider, "installment": plan.installment, "remaining": plan.remaining, "next_date": plan.next_date.isoformat(), "ends": (plan.next_date + timedelta(days=plan.cadence_days * max(0, plan.remaining - 1))).isoformat()}}


@router.put("/settings")
def put_settings(body: SettingsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    stored = get_setting(session, "cards", {}) or {}
    if body.cards_pay_from is not None:
        if body.cards_pay_from not in CARD_MODES:
            raise HTTPException(400, "cards_pay_from is auto, split, next, mid, end, minimum or off")
        stored["pay_from"] = body.cards_pay_from
    set_setting(session, "cards", stored)
    mark_pending(session, "card payoff setting changed")
    return {"ok": True, "settings": {"cards_pay_from": stored.get("pay_from", "auto")}}
