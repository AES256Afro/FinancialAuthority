"""Plan against what happened. Before each paycheck the app keeps the plan it made for it: the check, the bills,
the advances, what it keeps for the next one, and what was free to spend. When that paycheck's days are over, the
plan is set beside what the transactions show: what came in, what the bills took, and what everyday spending
took against what was free. The usual pace is shown as it was then and as it is now.

Also the week's recap for the phone: what went out in the last seven days, the three biggest kinds of spending, and
the usual week beside it."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Payment, Transaction
from ..store import get_setting, set_setting

PLANS = "recap.plans"
KEEP = 26


def _money(v: float) -> float:
    return round(float(v), 2)


def record_plan(session: Session, today: date) -> dict[str, Any] | None:
    """Keep today's plan for the coming paycheck. It is replaced each day until that paycheck lands, so the plan
    kept is the last one made before it."""
    from .snapshot import finance_snapshot

    snap = finance_snapshot(session, today)
    if not snap["windows"] or not snap["next_paycheck"].get("known", True):
        return None
    w = snap["windows"][0]
    if date.fromisoformat(w["start"]) < today:
        return None  # that paycheck's days have begun; its plan is already kept
    plans = get_setting(session, PLANS, {}) or {}
    plans[w["start"]] = {
        "start": w["start"], "end": w["end"], "paycheck": w["paycheck"], "bills": w["bills"]["total"], "advances": w["advances"]["total"],
        "set_aside": w["set_aside"]["total"], "free": w["safe"], "pace_per_day": snap["usual_pace"]["per_day"], "recorded": today.isoformat(),
    }
    for old in sorted(plans)[:-KEEP]:
        plans.pop(old)
    set_setting(session, PLANS, plans)
    return plans[w["start"]]


def what_happened(session: Session, plan: dict[str, Any], today: date) -> dict[str, Any]:
    start, end = date.fromisoformat(plan["start"]), min(date.fromisoformat(plan["end"]), today)
    rows = session.exec(select(Transaction).where(Transaction.date >= start, Transaction.date <= end, Transaction.pending == False)).all()  # noqa: E712
    income = sum(-t.amount for t in rows if t.is_income)
    bill_tx = {p.transaction_id for p in session.exec(select(Payment).where(Payment.paid_date >= start, Payment.paid_date <= end, Payment.status == "paid")).all() if p.transaction_id}
    bills = sum(t.amount for t in rows if t.id in bill_tx)
    everyday = sum(t.amount for t in rows if t.is_spending and t.amount > 0 and t.id not in bill_tx)
    by_cat: dict[str, float] = defaultdict(float)
    for t in rows:
        if t.is_spending and t.amount > 0 and t.id not in bill_tx:
            by_cat[t.category or "Uncategorized"] += t.amount
    over = everyday - plan["free"]
    return {
        "plan": plan, "came_in": _money(income), "bills_paid": _money(bills), "everyday": _money(everyday), "everyday_against_free": _money(over),
        "status": "good" if over <= 0 else "warn" if over <= max(25.0, 0.1 * max(plan["free"], 0.0)) else "crit",
        "word": f"${-over:,.2f} under what was free" if over <= 0 else f"${over:,.2f} over what was free",
        "paycheck_word": "landed as planned" if abs(income - plan["paycheck"]) <= max(20.0, 0.05 * plan["paycheck"]) else f"${income:,.2f} came in against ${plan['paycheck']:,.2f} planned",
        "top": [{"category": c, "spent": _money(v)} for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])[:5]],
        "finished": date.fromisoformat(plan["end"]) < today,
    }


def recap(session: Session, today: date) -> dict[str, Any]:
    from .snapshot import finance_snapshot

    plans = get_setting(session, PLANS, {}) or {}
    started = [p for k, p in sorted(plans.items(), reverse=True) if date.fromisoformat(k) <= today]
    windows = [what_happened(session, p, today) for p in started[:6]]
    pace_now = finance_snapshot(session, today)["usual_pace"]["per_day"]
    pace_then = started[1]["pace_per_day"] if len(started) > 1 else (started[0]["pace_per_day"] if started else None)
    return {
        "as_of": today.isoformat(), "windows": windows,
        "pace": {"now": pace_now, "then": pace_then, "change": _money(pace_now - pace_then) if pace_then is not None else None},
        "note": "The plan for each paycheck is kept the day before it lands; what happened comes from your transactions.",
    }


def week_recap(session: Session, today: date) -> dict[str, Any]:
    from .snapshot import finance_snapshot

    start = today - timedelta(days=7)
    rows = [t for t in session.exec(select(Transaction).where(Transaction.date > start, Transaction.date <= today)).all() if t.is_spending and t.amount > 0]
    by_cat: dict[str, float] = defaultdict(float)
    for t in rows:
        by_cat[t.category or "Uncategorized"] += t.amount
    snap = finance_snapshot(session, today)
    usual = snap["usual_pace"]["per_day"] * 7
    spent = sum(t.amount for t in rows)
    top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:3]
    text = f"This week: ${spent:,.0f} out, against a usual week of ${usual:,.0f}. " + ("Most went to " + ", ".join(f"{c} (${v:,.0f})" for c, v in top) + ". " if top else "") + f"Safe to spend now: {'-' if snap['safe_to_spend']['amount'] < 0 else ''}${abs(snap['safe_to_spend']['amount']):,.0f}."
    return {"from": (start + timedelta(days=1)).isoformat(), "to": today.isoformat(), "spent": _money(spent), "usual_week": _money(usual), "top": [{"category": c, "spent": _money(v)} for c, v in top], "text": text}
