"""Approvals: the sends waiting for the owner, approve or deny, standing allowances per recipient."""

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


@router.get("")
def list_approvals(status: str = "pending", limit: int = 50, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    query = select(Approval)
    if status != "all":
        query = query.where(Approval.status == status)
    rows = session.exec(query.order_by(Approval.created_at.desc()).limit(max(1, min(limit, 200)))).all()
    pending = len(session.exec(select(Approval.id).where(Approval.status == "pending")).all())
    return {"approvals": [approvals.out(r) for r in rows], "pending": pending, "allowances": approvals.allowances(session)}


@router.post("")
def queue(body: ApprovalIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    try:
        row = approvals.create(session, to=body.to, subject=body.subject, body=body.body, reason=body.reason, source=body.source, created_by=principal.name, created_kind=principal.kind, account_id=body.account_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return approvals.out(row)


@router.put("/{approval_id}")
def edit(approval_id: int, body: EditIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    row = session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(404, "No such approval")
    if row.status != "pending":
        raise HTTPException(409, f"The approval is {row.status}")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value.strip())
    session.add(row)
    session.commit()
    session.refresh(row)
    return approvals.out(row)


@router.post("/{approval_id}/approve")
def approve(approval_id: int, body: DecideIn | None = None, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    row = session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(404, "No such approval")
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
    try:
        row = approvals.deny(session, row, decided_by=principal.name)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return approvals.out(row)


@router.delete("/allowances/{recipient}")
def remove_allowance(recipient: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    return {"allowances": approvals.disallow(session, recipient)}
