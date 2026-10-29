"""Payment matching: each bill period finds its payment in the ledger, or becomes late, then past due.

A match is a transaction near the due date whose merchant looks like the bill, or whose amount is the bill's
exact amount. Card payments count: a card's minimum is paid by a transfer, not a purchase.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Bill, Payment, RecurringSeries, Transaction
from .recurring import bill_keys, merchant_key

LOOKBACK_DAYS = 60
AHEAD_DAYS = 21
BEFORE_DAYS = 6
AFTER_DAYS = 4
HEAL_DAYS = 90  # as needsyou.PAST_DUE_DAYS: a kept period is looked at again for as long as Needs you shows it


def _due_dates(bill: Bill, start: date, end: date) -> list[date]:
    from .billdates import bill_dates

    return bill_dates(bill, start, end)


def _looks_like(bill_key: list[str] | str, series_key: str | None, t_key: str) -> bool:
    if not t_key:
        return False
    if series_key and (t_key == series_key or t_key.startswith(series_key) or series_key.startswith(t_key)):
        return True
    return any(t_key == k or t_key.startswith(k) or k.startswith(t_key) for k in ([bill_key] if isinstance(bill_key, str) else bill_key))


def _match_for(bill: Bill, bill_key: list[str], series_key: str | None, due: date, transactions: list[Transaction], used: set) -> Transaction | None:
    """The transaction that pays this period: near the due date (six days before to the bill's grace after), under
    the bill's names within its drift, or at its exact amount. A transaction already used pays nothing else."""
    # A named match may drift by the detector's own 15% (a price change); an estimate by 40%.
    tolerance = max(2.0, bill.amount * (0.4 if bill.amount_mode == "estimate" else 0.15))
    for t in transactions:
        if t.id in used or not (due - timedelta(days=BEFORE_DAYS) <= t.date <= due + timedelta(days=max(AFTER_DAYS, bill.grace_days))):
            continue
        close_amount = abs(t.amount - bill.amount) <= tolerance
        exact_amount = abs(t.amount - bill.amount) < 0.01
        if (close_amount and _looks_like(bill_key, series_key, merchant_key(t.merchant))) or (exact_amount and not t.is_transfer):
            return t
    return None


def _match_in_month(bill: Bill, bill_key: list[str], series_key: str | None, due: date, transactions: list[Transaction], used: set, today: date, others: list[date] = ()) -> Transaction | None:
    """The row that paid a monthly bill's month when none sits near the due day: under the bill's own names (never
    by amount alone), in the same calendar month, not used by another period. A bill whose due day the owner moved
    was paid on its old day in the months before the move; a payment later in the month is still that month's."""
    tolerance = max(2.0, bill.amount * (0.4 if bill.amount_mode == "estimate" else 0.15))
    for t in transactions:
        if t.id in used or t.date > today or (t.date.year, t.date.month) != (due.year, due.month):
            continue
        if any(d != due and d - timedelta(days=BEFORE_DAYS) <= t.date <= d + timedelta(days=max(AFTER_DAYS, bill.grace_days)) for d in others):
            continue  # it sits near another period's day (rent due the 2nd paid on the 28th before): that period's payment, never a missed month's
        if abs(t.amount - bill.amount) <= tolerance and _looks_like(bill_key, series_key, merchant_key(t.merchant)):
            return t
    return None


def match_payments(session: Session, today: date) -> dict:
    start = today - timedelta(days=LOOKBACK_DAYS)
    end = today + timedelta(days=AHEAD_DAYS)
    heal_from = today - timedelta(days=HEAL_DAYS)
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    series_keys = {s.id: s.merchant_key for s in session.exec(select(RecurringSeries)).all()}
    transactions = session.exec(select(Transaction).where(Transaction.date >= heal_from - timedelta(days=BEFORE_DAYS), Transaction.date <= today, Transaction.amount > 0, Transaction.pending == False)).all()  # noqa: E712
    by_id = {t.id: t for t in transactions}
    existing = {(p.bill_id, p.due_date): p for p in session.exec(select(Payment).where(Payment.due_date >= start)).all()}
    # Every transaction a period already holds, however old that period: one that paid a period now past the lookback
    # (a long grace) must not pay the next period too once the first has left the window.
    used = {t for t in session.exec(select(Payment.transaction_id).where(Payment.transaction_id != None)).all()}  # noqa: E711
    counts = {"paid": 0, "late": 0, "past_due": 0, "scheduled": 0}
    # A due day moved without the Bills page's rebuild (a confirmed notice sets it) left periods on the old day: the
    # paid ones held the month's payment while the new day's period stood past due (found on the owner's box, sweep 18).
    for p in session.exec(select(Payment).where(Payment.due_date >= heal_from, Payment.due_date < start)).all():
        existing[(p.bill_id, p.due_date)] = p
    holders: dict[int, int] = {}
    for tid in session.exec(select(Payment.transaction_id).where(Payment.transaction_id != None)).all():  # noqa: E711
        holders[tid] = holders.get(tid, 0) + 1
    for bill in bills:
        bill_key = bill_keys(bill)
        series_key = series_keys.get(bill.series_id) if bill.series_id else None
        monthly = (bill.cadence or "monthly") == "monthly"
        first = bill.created_on or heal_from
        dues = _due_dates(bill, max(heal_from, first), end)
        on_day = set(dues)
        # Periods on a day the bill no longer comes due: an open one is no period at all; a paid one lends its payment to
        # the period on the new day that it pays (and goes), or stays as the month's record when none takes it.
        released: dict[int, Payment] = {}
        for key, p in [(k, p) for k, p in existing.items() if k[0] == bill.id and k[1] >= first and k[1] not in on_day and (monthly or not bill.anchor_date or k[1] >= bill.anchor_date)]:
            if p.status in ("scheduled", "late", "past_due"):
                session.delete(p)
                del existing[key]
            elif p.status == "paid" and p.transaction_id and holders.get(p.transaction_id) == 1:
                used.discard(p.transaction_id)
                released[p.transaction_id] = p
        for (b, _), p in existing.items():
            t = by_id.get(p.transaction_id) if b == bill.id and p.status == "paid" and p.transaction_id else None
            if t is not None and (p.amount, p.paid_date) != (t.amount, t.date):
                p.amount, p.paid_date = t.amount, t.date  # the bank changed the row after it was matched: every kept period follows, the old day's too
                session.add(p)
        paid_months = {p.due_date.strftime("%Y-%m") for (b, _), p in existing.items() if b == bill.id and p.status == "paid"}
        # A bill is looked for from the day it was added, never in the months before it existed; before the lookback only
        # the periods already kept are looked at again, none is made.
        for due in dues:
            payment = existing.get((bill.id, due))
            if due < start and payment is None:
                continue
            if payment is not None and payment.status == "paid":
                counts["paid"] += 1  # its row's changes were followed above
                continue
            # A monthly bill is paid once a month: a period on a new due day is not owed again in a month whose period
            # on the old day is already paid (the owner moved the day; the months before the move were paid as they were),
            # unless the old day's own payment sits near the new day, when it pays the new day's period instead.
            paid_month = monthly and due.strftime("%Y-%m") in paid_months
            if paid_month and payment is None:
                match = _match_for(bill, bill_key, series_key, due, [t for t in transactions if t.id in released], used)
                if match is None:
                    continue
            else:
                match = _match_for(bill, bill_key, series_key, due, transactions, used)
                if match is None and monthly and due < today:
                    match = _match_in_month(bill, bill_key, series_key, due, transactions, used | set(released), today, dues)
            if match is not None:
                status, paid_date, tx_id, amount, evidence = "paid", match.date, match.id, match.amount, f"transaction:{match.id}"
                used.add(match.id)
                gone = released.pop(match.id, None)
                if gone is not None:  # the old day's period was this one, paid: one row for one payment
                    session.delete(gone)
                    existing.pop((bill.id, gone.due_date), None)
            elif paid_month:
                session.delete(payment)  # made before the month's payment on the old day was seen: not owed twice
                del existing[(bill.id, due)]
                continue
            elif due < today:
                days_late = (today - due).days
                status = "late" if days_late <= max(3, bill.grace_days) else "past_due"
                paid_date, tx_id, amount, evidence = None, None, bill.amount, ""
            else:
                status, paid_date, tx_id, amount, evidence = "scheduled", None, None, bill.amount, ""
            if payment is None:
                payment = Payment(bill_id=bill.id, due_date=due, amount=amount, status=status, paid_date=paid_date, transaction_id=tx_id, evidence=evidence)
                existing[(bill.id, due)] = payment
            else:
                payment.status, payment.paid_date, payment.transaction_id, payment.amount, payment.evidence = status, paid_date, tx_id, amount, evidence
            session.add(payment)
            counts[status] += 1
        used.update(released)  # what no period on the new day took stays the old day's record
    session.commit()
    return counts


def paid_periods(session: Session, today: date) -> set[tuple[int, date]]:
    """The (bill, due date) periods from today on whose payment the ledger already shows, paid today or early.
    The plan leaves them out: that money has left the balance, or rides the card that was charged."""
    rows = session.exec(select(Payment.bill_id, Payment.due_date).where(Payment.status == "paid", Payment.due_date >= today)).all()
    return {(bill_id, due) for bill_id, due in rows}


def unposted_periods(session: Session, today: date) -> set[tuple[int, date]]:
    """The (bill, due date) periods whose due date passed in the last few days, while a payment can still post (four
    days, or the bill's own grace), with no payment in the ledger yet. That money has not left the account: the plan
    counts it as going out today, so rent due on a Saturday that posts on Monday is never spendable on Sunday.
    Worked out from the ledger itself, not only from the matching job's last run, so a payment that posted an hour
    ago is not counted again; a charge the bank holds as pending is already off the balance and counts as paid."""
    from .billdates import bill_dates

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    if not bills:
        return set()
    reach = max(AFTER_DAYS, max(int(b.grace_days or 0) for b in bills))
    earliest = today - timedelta(days=reach)
    series_keys = {s.id: s.merchant_key for s in session.exec(select(RecurringSeries)).all()}
    transactions = session.exec(select(Transaction).where(Transaction.date >= earliest - timedelta(days=BEFORE_DAYS), Transaction.date <= today, Transaction.amount > 0)).all()
    rows = session.exec(select(Payment).where(Payment.due_date >= earliest - timedelta(days=LOOKBACK_DAYS))).all()
    paid = {(p.bill_id, p.due_date) for p in rows if p.status == "paid"}
    used = {p.transaction_id for p in rows if p.transaction_id}
    out: set[tuple[int, date]] = set()
    for bill in bills:
        start = today - timedelta(days=max(AFTER_DAYS, int(bill.grace_days or 0)))
        if bill.created_on and bill.created_on > start:
            start = bill.created_on  # never a period from before the bill was on the list
        for due in bill_dates(bill, start, today - timedelta(days=1)):
            if (bill.id, due) in paid:
                continue
            match = _match_for(bill, bill_keys(bill), series_keys.get(bill.series_id) if bill.series_id else None, due, transactions, used)
            if match is not None:
                used.add(match.id)
                continue
            out.add((bill.id, due))
    return out


def anchor_from_ledger(session: Session, bill: Bill, today: date) -> date | None:
    """The next due date of a bill that comes every few months, from the ledger: its series' next expected date,
    else its last payment (a transaction under its names, near its amount, in the last fourteen months) plus one
    cycle, moved on by whole cycles to today or later. None when the ledger does not show one."""
    from .billdates import MONTH_STEPS, _add_months

    months = MONTH_STEPS.get((bill.cadence or "monthly").lower(), 1)
    if months <= 1:
        return None
    found: date | None = None
    series = session.get(RecurringSeries, bill.series_id) if bill.series_id else None
    if series is not None and series.next_expected:
        found = series.next_expected
    else:
        keys = bill_keys(bill)
        tolerance = max(2.0, float(bill.amount or 0.0) * (0.4 if bill.amount_mode == "estimate" else 0.15))
        rows = session.exec(select(Transaction).where(Transaction.amount > 0, Transaction.date >= today - timedelta(days=430), Transaction.date <= today).order_by(Transaction.date.desc())).all()
        last = next((t.date for t in rows if abs(t.amount - float(bill.amount or 0.0)) <= tolerance and _looks_like(keys, None, merchant_key(t.merchant))), None)
        if last is not None:
            found = _add_months(last, months, last.day)
    if found is None:
        return None
    day = found.day
    for _ in range(100):
        if found >= today:
            break
        found = _add_months(found, months, day)
    return found


def payments_overview(session: Session, today: date) -> dict:
    """Each period of the bills on the register: past due, late, paid and coming. A bill that is no longer on it
    (undone, or turned off) has no periods here; its rows are kept, unshown."""
    bills = {b.id: b for b in session.exec(select(Bill).where(Bill.active == True)).all()}  # noqa: E712
    rows = [p for p in session.exec(select(Payment).where(Payment.due_date >= today - timedelta(days=45)).order_by(Payment.due_date)).all() if p.bill_id in bills]

    def out(p: Payment) -> dict:
        b = bills.get(p.bill_id)
        # A period still to come is owed at the bill's amount as it stands now (a confirmed notice or an edit changed it
        # after the last matching run); the matched and the missed keep what they were matched or missed at.
        amount = b.amount if (b is not None and p.status == "scheduled") else p.amount
        return {"bill_id": p.bill_id, "name": b.name if b else "?", "kind": b.kind if b else "bill", "due_date": p.due_date.isoformat(), "amount": amount, "status": p.status,
                "paid_date": p.paid_date.isoformat() if p.paid_date else None, "evidence": p.evidence, "autopay": b.autopay if b else False, "source": f"payment:{p.id}"}

    return {
        "past_due": [out(p) for p in rows if p.status == "past_due"],
        "late": [out(p) for p in rows if p.status == "late"],
        "paid": [out(p) for p in rows if p.status == "paid" and p.due_date >= today - timedelta(days=45)],
        "scheduled": [out(p) for p in rows if p.status == "scheduled"],
    }
