"""The pure pieces of the projection, shared by the snapshot, the purchase check and the date optimiser:
events from the registers, the paycheck windows, the set-aside rule, and the daily path for sixty days."""

from __future__ import annotations

from datetime import date, timedelta

from ..models import Advance, Bill, InstallmentPlan

LARGE_BILL = 500.0  # a bill at or over this is set aside by halves across two paychecks
WINDOW_DAYS = 14
HORIZON_DAYS = 75


def _money(v: float) -> float:
    return round(float(v), 2)


def next_due(due_day: int, today: date) -> date:
    candidate = today.replace(day=min(due_day, 28))
    if candidate < today:
        month = today.month % 12 + 1
        year = today.year + (1 if month == 1 else 0)
        candidate = date(year, month, min(due_day, 28))
    return candidate


def line_of(category: str, kind: str) -> str:
    """Which line a bill rides on the transit map: housing, utilities or debt."""
    if kind in ("installment", "advance"):
        return "debt"
    c = (category or "").lower()
    if c in {"rent", "home", "housing", "insurance", "mortgage", "hoa"} or any(w in c for w in ("rent", "insurance", "mortgage")):
        return "housing"
    if c in {"loan payment", "credit card payment", "loans", "debt"} or "loan" in c or "credit card" in c:
        return "debt"
    return "utilities"


def build_events(bills: list[Bill], plans: list[InstallmentPlan], advances: list[Advance], today: date, *, due_days: dict[int, int] | None = None) -> list[dict]:
    """Every dated obligation for the horizon. `due_days` overrides a bill's due day (the optimiser's what-if)."""
    events: list[dict] = []
    horizon = today + timedelta(days=HORIZON_DAYS)
    for b in bills:
        due_day = (due_days or {}).get(b.id, b.due_day)
        d = next_due(due_day, today)
        while d <= horizon:
            events.append({"date": d, "name": b.name, "amount": _money(b.amount), "kind": b.kind, "autopay": b.autopay, "source": f"bill:{b.id}", "line": line_of(b.category, b.kind), "bill_id": b.id})
            d = next_due(due_day, d + timedelta(days=1))
    for p in plans:
        d, left = p.next_date, p.remaining
        while left > 0 and d <= today + timedelta(days=90):
            events.append({"date": d, "name": f"{p.merchant} {p.count - left + 1} of {p.count}", "amount": _money(p.installment), "kind": "installment", "autopay": True, "source": f"plan:{p.id}", "line": "debt"})
            d, left = d + timedelta(days=p.cadence_days), left - 1
    for a in advances:
        events.append({"date": a.repay_date, "name": f"{a.provider} repays", "amount": _money(a.amount + a.fee), "kind": "advance", "autopay": True, "source": f"advance:{a.id}", "line": "debt"})
    events.sort(key=lambda e: e["date"])
    return events


def set_aside_of(items: list[dict]) -> float:
    return sum(e["amount"] for e in items if e["amount"] >= LARGE_BILL) / 2 + sum(e["amount"] for e in items if e["amount"] < LARGE_BILL)


def split_windows(events: list[dict], today: date, next_payday: date, following_payday: date) -> dict:
    """What is due before payday, in the window after it, in the window after that, and the advances due."""
    before = [e for e in events if today <= e["date"] < next_payday and e["kind"] != "advance"]
    window = [e for e in events if next_payday <= e["date"] < following_payday and e["kind"] != "advance"]
    next_window = [e for e in events if following_payday <= e["date"] < following_payday + timedelta(days=WINDOW_DAYS) and e["kind"] != "advance"]
    advances_due = [e for e in events if e["kind"] == "advance" and e["date"] <= next_payday]
    return {"before": before, "window": window, "next_window": next_window, "advances_due": advances_due, "set_aside": set_aside_of(next_window)}


def project(today: date, *, cash: float, buffer: float, expected_net: float, pay_dates: set[date], set_aside: float, events: list[dict], per_day: float, days: int = 60) -> list[dict]:
    """A daily path: `available` is cash minus the buffer minus what is reserved, if nothing else is spent;
    `at_pace` also spends the usual everyday amount each day, which is the honest line."""
    following_payday = sorted(pay_dates)[1] if len(pay_dates) > 1 else today + timedelta(days=28)
    by_day: dict[date, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    balance, reserved, spent = cash, 0.0, 0.0
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        if d in pay_dates and expected_net:
            balance += expected_net
            reserved = set_aside
        for e in by_day.get(d, []):
            balance -= e["amount"]
            if d >= following_payday and reserved:
                reserved = max(0.0, reserved - e["amount"])
        if i > 0:
            spent += per_day
        available = balance - buffer - reserved
        out.append({"date": d.isoformat(), "available": _money(available), "at_pace": _money(available - spent), "cash": _money(balance)})
    return out


def low_point(projection: list[dict], key: str = "at_pace") -> dict:
    low = min(projection, key=lambda p: p[key])
    return {"date": low["date"], "value": low[key]}
