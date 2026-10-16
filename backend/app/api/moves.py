"""Switching banks: start a move from one account to another, read its plan (the ledger is read first, so each
switch it can confirm is confirmed), decide and mark items, refresh what is found, and finish or cancel."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, services
from ..db import get_session
from ..engine.moves import _point, move_plan, refresh_items, set_item, start_move, verify
from ..models import Account, AccountMove, MoveItem
from .routes import today

router = APIRouter(prefix="/api/v1/moves", tags=["moves"])


def _move(session: Session, move_id: int) -> AccountMove:
    move = session.get(AccountMove, move_id)
    if move is None:
        raise HTTPException(404, "No such move")
    return move


@router.get("")
def list_moves(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    names = {a.id: a.name for a in accounts}
    moves = session.exec(select(AccountMove).order_by(AccountMove.id.desc())).all()
    out = []
    for m in moves:
        items = session.exec(select(MoveItem).where(MoveItem.move_id == m.id)).all()
        moving = [i for i in items if i.decision == "move"]
        out.append({"id": m.id, "name": m.name, "status": m.status, "source": names.get(m.source_account_id, "?"), "target": names.get(m.target_account_id or -1, m.target_name),
                    "started_on": m.started_on.isoformat(), "finish_by": m.finish_by.isoformat() if m.finish_by else None,
                    "moving": len(moving), "confirmed": len([i for i in moving if i.status == "confirmed"])})
    return {"moves": out, "accounts": [{"id": a.id, "name": a.name, "kind": a.kind, "institution": a.institution} for a in accounts]}


class MoveIn(BaseModel):
    source_account_id: int
    target_account_id: int | None = None
    target_name: str = ""
    finish_by: date | None = None
    name: str | None = None


@router.post("")
def create_move(body: MoveIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    if session.exec(select(AccountMove).where(AccountMove.status == "moving", AccountMove.source_account_id == body.source_account_id)).first():
        raise HTTPException(409, "A move from that account is already under way")
    try:
        move = start_move(session, today(), source_id=body.source_account_id, target_id=body.target_account_id, target_name=body.target_name[:80], finish_by=body.finish_by, name=body.name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind="owner" if principal.kind == "owner" else "key", tool="move_start", args={"source": body.source_account_id}, rows=1)
    return move_plan(session, move, today())


@router.get("/{move_id}")
def get_move(move_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    move = _move(session, move_id)
    if move.status == "moving":
        verify(session, move, today())
    out = move_plan(session, move, today())
    services.audit(session, client=principal.name, client_kind="owner" if principal.kind == "owner" else "key", tool="account_move", args={"id": move_id}, rows=out["progress"]["total"], redaction=principal.redaction)
    return out


class MovePatch(BaseModel):
    target_account_id: int | None = None
    target_name: str | None = None
    finish_by: date | None = None
    debit_card_on: date | None = None
    status: str | None = None


@router.put("/{move_id}")
def update_move(move_id: int, body: MovePatch, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    move = _move(session, move_id)
    fields = body.model_dump(exclude_unset=True)
    if "target_account_id" in fields and fields["target_account_id"] is not None:
        acc = session.get(Account, fields["target_account_id"])
        if acc is None or acc.id == move.source_account_id:
            raise HTTPException(400, "The new account must be another account in the app")
        move.target_account_id = acc.id
        move.target_name = acc.name
        # Items already switched now point their registers at the account that is really there.
        for item in session.exec(select(MoveItem).where(MoveItem.move_id == move.id, MoveItem.status != "todo")).all():
            _point(session, item, acc.id)
    if fields.get("target_name"):
        move.target_name = fields["target_name"][:80]
    if "finish_by" in fields:
        move.finish_by = fields["finish_by"]
    if "debit_card_on" in fields:
        move.debit_card_on = fields["debit_card_on"]
    if fields.get("status"):
        if fields["status"] not in ("moving", "done", "cancelled"):
            raise HTTPException(400, "status is moving, done or cancelled")
        move.status = fields["status"]
    session.add(move)
    session.commit()
    return move_plan(session, move, today())


class ItemPatch(BaseModel):
    decision: str | None = None
    status: str | None = None
    note: str | None = None


@router.put("/{move_id}/items/{item_id}")
def update_item(move_id: int, item_id: int, body: ItemPatch, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    move = _move(session, move_id)
    item = session.get(MoveItem, item_id)
    if item is None or item.move_id != move.id:
        raise HTTPException(404, "No such item")
    try:
        set_item(session, move, item, today(), decision=body.decision, status=body.status, note=body.note)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return move_plan(session, move, today())


@router.post("/{move_id}/refresh")
def refresh(move_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    move = _move(session, move_id)
    added = refresh_items(session, move, today())
    verify(session, move, today())
    return {**move_plan(session, move, today()), "added": added}
