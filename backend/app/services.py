"""Service functions shared by the REST routes and the MCP tools, so both doors answer identically."""

from __future__ import annotations

import hashlib
import logging
import hmac
import json
from datetime import date

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
) -> dict:
    limit = max(1, min(int(limit), 500))
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
    rows = session.exec(query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit)).all()
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
    return {"returned": len(items), "total_amount": round(sum(i["amount"] for i in items), 2), "transactions": items}


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
