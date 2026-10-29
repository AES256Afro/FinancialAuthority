"""Cover, Saving and the guide: what each account needs and the payday sheet, the saving ladder and its
share, the one thing today with its streaks, the weekly check-in, and the first run. Each answer is the
engine's; the screens and the tools show the same."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .inputs import In
from .. import auth, services
from ..db import get_session
from ..engine import firstrun as firstrun_engine
from ..engine.cover import account_cover, tick
from ..engine.guide import one_thing, record_checkin, weekly_checkin
from ..engine.saving import save_settings, saving_plan
from ..engine.snapshot import finance_snapshot
from .routes import today

router = APIRouter(prefix="/api/v1", tags=["guide"])


def _who(p: auth.Principal) -> tuple[str, str]:
    return p.name, (p.kind if p.kind in ("owner", "person") else "key")


@router.get("/cover")
def cover(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    snap = finance_snapshot(session, today())
    out = account_cover(session, today(), saving=saving_plan(session, today(), snap=snap))
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="accounts_cover", args={}, rows=len(out["accounts"]), redaction=principal.redaction)
    return out


class TickIn(In):
    id: str
    done: bool = True


@router.put("/cover/sheet")
def cover_tick(body: TickIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if len(body.id) > 200:
        raise HTTPException(400, "id is too long")
    return tick(session, body.id, body.done, today())


@router.get("/saving")
def saving(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = saving_plan(session, today())
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="saving_plan", args={}, rows=len(out["rungs"]), redaction=principal.redaction)
    return out


class SavingIn(In):
    share: int | None = None
    clear_share: bool = False
    starter: float | None = None
    rollover: bool | None = None


@router.put("/saving")
def saving_settings(body: SavingIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.share is not None and not 0 <= body.share <= 80:
        raise HTTPException(400, "share is a percent from 0 to 80")
    if body.starter is not None and not 0 <= body.starter <= 100_000:
        raise HTTPException(400, "starter is dollars, up to 100,000")
    save_settings(session, share=body.share, starter=body.starter, rollover=body.rollover, clear_share=body.clear_share)
    return saving_plan(session, today())


@router.get("/today")
def today_thing(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    snap = finance_snapshot(session, today())
    cov = account_cover(session, today(), saving=saving_plan(session, today(), snap=snap))
    out = one_thing(session, today(), snap=snap, cover=cov)
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="today_one_thing", args={}, rows=1 + len(out["others"]), redaction=principal.redaction)
    return out


@router.get("/glance")
def glance_route(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.guide import glance

    snap = finance_snapshot(session, today())
    cov = account_cover(session, today(), saving=saving_plan(session, today(), snap=snap))
    out = glance(session, today(), snap=snap, cover=cov)
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="glance", args={}, rows=1, redaction=principal.redaction)
    return out


@router.get("/checkin")
def checkin(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    snap = finance_snapshot(session, today())
    cov = account_cover(session, today(), saving=saving_plan(session, today(), snap=snap))
    out = weekly_checkin(session, today(), snap=snap, cover=cov)
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="weekly_checkin", args={}, rows=len(out["unexpected"]), redaction=principal.redaction)
    return out


class CheckinIn(In):
    flagged: list[int] = []
    subscription: str | None = None  # keep | drop | unsure
    card_paid: bool | None = None
    saved: bool | None = None
    note: str = ""


@router.post("/checkin")
def checkin_record(body: CheckinIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.subscription not in (None, "keep", "drop", "unsure"):
        raise HTTPException(400, "subscription is keep, drop or unsure")
    if len(body.flagged) > 20:
        raise HTTPException(400, "at most twenty flagged charges")
    answers = {"flagged": body.flagged, "subscription": body.subscription, "card_paid": body.card_paid, "saved": body.saved, "note": body.note[:500]}
    return record_checkin(session, today(), answers, finance_snapshot(session, today()))


@router.get("/paychecks")
def paychecks(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.paychecks import paycheck_views

    out = paycheck_views(session, today())
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="paycheck_views", args={}, rows=len(out["windows"]) + len(out["history"]), redaction=principal.redaction)
    return out


@router.get("/bills/{bill_id}/move_check")
def move_check(bill_id: int, day: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.decisions import date_move_check

    if not 1 <= day <= 28:
        raise HTTPException(400, "day is 1 to 28")
    try:
        out = date_move_check(session, today(), bill_id, day)
    except KeyError as error:
        raise HTTPException(404, "No such bill") from error
    services.audit(session, client=principal.name, client_kind=_who(principal)[1], tool="billing_date_check", args={"bill_id": bill_id, "day": day}, rows=1, redaction=principal.redaction)
    return out


@router.get("/firstrun")
def firstrun(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return firstrun_engine.firstrun(session)


class FirstRunIn(In):
    dismissed: bool | None = None
    mark: str | None = None
    unmark: str | None = None


@router.put("/firstrun")
def firstrun_update(body: FirstRunIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    ids = {"paid", "data", "bills", "checks", "picture", None}
    if body.mark not in ids or body.unmark not in ids:
        raise HTTPException(400, "unknown step")
    return firstrun_engine.update(session, dismissed=body.dismissed, mark=body.mark, unmark=body.unmark)


@router.get("/setup/check")
def setup_check(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """Each setup step's state now, from what the app already records (M29)."""
    from ..engine import setup_check as sc

    items = sc.checks(session)
    return {"checks": items, "summary": sc.summary(items)}


@router.post("/setup/test")
def setup_test(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Try each connection for real: mailboxes sign in, the bank keys are checked, the https address is fetched."""
    from ..engine import setup_check as sc

    items = sc.checks(session)
    return {"checks": items, "summary": sc.summary(items), "tests": sc.test_everything(session)}
