"""The owner's own hand on the transactions: a category, a note, tags, hidden or not; one purchase split across
categories; many rows changed at once; and a rule tried on the ledger before it is kept.

A split must add up to the transaction, to the cent, and every part goes the same way (all money out, or all
money in). Reports and budgets count a split row by its parts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Rule, Transaction, TransactionSplit

MAX_TAGS = 12
MAX_BULK = 500
TAG = re.compile(r"[^a-z0-9 _-]+")


def clean_tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        word = TAG.sub("", str(t).strip().lower())[:30].strip()
        if word and word not in out:
            out.append(word)
    return out[:MAX_TAGS]


def tags_of(t: Transaction) -> list[str]:
    return [x for x in (t.tags or "").split(",") if x]


def update(session: Session, tx_id: int, *, category: str | None = None, note: str | None = None, tags: list[str] | None = None, hidden: bool | None = None) -> Transaction:
    t = session.get(Transaction, tx_id)
    if t is None:
        raise KeyError(tx_id)
    if category is not None:
        category = category.strip()[:60]
        if not category:
            raise ValueError("a category is needed")
        t.category = category
        t.is_transfer = category == "Transfer"
        t.is_card_payment = category == "Credit Card Payment"
    if note is not None:
        t.note = note.strip()[:500]
    if tags is not None:
        t.tags = ",".join(clean_tags(tags))
    if hidden is not None:
        t.is_ignored = hidden
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def set_splits(session: Session, tx_id: int, parts: list[dict[str, Any]]) -> list[TransactionSplit]:
    """Replace a transaction's split; an empty list removes it."""
    t = session.get(Transaction, tx_id)
    if t is None:
        raise KeyError(tx_id)
    clean = []
    for p in parts:
        category = str(p.get("category") or "").strip()[:60]
        try:
            amount = round(float(p.get("amount")), 2)
        except (TypeError, ValueError) as error:
            raise ValueError("each part needs an amount") from error
        if not category:
            raise ValueError("each part needs a category")
        if amount == 0 or (amount > 0) != (t.amount > 0):
            raise ValueError("every part goes the same way as the transaction, and none is zero")
        clean.append(TransactionSplit(transaction_id=t.id, category=category, amount=amount, note=str(p.get("note") or "").strip()[:200]))
    if clean and len(clean) < 2:
        raise ValueError("a split has two parts or more")
    if clean and abs(sum(p.amount for p in clean) - t.amount) > 0.005:
        raise ValueError(f"the parts add up to {sum(p.amount for p in clean):.2f}, and the transaction is {t.amount:.2f}")
    for old in session.exec(select(TransactionSplit).where(TransactionSplit.transaction_id == t.id)).all():
        session.delete(old)
    for p in clean:
        session.add(p)
    session.commit()
    return clean


def splits_by_tx(session: Session, ids: Iterable[int]) -> dict[int, list[TransactionSplit]]:
    ids = list(ids)
    out: dict[int, list[TransactionSplit]] = {}
    for start in range(0, len(ids), 500):
        for s in session.exec(select(TransactionSplit).where(TransactionSplit.transaction_id.in_(ids[start:start + 500]))).all():
            out.setdefault(s.transaction_id, []).append(s)
    return out


def by_category(t: Transaction, splits: dict[int, list[TransactionSplit]]) -> Iterator[tuple[str, float]]:
    """A row's money by category: its parts when it is split, else itself."""
    parts = splits.get(t.id)
    if parts:
        for p in parts:
            yield p.category, p.amount
    else:
        yield t.category or "Uncategorized", t.amount


def bulk(session: Session, ids: list[int], *, category: str | None = None, tags_add: list[str] | None = None, tags_remove: list[str] | None = None, hidden: bool | None = None) -> int:
    if not ids:
        return 0
    if len(ids) > MAX_BULK:
        raise ValueError(f"at most {MAX_BULK} at once")
    add, remove = clean_tags(tags_add or []), set(clean_tags(tags_remove or []))
    changed = 0
    for t in session.exec(select(Transaction).where(Transaction.id.in_(ids))).all():
        if category is not None and category.strip():
            t.category = category.strip()[:60]
            t.is_transfer = t.category == "Transfer"
            t.is_card_payment = t.category == "Credit Card Payment"
        if add or remove:
            t.tags = ",".join(clean_tags([*[x for x in tags_of(t) if x not in remove], *add]))
        if hidden is not None:
            t.is_ignored = hidden
        session.add(t)
        changed += 1
    session.commit()
    return changed


def preview_rule(session: Session, today: date, *, kind: str, pattern: str, value: str = "", match_field: str = "merchant", days: int = 365) -> dict[str, Any]:
    """What a rule would change if it were kept: how many rows, which, and what each would become."""
    from .rules import KINDS, matches

    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    if not pattern.strip():
        raise ValueError("a pattern is needed")
    rule = Rule(kind=kind, pattern=pattern, value=value, match_field=match_field, source="preview")
    rows = session.exec(select(Transaction).where(Transaction.date >= today - timedelta(days=days)).order_by(Transaction.date.desc())).all()
    hits = [t for t in rows if matches(rule, t)]
    becomes = {"rename": lambda t: {"merchant": value}, "categorise": lambda t: {"category": value}, "hide": lambda t: {"hidden": True}}[kind]
    return {
        "count": len(hits),
        "total": round(sum(t.amount for t in hits), 2),
        "sample": [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "category": t.category, "amount": t.amount, "becomes": becomes(t)} for t in hits[:12]],
        "days": days,
    }
