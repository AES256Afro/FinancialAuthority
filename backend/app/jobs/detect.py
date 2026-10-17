"""Recurring detection as a job: the ledger in, the series register out, linked to bills and incomes it already knows."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..engine.borrowing import plan_covers_series
from ..engine.recurring import detect, merchant_key
from ..models import Bill, Income, InstallmentPlan, RecurringSeries, Transaction
from .writing import one_writer


def _series_key(found: dict) -> tuple:
    return (found["merchant_key"], found["cadence"], round(found["typical_amount"]))


def _one_per_key(session: Session) -> dict[tuple, RecurringSeries]:
    """The series register by key, one series per key. Two runs at once (before they were serialised) could each
    add the same series; of such twins the one the owner confirmed or a bill or paycheck uses is kept, else the
    oldest, and a twin nobody confirmed or uses is removed. Twins that are both in use are left for the owner."""
    by_key: dict[tuple, list[RecurringSeries]] = {}
    for s in session.exec(select(RecurringSeries).order_by(RecurringSeries.id)).all():
        by_key.setdefault(_series_key({"merchant_key": s.merchant_key, "cadence": s.cadence, "typical_amount": s.typical_amount}), []).append(s)
    used = {b.series_id for b in session.exec(select(Bill)).all() if b.series_id} | {i.series_id for i in session.exec(select(Income)).all() if i.series_id}
    out: dict[tuple, RecurringSeries] = {}
    for key, rows in by_key.items():
        keeper = next((s for s in rows if s.confirmed or s.id in used), rows[0])
        out[key] = keeper
        for s in rows:
            if s is not keeper and not s.confirmed and s.id not in used:
                session.delete(s)
    session.flush()
    return out


def run_detection(session: Session, today: date, lookback_days: int = 400) -> dict:
    """Detection under the write lock (jobs/writing.py): a second run waits for the first and reads what it added,
    so the same series is never added twice. What the caller wrote is committed first, as the run commits anyway."""
    session.commit()
    with one_writer():
        return _run_detection(session, today, lookback_days)


def _run_detection(session: Session, today: date, lookback_days: int) -> dict:
    from ..engine.pairs import find_pairs

    pairs = find_pairs(session, today)  # transfers and card payments first, so they never look like spending
    since = today - timedelta(days=lookback_days)
    rows = [
        {"date": t.date, "merchant": t.merchant, "amount": t.amount, "category": t.category}
        for t in session.exec(select(Transaction).where(Transaction.date >= since, Transaction.is_ignored == False, Transaction.pending == False)).all()  # noqa: E712
        if not t.is_transfer and not t.is_card_payment
    ]
    spending = [r for r in rows if not _is_income_row(r)]
    income_rows = [r for r in rows if _is_income_row(r)]
    found = detect(spending, today, lookback_days, include_inactive=True) + detect(income_rows, today, lookback_days, include_inactive=True, income=True)

    existing = _one_per_key(session)
    bills = {}
    for b in session.exec(select(Bill).where(Bill.active == True)).all():  # noqa: E712
        bills[b.name.lower()] = b
        if b.pays_to:
            bills.setdefault(b.pays_to.lower(), b)
    incomes = list(session.exec(select(Income).where(Income.active == True)).all())  # noqa: E712
    plans = list(session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all())  # noqa: E712
    created = updated = linked = 0
    seen_ids: set[int] = set()
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
                        inc.pay_days = f.get("pay_days", "")
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
        seen_ids.add(series.id)
        if not series.confirmed:
            bill = bills.get(series.merchant_key) or bills.get(series.merchant.lower())
            if bill is not None and f["kind"] != "income":
                series.kind, series.confirmed = bill.kind, True
                bill.series_id = series.id
                if (bill.cadence or "monthly") != "monthly" and bill.anchor_date is None and series.next_expected:
                    bill.anchor_date = series.next_expected
                session.add_all([series, bill])
                linked += 1
                continue
            plan = next((p for p in plans if plan_covers_series(p, series)), None)  # the plan's merchant, or its lender at its installment
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
                        if f["cadence"] == "semimonthly" and inc.cadence == "semimonthly":
                            inc.pay_days = f.get("pay_days", "")
                        if f["cadence"] == "monthly" and inc.cadence == "monthly" and f.get("pay_day"):
                            inc.pay_day = f["pay_day"]
                        inc.next_date = max(inc.next_date, series.next_expected) if inc.next_date < today else inc.next_date
                        session.add_all([series, inc])
                        linked += 1
                        break
    # A stream nobody confirmed and the ledger no longer shows (its rows were undone or removed) stops counting.
    retired = 0
    for series in existing.values():
        if series.id not in seen_ids and not series.confirmed and series.active and series.origin != "borrowing":  # a lender's stream is sync_borrowing's to retire
            series.active = False
            session.add(series)
            retired += 1
    from ..engine.cadence import pay_day_from, scheduled_payday

    # Each paycheck's date moves to its first scheduled payday on or after today; a check that has landed is
    # worked out from the ledger each time it matters, so on payday the date stays today's.
    for inc in incomes:
        if (inc.cadence or "") == "monthly" and not inc.pay_day:
            inc.pay_day = pay_day_from(inc.next_date)  # pinned before the date rolls, so a check on the 31st stays one after February
            session.add(inc)
        rolled = scheduled_payday(inc, today)
        if rolled != inc.next_date:
            inc.next_date = rolled
            session.add(inc)
    session.commit()
    filled = fill_paying_accounts(session)
    from ..engine.planned import settle_planned

    settled = settle_planned(session, today)  # a plan whose payment just arrived stops counting now, not when a page opens
    return {"found": len(found), "created": created, "updated": updated, "linked": linked, "retired": retired, "paying_accounts_filled": filled, "planned_settled": settled, "pairs": pairs}


def accounts_index(session: Session) -> tuple[dict, dict]:
    """How many rows each merchant has on each account, by name and by merchant key, from one grouped count."""
    from collections import Counter

    from sqlalchemy import func

    by_name: dict[str, Counter] = {}
    by_key: dict[str, Counter] = {}
    for merchant, account_id, n in session.exec(select(Transaction.merchant, Transaction.account_id, func.count()).group_by(Transaction.merchant, Transaction.account_id)).all():
        by_name.setdefault(merchant, Counter())[account_id] += n
        by_key.setdefault(merchant_key(merchant), Counter())[account_id] += n
    return by_name, by_key


def account_for_series(session: Session, series: RecurringSeries, index: tuple[dict, dict] | None = None) -> int | None:
    """The account the series' charges come out of, by the charges themselves: a subscription on the card is
    paid by the card."""
    by_name, by_key = index or accounts_index(session)
    counts = by_name.get(series.merchant) or by_key.get(series.merchant_key)
    return counts.most_common(1)[0][0] if counts else None


def fill_paying_accounts(session: Session) -> int:
    """Every bill with a series and no paying account on file takes the account its charges come out of."""
    filled = 0
    series_by_id = {s.id: s for s in session.exec(select(RecurringSeries)).all()}
    index = None
    for b in session.exec(select(Bill).where(Bill.active == True, Bill.pay_from_account_id == None)).all():  # noqa: E711, E712
        series = series_by_id.get(b.series_id) if b.series_id else None
        if series is None:
            continue
        index = index or accounts_index(session)
        account_id = account_for_series(session, series, index)
        if account_id is not None:
            b.pay_from_account_id = account_id
            session.add(b)
            filled += 1
    if filled:
        session.commit()
    return filled


def _is_income_row(r: dict) -> bool:
    return r["amount"] < 0 and r["category"] == "Income"


def pending_series(session: Session) -> list[RecurringSeries]:
    return list(session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True).order_by(RecurringSeries.typical_amount.desc())).all())  # noqa: E712
