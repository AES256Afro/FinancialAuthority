"""finance.snapshot, version 0: balances, the next paycheck, what is due before it, and a first safe-to-spend.

Version 0 exists so the screens and the tools have something true to show from M0. M5 replaces the
arithmetic with the full projection, set-asides and goals; the shape of the result stays.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Debt, Income, InstallmentPlan, RecurringSeries, Transaction
from ..store import get_setting

CASH_KINDS = {"checking", "savings", "cash"}
PACE_DAYS = 56
OBLIGATION_CATEGORIES = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities"}


def usual_pace(session: Session, today: date, bill_names: set[str]) -> dict:
    """Everyday spending per day over the last eight weeks: what is spent that is not a bill."""
    since = today - timedelta(days=PACE_DAYS)
    rows = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.date < today)).all()
    names = {n.lower() for n in bill_names}
    total = 0.0
    counted = 0
    for t in rows:
        if not t.is_spending or t.amount <= 0 or t.category in OBLIGATION_CATEGORIES or t.merchant.lower() in names:
            continue
        total += t.amount
        counted += 1
    per_day = total / PACE_DAYS if counted else 0.0
    return {"per_day": _money(per_day), "per_window": _money(per_day * 14), "basis_days": PACE_DAYS, "transactions": counted}


def next_due(due_day: int, today: date) -> date:
    day = min(due_day, 28)
    candidate = date(today.year, today.month, day)
    if candidate < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        candidate = date(y, m, day)
    return candidate


def _money(v: float) -> float:
    return round(float(v), 2)


def finance_snapshot(session: Session, today: date) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    cash = sum((a.balance or 0.0) for a in accounts if a.kind in CASH_KINDS)
    buffer = float(get_setting(session, "buffer", 200.0))

    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    upcoming = sorted(((i.next_date, i) for i in incomes if i.next_date >= today), key=lambda p: p[0])
    next_pay = upcoming[0] if upcoming else None
    next_payday = next_pay[0] if next_pay else today + timedelta(days=14)
    following_payday = next_payday + timedelta(days=14)
    expected_net = next_pay[1].expected_net if next_pay else 0.0

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    unconfirmed = len(session.exec(select(RecurringSeries.id).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all())  # noqa: E712

    events: list[dict] = []
    horizon = today + timedelta(days=75)
    for b in bills:
        d = next_due(b.due_day, today)
        while d <= horizon:
            events.append({"date": d, "name": b.name, "amount": _money(b.amount), "kind": b.kind, "autopay": b.autopay, "source": f"bill:{b.id}"})
            d = next_due(b.due_day, d + timedelta(days=1))
    for p in plans:
        d, left = p.next_date, p.remaining
        while left > 0 and d <= today + timedelta(days=90):
            events.append({"date": d, "name": f"{p.merchant} {p.count - left + 1} of {p.count}", "amount": _money(p.installment), "kind": "installment", "autopay": True, "source": f"plan:{p.id}"})
            d, left = d + timedelta(days=p.cadence_days), left - 1
    for a in advances:
        events.append({"date": a.repay_date, "name": f"{a.provider} repays", "amount": _money(a.amount + a.fee), "kind": "advance", "autopay": True, "source": f"advance:{a.id}"})
    events.sort(key=lambda e: e["date"])

    before = [e for e in events if today <= e["date"] < next_payday and e["kind"] != "advance"]
    window = [e for e in events if next_payday <= e["date"] < following_payday and e["kind"] != "advance"]
    next_window = [e for e in events if following_payday <= e["date"] < following_payday + timedelta(days=14) and e["kind"] != "advance"]
    advances_due = [e for e in events if e["kind"] == "advance" and e["date"] <= next_payday]

    due_before = sum(e["amount"] for e in before)
    window_total = sum(e["amount"] for e in window)
    set_aside = sum(e["amount"] for e in next_window if e["amount"] >= 500) / 2 + sum(e["amount"] for e in next_window if e["amount"] < 500)
    advances_total = sum(e["amount"] for e in advances_due)
    safe = cash - due_before + expected_net - advances_total - window_total - set_aside - buffer

    # A daily path for sixty days: `available` is cash minus the buffer minus what is reserved, if nothing
    # else is spent; `at_pace` also spends the usual everyday amount each day, which is the honest line.
    pace = usual_pace(session, today, {b.name for b in bills})
    projection = []
    balance = cash
    reserved = 0.0
    spent = 0.0
    by_day: dict[date, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    pay_dates = {next_payday, following_payday, following_payday + timedelta(days=14), following_payday + timedelta(days=28)}
    for i in range(60):
        d = today + timedelta(days=i)
        if d in pay_dates and expected_net:
            balance += expected_net
            reserved = set_aside
        for e in by_day.get(d, []):
            balance -= e["amount"]
            if d >= following_payday and reserved:
                reserved = max(0.0, reserved - e["amount"])
        if i > 0:
            spent += pace["per_day"]
        available = balance - buffer - reserved
        projection.append({"date": d.isoformat(), "available": _money(available), "at_pace": _money(available - spent), "cash": _money(balance)})

    return {
        "version": "snapshot.v0",
        "as_of": today.isoformat(),
        "accounts": [
            {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector}
            for a in accounts
        ],
        "cash": _money(cash),
        "buffer": _money(buffer),
        "next_paycheck": {"date": next_payday.isoformat(), "days": (next_payday - today).days, "amount": _money(expected_net), "source": f"income:{next_pay[1].id}" if next_pay else None},
        "due_before_payday": {"total": _money(due_before), "items": [{**e, "date": e["date"].isoformat()} for e in before]},
        "advances": {"total": _money(advances_total), "items": [{**e, "date": e["date"].isoformat()} for e in advances_due]},
        "window": {"start": next_payday.isoformat(), "end": (following_payday - timedelta(days=1)).isoformat(), "total": _money(window_total), "items": [{**e, "date": e["date"].isoformat()} for e in window]},
        "set_aside": {"total": _money(set_aside), "items": [{**e, "date": e["date"].isoformat()} for e in next_window]},
        "safe_to_spend": {"amount": _money(safe), "through": (following_payday - timedelta(days=1)).isoformat(), "version": "v0"},
        "usual_pace": pace,
        "debts": [{"id": d.id, "name": d.name, "kind": d.kind, "balance": d.balance, "apr": d.apr, "minimum": d.minimum, "due_day": d.due_day} for d in debts],
        "pending": {"confirmations": unconfirmed, "approvals": 0},
        "projection": projection,
        "not_advice": "Computed by the engine from the registers above. Not financial advice.",
    }
