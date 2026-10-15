"""A what-if laid over the real registers without touching them: a rent change from a date, an overlap month,
one-off costs with dates, recurring additions, bills that stop. Returns the low point and its date, the first
date the move passes, each check's load with the scenario, the shortfall the current pace would force, and the
monthly change against today. The same inputs and arithmetic as the snapshot, over a longer horizon; the page
and the models say it is not a recommendation."""

from __future__ import annotations

import copy
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Debt, Income, InstallmentPlan, RecurringSeries
from ..store import get_setting
from .cadence import next_paydays, payday_position
from .projection import assemble, cash_of, low_point, next_due, pending_installment_events, project, set_aside_of, wdate
from .projections import NOT_ADVICE
from .snapshot import usual_pace

HORIZON = 180
SEARCH_DAYS = 240


def _money(v: float) -> float:
    return round(float(v), 2)


def load_inputs(session: Session, today: date, horizon_days: int = HORIZON) -> dict:
    """The snapshot's own inputs: accounts and cash, the paycheck and its paydays, the registers, the pace."""
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    cash, _all = cash_of(accounts)
    buffer = float(get_setting(session, "buffer", 200.0))
    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    upcoming = sorted(((i.next_date, i) for i in incomes if i.next_date >= today), key=lambda p: p[0])
    next_pay = upcoming[0] if upcoming else None
    next_payday = next_pay[0] if next_pay else today + timedelta(days=14)
    cadence = (next_pay[1].cadence if next_pay else "biweekly") or "biweekly"
    paydays = [d for d in next_paydays(next_payday, cadence, 24) if d <= today + timedelta(days=horizon_days + 31)]

    def net_for(payday: date) -> float:
        if next_pay is None:
            return 0.0
        if cadence == "semimonthly" and payday_position(payday) == "end" and next_pay[1].expected_net_end is not None:
            return float(next_pay[1].expected_net_end)
        return float(next_pay[1].expected_net)

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    pending_streams = [r for r in session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all() if r.suggested_kind == "installment"]  # noqa: E712
    from .planned import planned_events, planned_to_cards

    pace = usual_pace(session, today, {b.name for b in bills})
    by_id = {a.id: a for a in accounts}
    card_mode = (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")
    return {
        "today": today, "cash": cash, "buffer": buffer, "has_income": next_pay is not None, "cadence": cadence, "paydays": paydays, "net_for": net_for, "pay_amounts": {d: net_for(d) for d in paydays},
        "bills": bills, "plans": plans, "advances": advances, "debts": debts, "accounts": by_id, "card_mode": card_mode,
        "planned": planned_to_cards(session, today), "extra": pending_installment_events(pending_streams, today, horizon_days=horizon_days) + planned_events(session, today), "pace": pace, "horizon_days": horizon_days,
    }


def baseline_events(inp: dict) -> list[dict]:
    return assemble(bills=inp["bills"], plans=inp["plans"], advances=inp["advances"], today=inp["today"], accounts=inp["accounts"], paydays=inp["paydays"], card_mode=inp["card_mode"], debts=inp["debts"], planned=inp["planned"],
                    extra=inp["extra"], card_pace_by_account=inp["pace"]["per_day_by_account"], horizon_days=inp["horizon_days"])["math"]


def rent_bill(bills: list[Bill], bill_id: int | None = None) -> Bill | None:
    if bill_id:
        return next((b for b in bills if b.id == bill_id), None)
    return next((b for b in bills if (b.category or "").lower() == "rent"), None) or next((b for b in sorted(bills, key=lambda b: -b.amount)), None)


def apply_scenario(events: list[dict], inp: dict, spec: dict, move_date: date) -> list[dict]:
    """The scenario's events over the baseline's: the rent changes from the move date (with an overlap month if
    asked), stopped bills end there, one-offs land on their days, recurring additions run monthly from the move."""
    today = inp["today"]
    horizon = today + timedelta(days=inp["horizon_days"])
    out = [copy.copy(e) for e in events]
    rent = rent_bill(inp["bills"], spec.get("rent_bill_id"))
    new_rent = spec.get("new_rent")
    overlap_months = int(spec.get("overlap_months") or 0)
    stop_ids = {int(x) for x in (spec.get("stop_bills") or [])}
    result: list[dict] = []
    for e in out:
        bill_id = e.get("bill_id")
        if bill_id in stop_ids and e["date"] >= move_date:
            continue
        if rent is not None and bill_id == rent.id and e["date"] >= move_date:
            if overlap_months and e["date"] < move_date + timedelta(days=31 * overlap_months):
                result.append({**e, "name": f"{e['name']} (overlap)", "scenario": "overlap"})
            if new_rent is not None:
                e = {**e, "amount": _money(new_rent), "name": f"{spec.get('name') or 'New home'} rent", "scenario": "rent"}
        result.append(e)
    for o in spec.get("one_offs") or []:
        when = date.fromisoformat(o["date"]) if isinstance(o.get("date"), str) else (o.get("date") or move_date)
        if today <= when <= horizon and float(o.get("amount") or 0) > 0:
            result.append({"date": when, "window_date": when, "name": o.get("name") or "One-off", "amount": _money(o["amount"]), "kind": "one_off", "autopay": False, "source": "scenario", "line": "housing", "paid_from": "due", "from_kind": "cash", "scenario": "one_off"})
    for r in spec.get("recurring") or []:
        start = date.fromisoformat(r["from"]) if isinstance(r.get("from"), str) and r.get("from") else move_date
        due_day = int(r.get("due_day") or start.day)
        d = next_due(due_day, max(start, today))
        while d <= horizon:
            result.append({"date": d, "window_date": d, "name": r.get("name") or "Added bill", "amount": _money(r["amount"]), "kind": "bill", "autopay": True, "source": "scenario", "line": "utilities", "paid_from": "due", "from_kind": "cash", "scenario": "recurring"})
            d = next_due(due_day, d + timedelta(days=1))
    result.sort(key=lambda e: e["date"])
    return result


def run_path(inp: dict, events: list[dict]) -> list[dict]:
    paydays = inp["paydays"]
    nxt = paydays[0] if paydays else inp["today"] + timedelta(days=14)
    following = paydays[1] if len(paydays) > 1 else nxt + timedelta(days=14)
    third = paydays[2] if len(paydays) > 2 else following + timedelta(days=14)
    next_window = [e for e in events if following <= wdate(e) < third and e["kind"] != "advance" and not e.get("scenario")]
    return project(inp["today"], cash=inp["cash"], buffer=inp["buffer"], expected_net=inp["net_for"](nxt), pay_dates=set(paydays), set_aside=set_aside_of(next_window), events=events, per_day=inp["pace"]["per_day_cash"], days=inp["horizon_days"], pay_amounts=inp["pay_amounts"], reserved_for={(e.get("source"), e["date"]) for e in next_window})


def windows_for(inp: dict, events: list[dict]) -> list[dict]:
    """Each check's load over the horizon: what its window carries, by the window date of each event."""
    paydays = inp["paydays"]
    out = []
    for i, start in enumerate(paydays[:-1]):
        end = paydays[i + 1] - timedelta(days=1)
        mine = [e for e in events if start <= wdate(e) <= end]
        out.append({"start": start.isoformat(), "end": end.isoformat(), "paycheck": _money(inp["net_for"](start)), "total": _money(sum(e["amount"] for e in mine)), "items": [{**e, "date": e["date"].isoformat(), "window_date": wdate(e).isoformat()} for e in sorted(mine, key=lambda e: -e["amount"])[:8]]})
    before = [e for e in events if inp["today"] <= e["date"] and wdate(e) < (paydays[0] if paydays else inp["today"])]
    return [{"start": inp["today"].isoformat(), "end": ((paydays[0] - timedelta(days=1)) if paydays else inp["today"]).isoformat(), "paycheck": 0.0, "total": _money(sum(e["amount"] for e in before)), "items": [{**e, "date": e["date"].isoformat(), "window_date": wdate(e).isoformat()} for e in sorted(before, key=lambda e: -e["amount"])[:8]], "before_payday": True}] + out


def run_scenario(session: Session, today: date, spec: dict) -> dict:
    inp = load_inputs(session, today)
    move_date = date.fromisoformat(spec["move_date"]) if isinstance(spec.get("move_date"), str) else (spec.get("move_date") or today + timedelta(days=30))
    base_events = baseline_events(inp)
    base_path = run_path(inp, base_events)
    scen_events = apply_scenario(base_events, inp, spec, move_date)
    scen_path = run_path(inp, scen_events)
    base_low, scen_low = low_point(base_path), low_point(scen_path)
    # The first date the move passes: the same scenario moved later, a day at a time, until the honest line holds.
    first_passes = None
    if scen_low["value"] >= 0:
        first_passes = move_date
    else:
        for k in range(1, SEARCH_DAYS):
            trial = move_date + timedelta(days=k)
            if trial > today + timedelta(days=inp["horizon_days"] - 1):
                break
            if low_point(run_path(inp, apply_scenario(base_events, inp, spec, trial)))["value"] >= 0:
                first_passes = trial
                break
    rent = rent_bill(inp["bills"], spec.get("rent_bill_id"))
    old_rent = float(rent.amount) if rent else 0.0
    new_rent = float(spec["new_rent"]) if spec.get("new_rent") is not None else old_rent
    monthly_change = _money((new_rent - old_rent) + sum(float(r.get("amount") or 0) for r in spec.get("recurring") or []) - sum(float(b.amount) for b in inp["bills"] if b.id in {int(x) for x in (spec.get("stop_bills") or [])} and b.cadence == "monthly"))
    one_offs_total = _money(sum(float(o.get("amount") or 0) for o in spec.get("one_offs") or []))
    base_windows = {w["start"]: w for w in windows_for(inp, base_events)}
    scen_windows = windows_for(inp, scen_events)
    for w in scen_windows:
        b = base_windows.get(w["start"])
        w["baseline_total"] = b["total"] if b else 0.0
        w["delta"] = _money(w["total"] - (b["total"] if b else 0.0))
    shortfall = _money(max(0.0, -scen_low["value"]))
    short_days = sum(1 for p in scen_path if p["at_pace"] < 0)
    caveats = []
    if not inp["has_income"]:
        caveats.append("No paycheck is on file, so no income is counted; the verdict cannot hold until one is named.")
    if inp["pace"]["transactions"] < 20:
        caveats.append("The usual pace rests on few transactions; the honest line is rough until more history lands.")
    verdict = "passes" if scen_low["value"] >= 0 else "short"
    summary = (f"A move on {move_date.strftime('%b %d')} holds: the honest line's low point is {scen_low['value']:,.0f} on {scen_low['date']}." if verdict == "passes"
               else f"A move on {move_date.strftime('%b %d')} runs {shortfall:,.0f} short at the usual pace, lowest on {scen_low['date']}" + (f"; the first date that holds is {first_passes.strftime('%b %d')}." if first_passes else "; no date inside the horizon holds at this pace."))
    return {
        "as_of": today.isoformat(), "name": spec.get("name") or "Scenario", "move_date": move_date.isoformat(), "horizon_days": inp["horizon_days"], "cadence": inp["cadence"],
        "verdict": verdict, "summary": summary, "first_passes": first_passes.isoformat() if first_passes else None,
        "baseline": {"low": base_low, "path": [{"date": p["date"], "at_pace": p["at_pace"]} for p in base_path]},
        "scenario": {"low": scen_low, "path": [{"date": p["date"], "at_pace": p["at_pace"]} for p in scen_path], "shortfall": shortfall, "short_days": short_days},
        "windows": scen_windows, "monthly_change": monthly_change, "one_offs_total": one_offs_total, "old_rent": _money(old_rent), "new_rent": _money(new_rent),
        "pace": {"per_day_cash": inp["pace"]["per_day_cash"], "per_day_card": inp["pace"]["per_day_card"]}, "buffer": inp["buffer"], "cash": _money(inp["cash"]),
        "caveats": caveats, "sources": ["snapshot:projection", *(f"bill:{rent.id}" for _ in [0] if rent)],
        "not_advice": NOT_ADVICE + " A scenario is the arithmetic of your own checks against a date; it is not a recommendation to move, or not to.",
    }
