"""Live bank links: Plaid and Teller settings, the connect flows, sync and removal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, crypto
from ..config import settings
from ..connectors import CONNECTORS
from ..connectors.banks import bank_state, save_bank_settings
from ..connectors.plaid import PlaidConnector, PlaidError
from ..connectors.teller import TellerConnector
from ..db import get_session
from ..models import Account, BankLink
from ..store import get_setting, set_setting

router = APIRouter(prefix="/api/v1/banks", tags=["banks"])


class PlaidSettingsIn(BaseModel):
    client_id: str | None = None
    secret: str | None = None
    environment: str | None = None


class TellerSettingsIn(BaseModel):
    application_id: str | None = None
    certificate: str | None = None
    private_key: str | None = None
    environment: str | None = None


class LinkTokenIn(BaseModel):
    link_id: int | None = None
    purpose: str = "bank"  # bank | investments


class ExchangeIn(BaseModel):
    public_token: str
    institution: str = ""


class EnrollIn(BaseModel):
    access_token: str
    enrollment_id: str
    institution: str = ""


def detect_after(session: Session, result) -> dict:  # noqa: ANN001
    """New rows may complete a recurring series: run detection now, so a freshly linked bank shows its
    bills and paychecks on the next screen instead of after the six-hourly job."""
    if not result.inserted:
        return {}
    from ..engine.borrowing import sync_borrowing
    from ..jobs.detect import run_detection
    from .routes import today

    out = run_detection(session, today())
    sync_borrowing(session, today())
    return out


def plaid() -> PlaidConnector:
    return CONNECTORS["plaid"]  # type: ignore[return-value]


def teller() -> TellerConnector:
    return CONNECTORS["teller"]  # type: ignore[return-value]


def link_out(session: Session, link: BankLink) -> dict:
    ids = [i for i in (link.accounts or "").split(",") if i]
    accounts = session.exec(select(Account).where(Account.connector == link.provider, Account.external_id.in_(ids))).all() if ids else []  # type: ignore[attr-defined]
    return {
        "id": link.id, "provider": link.provider, "institution": link.institution, "active": link.active, "needs_login": link.needs_login,
        "created_at": link.created_at.isoformat(), "last_sync_at": link.last_sync_at.isoformat() if link.last_sync_at else None, "last_error": link.last_error,
        "accounts": [{"id": a.id, "name": a.name, "kind": a.kind, "balance": a.balance} for a in accounts],
    }


def overview(session: Session) -> dict:
    links = session.exec(select(BankLink).where(BankLink.active == True).order_by(BankLink.created_at)).all()  # noqa: E712
    return {
        "providers": {"plaid": {**bank_state(session, "plaid"), "keys": (get_setting(session, "banks.plaid", {}) or {}).get("keys")}, "teller": bank_state(session, "teller")},
        "links": [link_out(session, l) for l in links],
        "can_store_secrets": bool(settings.data_key),
        "public_url": settings.public_url,
    }


@router.get("")
def get_banks(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return overview(session)


@router.put("/plaid/settings")
def put_plaid(body: PlaidSettingsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.environment and body.environment not in ("sandbox", "production"):
        raise HTTPException(400, "environment is sandbox or production")
    try:
        save_bank_settings(session, "plaid", body.model_dump(exclude_unset=True))
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    api = plaid().api(session)
    if api is not None:
        record_key_check(session, api)
    return overview(session)


def record_key_check(session: Session, api) -> dict[str, str]:  # noqa: ANN001
    """Try the keys once and keep the verdict where the page and the next steps can show it."""
    verdict = api.check_keys()
    set_setting(session, "banks.plaid", {**(get_setting(session, "banks.plaid", {}) or {}), "keys": {**verdict, "environment": api.environment}})
    return verdict


@router.put("/teller/settings")
def put_teller(body: TellerSettingsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.environment and body.environment not in ("sandbox", "development", "production"):
        raise HTTPException(400, "environment is sandbox, development or production")
    try:
        save_bank_settings(session, "teller", body.model_dump(exclude_unset=True))
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    return overview(session)


@router.post("/plaid/link_token")
def plaid_link_token(body: LinkTokenIn | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    api = plaid().api(session)
    if api is None:
        raise HTTPException(409, "Plaid is not configured: paste the client id and secret first")
    access_token = None
    if body and body.link_id:
        link = session.get(BankLink, body.link_id)
        if link is None or link.provider != "plaid" or not link.active:
            raise HTTPException(404, "No such link")
        from ..connectors.banks import link_token as token_of

        access_token = token_of(link)
    purpose = body.purpose if body and body.purpose in ("bank", "investments") else "bank"
    uri = api.redirect_uri(settings.public_url)
    oauth = {"ok": uri is not None, "uri": uri}
    try:
        token = api.link_token(public_url=settings.public_url, access_token=access_token, purpose=purpose)
        state = get_setting(session, "banks.plaid", {}) or {}
        state["keys"] = {"result": "ok", "message": f"Plaid accepts the keys for {api.environment}.", "environment": api.environment}
        if uri:
            state.update(redirect_ok=True, redirect_uri=uri)
        set_setting(session, "banks.plaid", state)
    except PlaidError as error:
        if uri and "redirect uri" in str(error).lower():
            # The URI is not registered in the Plaid dashboard yet. Banks that sign in without OAuth can still
            # connect, so the token is made without it, and the next steps say what to register.
            set_setting(session, "banks.plaid", {**(get_setting(session, "banks.plaid", {}) or {}), "redirect_ok": False, "redirect_uri": uri})
            oauth = {"ok": False, "uri": uri, "note": f"Plaid does not know {uri} yet. Register it in the Plaid dashboard (Team Settings, API, Allowed redirect URIs) and banks that sign in with OAuth, Capital One among them, can connect; until then only banks without OAuth can."}
            try:
                token = api.link_token(public_url=settings.public_url, access_token=access_token, purpose=purpose, redirect=False)
            except PlaidError as again:
                raise HTTPException(502, str(again)) from again
        elif getattr(error, "code", "") == "INVALID_API_KEYS":
            raise HTTPException(502, record_key_check(session, api)["message"]) from error
        else:
            raise HTTPException(502, str(error)) from error
    return {"link_token": token, "environment": bank_state(session, "plaid")["environment"], "oauth_redirect": oauth}


@router.post("/plaid/exchange")
def plaid_exchange(body: ExchangeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    api = plaid().api(session)
    if api is None:
        raise HTTPException(409, "Plaid is not configured")
    try:
        link = plaid().connect(session, api, body.public_token, body.institution)
    except PlaidError as error:
        raise HTTPException(502, str(error)) from error
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    result = plaid().sync(session, link_id=link.id)
    detection = detect_after(session, result)
    session.refresh(link)
    return {"link": link_out(session, link), "sync": {"ok": result.ok, "summary": result.summary, "inserted": result.inserted}, "detection": detection}


@router.post("/teller/enroll")
def teller_enroll(body: EnrollIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if teller().api(session) is None:
        raise HTTPException(409, "Teller is not configured: paste the application id first")
    try:
        link = teller().connect(session, body.access_token, body.enrollment_id, body.institution)
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    result = teller().sync(session, link_id=link.id)
    detection = detect_after(session, result)
    session.refresh(link)
    return {"link": link_out(session, link), "sync": {"ok": result.ok, "summary": result.summary, "inserted": result.inserted}, "detection": detection}


@router.post("/{link_id}/sync")
def sync_link(link_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    link = session.get(BankLink, link_id)
    if link is None or not link.active:
        raise HTTPException(404, "No such link")
    result = CONNECTORS[link.provider].sync(session, link_id=link.id)
    detection = detect_after(session, result)
    session.refresh(link)
    return {"ok": result.ok, "summary": result.summary, "inserted": result.inserted, "details": result.details, "link": link_out(session, link), "detection": detection}


@router.delete("/{link_id}")
def remove_link(link_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    link = session.get(BankLink, link_id)
    if link is None:
        raise HTTPException(404, "No such link")
    if link.provider == "plaid":
        plaid().disconnect(session, plaid().api(session), link)
    else:
        teller().disconnect(session, link)
    return {"ok": True}



SYNC_CHOICES = (1, 2, 4, 6, 12, 24)
TRIAL_ITEMS = 10


@router.get("/freshness")
def freshness(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """How fresh the bank data is, how often the app reads it, every call it has made, and what that costs."""
    from datetime import UTC, datetime, timedelta

    from .. import usage
    from ..models import Transaction
    from ..store import get_setting
    from .routes import today

    conf = {"sync_every_hours": 4, "price_per_item": None, **(get_setting(session, "banks.freshness", {}) or {})}
    every = timedelta(hours=float(conf["sync_every_hours"]))
    links = session.exec(select(BankLink)).all()
    out_links = []
    for link in links:
        if not link.active:
            continue
        mine = [x for x in (link.accounts or "").split(",") if x]
        query = select(Account.id).where(Account.connector == link.provider)
        account_ids = list(session.exec(query.where(Account.external_id.in_(mine)) if mine else query).all())
        newest = session.exec(select(Transaction.date).where(Transaction.account_id.in_(account_ids)).order_by(Transaction.date.desc()).limit(1)).first() if account_ids else None
        last = link.last_sync_at.replace(tzinfo=UTC) if link.last_sync_at and not link.last_sync_at.tzinfo else link.last_sync_at
        out_links.append({"id": link.id, "provider": link.provider, "institution": link.institution, "last_sync_at": last.isoformat() if last else None,
                          "next_sync_at": (last + every).isoformat() if last else datetime.now(UTC).isoformat(), "newest_transaction": newest.isoformat() if newest else None,
                          "error": link.last_error or None, "needs_login": link.needs_login})
    environment = bank_state(session, "plaid")["environment"]
    production_items = [link for link in links if link.provider == "plaid" and (link.environment or environment) == "production"]
    active_plaid = [link for link in production_items if link.active]
    price = conf.get("price_per_item")
    monthly = round(len(active_plaid) * float(price), 2) if price not in (None, "") else None
    return {
        "sync_every_hours": conf["sync_every_hours"], "choices": list(SYNC_CHOICES), "links": out_links,
        "plaid": {"environment": environment, "items_made": len(production_items), "items_active": len(active_plaid), "trial_limit": TRIAL_ITEMS},
        "calls": usage.calls(session, today()),
        "cost": {"price_per_item": price, "monthly": monthly, "yearly": round(monthly * 12, 2) if monthly is not None else None,
                 "plan_note": "On Plaid's free Trial plan this costs nothing, for up to ten bank logins over the life of your account. On a paid plan, Plaid bills transactions as a flat monthly fee per bank login, however often the app reads it. The app never uses Plaid's paid on-demand refresh or real-time balance calls. Plaid shows your actual bill in its dashboard under Billing; it offers apps no way to read it.",
                 "teller_note": "Teller's development environment is free for up to a hundred bank logins."},
        "chain": "Your bank posts a purchase, often within minutes as pending. Plaid collects new transactions from the bank on its own schedule, usually a few times a day. The app then reads Plaid at the interval set here, or at once with Sync now. A bank that emails purchase alerts reaches the app sooner: mailboxes are read every fifteen minutes.",
    }


class FreshnessIn(BaseModel):
    sync_every_hours: int | None = None
    price_per_item: float | None = None
    clear_price: bool = False


@router.put("/freshness")
def set_freshness(body: FreshnessIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..store import get_setting, set_setting

    conf = {"sync_every_hours": 4, "price_per_item": None, **(get_setting(session, "banks.freshness", {}) or {})}
    if body.sync_every_hours is not None:
        if body.sync_every_hours not in SYNC_CHOICES:
            raise HTTPException(400, f"Choose one of {', '.join(str(c) for c in SYNC_CHOICES)} hours")
        conf["sync_every_hours"] = body.sync_every_hours
    if body.clear_price:
        conf["price_per_item"] = None
    elif body.price_per_item is not None:
        if not 0 <= body.price_per_item <= 100:
            raise HTTPException(400, "A price per bank login a month between 0 and 100 dollars")
        conf["price_per_item"] = round(float(body.price_per_item), 4)
    set_setting(session, "banks.freshness", conf)
    return freshness(session, principal)
