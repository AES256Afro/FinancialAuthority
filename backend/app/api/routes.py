"""The v1 REST API: accounts, transactions, the snapshot, imports, keys, jobs and settings."""

from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, services
from ..config import settings
from ..connectors import CONNECTORS
from ..connectors.rocketmoney import export_csv
from ..db import get_session
from ..engine.snapshot import finance_snapshot
from ..jobs.scheduler import jobs
from ..models import Account, ApiKey, Transaction, utcnow
from ..store import DEFAULTS, get_setting, set_setting
from ..version import __version__

router = APIRouter(prefix="/api/v1", tags=["v1"])


def today() -> date:
    override = get_setting_today()
    return override or date.today()


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


@router.get("/snapshot")
def snapshot(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return finance_snapshot(session, today())


# ---- imports -------------------------------------------------------------------------------


@router.post("/imports/rocketmoney")
async def import_rocketmoney(file: UploadFile = File(...), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "That file is over 50 MB")
    text = raw.decode("utf-8-sig", errors="replace")
    result = CONNECTORS["rocketmoney"].sync(session, text=text, source_file=file.filename or "upload.csv")
    if not result.ok:
        raise HTTPException(400, result.summary)
    return {"ok": True, "summary": result.summary, "inserted": result.inserted, "duplicates": result.duplicates, "accounts_created": result.accounts}


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
    if key not in DEFAULTS or key in ("imports.processed", "selfcheck"):
        raise HTTPException(400, "That setting cannot be written here")
    if key == "buffer" and not isinstance(body.value, (int, float)):
        raise HTTPException(400, "buffer must be a number")
    set_setting(session, key, body.value)
    return {"ok": True, key: get_setting(session, key)}


@router.get("/about")
def about(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    tx = session.exec(select(Transaction.id)).all()
    return {"version": __version__, "demo": settings.demo, "transactions": len(tx), "today": today().isoformat(), "tz": settings.tz, "public_url": settings.public_url or None, "started": json.dumps(datetime.now().isoformat())}
