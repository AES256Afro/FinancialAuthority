"""Receipts and the files kept by month (M31).

A receipt (a store's email, a photo, a PDF) is kept with the transaction it paid for: matched by its total within
a few days of the purchase, the store's name breaking a tie. Its last day to return and its warranty's end become
dated reminders. Statements and paystubs are kept with their month, so a month's files are in one place."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Document, Transaction
from .recurring import merchant_key

RETURN_SOON = 7  # days before the last day to return: a step on Needs you
WARRANTY_SOON = 30


def candidates(session: Session, total: float | None, bought_on: date | None, merchant: str = "", limit: int = 3) -> list[dict[str, Any]]:
    """The transactions a receipt may belong to: the same total, from two days before the purchase to ten after
    (a card charge posts late), closest name and date first."""
    if total is None or bought_on is None:
        return []
    rows = session.exec(select(Transaction, Account).join(Account, Account.id == Transaction.account_id).where(
        Transaction.date >= bought_on - timedelta(days=2), Transaction.date <= bought_on + timedelta(days=10), Transaction.is_transfer == False)).all()  # noqa: E712
    key = merchant_key(merchant)
    taken = {d.attach_to for d in session.exec(select(Document).where(Document.kind == "receipt")).all()}
    found = []
    for t, a in rows:
        if abs(abs(t.amount) - total) > 0.01:
            continue
        tk = merchant_key(t.merchant)
        named = bool(key and tk and (tk.startswith(key[:6]) or key.startswith(tk[:6])))
        found.append((0 if named else 1, abs((t.date - bought_on).days), t, a))
    found.sort(key=lambda x: (x[0], x[1]))
    return [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": t.amount, "account": a.name, "named": n == 0,
             "has_receipt": f"transaction:{t.id}" in taken, "source": f"transaction:{t.id}"} for n, _, t, a in found[:limit]]


def receipt_out(doc: Document) -> dict[str, Any]:
    return {"id": doc.id, "title": doc.title, "merchant": doc.merchant, "amount": doc.amount, "bought_on": doc.bought_on.isoformat() if doc.bought_on else None,
            "return_by": doc.return_by.isoformat() if doc.return_by else None, "warranty_until": doc.warranty_until.isoformat() if doc.warranty_until else None,
            "attach_to": doc.attach_to, "period": doc.period, "source": f"document:{doc.id}"}


def deadlines(session: Session, today: date, *, within_days: int = 3650) -> list[dict[str, Any]]:
    """Every return window and warranty still open, soonest first, each with the days left and how near it is."""
    out = []
    for doc in session.exec(select(Document).where(Document.kind == "receipt")).all():
        for what, on, soon in (("return", doc.return_by, RETURN_SOON), ("warranty", doc.warranty_until, WARRANTY_SOON)):
            if on is None or on < today or (on - today).days > within_days:
                continue
            left = (on - today).days
            status = "crit" if what == "return" and left <= 2 else "warn" if left <= soon else "good"
            item = doc.merchant or doc.title
            word = (f"Last day to return {item}: {on:%a %b} {on.day}" if what == "return" else f"The warranty on {item} ends {on:%a %b} {on.day}, {on.year}")
            out.append({"document_id": doc.id, "what": what, "on": on.isoformat(), "days_left": left, "status": status, "word": word,
                        "title": doc.title, "merchant": doc.merchant, "amount": doc.amount, "attach_to": doc.attach_to, "source": f"document:{doc.id}"})
    out.sort(key=lambda x: (x["on"], x["what"]))
    return out


def receipts_overview(session: Session, today: date) -> dict[str, Any]:
    docs = session.exec(select(Document).where(Document.kind == "receipt").order_by(Document.bought_on.desc(), Document.uploaded_at.desc())).all()
    unmatched = [receipt_out(d) for d in docs if not d.attach_to.startswith("transaction:")]
    return {
        "as_of": today.isoformat(), "deadlines": deadlines(session, today), "receipts": [receipt_out(d) for d in docs], "unmatched": unmatched,
        "total": round(sum(d.amount or 0.0 for d in docs), 2), "count": len(docs),
        "not_advice": "What your receipts say about returns and warranties. The store's own terms are what hold.",
    }


def months(session: Session) -> list[dict[str, Any]]:
    """Every kept file with a month, grouped by month, newest first: statements, paystubs, receipts and the rest."""
    from .documents import doc_out

    by: dict[str, list[Document]] = {}
    for doc in session.exec(select(Document).where(Document.period != "").order_by(Document.uploaded_at.desc())).all():
        by.setdefault(doc.period, []).append(doc)
    out = []
    for month in sorted(by, reverse=True):
        rows = by[month]
        kinds: dict[str, int] = {}
        for d in rows:
            kinds[d.kind] = kinds.get(d.kind, 0) + 1
        out.append({"month": month, "count": len(rows), "kinds": kinds, "documents": [doc_out(d) for d in rows]})
    return out


def month_of(value: date | str | None) -> str:
    if value is None:
        return ""
    return (value.isoformat() if isinstance(value, date) else str(value))[:7]
