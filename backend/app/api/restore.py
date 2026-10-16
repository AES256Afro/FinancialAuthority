"""Restore points: list them, take one now, and put the whole database back as it was at one. Only the owner,
signed in, can restore; a restore takes a point of the moment before, so it can be undone the same way."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, restore, services
from ..db import get_session

router = APIRouter(prefix="/api/v1/restore-points", tags=["restore"])


@router.get("")
def list_points(_: auth.Principal = Depends(auth.require_admin)) -> dict:  # noqa: B008
    return {"points": restore.list_points(), "keep": restore.KEEP, "status": restore.status()}


@router.post("")
def make_point(principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    made = restore.make_point("manual", f"Taken by hand ({principal.name})")
    return {"made": made, "points": restore.list_points()}


class OffboxIn(BaseModel):
    folder: str | None = None
    passphrase: str | None = None
    every_days: int | None = None


@router.get("/offbox")
def offbox_state(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import offbox

    return offbox.state(session)


@router.put("/offbox")
def offbox_save(body: OffboxIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import offbox

    if principal.kind != "owner":
        raise HTTPException(403, "Only the owner, signed in, sets the off-box copy")
    try:
        return offbox.save(session, folder=body.folder, passphrase=body.passphrase, every_days=body.every_days)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/offbox/copy")
def offbox_copy_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import offbox

    try:
        made = offbox.copy_now(session)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    return {"made": made, **offbox.state(session)}


@router.post("/drill")
def drill_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import offbox

    return {"drill": offbox.drill(session)}


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
