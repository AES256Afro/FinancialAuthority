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
        "providers": {"plaid": bank_state(session, "plaid"), "teller": bank_state(session, "teller")},
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
    return overview(session)


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
        if uri:
            set_setting(session, "banks.plaid", {**(get_setting(session, "banks.plaid", {}) or {}), "redirect_ok": True, "redirect_uri": uri})
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
    session.refresh(link)
    return {"link": link_out(session, link), "sync": {"ok": result.ok, "summary": result.summary, "inserted": result.inserted}}


@router.post("/teller/enroll")
def teller_enroll(body: EnrollIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if teller().api(session) is None:
        raise HTTPException(409, "Teller is not configured: paste the application id first")
    try:
        link = teller().connect(session, body.access_token, body.enrollment_id, body.institution)
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    result = teller().sync(session, link_id=link.id)
    session.refresh(link)
    return {"link": link_out(session, link), "sync": {"ok": result.ok, "summary": result.summary, "inserted": result.inserted}}


@router.post("/{link_id}/sync")
def sync_link(link_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    link = session.get(BankLink, link_id)
    if link is None or not link.active:
        raise HTTPException(404, "No such link")
    result = CONNECTORS[link.provider].sync(session, link_id=link.id)
    session.refresh(link)
    return {"ok": result.ok, "summary": result.summary, "inserted": result.inserted, "details": result.details, "link": link_out(session, link)}


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
