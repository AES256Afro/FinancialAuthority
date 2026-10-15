"""The registers: recurring series and how they become bills, subscriptions, incomes or installment plans."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth
from ..db import get_session
from ..jobs.detect import pending_series, run_detection
from ..llm.reviewer import mark_pending
from ..models import Bill, Income, InstallmentPlan, RecurringSeries
from .routes import today

router = APIRouter(prefix="/api/v1", tags=["registers"])

KINDS = {"bill", "subscription", "income", "installment", "ignored"}


def series_out(s: RecurringSeries) -> dict:
    return {
        "id": s.id, "merchant": s.merchant, "category": s.category, "cadence": s.cadence, "typical_amount": s.typical_amount, "latest_amount": s.latest_amount,
        "charges_seen": s.charges_seen, "first_seen": s.first_seen.isoformat(), "last_date": s.last_date.isoformat(), "next_expected": s.next_expected.isoformat(),
        "active": s.active, "kind": s.kind, "confirmed": s.confirmed, "suggested_kind": s.suggested_kind,
    }


def bill_out(b: Bill) -> dict:
    return {
        "id": b.id, "name": b.name, "kind": b.kind, "amount": b.amount, "amount_mode": b.amount_mode, "cadence": b.cadence, "due_day": b.due_day, "autopay": b.autopay,
        "pay_from_account_id": b.pay_from_account_id, "grace_days": b.grace_days, "late_fee": b.late_fee, "movable": b.movable, "move_window": b.move_window,
        "category": b.category, "series_id": b.series_id, "active": b.active, "paid_from": getattr(b, "paid_from", "due") or "due",
    }


@router.get("/recurring")
def list_recurring(include_classified: bool = False, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    if include_classified:
        rows = session.exec(select(RecurringSeries).order_by(RecurringSeries.typical_amount.desc())).all()
    else:
        rows = pending_series(session)
    return {"series": [series_out(s) for s in rows], "pending": len(pending_series(session))}


@router.post("/recurring/detect")
def detect_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return run_detection(session, today())


class ClassifyIn(BaseModel):
    kind: str
    name: str | None = None
    amount: float | None = None
    due_day: int | None = None
    autopay: bool = False
    movable: bool = True
    remaining: int | None = None


@router.get("/paystubs")
def list_paystubs(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.paystubs import paystubs_overview

    return paystubs_overview(session, limit=12)


@router.get("/payments")
def list_payments(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.payments import payments_overview

    return payments_overview(session, today())


@router.post("/payments/match")
def match_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.payments import match_payments

    return match_payments(session, today())


@router.post("/recurring/{series_id}/classify")
def classify(series_id: int, body: ClassifyIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    series = session.get(RecurringSeries, series_id)
    if series is None:
        raise HTTPException(404, "No such series")
    if body.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(sorted(KINDS))}")
    name = (body.name or series.merchant).strip()
    amount = body.amount if body.amount is not None else series.typical_amount
    created: dict = {}
    if body.kind in ("bill", "subscription"):
        due_day = body.due_day or series.next_expected.day
        cadence = series.cadence if series.cadence in ("weekly", "biweekly", "monthly", "quarterly", "yearly") else "monthly"
        from ..jobs.detect import account_for_series

        bill = Bill(name=name, kind=body.kind, amount=round(amount, 2), amount_mode="fixed", cadence=cadence, due_day=due_day, autopay=body.autopay or body.kind == "subscription", movable=body.movable, pay_from_account_id=account_for_series(session, series),
                    move_window="1-28" if body.movable else "", category=series.category, series_id=series.id)
        session.add(bill)
        session.flush()
        created = {"bill": bill_out(bill)}
    elif body.kind == "income":
        next_date = series.next_expected if series.next_expected >= today() else today()
        income = Income(name=name, cadence=series.cadence, expected_net=round(amount, 2), next_date=next_date, series_id=series.id)
        session.add(income)
        session.flush()
        created = {"income": {"id": income.id, "name": income.name, "cadence": income.cadence, "expected_net": income.expected_net, "next_date": income.next_date.isoformat()}}
    elif body.kind == "installment":
        remaining = body.remaining or 1
        plan = InstallmentPlan(provider=name, merchant=name, total=round(amount * (series.charges_seen + remaining), 2), installment=round(amount, 2), count=series.charges_seen + remaining,
                               remaining=remaining, next_date=series.next_expected, cadence_days={"weekly": 7, "biweekly": 14, "monthly": 30}.get(series.cadence, 14))
        session.add(plan)
        session.flush()
        created = {"installment_plan": {"id": plan.id, "merchant": plan.merchant, "installment": plan.installment, "remaining": plan.remaining, "next_date": plan.next_date.isoformat()}}
    series.kind = body.kind
    series.confirmed = True
    session.add(series)
    session.commit()
    session.refresh(series)
    return {"series": series_out(series), **created, "pending": len(pending_series(session))}


# ---- bills and incomes ---------------------------------------------------------------------


@router.get("/bills")
def list_bills(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Bill).where(Bill.active == True).order_by(Bill.due_day)).all()  # noqa: E712
    return {"bills": [bill_out(b) for b in rows]}


class BillIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    amount: float | None = None
    amount_mode: str | None = None
    due_day: int | None = None
    autopay: bool | None = None
    movable: bool | None = None
    grace_days: int | None = None
    late_fee: float | None = None
    category: str | None = None
    active: bool | None = None
    paid_from: str | None = None  # due | mid | end
    pay_from_account_id: int | None = None  # the account the bill is charged to; a card means it rides the card payment


PAID_FROM = {"due", "mid", "end"}


@router.post("/bills")
def create_bill(body: BillIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if not body.name or body.amount is None or body.due_day is None:
        raise HTTPException(400, "A bill needs a name, an amount and a due day")
    if body.paid_from and body.paid_from not in PAID_FROM:
        raise HTTPException(400, "paid_from is due, mid or end")
    bill = Bill(name=body.name, kind=body.kind or "bill", amount=body.amount, amount_mode=body.amount_mode or "fixed", due_day=max(1, min(31, body.due_day)), paid_from=body.paid_from or "due",
                autopay=bool(body.autopay), movable=body.movable if body.movable is not None else True, grace_days=body.grace_days or 0, late_fee=body.late_fee or 0.0, category=body.category or "")
    session.add(bill)
    session.commit()
    session.refresh(bill)
    mark_pending(session, f"bill added: {bill.name}")
    return bill_out(bill)


@router.put("/bills/{bill_id}")
def update_bill(bill_id: int, body: BillIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    bill = session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(404, "No such bill")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field == "due_day":
            value = max(1, min(31, int(value)))
        if field == "paid_from" and value not in PAID_FROM:
            raise HTTPException(400, "paid_from is due, mid or end")
        if field == "kind" and value not in ("bill", "subscription"):
            raise HTTPException(400, "kind must be bill or subscription")
        setattr(bill, field, value)
    session.add(bill)
    session.commit()
    session.refresh(bill)
    mark_pending(session, f"bill changed: {bill.name}")
    return bill_out(bill)


@router.get("/incomes")
def list_incomes(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    return {"incomes": [{"id": i.id, "name": i.name, "cadence": i.cadence, "expected_net": i.expected_net, "expected_net_end": i.expected_net_end, "next_date": i.next_date.isoformat(), "series_id": i.series_id} for i in rows]}


class IncomeIn(BaseModel):
    name: str | None = None
    cadence: str | None = None
    expected_net: float | None = None
    expected_net_end: float | None = None
    next_date: date | None = None
    active: bool | None = None


@router.put("/incomes/{income_id}")
def update_income(income_id: int, body: IncomeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    income = session.get(Income, income_id)
    if income is None:
        raise HTTPException(404, "No such income")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(income, field, value)
    session.add(income)
    session.commit()
    return {"ok": True}
