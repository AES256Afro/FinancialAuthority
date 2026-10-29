"""Investments and retirement accounts: the overview, accounts added by hand with their balances kept over
time, and the vendors a sign-in through Plaid reaches."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth
from ..connectors.banks import mark_removed, record_balance
from ..db import get_session
from ..engine.investments import investments_overview
from ..llm.reviewer import mark_pending
from ..models import Account, Holding
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1/investments", tags=["investments"])

SUBTYPES = ["401k", "403b", "457b", "ira", "roth_ira", "sep_ira", "simple_ira", "tsp", "pension", "brokerage", "hsa", "529", "crypto", "stock_plan", "other"]

# Where each kind of vendor is reached. Plaid's investments product covers the large ones through the
# vendor's own sign-in (OAuth where the vendor offers it); the rest are kept by hand or from statements.
VENDORS = [
    {"name": "Fidelity (NetBenefits, brokerage)", "via": "plaid"}, {"name": "Vanguard", "via": "plaid"}, {"name": "Charles Schwab", "via": "plaid"},
    {"name": "Empower (retirement)", "via": "plaid"}, {"name": "Principal", "via": "plaid"}, {"name": "Voya", "via": "plaid"}, {"name": "TIAA", "via": "plaid"},
    {"name": "T. Rowe Price", "via": "plaid"}, {"name": "John Hancock", "via": "plaid"}, {"name": "Transamerica", "via": "plaid"}, {"name": "ADP retirement", "via": "plaid"},
    {"name": "Merrill", "via": "plaid"}, {"name": "E*TRADE / Morgan Stanley", "via": "plaid"}, {"name": "Robinhood", "via": "plaid"}, {"name": "Betterment", "via": "plaid"},
    {"name": "Wealthfront", "via": "plaid"}, {"name": "Coinbase", "via": "plaid"}, {"name": "Thrift Savings Plan", "via": "manual"}, {"name": "A pension", "via": "manual"},
    {"name": "Anything else", "via": "manual"},
]


class InvestmentIn(BaseModel):
    name: str | None = None
    institution: str | None = None
    subtype: str | None = None
    balance: float | None = None
    as_of: str | None = None


class HoldingIn(BaseModel):
    name: str
    ticker: str = ""
    kind: str = "other"
    quantity: float | None = None
    price: float | None = None
    value: float


def _when(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
    except ValueError as error:
        raise HTTPException(400, "as_of must be YYYY-MM-DD") from error


def overview(session: Session) -> dict:
    return {**investments_overview(session, today()), "subtypes": SUBTYPES, "vendors": VENDORS}


@router.get("")
def get_overview(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return overview(session)


@router.post("")
@under_writer
def add_account(body: InvestmentIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if not body.name or not body.name.strip():
        raise HTTPException(400, "An account needs a name")
    if body.subtype and body.subtype not in SUBTYPES:
        raise HTTPException(400, f"subtype is one of {', '.join(SUBTYPES)}")
    when = _when(body.as_of)
    key = f"manual:{body.name.strip().lower()}"
    if session.exec(select(Account).where(Account.connector == "manual", Account.kind == "investment", Account.external_id == key, Account.active == True)).first() is not None:  # noqa: E712
        raise HTTPException(409, "An investment account with that name is already on file; change its balance there, or give this one another name")
    account = Account(name=body.name.strip(), institution=(body.institution or "").strip(), kind="investment", subtype=body.subtype or "other", connector="manual", external_id=key)
    if body.balance is not None:
        account.balance, account.balance_at = round(float(body.balance), 2), when
    session.add(account)
    session.commit()
    session.refresh(account)
    if body.balance is not None:
        record_balance(session, account, float(body.balance), when.date())
    mark_pending(session, f"investment added: {account.name}")
    return overview(session)


@router.put("/{account_id}")
@under_writer
def update_account(account_id: int, body: InvestmentIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.kind != "investment":
        raise HTTPException(404, "No such investment account")
    if body.name:
        account.name = body.name.strip()
    if body.institution is not None:
        account.institution = body.institution.strip()
    if body.subtype:
        if body.subtype not in SUBTYPES:
            raise HTTPException(400, f"subtype is one of {', '.join(SUBTYPES)}")
        account.subtype = body.subtype
    if body.balance is not None:
        when = _when(body.as_of)
        account.balance, account.balance_at = round(float(body.balance), 2), when
        session.add(account)
        session.commit()
        record_balance(session, account, float(body.balance), when.date())
    session.add(account)
    session.commit()
    return overview(session)


@router.post("/{account_id}/holdings")
@under_writer
def set_holding(account_id: int, body: HoldingIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.kind != "investment":
        raise HTTPException(404, "No such investment account")
    key = f"manual:{(body.ticker or body.name).strip().lower()}"
    if body.value < 0 or (body.quantity is not None and body.quantity < 0) or (body.price is not None and body.price < 0):
        raise HTTPException(400, "A holding's value, quantity and price are 0 or more")
    row = session.exec(select(Holding).where(Holding.account_id == account.id, Holding.external_id == key)).first()
    if row is None:
        row = Holding(account_id=account.id, external_id=key)
    row.name, row.ticker, row.kind = body.name.strip(), body.ticker.strip().upper(), body.kind
    row.quantity, row.price, row.value = body.quantity, body.price, round(float(body.value), 2)
    row.as_of = row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return overview(session)


@router.delete("/{account_id}/holdings/{holding_id}")
@under_writer
def remove_holding(account_id: int, holding_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    row = session.get(Holding, holding_id)
    if row is None or row.account_id != account_id:
        raise HTTPException(404, "No such holding")
    session.delete(row)
    session.commit()
    return overview(session)


@router.delete("/{account_id}")
@under_writer
def remove_account(account_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.kind != "investment":
        raise HTTPException(404, "No such investment account")
    account.active = False
    session.add(account)
    mark_removed(session, account.id)  # a bank link that still lists it (an old 401k on the same sign-in) leaves it removed; this commits
    return overview(session)
