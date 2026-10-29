"""Planned payments: what the owner says is leaving before the ledger sees it. Listed, added, removed, or
marked done by hand; settled by the bank sync when the transaction posts."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from .inputs import In
from .. import auth
from ..db import get_session
from ..engine.planned import planned_out, settle_planned
from ..llm.reviewer import mark_pending
from ..models import Account, PlannedPayment
from .routes import today

router = APIRouter(prefix="/api/v1/planned", tags=["planned"])
KINDS = {"card_payment", "bill", "transfer", "other"}


class PlannedIn(In):
    name: str
    amount: float
    date: date
    kind: str = "other"
    to_account_id: int | None = None
    note: str = ""


@router.get("")
def list_planned(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    settle_planned(session, today())
    rows = session.exec(select(PlannedPayment).order_by(PlannedPayment.done, PlannedPayment.date)).all()
    cards = session.exec(select(Account).where(Account.active == True, Account.kind == "card")).all()  # noqa: E712
    return {"items": [planned_out(p) for p in rows], "cards": [{"id": a.id, "name": a.name, "balance": a.balance} for a in cards]}


@router.post("")
def add_planned(body: PlannedIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.amount <= 0 or body.amount > 100_000:
        raise HTTPException(400, "amount is dollars leaving, up to 100,000")
    if body.kind not in KINDS:
        raise HTTPException(400, "kind is card_payment, bill, transfer or other")
    if body.to_account_id is not None and session.get(Account, body.to_account_id) is None:
        raise HTTPException(404, "No such account")
    row = PlannedPayment(name=body.name.strip()[:80] or "Planned payment", amount=round(body.amount, 2), date=body.date, kind=body.kind, to_account_id=body.to_account_id, note=body.note[:200])
    session.add(row)
    session.commit()
    session.refresh(row)
    mark_pending(session, f"planned payment: {row.name}")
    return planned_out(row)


@router.post("/{planned_id}/done")
def mark_done(planned_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    row = session.get(PlannedPayment, planned_id)
    if row is None:
        raise HTTPException(404, "No such planned payment")
    row.done = True
    session.add(row)
    session.commit()
    return planned_out(row)


@router.delete("/{planned_id}")
def remove_planned(planned_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    row = session.get(PlannedPayment, planned_id)
    if row is None:
        raise HTTPException(404, "No such planned payment")
    session.delete(row)
    session.commit()
    return {"ok": True}
