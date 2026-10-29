"""Ledger rules: make one from the Ledger page, see them, remove them, run them again."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.rules import apply_to_ledger, create_rule, load_rules, rule_out
from ..models import Rule
from .routes import under_writer

router = APIRouter(prefix="/api/v1/ledger/rules", tags=["ledger"])


class RuleIn(BaseModel):
    kind: str
    pattern: str
    value: str = ""
    match_field: str = "merchant"


@router.get("")
def list_rules(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"rules": [rule_out(r) for r in load_rules(session)]}


@router.post("")
@under_writer
def make_rule(body: RuleIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        rule, changed = create_rule(session, kind=body.kind, pattern=body.pattern, value=body.value, match_field=body.match_field)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"rule": rule_out(rule), "changed": changed}


@router.post("/apply")
@under_writer
def reapply(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return {"changed": apply_to_ledger(session)}


@router.delete("/{rule_id}")
@under_writer
def remove_rule(rule_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, "No such rule")
    session.delete(rule)
    session.commit()
    return {"ok": True}
