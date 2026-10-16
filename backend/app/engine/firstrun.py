"""The first run, in five steps, each done when the registers say so: when you are paid, where the money comes
from, the bills named, which check pays which bill, and the first picture. Every step can be skipped and opened
again from Next steps; nothing is asked twice."""

from __future__ import annotations

from sqlmodel import Session, select

from ..models import Account, Bill, Income, RecurringSeries, Setting, Transaction
from ..store import get_setting, set_setting

SETTING = "firstrun"


def firstrun(session: Session) -> dict:
    state = get_setting(session, SETTING, {}) or {}
    marked = set(state.get("marked", []))
    has_income = session.exec(select(Income.id).where(Income.active == True).limit(1)).first() is not None  # noqa: E712
    has_data = session.exec(select(Transaction.id).limit(1)).first() is not None or session.exec(select(Account.id).where(Account.active == True, Account.connector != "manual").limit(1)).first() is not None  # noqa: E712
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    unnamed = session.exec(select(RecurringSeries.id).where(RecurringSeries.active == True, RecurringSeries.confirmed == False).limit(1)).first() is not None  # noqa: E712
    assigned = bool(bills) and all((b.paid_from or "due") in ("mid", "end") for b in bills)
    picked = session.get(Setting, "appearance") is not None  # a choice saved, not the default
    steps = [
        {"id": "paid", "title": "When are you paid?", "text": "The cadence and the usual amount of each check. Two taps; your paystubs fill it in once they arrive.", "done": has_income or "paid" in marked, "link": "/paystubs"},
        {"id": "data", "title": "Where does the money come from?", "text": "Connect a bank, or drop a CSV or a Rocket Money export. Either is fine; both is better.", "done": has_data or "data" in marked, "link": "/settings/connectors"},
        {"id": "bills", "title": "Name the bills it found", "text": "One list: keep what is a bill, drop what is not. Nothing is counted until it has a name.", "done": (bool(bills) and not unnamed) or "bills" in marked, "link": "/confirm"},
        {"id": "checks", "title": "Which check pays which bill?", "text": "Say which paycheck carries each bill, so each check knows its load.", "done": assigned or "checks" in marked, "link": "/bills"},
        {"id": "picture", "title": "Your first picture", "text": "Pick how the home looks. Every view shows your own numbers; change it any time.", "done": picked or "picture" in marked, "link": "/settings/appearance"},
    ]
    done = len([s for s in steps if s["done"]])
    return {"steps": steps, "done": done, "total": len(steps), "dismissed": bool(state.get("dismissed")), "show": not state.get("dismissed") and done < len(steps)}


def update(session: Session, *, dismissed: bool | None = None, mark: str | None = None, unmark: str | None = None) -> dict:
    state = get_setting(session, SETTING, {}) or {}
    marked = set(state.get("marked", []))
    if mark:
        marked.add(mark)
    if unmark:
        marked.discard(unmark)
    state["marked"] = sorted(marked)
    if dismissed is not None:
        state["dismissed"] = bool(dismissed)
    set_setting(session, SETTING, state)
    return firstrun(session)
