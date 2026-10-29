"""Two of the three questions: should I buy this, and which billing dates should move. Both are pure
functions over the registers and the projection; every figure carries its source."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Income, InstallmentPlan, RecurringSeries
from ..store import get_setting
from .cadence import next_paydays
from .projection import assemble, build_events, card_events, cash_of, low_point, pending_installment_events, project, projection_caveats, split_windows
from .snapshot import finance_snapshot, usual_pace

NOT_ADVICE = "Computed by the engine from the registers and the projection. Not financial advice."
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 21: "21st", 22: "22nd", 23: "23rd"}


def ordinal(n: int) -> str:
    return ORDINALS.get(n, f"{n}th")


def _charges(amount: float, on: date, installments: int | None) -> list[tuple[date, float]]:
    if installments and installments > 1:
        part = round(amount / installments, 2)
        last = round(amount - part * (installments - 1), 2)
        return [(on + timedelta(days=14 * i), part if i < installments - 1 else last) for i in range(installments)]
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
    it is already under, through the horizon; the first thing it would break; the first date it would pass.
    "outside" when a charge falls past the end of the line, where the plan cannot see whether it holds."""
    on = on or today
    amount = round(float(amount), 2)
    snap = finance_snapshot(session, today)
    projection = snap["projection"]
    horizon_end = date.fromisoformat(projection[-1]["date"])
    charges = _charges(amount, on, installments)
    if snap.get("no_balance"):
        # No bank has reported a balance: the line starts from an unknown (its figures are None), so the check cannot
        # say yes or no. It says it cannot tell yet, and the caveat says why.
        projection = [{**p, "available": 0.0, "at_pace": 0.0, "cash": 0.0} for p in projection]  # a frame of dates only; nothing below is shown
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
    # A charge past the end of the line is one the plan cannot see: nothing past it is known to hold, so it is
    # never a yes. What breaks inside the line is still a no.
    after_line = [(d, a) for d, a in charges if d > horizon_end]
    verdict = "no" if hit is not None else "outside" if after_line else "yes"
    first_ok = None
    if verdict == "no":
        for p in projection:
            d = date.fromisoformat(p["date"])
            if d <= on:
                continue
            trial = _charges(amount, d, installments)
            if trial[-1][0] > horizon_end:
                break  # from here the last charge falls past what the plan can see
            if _first_break(_apply(projection, trial), projection, d) is None:
                first_ok = d.isoformat()
                verdict = "wait"
                break
    safe = snap["safe_to_spend"]
    if snap.get("no_balance"):
        # No bank has reported a balance: the line starts from an unknown, so the check cannot say yes or no. It says
        # it cannot tell yet, and the caveat says why.
        verdict, breaks, first_ok, after_line = "outside", None, None, []
    no_balance = bool(snap.get("no_balance"))
    # What the purchase adds to each of the next three paychecks, as PLAN section 7 promises for the pay-in-four variant.
    by_paycheck = [{"payday": w["start"], "through": w["end"], "adds": round(sum(a for d, a in charges if w["start"] <= d.isoformat() <= w["end"]), 2)} for w in snap["windows"][:3]]
    return {
        "as_of": today.isoformat(),
        "amount": amount,
        "on": on.isoformat(),
        "installments": installments if installments and installments > 1 else None,
        "charges": [{"date": d.isoformat(), "amount": a} for d, a in charges],
        "by_paycheck": by_paycheck,
        "verdict": verdict,
        "breaks": breaks,
        "outside": {"horizon": horizon_end.isoformat(), "charges": [{"date": d.isoformat(), "amount": a} for d, a in after_line],
                    "text": f"The plan sees through {horizon_end:%b} {horizon_end.day}; {len(after_line)} charge{'s' if len(after_line) != 1 else ''} fall{'' if len(after_line) != 1 else 's'} after it, which it cannot check."} if after_line else None,
        "first_ok_date": first_ok,
        "low_point": {"date": None, "at_pace": None} if no_balance else {"date": low["date"], "at_pace": low["value"]},
        "low_point_before": None if no_balance else low_point([p for p in projection if date.fromisoformat(p["date"]) >= on] or projection),
        "caveats": snap["caveats"],
        "fits_safe_to_spend": None if safe["amount"] is None else amount <= safe["amount"],
        "no_balance": bool(snap.get("no_balance")),
        "safe_to_spend": safe,
        "horizon": horizon_end.isoformat(),
        "sources": sorted({"snapshot:projection", "snapshot:safe_to_spend", *([breaks["source"]] if breaks else [])}),
        "not_advice": NOT_ADVICE,
    }


def _low_finder(session: Session, today: date):
    """The registers as they stand, and a function giving the sixty-day low point with some bills' due days
    changed. Shared by the suggestions and by a single move checked from the paycheck pictures.

    A moved bill keeps this cycle's charge on its old day and moves from the next cycle, as the request to the
    biller asks. The low point is judged over whole pay periods only, up to the last payday inside the sixty days:
    a charge pushed past that payday is kept on the day before it (see projection._moved_dates), so a move never
    looks better by pushing a charge out of view."""
    from .projection import household_accounts

    accounts = household_accounts(session)  # as the snapshot takes them: a second person's own account is not the plan's
    cash, _all_cash = cash_of(accounts)
    buffer = float(get_setting(session, "buffer", 200.0))
    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    from .billdates import unanchored
    from .borrowing import plan_covers_series
    from .cadence import pay_schedule
    from .payments import paid_periods, unposted_periods
    from .projection import HORIZON_DAYS

    # Every paycheck on one schedule, as the snapshot takes it: each payday of each, and what lands on it.
    schedule = pay_schedule(session, incomes, today, today + timedelta(days=HORIZON_DAYS + 31), at_least=5)
    next_payday = schedule["next"]
    paydays = schedule["paydays"]

    def net_for(payday: date) -> float:
        return schedule["amounts"].get(payday, 0.0)

    expected_net = net_for(next_payday)
    following = paydays[1]
    pay_dates = set(paydays)
    pay_amounts = {d: net_for(d) for d in paydays}
    from .projection import paid_by_household

    bills = paid_by_household(session, session.exec(select(Bill).where(Bill.active == True)).all())  # noqa: E712 - as the snapshot takes them
    plans = paid_by_household(session, session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all())  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    pace = usual_pace(session, today, {b.name for b in bills})
    unconfirmed_rows = session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all()  # noqa: E712
    streams = [r for r in unconfirmed_rows if r.suggested_kind == "installment"]
    pending_streams = [r for r in streams if not any(plan_covers_series(p, r) for p in plans)]
    caveats = projection_caveats(has_income=schedule["known"], bills=len(bills), unnamed=len(unconfirmed_rows) - len(streams), pending_installments=len(pending_streams), unanchored=sum(1 for b in bills if unanchored(b)))
    pending_rows = pending_installment_events(pending_streams, today, plans=plans)
    paid = paid_periods(session, today)
    late = unposted_periods(session, today)
    line_days = 60
    inside = [d for d in paydays if today < d < today + timedelta(days=line_days)]
    judge_until = inside[-1] if inside else None  # the last payday inside the line: whole pay periods are judged, no more

    from ..models import Debt

    from .planned import planned_events, planned_to_cards

    card_mode = (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")
    debts_rows = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    planned_map = planned_to_cards(session, today)
    extra = pending_rows + planned_events(session, today)
    by_id = {a.id: a for a in accounts}

    def low_for(due_days: dict[int, int]) -> dict:
        events = assemble(bills=bills, plans=plans, advances=advances, today=today, accounts=by_id, paydays=paydays, card_mode=card_mode, debts=debts_rows, planned=planned_map, extra=extra, card_pace_by_account=pace["per_day_by_account"], due_days=due_days,
                          paid=paid, pay_days=schedule["pay_days"], late=late, judge_until=judge_until, checks_by_day=schedule["semimonthly"])["math"]
        windows = split_windows(events, today, next_payday, following, paydays[2])
        path = project(today, cash=cash, buffer=buffer, expected_net=expected_net, pay_dates=pay_dates, set_aside=windows["set_aside"], events=events, per_day=pace["per_day_cash"], days=line_days, pay_amounts=pay_amounts, reserved_for={(e.get("source"), e["date"]) for e in windows["next_window"]})
        judged = [p for p in path if judge_until is None or p["date"] < judge_until.isoformat()] or path
        return low_point(judged)

    return low_for, bills, caveats


CYCLE_WORDS = {"monthly": "of each month", "quarterly": "of its month each quarter", "semiannual": "of its month twice a year", "yearly": "of its month each year"}


def _request(bill: Bill, day: int) -> tuple[str, str]:
    """The instruction and the draft request for moving a bill's due day, in the bill's own cadence: a bill that
    comes every week or two has no day of the month, so it asks for a due date nearer that day instead."""
    cycle = CYCLE_WORDS.get(bill.cadence or "monthly")
    if cycle is None:
        every = "every week" if bill.cadence == "weekly" else "every two weeks"
        return (f"{bill.name} comes {every}, not on a day of the month; ask {bill.name} for a due date nearer the {ordinal(day)}.",
                f"Hello,\n\nMy payment is due {every}. Could the due date move by a few days, to fall nearer the {ordinal(day)}, starting with the next cycle? "
                f"Please confirm the change and any one-time adjustment to the next bill.\n\nThank you.")
    return (f"Ask {bill.name} to move the due date from the {ordinal(bill.due_day)} to the {ordinal(day)}.",
            f"Hello,\n\nI would like to move my billing date from the {ordinal(bill.due_day)} {cycle} to the {ordinal(day)}, starting with the next cycle. "
            f"Please confirm the change and any one-time adjustment to the next bill.\n\nThank you.")


def _monthly(b: Bill) -> bool:
    """A movable bill whose due day is a day of every month: the only kind the suggestions move."""
    return bool(b.movable) and (b.cadence or "monthly") == "monthly"


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
    instruction, draft = _request(bill, day)
    return {
        "bill_id": bill.id, "name": bill.name, "cadence": bill.cadence or "monthly", "from_day": bill.due_day, "to_day": day, "movable": bool(bill.movable),
        "low_before": before["value"], "low_before_date": before["date"], "low_after": after["value"], "low_after_date": after["date"], "change": change,
        "verdict": "better" if change >= 1.0 else ("worse" if change <= -1.0 else "same"),
        "instruction": instruction, "draft": draft,
        "source": f"bill:{bill.id}", "caveats": caveats, "not_advice": NOT_ADVICE,
    }


_suggested: dict[tuple, dict] = {}


def billing_date_suggestions(session: Session, today: date) -> dict:
    """For every movable bill, the due day that lifts the sixty-day low point the most, each change judged on
    its own against the registers as they stand. A change that does not lift the low point is not offered.
    Kept until the data changes: it tries every day for every bill."""
    import copy

    from ..db import data_version

    key = (today, data_version())
    if key in _suggested:
        return copy.deepcopy(_suggested[key])
    out = _billing_date_suggestions(session, today)
    if data_version() == key[1]:
        _suggested.clear()
        _suggested[key] = out
    return copy.deepcopy(out)


def _billing_date_suggestions(session: Session, today: date) -> dict:
    finder, bills, caveats = _low_finder(session, today)
    from .projection import household_accounts, is_spendable

    counted = [a for a in household_accounts(session) if is_spendable(a) and a.kind != "card"]
    if not counted or all(a.balance is None for a in counted):
        # No balance yet: a low point worked out from $0 is no low point, as the snapshot says (sweep 21).
        return {"as_of": today.isoformat(), "no_balance": True, "baseline": {"low": None, "low_date": None}, "moves_start": None,
                "note": "No balance yet: link the bank or type what checking holds, and the billing dates can be judged.", "suggestions": [], "plan": [], "plan_low": None,
                "movable": sum(1 for b in bills if _monthly(b)), "sources": [], "caveats": caveats,
                "drafts_only": "The app drafts the request; it never sends anything. Copy it to the biller yourself, or approve a send once sending exists."}
    tried: dict[frozenset, dict] = {}

    def low_for(due_days: dict[int, int]) -> dict:
        k = frozenset(due_days.items())
        if k not in tried:
            tried[k] = finder(due_days)
        return tried[k]

    baseline = low_for({})
    suggestions = []
    for b in bills:
        if not _monthly(b):  # a weekly, quarterly or yearly bill is not moved to "the Nth of each month"
            continue
        best_day, best_low = b.due_day, baseline["value"]
        for day in range(1, 29):
            if day == b.due_day:
                continue
            low = low_for({b.id: day})
            if low["value"] > best_low + 0.005 or (abs(low["value"] - best_low) <= 0.005 and best_day != b.due_day and abs(day - b.due_day) < abs(best_day - b.due_day)):
                best_day, best_low = day, low["value"]
        if best_day != b.due_day and best_low - baseline["value"] >= 1.0:
            instruction, draft = _request(b, best_day)
            suggestions.append({
                "bill_id": b.id, "name": b.name, "amount": b.amount, "from_day": b.due_day, "to_day": best_day,
                "low_before": baseline["value"], "low_after": round(best_low, 2), "improvement": round(best_low - baseline["value"], 2),
                "instruction": instruction, "draft": draft, "source": f"bill:{b.id}",
            })
    suggestions.sort(key=lambda s: (-s["improvement"], s["name"]))

    # Together: take the best move, then the best next move given it, until nothing lifts the low point a dollar.
    plan: list[dict] = []
    chosen: dict[int, int] = {}
    current = baseline["value"]
    movable_ids = [b.id for b in bills if _monthly(b)]
    for _step in range(len(movable_ids)):
        best = None
        for b in bills:
            if not _monthly(b) or b.id in chosen:
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
    # A change starts with the cycle after each bill's next charge, so the days before that cannot move: when the
    # lowest point falls there, no move lifts it, and the result says why instead of implying the dates are best.
    from .billdates import bill_dates

    seconds = [ds[1] for b in bills if _monthly(b) for ds in [bill_dates(b, today, today + timedelta(days=75))] if len(ds) > 1]
    moves_start = (min(seconds) - timedelta(days=15)) if seconds else None  # a moved charge lands at most about half a month earlier
    low_on = date.fromisoformat(baseline["date"])
    note = None
    if not suggestions and moves_start is not None and low_on < moves_start:
        note = (f"Your lowest point in the next 60 days, {low_on:%a %b} {low_on.day}, comes before a billing-date change could start: each bill's next charge stays on "
                f"its date, and a change starts with the cycle after it. So no move lifts that point.")
    return {
        "as_of": today.isoformat(),
        "no_balance": False,
        "baseline": {"low": baseline["value"], "low_date": baseline["date"]},
        "moves_start": moves_start.isoformat() if moves_start else None,
        "note": note,
        "suggestions": suggestions,
        "plan": plan,
        "plan_low": round(current, 2),
        "movable": sum(1 for b in bills if _monthly(b)),
        "sources": ["snapshot:projection", *(s["source"] for s in suggestions)],
        "caveats": caveats,
        "drafts_only": "The app drafts the request; it never sends anything. Copy it to the biller yourself, or approve a send once sending exists.",
        "not_advice": NOT_ADVICE,
    }
