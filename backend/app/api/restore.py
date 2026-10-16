"""Restore points: list them, take one now, and put the whole database back as it was at one. Only the owner,
signed in, can restore; a restore takes a point of the moment before, so it can be undone the same way."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import auth, restore, services
from ..db import get_session

router = APIRouter(prefix="/api/v1/restore-points", tags=["restore"])


@router.get("")
def list_points(_: auth.Principal = Depends(auth.require_admin)) -> dict:
    return {"points": restore.list_points(), "keep": restore.KEEP, "status": restore.status()}


@router.post("")
def make_point(principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    made = restore.make_point("manual", f"Taken by hand ({principal.name})")
    return {"made": made, "points": restore.list_points()}


@router.post("/{name}/restore")
def restore_point(name: str, principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    if principal.kind != "owner":
        raise HTTPException(403, "Only the owner, signed in, can restore the database")
    try:
        done = restore.restore(name)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    from ..db import engine

    with Session(engine) as session:
        services.audit(session, client=principal.name, client_kind="owner", tool="restore", args={"restored": name, "before": done["before"]["name"]}, rows=0)
    return {**done, "points": restore.list_points()}
