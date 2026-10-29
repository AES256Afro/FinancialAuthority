"""Rocket Money's weekly "This week's upcoming bills" email: every charge it lists becomes a bill fact.

Deterministic: the mail lays each charge out as three lines, the name, the day ("Thursday, October 15") and
the amount ("$10.00"). The year comes from the message date; a day outside the next three weeks, or an amount
that is not bill-sized, is dropped. The text is data and nothing in it is followed."""

from __future__ import annotations

import re
from datetime import date, datetime

HEADER = re.compile(r"recurring charges? coming up|upcoming bills", re.IGNORECASE)
DAY_LINE = re.compile(
    r"^(?:(?:Mon|Tue|Tues|Wed|Wednes|Thu|Thur|Thurs|Fri|Sat|Satur|Sun)[a-z]*,?\s+)?"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$",
    re.IGNORECASE,
)
AMOUNT_LINE = re.compile(r"^-?\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)$")
STOP = re.compile(r"^(open rocket money|looking to lower|lower my bills|did you find this email)", re.IGNORECASE)
MAX_AMOUNT = 50_000.0
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _day(month_word: str, day: str, year: str | None, ref: date) -> date | None:
    month = MONTHS.get(month_word.lower().rstrip(".")[:3])
    if month is None:
        return None
    years = [int(year)] if year else [ref.year, ref.year + 1, ref.year - 1]
    for yr in years:
        try:
            candidate = datetime(yr, month, int(day)).date()
        except ValueError:
            continue
        if -3 <= (candidate - ref).days <= 21:  # the mail covers the coming week; nothing else is a bill from it
            return candidate
    return None


def parse_upcoming_bills(text: str, ref: date) -> list[dict]:
    """[{name, due_date, amount}] for each charge the mail lists, in order; empty when the layout is not there."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line and not re.fullmatch(r"[\W_]+", line)]
    start = next((i for i, line in enumerate(lines) if HEADER.search(line)), None)
    if start is None:
        return []
    found: list[dict] = []
    i = start + 1
    while i < len(lines):
        if STOP.search(lines[i]):
            break
        day = DAY_LINE.match(lines[i + 1]) if i + 1 < len(lines) else None
        amount = AMOUNT_LINE.match(lines[i + 2]) if i + 2 < len(lines) else None
        if day and amount:
            name = lines[i]
            due = _day(day.group(1), day.group(2), day.group(3), ref)
            value = float(amount.group(1).replace(",", ""))
            # The heading is never a charge's name (a name line lost in the mail's text would hand it the heading).
            if due is not None and 0 < value <= MAX_AMOUNT and 2 <= len(name) <= 80 and not DAY_LINE.match(name) and not AMOUNT_LINE.match(name) and not HEADER.search(name):
                found.append({"name": name, "due_date": due, "amount": round(value, 2)})
            i += 3
            continue
        i += 1
    return found
