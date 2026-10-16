"""Two of the three questions: should I buy this, and which billing dates should move. Both are pure
functions over the registers and the projection; every figure carries its source."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Income, InstallmentPlan, RecurringSeries
from ..store import get_setting
from .cadence import next_paydays, payday_position
from .projection import assemble, build_events, card_events, cash_of, low_point, pending_installment_events, project, projection_caveats, split_windows
from .snapshot import finance_snapshot, usual_pace

NOT_ADVICE = "Computed by the engine from the registers and the projection. Not financial advice."
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 21: "21st", 22: "22nd", 23: "23rd"}


def ordinal(n: int) -> str:
    return ORDINALS.get(n, f"{n}th")


def _charges(amount: float, on: date, installments: int | None) -> list[tuple[date, float]]:
    if installments and installments > 1:
        part = round(amount / installments, 2)
        return [(on + timedelta(days=14 * i), part) for i in range(installments)]
    return [(on, round(amount, 2))]


def _apply(projection: list[dict], charges: list[tuple[date, float]]) -> list[dict]:
    out = []
    for p in projection:
        d = date.fromisoformat(p["date"])
        taken = sum(a for when, a in charges if when <= d)
        out.append({**p, "at_pace": round(p["at_pace"] - taken, 2), "available": round(p["available"] - taken, 2)})
    return out


def _first_break(after: list[dict], before: list[dict], start: date) -> tuple[dict, dict] | None:
    """The first day the purchase pushes the honest line under zero, or deeper under it than it already was."""
    for a, b in zip(after, before, strict=True):
        if date.fromisoformat(a["date"]) < start:
            continue
        if a["at_pace"] < min(0.0, b["at_pace"]) - 0.005:
            return a, b
    return None


def purchase_check(session: Session, today: date, amount: float, *, on: date | None = None, installments: int | None = None) -> dict:
    """Yes, wait or no: whether the purchase keeps the honest line where it is above zero, and no deeper where
    it is already under, through the horizon; the first thing it would break; the first date it would pass."""
    on = on or today
    amount = round(float(amount), 2)
    snap = finance_snapshot(session, today)
    projection = snap["projection"]
    horizon_end = date.fromisoformat(projection[-1]["date"])
    charges = _charges(amount, on, installments)
    after = _apply(projection, charges)
    from_on = [p for p in after if date.fromisoformat(p["date"]) >= on] or after
    low = low_point(from_on)
    events_by_day: dict[str, list[dict]] = {}
    for e in snap["calendar"]["events"]:
        if e["kind"] != "paycheck":
            events_by_day.setdefault(e["date"], []).append(e)
    breaks = None
    hit = _first_break(after, projection, on)
    if hit is not None:
        first_bad, was = hit
        on_that_day = events_by_day.get(first_bad["date"], [])
        upcoming = next((e for p in from_on if p["date"] >= first_bad["date"] for e in events_by_day.get(p["date"], [])), None)
        target = on_that_day[0] if on_that_day else upcoming
        breaks = {
            "date": first_bad["date"],
            "name": target["name"] if target else "the buffer",
            "event_date": target["date"] if target else first_bad["date"],
            "source": target["source"] if target else "setting:buffer",
            "shortfall": round(-first_bad["at_pace"], 2),
            "already_short": round(-was["at_pace"], 2) if was["at_pace"] < 0 else 0.0,
        }
    verdict = "yes" if hit is None else "no"
    first_ok = None
    if verdict == "no":
        for p in projection:
            d = date.fromisoformat(p["date"])
            if d <= on:
                continue
            if _first_break(_apply(projection, _charges(amount, d, installments)), projection, d) is None:
                first_ok = d.isoformat()
                verdict = "wait"
                break
    safe = snap["safe_to_spend"]
    return {
        "as_of": today.isoformat(),
        "amount": amount,
        "on": on.isoformat(),
        "installments": installments if installments and installments > 1 else None,
        "charges": [{"date": d.isoformat(), "amount": a} for d, a in charges],
        "verdict": verdict,
        "breaks": breaks,
        "first_ok_date": first_ok,
        "low_point": {"date": low["date"], "at_pace": low["value"]},
        "low_point_before": low_point([p for p in projection if date.fromisoformat(p["date"]) >= on] or projection),
        "caveats": snap["caveats"],
        "fits_safe_to_spend": amount <= safe["amount"],
        "safe_to_spend": safe,
        "horizon": horizon_end.isoformat(),
        "sources": sorted({"snapshot:projection", "snapshot:safe_to_spend", *([breaks["source"]] if breaks else [])}),
        "not_advice": NOT_ADVICE,
    }


def _low_finder(session: Session, today: date):
    """The registers as they stand, and a function giving the sixty-day low point with some bills' due days
    changed. Shared by the suggestions and by a single move checked from the paycheck pictures."""
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    cash, _all_cash = cash_of(accounts)
    buffer = float(get_setting(session, "buffer", 200.0))
    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    from .cadence import next_unpaid_payday

    upcoming = sorted(((next_unpaid_payday(session, i, today), i) for i in incomes), key=lambda p: p[0])
    next_payday = upcoming[0][0] if upcoming else today + timedelta(days=14)
    cadence = (upcoming[0][1].cadence if upcoming else "biweekly") or "biweekly"
    paydays = next_paydays(next_payday, cadence, 5)

    def net_for(payday: date) -> float:
        if not upcoming:
            return 0.0
        income = upcoming[0][1]
        if cadence == "semimonthly" and payday_position(payday) == "end" and income.expected_net_end is not None:
            return float(income.expected_net_end)
        return float(income.expected_net)

    expected_net = net_for(next_payday)
    following = paydays[1]
    pay_dates = set(paydays[:4])
    pay_amounts = {d: net_for(d) for d in paydays[:4]}
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    pace = usual_pace(session, today, {b.name for b in bills})
    unconfirmed_rows = session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all()  # noqa: E712
    pending_streams = [r for r in unconfirmed_rows if r.suggested_kind == "installment"]
    caveats = projection_caveats(has_income=bool(upcoming), bills=len(bills), unnamed=len(unconfirmed_rows) - len(pending_streams), pending_installments=len(pending_streams))
    pending_rows = pending_installment_events(pending_streams, today)

    from ..models import Debt

    from .planned import planned_events, planned_to_cards

    card_mode = (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")
    debts_rows = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    planned_map = planned_to_cards(session, today)
    extra = pending_rows + planned_events(session, today)
    by_id = {a.id: a for a in accounts}

    def low_for(due_days: dict[int, int]) -> dict:
        events = assemble(bills=bills, plans=plans, advances=advances, today=today, accounts=by_id, paydays=paydays, card_mode=card_mode, debts=debts_rows, planned=planned_map, extra=extra, card_pace_by_account=pace["per_day_by_account"], due_days=due_days)["math"]
        windows = split_windows(events, today, next_payday, following, paydays[2])
        path = project(today, cash=cash, buffer=buffer, expected_net=expected_net, pay_dates=pay_dates, set_aside=windows["set_aside"], events=events, per_day=pace["per_day_cash"], pay_amounts=pay_amounts, reserved_for={(e.get("source"), e["date"]) for e in windows["next_window"]})
        return low_point(path)

    return low_for, bills, caveats


def date_move_check(session: Session, today: date, bill_id: int, day: int) -> dict:
    """What moving one bill's due day does to the sixty-day low point, with the draft request to the biller.
    The app never moves anything; it drafts, and the owner asks."""
    low_for, bills, caveats = _low_finder(session, today)
    bill = next((b for b in bills if b.id == bill_id), None)
    if bill is None:
        raise KeyError(bill_id)
    day = max(1, min(28, int(day)))
    before = low_for({})
    after = low_for({bill.id: day})
    change = round(after["value"] - before["value"], 2)
    return {
        "bill_id": bill.id, "name": bill.name, "from_day": bill.due_day, "to_day": day, "movable": bool(bill.movable),
        "low_before": before["value"], "low_before_date": before["date"], "low_after": after["value"], "low_after_date": after["date"], "change": change,
        "verdict": "better" if change >= 1.0 else ("worse" if change <= -1.0 else "same"),
        "instruction": f"Ask {bill.name} to move the due date from the {ordinal(bill.due_day)} to the {ordinal(day)}.",
        "draft": (f"Hello,\n\nI would like to move my billing date from the {ordinal(bill.due_day)} of each month to the {ordinal(day)}, starting with the next cycle. "
                  f"Please confirm the change and any one-time adjustment to the next bill.\n\nThank you."),
        "source": f"bill:{bill.id}", "caveats": caveats, "not_advice": NOT_ADVICE,
    }


def billing_date_suggestions(session: Session, today: date) -> dict:
    """For every movable bill, the due day that lifts the sixty-day low point the most, each change judged on
    its own against the registers as they stand. A change that does not lift the low point is not offered."""
    low_for, bills, caveats = _low_finder(session, today)
    baseline = low_for({})
    suggestions = []
    for b in bills:
        if not b.movable:
            continue
        best_day, best_low = b.due_day, baseline["value"]
        for day in range(1, 29):
            if day == b.due_day:
                continue
            low = low_for({b.id: day})
            if low["value"] > best_low + 0.005 or (abs(low["value"] - best_low) <= 0.005 and best_day != b.due_day and abs(day - b.due_day) < abs(best_day - b.due_day)):
                best_day, best_low = day, low["value"]
        if best_day != b.due_day and best_low - baseline["value"] >= 1.0:
            suggestions.append({
                "bill_id": b.id, "name": b.name, "amount": b.amount, "from_day": b.due_day, "to_day": best_day,
                "low_before": baseline["value"], "low_after": round(best_low, 2), "improvement": round(best_low - baseline["value"], 2),
                "instruction": f"Ask {b.name} to move the due date from the {ordinal(b.due_day)} to the {ordinal(best_day)}.",
                "draft": (f"Hello,\n\nI would like to move my billing date from the {ordinal(b.due_day)} of each month to the {ordinal(best_day)}, starting with the next cycle. "
                          f"Please confirm the change and any one-time adjustment to the next bill.\n\nThank you."),
                "source": f"bill:{b.id}",
            })
    suggestions.sort(key=lambda s: (-s["improvement"], s["name"]))

    # Together: take the best move, then the best next move given it, until nothing lifts the low point a dollar.
    plan: list[dict] = []
    chosen: dict[int, int] = {}
    current = baseline["value"]
    movable_ids = [b.id for b in bills if b.movable]
    for _step in range(len(movable_ids)):
        best = None
        for b in bills:
            if not b.movable or b.id in chosen:
                continue
            for day in range(1, 29):
                if day == b.due_day:
                    continue
                low = low_for({**chosen, b.id: day})
                if best is None or low["value"] > best[2] + 0.005:
                    best = (b, day, low["value"], low["date"])
        if best is None or best[2] - current < 1.0:
            break
        b, day, value, when = best
        chosen[b.id] = day
        plan.append({"bill_id": b.id, "name": b.name, "from_day": b.due_day, "to_day": day, "low_after": round(value, 2), "low_date": when, "improvement": round(value - current, 2), "source": f"bill:{b.id}"})
        current = value
    return {
        "as_of": today.isoformat(),
        "baseline": {"low": baseline["value"], "low_date": baseline["date"]},
        "suggestions": suggestions,
        "plan": plan,
        "plan_low": round(current, 2),
        "movable": sum(1 for b in bills if b.movable),
        "sources": ["snapshot:projection", *(s["source"] for s in suggestions)],
        "caveats": caveats,
        "drafts_only": "The app drafts the request; it never sends anything. Copy it to the biller yourself, or approve a send once sending exists.",
        "not_advice": NOT_ADVICE,
    }
