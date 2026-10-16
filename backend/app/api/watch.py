"""The watching assistant's findings, and the owner's "this is expected"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine.watch import dismiss, overview
from .routes import today

router = APIRouter(prefix="/api/v1/watch", tags=["watch"])


@router.get("")
def get_watch(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return overview(session, today())


@router.post("/{key}/expected")
def mark_expected(key: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    try:
        dismiss(session, key, today())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return overview(session, today())
