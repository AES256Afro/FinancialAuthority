"""When a bill comes due, by its cadence: monthly on its due day; weekly and every two weeks by stepping days;
quarterly, twice a year and yearly by stepping months, from the bill's anchor date (its next due date). A bill that
comes every few months and has no anchor has no dates: its due day alone cannot say which month, and inventing the
next one would put a yearly bill in every coming month. Every place that lists a bill's dates uses this, so a yearly
bill is never counted twelve times."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

DAY_STEPS = {"weekly": 7, "biweekly": 14}
MONTH_STEPS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}
PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4, "semiannual": 2, "yearly": 1}


def _add_months(d: date, n: int, day: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_on_day(day: int, start: date) -> date:
    day = max(1, min(31, int(day or 1)))
    this = date(start.year, start.month, min(day, calendar.monthrange(start.year, start.month)[1]))
    return this if this >= start else _add_months(this, 1, day)


def bill_dates(bill, start: date, end: date, *, due_day: int | None = None) -> list[date]:  # noqa: ANN001 - a Bill
    """Every date from `start` through `end` the bill comes due, never before its first due date or after its last."""
    first, last = getattr(bill, "starts_on", None), getattr(bill, "ends_on", None)
    if first is not None and first > start:
        start = first
    if last is not None and last < end:
        end = last
    if start > end:
        return []
    return _bill_dates(bill, start, end, due_day=due_day)


def _bill_dates(bill, start: date, end: date, *, due_day: int | None = None) -> list[date]:  # noqa: ANN001 - a Bill
    cadence = (getattr(bill, "cadence", "") or "monthly").lower()
    day = due_day if due_day is not None else getattr(bill, "due_day", 1)
    anchor = getattr(bill, "anchor_date", None)
    out: list[date] = []
    if cadence in DAY_STEPS:
        step = timedelta(days=DAY_STEPS[cadence])
        d = anchor or _next_on_day(day, start)
        while d < start:
            d += step
        while d - step >= start:
            d -= step
        while d <= end:
            out.append(d)
            d += step
        return out
    months = MONTH_STEPS.get(cadence, 1)
    if months > 1 and anchor is None:
        return []  # which month it falls in is not known: never invented (see unanchored)
    if months == 1:
        d = _next_on_day(day, start)
    else:
        d = date(anchor.year, anchor.month, min(day, calendar.monthrange(anchor.year, anchor.month)[1]))
        for _ in range(400):
            if d >= start:
                break
            d = _add_months(d, months, day)
        for _ in range(400):
            back = _add_months(d, -months, day)
            if back < start:
                break
            d = back
    for _ in range(400):
        if d > end:
            break
        out.append(d)
        d = _add_months(d, months, day)
    return out


def per_year(bill) -> float:  # noqa: ANN001 - a Bill
    """How many times a year the bill comes due."""
    return float(PER_YEAR.get((getattr(bill, "cadence", "") or "monthly").lower(), 12))


def unanchored(bill) -> bool:  # noqa: ANN001 - a Bill
    """A bill that comes every few months with no next due date on file: it is not on any date until one is given."""
    return MONTH_STEPS.get((getattr(bill, "cadence", "") or "monthly").lower(), 1) > 1 and getattr(bill, "anchor_date", None) is None


def one_time(bill) -> bool:  # noqa: ANN001 - a Bill
    """A bill due once: its first and its last due date are the same day (a cost of moving in)."""
    first, last = getattr(bill, "starts_on", None), getattr(bill, "ends_on", None)
    return first is not None and first == last


def monthly_cost(bills: list, today: date) -> dict:
    """What the bills cost a month as they run today: each bill running now at its own cadence, a twelfth of a
    year's worth. A one-time bill (a cost of moving in) is not a monthly cost: it is listed apart as coming once.
    A bill that starts later (the next lease) is listed apart with what it will add; one that has ended is left out."""
    running, once, later = [], [], []
    for b in bills:
        first, last = getattr(b, "starts_on", None), getattr(b, "ends_on", None)
        if one_time(b):
            if last >= today:
                once.append(b)
            continue
        if last is not None and last < today:
            continue
        (later if first is not None and first > today else running).append(b)

    def month(b) -> float:  # noqa: ANN001 - a Bill
        return float(b.amount) * per_year(b) / 12.0

    return {
        "total": round(sum(month(b) for b in running), 2),
        "basis": "each bill running today by its own cadence: weekly 52 a year, every two weeks 26, monthly 12, quarterly 4, twice a year 2, yearly 1, then a twelfth of the year; one-time costs and bills that start later are listed apart, not counted",
        "sources": [f"bill:{b.id}" for b in running],
        "one_time": {"total": round(sum(float(b.amount) for b in once), 2), "items": [{"bill_id": b.id, "name": b.name, "amount": round(float(b.amount), 2), "date": b.starts_on.isoformat(), "source": f"bill:{b.id}"} for b in sorted(once, key=lambda b: b.starts_on)]},
        "starting_later": {"total": round(sum(month(b) for b in later), 2), "items": [{"bill_id": b.id, "name": b.name, "amount": round(float(b.amount), 2), "per_month": round(month(b), 2), "starts_on": b.starts_on.isoformat(), "source": f"bill:{b.id}"} for b in sorted(later, key=lambda b: b.starts_on)]},
    }
