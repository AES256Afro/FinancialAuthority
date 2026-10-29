"""Approvals: the sends waiting, approve or deny, standing allowances per recipient. Each person's sends are their
own: the owner approves the owner's, a second person their own, and neither approves the other's."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import approvals, auth
from ..db import get_session
from ..models import Approval

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class ApprovalIn(BaseModel):
    to: str
    subject: str
    body: str
    reason: str = ""
    source: str = ""
    account_id: int | None = None


class EditIn(BaseModel):
    to: str | None = None
    subject: str | None = None
    body: str | None = None


class DecideIn(BaseModel):
    allow_always: bool = False


def _mine(principal: auth.Principal) -> int | None:
    """Whose sends this caller decides or queues: the owner's (None) or a second person's own. A key or a
    connected app a second person made carries that person, so what it queues is theirs, never the owner's."""
    return principal.person_id


def _check_mine(row: Approval, principal: auth.Principal, *, deny: bool = False) -> None:
    if row.for_person_id == _mine(principal):
        return
    if deny and principal.kind == "owner" and row.for_person_id is not None and row.for_person_id < 0:
        return  # a send left by a person the owner removed: the owner may turn it down, never send it
    raise HTTPException(403, "This send is someone else's to approve; each person approves only their own.")


def _whose(session: Session, row: Approval, principal: auth.Principal) -> dict:
    from ..models import Person
    from ..config import settings

    out = approvals.out(row)
    mine = row.for_person_id == _mine(principal)
    person = session.get(Person, row.for_person_id) if row.for_person_id else None
    out["mine"] = mine
    out["whose"] = "yours" if mine else (person.display_name or person.username) if person else settings.username
    return out


@router.get("")
def list_approvals(status: str = "pending", limit: int = 50, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    query = select(Approval)
    if status != "all":
        query = query.where(Approval.status == status)
    rows = session.exec(query.order_by(Approval.created_at.desc()).limit(max(1, min(limit, 200)))).all()
    pending = len(session.exec(select(Approval.id).where(Approval.status == "pending", Approval.for_person_id == _mine(principal))).all())
    return {"approvals": [_whose(session, r, principal) for r in rows], "pending": pending, "allowances": approvals.allowances(session, _mine(principal))}


@router.post("")
def queue(body: ApprovalIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    try:
        row = approvals.create(session, to=body.to, subject=body.subject, body=body.body, reason=body.reason, source=body.source, created_by=principal.name, created_kind=principal.kind, account_id=body.account_id,
                               for_person_id=_mine(principal))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return approvals.out(row)


@router.put("/{approval_id}")
def edit(approval_id: int, body: EditIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    row = session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(404, "No such approval")
    _check_mine(row, principal)
    if row.status != "pending":
        raise HTTPException(409, f"The approval is {row.status}")
    changed = {f: v.strip() for f, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        approvals.check_draft(changed.get("to", row.to), changed.get("subject", row.subject), changed.get("body", row.body))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    try:
        row = approvals.edit_draft(session, row, changed)  # read again under the decision lock: one approved meanwhile is not rewritten
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return approvals.out(row)


@router.post("/{approval_id}/approve")
def approve(approval_id: int, body: DecideIn | None = None, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    row = session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(404, "No such approval")
    _check_mine(row, principal)
    try:
        row = approvals.approve(session, row, decided_by=principal.name, allow_always=bool(body and body.allow_always))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return approvals.out(row)


@router.post("/{approval_id}/deny")
def deny(approval_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    row = session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(404, "No such approval")
    _check_mine(row, principal, deny=True)
    try:
        row = approvals.deny(session, row, decided_by=principal.name)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return approvals.out(row)


@router.delete("/allowances/{recipient}")
def remove_allowance(recipient: str, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    return {"allowances": approvals.disallow(session, recipient, _mine(principal))}
