"""Needs you that empties (M44): the grouped view, answering alike items together, and "is it paid?"."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, services
from ..db import get_session
from ..engine.needsyou import answer_payment, plan_many, triage
from ..models import ExtractedFact, MessageRef, Payment, utcnow
from .routes import today

router = APIRouter(prefix="/api/v1/needs-you", tags=["needs you"])


@router.get("")
def get_triage(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return triage(session, today())


class ManyIn(BaseModel):
    ids: list[int]
    action: str  # preview | confirm | reject


@router.post("/facts")
def answer_many(body: ManyIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    """Preview, confirm or discard several at once. Confirm follows the plan the preview shows (plan_many): only a
    bill's newest notice counts, an older one is set aside, and one that cannot be confirmed together stays waiting
    and says why. Each is answered on its own, so a failure leaves the rest done."""
    from .mail import ConfirmIn, apply_fact

    if body.action not in ("preview", "confirm", "reject") or not body.ids or len(body.ids) > 200:
        raise HTTPException(400, "Choose preview, confirm or reject, for between 1 and 200 items")
    found = {f.id: f for f in session.exec(select(ExtractedFact).where(ExtractedFact.id.in_(body.ids))).all()}  # type: ignore[union-attr]
    chosen = [found[i] for i in dict.fromkeys(body.ids) if i in found]
    if body.action == "preview":
        plan = plan_many(session, chosen, today(), loose=principal.full)
        return {k: v for k, v in plan.items() if k != "steps"}
    steps = {s["id"]: s for s in plan_many(session, chosen, today(), loose=principal.full)["steps"]} if body.action == "confirm" else {}
    done, failed, aside = [], [], 0
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
        step = steps.get(fid, {"do": "own_card", "why": "needs your answer on its own card"})
        if step["do"] == "set_aside":
            fact.status, fact.applied_to, fact.resolved_at = "rejected", f"set aside: {step['why']}"[:200], utcnow()
            session.add(fact)
            session.commit()
            aside += 1
            done.append({"id": fid, "applied_to": ""})
            continue
        if step["do"] != "apply":
            failed.append({"id": fid, "error": step["why"]})
            continue
        if step.get("record"):
            # A notice that changes nothing in the plan is kept as read; nothing is applied.
            fact.status, fact.applied_to, fact.resolved_at = "confirmed", "read: changes nothing in the plan", utcnow()
            session.add(fact)
            session.commit()
            done.append({"id": fid, "applied_to": ""})
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
    kept = len(done) - aside
    head = (f"Confirmed {kept}" + (f" and set aside {aside} older notice{'s' if aside != 1 else ''}" if aside else "") + ".") if body.action == "confirm" else f"Discarded {len(done)}."
    word = head + (f" {len(failed)} still wait: they need your answer one at a time." if failed else "")
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
