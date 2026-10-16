"""Pay cadences as dates. Weekly and biweekly step by days; monthly keeps the day; semi-monthly pays on the
15th and the last day of the month, moved to the business day before when either falls on a weekend. Pure
functions, so the detector, the snapshot and the decisions agree on every payday."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

PAYCHECKS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12, "quarterly": 4, "yearly": 1}
CADENCE_WORDS = {"weekly": "every week", "biweekly": "every two weeks", "semimonthly": "twice a month, the 15th and the last day", "monthly": "monthly", "quarterly": "quarterly", "yearly": "yearly"}


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def business_day_on_or_before(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def semimonthly_anchors(year: int, month: int) -> tuple[date, date]:
    """The two paydays of a month: the 15th and the last day, each moved before a weekend."""
    mid = business_day_on_or_before(date(year, month, 15))
    end = business_day_on_or_before(month_end(date(year, month, 1)))
    return mid, end


def _months_around(d: date) -> list[tuple[int, int]]:
    out = []
    for delta in (-1, 0, 1):
        month = d.month + delta
        year = d.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        out.append((year, month))
    return out


def payday_position(d: date) -> str:
    """"mid" for a payday near the 15th, "end" for one near the turn of the month."""
    return "mid" if 8 <= d.day <= 22 else "end"


def is_semimonthly(dates: list[date]) -> bool:
    """Whether every date sits within two days of a 15th-or-month-end payday, which a biweekly run drifts off."""
    if len(dates) < 4:
        return False
    for d in dates:
        anchors = [a for y, m in _months_around(d) for a in semimonthly_anchors(y, m)]
        if min(abs((d - a).days) for a in anchors) > 2:
            return False
    return True


def _add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_paydays(first: date, cadence: str, count: int) -> list[date]:
    """`count` paydays starting with `first`, by cadence. Semi-monthly walks the anchors after `first`; the
    others step from it."""
    out = [first]
    if cadence == "semimonthly":
        year, month = first.year, first.month
        while len(out) < count:
            for anchor in semimonthly_anchors(year, month):
                if anchor > out[-1] and len(out) < count:
                    out.append(anchor)
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return out
    step = {"weekly": 7, "biweekly": 14}.get(cadence)
    while len(out) < count:
        if cadence == "monthly":
            out.append(_add_months(first, len(out)))
        else:
            out.append(out[-1] + timedelta(days=step or 14))
    return out


def payday_for(due: date, which: str) -> date:
    """The mid or end-of-month payday that pays a bill due on `due`: the nearest one within a week either
    side, else the last one before. Rent due on the 1st is paid from the end-of-month check two days before."""
    candidates = [semimonthly_anchors(y, m)[0 if which == "mid" else 1] for y, m in _months_around(due)]
    near = [c for c in candidates if abs((c - due).days) <= 7]
    if near:
        return min(near, key=lambda c: (abs((c - due).days), c))
    before = [c for c in candidates if c <= due]
    return max(before) if before else min(candidates)


def step_payday(d: date, cadence: str) -> date:
    """The payday after `d`."""
    return next_paydays(d, cadence, 2)[1]


def next_unpaid_payday(session, income, today: date) -> date:  # noqa: ANN001 - a Session and an Income
    """The next payday whose check has not landed yet. The income's date is rolled forward past every payday
    already gone, and past today's (or an early one) once a deposit near the usual amount has posted from two
    days before it: that check is in the balance already and must not be counted again."""
    from sqlmodel import select

    from ..models import Transaction

    d = income.next_date
    for _ in range(400):
        if d >= today:
            break
        d = step_payday(d, income.cadence or "biweekly")
    expected = abs(float(income.expected_net or 0.0))
    if expected <= 0:
        return d
    for _ in range(3):
        window_start = d - timedelta(days=2)
        if window_start > today:
            break
        landed = session.exec(select(Transaction.amount).where(Transaction.date >= window_start, Transaction.date <= today, Transaction.amount < 0, Transaction.is_income == True)).all()  # noqa: E712
        if not any(abs(abs(a) - expected) <= max(50.0, expected * 0.4) for a in landed):
            break
        d = step_payday(d, income.cadence or "biweekly")
    return d
