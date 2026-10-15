"""Readiness for a move, computed: the things that have to be true, each with its current value, its target,
whether it is met, and where to act. A buffer of one pay period's advances; two pay periods with no advance;
the card under thirty percent of its line on the statement day; the pay-later plans that end before the date;
the rent-to-gross ratio a landlord screens on; the mailbox connected; one window reconciled by hand. Figures
from the ledger, never a recommendation."""

from __future__ import annotations

import json
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Bill, Debt, InstallmentPlan, MailAccount, Paystub
from ..store import get_setting
from .borrowing import borrowing_summary
from .cadence import next_paydays, payday_position
from .projection import next_due
from .projections import NOT_ADVICE


def _money(v: float) -> float:
    return round(float(v), 2)


def gross_per_month(session: Session) -> tuple[float | None, str]:
    """Gross pay a month from the last stubs: a semi-monthly paycheck's two positions summed, else the last
    stub scaled by cadence."""
    stubs = [p for p in session.exec(select(Paystub).order_by(Paystub.pay_date.desc()).limit(6)).all() if p.gross]
    if not stubs:
        return None, "no stub with a gross yet"
    from ..models import Income

    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    cadence = (income.cadence if income else "biweekly") or "biweekly"
    if cadence == "semimonthly":
        mid = next((p.gross for p in stubs if payday_position(p.pay_date) == "mid"), None)
        end = next((p.gross for p in stubs if payday_position(p.pay_date) == "end"), None)
        if mid and end:
            return _money(float(mid) + float(end)), "the last two stubs, one of each half of the month"
    per_year = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}.get(cadence, 26)
    return _money(float(stubs[0].gross) * per_year / 12.0), f"the last stub, {per_year} a year"


def readiness(session: Session, today: date, *, move_date: date | None = None, new_rent: float | None = None, safe_to_spend: float | None = None) -> dict:
    items: list[dict] = []
    borrowing = borrowing_summary(session, today)
    from ..models import Income

    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    cadence = (income.cadence if income else "biweekly") or "biweekly"
    next_payday = income.next_date if income and income.next_date >= today else today + timedelta(days=14)
    paydays = next_paydays(next_payday, cadence, 8)

    # 1. a buffer of one pay period's advances
    typical = float((get_setting(session, "borrowing", {}) or {}).get("typical_per_period") or 0.0)
    if not typical:
        from .borrowing import borrowing_overview

        typical = float(borrowing_overview(session, today).get("typical_per_period") or 0.0)
    target_buffer = max(500.0, typical)
    buffer_now = float(safe_to_spend if safe_to_spend is not None else 0.0)
    items.append({"key": "buffer", "label": "A cushion of one pay period's advances", "value": _money(buffer_now), "target": _money(target_buffer), "met": buffer_now >= target_buffer,
                  "note": f"safe to spend until payday, against the {('typical ' + f'${typical:,.0f} advanced each period') if typical else 'minimum of $500'}", "link": "/today#goals"})

    # 2. two pay periods with no advance
    since = borrowing["since_payday"]["advances"]
    last_taken = None
    from ..models import Advance

    rows = session.exec(select(Advance).order_by(Advance.taken_on.desc()).limit(1)).all()
    if rows:
        last_taken = rows[0].taken_on
    clean_after = (last_taken or today) if last_taken else today
    after = [d for d in paydays if d > clean_after]
    clean_by = after[1] if len(after) > 1 else None
    met = since == 0 and (last_taken is None or (today - last_taken).days > 35)
    items.append({"key": "no_advances", "label": "Two pay periods with no advance", "value": f"{since} taken since payday" + (f"; last on {last_taken.strftime('%b %d')}" if last_taken else ""), "target": "none for two periods", "met": met,
                  "projected": clean_by.isoformat() if (not met and clean_by) else None, "note": "an advance taken now resets the two periods", "link": "/borrowing"})

    # 3. the card under 30% of its line on the statement day
    from .planned import planned_to_cards

    planned = planned_to_cards(session, today)
    cards = session.exec(select(Account).where(Account.active == True, Account.kind == "card")).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True, Debt.kind == "card")).all()  # noqa: E712
    for a in cards:
        debt = next((d for d in debts if d.account_id == a.id), None)
        limit = (debt.credit_limit if debt and debt.credit_limit else None) or a.credit_limit
        if not limit and not (a.balance or 0):
            continue
        balance = max(0.0, float(a.balance or 0.0) - planned.get(a.id, 0.0))
        use = balance / limit if limit else None
        statement = next_due(int(debt.statement_day), today).isoformat() if debt and debt.statement_day else None
        items.append({"key": f"card:{a.id}", "label": f"{a.name} under 30% of its line on the statement day", "value": f"{use * 100:.0f}% in use" if use is not None else f"${balance:,.0f} owed, line unknown", "target": "under 30%",
                      "met": use is not None and use < 0.30, "projected": statement, "note": ("paid down by a planned payment already counted" if planned.get(a.id) else "") + (f"; the statement closes {statement}" if statement else "; enter the line and the statement day on Debts"), "link": "/debts"})

    # 4. pay-later plans that end before the date
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    ending, running = [], []
    for p in plans:
        sched = sorted(json.loads(p.schedule or "[]"), key=lambda r: r["date"])
        ends = date.fromisoformat(sched[-1]["date"]) if sched else p.next_date + timedelta(days=p.cadence_days * max(0, p.remaining - 1))
        (ending if (move_date is None or ends <= move_date) else running).append((p, ends))
    monthly_after = _money(sum(p.installment * (30.0 / max(1, p.cadence_days)) for p, _ in running))
    items.append({"key": "plans", "label": "Pay-later plans ended before the date" if move_date else "Pay-later plans still running", "value": f"{len(ending)} end{' before it' if move_date else ' within view'}, {len(running)} run on at ${monthly_after:,.0f} a month", "target": "none running, or counted in the rent-to-pay figure",
                  "met": len(running) == 0, "note": ", ".join(f"{(p.merchant or p.provider)} ends {e.strftime('%b %d')}" for p, e in sorted(running, key=lambda x: x[1])[:3]), "link": "/borrowing#loans"})

    # 5. the rent-to-gross ratio a landlord screens on
    gross, basis = gross_per_month(session)
    rent_now = next((b.amount for b in session.exec(select(Bill).where(Bill.active == True)).all() if (b.category or "").lower() == "rent"), None)  # noqa: E712
    rent = float(new_rent) if new_rent is not None else (float(rent_now) if rent_now else None)
    if gross and rent:
        ratio = rent / gross
        items.append({"key": "rent_to_gross", "label": "Rent at most a third of gross pay", "value": f"{ratio * 100:.0f}% of ${gross:,.0f} a month (gross, from {basis})", "target": "33% or under; most landlords screen at three times the rent",
                      "met": ratio <= 1 / 3, "note": f"{'the new' if new_rent is not None else 'the current'} rent of ${rent:,.0f}", "link": "/paystubs"})
    else:
        items.append({"key": "rent_to_gross", "label": "Rent at most a third of gross pay", "value": "needs a paystub with a gross" if not gross else "needs the new rent", "target": "33% or under", "met": False, "note": basis, "link": "/paystubs"})

    # 6. the mailbox connected, so plans and bills fill themselves
    boxes = session.exec(select(MailAccount).where(MailAccount.active == True)).all()  # noqa: E712
    items.append({"key": "mailbox", "label": "A mailbox connected", "value": f"{len(boxes)} connected" if boxes else "none", "target": "one or both", "met": bool(boxes), "note": "Affirm's confirmations and Rocket Money's bill list fill the registers from it", "link": "/settings/connectors#mail"})

    # 7. one window reconciled by hand
    checked = get_setting(session, "reconciled", {}) or {}
    items.append({"key": "reconciled", "label": "One paycheck window checked by hand", "value": f"checked {checked['at'][:10]}" if checked.get("at") else "not yet", "target": "once, against the bank's own statement", "met": bool(checked.get("at")), "note": "tap when a window's lines matched the bank; the verdict is trusted after that", "link": "/today"})

    met = sum(1 for i in items if i["met"])
    return {"as_of": today.isoformat(), "move_date": move_date.isoformat() if move_date else None, "items": items, "met": met, "of": len(items), "ready": met == len(items), "not_advice": NOT_ADVICE}
