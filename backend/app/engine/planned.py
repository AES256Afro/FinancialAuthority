"""Planned payments: money the owner has told the app is leaving, before the ledger sees it. A card payment
scheduled for tonight, a transfer set up for Friday. Each is counted from its date until a matching
transaction posts, when it settles itself; a card payment also nets off the card's balance so the same
money is not held back twice."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, PlannedPayment, Transaction


def _money(v: float) -> float:
    return round(float(v), 2)


def open_planned(session: Session, today: date) -> list[PlannedPayment]:
    rows = session.exec(select(PlannedPayment).where(PlannedPayment.done == False).order_by(PlannedPayment.date)).all()  # noqa: E712
    return [p for p in rows if (today - p.date).days <= 14]  # a plan two weeks past its date with nothing posted is stale, not a reservation


def planned_events(session: Session, today: date) -> list[dict]:
    """One event per open plan, on its date (today when the date has passed), so it sits in the window it falls
    in and comes off the daily line on the day it leaves."""
    events: list[dict] = []
    for p in open_planned(session, today):
        when = max(p.date, today)
        events.append({"date": when, "window_date": when, "name": p.name, "amount": _money(p.amount), "kind": "planned", "autopay": True, "source": f"planned:{p.id}", "line": "debt" if p.kind == "card_payment" else "utilities", "paid_from": "due", "planned": True, "planned_kind": p.kind})
    return events


def planned_to_cards(session: Session, today: date) -> dict[int, float]:
    """Card account id to the planned payments heading for it, which the card reservation nets off."""
    out: dict[int, float] = {}
    cards = session.exec(select(Account).where(Account.active == True, Account.kind == "card")).all()  # noqa: E712
    for p in open_planned(session, today):
        if p.kind != "card_payment":
            continue
        target = p.to_account_id if p.to_account_id else (cards[0].id if len(cards) == 1 else None)
        if target is not None:
            out[target] = out.get(target, 0.0) + float(p.amount)
    return out


def settle_planned(session: Session, today: date) -> int:
    """A plan settles when a transaction that looks like it posts: money out, within a dollar, from a day before
    its date to a week after. Returns how many settled."""
    settled = 0
    for p in session.exec(select(PlannedPayment).where(PlannedPayment.done == False)).all():  # noqa: E712
        rows = session.exec(select(Transaction).where(Transaction.date >= p.date - timedelta(days=1), Transaction.date <= p.date + timedelta(days=7), Transaction.amount > 0)).all()
        match = None
        for t in rows:
            if abs(t.amount - p.amount) > 1.0:
                continue
            if p.kind == "card_payment" and not (t.is_card_payment or t.category in ("Credit Card Payment", "Transfer") or "payment" in (t.raw_name + " " + t.description).lower()):
                continue
            if p.to_account_id and t.account_id == p.to_account_id:
                continue  # the card side of the same payment is money in on the card, not the payment leaving checking
            match = t
            break
        if match is not None:
            p.done, p.transaction_id = True, match.id
            session.add(p)
            settled += 1
    if settled:
        session.commit()
    return settled


def planned_out(p: PlannedPayment) -> dict:
    return {"id": p.id, "name": p.name, "amount": p.amount, "date": p.date.isoformat(), "kind": p.kind, "to_account_id": p.to_account_id, "note": p.note, "done": p.done, "transaction_id": p.transaction_id, "created_at": p.created_at.isoformat()}
