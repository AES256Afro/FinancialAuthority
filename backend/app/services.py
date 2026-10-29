"""Service functions shared by the REST routes and the MCP tools, so both doors answer identically."""

from __future__ import annotations

import hashlib
import logging
import hmac
import json
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, select

from .models import Account, AuditEntry, Transaction


def list_accounts(session: Session) -> list[dict]:
    rows = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    return [
        {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance,
         "balance_at": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector}
        for a in rows
    ]


def search_transactions(
    session: Session,
    *,
    redaction: str = "full",
    text: str | None = None,
    category: str | None = None,
    account: str | None = None,
    start: date | None = None,
    end: date | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    include_non_spending: bool = False,
    limit: int = 50,
    tag: str | None = None,
    offset: int = 0,
) -> dict:
    limit = max(1, min(int(limit), 500))
    offset = max(0, min(int(offset), 10_000_000))
    query = select(Transaction, Account).join(Account, Account.id == Transaction.account_id)
    if text:
        like = f"%{text}%"
        query = query.where((Transaction.merchant.ilike(like)) | (Transaction.raw_name.ilike(like)) | (Transaction.description.ilike(like)) | (Transaction.note.ilike(like)))
    if category:
        query = query.where(Transaction.category == category)
    if tag:
        word = tag.strip().lower()
        query = query.where((Transaction.tags == word) | Transaction.tags.like(f"{word},%") | Transaction.tags.like(f"%,{word}") | Transaction.tags.like(f"%,{word},%"))
    if account:
        like = f"%{account}%"
        query = query.where((Account.name.ilike(like)) | (Account.institution.ilike(like)))
    if start:
        query = query.where(Transaction.date >= start)
    if end:
        query = query.where(Transaction.date <= end)
    if min_amount is not None:
        query = query.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        query = query.where(Transaction.amount <= max_amount)
    if not include_non_spending:
        query = query.where(Transaction.is_transfer == False, Transaction.is_card_payment == False, Transaction.is_income == False, Transaction.is_ignored == False)  # noqa: E712
    # `total` counts every row the filter matches, so a caller can page with `offset`; newest first with the id
    # breaking ties keeps a page from repeating or skipping a row between calls.
    total = session.exec(select(func.count()).select_from(query.with_only_columns(Transaction.id).subquery())).one()
    rows = session.exec(query.order_by(Transaction.date.desc(), Transaction.id.desc()).offset(offset).limit(limit)).all()
    from .engine.edits import splits_by_tx

    split = splits_by_tx(session, [t.id for t, _ in rows]) if redaction == "full" else {}
    from .models import Document

    kept: dict[int, list[int]] = {}
    if rows:
        for d in session.exec(select(Document).where(Document.attach_to.in_([f"transaction:{t.id}" for t, _ in rows]))).all():
            kept.setdefault(int(d.attach_to.split(":")[1]), []).append(d.id)
    items = []
    for t, a in rows:
        items.append({
            "id": t.id,
            "date": t.date.isoformat(),
            "merchant": "(redacted)" if redaction == "aggregates" else t.merchant,
            "amount": t.amount,
            "category": t.category,
            "account": a.name,
            "institution": a.institution,
            "description": t.description if redaction == "full" else "",
            "note": t.note if redaction == "full" else "",
            "tags": [x for x in (t.tags or "").split(",") if x] if redaction == "full" else [],
            "pending": t.pending,
            "hidden": t.is_ignored,
            "kind": "transfer" if t.is_transfer else "card payment" if t.is_card_payment else "income" if t.is_income else "spending",
            "splits": [{"category": s.category, "amount": s.amount, "note": s.note} for s in split.get(t.id, [])],
            "receipts": kept.get(t.id, []),
        })
    return {"returned": len(items), "total": total, "offset": offset, "limit": limit, "total_amount": round(sum(i["amount"] for i in items), 2), "transactions": items}


def audit(session: Session, *, client: str, client_kind: str, tool: str, args: dict | None = None, rows: int = 0, redaction: str = "full") -> None:
    # Keyed with the session secret: an unkeyed hash of a low-entropy argument (an amount, a bill id) could be read
    # back from the audit table by trying values; the log says which call, never what was asked.
    from .config import settings

    digest = hmac.new(settings.signing_secret().encode(), json.dumps(args or {}, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()[:16]
    # When the caller's write is already committed (nothing else pending), the row is all this commit carries: a
    # failure here is logged and swallowed, so a completed write never answers 500 and is never sent again as a
    # retry. When the write is still pending, this commit is the write's own and its failure is the caller's.
    alone = not (session.new or session.dirty or session.deleted)
    session.add(AuditEntry(client=client, client_kind=client_kind, tool=tool, args_hash=digest, rows=rows, redaction=redaction))
    if not alone:
        session.commit()
        return
    try:
        session.commit()
    except Exception as error:  # noqa: BLE001
        session.rollback()
        logging.getLogger(__name__).warning("an activity row for %s could not be written: %s", tool, type(error).__name__)


def apply_statement_balances(session: Session, text: str, account_id: int | None) -> list[dict]:
    """The balance an imported OFX/QFX statement states, into the account its rows landed in: an owner with files only
    otherwise never has a balance, and the plan cannot say what is safe to spend. A bank-linked account keeps its
    feed's balance; a balance newer than the statement's day (a typed one, a later file) stands. A file with several
    statements finds each account by the last four digits of the number it names. Returns what was set."""
    from datetime import datetime, time, timezone

    from .connectors.banks import record_balance, sync_card_debt
    from .parsers.ofx_balance import balance_to_count, statement_balances

    found = statement_balances(text)
    if not found:
        return []
    accounts = [a for a in session.exec(select(Account).where(Account.active == True)).all()]  # noqa: E712
    set_: list[dict] = []
    for i, f in enumerate(found):
        digits = "".join(ch for ch in f["account"] if ch.isdigit())[-4:]
        account = next((a for a in accounts if len(found) > 1 and len(digits) == 4 and (a.name.endswith(digits) or (a.external_id or "").endswith(digits))), None)
        if account is None and i == 0 and account_id is not None:
            account = session.get(Account, account_id)  # the file's first statement is the account its rows were imported into
        figure = balance_to_count(f)
        if account is None or figure is None or figure[1] is None or account.connector in ("plaid", "teller"):
            continue
        amount, on = figure
        if account.balance is not None and account.balance_at is not None and account.balance_at.date() >= on:
            continue  # a balance as new as the statement's, or newer, is already there (typed, or a later file)
        account.balance, account.balance_at = amount, datetime.combine(on, time(0), tzinfo=timezone.utc)
        session.add(account)
        record_balance(session, account, amount, on, commit=False)
        if account.kind == "card":
            sync_card_debt(session, account)
        set_.append({"account_id": account.id, "balance": amount, "as_of": on.isoformat()})
    session.commit()
    return set_
