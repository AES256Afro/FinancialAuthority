"""Recurring detection as a job: the ledger in, the series register out, linked to bills and incomes it already knows."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..engine.recurring import detect, merchant_key
from ..models import Bill, Income, InstallmentPlan, RecurringSeries, Transaction


def _series_key(found: dict) -> tuple:
    return (found["merchant_key"], found["cadence"], round(found["typical_amount"]))


def run_detection(session: Session, today: date, lookback_days: int = 400) -> dict:
    since = today - timedelta(days=lookback_days)
    rows = [
        {"date": t.date, "merchant": t.merchant, "amount": t.amount, "category": t.category}
        for t in session.exec(select(Transaction).where(Transaction.date >= since, Transaction.is_ignored == False)).all()  # noqa: E712
        if not t.is_transfer and not t.is_card_payment
    ]
    spending = [r for r in rows if not _is_income_row(r)]
    income_rows = [r for r in rows if _is_income_row(r)]
    found = detect(spending, today, lookback_days, include_inactive=True) + detect(income_rows, today, lookback_days, include_inactive=True, income=True)

    existing = {(_series_key({"merchant_key": s.merchant_key, "cadence": s.cadence, "typical_amount": s.typical_amount})): s for s in session.exec(select(RecurringSeries)).all()}
    bills = {b.name.lower(): b for b in session.exec(select(Bill).where(Bill.active == True)).all()}  # noqa: E712
    incomes = list(session.exec(select(Income).where(Income.active == True)).all())  # noqa: E712
    plans = list(session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all())  # noqa: E712
    created = updated = linked = 0
    for f in found:
        key = _series_key(f)
        series = existing.get(key)
        if series is None and f["cadence"] in ("biweekly", "semimonthly"):
            # A run first called biweekly that the day pattern now shows as semi-monthly (or the reverse) is the
            # same series: its cadence moves, and so does the paycheck that hangs off it.
            other = (key[0], "semimonthly" if f["cadence"] == "biweekly" else "biweekly", key[2])
            series = existing.pop(other, None)
            if series is not None:
                series.cadence = f["cadence"]
                existing[key] = series
                for inc in incomes:
                    if inc.series_id == series.id:
                        inc.cadence = f["cadence"]
                        if f["next_expected"] >= today:
                            inc.next_date = f["next_expected"]
                        session.add(inc)
        if series is None:
            series = RecurringSeries(
                merchant_key=f["merchant_key"], merchant=f["merchant"], category=f["category"], cadence=f["cadence"], typical_amount=f["typical_amount"],
                latest_amount=f["latest_amount"], charges_seen=f["charges_seen"], first_seen=f["first_seen"], last_date=f["last_date"], next_expected=f["next_expected"],
                active=f["active"], kind=f["kind"], suggested_kind=f.get("suggested_kind", ""),
            )
            session.add(series)
            session.flush()
            existing[key] = series
            created += 1
        else:
            series.latest_amount, series.charges_seen, series.last_date, series.next_expected, series.active = f["latest_amount"], f["charges_seen"], f["last_date"], f["next_expected"], f["active"]
            if f["kind"] == "income":
                series.kind = "income"
            if not series.confirmed:
                series.suggested_kind = f.get("suggested_kind", "")
            session.add(series)
            updated += 1
        if not series.confirmed:
            bill = bills.get(series.merchant_key) or bills.get(series.merchant.lower())
            if bill is not None and f["kind"] != "income":
                series.kind, series.confirmed = bill.kind, True
                bill.series_id = series.id
                session.add_all([series, bill])
                linked += 1
                continue
            plan = next((p for p in plans if p.merchant and series.merchant_key.startswith(p.merchant.lower())), None)
            if plan is not None and f["kind"] != "income":
                series.kind, series.confirmed = "installment", True
                session.add(series)
                linked += 1
                continue
            if f["kind"] == "income":
                for inc in incomes:
                    name = inc.name.lower()
                    if series.merchant_key == name or series.merchant_key.startswith(name):
                        series.confirmed = True
                        inc.series_id = series.id
                        inc.next_date = max(inc.next_date, series.next_expected) if inc.next_date < today else inc.next_date
                        session.add_all([series, inc])
                        linked += 1
                        break
    session.commit()
    return {"found": len(found), "created": created, "updated": updated, "linked": linked}


def _is_income_row(r: dict) -> bool:
    return r["amount"] < 0 and r["category"] == "Income"


def pending_series(session: Session) -> list[RecurringSeries]:
    return list(session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True).order_by(RecurringSeries.typical_amount.desc())).all())  # noqa: E712
