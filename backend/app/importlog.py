"""The import log. Every import of a file or an export is one batch: a restore point is taken before it starts,
each transaction it adds carries the batch's id, and the bills it creates or changes are written down with their
values before. Undoing a batch removes exactly what it added and puts back exactly what it changed, leaving
everything that happened since in place. An import that changed nothing leaves no entry and no restore point."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from sqlmodel import Session, select

from . import restore
from .models import Account, Bill, ImportBatch, Payment, Paystub, PlannedPayment, Transaction, utcnow

log = logging.getLogger(__name__)
BILL_FIELDS = ("amount", "cadence", "due_day", "anchor_date", "autopay", "category")


def begin(session: Session, kind: str, source_file: str, via: str = "upload") -> ImportBatch:
    """Start an import: the batch the rows will carry. The restore point is taken in finish(), and only when the
    import keeps something: the import has not committed then, so the copy, read on a connection of its own, is
    the database as it was before the import began."""
    batch = ImportBatch(kind=kind, source_file=(source_file or "")[:200], via=via, restore_point="")
    session.add(batch)
    session.flush()
    return batch


def finish(session: Session, batch: ImportBatch, *, inserted: int = 0, duplicates: int = 0, merged: int = 0, account_id: int | None = None,
           accounts_created: list[int] | None = None, changes: list[dict] | None = None, summary: str = "") -> None:
    """Record what the import did; the caller commits. Nothing changed: no entry and no restore point."""
    if not inserted and not changes and not accounts_created:
        session.delete(batch)
        return
    if not batch.restore_point:
        try:
            batch.restore_point = restore.make_point("import", f"Before importing {batch.source_file or batch.kind}")["name"]
        except Exception as error:  # noqa: BLE001 - a copy that cannot be made must not stop the import; the batch still undoes it
            log.warning("restore point before an import failed: %s", error)
    batch.inserted, batch.duplicates, batch.merged, batch.account_id = inserted, duplicates, merged, account_id
    batch.accounts_created = json.dumps(accounts_created or [])
    batch.changes = json.dumps(changes or [], default=str)
    batch.summary = summary[:300]
    session.add(batch)


def bill_before(bill: Bill) -> dict[str, Any]:
    return {k: getattr(bill, k) for k in BILL_FIELDS}


def undo(session: Session, batch_id: int, today: date) -> dict[str, Any]:
    """Remove what one import added and put back what it changed. Returns what was undone."""
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError("No such import")
    if batch.status == "undone":
        raise ValueError("That import is already undone")
    rows = list(session.exec(select(Transaction).where(Transaction.import_id == batch.id)).all())
    ids = [t.id for t in rows]
    if ids:
        # What was matched to these rows is unmatched; the matcher runs again below.
        for p in session.exec(select(Payment).where(Payment.transaction_id.in_(ids))).all():
            p.transaction_id = None
            if p.status == "paid":
                p.status = "scheduled"
            session.add(p)
        for model in (PlannedPayment, Paystub):
            for r in session.exec(select(model).where(model.transaction_id.in_(ids))).all():
                r.transaction_id = None
                session.add(r)
        session.flush()
    for t in rows:
        session.delete(t)
    bills_removed = bills_restored = 0
    for c in json.loads(batch.changes or "[]"):
        bill = session.get(Bill, int(c.get("id", 0)))
        if bill is None:
            continue
        if c.get("op") == "bill_created":
            bill.active = False
            bills_removed += 1
        elif c.get("op") == "bill_updated":
            for k, v in (c.get("before") or {}).items():
                if k in BILL_FIELDS:
                    setattr(bill, k, date.fromisoformat(v) if k == "anchor_date" and isinstance(v, str) else v)
            bills_restored += 1
        session.add(bill)
    session.flush()
    # An account an undone import created closes once nothing is left in it, whichever import emptied it.
    created_by_undone = set(json.loads(batch.accounts_created or "[]"))
    for other in session.exec(select(ImportBatch).where(ImportBatch.status == "undone")).all():
        created_by_undone.update(json.loads(other.accounts_created or "[]"))
    accounts_closed = 0
    for aid in sorted(created_by_undone):
        account = session.get(Account, int(aid))
        if account is not None and account.active and session.exec(select(Transaction.id).where(Transaction.account_id == account.id)).first() is None:
            account.active = False
            session.add(account)
            accounts_closed += 1
    batch.status, batch.undone_at = "undone", utcnow()
    session.add(batch)
    session.commit()
    from .engine.borrowing import sync_borrowing
    from .engine.payments import match_payments
    from .jobs.detect import run_detection

    if rows:
        run_detection(session, today)
        sync_borrowing(session, today)  # an advance the undone rows showed goes with them
    match_payments(session, today)
    return {"id": batch.id, "removed": len(rows), "bills_removed": bills_removed, "bills_restored": bills_restored, "accounts_closed": accounts_closed}


def history(session: Session, limit: int = 30) -> list[dict[str, Any]]:
    batches = session.exec(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit)).all()
    names = {a.id: a.name for a in session.exec(select(Account)).all()}
    points = {p["name"] for p in restore.list_points()}
    return [{
        "id": b.id, "kind": b.kind, "source_file": b.source_file, "via": b.via, "account": names.get(b.account_id or -1), "inserted": b.inserted, "duplicates": b.duplicates,
        "merged": b.merged, "summary": b.summary, "status": b.status, "created_at": b.created_at.isoformat(), "undone_at": b.undone_at.isoformat() if b.undone_at else None,
        "restore_point": b.restore_point if b.restore_point in points else None,
    } for b in batches]
