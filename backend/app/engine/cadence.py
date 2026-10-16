"""Pay cadences as dates. Weekly and biweekly step by days; monthly keeps the paycheck's own day of the month
(month end is a day of its own: Jan 31, Feb 28, Mar 31); semi-monthly pays on the 15th and the last day of the
month, or on the 1st and the 15th, moved to the business day before when either falls on a weekend. Pure
functions, so the detector, the snapshot and the decisions agree on every payday.

Which days apply is the paycheck's own and is passed in with each call: `pay_days` for a semi-monthly paycheck
(`Income.pay_days`) and `pay_day` for a monthly one (`Income.pay_day`). Nothing is remembered between calls.
Several paychecks make one schedule (`pay_schedule`): every payday of each, and what lands on each day."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

PAYCHECKS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12, "quarterly": 4, "yearly": 1}
CADENCE_WORDS = {"weekly": "every week", "biweekly": "every two weeks", "semimonthly": "twice a month", "monthly": "monthly", "quarterly": "quarterly", "yearly": "yearly"}
PAY_DAYS = {"": "the 15th and the last day", "1,15": "the 1st and the 15th"}
MONTH_END = 31  # a monthly paycheck's day when it pays on the last day of the month, whatever that month's length


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def business_day_on_or_before(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def pay_day_from(d: date) -> int:
    """The day of the month a monthly paycheck dated `d` keeps: its own day, or month end when `d` is the last day
    of its month (a check on Feb 28 or Apr 30 is taken for a month-end check, which errs on the later date)."""
    return MONTH_END if d == month_end(d) else d.day


def income_days(income) -> dict:  # noqa: ANN001 - an Income
    """The days an income pays on, as the functions here take them: its semi-monthly days and its day of the
    month (the day of its date when none is on file yet)."""
    return {"pay_days": getattr(income, "pay_days", "") or "", "pay_day": int(getattr(income, "pay_day", 0) or 0) or income.next_date.day}


def semimonthly_anchors(year: int, month: int, pay_days: str | None = None) -> tuple[date, date]:
    """The two paydays of a month as (mid, turn of the month), each moved before a weekend: the 15th and the
    last day, or the 15th and the 1st (which may move back into the month before)."""
    mid = business_day_on_or_before(date(year, month, 15))
    if (pay_days or "") == "1,15":
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


def _off_anchor(d: date, pay_days: str | None) -> int:
    """How many days `d` sits from the nearest semi-monthly payday."""
    return min(abs((d - a).days) for y, m in _months_around(d) for a in semimonthly_anchors(y, m, pay_days))


def is_semimonthly(dates: list[date], pay_days: str | None = None) -> bool:
    """Whether every date sits within two days of a semi-monthly payday, which a biweekly run drifts off. A run
    whose every gap is exactly fourteen days is biweekly, however well it happens to sit."""
    if len(dates) < 4:
        return False
    if all((b - a).days == 14 for a, b in zip(dates, dates[1:])):
        return False
    return all(_off_anchor(d, pay_days) <= 2 for d in dates)


def semimonthly_days_of(dates: list[date]) -> str | None:
    """Which semi-monthly days a run of paydays keeps: "" (15th and last), "1,15", or None for neither. When
    both fit (a check that posts a day early sits near either), the closer pattern wins; a tie keeps ""."""
    fits = [days for days in ("", "1,15") if is_semimonthly(dates, days)]
    if not fits:
        return None
    return min(fits, key=lambda days: (sum(_off_anchor(d, days) for d in dates), days != ""))


def _add_months(d: date, n: int, day: int | None = None) -> date:
    """`d` moved by `n` months, kept on `day` (by default its own), or the month's last day when it is shorter."""
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day or d.day, calendar.monthrange(year, month)[1]))


def next_paydays(first: date, cadence: str, count: int, pay_days: str | None = None, pay_day: int = 0) -> list[date]:
    """`count` paydays starting with `first`, by cadence. Semi-monthly walks the anchors after `first`; monthly
    keeps `pay_day` (by default the day of `first`) in every month; the others step from it."""
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
            out.append(_add_months(first, len(out), pay_day or first.day))
        else:
            out.append(out[-1] + timedelta(days=step or 14))
    return out


def payday_for(due: date, which: str, pay_days: str | None = None) -> date:
    """The mid or end-of-month payday that pays a bill due on `due`: the nearest one within a week either
    side, else the last one before. Rent due on the 1st is paid from the end-of-month check two days before."""
    candidates = [semimonthly_anchors(y, m, pay_days)[0 if which == "mid" else 1] for y, m in _months_around(due)]
    near = [c for c in candidates if abs((c - due).days) <= 7]
    if near:
        return min(near, key=lambda c: (abs((c - due).days), c))
    before = [c for c in candidates if c <= due]
    return max(before) if before else min(candidates)


def previous_paydays(next_payday: date, cadence: str, count: int, pay_days: str | None = None, pay_day: int = 0) -> list[date]:
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
            out.append(_add_months(next_payday, -k, pay_day or next_payday.day))
        else:
            out.append(next_payday - timedelta(days=(7 if cadence == "weekly" else 14) * k))
    return out


def paydays_through(first: date, cadence: str, end: date, at_least: int = 3, pay_days: str | None = None, pay_day: int = 0) -> list[date]:
    """Every payday from `first` through `end`, and never fewer than `at_least` (the windows look one or two
    checks past the end of the line)."""
    count = max(at_least, (end - first).days // 6 + 3)
    out = next_paydays(first, cadence, count, pay_days, pay_day)
    return [d for i, d in enumerate(out) if d <= end or i < at_least]


def step_payday(d: date, cadence: str, pay_days: str | None = None, pay_day: int = 0) -> date:
    """The payday after `d`."""
    return next_paydays(d, cadence, 2, pay_days, pay_day)[1]


def scheduled_payday(income, today: date) -> date:  # noqa: ANN001 - an Income
    """The income's first payday on or after today by its schedule alone, whether or not that check has landed:
    on payday it is today, before the deposit posts and after."""
    days = income_days(income)
    d = income.next_date
    for _ in range(400):
        if d >= today:
            break
        d = step_payday(d, income.cadence or "biweekly", **days)
    return d


def _landed(session, income, start: date, end: date, expected: float) -> bool:  # noqa: ANN001 - a Session and an Income
    """Whether this income's check has posted between `start` and `end`: a deposit near its usual amount under its
    own name when it has a series, else into its own account; another job's check or a refund is not it."""
    from sqlmodel import select

    from ..models import RecurringSeries, Transaction
    from .recurring import merchant_key

    query = select(Transaction.amount, Transaction.merchant).where(Transaction.date >= start, Transaction.date <= end, Transaction.amount < 0, Transaction.is_income == True, Transaction.pending == False)  # noqa: E712
    series = session.get(RecurringSeries, income.series_id) if getattr(income, "series_id", None) else None
    key = series.merchant_key if series is not None else ""
    if not key and getattr(income, "account_id", None):
        query = query.where(Transaction.account_id == income.account_id)
    for amount, merchant in session.exec(query).all():
        if key:
            mine = merchant_key(merchant)
            if not mine or not (mine == key or mine.startswith(key) or key.startswith(mine)):
                continue
        if abs(abs(amount) - expected) <= max(50.0, expected * 0.4):
            return True
    return False


def next_unpaid_payday(session, income, today: date) -> date:  # noqa: ANN001 - a Session and an Income
    """The next payday whose check has not landed yet. The income's date is rolled forward past every payday
    already gone, and past today's (or an early one) once its deposit has posted from two days before it: that
    check is in the balance already and must not be counted again."""
    days = income_days(income)
    d = scheduled_payday(income, today)
    expected = abs(float(income.expected_net or 0.0))
    if expected <= 0:
        return d
    for _ in range(3):
        window_start = d - timedelta(days=2)
        if window_start > today or not _landed(session, income, window_start, today, expected):
            break
        d = step_payday(d, income.cadence or "biweekly", **days)
    return d


def income_net(income, d: date) -> float:  # noqa: ANN001 - an Income
    """What one income's check brings on `d`: a semi-monthly check may differ by its position in the month."""
    if (income.cadence or "") == "semimonthly" and payday_position(d) == "end" and income.expected_net_end is not None:
        return float(income.expected_net_end)
    return float(income.expected_net)


def pay_schedule(session, incomes: list, today: date, end: date, at_least: int = 3) -> dict:  # noqa: ANN001 - a Session
    """Every active paycheck as one schedule: each income's paydays from its next unpaid one through `end` (never
    fewer than `at_least` in all), what lands on each (the incomes paid that day, added), and the next payday,
    the earliest of them, with its income. One paycheck is its own schedule, unchanged. With none, the paydays
    are a frame two weeks apart, nothing lands on them, and `known` is false.

    `pay_days` are the semi-monthly days a bill paid from the mid or end-of-month check is placed by: those of
    the first semi-monthly paycheck, else of the next one."""
    upcoming = sorted(((next_unpaid_payday(session, i, today), i) for i in incomes), key=lambda p: p[0])
    if not upcoming:
        first = today + timedelta(days=14)
        return {"known": False, "next": first, "income": None, "cadence": "biweekly", "days": {"pay_days": "", "pay_day": 0}, "pay_days": "",
                "paydays": paydays_through(first, "biweekly", end, at_least), "amounts": {}, "incomes_on": {}}
    amounts: dict[date, float] = {}
    incomes_on: dict[date, list] = {}
    for first, income in upcoming:
        for d in paydays_through(first, income.cadence or "biweekly", end, at_least, **income_days(income)):
            amounts[d] = amounts.get(d, 0.0) + income_net(income, d)
            incomes_on.setdefault(d, []).append(income)
    # Each income lists at least `at_least` paydays of its own, so none is missing among the first `at_least` of all.
    paydays = [d for i, d in enumerate(sorted(amounts)) if d <= end or i < at_least]
    next_payday, income = upcoming[0]
    semi = next((i for _, i in upcoming if (i.cadence or "") == "semimonthly"), income)
    return {"known": True, "next": next_payday, "income": income, "cadence": income.cadence or "biweekly", "days": income_days(income), "pay_days": semi.pay_days or "",
            "paydays": paydays, "amounts": {d: amounts[d] for d in paydays}, "incomes_on": {d: incomes_on[d] for d in paydays}}
