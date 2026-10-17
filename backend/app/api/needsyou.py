"""Needs you that empties (M44): the grouped view, answering alike items together, and "is it paid?"."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, services
from ..db import get_session
from ..engine.needsyou import answer_payment, complete, triage
from ..models import ExtractedFact, MessageRef, Payment, utcnow
from .routes import today

router = APIRouter(prefix="/api/v1/needs-you", tags=["needs you"])


@router.get("")
def get_triage(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return triage(session, today())


class ManyIn(BaseModel):
    ids: list[int]
    action: str  # confirm | reject


@router.post("/facts")
def answer_many(body: ManyIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    """Confirm (only the complete ones) or discard several at once; each is answered on its own, so one that cannot
    be confirmed stays waiting and says why."""
    from .mail import ConfirmIn, apply_fact

    if body.action not in ("confirm", "reject") or not body.ids or len(body.ids) > 200:
        raise HTTPException(400, "Choose confirm or reject, for between 1 and 200 items")
    done, failed = [], []
    for fid in body.ids:
        fact = session.get(ExtractedFact, fid)
        if fact is None or fact.status != "pending":
            failed.append({"id": fid, "error": "already answered"})
            continue
        if body.action == "reject":
            fact.status, fact.resolved_at = "rejected", utcnow()
            session.add(fact)
            session.commit()
            done.append({"id": fid, "applied_to": ""})
            continue
        if not complete(fact):
            failed.append({"id": fid, "error": "needs your answer on its own card"})
            continue
        ref = session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None
        try:
            applied = apply_fact(session, fact, ref, ConfirmIn(apply=True), owner=principal.full)
        except HTTPException as error:
            session.rollback()
            failed.append({"id": fid, "error": str(error.detail)})
            continue
        fact.status, fact.applied_to, fact.resolved_at = ("applied" if applied else "confirmed"), applied, utcnow()
        session.add(fact)
        session.commit()
        done.append({"id": fid, "applied_to": applied})
    services.audit(session, client=principal.name, client_kind=principal.kind, tool=f"facts_{body.action}_many", args={"ids": body.ids[:50]}, rows=len(done))
    if done:
        from ..llm.reviewer import mark_pending

        mark_pending(session, f"{len(done)} answered on Needs you")
    word = (f"{'Confirmed' if body.action == 'confirm' else 'Discarded'} {len(done)}." + (f" {len(failed)} still wait: they need your answer one at a time." if failed else ""))
    return {"done": done, "failed": failed, "said": word, "triage": triage(session, today())}


class PaidIn(BaseModel):
    answer: str  # paid | not_yet | stopped
    transaction_id: int | None = None
    paid_on: date | None = None


@router.post("/payments/{payment_id}")
def answer_one_payment(payment_id: int, body: PaidIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(404, "No such payment")
    try:
        said = answer_payment(session, payment, body.answer, today(), transaction_id=body.transaction_id, paid_on=body.paid_on)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="payment_answer", args={"payment": payment_id, "answer": body.answer}, rows=1)
    return {"said": said, "triage": triage(session, today())}
