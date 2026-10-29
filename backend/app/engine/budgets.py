"""A budget for each paycheck: an amount per category for the days one paycheck covers, what is spent so far since
payday, and what is left until the next one. What a category does not use can carry into the next paycheck.

The span is the owner's own: from the last payday to the day before the next, by the paycheck's cadence. Spending
is what the transactions say (pending ones included, so a purchase counts the day it is made), transfers, card
payments, income and ignored rows left out. The budgets never move money and never change safe to spend; they
say how the spending is going, and whether together they promise more than is safe."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import CategoryBudget, Income, Transaction
from .cadence import PAYCHECKS_PER_YEAR, income_days, next_unpaid_payday, previous_paydays

SUGGEST_DAYS = 56
TOLERANCE = 5.0  # dollars of slack before "ahead of pace"


def _money(v: float) -> float:
    return round(float(v), 2)


def paycheck_span(session: Session, today: date) -> dict[str, Any]:
    """The days the current paycheck covers: the last payday to the day before the next, and the one before."""
    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    upcoming = sorted(((next_unpaid_payday(session, i, today), i) for i in incomes), key=lambda p: p[0])
    if not upcoming:
        start = today - timedelta(days=13)
        return {"known": False, "start": start, "end": today, "next_payday": today + timedelta(days=1), "previous_start": start - timedelta(days=14), "cadence": "biweekly"}
    next_payday, income = upcoming[0]
    cadence = income.cadence or "biweekly"
    back = previous_paydays(next_payday, cadence, 2, **income_days(income))
    start = back[0] if back[0] <= today else today
    return {"known": True, "start": start, "end": next_payday - timedelta(days=1), "next_payday": next_payday, "previous_start": back[1], "cadence": cadence}


def _spent(session: Session, start: date, end: date) -> dict[str, float]:
    from .edits import by_category, splits_by_tx

    out: dict[str, float] = defaultdict(float)
    # A refund is spending that came back (a negative row that is not income): it nets off its category, as the reports count it.
    rows = [t for t in session.exec(select(Transaction).where(Transaction.date >= start, Transaction.date <= end)).all() if t.is_spending]
    split = splits_by_tx(session, [t.id for t in rows])
    for t in rows:
        for category, amount in by_category(t, split):
            out[category] += float(amount)
    return out


def budget_plan(session: Session, today: date) -> dict[str, Any]:
    span = paycheck_span(session, today)
    start, end, next_payday = span["start"], span["end"], span["next_payday"]
    days_total = max(1, (end - start).days + 1)
    days_gone = max(0, min(days_total, (today - start).days + 1))
    now = _spent(session, start, today)
    before = _spent(session, span["previous_start"], start - timedelta(days=1))
    per_year = PAYCHECKS_PER_YEAR.get(span["cadence"], 26)
    recent = _spent(session, today - timedelta(days=SUGGEST_DAYS), today - timedelta(days=1))
    windows_in_recent = SUGGEST_DAYS / (365.0 / per_year)
    budgets = session.exec(select(CategoryBudget).where(CategoryBudget.active == True).order_by(CategoryBudget.category)).all()  # noqa: E712
    items = []
    for b in budgets:
        carried = max(0.0, b.per_paycheck - before.get(b.category, 0.0)) if b.rollover == "next" and span["known"] else 0.0
        allowed = b.per_paycheck + carried
        spent = now.get(b.category, 0.0)
        left = allowed - spent
        expected_by_now = allowed * days_gone / days_total
        if spent > allowed + 0.005:
            status, word = "crit", f"Over by ${spent - allowed:,.2f}"
        elif spent > expected_by_now + TOLERANCE:
            status, word = "warn", "Ahead of pace"
        else:
            status, word = "good", "On pace"
        items.append({
            "category": b.category, "per_paycheck": _money(b.per_paycheck), "carried": _money(carried), "allowed": _money(allowed), "spent": _money(spent), "left": _money(left),
            "share_used": round(spent / allowed, 4) if allowed > 0 else None, "expected_by_now": _money(expected_by_now), "status": status, "word": word,
            "rollover": b.rollover, "suggested": _money(recent.get(b.category, 0.0) / windows_in_recent) if windows_in_recent else 0.0, "source": f"budget:{b.id}",
        })
    budgeted = {b.category for b in budgets}
    others = sorted(((c, v) for c, v in now.items() if c not in budgeted), key=lambda kv: -kv[1])
    suggestions = sorted(((c, v / windows_in_recent) for c, v in recent.items() if c not in budgeted and windows_in_recent and v / windows_in_recent >= 10), key=lambda kv: -kv[1])[:8]
    from .snapshot import finance_snapshot

    amount = finance_snapshot(session, today)["safe_to_spend"]["amount"]
    left_total = sum(max(0.0, i["left"]) for i in items)
    if amount is None:  # no balance yet: whether the budgets fit is unknown, never "more than is safe"
        safe = None
    else:
        safe = float(amount)
    return {
        "as_of": today.isoformat(),
        "span": {"start": start.isoformat(), "end": end.isoformat(), "next_payday": next_payday.isoformat(), "known": span["known"], "days_total": days_total, "days_gone": days_gone, "days_left": max(0, days_total - days_gone)},
        "items": items,
        "totals": {"allowed": _money(sum(i["allowed"] for i in items)), "spent": _money(sum(i["spent"] for i in items)), "left": _money(sum(i["left"] for i in items)), "left_unspent": _money(left_total)},
        "unbudgeted": {"spent": _money(sum(v for _, v in others)), "top": [{"category": c, "spent": _money(v)} for c, v in others[:6]]},
        "suggestions": [{"category": c, "per_paycheck": _money(v)} for c, v in suggestions],
        "safe_to_spend": None if safe is None else _money(safe),
        "fits": None if safe is None else left_total <= safe + 0.005,
        "fit_note": None if safe is None or left_total <= safe + 0.005 else f"Together the budgets leave ${left_total:,.2f} to spend, ${left_total - safe:,.2f} more than is safe before payday.",
        "not_advice": "Computed by the app from your transactions and the amounts you set. Not financial advice.",
    }


def save_budget(session: Session, category: str, per_paycheck: float, rollover: str = "next") -> CategoryBudget:
    category = (category or "").strip()[:60]
    if not category:
        raise ValueError("a category is needed")
    if not (0 <= per_paycheck <= 100000):
        raise ValueError("the amount is between 0 and 100,000")
    if rollover not in ("next", "none"):
        raise ValueError("rollover is next or none")
    row = session.exec(select(CategoryBudget).where(CategoryBudget.category == category)).first() or CategoryBudget(category=category)
    row.per_paycheck, row.rollover, row.active = round(float(per_paycheck), 2), rollover, True
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
