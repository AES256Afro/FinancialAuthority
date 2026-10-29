"""Reports the owner builds, saved by name; groups of categories; a year in review; tax time and its pack."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from .inputs import In
from .. import auth, services
from ..db import get_session
from ..engine import custom_reports as cr
from ..engine.taxpack import build_pack, tax_pack
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


class ReportIn(In):
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


class GroupsIn(In):
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
    """The totals a return asks about, with the paystubs summed, the receipts by category and the statements kept."""
    return tax_pack(session, today(), year)


@router.get("/tax/pack")
def tax_pack_file(year: int | None = None, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> Response:
    """The year's pack as one .zip: the summary and the kept files themselves, so the owner only, as a document's file is."""
    try:
        body, name = build_pack(session, today(), year)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="tax_pack", args={"year": year}, rows=1)
    return Response(content=body, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
