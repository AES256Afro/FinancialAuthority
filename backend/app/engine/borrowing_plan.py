"""A borrowing plan with its reasons (M54): what the pay-later loans take each month and how that steps down as they
end, which are at the finish line, what each still costs in interest, and in what order money beyond the scheduled
payments does the most good. Worked out here, with no model, from the plans (the lender's own schedules where the
app has them), the rates on the lender's statement, and the paycheck windows. Not financial advice.

The logic, in the order a person would apply it:
1. Every scheduled payment on its day; a loan without AutoPay is named, since a missed one costs the most.
2. The finish line: a loan with one or two payments left ends on its own; the plan says when, and how many loans
   are left to watch after it.
3. Whether there is room for more: each paycheck window's safe-to-spend less the usual day-to-day spending over
   it. While the plan runs short, there is none, and the plan says when the stairs step down and money frees up.
4. Where money beyond the schedule goes: both orders are worked out month by month with that room (half the
   leanest paycheck's, as a cautious amount): the highest rate first, which costs the least interest, and the
   smallest first, which ends loans soonest. When the two cost nearly the same (within $25, or 2%), the plan
   finishes the small ones first, since fewer loans to watch is worth more than a few dollars; otherwise the
   cheaper order. A loan at 0% is paid on schedule, since paying it early saves nothing."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..models import InstallmentPlan
from .borrowing import plan_provider

NOT_ADVICE = "Worked out by the app from your loans' schedules and rates and your paycheck plan. A lender's payoff quote may differ a little. Not financial advice."
FINISH = 2  # payments left at which a loan is at the finish line
MONTHS = 18


def _money(v: float) -> float:
    return round(float(v), 2)


def _upcoming(p: InstallmentPlan, today: date) -> list[dict[str, Any]]:
    """The payments still to come: the lender's schedule where there is one, else the installment on the cadence."""
    sched = sorted(json.loads(p.schedule or "[]"), key=lambda e: e["date"])
    if sched:
        # The last payments of the schedule, as many as are left: one paid early is gone even before its date.
        return sched[len(sched) - p.remaining:] if p.remaining else []
    out, d = [], p.next_date
    from datetime import timedelta

    for _ in range(max(0, p.remaining)):
        out.append({"date": d.isoformat(), "amount": p.installment})
        d = d + timedelta(days=p.cadence_days or 30)
    return out


def _months_from(today: date, iso: str) -> int:
    d = date.fromisoformat(iso)
    return max(0, (d.year - today.year) * 12 + d.month - today.month)


def payoff_estimate(payments: list[dict[str, Any]], apr: float | None, today: date | None = None) -> float:
    """What it would take to end the loan today: each payment left brought back to today at the loan's monthly rate,
    by the months between (a payment later this month, none). At 0% it is simply what is left to pay."""
    left = sum(e["amount"] for e in payments)
    if not apr:
        return _money(left)
    r = apr / 100 / 12
    start = today or date.fromisoformat(payments[0]["date"])
    return _money(sum(e["amount"] / (1 + r) ** _months_from(start, e["date"]) for e in payments))


def _signed(v: float) -> str:
    """-$383.00, the sign before the dollar, as the recap says money (never "$-383.00")."""
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def borrowing_plan(session: Session, today: date) -> dict[str, Any]:
    from .reports import _plan_month

    plans = [p for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all() if p.remaining > 0]  # noqa: E712
    loans = []
    for p in plans:
        pays = _upcoming(p, today)
        if not pays:
            continue
        left = _money(sum(e["amount"] for e in pays))
        payoff = payoff_estimate(pays, p.apr, today)
        interest = _money(max(0.0, left - payoff))
        loans.append({
            "id": p.id, "name": p.merchant or p.provider, "lender": plan_provider(p), "next_date": pays[0]["date"], "next_amount": _money(pays[0]["amount"]),
            "payments_left": len(pays), "ends": pays[-1]["date"], "left_to_pay": left, "payoff_estimate": payoff, "interest_ahead": interest,
            "apr": p.apr, "autopay": p.autopay, "per_month": _plan_month(p),  # the one monthly figure Debts and Borrowing give it; the stairs are each month's own dates
            "payments": pays, "source": f"plan:{p.id}", "dates_from_lender": not p.dates_estimated,
        })
    loans.sort(key=lambda l: (l["ends"], l["name"]))
    named = [l["name"] for l in loans]
    for l in loans:
        l["label"] = f"{l['name']} (${l['next_amount']:,.2f})" if named.count(l["name"]) > 1 else l["name"]

    # The stairs: what the loans take each month, for the next year and a half
    months: list[str] = []
    y, m = today.year, today.month
    for i in range(MONTHS):
        mm = m - 1 + i
        months.append(f"{y + mm // 12:04d}-{mm % 12 + 1:02d}")
    stairs = []
    for month in months:
        parts = [{"id": l["id"], "name": l["name"], "amount": _money(sum(e["amount"] for e in l["payments"] if e["date"][:7] == month))} for l in loans]
        parts = [x for x in parts if x["amount"] > 0]
        stairs.append({"month": month, "total": _money(sum(x["amount"] for x in parts)), "parts": parts})
    while stairs and stairs[-1]["total"] == 0 and len(stairs) > 6:
        stairs.pop()

    # The finish line
    finish = [l for l in loans if l["payments_left"] <= FINISH]
    after = len(loans) - len(finish)
    last_finish = max((l["ends"] for l in finish), default=None)
    freed_from = None
    if last_finish:
        first_after = next((s for s in stairs if s["month"] > last_finish[:7]), None)
        before = [s for s in stairs if s["month"] <= last_finish[:7]]
        peak = max(before, key=lambda s: s["total"]) if before else None
        if first_after and peak:
            freed_from = {"month": first_after["month"], "per_month_before": peak["total"], "peak_month": peak["month"], "per_month_after": first_after["total"],
                          "freed": _money(peak["total"] - first_after["total"])}

    zero = [l for l in loans if l["interest_ahead"] <= 0]

    # Is there money beyond the schedule? Each paycheck window's safe-to-spend, less the usual day-to-day spending
    # over it, is what is really left.
    room = None
    try:
        from .snapshot import finance_snapshot

        snap = finance_snapshot(session, today)
        windows = snap.get("windows") or []
        per_day = float((snap.get("usual_pace") or {}).get("per_day") or 0.0)
        if windows and not snap.get("no_balance"):  # no balance yet: no room is worked out from nothing
            left = []
            for w in windows:
                days = (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1 if w.get("end") else 14
                left.append(float(w["safe"]) - per_day * days)
            room = {"safe_now": _money(snap["safe_to_spend"]["amount"]), "per_day": _money(per_day), "least_after_a_payday": _money(min(left)),
                    "windows": len(windows), "short_now": float(snap["safe_to_spend"]["amount"]) < 0}
    except Exception:  # noqa: BLE001 - the plan stands without the paycheck view
        room = None
    extra = 0.0
    if room and not room["short_now"] and room["least_after_a_payday"] > 0:
        extra = float(int(room["least_after_a_payday"] * 0.5 / 25) * 25)  # half the leanest paycheck's room, in $25 steps
    paying = [l for l in loans if l["interest_ahead"] > 0]
    ways = {}
    for way in ("finish_first", "rate_first"):
        ways[way] = simulate(paying, extra, way, today)
    base = simulate(paying, 0.0, "rate_first", today)
    difference = _money(ways["finish_first"]["interest"] - ways["rate_first"]["interest"])
    close = abs(difference) <= max(25.0, 0.02 * max(1.0, ways["rate_first"]["interest"]))
    chosen = "finish_first" if (close or difference < 0) else "rate_first"
    order = sorted(paying, key=(lambda l: (l["payoff_estimate"], -(l["apr"] or 0))) if chosen == "finish_first" else (lambda l: (-(l["apr"] or 0), l["payoff_estimate"])))

    steps = []
    off = [l for l in loans if l["autopay"] is False]
    unknown = [l for l in loans if l["autopay"] is None]
    steps.append({"kind": "keep", "text": f"Keep every scheduled payment: {len(loans)} loan{'s' if len(loans) != 1 else ''}, ${sum(l['left_to_pay'] for l in loans):,.2f} still to pay."
                  + (f" AutoPay is off on {', '.join(l['name'] + ' (' + _day(l['next_date']) + ', $' + format(l['next_amount'], ',.2f') + ')' for l in off)}: pay it on its day, or turn AutoPay on in the lender's app." if off else "")
                  + (f" AutoPay is not known for {len(unknown)}." if unknown else ""), "sources": [l["source"] for l in loans]})
    if finish:
        names = "; ".join(f"{l['label']}: {l['payments_left']} payment{'s' if l['payments_left'] != 1 else ''} left, done {_day(l['ends'])}" for l in finish)
        line = f"At the finish line: {names}. After {_day(last_finish)} you watch {after} loan{'s' if after != 1 else ''} instead of {len(loans)}."
        if freed_from and freed_from["freed"] > 0:
            line += f" From {_month(freed_from['month'])} the loans take ${freed_from['per_month_after']:,.2f} a month instead of ${freed_from['per_month_before']:,.2f}: ${freed_from['freed']:,.2f} frees up."
        steps.append({"kind": "finish", "text": line, "sources": [l["source"] for l in finish]})
    if room is not None:
        if room["short_now"]:
            text = (f"Right now there is no room beyond the schedule: this paycheck runs short (safe to spend {_signed(room['safe_now'])}). Keep to step one; "
                    + (f"the money that frees up from {_month(freed_from['month'])} is the first to put toward the next step." if freed_from and freed_from["freed"] > 0 else "room can come from moving a bill or pausing a goal on Cover."))
        elif room["least_after_a_payday"] <= 0:
            text = f"After bills, payments and your usual ${room['per_day']:,.2f} a day, the leanest paycheck has nothing left over, so the plan is step one until the stairs step down."
        else:
            text = (f"After bills, payments and your usual ${room['per_day']:,.2f} a day, the leanest paycheck still leaves ${room['least_after_a_payday']:,.2f}. "
                    f"The plan uses half of that, ${extra:,.0f} a month, so a lean month never comes up short.")
        steps.append({"kind": "room", "text": text, "sources": ["snapshot.windows", "snapshot.usual_pace"]})
    if order and extra > 0:
        a, b = ways["finish_first"], ways["rate_first"]
        names = ", then ".join(l["label"] for l in order[:4])
        why = (f"Finishing the smallest first costs ${abs(difference):,.2f} {'more' if difference > 0 else 'less'} in interest than the highest rate first, so the plan finishes them first: fewer loans to watch, sooner."
               if chosen == "finish_first" else
               f"The highest rate first saves ${difference:,.2f} in interest against finishing the smallest first, so the plan follows the rates.")
        steps.append({"kind": "extra", "text": f"Put the ${extra:,.0f} a month toward {names}. {why} All loans end by {_month(a['done'] if chosen == 'finish_first' else b['done'])}, "
                      f"against {_month(base['done'])} on the schedule alone, and the interest falls from ${base['interest']:,.2f} to ${(a if chosen == 'finish_first' else b)['interest']:,.2f}.",
                      "sources": [l["source"] for l in order]})
    elif order:
        steps.append({"kind": "extra", "text": f"When there is room, {'the smallest first' if chosen == 'finish_first' else 'the highest rate first'}: {', then '.join(l['label'] for l in order[:4])}. "
                      f"Paying one off early saves its interest ahead (shown below); every one here charges interest.", "sources": [l["source"] for l in order]})
    if zero:
        steps.append({"kind": "zero", "text": f"{', '.join(l['label'] for l in zero)}: no interest ahead, so paying early saves nothing; pay on schedule.", "sources": [l["source"] for l in zero]})

    totals = {"loans": len(loans), "left_to_pay": _money(sum(l["left_to_pay"] for l in loans)), "payoff_estimate": _money(sum(l["payoff_estimate"] for l in loans)),
              "interest_ahead": _money(sum(l["interest_ahead"] for l in loans))}
    for l in loans:
        l.pop("payments")
    return {"as_of": today.isoformat(), "loans": loans, "stairs": stairs, "finish_line": [{"name": l["label"], "ends": l["ends"], "payments_left": l["payments_left"], "left_to_pay": l["left_to_pay"], "source": l["source"]} for l in finish],
            "freed": freed_from, "order": [{"name": l["label"], "apr": l["apr"], "payoff_estimate": l["payoff_estimate"], "source": l["source"]} for l in order],
            "chosen": chosen, "extra_per_month": extra, "ways": {k: {kk: vv for kk, vv in v.items() if kk != "ended"} for k, v in ways.items()}, "schedule_alone": base,
            "room": room, "steps": steps, "totals": totals, "not_advice": NOT_ADVICE}


def simulate(loans: list[dict[str, Any]], extra: float, way: str, today: date) -> dict[str, Any]:
    """Month by month: each loan accrues its rate on what is owed and takes its scheduled payment; the extra, and any
    payment a finished loan no longer needs, goes to the target (the highest rate, or the smallest owed). Starts
    from what it would take to end each loan today."""
    live = []
    for l in loans:
        by_month: dict[str, float] = {}
        for e in l["payments"]:
            by_month[e["date"][:7]] = by_month.get(e["date"][:7], 0.0) + e["amount"]
        live.append({"id": l["id"], "owed": l["payoff_estimate"], "r": (l["apr"] or 0) / 1200, "due": by_month, "ended": None})
    y, m = today.year, today.month
    interest = 0.0
    for i in range(240):
        mm = m - 1 + i
        month = f"{y + mm // 12:04d}-{mm % 12 + 1:02d}"
        freed = 0.0
        for x in live:
            due = x["due"].get(month, 0.0)
            if x["owed"] <= 0.005:
                freed += due
                continue
            charge = x["owed"] * x["r"] if i > 0 else 0.0  # the plan starts today: this month's interest is in what is owed
            interest += charge
            x["owed"] += charge
            pay = min(x["owed"], due) if due else (x["owed"] if month > max(x["due"] or {"": ""}) else 0.0)
            x["owed"] -= pay
        pot = extra + freed
        for x in sorted((x for x in live if x["owed"] > 0.005), key=(lambda x: (x["owed"], -x["r"])) if way == "finish_first" else (lambda x: (-x["r"], x["owed"]))):
            if pot <= 0.005:
                break
            take = min(pot, x["owed"])
            x["owed"] -= take
            pot -= take
        for x in live:
            if x["owed"] <= 0.005 and x["ended"] is None:
                x["ended"] = month
        if all(x["ended"] for x in live):
            break
    done = max((x["ended"] for x in live if x["ended"]), default=None)
    return {"way": way, "extra": _money(extra), "interest": _money(interest), "done": done, "ended": {x["id"]: x["ended"] for x in live}}


def _day(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d:%a %b} {d.day}"


def _month(ym: str) -> str:
    d = date.fromisoformat(ym + "-01")
    return f"{d:%B %Y}"
