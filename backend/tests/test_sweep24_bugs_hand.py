"""Sweep 24: the hand model of sweep 10 and sweep 23 widened to every household. Sweep 23 recomputed the moved
definitions from the rows only where the fixture's events reach; on sweep 7's random households it took the windows as
the engine states them. Here the whole plan is worked out again from the rows, spreadsheet-style, for any household:
paydays by each cadence and from several paychecks, bills by each cadence with their anchors, first and last days and
the check that pays them, bills and plans riding a card, each way of paying a card, the matching register, and from
those Cover per account and window with the set-aside, Saving's per-check table, Borrowing's per-plan monthly figure and
installments_month, the brief's past-due count and the purchase check's by_paycheck. Nothing in the model below calls
the engine; the engine is imported only to produce the figures under comparison.

Checked at sweep 10's three dates, at five random days of the simulated quarter and on five random households, and on
more of each (the seeds below): every difference is a finding, pinned in test_sweep24_bugs.py when the engine was wrong."""

from __future__ import annotations

import calendar
import json
import random
import re
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.db import bump_version, engine
from app.engine.household import seed
from app.engine.payments import match_payments
from app.models import Account, Advance, Bill, Debt, Income, InstallmentPlan, Payment, PlannedPayment, RecurringSeries, Setting, Transaction
from tests.test_sweep10_bugs import DATES, r2

SHARE = 25  # the suggested share; no share is chosen on any of these households
PACE_DAYS = 56
LARGE = 500.0
OBLIGATIONS = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities", "Borrowing"}
PAST_DUE_DAYS = 90
QUARTER_START = date(2026, 10, 14)
QUARTER_DAYS = [QUARTER_START + timedelta(days=d) for d in sorted(random.Random(24).sample(range(1, 101), 5))]
HOUSEHOLD_SEEDS = [24001, 24002, 24003, 24004, 24005]  # five more of sweep 7's households, with its first five (random.Random(7100 + n))
SWEEP_SEVEN_SEEDS = [7101, 7102, 7103, 7104, 7105]


def key(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


# ---- dates ------------------------------------------------------------------------------------------------------


def clamp(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def month_later(d: date, n: int, day: int) -> date:
    m = d.month - 1 + n
    return clamp(d.year + m // 12, m % 12 + 1, day)


def before_weekend(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def semi_checks(y: int, m: int, pay_days: str) -> tuple[date, date]:
    """(the check near the 15th, the check at the turn of the month), each on the business day before a weekend."""
    mid = before_weekend(date(y, m, 15))
    if pay_days == "1,15":
        return mid, before_weekend(date(y, m, 1))
    return mid, before_weekend(clamp(y, m, 31))


def position(d: date) -> str:
    return "mid" if 8 <= d.day <= 22 else "end"


def pay_day_of(inc: Income) -> int:
    return int(inc.pay_day or 0) or inc.next_date.day


def next_check(d: date, inc: Income) -> date:
    """The payday after `d`, by the paycheck's own cadence and days."""
    cadence = inc.cadence or "biweekly"
    if cadence == "weekly":
        return d + timedelta(days=7)
    if cadence == "monthly":
        return month_later(d, 1, pay_day_of(inc))
    if cadence == "semimonthly":
        y, m = d.year, d.month
        while True:
            for c in sorted(semi_checks(y, m, inc.pay_days or "")):
                if c > d:
                    return c
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return d + timedelta(days=14)


def checks_from(first: date, inc: Income, n: int) -> list[date]:
    out = [first]
    while len(out) < n:
        if (inc.cadence or "biweekly") == "monthly":
            out.append(month_later(first, len(out), pay_day_of(inc)))  # each month's date from the first, so a short month does not shorten the day for good
        else:
            out.append(next_check(out[-1], inc))
    return out


def on_day(day: int, start: date) -> date:
    day = max(1, min(31, int(day or 1)))
    d = clamp(start.year, start.month, day)
    return d if d >= start else month_later(d, 1, day)


def bill_days(b: Bill, start: date, end: date) -> list[date]:
    """The days a bill comes due from `start` through `end`: monthly on its due day; weekly and every two weeks by
    stepping from its anchor; quarterly, twice a year and yearly from the anchor's month; never outside its first and
    last day. A bill that comes every few months with no anchor has no days."""
    if b.starts_on is not None and b.starts_on > start:
        start = b.starts_on
    if b.ends_on is not None and b.ends_on < end:
        end = b.ends_on
    if start > end:
        return []
    cadence = (b.cadence or "monthly").lower()
    day = max(1, min(31, int(b.due_day or 1)))
    out = []
    if cadence in ("weekly", "biweekly"):
        step = timedelta(days=7 if cadence == "weekly" else 14)
        # No next date on file: its weeks count from its first due day, on or after its first date or the day it was added (a day that stays put).
        d = b.anchor_date or on_day(day, b.starts_on or b.created_on or date(2000, 1, 1))
        while d < start:
            d += step
        while d <= end:
            out.append(d)
            d += step
        return out
    months = {"quarterly": 3, "semiannual": 6, "yearly": 12}.get(cadence, 1)
    if months == 1:
        d = on_day(day, start)
    elif b.anchor_date is None:
        return []
    else:
        d = clamp(b.anchor_date.year, b.anchor_date.month, day)
        while d < start:
            d = month_later(d, months, day)
    while d <= end:
        out.append(d)
        d = month_later(d, months, day)
    return out


def check_that_pays(due: date, which: str, pay_days: str) -> date:
    """The mid or end-of-month check that pays a bill due on `due`: the nearest within a week either side, else the
    last one before."""
    months = [(due.year - 1, 12) if due.month == 1 else (due.year, due.month - 1), (due.year, due.month), (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)]
    candidates = [semi_checks(y, m, pay_days)[0 if which == "mid" else 1] for y, m in months]
    near = [c for c in candidates if abs((c - due).days) <= 7]
    if near:
        return min(near, key=lambda c: (abs((c - due).days), c))
    before = [c for c in candidates if c <= due]
    return max(before) if before else min(candidates)


# ---- the rows ---------------------------------------------------------------------------------------------------


class World:
    def __init__(self, session: Session):
        every = session.exec(select(Account)).all()
        self.every = every
        self.theirs = {a.id for a in every if a.person_id is not None and not a.shared}
        self.accounts = [a for a in every if a.active and a.id not in self.theirs]
        self.by_id = {a.id: a for a in self.accounts}
        self.kinds = {a.id: a.kind for a in every}
        self.tx = sorted(session.exec(select(Transaction)).all(), key=lambda t: t.id)
        self.bills = sorted((b for b in session.exec(select(Bill).where(Bill.active == True)).all()), key=lambda b: b.id)  # noqa: E712
        self.payable = [b for b in self.bills if b.pay_from_account_id not in self.theirs]
        self.plans = [p for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all() if p.pay_from_account_id not in self.theirs]  # noqa: E712
        self.all_plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
        self.advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
        self.debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
        self.incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
        self.series = {s.id: s for s in session.exec(select(RecurringSeries)).all()}
        self.matched = session.exec(select(Payment).limit(1)).first() is not None  # the matching job has run
        settings = {s.key: json.loads(s.value) for s in session.exec(select(Setting)).all()}
        self.buffer = float(settings.get("buffer", 200.0))
        self.card_mode = (settings.get("cards") or {}).get("pay_from", "auto")
        self.register_rows = session.exec(select(Payment)).all()
        self.planned = session.exec(select(PlannedPayment).where(PlannedPayment.done == False)).all()  # noqa: E712
        self.streams = [r for r in self.series.values() if not r.confirmed and r.active and r.suggested_kind == "installment"]


# ---- the paydays ------------------------------------------------------------------------------------------------


def landed(W: World, inc: Income, start: date, today: date) -> bool:
    expected = abs(float(inc.expected_net or 0.0))
    series = W.series.get(inc.series_id) if inc.series_id else None
    own = key(series.merchant_key) if series is not None else ""
    for t in W.tx:
        if not (start <= t.date <= today and t.amount < 0 and t.is_income and not t.pending):
            continue
        if own:
            mine = key(t.merchant)
            if not mine or not (mine == own or mine.startswith(own) or own.startswith(mine)):
                continue
        elif inc.account_id and t.account_id != inc.account_id:
            continue
        if abs(abs(t.amount) - expected) <= max(50.0, expected * 0.4):
            return True
    return False


def scheduled(inc: Income, today: date) -> date:
    d = inc.next_date
    while d < today:
        d = next_check(d, inc)
    return d


def unpaid_check(W: World, inc: Income, today: date) -> date:
    d = scheduled(inc, today)
    if abs(float(inc.expected_net or 0.0)) <= 0:
        return d
    for _ in range(3):
        if d - timedelta(days=2) > today or not landed(W, inc, d - timedelta(days=2), today):
            break
        d = next_check(d, inc)
    return d


def net_on(inc: Income, d: date) -> float:
    if (inc.cadence or "") == "semimonthly" and position(d) == "end" and inc.expected_net_end is not None:
        return float(inc.expected_net_end)
    return float(inc.expected_net)


def pay_schedule(W: World, today: date) -> dict:
    upcoming = sorted(((unpaid_check(W, i, today), i) for i in W.incomes), key=lambda p: p[0])
    if not upcoming:
        return {"known": False, "paydays": [today + timedelta(days=14 * (k + 1)) for k in range(8)], "amounts": {}, "pay_days": "", "by_check": False}
    amounts: dict[date, float] = {}
    for first, inc in upcoming:
        for d in checks_from(first, inc, 12):
            amounts[d] = amounts.get(d, 0.0) + net_on(inc, d)
    semi = next((i for _, i in upcoming if (i.cadence or "") == "semimonthly"), upcoming[0][1])
    return {"known": True, "paydays": sorted(amounts), "amounts": amounts, "pay_days": semi.pay_days or "", "by_check": (semi.cadence or "") == "semimonthly"}


# ---- the register -----------------------------------------------------------------------------------------------


def names_of(W: World, b: Bill) -> list[str]:
    before = json.loads(b.match_names or "[]") if getattr(b, "match_names", "") else []
    return [k for k in (key(b.name), key(b.pays_to or ""), *(key(n) for n in before)) if k]


def looks(W: World, b: Bill, t: Transaction) -> bool:
    mine = key(t.merchant)
    if not mine:
        return False
    names = names_of(W, b)
    series = W.series.get(b.series_id) if b.series_id else None
    if series is not None and series.merchant_key:
        names.append(series.merchant_key)
    return any(mine == k or mine.startswith(k) or k.startswith(mine) for k in names)


def drift(b: Bill) -> float:
    return max(2.0, b.amount * (0.4 if b.amount_mode == "estimate" else 0.15))


def near_row(W: World, b: Bill, due: date, rows: list[Transaction], used: set[int], but: int | None = None) -> Transaction | None:
    """The row that pays this period: six days before the due day to its grace after, under the bill's names within its
    drift, or at its exact amount and not a transfer."""
    for t in rows:
        if t.id in used or t.id == but or not (due - timedelta(days=6) <= t.date <= due + timedelta(days=max(4, b.grace_days))):
            continue
        if (abs(t.amount - b.amount) <= drift(b) and looks(W, b, t)) or (abs(t.amount - b.amount) < 0.01 and not t.is_transfer):
            return t
    return None


def register(W: World, today: date) -> dict[tuple[int, date], dict]:
    """What the matching job leaves after one run from an empty register: each period of the last sixty days (from the
    day the bill was added) through three weeks ahead, paid by its row, or in the same month by a row under its names;
    else late, past due or still to come."""
    rows = sorted((t for t in W.tx if today - timedelta(days=96) <= t.date <= today and t.amount > 0 and not t.pending), key=lambda t: (t.date, t.id))  # oldest first: two rows that could pay one period, the earlier does
    used: set[int] = set()
    out: dict[tuple[int, date], dict] = {}
    for b in W.bills:
        monthly = (b.cadence or "monthly") == "monthly"
        heal = today - timedelta(days=90)
        dues = bill_days(b, max(heal, b.created_on or heal), today + timedelta(days=21))
        for due in dues:
            if due < today - timedelta(days=60):
                continue
            t = near_row(W, b, due, rows, used)
            if t is None and monthly and due < today:
                for r in rows:
                    if r.id in used or (r.date.year, r.date.month) != (due.year, due.month) or not (abs(r.amount - b.amount) <= drift(b) and looks(W, b, r)):
                        continue
                    open_near = [d for d in dues if d != due and out.get((b.id, d), {}).get("status") != "paid" and d - timedelta(days=6) <= r.date <= d + timedelta(days=max(4, b.grace_days))]
                    if any(near_row(W, b, d, rows, used, but=r.id) is None for d in open_near):
                        continue  # near another open period with no row of its own: that period's payment
                    t = r
                    break
            if t is not None:
                used.add(t.id)
                out[(b.id, due)] = {"status": "paid", "tx": t.id, "amount": t.amount}
            elif due >= today:
                out[(b.id, due)] = {"status": "scheduled", "tx": None, "amount": b.amount}
            else:
                out[(b.id, due)] = {"status": "late" if (today - due).days <= max(3, b.grace_days) else "past_due", "tx": None, "amount": b.amount}
    return out


def past_due_list(W: World, today: date, reg: dict) -> list[tuple[int, date, float]]:
    """Needs you's list: every active bill's periods past due in the last ninety days, oldest first."""
    return sorted(((b, d, r["amount"]) for (b, d), r in reg.items() if r["status"] == "past_due" and d >= today - timedelta(days=PAST_DUE_DAYS)), key=lambda x: x[1])


def unposted(W: World, today: date, reg: dict) -> set[tuple[int, date]]:
    """Periods whose due day passed in the last few days (four, or the bill's grace), with no payment in the ledger yet."""
    out: set[tuple[int, date]] = set()
    if not W.bills:
        return out
    reach = max(4, max(int(b.grace_days or 0) for b in W.bills))
    rows = sorted((t for t in W.tx if today - timedelta(days=reach + 6) <= t.date <= today and t.amount > 0), key=lambda t: (t.date, t.id))
    used = {r["tx"] for r in reg.values() if r["tx"]}
    for b in W.bills:
        start = today - timedelta(days=max(4, int(b.grace_days or 0)))
        if b.created_on and b.created_on > start:
            start = b.created_on
        for due in bill_days(b, start, today - timedelta(days=1)):
            if reg.get((b.id, due), {}).get("status") == "paid":
                continue
            t = near_row(W, b, due, rows, used)
            if t is not None:
                used.add(t.id)
                continue
            out.add((b.id, due))
    return out


# ---- the plan ---------------------------------------------------------------------------------------------------


def pace_of(W: World, today: date) -> dict:
    names = {key(b.name) for b in W.payable}
    total = cash = card = 0.0
    by: dict[int, float] = {}
    for t in sorted(W.tx, key=lambda t: (t.date, t.id)):  # the ledger's own order, so a sum at a half cent rounds alike
        if not (today - timedelta(days=PACE_DAYS) <= t.date < today) or t.account_id in W.theirs or t.is_transfer or t.is_card_payment or t.is_income or t.is_ignored or t.amount <= 0 or t.category in OBLIGATIONS or (t.merchant or "").lower() in {n for n in names}:
            continue
        total += t.amount
        by[t.account_id] = by.get(t.account_id, 0.0) + t.amount
        if W.kinds.get(t.account_id) == "card":
            card += t.amount
        else:
            cash += t.amount
    return {"cash": r2(cash / PACE_DAYS), "card": r2(card / PACE_DAYS), "total": r2(total / PACE_DAYS), "by": {k: r2(v / PACE_DAYS) for k, v in by.items()}}


def debt_of(W: World, a: Account) -> Debt | None:
    return next((d for d in W.debts if d.account_id == a.id or key(d.name) == key(a.name)), None)


def cards_with_a_bill(W: World) -> dict[int, set[int]]:
    """The cards a bill filed under Credit Card Payment pays: the card it names, else the card whose minimum it is."""
    cards = [a for a in W.accounts if a.kind == "card"]
    out: dict[int, set[int]] = {}
    for b in W.payable:
        if key(b.category) != "credit card payment":
            continue
        text = (b.name or "").lower()
        named = []
        for a in cards:
            d = debt_of(W, a)
            if any(len(n.strip()) >= 3 and n.strip().lower() in text for n in (a.name or "", d.name if d else "")):
                named.append(a)
        at_min = [(a, debt_of(W, a)) for a in cards if debt_of(W, a) is not None and (debt_of(W, a).minimum or 0) > 0 and abs(b.amount - debt_of(W, a).minimum) < 1.0]
        if len(at_min) > 1:
            at_min = [(a, d) for a, d in at_min if d.due_day and int(d.due_day) == int(b.due_day)] or at_min[:1]
        card = named[0] if named else (at_min[0][0] if at_min else None)
        if card is not None:
            out.setdefault(card.id, set()).add(b.id)
    return out


def plan_days(p: InstallmentPlan, horizon: date) -> list[tuple[date, float]]:
    """A plan's payments still to make: the last `remaining` of the lender's dates, else stepping from its next date."""
    rows = sorted((r for r in json.loads(p.schedule or "[]") if r.get("date") and r.get("amount")), key=lambda r: r["date"])
    if rows:
        return [(date.fromisoformat(r["date"]), r["amount"]) for r in (rows[len(rows) - p.remaining:] if p.remaining else [])]
    out, d = [], p.next_date
    for _ in range(p.remaining):
        if d > horizon:
            break
        out.append((d, p.installment))
        d += timedelta(days=p.cadence_days)
    return out


LENDERS = {"affirm": "Affirm", "klarna": "Klarna", "afterpay": "Afterpay", "sezzle": "Sezzle"}


def lender_of(text: str) -> str | None:
    low = (text or "").lower()
    return next((name for word, name in LENDERS.items() if re.search(rf"(?<![a-z]){word}(?![a-z])", low)), None)


def covers(p: InstallmentPlan, s: RecurringSeries) -> bool:
    """A stream the ledger shows is this plan's own payments: it carries the plan's merchant, or it is the same
    lender's at the plan's installment, within a dollar."""
    mine = (s.merchant_key or s.merchant or "").lower()
    if p.merchant and mine.startswith(p.merchant.lower()):
        return True
    lender = lender_of(s.merchant)
    return bool(lender) and lender == (lender_of(f"{p.provider} {p.merchant}") or p.provider) and abs(float(p.installment or 0.0) - float(s.typical_amount or 0.0)) < 1.0


def stream_days(s: RecurringSeries, start: date, end: date) -> list[date]:
    if s.cadence in ("weekly", "biweekly"):
        step, d, out = timedelta(days=7 if s.cadence == "weekly" else 14), s.next_expected, []
        while d < start:
            d += step
        while d <= end:
            out.append(d)
            d += step
        return out
    out, k, d = [], 0, s.next_expected
    while d <= end:
        if d >= start:
            out.append(d)
        k += 1
        d = month_later(s.next_expected, k, s.next_expected.day)
    return out


def events_for(W: World, today: date, sched: dict, reg: dict, horizon_days: int, pace: dict) -> dict:
    """Every dated obligation through the horizon: what leaves cash (`math`) and what rides a card (`card_bills`)."""
    horizon = today + timedelta(days=horizon_days)
    paydays = sched["paydays"]
    late = unposted(W, today, reg)
    paid = {k for k, r in reg.items() if r["status"] == "paid" and k[1] >= today}
    # Periods the register holds as paid whose days the model has not listed (none on one run): none.
    events: list[dict] = []
    for b in W.payable:
        payer = W.by_id.get(b.pay_from_account_id)
        from_card = payer is not None and payer.kind == "card"
        paid_from = b.paid_from if b.paid_from in ("mid", "end") and sched["by_check"] else "due"
        base = {"name": b.name, "amount": r2(b.amount), "kind": b.kind, "src": f"bill:{b.id}", "bill": b.id, "card": b.pay_from_account_id if from_card else None, "from": b.pay_from_account_id, "paid_from": paid_from}
        for (bid, d) in sorted(late, key=lambda k: k[1]):
            if bid == b.id:
                events.append({**base, "date": today, "wdate": today, "due": d})
        for d in bill_days(b, today, horizon):
            if (b.id, d) in paid:
                continue
            events.append({**base, "date": d, "wdate": check_that_pays(d, paid_from, sched["pay_days"]) if paid_from in ("mid", "end") else d})
    for p in W.plans:
        payer = W.by_id.get(p.pay_from_account_id)
        from_card = payer is not None and payer.kind == "card"
        for d, amount in plan_days(p, today + timedelta(days=max(90, horizon_days))):
            if d < today - timedelta(days=4) or d > today + timedelta(days=max(90, horizon_days)):
                continue
            at = max(d, today)
            events.append({"name": p.merchant or p.provider, "amount": r2(amount), "kind": "installment", "src": f"plan:{p.id}", "card": p.pay_from_account_id if from_card else None, "from": p.pay_from_account_id, "date": at, "wdate": at, "paid_from": "due"})
    for a in W.advances:
        if a.repay_date >= today:
            events.append({"name": f"{a.provider} repays", "amount": r2(a.amount + a.fee), "kind": "advance", "src": f"advance:{a.id}", "card": None, "from": None, "date": a.repay_date, "wdate": a.repay_date, "paid_from": "due"})
    for s in W.streams:
        if any(covers(p, s) for p in W.plans):
            continue  # the stream is a plan's own payments: the plan counts them
        for d in stream_days(s, today, horizon):
            events.append({"name": s.merchant, "amount": r2(s.typical_amount), "kind": "installment", "src": f"series:{s.id}", "card": None, "from": None, "date": d, "wdate": d, "paid_from": "due"})
    planned_off: dict[int, float] = {}
    active_cards = [a for a in W.every if a.active and a.kind == "card"]
    for pl in W.planned:
        if (today - pl.date).days > 14:
            continue  # two weeks past its date with nothing posted: stale, not a reservation
        at = max(pl.date, today)
        events.append({"name": pl.name, "amount": r2(pl.amount), "kind": "planned", "src": f"planned:{pl.id}", "card": None, "from": None, "date": at, "wdate": at, "paid_from": "due"})
        if pl.kind == "card_payment":
            target = pl.to_account_id or (active_cards[0].id if len(active_cards) == 1 else None)
            if target is not None:
                planned_off[target] = planned_off.get(target, 0.0) + float(pl.amount)
    card_bills = [e for e in events if e["card"]]
    cash = [e for e in events if not e["card"]]
    covered = cards_with_a_bill(W)
    payoffs: list[dict] = []
    mode = W.card_mode
    if mode != "off":
        for a in (a for a in W.accounts if a.kind == "card" and a.id not in covered):
            balance = max(0.0, float(a.balance or 0.0) - planned_off.get(a.id, 0.0))
            debt = debt_of(W, a)
            if mode == "minimum" or (mode == "auto" and debt is not None and (debt.minimum or 0) > 0):
                if debt is None or not (debt.minimum or 0) or balance <= 0:
                    continue
                day = int(debt.due_day or paydays[0].day)
                d = on_day(day, today)
                while d <= horizon:
                    payoffs.append({"name": f"{a.name} minimum", "amount": r2(min(debt.minimum, balance)), "kind": "card_payment", "src": f"debt:{debt.id}", "card": None, "from": None, "date": d, "wdate": d, "paid_from": "due", "pays": a.id})
                    d = on_day(day, d + timedelta(days=1))
                continue
            dates = [d for d in paydays if today <= d <= horizon and (mode in ("split", "next", "auto") or position(d) == mode)]
            if not dates:
                continue
            mine = [e for e in card_bills if e["card"] == a.id]
            rate = float(pace["by"].get(a.id, 0.0))
            if balance <= 0 and not mine and rate <= 0:
                continue
            shares = [round(balance / 2, 2), round(balance - round(balance / 2, 2), 2)] if mode in ("split", "auto") and len(dates) > 1 else [balance]
            prev = today
            for i, d in enumerate(dates):
                amount = (shares[i] if i < len(shares) else 0.0) + sum(e["amount"] for e in mine if prev <= e["date"] < d) + rate * (d - prev).days
                if amount > 0.5:
                    payoffs.append({"name": f"{a.name} payment", "amount": r2(amount), "kind": "card_payment", "src": f"account:{a.id}", "card": None, "from": None, "date": d, "wdate": d, "paid_from": mode, "pays": a.id})
                prev = d
    return {"math": sorted(cash + payoffs, key=lambda e: e["date"]), "card_bills": card_bills, "payoffs": payoffs, "covered": covered, "planned_off": planned_off}


def kept_for(items: list[dict]) -> float:
    items = [e for e in items if e["kind"] != "card_payment"]
    return sum(e["amount"] for e in items if e["amount"] >= LARGE) / 2 + sum(e["amount"] for e in items if e["amount"] < LARGE)


def plan_of(W: World, today: date) -> dict:
    """Safe to spend, the windows and the daily line, from the rows."""
    sched = pay_schedule(W, today)
    paydays, amounts = sched["paydays"], sched["amounts"]
    reg = register(W, today) if W.matched else {}
    pace = pace_of(W, today)
    horizon_days = max(75, (paydays[5] - today).days)
    ev = events_for(W, today, sched, reg, horizon_days, pace)
    math = ev["math"]
    cash = sum(float(a.balance or 0.0) for a in W.accounts if (a.spendable if a.spendable is not None else a.kind in ("checking", "cash")))
    p0, p1, p2 = paydays[:3]
    before = [e for e in math if today <= e["date"] and e["wdate"] < p0 and e["kind"] != "advance"]
    window = [e for e in math if p0 <= e["wdate"] < p1 and e["kind"] != "advance"]
    next_window = [e for e in math if p1 <= e["wdate"] < p2 and e["kind"] != "advance"]
    adv_due = [e for e in math if e["kind"] == "advance" and e["date"] <= p0]
    due_before = sum(e["amount"] for e in before)
    adv_before = sum(e["amount"] for e in adv_due if e["date"] < p0)
    adv_on_payday = sum(e["amount"] for e in adv_due) - adv_before
    net = amounts.get(p0, 0.0)
    set_aside = kept_for(next_window)
    shortfall = max(0.0, sum(e["amount"] for e in window) + set_aside + adv_on_payday - net)
    safe = cash - due_before - adv_before - W.buffer - shortfall
    windows = []
    carried = cash - due_before - W.buffer
    for k in range(4):
        start, end, after = paydays[k], paydays[k + 1] - timedelta(days=1), paydays[k + 2] - timedelta(days=1)
        inside = [e for e in math if start <= e["wdate"] <= end and e["kind"] != "advance"]
        adv = [e for e in math if e["kind"] == "advance" and (start <= e["date"] <= end or (k == 0 and e["date"] <= start))]
        following = [e for e in math if end < e["wdate"] <= after and e["kind"] != "advance"]
        aside = kept_for(following)
        bills, advs = sum(e["amount"] for e in inside), sum(e["amount"] for e in adv)
        windows.append({"start": start, "end": end, "paycheck": amounts.get(start, 0.0), "carried": r2(carried), "bills": r2(bills), "advances": r2(advs), "aside": r2(aside), "safe": r2(carried + amounts.get(start, 0.0) - advs - bills - aside)})
        carried = aside
    return {"sched": sched, "paydays": paydays, "amounts": amounts, "reg": reg, "pace": pace, "ev": ev, "math": math, "cash": r2(cash), "before": before, "window": window, "next_window": next_window, "due_before": r2(due_before),
            "adv_before": r2(adv_before), "adv_on_payday": r2(adv_on_payday), "set_aside": r2(set_aside), "shortfall": r2(shortfall), "safe": r2(safe), "windows": windows, "net": net, "raw_cash": cash, "raw_due_before": due_before, "raw_adv_before": adv_before, "raw_set_aside": set_aside, "raw_shortfall": shortfall}


def daily_line(W: World, today: date, P: dict) -> list[dict]:
    paydays, amounts = P["paydays"], P["amounts"]
    following, released = paydays[1], paydays[2]
    reserved_for = {(e["src"], e["date"]) for e in P["next_window"]}
    balance, reserved, spent = P["raw_cash"], 0.0, 0.0
    out = []
    for i in range(60):
        d = today + timedelta(days=i)
        if d == released:
            reserved = 0.0
        if d in amounts or d in paydays:
            got = amounts.get(d, 0.0)
            if got:
                balance += got
                if d == paydays[0] and P["raw_set_aside"]:
                    reserved = P["raw_set_aside"]
        for e in (e for e in P["math"] if e["date"] == d):
            balance -= e["amount"]
            if reserved and following <= d < released and (e["src"], d) in reserved_for:
                reserved = max(0.0, reserved - e["amount"])
        if i > 0:
            spent += P["pace"]["cash"]
        out.append({"date": d.isoformat(), "available": r2(balance - W.buffer - reserved), "at_pace": r2(balance - W.buffer - reserved - spent), "cash": r2(balance)})
    return out


# ---- Saving, Cover, Borrowing, the brief, the purchase check -------------------------------------------------------


def landed_today(W: World, today: date, P: dict) -> bool:
    return any(scheduled(i, today) == today for i in W.incomes) and P["paydays"][0] > today


def saving_rows(W: World, today: date, P: dict) -> list[dict]:
    """What each check leaves after the everyday cash pace over its window's days; the first window also pays the days
    before its payday (today excluded, as the daily line spends from day two), and a check that landed today leaves what
    it keeps for its window and the pace through the day before the next check."""
    pace = P["pace"]["cash"]
    rows = []
    if landed_today(W, today, P):
        left = P["raw_cash"] - P["raw_due_before"] - P["raw_adv_before"] - W.buffer - kept_for(P["window"])
        left = r2(left) - pace * (P["paydays"][0] - today).days
        rows.append({"date": today.isoformat(), "left": r2(left), "keep": r2(max(0.0, left) * SHARE / 100)})
    for k, w in enumerate(P["windows"]):
        days = (w["end"] - w["start"]).days + 1 + (max(0, (w["start"] - today).days - 1) if k == 0 else 0)
        left = w["safe"] - pace * days
        rows.append({"date": w["start"].isoformat(), "left": r2(left), "keep": r2(max(0.0, left) * SHARE / 100)})
    return rows


def deposit_account(W: World) -> Account | None:
    spend = [a for a in W.accounts if (a.spendable if a.spendable is not None else a.kind in ("checking", "cash")) and a.kind != "card"]
    inc = min(W.incomes, key=lambda i: i.next_date) if W.incomes else None
    if inc is not None and inc.account_id in {a.id for a in spend}:
        return W.by_id[inc.account_id]
    return max(spend, key=lambda a: a.balance or 0.0) if spend else None


def payer_of(W: World, e: dict, deposit_id: int | None) -> int | None:
    """Which spending account an event leaves: the one it names, else the one the check lands in."""
    acc = W.by_id.get(e.get("from")) if e.get("from") else None
    return acc.id if acc is not None and acc.kind != "card" and _spends(acc) else deposit_id


def rungs_of(W: World, P: dict) -> dict:
    """The ladder from the next two checks' bills and advances and the usual pace, from the rows."""
    first = P["windows"][:2]
    must = max(w["bills"] + w["advances"] for w in first)
    span = (first[-1]["end"] - first[0]["start"]).days + 1
    month = sum(w["bills"] + w["advances"] for w in first) * 30.0 / max(span, 1) + P["pace"]["cash"] * 30.0

    def up(v: float) -> float:
        return float(int(max(0.0, v) / 10.0 + (0.0 if max(0.0, v) / 10.0 == int(max(0.0, v) / 10.0) else 1.0)) * 10)

    targets = [500.0, up(must), up(month), up(month * 3), up(month * 6)]
    balance = sum(float(a.balance or 0.0) for a in W.accounts if a.kind in ("checking", "savings", "cash") and not _spends(a))
    nxt = next((k for k, t in enumerate(targets) if balance < t), None)
    return {"targets": targets, "month": r2(month), "must": r2(must), "balance": r2(balance), "next": nxt}


def cover_rows(W: World, today: date, P: dict) -> dict:
    """Each spending account over two windows (today to the day before payday; payday to the day before the next): its
    bills by the day they leave it, its own everyday pace, the buffer in the account the check lands in, the check's
    set-aside for the window after staying there too, and its lowest close. Each card: what it will hold on its
    statement day and what it is paid in each window; over its limit, or its own bill past due, is crit; over thirty
    percent on the statement day is warn. A savings account against the next rung."""
    p1, p2 = P["paydays"][:2]
    deposit = deposit_account(W)
    deposit_id = deposit.id if deposit else None
    ev = events_for(W, today, P["sched"], P["reg"], max(60, (p2 - today).days), P["pace"])
    aside = P["windows"][0]["aside"]
    out: dict = {"accounts": {}, "deposit": deposit_id}
    past = past_due_list(W, today, P["reg"]) if W.matched else [(p.bill_id, p.due_date, p.amount) for p in W.register_rows if p.status == "past_due"]
    rung = rungs_of(W, P)
    for a in W.accounts:
        if _spends(a) and a.kind != "card":
            mine = [e for e in ev["math"] if payer_of(W, e, deposit_id) == a.id]
            rate = max(0.0, float(P["pace"]["by"].get(a.id, 0.0)))
            is_deposit = a.id == deposit_id
            keep = W.buffer if is_deposit else 0.0
            has = float(a.balance or 0.0)
            w1_items = [e for e in mine if today <= e["date"] < p1]
            w2_items = [e for e in mine if p1 <= e["date"] < p2]
            w1_bills, w1_day = sum(e["amount"] for e in w1_items), rate * max(0, (p1 - timedelta(days=1) - today).days)
            w1_needs = w1_bills + w1_day + keep
            w1_left = has - w1_needs
            inflow = P["amounts"].get(p1, 0.0) if is_deposit and P["sched"]["known"] else 0.0
            set_aside = aside if is_deposit else 0.0
            w2_has = w1_left + keep
            w2_bills, w2_day = sum(e["amount"] for e in w2_items), rate * (p2 - p1).days
            w2_needs = w2_bills + w2_day + keep + set_aside
            w2_left = w2_has + inflow - w2_needs

            def lowest(balance: float, items: list[dict], first: date, last: date, money_in: float, from_start: bool) -> tuple[float, date]:
                low, on = None, first
                d = first
                while d <= last:
                    if money_in and d == p1:
                        balance += money_in
                    balance -= sum(e["amount"] for e in items if e["date"] == d) + (rate if d > first or from_start else 0.0)
                    if low is None or balance < low:
                        low, on = balance, d
                    d += timedelta(days=1)
                return (balance if low is None else low), on

            low1, on1 = lowest(has - keep, w1_items, today, p1 - timedelta(days=1), 0.0, False)
            low2, on2 = lowest(w2_has - keep, w2_items, p1, p2 - timedelta(days=1), inflow, True)
            windows = [
                {"has": r2(has), "inflow": 0.0, "bills": r2(w1_bills), "everyday": r2(w1_day), "buffer": r2(keep), "set_aside": 0.0, "needs": r2(w1_needs), "left": r2(w1_left), "low": r2(low1), "low_on": on1.isoformat()},
                {"has": r2(w2_has), "inflow": r2(inflow), "bills": r2(w2_bills), "everyday": r2(w2_day), "buffer": r2(keep), "set_aside": r2(set_aside), "needs": r2(w2_needs), "left": r2(w2_left), "low": r2(low2), "low_on": on2.isoformat()},
            ]
            status = "crit" if r2(w1_left) < 0 else "warn" if r2(w2_left) < 0 else "good"
            if a.balance is None:
                status = "warn"
            out["accounts"][a.id] = {"role": "spend", "status": status, "windows": windows, "no_balance": a.balance is None}
        elif a.kind == "card":
            debt = debt_of(W, a)
            limit = a.credit_limit or (debt.credit_limit if debt else None)
            balance = float(a.balance or 0.0)
            by_bill = ev["covered"].get(a.id, set())
            overdue = [x for x in past if x[0] in by_bill]
            pays = sorted([e for e in ev["payoffs"] if e.get("pays") == a.id] + [e for e in ev["math"] if e.get("bill") in by_bill], key=lambda e: e["date"])
            statement = on_day(int(debt.statement_day), today) if debt and debt.statement_day else None
            rate = float(P["pace"]["by"].get(a.id, 0.0))
            projected = None
            if statement:
                riding = sum(e["amount"] for e in ev["card_bills"] if e["card"] == a.id and today <= e["date"] < statement)
                projected = max(0.0, balance - ev["planned_off"].get(a.id, 0.0) + riding + rate * (statement - today).days - sum(e["amount"] for e in pays if e["date"] < statement))
            target = max(0.0, (projected or 0.0) - 0.30 * limit) if (limit and projected is not None) else 0.0
            status = "crit" if overdue or (limit and balance > limit) else "warn" if (limit and projected is not None and projected / limit > 0.30) else "good"
            out["accounts"][a.id] = {"role": "card", "status": status, "overdue": len(overdue), "statement_balance": r2(projected) if projected is not None else None, "to_target": r2(target),
                                     "per_window": [r2(sum(e["amount"] for e in pays if e["date"] < p1)), r2(sum(e["amount"] for e in pays if p1 <= e["date"] < p2))]}
        elif a.kind == "savings" or (a.kind == "cash" and not _spends(a)):
            short = max(0.0, rung["targets"][rung["next"]] - rung["balance"]) if rung["next"] is not None else 0.0
            target = rung["targets"][rung["next"]] if rung["next"] is not None else None
            status = "good" if not target or short <= 0 else ("warn" if rung["balance"] > 0 else "crit")
            out["accounts"][a.id] = {"role": "save", "status": status, "short": r2(short)}
        else:
            out["accounts"][a.id] = {"role": "other", "status": "good"}
    return out


def safe_by_account(W: World, today: date, P: dict) -> dict[int, float]:
    """Each spending account's share of safe to spend: what it holds less what leaves it before payday; the buffer, the
    advances and any shortfall belong to the account the check lands in."""
    deposit = deposit_account(W)
    deposit_id = deposit.id if deposit else None
    out = {}
    for a in W.accounts:
        if not _spends(a) or a.kind == "card":
            continue
        due = sum(e["amount"] for e in P["before"] if (payer_of(W, e, deposit_id) if payer_of(W, e, deposit_id) in W.by_id else deposit_id) == a.id)
        goes = due + W.buffer + P["raw_adv_before"] + P["raw_shortfall"] if a.id == deposit_id else due + 0.0 + 0.0 + 0.0
        out[a.id] = r2(float(a.balance or 0.0) - r2(goes))
    return out


def _spends(a: Account) -> bool:
    return bool(a.spendable) if a.spendable is not None else a.kind in ("checking", "cash")


def plan_month(p: InstallmentPlan) -> float:
    per = p.installment * 30.0 / max(1, p.cadence_days or 30)
    left = p.balance if p.balance else p.installment * max(0, p.remaining)
    return r2(min(per, left) if left > 0 else per)


def left_of(p: InstallmentPlan, today: date) -> float:
    """What a plan still owes: the lender's balance, else its schedule's last payments, else the installment times what is left."""
    rows = sorted(json.loads(p.schedule or "[]"), key=lambda r: r["date"])
    return float(p.balance) if p.balance else (sum(r["amount"] for r in rows[len(rows) - p.remaining:]) if rows and p.remaining else p.installment * p.remaining)


def by_paycheck(charges: list[tuple[date, float]], P: dict) -> list[dict]:
    return [{"payday": w["start"].isoformat(), "through": w["end"].isoformat(), "adds": r2(sum(a for d, a in charges if w["start"] <= d <= w["end"]))} for w in P["windows"][:3]]


def pay_in(n: int, amount: float, on: date) -> list[tuple[date, float]]:
    part = round(amount / n, 2)
    return [(on + timedelta(days=14 * i), part if i < n - 1 else round(amount - part * (n - 1), 2)) for i in range(n)]


# ---- the comparison ---------------------------------------------------------------------------------------------


def compare(session: Session, today: date) -> list[str]:
    """Every difference between the engine and the model, as words."""
    from app.engine.borrowing import borrowing_overview
    from app.engine.borrowing_plan import borrowing_plan
    from app.engine.cover import account_cover
    from app.engine.decisions import purchase_check
    from app.engine.reports import debt_mountain
    from app.engine.saving import saving_plan
    from app.engine.snapshot import finance_snapshot

    W = World(session)
    P = plan_of(W, today)
    out: list[str] = []

    def same(what: str, got, want) -> None:  # noqa: ANN001
        if got != want:
            out.append(f"{today} {what}: engine {got!r}, hand {want!r}")

    snap = finance_snapshot(session, today)
    same("next paycheck", (snap["next_paycheck"]["date"], snap["next_paycheck"]["amount"]), (P["paydays"][0].isoformat(), r2(P["net"])))
    if W.matched:
        got_reg = {(p.bill_id, p.due_date): p.status for p in W.register_rows}
        lookback = today - timedelta(days=60)
        same("the register", {k: v for k, v in got_reg.items() if v != "scheduled" and k[1] >= lookback}, {k: r["status"] for k, r in P["reg"].items() if r["status"] != "scheduled"})
    pace = snap["usual_pace"]
    same("pace", (pace["per_day_cash"], pace["per_day_card"], {int(k): v for k, v in pace["per_day_by_account"].items()}), (P["pace"]["cash"], P["pace"]["card"], P["pace"]["by"]))
    same("before payday", sorted((e["source"], e["amount"], e["date"]) for e in snap["due_before_payday"]["items"] if not e.get("via_card")), sorted((e["src"], e["amount"], e["date"].isoformat()) for e in P["before"]))
    same("safe to spend", snap["safe_to_spend"]["amount"], P["safe"])
    same("safe's parts", snap["safe_to_spend"]["parts"], {"cash": P["cash"], "due_before_payday": P["due_before"], "advances": P["adv_before"], "advances_on_payday": P["adv_on_payday"], "buffer": r2(W.buffer), "next_window_shortfall": P["shortfall"]})
    same("the windows", [(w["start"], w["end"], w["paycheck"], w["carried_in"], w["bills"]["total"], w["advances"]["total"], w["set_aside"]["total"], w["safe"]) for w in snap["windows"]],
         [(w["start"].isoformat(), w["end"].isoformat(), r2(w["paycheck"]), w["carried"], w["bills"], w["advances"], w["aside"], w["safe"]) for w in P["windows"]])
    same("the daily line", [(p["date"], p["available"], p["at_pace"], p["cash"]) for p in snap["projection"]], [(p["date"], p["available"], p["at_pace"], p["cash"]) for p in daily_line(W, today, P)])
    # Saving's per-check table
    saving = saving_plan(session, today, snap=snap)
    same("saving per check", [(r["date"], r["left"], r["keep"]) for r in saving["per_check"]], [(r["date"], r["left"], r["keep"]) for r in saving_rows(W, today, P)])
    # Cover per account and window
    cover = account_cover(session, today, saving=saving)
    want = cover_rows(W, today, P)
    same("the deposit account", cover["deposit_account_id"], want["deposit"])
    shares, hand_shares = {r["id"]: r["safe"] for r in snap["safe_to_spend"]["by_account"]}, safe_by_account(W, today, P)
    same("safe by account, within a half cent of a set-aside", set(shares), set(hand_shares))
    same("safe by account", {k: shares[k] for k in shares if abs(shares[k] - hand_shares.get(k, 1e12)) > 0.011}, {})
    fields = ("has", "inflow", "bills", "everyday", "buffer", "set_aside", "needs", "left", "low", "low_on")
    for a in cover["accounts"]:
        h = want["accounts"][a["id"]]
        same(f"cover {a['name']} role", a["role"], h["role"])
        same(f"cover {a['name']} status", a["status"], h["status"])
        if a["role"] == "spend" and not h["no_balance"]:
            for k, (w, hw) in enumerate(zip(a["windows"], h["windows"], strict=True)):
                same(f"cover {a['name']} window {k + 1}", {f: w[f] for f in fields}, hw)
        elif a["role"] == "card":
            same(f"cover {a['name']} per window", a["per_window"], h["per_window"])
            same(f"cover {a['name']} on its statement day", a["statement_balance"], h["statement_balance"])
            same(f"cover {a['name']} to reach the target", abs((a["to_target"] or 0.0) - (h["to_target"] or 0.0)) <= 0.011, True)  # a half cent of a sum rounds by the order it was added in
            if h["overdue"]:
                same(f"cover {a['name']} says past due", a["headline"].startswith("Past due"), True)
        elif a["role"] == "save":
            same(f"cover {a['name']} short of the next rung", a["short"], h["short"])
    rung = rungs_of(W, P)
    same("the rungs", ([r["target"] for r in saving["rungs"]], saving["month"], saving["must_pays"], saving["balance"]), (rung["targets"], rung["month"], rung["must"], rung["balance"]))
    # The payday sheet: the check's set-aside stays, its share of what it leaves moves
    pay_on = today if any(scheduled(i, today) == today for i in W.incomes) else P["paydays"][0]
    rows = saving_rows(W, today, P)
    keep_row = next((r["keep"] for r in rows if r["date"] == pay_on.isoformat()), 0.0)
    aside_on = kept_for(P["window"]) if landed_today(W, today, P) else P["windows"][0]["aside"] if pay_on == P["paydays"][0] else None
    if P["sched"]["known"] and deposit_account(W) is not None and aside_on is not None:
        same("the sheet's set-aside", [r["amount"] for r in cover["sheet"] if r["kind"] == "keep"], [r2(aside_on)] if r2(aside_on) > 0 else [])
        if any(a.kind in ("savings", "cash") and not _spends(a) for a in W.accounts):
            same("the sheet's move", [r["amount"] for r in cover["sheet"] if r["kind"] == "move"], [keep_row] if keep_row > 0 else [])
    # Borrowing: each register plan's monthly figure on Borrowing, Debts and the stairs, and the month's total
    plans = {p.id: plan_month(p) for p in W.plans if p.remaining > 0}
    page = borrowing_overview(session, today)
    same("borrowing per plan", {i["plan_id"]: i["per_month"] for i in page["installments"] if i["plan_id"]}, plans)
    if not any(i["active"] and not i["plan_id"] for i in page["installments"]):
        same("installments_month", page["cost"]["installments_month"], r2(sum(plans.values())))
    same("the stairs' loans", {l["id"]: l["per_month"] for l in borrowing_plan(session, today)["loans"]}, plans)
    same("debts' plans", sorted(i["minimum"] for i in debt_mountain(session, today)["items"] if i["kind"] == "bnpl" and i["id"] in plans), sorted(plans.values()))
    same("what the plans have left to pay", page["loans_left_to_pay"], r2(sum(left_of(p, today) for p in W.plans)))
    # The brief's past-due count
    lines = [i["text"] for i in snap["brief"] if "past due with no payment" in i["text"]]
    if W.matched:
        due = past_due_list(W, today, P["reg"])
        n = int(re.match(r"\d+", lines[0]).group()) if lines else 0
        same("the brief's past-due count", n, len(due))
        same("the badge", snap["pending"]["past_due"], len(due))
    # The purchase check's by_paycheck
    same("by_paycheck pay in four", purchase_check(session, today, 400.0, installments=4)["by_paycheck"], by_paycheck(pay_in(4, 400.0, today), P))
    same("by_paycheck one charge on the next payday", purchase_check(session, today, 120.0, on=P["paydays"][0])["by_paycheck"], by_paycheck([(P["paydays"][0], 120.0)], P))
    return out


# ---- wider households: what sweep 7's does not hold --------------------------------------------------------------


def widen(session: Session, rng: random.Random, today: date, made: dict) -> None:
    """On top of sweep 7's household: a second spending account with its own bills, plan and spending; savings marked
    to spend; an account kept out of the plan; a second person's own account and card with bills, a plan and spending;
    a shared account; a check that has landed, on the day or early; statement days on the cards; a plan with the
    lender's balance; planned payments; a stream the ledger found and the owner has not named. The register is matched
    afresh, once, with all of it in the ledger."""
    from sqlalchemy import delete

    from tests.test_sweep7_bugs_db import _tx

    was_matched = session.exec(select(Payment).limit(1)).first() is not None
    session.exec(delete(Payment))
    checking, cards = made["checking"], made["cards"]
    bill_no = 100

    def bills_on(account: Account, n: int) -> None:
        nonlocal bill_no
        for _ in range(n):
            bill_no += 1
            session.add(Bill(name=f"Bill {bill_no}", amount=round(rng.uniform(5, 700), 2), due_day=rng.randint(1, 31), pay_from_account_id=account.id, category=rng.choice(["Bills & Utilities", "Insurance", ""]), created_on=today - timedelta(days=rng.randint(0, 200))))

    def spending_on(account: Account, n: int) -> None:
        for _ in range(n):
            _tx(session, account, today - timedelta(days=rng.randint(1, 56)), rng.choice(["Example Grocery", "Example Fuel", "Example Cafe"]), rng.uniform(3, 90))

    if rng.random() < 0.6:
        wallet = Account(name="Wallet", institution="Example Bank", kind=rng.choice(["cash", "checking"]), balance=round(rng.uniform(-50, 1500), 2), connector="manual", external_id="wallet")
        session.add(wallet)
        session.flush()
        bills_on(wallet, rng.randint(1, 4))
        spending_on(wallet, rng.randint(0, 12))
        session.add(InstallmentPlan(provider="Example BNPL", merchant="Wallet plan", total=240.0, installment=60.0, count=4, remaining=rng.randint(1, 4), next_date=today + timedelta(days=rng.randint(-3, 20)), cadence_days=rng.choice([14, 30]), pay_from_account_id=wallet.id))
    if rng.random() < 0.3:
        session.add(Account(name="Spendable Savings", institution="Example Bank", kind="savings", spendable=True, balance=round(rng.uniform(0, 900), 2), connector="manual", external_id="spend-sav"))
    if rng.random() < 0.3:
        session.add(Account(name="Kept Checking", institution="Example Bank", kind="checking", spendable=False, balance=round(rng.uniform(0, 900), 2), connector="manual", external_id="kept"))
    if rng.random() < 0.5:
        theirs = Account(name="Partner Checking", institution="Example Bank", kind="checking", balance=round(rng.uniform(0, 900), 2), person_id=7, connector="manual", external_id="partner")
        card = Account(name="Partner Card", institution="Example Bank", kind="card", balance=round(rng.uniform(0, 900), 2), credit_limit=2000.0, person_id=7, connector="manual", external_id="partner-card")
        session.add_all([theirs, card])
        session.flush()
        bills_on(theirs, rng.randint(1, 3))
        bills_on(card, rng.randint(0, 2))
        spending_on(theirs, rng.randint(0, 12))
        session.add(InstallmentPlan(provider="Example BNPL", merchant="Partner plan", total=300.0, installment=50.0, count=6, remaining=rng.randint(1, 6), next_date=today + timedelta(days=rng.randint(0, 20)), cadence_days=30, pay_from_account_id=theirs.id))
    if rng.random() < 0.3:
        joint = Account(name="Joint Checking", institution="Example Bank", kind="checking", balance=round(rng.uniform(0, 900), 2), person_id=7, shared=True, connector="manual", external_id="joint")
        session.add(joint)
        session.flush()
        bills_on(joint, rng.randint(1, 2))
        spending_on(joint, rng.randint(0, 6))
    for inc in made["incomes"]:
        d = inc.next_date
        while d < today:
            d = next_check(d, inc)
        if d - timedelta(days=2) <= today and rng.random() < 0.7:  # the check has landed, on the day or up to two days early
            _tx(session, checking, today - timedelta(days=rng.randint(0, max(0, 2 - (d - today).days))), f"{inc.name} Payroll", -float(inc.expected_net), "Income", is_income=True)
    for debt in session.exec(select(Debt)).all():
        if rng.random() < 0.6:
            debt.statement_day = rng.randint(1, 28)
            session.add(debt)
    for plan in session.exec(select(InstallmentPlan)).all():
        if rng.random() < 0.25:
            plan.balance = round(rng.uniform(5, plan.installment * max(1, plan.remaining)), 2)
            session.add(plan)
    if cards and rng.random() < 0.4:
        session.add(PlannedPayment(name="Card payment", amount=round(rng.uniform(20, 300), 2), date=today + timedelta(days=rng.randint(-20, 10)), kind="card_payment", to_account_id=rng.choice(cards).id))
    if rng.random() < 0.3:
        session.add(PlannedPayment(name="Sent to a friend", amount=round(rng.uniform(5, 200), 2), date=today + timedelta(days=rng.randint(-5, 10)), kind="other"))
    if rng.random() < 0.3:
        session.add(RecurringSeries(merchant_key="example lender", merchant="Example Lender", category="Loan Payment", cadence=rng.choice(["monthly", "biweekly"]), typical_amount=round(rng.uniform(20, 200), 2), latest_amount=20.0, first_seen=today - timedelta(days=60), last_date=today - timedelta(days=5),
                                    next_expected=today + timedelta(days=rng.randint(0, 20)), kind="unknown", confirmed=False, suggested_kind="installment", active=True))
    session.commit()
    if was_matched:
        match_payments(session, today)


# ---- the runs ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("today", DATES, ids=[d.isoformat() for d in DATES])
def test_the_model_at_sweep_tens_three_dates(today: date):
    with Session(engine) as session:
        seed(session)
        match_payments(session, today)
        bump_version()
        assert compare(session, today) == []


def feed_quarter(session: Session, day: date) -> None:
    from tests.test_sweep23_bugs_hand import feed_quarter as feed

    feed(session, day)
    bump_version()


@pytest.mark.parametrize("day", QUARTER_DAYS, ids=[d.isoformat() for d in QUARTER_DAYS])
def test_the_model_on_random_days_of_the_simulated_quarter(day: date):
    with Session(engine) as session:
        feed_quarter(session, day)
        assert compare(session, day) == []


@pytest.mark.parametrize("seed_value", SWEEP_SEVEN_SEEDS + HOUSEHOLD_SEEDS)
def test_the_model_on_random_households(seed_value: int):
    from tests.test_sweep7_bugs_db import _fresh, _household, _today

    rng = random.Random(seed_value)
    today = _today(rng)
    _fresh()
    with Session(engine) as session:
        _household(session, rng, today)
        bump_version()
        assert compare(session, today) == [], seed_value


@pytest.mark.parametrize("seed_value", [24101, 24102, 24103, 24104, 24105])
def test_the_model_on_wider_households(seed_value: int):
    from tests.test_sweep7_bugs_db import _fresh, _household, _today

    rng = random.Random(seed_value)
    today = _today(rng)
    _fresh()
    with Session(engine) as session:
        made = _household(session, rng, today)
        widen(session, rng, today, made)
        bump_version()
        assert compare(session, today) == [], seed_value
