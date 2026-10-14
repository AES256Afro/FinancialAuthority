"""Files from any bank or vendor: a look at what a file holds, then the import; holdings into an investment account."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session

from .. import auth
from ..connectors.files import detect, import_holdings, import_rows, parse_csv_rows, parse_ofx, sniff_holdings_csv
from ..db import get_session
from ..models import Account
from .routes import today

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])
LIMIT = 50 * 1024 * 1024


async def _text(file: UploadFile) -> str:
    raw = await file.read()
    if len(raw) > LIMIT:
        raise HTTPException(413, "That file is over 50 MB")
    return raw.decode("utf-8-sig", errors="replace")


def _serial(value):  # noqa: ANN001, ANN202
    if isinstance(value, dict):
        return {k: _serial(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serial(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


@router.post("/preview")
async def preview(file: UploadFile = File(...), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """What the file is, which column is which, and the first rows as they would land. Nothing is written."""
    text = await _text(file)
    found = detect(text)
    return _serial({**found, "filename": file.filename or "upload", "rows": None, "positions": found.get("positions", [])[:5] if found["kind"] == "ofx" else None})


@router.post("/file")
async def import_file(
    file: UploadFile = File(...), account_name: str = Form(""), institution: str = Form(""), kind: str = Form("checking"), sign: str = Form(""), mapping: str = Form(""),
    session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin),
) -> dict:
    text = await _text(file)
    found = detect(text)
    if found["kind"] == "ofx":
        parsed = parse_ofx(text)
        rows = parsed["rows"]
        name = account_name.strip() or (f"{parsed['vendor']} {parsed['account_type'] or 'account'}".strip() if parsed["vendor"] else f"Account {parsed['account_id'][-4:]}" if parsed["account_id"] else "Imported")
        vendor = institution.strip() or parsed["vendor"]
    else:
        try:
            overrides = json.loads(mapping) if mapping else {}
        except json.JSONDecodeError as error:
            raise HTTPException(400, "mapping must be JSON") from error
        rows = parse_csv_rows(text, overrides, sign or None)
        name = account_name.strip() or "Imported"
        vendor = institution.strip()
    if not rows:
        raise HTTPException(400, "No rows with a date and an amount were found; check the columns in the preview")
    result = import_rows(session, rows, source_file=file.filename or "upload", account_name=name, institution=vendor, kind=kind)
    from ..jobs.detect import run_detection

    detection = run_detection(session, today()) if result.inserted else {}
    return {"ok": True, "summary": result.summary, "inserted": result.inserted, "duplicates": result.duplicates, "accounts_created": result.accounts, "account_id": result.details.get("account_id"), "detection": detection}


@router.post("/holdings/{account_id}")
async def import_holdings_file(account_id: int, file: UploadFile = File(...), as_of: str = Form(""), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.kind != "investment":
        raise HTTPException(404, "No such investment account")
    text = await _text(file)
    found = detect(text)
    positions = parse_ofx(text)["positions"] if found["kind"] == "ofx" else sniff_holdings_csv(text)["positions"]
    if not positions:
        raise HTTPException(400, "No holdings with a name and a value were found")
    when = None
    if as_of:
        try:
            when = date.fromisoformat(as_of)
        except ValueError as error:
            raise HTTPException(400, "as_of must be YYYY-MM-DD") from error
    result = import_holdings(session, account, positions, as_of=when)
    return {"ok": True, "summary": result.summary, "holdings": result.inserted, "total": result.details["total"]}
