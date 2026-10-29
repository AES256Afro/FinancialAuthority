"""Debt payoff with dates: every debt with a rate paid down month by month, the minimums first and whatever
extra the owner names on top, by the highest rate first (avalanche) or the smallest balance first (snowball);
when a debt is gone its minimum rolls on to the next. The answer is a date for each debt, the interest each way,
and what the extra saves against paying only the minimums.

Advance apps are counted too, by what they cost: the fee or tip for each $100 taken, over the last 90 days, as a
yearly rate a card would have to charge to cost the same. Money lent to or borrowed from people is kept on its own
list, with no rate unless the owner gives one."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Advance
from ..store import get_setting, set_setting
from .reports import debt_mountain

MAX_MONTHS = 600
PEOPLE = "people.loans"
NOT_ADVICE = "Worked out by the app from your debts and the extra you name. Not financial advice."


def _money(v: float) -> float:
    return round(float(v), 2)


def _add_months(d: date, n: int) -> date:
    import calendar

    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def simulate(debts: list[dict[str, Any]], extra: float, order: str, start: date) -> dict[str, Any]:
    """Month by month until every balance is gone. `order` is avalanche, snowball or minimums (no extra, no roll)."""
    live = [{**d, "balance": float(d["balance"]), "paid_on": None, "interest": 0.0} for d in debts if float(d["balance"]) > 0.005]
    month = 0
    while any(d["balance"] > 0.005 for d in live) and month < MAX_MONTHS:
        month += 1
        freed = 0.0
        for d in live:
            if d["balance"] <= 0.005:
                freed += d["minimum"] if order != "minimums" else 0.0
                continue
            interest = d["balance"] * d["apr"] / 100.0 / 12.0
            d["interest"] += interest
            d["balance"] += interest
            pay = min(d["balance"], d["minimum"])
            d["balance"] -= pay
            if order != "minimums":
                freed += d["minimum"] - pay  # the part of a minimum its debt no longer needs, the month it is paid off, rolls on too
        if order != "minimums":
            pot = extra + freed
            targets = sorted((d for d in live if d["balance"] > 0.005), key=(lambda d: (-d["apr"], d["balance"])) if order == "avalanche" else (lambda d: (d["balance"], -d["apr"])))
            for d in targets:
                if pot <= 0.005:
                    break
                pay = min(pot, d["balance"])
                d["balance"] -= pay
                pot -= pay
        for d in live:
            if d["balance"] <= 0.005 and d["paid_on"] is None:
                d["paid_on"] = _add_months(start, month)
    done = all(d["balance"] <= 0.005 for d in live)
    # A way that never finishes (a minimum under the month's interest) has no interest figure: the sum to the
    # cap is a fiction in the billions, so it is None and "grows" says why; the page says the balance grows.
    return {
        "order": order, "months": month if done else None, "debt_free": _add_months(start, month).isoformat() if done else None,
        "interest": _money(sum(d["interest"] for d in live)) if done else None, "grows": not done,
        "debts": [{"name": d["name"], "source": d["source"], "paid_on": d["paid_on"].isoformat() if d["paid_on"] else None, "interest": _money(d["interest"]) if d["paid_on"] else None} for d in live],
    }


def advance_costs(session: Session, today: date) -> list[dict[str, Any]]:
    since = today - timedelta(days=90)
    by: dict[str, list[Advance]] = defaultdict(list)
    for a in session.exec(select(Advance).where(Advance.taken_on >= since)).all():
        by[a.provider].append(a)
    out = []
    for provider, rows in by.items():
        taken = sum(a.amount for a in rows)
        fees = sum(a.fee for a in rows)
        days = max(1.0, sum(max(1, (a.repay_date - a.taken_on).days) * a.amount for a in rows) / taken) if taken else 14.0
        per_100 = fees / taken * 100 if taken else 0.0
        out.append({"provider": provider, "advances": len(rows), "taken": _money(taken), "fees": _money(fees), "fee_per_100": _money(per_100),
                    "days_held": round(days, 1), "as_yearly_rate": round(per_100 / 100 * 365 / days * 100, 1) if taken else 0.0})
    return sorted(out, key=lambda r: -r["fees"])


def payoff_plan(session: Session, today: date, extra: float = 0.0) -> dict[str, Any]:
    if not (0 <= extra <= 100_000):
        raise ValueError("the extra each month is between 0 and 100,000")
    mountain = debt_mountain(session, today)
    debts = []
    for d in mountain["items"]:
        if d["kind"] not in ("card", "loan", "medical", "bnpl") or not d.get("balance") or d["balance"] <= 0:
            continue
        minimum = float(d.get("minimum") or 0.0) or max(25.0, round(d["balance"] * 0.02, 2))
        debts.append({"name": d["name"], "balance": d["balance"], "apr": float(d.get("apr") or 0.0), "minimum": minimum, "source": d["source"], "minimum_assumed": not d.get("minimum")})
    start = today.replace(day=1)
    base = simulate(debts, 0.0, "minimums", start)
    ways = {o: simulate(debts, extra, o, start) for o in ("avalanche", "snowball")}
    for w in ways.values():
        w["interest_saved"] = _money(base["interest"] - w["interest"]) if base["months"] is not None and w["months"] is not None else None
    best = min(ways.values(), key=lambda w: (w["interest"] if w["interest"] is not None else float("inf"), w["months"] or MAX_MONTHS))
    return {
        "as_of": today.isoformat(), "extra": _money(extra), "debts": [{k: v for k, v in d.items()} for d in debts],
        "minimums_only": base, "avalanche": ways["avalanche"], "snowball": ways["snowball"], "cheapest": best["order"],
        "minimums_total": _money(sum(d["minimum"] for d in debts)), "advances": advance_costs(session, today), "people": people(session),
        "note": "A minimum the app did not know is taken as 2% of the balance, at least $25; add the real one on Debts.", "not_advice": NOT_ADVICE,
    }


def people(session: Session) -> list[dict[str, Any]]:
    return list(get_setting(session, PEOPLE, []) or [])


def save_person_loan(session: Session, *, person: str, direction: str, amount: float, on: date, note: str = "", settled: bool = False, index: int | None = None) -> list[dict[str, Any]]:
    if direction not in ("lent", "borrowed"):
        raise ValueError("direction is lent or borrowed")
    if not person.strip() or not (0 < amount <= 1_000_000):
        raise ValueError("a name and an amount above zero")
    rows = people(session)
    row = {"person": person.strip()[:60], "direction": direction, "amount": _money(amount), "on": on.isoformat(), "note": note.strip()[:200], "settled": bool(settled)}
    if index is not None and 0 <= index < len(rows):
        rows[index] = row
    else:
        rows.append(row)
    set_setting(session, PEOPLE, rows[:200])
    return rows
