"""Credit health from your own data (M48): what a credit score weighs that the app can see, with no credit pull and
no paid service. Two things: whether payments are on time (from the payments the app matched to bills), and the
share of each card's limit used on the day its statement closes (what the card reports). The app cannot see the
age of your accounts, new applications, or the score itself; it says so.

For each card: what to pay before its statement closes so it reports under 30% of its limit, and under 10%.
Figures are the engine's, with sources. Not financial advice."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Bill, Payment
from .borrowing import borrowing_overview


def _money(v: float) -> float:
    return round(float(v), 2)


def on_time(session: Session, today: date) -> dict[str, Any]:
    """The last twelve months of bill payments: on time, late, not seen; and how many in a row were on time."""
    since = today - timedelta(days=365)
    bills = {b.id: b for b in session.exec(select(Bill)).all()}
    rows = session.exec(select(Payment).where(Payment.due_date >= since, Payment.due_date <= today).order_by(Payment.due_date)).all()
    on, late, missed, records = 0, 0, 0, []
    for p in rows:
        b = bills.get(p.bill_id)
        grace = (b.grace_days if b else 0) or 0
        if p.status == "paid" and p.paid_date and p.paid_date <= p.due_date + timedelta(days=max(3, grace)):
            on += 1
            records.append(True)
        elif p.status == "paid":
            late += 1
            records.append(False)
        elif p.status in ("past_due", "late", "missed") and p.evidence != "the owner: this bill has stopped":
            missed += 1
            records.append(False)
    streak = 0
    for ok in reversed(records):
        if not ok:
            break
        streak += 1
    late_rows = [{"name": bills[p.bill_id].name if p.bill_id in bills else "?", "due_date": p.due_date.isoformat(), "status": p.status,
                  "paid_date": p.paid_date.isoformat() if p.paid_date else None, "source": f"payment:{p.id}"}
                 for p in rows if p.status in ("past_due", "late", "missed") or (p.status == "paid" and p.paid_date and p.paid_date > p.due_date + timedelta(days=3))][-10:]
    total = on + late + missed
    return {"on_time": on, "late": late, "not_seen": missed, "total": total, "share_on_time": round(on / total, 4) if total else None, "streak": streak, "late_rows": late_rows}


def credit_health(session: Session, today: date) -> dict[str, Any]:
    cards_in = borrowing_overview(session, today).get("credit") or []
    cards = []
    for c in cards_in:
        limit, balance = c.get("credit_limit"), max(0.0, float(c.get("balance") or 0.0))
        item = {**c, "share": round(balance / limit, 4) if limit else None}
        if limit:
            item["pay_for_30"] = _money(max(0.0, balance - 0.30 * limit))
            item["pay_for_10"] = _money(max(0.0, balance - 0.10 * limit))
            item["status"] = "good" if balance <= 0.10 * limit else "warn" if balance <= 0.30 * limit else "crit"
            item["word"] = ("Reports under 10% of its limit." if item["status"] == "good" else
                            f"Pay ${item['pay_for_10']:,.2f} before its statement closes{' on ' + _day(c['next_statement']) if c.get('next_statement') else ''} to report under 10%." if item["status"] == "warn" else
                            f"Pay ${item['pay_for_30']:,.2f} before its statement closes{' on ' + _day(c['next_statement']) if c.get('next_statement') else ''} to report under 30% (${item['pay_for_10']:,.2f} for under 10%).")
        else:
            item["status"], item["word"] = None, "No credit limit on file: add it on Debts to see the share it reports."
        item["source"] = f"account:{c['account_id']}"
        cards.append(item)
    with_limit = [c for c in cards if c.get("credit_limit")]
    total_limit = sum(c["credit_limit"] for c in with_limit)
    total_balance = sum(max(0.0, float(c.get("balance") or 0.0)) for c in with_limit)
    overall = round(total_balance / total_limit, 4) if total_limit else None
    record = on_time(session, today)
    return {
        "as_of": today.isoformat(), "cards": cards,
        "overall": {"balance": _money(total_balance), "limit": _money(total_limit), "share": overall,
                    "status": None if overall is None else "good" if overall <= 0.10 else "warn" if overall <= 0.30 else "crit"},
        "payments": record,
        "cannot_see": ["your score itself", "how long your accounts have been open", "new applications and hard checks", "accounts the app is not connected to"],
        "word": _summary(record, overall),
        "not_advice": "What a score weighs that the app can see, from your own data. No credit pull is made. Not financial advice.",
    }


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a %b} {d.day}"


def _summary(record: dict[str, Any], overall: float | None) -> str:
    parts = []
    if record["total"]:
        parts.append(f"{record['on_time']} of {record['total']} bill payments in the last year were on time; the last {record['streak']} in a row." if record["streak"] else
                     f"{record['on_time']} of {record['total']} bill payments in the last year were on time; the latest was not.")
    else:
        parts.append("No bill payments matched yet, so there is no on-time record to show.")
    if overall is not None:
        parts.append(f"Your cards together use {overall * 100:.0f}% of their limits.")
    return " ".join(parts)
