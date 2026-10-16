"""Money that moved between the owner's own accounts, matched: a transfer out of checking with the same amount
arriving in savings, a card payment from checking with its credit on the card, and the same purchase seen twice
through two sources. A matched transfer or card payment is not spending, so it leaves the pace, the reports and
the detection alone.

A pair is made only when it is the one candidate and either side already reads as a transfer or a payment;
anything less sure waits on Needs you for the owner's yes or no. A same-purchase pair always waits: merging hides
a row, so the owner decides. Every pair remembers the flags its rows had before, so a "no" or an undo puts them
back exactly."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Transaction, TransactionPair, utcnow

WINDOW_DAYS = 3
LOOKBACK_DAYS = 120
MOVE_WORDS = re.compile(r"transfer|xfer|online banking|to savings|from savings|to checking|from checking|payment thank you|autopay|card payment|epay|mobile payment|payment received", re.IGNORECASE)
LIVE = ("plaid", "teller")


def _text(t: Transaction) -> str:
    return f"{t.merchant} {t.raw_name} {t.description}"


def _flags(t: Transaction) -> dict[str, bool]:
    return {"is_transfer": t.is_transfer, "is_card_payment": t.is_card_payment, "is_ignored": t.is_ignored}


def _apply(session: Session, pair: TransactionPair, a: Transaction, b: Transaction) -> None:
    if pair.kind == "duplicate":
        b.is_ignored = True  # the second sighting of the same purchase; the first stays
        b.note = (b.note + " · " if b.note else "") + f"Same purchase as transaction {a.id}"
    elif pair.kind == "card_payment":
        a.is_card_payment = b.is_card_payment = True
    else:
        a.is_transfer = b.is_transfer = True
    session.add_all([a, b])


def _restore(session: Session, pair: TransactionPair) -> None:
    before = json.loads(pair.before or "{}")
    for key, row_id in (("a", pair.a_id), ("b", pair.b_id)):
        row = session.get(Transaction, row_id)
        if row is None or key not in before:
            continue
        for flag, value in before[key].items():
            setattr(row, flag, value)
        if pair.kind == "duplicate" and key == "b":
            row.note = re.sub(r"(?: · )?Same purchase as transaction \d+", "", row.note or "")
        session.add(row)


def find_pairs(session: Session, today: date) -> dict[str, int]:
    """Look for new pairs in the last four months; drop pairs whose rows are gone. Returns what it did."""
    pairs = session.exec(select(TransactionPair)).all()
    rows = session.exec(select(Transaction).where(Transaction.date >= today - timedelta(days=LOOKBACK_DAYS), Transaction.pending == False)).all()  # noqa: E712
    by_id = {t.id: t for t in rows}
    gone = 0
    claimed: set[int] = set()
    for p in pairs:
        if session.get(Transaction, p.a_id) is None or session.get(Transaction, p.b_id) is None:
            _restore(session, p)
            session.delete(p)
            gone += 1
            continue
        if p.status != "rejected":
            claimed.update((p.a_id, p.b_id))
    known = {(p.a_id, p.b_id) for p in pairs}
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    arriving: dict[float, list[Transaction]] = defaultdict(list)
    for t in rows:
        if t.amount < 0 and not t.is_ignored:
            arriving[round(-t.amount, 2)].append(t)
    made = waiting = 0
    for t in sorted((t for t in rows if t.amount > 0 and not t.is_ignored), key=lambda t: t.date):
        if t.id in claimed:
            continue
        candidates = [u for u in arriving.get(round(t.amount, 2), []) if u.account_id != t.account_id and u.id not in claimed and abs((u.date - t.date).days) <= WINDOW_DAYS and (t.id, u.id) not in known]
        if not candidates:
            continue
        best = min(candidates, key=lambda u: (abs((u.date - t.date).days), u.id))
        target = accounts.get(best.account_id)
        kind = "card_payment" if target is not None and target.kind == "card" else "transfer"
        signal = any((x.is_transfer or x.is_card_payment or x.category in ("Transfer", "Credit Card Payment") or MOVE_WORDS.search(_text(x))) for x in (t, best))
        sure = len(candidates) == 1 and signal
        pair = TransactionPair(kind=kind, a_id=t.id, b_id=best.id, status="auto" if sure else "pending", confidence=0.95 if sure else (0.6 if signal else 0.4),
                               reason=("the one match, and it reads as a " + ("card payment" if kind == "card_payment" else "transfer")) if sure else
                               (f"{len(candidates)} possible matches" if len(candidates) > 1 else "the amounts and dates match, but neither side says transfer or payment"),
                               before=json.dumps({"a": _flags(t), "b": _flags(best)}))
        session.add(pair)
        if sure:
            _apply(session, pair, t, best)
            made += 1
        else:
            waiting += 1
        claimed.update((t.id, best.id))
    # The same purchase twice: an export or a file row beside the bank's own, on the same account.
    for account_id, group in _by_account(rows).items():
        live = [t for t in group if t.source in LIVE and not t.is_ignored and t.id not in claimed]
        others = [t for t in group if t.source not in LIVE and not t.is_ignored and t.id not in claimed]
        for o in others:
            twin = next((t for t in live if t.id not in claimed and abs(t.amount - o.amount) <= 0.01 and abs((t.date - o.date).days) <= WINDOW_DAYS and (t.id, o.id) not in known), None)
            if twin is None:
                continue
            session.add(TransactionPair(kind="duplicate", a_id=twin.id, b_id=o.id, status="pending", confidence=0.7 if twin.date == o.date else 0.5,
                                        reason=f"the same amount on the same account, {abs((twin.date - o.date).days)} day(s) apart, from {twin.source} and {o.source}",
                                        before=json.dumps({"a": _flags(twin), "b": _flags(o)})))
            claimed.update((twin.id, o.id))
            waiting += 1
    session.commit()
    return {"matched": made, "waiting": waiting, "gone": gone}


def _by_account(rows: list[Transaction]) -> dict[int, list[Transaction]]:
    out: dict[int, list[Transaction]] = defaultdict(list)
    for t in rows:
        out[t.account_id].append(t)
    return out


def decide(session: Session, pair_id: int, decision: str) -> dict[str, Any]:
    """The owner's answer: yes applies the pair (or keeps an automatic one), no puts the rows back as they were
    and the pair is never offered again."""
    pair = session.get(TransactionPair, pair_id)
    if pair is None:
        raise KeyError(pair_id)
    a, b = session.get(Transaction, pair.a_id), session.get(Transaction, pair.b_id)
    if a is None or b is None:
        raise KeyError(pair_id)
    if decision == "yes":
        if pair.status in ("pending", "rejected"):
            _apply(session, pair, a, b)
        pair.status = "confirmed"
    elif decision == "no":
        if pair.status in ("auto", "confirmed"):
            _restore(session, pair)
        pair.status = "rejected"
    else:
        raise ValueError("decision is yes or no")
    pair.decided_at = utcnow()
    session.add(pair)
    session.commit()
    return pair_out(session, pair)


def pair_out(session: Session, p: TransactionPair, accounts: dict[int, Account] | None = None) -> dict[str, Any]:
    accounts = accounts or {a.id: a for a in session.exec(select(Account)).all()}

    def side(row_id: int) -> dict[str, Any] | None:
        t = session.get(Transaction, row_id)
        if t is None:
            return None
        acct = accounts.get(t.account_id)
        return {"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": t.amount, "account": acct.name if acct else "", "source": t.source}

    words = {"transfer": "Moved between your accounts", "card_payment": "A card payment and its credit", "duplicate": "The same purchase, seen twice"}
    return {"id": p.id, "kind": p.kind, "title": words.get(p.kind, p.kind), "status": p.status, "confidence": p.confidence, "reason": p.reason,
            "out": side(p.a_id), "in": side(p.b_id), "decided_at": p.decided_at.isoformat() if p.decided_at else None, "source": f"pair:{p.id}"}


def pairs_overview(session: Session) -> dict[str, Any]:
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    rows = session.exec(select(TransactionPair).order_by(TransactionPair.created_at.desc())).all()
    out = [pair_out(session, p, accounts) for p in rows]
    return {
        "waiting": [p for p in out if p["status"] == "pending"],
        "matched": [p for p in out if p["status"] in ("auto", "confirmed")][:50],
        "counts": {"waiting": sum(1 for p in out if p["status"] == "pending"), "matched": sum(1 for p in out if p["status"] in ("auto", "confirmed"))},
    }
