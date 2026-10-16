"""The v1 REST API: accounts, transactions, the snapshot, imports, keys, jobs and settings."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, services
from ..config import settings
from ..connectors import CONNECTORS
from ..connectors.rocketmoney import export_csv
from ..db import get_session
from ..engine.decisions import billing_date_suggestions, purchase_check
from ..engine.reports import debt_mountain, net_worth, spending_summary
from ..engine.snapshot import finance_snapshot
from ..engine.next_steps import next_steps
from ..engine.watchlist import watchlist
from ..jobs.scheduler import jobs
from ..models import Account, ApiKey, AuditEntry, Transaction, utcnow
from ..store import DEFAULTS, get_setting, set_setting
from ..version import __version__

router = APIRouter(prefix="/api/v1", tags=["v1"])


def today() -> date:
    override = get_setting_today()
    from ..clock import local_today

    return override or local_today()


_today_override: date | None = None


def get_setting_today() -> date | None:
    return _today_override


def set_today_override(value: date | None) -> None:
    global _today_override
    _today_override = value


# ---- accounts and transactions -------------------------------------------------------------


@router.get("/accounts")
def list_accounts(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"accounts": services.list_accounts(session)}


class AccountIn(BaseModel):
    name: str
    kind: str = "card"
    institution: str = ""
    last4: str = ""
    balance: float | None = None
    credit_limit: float | None = None


@router.post("/accounts")
def add_account(body: AccountIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """An account the connectors do not reach yet, by hand: a card another lender charges, a loan. Its balance
    is typed until a link brings it live."""
    from ..connectors.banks import upsert_account

    if body.kind not in ("checking", "savings", "cash", "card", "loan"):
        raise HTTPException(400, "kind is checking, savings, cash, card or loan")
    last4 = "".join(ch for ch in body.last4 if ch.isdigit())[-4:]
    key = f"manual:{body.name.strip().lower()[:80]}:{last4}"
    row = upsert_account(session, provider="manual", external_id=key, name=body.name.strip()[:80], institution=body.institution[:80], kind=body.kind, balance=body.balance, last4=last4, credit_limit=body.credit_limit)
    session.commit()
    return {"id": row.id, "name": row.name, "kind": row.kind, "balance": row.balance, "credit_limit": row.credit_limit}


@router.get("/transactions/search")
def search_transactions(
    text: str | None = None,
    category: str | None = None,
    account: str | None = None,
    start: date | None = None,
    end: date | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    include_non_spending: bool = False,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
    principal: auth.Principal = Depends(auth.require_read),
) -> dict:
    result = services.search_transactions(
        session, redaction=principal.redaction, text=text, category=category, account=account, start=start, end=end,
        min_amount=min_amount, max_amount=max_amount, include_non_spending=include_non_spending, limit=limit,
    )
    if principal.kind == "key":
        services.audit(session, client=principal.name, client_kind="key", tool="transactions_search", args={"text": text, "category": category}, rows=result["returned"], redaction=principal.redaction)
    return result


@router.get("/check")
def check_purchase(amount: float, on: str | None = None, installments: int | None = None, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    import math

    if not math.isfinite(amount) or amount < 0 or amount > 1_000_000:
        raise HTTPException(400, "amount must be between 0 and a million")
    if installments is not None and not 1 <= installments <= 24:
        raise HTTPException(400, "installments must be between 1 and 24")
    try:
        when = date.fromisoformat(on) if on else None
    except ValueError as error:
        raise HTTPException(400, "on must be YYYY-MM-DD") from error
    out = purchase_check(session, today(), amount, on=when, installments=installments)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="purchase_check", args={"amount": amount}, rows=1, redaction=principal.redaction)
    return out


@router.get("/bills/suggestions")
def bill_suggestions(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = billing_date_suggestions(session, today())
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="billing_dates_suggest", rows=len(out["suggestions"]), redaction=principal.redaction)
    return out


@router.get("/next_steps")
def get_next_steps(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return next_steps(session, today())


@router.get("/watchlist")
def get_watchlist(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = watchlist(session, today())
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="watchlist", rows=len(out["items"]), redaction=principal.redaction)
    return out


@router.get("/reports/spending")
def report_spending(months: int = 6, by: str = "category", session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    if by not in ("category", "merchant"):
        raise HTTPException(400, "by is category or merchant")
    out = spending_summary(session, today(), months=months, by=by, redaction=principal.redaction)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="spending_summary", args={"months": months, "by": by}, rows=len(out["groups"]), redaction=principal.redaction)
    return out


@router.get("/accounts/overview")
def accounts_overview(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = net_worth(session, today())
    if principal.redaction != "full":
        for item in out["assets"]["items"] + out["debts"]["items"]:
            item["name"] = item["kind"]
            item["institution"] = ""
    return out


class DebtIn(BaseModel):
    apr: float | None = None
    minimum: float | None = None
    statement_day: int | None = None
    due_day: int | None = None
    credit_limit: float | None = None
    pay_in_full: bool | None = None
    name: str | None = None


@router.put("/debts/{debt_id}")
def update_debt(debt_id: int, body: DebtIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """The card's terms are the owner's to type: its APR, its line, the statement and due days, whether it is
    paid in full. The balance follows the account."""
    from ..models import Debt

    debt = session.get(Debt, debt_id)
    if debt is None:
        raise HTTPException(404, "No such debt")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field in ("statement_day", "due_day"):
            value = max(1, min(31, int(value)))
        if field in ("apr", "minimum", "credit_limit"):
            value = max(0.0, float(value))
        setattr(debt, field, value)
    session.add(debt)
    session.commit()
    from ..llm.reviewer import mark_pending

    mark_pending(session, f"debt terms changed: {debt.name}")
    return debt_mountain(session, today())


@router.get("/debts")
def debts_overview(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    out = debt_mountain(session, today())
    if principal.redaction != "full":
        for item in out["items"]:
            item["name"] = item["kind"]
        out["order"] = {"avalanche": [], "snowball": []}
    return out


@router.get("/audit")
def audit_log(limit: int = 200, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    limit = max(1, min(int(limit), 1000))
    rows = session.exec(select(AuditEntry).order_by(AuditEntry.at.desc()).limit(limit)).all()
    week_ago = utcnow() - timedelta(days=7)
    recent = session.exec(select(AuditEntry).where(AuditEntry.at >= week_ago)).all()
    by_tool: dict[str, int] = {}
    by_client: dict[str, int] = {}
    for r in recent:
        by_tool[r.tool] = by_tool.get(r.tool, 0) + 1
        by_client[r.client] = by_client.get(r.client, 0) + 1
    return {
        "entries": [{"id": r.id, "at": r.at.isoformat(), "client": r.client, "client_kind": r.client_kind, "tool": r.tool, "rows": r.rows, "redaction": r.redaction} for r in rows],
        "week": {"calls": len(recent), "by_tool": by_tool, "by_client": by_client},
    }


@router.get("/snapshot")
def snapshot(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return finance_snapshot(session, today())


# ---- imports -------------------------------------------------------------------------------


@router.post("/imports/rocketmoney")
def import_rocketmoney(file: UploadFile = File(...), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    raw = file.file.read(50 * 1024 * 1024 + 1)
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "That file is over 50 MB")
    text = raw.decode("utf-8-sig", errors="replace")
    result = CONNECTORS["rocketmoney"].sync(session, text=text, source_file=file.filename or "upload.csv")
    if not result.ok:
        raise HTTPException(400, result.summary)
    from ..jobs.detect import run_detection

    detection = run_detection(session, today()) if result.inserted else {}
    return {"ok": True, "summary": result.summary, "inserted": result.inserted, "duplicates": result.duplicates, "accounts_created": result.accounts, "detection": detection,
            "import_id": result.details.get("import_id")}


@router.get("/exports/rocketmoney.csv", response_class=PlainTextResponse)
def export_rocketmoney(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> str:
    return export_csv(session)


@router.get("/connectors")
def list_connectors(_: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"connectors": [{"id": c.id, "title": c.title, "yields": c.yields, "settings": c.settings_schema} for c in CONNECTORS.values()]}


# ---- keys ----------------------------------------------------------------------------------


class KeyIn(BaseModel):
    name: str
    scopes: list[str] = ["read"]
    redaction: str = "full"


@router.get("/keys")
def list_keys(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    rows = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return {"keys": [
        {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": sorted(k.scope_set), "redaction": k.redaction, "created_at": k.created_at.isoformat(),
         "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None, "revoked": k.revoked_at is not None}
        for k in rows
    ]}


@router.post("/keys")
def create_key(body: KeyIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.redaction not in ("full", "merchants", "aggregates"):
        raise HTTPException(400, "redaction must be full, merchants or aggregates")
    try:
        key, token = auth.create_key(session, body.name, set(body.scopes), body.redaction)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"id": key.id, "name": key.name, "prefix": key.prefix, "scopes": sorted(key.scope_set), "token": token, "note": "Shown once. Keep it in the client, not in chat."}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "No such key")
    key.revoked_at = utcnow()
    session.add(key)
    session.commit()
    return {"ok": True}


@router.get("/oauth/clients")
def list_oauth_clients(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..models import OAuthClient, OAuthToken

    rows = session.exec(select(OAuthClient).order_by(OAuthClient.created_at.desc())).all()
    live = {}
    for t in session.exec(select(OAuthToken).where(OAuthToken.revoked_at == None, OAuthToken.kind == "refresh")).all():  # noqa: E711
        live[t.client_id] = live.get(t.client_id, 0) + 1
    return {"clients": [
        {"id": c.id, "client_id": c.client_id, "name": c.name, "scopes": c.scopes, "redaction": c.redaction, "created_at": c.created_at.isoformat(),
         "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None, "revoked": c.revoked_at is not None, "sessions": live.get(c.client_id, 0)}
        for c in rows
    ]}


@router.delete("/oauth/clients/{client_id}")
def revoke_oauth_client(client_id: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..oauth import revoke_client

    return {"ok": True, "tokens_revoked": revoke_client(session, client_id)}


class ClientRedactionIn(BaseModel):
    redaction: str


@router.put("/oauth/clients/{client_id}")
def set_oauth_client_redaction(client_id: str, body: ClientRedactionIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..models import OAuthClient

    if body.redaction not in ("full", "merchants", "aggregates"):
        raise HTTPException(400, "redaction must be full, merchants or aggregates")
    row = session.exec(select(OAuthClient).where(OAuthClient.client_id == client_id)).first()
    if row is None:
        raise HTTPException(404, "No such client")
    row.redaction = body.redaction
    session.add(row)
    session.commit()
    return {"ok": True}


# ---- jobs ----------------------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(_: auth.Principal = Depends(auth.require_read)) -> dict:
    runs = jobs.recent()
    return {
        "jobs": [{"name": s.name, "every_seconds": s.seconds, "description": s.description} for s in jobs.specs.values()],
        "runs": [{"id": r.id, "name": r.name, "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None, "ok": r.ok, "summary": r.summary} for r in runs],
        "scheduler": "running" if jobs.scheduler else "stopped",
    }


@router.post("/jobs/{name}/run")
def run_job(name: str, _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if name not in jobs.specs:
        raise HTTPException(404, "No such job")
    run = jobs.run(name)
    return {"id": run.id, "name": run.name, "ok": run.ok, "summary": run.summary}


# ---- settings ------------------------------------------------------------------------------


@router.get("/settings")
def read_settings(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {key: get_setting(session, key) for key in ("buffer", "appearance", "selfcheck")}


class SettingIn(BaseModel):
    value: object


@router.put("/settings/{key}")
def write_setting(key: str, body: SettingIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if key not in DEFAULTS or key in ("imports.processed", "selfcheck", "appearance"):
        raise HTTPException(400, "That setting cannot be written here; appearance is saved on Settings › Appearance, which checks it")
    if key == "buffer" and not isinstance(body.value, (int, float)):
        raise HTTPException(400, "buffer must be a number")
    set_setting(session, key, body.value)
    return {"ok": True, key: get_setting(session, key)}


@router.get("/about")
def about(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from sqlalchemy import func

    count = session.exec(select(func.count()).select_from(Transaction)).one()
    return {"version": __version__, "demo": settings.demo, "transactions": int(count), "today": today().isoformat(), "tz": settings.tz, "public_url": settings.public_url or None, "started": json.dumps(datetime.now().isoformat())}
