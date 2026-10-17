"""Moving day plan (M43): the dated checklist, ticks, and the notice letter queued for approval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import approvals, auth, services
from ..db import get_session
from ..engine.moving import moving_plan, tick
from .routes import today

router = APIRouter(prefix="/api/v1/move/plan", tags=["moving"])


@router.get("")
def get_plan(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return moving_plan(session, today())


class TickIn(BaseModel):
    done: bool = True


@router.post("/steps/{step_id}")
def tick_step(step_id: str, body: TickIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    ids = {s["id"] for s in moving_plan(session, today())["steps"]}
    if step_id not in ids:
        raise HTTPException(404, "No such step in the plan")
    tick(session, step_id, body.done, today())
    return moving_plan(session, today())


class LetterIn(BaseModel):
    to: str
    body: str


@router.post("/letter")
def send_letter(body: LetterIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    """The notice letter as an email that waits for approval on Needs you. Nothing is sent by this call."""
    plan = moving_plan(session, today())
    if not plan.get("letter"):
        raise HTTPException(409, "There is no home you are moving out of on Housing.")
    if "[your" in body.body.lower():
        raise HTTPException(400, "Fill in the parts in brackets first: your new address, your name and your phone number.")
    subject = f"Notice to vacate, {plan['move']['from']}"
    try:
        row = approvals.create(session, to=body.to, subject=subject, body=body.body, reason=f"Written notice to move out, due {plan['steps'][0]['date'] if plan['steps'] else ''}",
                               source="move:notice", created_by=principal.name, created_kind=principal.kind, for_person_id=principal.person_id if principal.kind == "person" else None)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="move_notice_queued", rows=1)
    return {"approval": approvals.out(row), "plan": moving_plan(session, today()),
            "said": "Queued on Needs you. It is sent only when you approve it there." if row.status == "pending" else f"The send is {row.status}."}


# Leaving the advance apps (M46) sits beside the move: both are dated plans built from the same paychecks.
exit_router = APIRouter(prefix="/api/v1/borrowing", tags=["borrowing"])


@exit_router.get("/exit")
def advance_exit(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.advance_exit import exit_plan

    return exit_plan(session, today())


@exit_router.get("/plan")
def get_borrowing_plan(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """The pay-later loans as one plan with its reasons (M54): the monthly stairs, the finish line, the interest
    ahead and the order for money beyond the schedule."""
    from ..engine.borrowing_plan import borrowing_plan

    return borrowing_plan(session, today())
