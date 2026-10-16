"""Reports the owner builds, saved by name; groups of categories; a year in review; tax-time totals."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth
from ..db import get_session
from ..engine import custom_reports as cr
from .routes import today

router = APIRouter(prefix="/api/v1/reports/custom", tags=["reports"])


@router.get("")
def run(rows: str = "category", period: str = "month", start: date | None = None, end: date | None = None, include_income: bool = False, only: str = "",
        session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    try:
        return cr.run_report(session, today(), rows=rows, period=period, start=start, end=end, include_income=include_income, only=[x for x in only.split(",") if x] or None)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/saved")
def list_saved(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..store import get_setting

    return {"reports": cr.saved(session), "groups": get_setting(session, cr.GROUPS, {}) or {}}


class ReportIn(BaseModel):
    name: str
    rows: str = "category"
    period: str = "month"
    picture: str = "bars"
    include_income: bool = False
    only: list[str] = []
    start: str | None = None
    end: str | None = None


@router.post("/saved")
def save(body: ReportIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        cr.save_report(session, body.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return list_saved(session)


@router.delete("/saved/{name}")
def remove(name: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    cr.remove_report(session, name)
    return list_saved(session)


class GroupsIn(BaseModel):
    groups: dict[str, list[str]]


@router.put("/groups")
def put_groups(body: GroupsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    cr.save_groups(session, body.groups)
    return list_saved(session)


@router.get("/year")
def year(year: int | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    try:
        return cr.year_in_review(session, today(), year)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/tax")
def tax(year: int | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return cr.tax_totals(session, today(), year)
