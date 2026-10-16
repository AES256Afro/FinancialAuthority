"""Pay cadences as dates. Weekly and biweekly step by days; monthly keeps the day; semi-monthly pays on the
15th and the last day of the month, or on the 1st and the 15th, moved to the business day before when either
falls on a weekend. Pure functions, so the detector, the snapshot and the decisions agree on every payday.

Which semi-monthly days apply is the paycheck's (`Income.pay_days`); next_unpaid_payday sets them for the
calculation that follows, since every plan starts from it."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

PAYCHECKS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12, "quarterly": 4, "yearly": 1}
CADENCE_WORDS = {"weekly": "every week", "biweekly": "every two weeks", "semimonthly": "twice a month", "monthly": "monthly", "quarterly": "quarterly", "yearly": "yearly"}
PAY_DAYS = {"": "the 15th and the last day", "1,15": "the 1st and the 15th"}
_pay_days = [""]


def use_pay_days(pay_days: str | None) -> None:
    """The semi-monthly days for what follows: "" is the 15th and the last day, "1,15" the 1st and the 15th."""
    _pay_days[0] = pay_days if pay_days in PAY_DAYS else ""


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def business_day_on_or_before(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def semimonthly_anchors(year: int, month: int, pay_days: str | None = None) -> tuple[date, date]:
    """The two paydays of a month as (mid, turn of the month), each moved before a weekend: the 15th and the
    last day, or the 15th and the 1st (which may move back into the month before)."""
    days = _pay_days[0] if pay_days is None else pay_days
    mid = business_day_on_or_before(date(year, month, 15))
    if days == "1,15":
        return mid, business_day_on_or_before(date(year, month, 1))
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


def is_semimonthly(dates: list[date], pay_days: str | None = None) -> bool:
    """Whether every date sits within two days of a semi-monthly payday, which a biweekly run drifts off."""
    if len(dates) < 4:
        return False
    for d in dates:
        anchors = [a for y, m in _months_around(d) for a in semimonthly_anchors(y, m, pay_days)]
        if min(abs((d - a).days) for a in anchors) > 2:
            return False
    return True


def semimonthly_days_of(dates: list[date]) -> str | None:
    """Which semi-monthly days a run of paydays keeps: "" (15th and last), "1,15", or None for neither."""
    for days in ("", "1,15"):
        if is_semimonthly(dates, days):
            return days
    return None


def _add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_paydays(first: date, cadence: str, count: int, pay_days: str | None = None) -> list[date]:
    """`count` paydays starting with `first`, by cadence. Semi-monthly walks the anchors after `first`; the
    others step from it."""
    out = [first]
    if cadence == "semimonthly":
        year, month = first.year, first.month
        while len(out) < count:
            for anchor in sorted(semimonthly_anchors(year, month, pay_days)):
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


def previous_paydays(next_payday: date, cadence: str, count: int, pay_days: str | None = None) -> list[date]:
    """The `count` paydays before `next_payday`, latest first, stepped back by the cadence itself."""
    out: list[date] = []
    if cadence == "semimonthly":
        year, month = next_payday.year, next_payday.month
        while len(out) < count:
            for anchor in sorted(semimonthly_anchors(year, month, pay_days), reverse=True):
                if anchor < (out[-1] if out else next_payday) and len(out) < count:
                    out.append(anchor)
            month -= 1
            if month < 1:
                month, year = 12, year - 1
        return out
    for k in range(1, count + 1):
        if cadence == "monthly":
            out.append(_add_months(next_payday, -k))
        else:
            out.append(next_payday - timedelta(days=(7 if cadence == "weekly" else 14) * k))
    return out


def paydays_through(first: date, cadence: str, end: date, at_least: int = 3) -> list[date]:
    """Every payday from `first` through `end`, and never fewer than `at_least` (the windows look one or two
    checks past the end of the line)."""
    count = max(at_least, (end - first).days // 6 + 3)
    out = next_paydays(first, cadence, count)
    return [d for i, d in enumerate(out) if d <= end or i < at_least]


def step_payday(d: date, cadence: str) -> date:
    """The payday after `d`."""
    return next_paydays(d, cadence, 2)[1]


def next_unpaid_payday(session, income, today: date) -> date:  # noqa: ANN001 - a Session and an Income
    """The next payday whose check has not landed yet. The income's date is rolled forward past every payday
    already gone, and past today's (or an early one) once a deposit near the usual amount has posted from two
    days before it: that check is in the balance already and must not be counted again."""
    from sqlmodel import select

    from ..models import Transaction

    use_pay_days(getattr(income, "pay_days", "") or "")
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
        landed = session.exec(select(Transaction.amount).where(Transaction.date >= window_start, Transaction.date <= today, Transaction.amount < 0, Transaction.is_income == True, Transaction.pending == False)).all()  # noqa: E712
        if not any(abs(abs(a) - expected) <= max(50.0, expected * 0.4) for a in landed):
            break
        d = step_payday(d, income.cadence or "biweekly")
    return d
