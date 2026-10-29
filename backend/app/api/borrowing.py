"""Borrowing: what advances and installments cost, the cycle, the outstanding, and the steps that end it; a
stream turned into a plan with an end date; the card payoff setting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session

from .inputs import In
from .. import auth
from ..db import get_session
from ..engine.borrowing import borrowing_overview
from ..llm.reviewer import mark_pending
from ..models import Bill, InstallmentPlan, RecurringSeries
from ..store import get_setting, set_setting
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1/borrowing", tags=["borrowing"])
CARD_MODES = {"auto", "split", "next", "mid", "end", "minimum", "off"}


class PlanIn(In):
    bill_id: int | None = None
    series_id: int | None = None
    remaining: int
    apr: float | None = None


class SettingsIn(In):
    cards_pay_from: str | None = None  # split | next | mid | end


@router.get("")
def get_borrowing(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    out = borrowing_overview(session, today())
    out["settings"] = {"cards_pay_from": (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")}
    return out


@router.post("/plan")
@under_writer
def make_plan(body: PlanIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """An installment stream becomes a plan with an end date: from a bill that was really a plan, or from a
    pending series. The payments left come from the owner; the lender's app shows them."""
    if body.remaining < 1 or body.remaining > 120:
        raise HTTPException(400, "remaining is the number of payments still to come, 1 to 120")
    if body.apr is not None and not 0 <= body.apr <= 400:
        raise HTTPException(400, "apr is a percent a year, 0 to 400")
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

        plan = InstallmentPlan(provider=bill.name, merchant=bill.name, total=round(bill.amount * (seen + body.remaining), 2), installment=round(bill.amount, 2), count=seen + body.remaining, remaining=body.remaining, next_date=next_due(bill.due_day, today()), cadence_days=cadence_days, apr=body.apr)
        bill.active = False
        session.add_all([plan, bill])
        what = bill.name
    elif body.series_id:
        series = session.get(RecurringSeries, body.series_id)
        if series is None:
            raise HTTPException(404, "No such series")
        cadence_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(series.cadence, 30)
        next_date = series.next_expected if series.next_expected >= today() else today()
        plan = InstallmentPlan(provider=series.merchant, merchant=series.merchant, total=round(series.typical_amount * (series.charges_seen + body.remaining), 2), installment=round(series.typical_amount, 2), count=series.charges_seen + body.remaining, remaining=body.remaining, next_date=next_date, cadence_days=cadence_days, apr=body.apr)
        series.kind, series.confirmed = "installment", True
        session.add_all([plan, series])
        what = series.merchant
    else:
        raise HTTPException(400, "give bill_id or series_id")
    session.commit()
    session.refresh(plan)
    mark_pending(session, f"installment plan set: {what}")
    # The plan's end date is the engine's, the same one every screen shows; the route does no date arithmetic of its own.
    ends = next((l["ends"] for l in borrowing_overview(session, today())["loans"] if l["id"] == plan.id), None)
    return {"ok": True, "plan": {"id": plan.id, "provider": plan.provider, "installment": plan.installment, "remaining": plan.remaining, "next_date": plan.next_date.isoformat(), "ends": ends}}


@router.post("/statement")
@under_writer
def import_statement(file: UploadFile = File(...), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Affirm's monthly statement PDF, the lender's own register of every loan: read and applied at once."""
    from ..engine.borrowing import statement_fact_from_file

    raw = file.file.read(50 * 1024 * 1024 + 1)
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "That file is over 50 MB")
    out = statement_fact_from_file(session, raw, file.filename or "statement.pdf", today(), "upload")
    if out is None:
        raise HTTPException(400, "That file does not read like an Affirm loans statement. The reader looks for the Loans Summary table.")
    return {"ok": True, **out}


@router.put("/settings")
@under_writer
def put_settings(body: SettingsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    stored = get_setting(session, "cards", {}) or {}
    if body.cards_pay_from is not None:
        if body.cards_pay_from not in CARD_MODES:
            raise HTTPException(400, "cards_pay_from is auto, split, next, mid, end, minimum or off")
        stored["pay_from"] = body.cards_pay_from
    set_setting(session, "cards", stored)
    mark_pending(session, "card payoff setting changed")
    return {"ok": True, "settings": {"cards_pay_from": stored.get("pay_from", "auto")}}
