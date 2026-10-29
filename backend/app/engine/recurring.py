"""Recurring-series detection, ported from the owner's Rocket Money MCP server.

Looks for the same merchant charging a similar amount on a regular schedule (weekly, biweekly, monthly,
quarterly, yearly). Pure functions over plain dicts so the tests need no database.
"""

from __future__ import annotations

import re
import calendar
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from .cadence import MONTH_END, _add_months, month_end, next_paydays, semimonthly_days_of

CADENCES = [  # (name, min_days, max_days, charges_per_year)
    ("weekly", 6, 8, 52),
    ("biweekly", 13, 16, 26),
    ("monthly", 26, 35, 12),
    ("quarterly", 85, 97, 4),
    ("yearly", 350, 380, 1),
]


def merchant_key(merchant: str) -> str:
    return re.sub(r"\s+", " ", (merchant or "").lower()).strip()


def bill_keys(bill) -> list[str]:  # noqa: ANN001 - a Bill
    """The names a bill's payments may carry: its own, who it is paid to when that differs, and every name it carried
    before Housing renamed it (the bank's own words for a rent or a utility bill)."""
    import json

    try:
        before = [str(n) for n in json.loads(getattr(bill, "match_names", "") or "[]") if n]
    except (ValueError, TypeError):
        before = []
    keys = [merchant_key(bill.name), merchant_key(getattr(bill, "pays_to", "") or ""), *(merchant_key(n) for n in before)]
    return [k for i, k in enumerate(keys) if k and k not in keys[:i]]


def bill_named(bills, key: str, *, loose: bool = False):  # noqa: ANN001, ANN201
    """The bill this name means: the same name (or who it is paid to), or, loosely, one that starts the other.
    The exact name wins over every loose one: "Music" is the bill named Music, not "Music (two)" listed before it."""
    for b in bills:
        if key in bill_keys(b):
            return b
    if not loose or not key:
        return None
    for b in bills:
        for k in bill_keys(b):
            if k.startswith(key) or key.startswith(k):
                return b
    return None


def _consistent(amounts: list[float]) -> bool:
    med = statistics.median(amounts)
    return sum(1 for a in amounts if abs(a - med) <= max(1.0, 0.15 * med)) >= 0.75 * len(amounts)


BILL_WORDS = ("rent", "mortgage", "loan", "insurance", "electric", "utilities", "utility", "water", "sewer", "trash", "internet", "phone", "cable", "wireless", "cell", "tuition", "childcare", "child care", "daycare", "hoa", "property tax", "power", "heating")
SUBSCRIPTION_WORDS = ("streaming", "music", "subscription", "software", "gym", "fitness", "membership", "news", "entertainment", "services", "games", "apps", "app store", "cloud", "storage")
BORROWING_WORDS = ("borrowing", "loan payment", "installment", "pay later", "advance")
SPEND_WORDS = ("groceries", "grocery", "dining", "restaurant", "food", "shopping", "gas", "fuel", "transfer", "credit card payment", "financial", "atm", "travel", "transport", "pharmacy", "coffee", "general merchandise")


def suggest_kind(category: str, merchant: str = "", income: bool = False) -> str:
    """A default the owner can accept with one tap: what the category (Plaid's, Rocket Money's or a bank's)
    says this series is. Empty when nothing is clear. Only the owner's confirmation makes it count."""
    if income:
        return "income"
    text = f"{category} {merchant}".lower()
    if any(w in text for w in BORROWING_WORDS) and "loan payment" not in text:
        return "installment"
    bill = any(w in text for w in BILL_WORDS)
    subscription = any(w in text for w in SUBSCRIPTION_WORDS)
    if any(w in text for w in SPEND_WORDS) and not bill and not subscription:
        return "ignored"
    if bill:
        return "bill"
    if subscription:
        return "subscription"
    return ""


def detect(rows: list[dict], as_of: date, lookback_days: int = 400, include_inactive: bool = False, income: bool = False) -> list[dict]:
    """rows: dicts with date (date), merchant, amount (positive = money out), category.

    With income=True the negative amounts (money in) are examined instead, which finds paychecks.
    """
    since = as_of - timedelta(days=lookback_days)
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        amount = -r["amount"] if income else r["amount"]
        if amount <= 0 or r["date"] < since:
            continue
        by_merchant[merchant_key(r["merchant"])].append({**r, "amount": amount})

    found: list[dict] = []
    for key, merchant_rows in by_merchant.items():
        series = [merchant_rows]
        split = not _consistent([r["amount"] for r in merchant_rows])
        if split:
            buckets: dict[int, list[dict]] = defaultdict(list)
            for r in merchant_rows:
                buckets[round(r["amount"])].append(r)
            series = list(buckets.values())
        for s in series:
            s = sorted(s, key=lambda r: r["date"])  # an export may list the newest first; the latest is by date
            dates = sorted({r["date"] for r in s})
            amounts = [r["amount"] for r in s]
            if not _consistent(amounts):
                continue
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            if not gaps:
                continue
            med_gap = statistics.median(gaps)
            for name, lo, hi, per_year in CADENCES:
                if lo <= med_gap <= hi:
                    break
            else:
                continue
            on_schedule = sum(1 for g in gaps if lo * 0.8 <= g <= hi * 1.2)
            min_charges = 2 if name in ("quarterly", "yearly") and not split else 3
            if len(dates) < min_charges or on_schedule < 0.6 * len(gaps):
                continue
            typical = statistics.median(amounts)
            next_expected = dates[-1] + timedelta(days=round(med_gap))
            pay_days = ""
            pay_day = 0
            if name in ("monthly", "quarterly", "yearly"):
                # a calendar month is not a fixed number of days: step by months and keep the usual day of the
                # last three (a weekend-shifted charge does not move it; a tie goes to the latest); a run that
                # lands on the last day of every month keeps month end, so Apr 30 is followed by May 31
                recent = [d.day for d in dates[-3:]]
                counts = Counter(recent)
                usual_day = next(day for day in reversed(recent) if counts[day] == max(counts.values()))
                if all(d == month_end(d) for d in dates[-3:]):
                    usual_day = MONTH_END
                pay_day = usual_day
                stepped = _add_months(dates[-1], {"monthly": 1, "quarterly": 3, "yearly": 12}[name])
                next_expected = stepped.replace(day=min(usual_day, calendar.monthrange(stepped.year, stepped.month)[1]))
            if name == "biweekly":
                days = semimonthly_days_of(dates)
                if days is not None:
                    name, per_year, pay_days = "semimonthly", 24, days
                    next_expected = next_paydays(dates[-1], "semimonthly", 2, days)[1]
            active = (as_of - dates[-1]).days <= med_gap * 1.5
            if not active and not include_inactive:
                continue
            category = Counter(r.get("category", "") for r in s).most_common(1)[0][0]
            found.append({
                "merchant_key": key,
                "merchant": s[-1]["merchant"],
                "category": category,
                "suggested_kind": suggest_kind(category, s[-1]["merchant"], income),
                "cadence": name,
                "typical_amount": round(typical, 2),
                "latest_amount": round(s[-1]["amount"], 2),
                "yearly_cost": round(typical * per_year, 2),
                "charges_seen": len(dates),
                "first_seen": dates[0],
                "last_date": dates[-1],
                "next_expected": next_expected,
                "active": active,
                "kind": "income" if income else "unknown",
                "pay_days": pay_days,
                "pay_day": pay_day,
            })
    found.sort(key=lambda f: f["yearly_cost"], reverse=True)
    return found
