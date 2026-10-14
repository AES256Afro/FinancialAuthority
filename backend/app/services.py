"""Service functions shared by the REST routes and the MCP tools, so both doors answer identically."""

from __future__ import annotations

import hashlib
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
) -> dict:
    limit = max(1, min(int(limit), 500))
    query = select(Transaction, Account).join(Account, Account.id == Transaction.account_id)
    if text:
        like = f"%{text}%"
        query = query.where((Transaction.merchant.ilike(like)) | (Transaction.raw_name.ilike(like)) | (Transaction.description.ilike(like)) | (Transaction.note.ilike(like)))
    if category:
        query = query.where(Transaction.category == category)
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
        })
    return {"returned": len(items), "total_amount": round(sum(i["amount"] for i in items), 2), "transactions": items}


def audit(session: Session, *, client: str, client_kind: str, tool: str, args: dict | None = None, rows: int = 0, redaction: str = "full") -> None:
    digest = hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    session.add(AuditEntry(client=client, client_kind=client_kind, tool=tool, args_hash=digest, rows=rows, redaction=redaction))
    session.commit()
