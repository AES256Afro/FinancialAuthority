"""Needs you that empties (M44): the grouped view, answering alike items together, and "is it paid?"."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from .inputs import In
from .. import auth, services
from ..db import get_session
from ..engine import facts
from ..engine.needsyou import AlreadyAnswered, answer_payment, plan_many, triage
from ..jobs.writing import one_writer
from ..models import ExtractedFact, MessageRef, Payment
from .routes import today

router = APIRouter(prefix="/api/v1/needs-you", tags=["needs you"])


@router.get("")
def get_triage(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = triage(session, today())
    if principal.kind == "key":  # the waiting facts carry the sender's words: boxed for a key or a connected app
        from ..mail.box import MAIL_WORDS, box_fields

        out = {**out, "groups": box_fields(out["groups"], "waiting mail"), "how": MAIL_WORDS}
    return out


class ManyIn(In):
    ids: list[int]
    action: str  # preview | confirm | reject


@router.post("/facts")
def answer_many(body: ManyIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    """Preview, confirm or discard several at once. Confirm follows the plan the preview shows (plan_many): only a
    bill's newest notice counts, an older one is set aside, and one that cannot be confirmed together stays waiting
    and says why. Each is answered on its own, so a failure leaves the rest done."""
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
        with one_writer():  # each fact claimed as its own card claims it: read again under the lock, so one answered there meanwhile is found answered
            _answer_one(session, fid, body.action, steps, principal, done, failed)
            aside += 1 if done and done[-1].get("aside") else 0
    confirmed = [row for row in done if not row.get("aside")] if body.action == "confirm" else []
    for row in done:
        row.pop("aside", None)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool=f"facts_{body.action}_many", args={"ids": body.ids[:50]}, rows=len(done))
    if body.action == "confirm" and principal.kind == "key":  # a key or a connected app confirmed: the owner hears of it, as from the MCP tool
        from .mail import told_owner

        kinds = {f.id: f.kind for f in chosen}
        told_owner(principal.name, [(kinds.get(row["id"], "fact"), row["id"], row.get("applied_to", "")) for row in confirmed])
    if done:
        from ..llm.reviewer import mark_pending

        mark_pending(session, f"{len(done)} answered on Needs you")
    kept = len(done) - aside
    head = (f"Confirmed {kept}" + (f" and set aside {aside} older notice{'s' if aside != 1 else ''}" if aside else "") + ".") if body.action == "confirm" else f"Discarded {len(done)}."
    word = head + (f" {len(failed)} still wait: they need your answer one at a time." if failed else "")
    return {"done": done, "failed": failed, "said": word, "triage": triage(session, today())}


def _answer_one(session: Session, fid: int, action: str, steps: dict, principal: auth.Principal, done: list, failed: list) -> None:
    """One fact answered under the writer lock; what happened lands on done (with aside=True for one set aside) or failed."""
    from .mail import ConfirmIn, apply_fact

    fact = session.get(ExtractedFact, fid)
    if fact is not None:
        session.refresh(fact)
    if fact is None or fact.status != "pending":
        failed.append({"id": fid, "error": "already answered"})
        return
    if action == "reject":
        facts.resolve_fact(session, fact, "rejected", by=facts.who(principal))
        session.commit()
        done.append({"id": fid, "applied_to": ""})
        return
    step = steps.get(fid, {"do": "own_card", "why": "needs your answer on its own card"})
    if step["do"] == "set_aside":
        facts.resolve_fact(session, fact, "rejected", applied_to=f"set aside: {step['why']}"[:200], by=facts.who(principal))
        session.commit()
        done.append({"id": fid, "applied_to": "", "aside": True})
        return
    if step["do"] != "apply":
        failed.append({"id": fid, "error": step["why"]})
        return
    if step.get("record"):
        # A notice that changes nothing in the plan is kept as read; nothing is applied.
        facts.resolve_fact(session, fact, "confirmed", applied_to="read: changes nothing in the plan", by=facts.who(principal))
        session.commit()
        done.append({"id": fid, "applied_to": ""})
        return
    ref = session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None
    b = facts.watermark(session)
    try:
        applied = apply_fact(session, fact, ref, ConfirmIn(apply=True), owner=principal.full)
    except HTTPException as error:
        session.rollback()
        failed.append({"id": fid, "error": str(error.detail)})
        return
    facts.resolve_fact(session, fact, "applied" if applied else "confirmed", applied_to=applied, by=facts.who(principal), before=b)
    session.commit()
    done.append({"id": fid, "applied_to": applied})


class PaidIn(In):
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
    except AlreadyAnswered as error:  # answered in another tab meanwhile: its sentence says how
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="payment_answer", args={"payment": payment_id, "answer": body.answer}, rows=1)
    return {"said": said, "triage": triage(session, today())}
