"""Files from any bank or vendor: a look at what a file holds, then the import; holdings into an investment account."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session

from .. import auth
from ..connectors.files import detect, import_bills, import_holdings, import_rows, parse_bills_rows, parse_csv_rows, parse_ofx, sniff_holdings_csv
from ..db import get_session
from ..engine.paystubs import paystub_fact_from_file
from ..models import Account
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])
LIMIT = 50 * 1024 * 1024


def _raw(file: UploadFile) -> bytes:
    raw = file.file.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise HTTPException(413, "That file is over 50 MB")
    return raw


def _text(file: UploadFile) -> str:
    return _raw(file).decode("utf-8-sig", errors="replace")


def _statement(raw: bytes, kind: str) -> dict:
    """A statement PDF's transaction lines, read from its text layer."""
    from ..parsers.paystub import pdf_text
    from ..parsers.statement import parse_statement_text

    text = pdf_text(raw)
    if not text.strip():
        raise HTTPException(400, "That PDF has no text the app can read (a scan or a photo). Download the statement from the bank's website instead, or a CSV of the same months.")
    return parse_statement_text(text, today(), card=kind == "card")


def _serial(value):  # noqa: ANN001, ANN202
    if isinstance(value, dict):
        return {k: _serial(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serial(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


@router.post("/preview")
def preview(file: UploadFile = File(...), kind: str = Form("checking"), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """What the file is, which column is which, and the first rows as they would land. Nothing is written."""
    raw = _raw(file)
    if raw.startswith(b"%PDF"):
        from ..parsers.affirm_statement import looks_like_affirm_statement
        from ..parsers.paystub import pdf_text

        if looks_like_affirm_statement(pdf_text(raw)):
            return {"kind": "affirm", "affirm": True, "filename": file.filename or "statement.pdf", "rows": None, "sample": [],
                    "note": "This is an Affirm statement: read it on Borrowing, where its loans and payments are kept."}
        found = _statement(raw, kind)
        return _serial({**found, "filename": file.filename or "statement.pdf", "sample": found["rows"][:12], "rows": None})
    text = raw.decode("utf-8-sig", errors="replace")
    found = _detect(text)
    return _serial({**found, "filename": file.filename or "upload", "rows": None, "positions": found.get("positions", [])[:5] if found["kind"] == "ofx" else None})


def _detect(text: str) -> dict:
    try:
        return detect(text)
    except ValueError as error:  # a CSV the reader refuses (a field past its limit) is a bad file, not a crash
        raise HTTPException(400, str(error)) from error


@router.post("/paystub")
@under_writer
def import_paystub(file: UploadFile = File(...), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """A paystub PDF handed over by hand: read, then queued on Needs you to confirm with one tap."""
    raw = file.file.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise HTTPException(413, "That file is over 50 MB")
    fact = paystub_fact_from_file(session, raw, file.filename or "paystub.pdf", today(), "upload")
    if fact is None:
        raise HTTPException(400, "That file does not read like a paystub. The reader looks for a pay date and a net pay line; a scanned image needs a text layer.")
    fields = json.loads(fact.fields)
    return {"ok": True, "fact_id": fact.id, "confidence": fact.confidence, "net": fields.get("net"), "gross": fields.get("gross"), "pay_date": fields.get("pay_date"), "note": "On Needs you to confirm."}


@router.post("/file")
def import_file(
    file: UploadFile = File(...), account_name: str = Form(""), institution: str = Form(""), kind: str = Form("checking"), sign: str = Form(""), mapping: str = Form(""),
    account_id: int | None = Form(None), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin),
) -> dict:
    raw = _raw(file)
    if raw.startswith(b"%PDF"):
        found, text = _statement(raw, kind), ""
    else:
        text = raw.decode("utf-8-sig", errors="replace")
        found = _detect(text)
    if found["kind"] == "statement":
        rows = found["rows"]
        name = account_name.strip() or "Imported statement"
        vendor = institution.strip()
        number = ""
    elif found["kind"] == "bills":
        bill_rows = parse_bills_rows(text, found["mapping"])
        if not bill_rows:
            raise HTTPException(400, "No rows with a name and an amount were found; check the columns in the preview")
        landed = import_bills(session, bill_rows, source_file=file.filename or "upload", today=today())
        return {"ok": True, "summary": landed.summary, "inserted": landed.inserted, "duplicates": 0, "accounts_created": 0, "account_id": None, "bills": landed.details, "detection": {},
                "import_id": landed.details.get("import_id")}
    if found["kind"] == "statement":
        pass
    elif found["kind"] == "ofx":
        parsed = parse_ofx(text)
        rows = parsed["rows"]
        name = account_name.strip() or (f"{parsed['vendor']} {parsed['account_type'] or 'account'}".strip() if parsed["vendor"] else f"Account {parsed['account_id'][-4:]}" if parsed["account_id"] else "Imported")
        vendor = institution.strip() or parsed["vendor"]
        number = parsed["account_id"]
    else:
        try:
            overrides = json.loads(mapping) if mapping else {}
            if not isinstance(overrides, dict) or not all(isinstance(v, (str, type(None))) for v in overrides.values()):
                raise ValueError("not an object of column names")
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(400, "mapping must be a JSON object of column names") from error
        rows = parse_csv_rows(text, overrides, sign or None)
        name = account_name.strip() or "Imported"
        vendor = institution.strip()
        number = ""
    if not rows:
        raise HTTPException(400, "No rows with a date and an amount were found; check the columns in the preview")
    try:
        result = import_rows(session, rows, source_file=file.filename or "upload", account_name=name, institution=vendor, kind=kind, account_id=account_id, account_number=number)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if found["kind"] == "ofx":
        from .. import services

        # The statement's own balance: an owner with files only then gets a real safe to spend, not "no balance yet".
        services.apply_statement_balances(session, text, (result.details or {}).get("account_id"))
    from ..jobs.detect import run_detection

    if raw.startswith(b"%PDF") and result.details.get("account_id") and result.inserted and result.details.get("import_id"):
        # The statement itself is kept with its account and its month, once: an import that added nothing keeps
        # nothing, and the same file already kept with the account is not kept again. Undoing the import removes it.
        import hashlib

        from sqlmodel import select

        from ..engine.documents import save_document
        from ..models import Document

        attach = f"account:{result.details['account_id']}"
        if session.exec(select(Document).where(Document.sha256 == hashlib.sha256(raw).hexdigest(), Document.attach_to == attach)).first() is None:
            last = max((r["date"] for r in rows if r.get("date")), default=None)
            month = (last.isoformat() if hasattr(last, "isoformat") else str(last or today()))[:7]
            try:
                save_document(session, raw, filename=file.filename or "statement.pdf", title=f"Statement · {name} · {month}", kind="statement",
                              attach_to=attach, period=month, source=f"import:{result.details['import_id']}")
            except ValueError:
                pass
    detection = run_detection(session, today()) if result.inserted else {}
    return {"ok": True, "summary": result.summary, "inserted": result.inserted, "duplicates": result.duplicates, "merged": result.details.get("merged", 0), "accounts_created": result.accounts,
            "account_id": result.details.get("account_id"), "import_id": result.details.get("import_id"), "detection": detection}


@router.post("/holdings/{account_id}")
@under_writer
def import_holdings_file(account_id: int, file: UploadFile = File(...), as_of: str = Form(""), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(Account, account_id)
    if account is None or account.kind != "investment":
        raise HTTPException(404, "No such investment account")
    text = _text(file)
    found = _detect(text)
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


@router.get("/history")
def import_history(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Every import, newest first, with what it did and whether it can still be undone."""
    from .. import importlog

    return {"imports": importlog.history(session)}


@router.post("/{import_id}/undo")
def undo_import(import_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Remove exactly what one import added and put back what it changed; everything since stays."""
    from .. import importlog, services

    try:
        done = importlog.undo(session, import_id, today())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="import_undo", args={"id": import_id}, rows=done["removed"])
    return {**done, "imports": importlog.history(session)}
