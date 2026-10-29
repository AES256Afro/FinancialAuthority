"""Reports the owner builds: what the rows are (category, a group of categories, merchant, account or tag), over
which period, and drawn how; saved by name. Also a year in review, and the totals that matter at tax time.

Everything is summed here, from the transactions and their splits; a screen only draws what this returns."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Transaction
from ..store import get_setting, set_setting
from .edits import by_category, splits_by_tx, tags_of

SAVED = "reports.saved"
GROUPS = "reports.groups"
ROWS = ("category", "group", "merchant", "account", "tag")
PICTURES = ("bars", "line", "table")
PERIODS = ("month", "quarter", "year", "last_30", "last_90", "last_365", "custom")
TAX_CATEGORIES = ("Medical", "Health & Wellness", "Charitable Donations", "Charity", "Education", "Childcare", "Business", "Taxes")
NOT_ADVICE = "Summed by the app from your transactions. For your taxes, check against your own records; not tax advice."


def _money(v: float) -> float:
    return round(float(v), 2)


def period_dates(period: str, today: date, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    if period == "month":
        return today.replace(day=1), today
    if period == "quarter":
        q = (today.month - 1) // 3 * 3 + 1
        return today.replace(month=q, day=1), today
    if period == "year":
        return today.replace(month=1, day=1), today
    if period in ("last_30", "last_90", "last_365"):
        return today - timedelta(days=int(period.split("_")[1]) - 1), today
    if period == "custom" and start and end and start <= end and (end - start).days <= 3 * 366:
        return start, end
    raise ValueError("a period is month, quarter, year, last_30, last_90, last_365, or custom with a start and end at most three years apart")


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def run_report(session: Session, today: date, *, rows: str = "category", period: str = "month", start: date | None = None, end: date | None = None,
               include_income: bool = False, only: list[str] | None = None) -> dict[str, Any]:
    if rows not in ROWS:
        raise ValueError(f"rows is one of {', '.join(ROWS)}")
    first, last = period_dates(period, today, start, end)
    txs = [t for t in session.exec(select(Transaction).where(Transaction.date >= first, Transaction.date <= last)).all() if t.is_spending or (include_income and t.is_income)]
    split = splits_by_tx(session, [t.id for t in txs])
    accounts = {a.id: a.name for a in session.exec(select(Account)).all()}
    groups = get_setting(session, GROUPS, {}) or {}
    group_of = {c: g for g, cats in groups.items() for c in cats}
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    once: dict[str, float] = defaultdict(float)  # each row once by month: the tag rows overlap, so their sum is not the total
    for t in txs:
        sign = -1.0 if t.is_income else 1.0
        parts: list[tuple[str, float]]
        if rows in ("category", "group"):
            parts = [((group_of.get(c, c) if rows == "group" else c), a) for c, a in by_category(t, split)]
        elif rows == "merchant":
            parts = [(t.merchant or t.raw_name or "(unknown)", t.amount)]
        elif rows == "account":
            parts = [(accounts.get(t.account_id, "(unknown)"), t.amount)]
        else:
            tags = tags_of(t) or ["(no tag)"]
            parts = [(tag, t.amount) for tag in tags]  # a purchase belongs whole to each tag it carries; tags are not shares of it
        hit = False
        for name, amount in parts:
            if only and name not in only:
                continue
            totals[name] += sign * amount
            counts[name] += 1
            by_month[_month_key(t.date)][name] += sign * amount
            hit = True
        if hit:
            once[_month_key(t.date)] += sign * t.amount
    months = []
    d = first.replace(day=1)
    while d <= last:
        months.append(_month_key(d))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    grand = sum(once.values()) if rows == "tag" else sum(totals.values())
    items = sorted(({"name": n, "total": _money(v), "count": counts[n], "share": round(v / grand, 4) if grand else 0.0, "months": [_money(by_month[m].get(n, 0.0)) for m in months]} for n, v in totals.items()), key=lambda r: -r["total"])
    return {"rows": rows, "period": period, "from": first.isoformat(), "to": last.isoformat(), "months": months, "items": items, "total": _money(grand),
            "month_totals": [_money(once[m] if rows == "tag" else sum(by_month[m].values())) for m in months], "not_advice": NOT_ADVICE}


def saved(session: Session) -> list[dict[str, Any]]:
    return list((get_setting(session, SAVED, {}) or {}).values())


def save_report(session: Session, spec: dict[str, Any]) -> dict[str, Any]:
    name = str(spec.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("a report needs a name")
    if spec.get("rows") not in ROWS or spec.get("period") not in PERIODS or spec.get("picture", "bars") not in PICTURES:
        raise ValueError("rows, period or picture is not one the app knows")
    clean = {"name": name, "rows": spec["rows"], "period": spec["period"], "picture": spec.get("picture", "bars"), "include_income": bool(spec.get("include_income")),
             "only": [str(x)[:60] for x in (spec.get("only") or [])][:30], "start": spec.get("start"), "end": spec.get("end")}
    stored = get_setting(session, SAVED, {}) or {}
    if name not in stored and len(stored) >= 50:
        raise ValueError("at most fifty saved reports")
    stored[name] = clean
    set_setting(session, SAVED, stored)
    return clean


def remove_report(session: Session, name: str) -> None:
    stored = get_setting(session, SAVED, {}) or {}
    stored.pop(name, None)
    set_setting(session, SAVED, stored)


def save_groups(session: Session, groups: dict[str, list[str]]) -> dict[str, list[str]]:
    clean: dict[str, list[str]] = {}
    seen: set[str] = set()
    for g, cats in list(groups.items())[:30]:
        g = str(g).strip()[:40]
        members = [str(c).strip()[:60] for c in cats if str(c).strip() and str(c).strip() not in seen][:40]
        seen.update(members)
        if g and members:
            clean[g] = members
    set_setting(session, GROUPS, clean)
    return clean


def year_in_review(session: Session, today: date, year: int | None = None) -> dict[str, Any]:
    year = year or today.year
    first, last = date(year, 1, 1), min(date(year, 12, 31), today)
    if first > today:
        raise ValueError("that year has not started")
    spending = run_report(session, today, rows="category", period="custom", start=first, end=last)
    merchants = run_report(session, today, rows="merchant", period="custom", start=first, end=last)
    income = sum(-t.amount for t in session.exec(select(Transaction).where(Transaction.date >= first, Transaction.date <= last, Transaction.is_income == True)).all())  # noqa: E712
    busiest = max(range(len(spending["months"])), key=lambda i: spending["month_totals"][i]) if spending["months"] else None
    return {
        "year": year, "from": first.isoformat(), "to": last.isoformat(), "spent": spending["total"], "income": _money(income), "kept": _money(income - spending["total"]),
        "categories": spending["items"][:10], "merchants": merchants["items"][:10], "months": spending["months"], "month_totals": spending["month_totals"],
        "busiest_month": spending["months"][busiest] if busiest is not None else None, "not_advice": NOT_ADVICE,
    }


def tax_totals(session: Session, today: date, year: int | None = None) -> dict[str, Any]:
    """What a tax return usually asks about: medical, giving, education, childcare, business, taxes paid, and
    anything the owner tagged "tax"."""
    year = year or (today.year - 1 if today.month <= 4 else today.year)
    first, last = date(year, 1, 1), min(date(year, 12, 31), max(today, date(year, 1, 1)))  # next year (the route allows it) is empty, not an error
    by_cat = run_report(session, today, rows="category", period="custom", start=first, end=last)
    tagged = run_report(session, today, rows="tag", period="custom", start=first, end=last, only=["tax"])
    lines = [i for i in by_cat["items"] if i["name"] in TAX_CATEGORIES]
    return {"year": year, "from": first.isoformat(), "to": last.isoformat(), "categories": lines, "tagged_tax": tagged["items"][0] if tagged["items"] else None,
            "note": 'Tag a purchase "tax" on Transactions and it is added here.', "not_advice": NOT_ADVICE}
