"""Sweep 10: the roadmap's M9.4 and M9.5 done on the synthetic household. The owner-facing figures are worked out
again here, spreadsheet-style, from the raw rows (the models only), by the definitions in docs/PLAN.md sections 5
and 7, and set beside the engine's at three dates: the household's own today (2026-10-14), a month end (2026-10-31)
and a payday (2026-11-13). Nothing in the hand model below calls the engine; the engine is imported only to produce
the figures under comparison, and `seed` and `match_payments` to load the fixture and its payment register the way
test_golden does.

What it found (each pinned by its own test at the end):
- the four paycheck windows were cut at a fixed seventy-five days, so the fourth window's set-aside was short
  whenever the next payday was more than a few days out, and a monthly paycheck's third and fourth windows had no
  bills at all (safe there was the whole check);
- an installment whose day passed with nothing posted dropped out of the plan, where a bill in the same state is
  counted today as money still in the account;
- a bill filed under Credit Card Payment was drawn in the bills band of the coming checks and in the card band of
  the past ones;
- an advance past its repay day, which the plan treats as taken back, still counted as owed on Debts, Borrowing
  ("the plan already holds it back"), the watchlist and Next steps until the sync retired the row.
"""

from __future__ import annotations

import calendar
import json
import re
import statistics
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.db import bump_version, engine
from app.engine.household import seed
from app.engine.payments import match_payments
from app.models import (
    Account,
    Advance,
    Bill,
    CategoryBudget,
    Debt,
    Goal,
    Income,
    InstallmentPlan,
    Paystub,
    Transaction,
    TransactionSplit,
)

DATES = [date(2026, 10, 14), date(2026, 10, 31), date(2026, 11, 13)]
BUFFER = 200.0  # the default buffer rule (store.DEFAULTS); no rule row is on the fixture
PACE_DAYS = 56
LARGE = 500.0
LATE_DAYS = 4  # a due day this many days back with nothing posted is still in the account
# What is a bill rather than everyday spending, as the plan's "usual pace" states its basis
OBLIGATIONS = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities", "Borrowing"}
TAX_WORDS = re.compile(r"federal|social security|medicare|state|local|tax|sdi|oasdi|fica", re.IGNORECASE)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def r2(v: float) -> float:
    return round(float(v), 2)


def mday(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def add_months(d: date, n: int, day: int | None = None) -> date:
    m = d.month - 1 + n
    return mday(d.year + m // 12, m % 12 + 1, day or d.day)


def monthly_dates(day: int, start: date, end: date) -> list[date]:
    out = []
    d = mday(start.year, start.month, day)
    if d < start:
        d = add_months(d, 1, day)
    while d <= end:
        out.append(d)
        d = add_months(d, 1, day)
    return out


def key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def numbers_in(text: str) -> list[float]:
    """Every amount and count in a sentence, with the day numbers of dates ("Oct 16") left out."""
    clean = re.sub(r"\b(?:" + "|".join(MONTHS) + r") \d{1,2}\b", "", text)
    clean = re.sub(r"\b\d{4}\b", "", clean)  # a year in a month name ("November 2026")
    clean = re.sub(r"\b\d+ of \d+\b", "", clean)  # a plan's "4 of 6" is a name, not a figure
    clean = clean.replace("-$", "$-")  # a shortfall is written "-$1,913"; read it as the negative amount it is
    return [float(x.replace(",", "")) for x in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", clean.replace("$", ""))]


# ---------------------------------------------------------------------------------------------------------------
# The hand model: every figure from the rows, by the plan's definitions.


class Rows:
    def __init__(self, session: Session):
        self.accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
        self.tx = session.exec(select(Transaction)).all()
        self.bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
        self.debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
        self.plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
        self.advances = session.exec(select(Advance)).all()
        self.incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
        self.stubs = session.exec(select(Paystub).order_by(Paystub.pay_date)).all()
        self.goals = session.exec(select(Goal).where(Goal.active == True)).all()  # noqa: E712
        self.splits: dict[int, list[TransactionSplit]] = {}
        for s in session.exec(select(TransactionSplit)).all():
            self.splits.setdefault(s.transaction_id, []).append(s)
        self.by_account = {a.id: a for a in self.accounts}

    def spending(self, t: Transaction) -> bool:
        return not (t.is_transfer or t.is_card_payment or t.is_income or t.is_ignored)

    def parts(self, t: Transaction) -> list[tuple[str, float]]:
        return [(s.category, s.amount) for s in self.splits[t.id]] if t.id in self.splits else [(t.category or "Uncategorized", t.amount)]


def hand_paydays(R: Rows, today: date) -> tuple[list[date], float]:
    """Every other Friday from the paycheck's date; the next is the first whose deposit has not landed (a deposit near
    the usual net from two days before it counts as landed)."""
    (inc,) = R.incomes
    assert inc.cadence == "biweekly"
    d = inc.next_date
    while d < today:
        d += timedelta(days=14)
    for _ in range(3):
        start = d - timedelta(days=2)
        if start > today:
            break
        landed = any(t.is_income and t.account_id == inc.account_id and start <= t.date <= today and abs(-t.amount - inc.expected_net) <= max(50.0, 0.4 * inc.expected_net) for t in R.tx)
        if not landed:
            break
        d += timedelta(days=14)
    return [d + timedelta(days=14 * k) for k in range(8)], float(inc.expected_net)


def match(R: Rows, bill: Bill, due: date, today: date, used: set[int]) -> Transaction | None:
    """The row that paid this period: near the due day (six days before to four after, or the grace), under the bill's
    name within its drift, or at its exact amount and not a transfer."""
    tol = max(2.0, bill.amount * (0.4 if bill.amount_mode == "estimate" else 0.15))
    lo, hi = due - timedelta(days=6), min(today, due + timedelta(days=max(LATE_DAYS, bill.grace_days)))
    for t in sorted(R.tx, key=lambda t: (t.date, t.id)):
        if t.id in used or t.amount <= 0 or t.pending or not (lo <= t.date <= hi):
            continue
        close, exact = abs(t.amount - bill.amount) <= tol, abs(t.amount - bill.amount) < 0.01
        named = key(t.merchant).startswith(key(bill.name)) or key(bill.name).startswith(key(t.merchant))
        if (close and named) or (exact and not t.is_transfer):
            return t
    return None


def hand_events(R: Rows, today: date, horizon: date, moved: dict[int, int] | None = None, judge_until: date | None = None) -> list[dict]:
    """Every dated obligation from today through `horizon`: the bills by their due days (one due in the last four days
    with no payment seen leaves today), the pay-later plans' installments, the advances on their repay day, and each
    card's minimum on its due day unless a bill on the register already pays that card. `moved` changes a bill's due
    day from its next cycle on, as the request to the biller asks (sweep 10 checks the date moves with it)."""
    events: list[dict] = []
    used: set[int] = set()
    for b in sorted(R.bills, key=lambda b: b.id):
        assert (b.cadence or "monthly") == "monthly" and b.anchor_date is None and b.starts_on is None and b.ends_on is None
        assert R.by_account[b.pay_from_account_id].kind != "card"
        base = {"name": b.name, "amount": r2(b.amount), "kind": b.kind, "autopay": b.autopay, "category": b.category, "src": ("bill", b.id)}
        for due in monthly_dates(b.due_day, today - timedelta(days=LATE_DAYS), today - timedelta(days=1)):
            t = match(R, b, due, today, used)
            if t is not None:
                used.add(t.id)
            else:
                events.append({**base, "date": today, "late": True, "due": due})
        dates = monthly_dates(b.due_day, today, horizon + timedelta(days=62))
        if moved and b.id in moved and moved[b.id] != b.due_day:
            new = monthly_dates(moved[b.id], today - timedelta(days=31), horizon + timedelta(days=93))
            out = [dates[0]]
            for d in dates[1:]:
                m = min(new, key=lambda c: (abs((c - d).days), -c.toordinal()))
                if judge_until is not None and d < judge_until <= m:
                    m = judge_until - timedelta(days=1)
                out.append(m)
            dates = out
        for due in dates:
            if not (today <= due <= horizon):
                continue
            t = match(R, b, due, today, used)
            if t is not None:
                used.add(t.id)  # paid early: that money has left already
                continue
            events.append({**base, "date": due})
    for p in R.plans:
        assert not (p.schedule and p.schedule != "[]")
        d, left = p.next_date, p.remaining
        while left > 0 and d <= horizon:
            if d >= today - timedelta(days=LATE_DAYS):
                events.append({"name": f"{p.merchant} {p.count - left + 1} of {p.count}", "amount": r2(p.installment), "kind": "installment", "autopay": True, "category": "", "src": ("plan", p.id, d),
                               "date": max(d, today), **({"late": True, "due": d} if d < today else {})})
            d, left = d + timedelta(days=p.cadence_days), left - 1
    for a in R.advances:
        if not a.repaid and a.repay_date >= today:
            events.append({"name": f"{a.provider} repays", "amount": r2(a.amount + a.fee), "kind": "advance", "autopay": True, "category": "", "src": ("advance", a.id), "date": a.repay_date})
    card_bills = [b for b in R.bills if key(b.category) == "credit card payment"]
    for a in R.accounts:
        if a.kind != "card" or (a.balance or 0) <= 0:
            continue
        debt = next((d for d in R.debts if d.account_id == a.id), None)
        if debt is None or not debt.minimum:
            continue
        covered = any(key(a.name) in key(b.name) or abs(b.amount - debt.minimum) < 1.0 for b in card_bills)
        if covered:
            continue
        for due in monthly_dates(int(debt.due_day), today, horizon):
            events.append({"name": f"{a.name} minimum", "amount": r2(min(debt.minimum, a.balance)), "kind": "card_payment", "autopay": False, "category": "", "src": ("debt", debt.id, due), "date": due})
    return sorted(events, key=lambda e: e["date"])


def set_aside_of(items: list[dict]) -> float:
    items = [e for e in items if e["kind"] != "card_payment"]
    return sum(e["amount"] for e in items if e["amount"] >= LARGE) / 2 + sum(e["amount"] for e in items if e["amount"] < LARGE)


def hand_pace(R: Rows, today: date) -> dict:
    names = {key(b.name) for b in R.bills}
    total = cash = card = 0.0
    by_account: dict[int, float] = {}
    for t in R.tx:
        if not (today - timedelta(days=PACE_DAYS) <= t.date < today) or not R.spending(t) or t.amount <= 0 or t.category in OBLIGATIONS or key(t.merchant) in names:
            continue
        total += t.amount
        by_account[t.account_id] = by_account.get(t.account_id, 0.0) + t.amount
        if R.by_account[t.account_id].kind == "card":
            card += t.amount
        else:
            cash += t.amount
    return {"per_day": r2(total / PACE_DAYS), "per_day_cash": r2(cash / PACE_DAYS), "per_day_card": r2(card / PACE_DAYS), "by_account": {k: r2(v / PACE_DAYS) for k, v in by_account.items()}}


def hand_plan(R: Rows, today: date, *, moved: dict[int, int] | None = None, judge_until: date | None = None, line_days: int = 60) -> dict:
    """Safe to spend and its parts, the four windows, and the daily line, from the rows."""
    cash = sum(float(a.balance or 0.0) for a in R.accounts if a.kind in ("checking", "cash") and a.spendable is None and (a.shared or a.person_id is None))
    paydays, net = hand_paydays(R, today)
    horizon = paydays[5]
    events = hand_events(R, today, horizon, moved, judge_until)
    p0, p1, p2 = paydays[:3]
    before = [e for e in events if today <= e["date"] < p0 and e["kind"] != "advance"]
    window = [e for e in events if p0 <= e["date"] < p1 and e["kind"] != "advance"]
    next_window = [e for e in events if p1 <= e["date"] < p2 and e["kind"] != "advance"]
    advances_due = [e for e in events if e["kind"] == "advance" and e["date"] <= p0]
    due_before = sum(e["amount"] for e in before)
    advances_before = sum(e["amount"] for e in advances_due if e["date"] < p0)
    advances_on_payday = sum(e["amount"] for e in advances_due) - advances_before
    window_total = sum(e["amount"] for e in window)
    set_aside = set_aside_of(next_window)
    shortfall = max(0.0, window_total + set_aside + advances_on_payday - net)
    safe = cash - due_before - advances_before - BUFFER - shortfall
    windows = []
    carried = cash - due_before - BUFFER
    for k in range(4):
        start, end, after_end = paydays[k], paydays[k + 1] - timedelta(days=1), paydays[k + 2] - timedelta(days=1)
        in_window = [e for e in events if start <= e["date"] <= end and e["kind"] != "advance"]
        adv = [e for e in events if e["kind"] == "advance" and (start <= e["date"] <= end or (k == 0 and e["date"] <= start))]
        following = [e for e in events if end < e["date"] <= after_end and e["kind"] != "advance"]
        aside = set_aside_of(following)
        bills_total, adv_total = sum(e["amount"] for e in in_window), sum(e["amount"] for e in adv)
        windows.append({"start": start, "end": end, "paycheck": net, "carried_in": r2(carried), "bills": r2(bills_total), "advances": r2(adv_total), "set_aside": r2(aside),
                        "safe": r2(carried + net - adv_total - bills_total - aside), "items": in_window, "payments": len(in_window), "set_aside_items": [e for e in following if e["kind"] != "card_payment"],
                        "card": r2(sum(e["amount"] for e in in_window if e["kind"] == "card_payment" or key(e["category"]) == "credit card payment"))})
        carried = aside
    pace = hand_pace(R, today)
    # The daily line: cash less the buffer and what is reserved (the next check's set-aside, spent only by the bills
    # it was kept for, free again at the check after), and `at_pace` also spends the usual cash pace from day two.
    reserved_for = {(e["src"], e["date"]) for e in next_window}
    by_day: dict[date, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    balance = cash
    reserved = spent = 0.0
    line = []
    for i in range(line_days):
        d = today + timedelta(days=i)
        if d == p2:
            reserved = 0.0
        if d in paydays:
            balance += net
            if d == p0 and set_aside:
                reserved = set_aside
        for e in by_day.get(d, []):
            balance -= e["amount"]
            if reserved and p1 <= d < p2 and (e["src"], d) in reserved_for:
                reserved = max(0.0, reserved - e["amount"])
        if i > 0:
            spent += pace["per_day_cash"]
        line.append({"date": d, "available": r2(balance - BUFFER - reserved), "at_pace": r2(balance - BUFFER - reserved - spent), "cash": r2(balance)})
    low = min(line, key=lambda p: p["at_pace"])
    weeks = []
    ws = today - timedelta(days=(today.weekday() + 1) % 7)
    while ws <= today + timedelta(days=60):
        in_week = [p for p in line if ws <= p["date"] <= ws + timedelta(days=6)]
        weeks.append({"start": ws, "low": min(p["at_pace"] for p in in_week) if in_week else None})
        ws += timedelta(days=7)
    groups: dict[str, float] = {}
    for e in before:
        g = e["kind"] if e["kind"] in ("card_payment", "installment", "subscription") else "bill"
        groups[g] = groups.get(g, 0.0) + e["amount"]
    return {"cash": r2(cash), "paydays": paydays, "net": net, "events": events, "before": before, "due_before": r2(due_before), "groups": {k: r2(v) for k, v in groups.items()},
            "advances_due": advances_due, "advances_before": r2(advances_before), "advances_on_payday": r2(advances_on_payday), "window": window, "window_total": r2(window_total),
            "next_window": next_window, "set_aside": r2(set_aside), "shortfall": r2(shortfall), "safe": r2(safe), "windows": windows, "line": line, "low": low, "weeks": weeks, "pace": pace,
            "goals": r2(sum(g.pace for g in R.goals if g.target > g.funded))}


def usual_net(R: Rows, before: date) -> float:
    nets = [p.net for p in sorted(R.stubs, key=lambda p: p.pay_date, reverse=True) if p.pay_date < before][:4]
    return round(statistics.median(nets), 2) if len(nets) >= 2 else float(R.incomes[0].expected_net)


def hand_debts(R: Rows, today: date) -> dict:
    """The mountain and the two payoff orders, month by month."""
    open_adv = [a for a in R.advances if not a.repaid and a.repay_date >= today]
    items = [{"name": d.name, "balance": d.balance, "apr": d.apr, "minimum": d.minimum, "kind": d.kind} for d in R.debts]
    for p in R.plans:
        left = p.installment * p.remaining
        items.append({"name": f"{p.provider} · {p.merchant} · {p.remaining} of {p.count} left", "balance": r2(left), "apr": 0.0, "minimum": r2(min(p.installment * 30.0 / p.cadence_days, left)), "kind": "bnpl"})
    items += [{"name": f"{a.provider} advance", "balance": r2(a.amount + a.fee), "apr": None, "minimum": None, "kind": "advance"} for a in open_adv]
    total = r2(sum(i["balance"] for i in items))
    for i in items:
        i["share"] = round(i["balance"] / total, 4)
    rated = [i for i in items if i["kind"] in ("card", "loan") and i["apr"] is not None]

    def simulate(order: str) -> dict:
        live = [{**i, "paid_on": None, "interest": 0.0} for i in items if i["kind"] in ("card", "loan", "bnpl")]
        start = today.replace(day=1)
        month = 0
        while any(d["balance"] > 0.005 for d in live) and month < 600:
            month += 1
            freed = 0.0
            for d in live:
                if d["balance"] <= 0.005:
                    freed += d["minimum"] if order != "minimums" else 0.0
                    continue
                interest = d["balance"] * d["apr"] / 1200.0
                d["interest"] += interest
                d["balance"] += interest
                pay = min(d["balance"], d["minimum"])
                d["balance"] -= pay
                if order != "minimums":
                    freed += d["minimum"] - pay
            if order != "minimums":
                pot = freed
                targets = sorted((d for d in live if d["balance"] > 0.005), key=(lambda d: (-d["apr"], d["balance"])) if order == "avalanche" else (lambda d: (d["balance"], -d["apr"])))
                for d in targets:
                    if pot <= 0.005:
                        break
                    pay = min(pot, d["balance"])
                    d["balance"] -= pay
                    pot -= pay
            for d in live:
                if d["balance"] <= 0.005 and d["paid_on"] is None:
                    d["paid_on"] = add_months(start, month)
        done = all(d["balance"] <= 0.005 for d in live)
        return {"months": month if done else None, "interest": r2(sum(d["interest"] for d in live)) if done else None, "debt_free": add_months(start, month).isoformat() if done else None,
                "debts": {d["name"]: (d["paid_on"].isoformat() if d["paid_on"] else None, r2(d["interest"]) if d["paid_on"] else None) for d in live}}

    return {"total": total, "items": items, "minimums_month": r2(sum(i["minimum"] or 0.0 for i in items)), "interest_month": r2(sum(i["balance"] * i["apr"] / 1200.0 for i in rated)),
            "ways": {o: simulate(o) for o in ("minimums", "avalanche", "snowball")}}


def hand_borrowing(R: Rows, today: date, H: dict) -> dict:
    """The register's advances (the ledger's rows carry a synthetic name the Borrowing page does not know as an app,
    so its figures come from the register alone): fees in ninety days, what stands outstanding, each app's rate."""
    since = today - timedelta(days=90)
    recent = [a for a in R.advances if a.taken_on >= since]
    open_adv = [a for a in R.advances if not a.repaid and a.repay_date >= today]
    providers = {}
    for a in recent:
        row = providers.setdefault(a.provider, {"advanced": 0.0, "fees": 0.0, "outstanding": 0.0, "n": 0, "days": []})
        row["advanced"] += a.amount
        row["fees"] += a.fee
        row["n"] += 1
        if a.repaid:
            row["days"].append(max(1, (a.repay_date - a.taken_on).days))
    for a in open_adv:
        providers[a.provider]["outstanding"] += a.amount + a.fee
    costs = {}
    for name, row in providers.items():
        held = sum(max(1, (a.repay_date - a.taken_on).days) * a.amount for a in recent if a.provider == name) / row["advanced"]
        per_100 = row["fees"] / row["advanced"] * 100
        costs[name] = {"fee_per_100": r2(per_100), "days_held": round(held, 1), "as_yearly_rate": round(per_100 / 100 * 365 / held * 100, 1), "fee_share": r2(row["fees"] / row["advanced"]),
                       "annual_rate": r2(row["fees"] / row["advanced"] * 365.0 / statistics.median(row["days"])) if row["days"] else None}
    last_payday = H["paydays"][0] - timedelta(days=14)
    since_payday = [a for a in R.advances if last_payday <= a.taken_on <= today]
    fees = r2(sum(a.fee for a in recent))
    # The pay-later plans' stairs: what the loans take each month, the finish line, and what frees up after it
    months = [f"{add_months(today.replace(day=1), i):%Y-%m}" for i in range(18)]
    pays = {p.id: [p.next_date + timedelta(days=p.cadence_days * k) for k in range(p.remaining)] for p in R.plans}
    stairs = [(m, r2(sum(p.installment for p in R.plans for d in pays[p.id] if d.strftime("%Y-%m") == m))) for m in months]
    while stairs and stairs[-1][1] == 0 and len(stairs) > 6:
        stairs.pop()
    finish = [p for p in R.plans if 0 < p.remaining <= 2]
    freed = None
    if finish:
        last = max(pays[p.id][-1] for p in finish).strftime("%Y-%m")
        before, after = [s for s in stairs if s[0] <= last], [s for s in stairs if s[0] > last]
        if before and after:
            peak = max(before, key=lambda s: s[1])
            freed = {"month": after[0][0], "per_month_before": peak[1], "per_month_after": after[0][1], "freed": r2(peak[1] - after[0][1])}
    least = min(w["safe"] - H["pace"]["per_day"] * ((w["end"] - w["start"]).days + 1) for w in H["windows"])
    extra = float(int(least * 0.5 / 25) * 25) if H["safe"] >= 0 and least > 0 else 0.0
    return {"fees_90d": fees, "fees_month": r2(fees * 30.0 / 90), "advances_90d": len(recent), "advanced_90d": r2(sum(a.amount for a in recent)), "outstanding": len(open_adv), "outstanding_total": r2(sum(a.amount + a.fee for a in open_adv)),
            "providers": providers, "costs": costs, "since_payday": {"n": len(since_payday), "amount": r2(sum(a.amount for a in since_payday))},
            "stairs": stairs, "finish": {p.merchant: (p.remaining, pays[p.id][-1].isoformat(), r2(p.installment * p.remaining)) for p in finish}, "freed": freed,
            "left_to_pay": r2(sum(p.installment * p.remaining for p in R.plans)), "least": r2(least), "extra": extra}


# ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def household(request):
    """The fixture at one of the three dates, its payment register matched as the jobs would have left it."""
    today = request.param
    with Session(engine) as session:
        seed(session)
        match_payments(session, today)
        yield session, today, Rows(session)


dated = pytest.mark.parametrize("household", DATES, ids=[d.isoformat() for d in DATES], indirect=True)


@dated
def test_safe_to_spend_its_parts_the_windows_and_the_low_point(household):
    from app.engine.snapshot import finance_snapshot

    session, today, R = household
    H = hand_plan(R, today)
    snap = finance_snapshot(session, today)
    s = snap["safe_to_spend"]
    assert snap["next_paycheck"]["date"] == H["paydays"][0].isoformat() and snap["next_paycheck"]["amount"] == H["net"]
    assert [p["date"] for p in snap["paydays"]] == [d.isoformat() for d in H["paydays"][:4]]
    assert s["parts"] == {"cash": H["cash"], "due_before_payday": H["due_before"], "advances": H["advances_before"], "advances_on_payday": H["advances_on_payday"], "buffer": BUFFER, "next_window_shortfall": H["shortfall"]}
    assert s["amount"] == H["safe"]
    assert sorted((e["name"], e["amount"], e["date"]) for e in snap["due_before_payday"]["items"]) == sorted((e["name"], e["amount"], e["date"].isoformat()) for e in H["before"])
    assert {g["key"]: g["total"] for g in snap["due_before_payday"]["groups"]} == H["groups"]
    assert snap["advances"]["total"] == r2(H["advances_before"] + H["advances_on_payday"])
    assert snap["window"]["total"] == H["window_total"] and snap["set_aside"]["total"] == H["set_aside"]
    assert sorted((e["name"], e["amount"], e["date"]) for e in snap["set_aside"]["items"]) == sorted((e["name"], e["amount"], e["date"].isoformat()) for e in H["next_window"])
    for w, h in zip(snap["windows"], H["windows"], strict=True):
        assert (w["start"], w["end"]) == (h["start"].isoformat(), h["end"].isoformat())
        assert {k: w[k] for k in ("paycheck", "carried_in", "safe")} == {k: h[k] for k in ("paycheck", "carried_in", "safe")}
        assert (w["bills"]["total"], w["advances"]["total"], w["set_aside"]["total"]) == (h["bills"], h["advances"], h["set_aside"])
        assert sorted((e["name"], e["amount"], e["date"]) for e in w["bills"]["items"]) == sorted((e["name"], e["amount"], e["date"].isoformat()) for e in h["items"])
        assert sorted((e["name"], e["amount"]) for e in w["set_aside"]["items"]) == sorted((e["name"], e["amount"]) for e in h["set_aside_items"])
    pace = snap["usual_pace"]
    assert (pace["per_day"], pace["per_day_cash"], pace["per_day_card"]) == (H["pace"]["per_day"], H["pace"]["per_day_cash"], H["pace"]["per_day_card"])
    assert {int(k): v for k, v in pace["per_day_by_account"].items()} == H["pace"]["by_account"]
    # The sixty-day line, day by day, and its low point
    assert [(p["date"], p["available"], p["at_pace"], p["cash"]) for p in snap["projection"]] == [(p["date"].isoformat(), p["available"], p["at_pace"], p["cash"]) for p in H["line"]]
    low = min(snap["projection"], key=lambda p: p["at_pace"])
    assert (low["date"], low["at_pace"]) == (H["low"]["date"].isoformat(), H["low"]["at_pace"])
    assert [(w["start"], w["low"]) for w in snap["calendar"]["weeks"]] == [(w["start"].isoformat(), w["low"]) for w in H["weeks"]]
    assert snap["goals"]["per_paycheck"] == H["goals"] and s["after_goals"] == r2(H["safe"] - H["goals"])


@dated
def test_every_number_in_the_brief_and_the_why_sentences_is_a_recomputed_figure(household):
    from app.engine.snapshot import finance_snapshot

    session, today, R = household
    H = hand_plan(R, today)
    B = hand_borrowing(R, today, H)
    snap = finance_snapshot(session, today)
    days = (H["paydays"][0] - today).days
    past_due = hand_past_due(R, today)
    allowed = {H["cash"], H["due_before"], *H["groups"].values(), H["window_total"], H["net"], days, len(H["advances_due"]), r2(H["advances_before"] + H["advances_on_payday"]),
               B["since_payday"]["n"], B["since_payday"]["amount"], B["fees_90d"], B["fees_month"], 90, H["pace"]["per_day"], H["goals"], H["safe"], abs(H["safe"]), r2(H["safe"] - H["goals"]),
               *(-w["low"] for w in H["weeks"] if w["low"] is not None), *(a for _, _, a in past_due), len(past_due), len(past_due) - 3, *(r2(usual_net(R, p.pay_date)) for p in R.stubs), *(r2(usual_net(R, p.pay_date) - p.net) for p in R.stubs)}
    allowed_int = {round(v) for v in allowed}
    for line in snap["brief"]:
        for n in numbers_in(line["text"]):
            assert n in allowed_int, (line["text"], n)
    # The "how is this worked out" sentences carry cents
    why_allowed = {*allowed, BUFFER, H["shortfall"], abs(H["safe"]), len(H["before"]), 0.0, *(x for w in H["windows"] for x in (w["paycheck"], abs(w["carried_in"]), w["bills"], w["payments"], w["advances"], w["set_aside"], abs(w["safe"])))}
    for text in snap["why"].values():
        for n in numbers_in(text):
            assert n in why_allowed, (text, n)


def hand_past_due(R: Rows, today: date) -> list[tuple[str, date, float]]:
    """Periods of the last sixty days with no payment seen, older than the grace: a monthly bill's month is paid by a row
    under its name anywhere in that month too."""
    used: set[int] = set()
    out = []
    for b in sorted(R.bills, key=lambda b: b.id):
        for due in monthly_dates(b.due_day, today - timedelta(days=60), today - timedelta(days=1)):
            t = match(R, b, due, today, used)
            if t is None:
                tol = max(2.0, b.amount * (0.4 if b.amount_mode == "estimate" else 0.15))
                t = next((t for t in R.tx if t.id not in used and t.amount > 0 and t.date <= today and (t.date.year, t.date.month) == (due.year, due.month) and abs(t.amount - b.amount) <= tol
                          and (key(t.merchant).startswith(key(b.name)) or key(b.name).startswith(key(t.merchant)))), None)
            if t is not None:
                used.add(t.id)
            elif (today - due).days > max(3, b.grace_days):
                out.append((b.name, due, r2(b.amount)))
    return sorted(out, key=lambda x: x[1])


@dated
def test_the_borrowing_page(household):
    from app.engine.borrowing import borrowing_overview, borrowing_summary
    from app.engine.borrowing_plan import borrowing_plan
    from app.engine.payoff import advance_costs

    session, today, R = household
    H = hand_plan(R, today)
    B = hand_borrowing(R, today, H)
    page = borrowing_overview(session, today)
    assert (page["cost"]["fees_90d"], page["cost"]["fees_month"], page["cost"]["advances_90d"], page["cost"]["advanced_90d"]) == (B["fees_90d"], B["fees_month"], B["advances_90d"], B["advanced_90d"])
    assert (page["outstanding_total"], len(page["outstanding"])) == (B["outstanding_total"], B["outstanding"])
    for row in page["providers"]:
        h = B["providers"][row["provider"]]
        assert (row["advances"], row["advanced"], row["fees"], row["outstanding"], row["fee_share"], row["annual_rate"]) == (h["n"], r2(h["advanced"]), r2(h["fees"]), r2(h["outstanding"]), B["costs"][row["provider"]]["fee_share"], B["costs"][row["provider"]]["annual_rate"])
    for row in advance_costs(session, today):
        c = B["costs"][row["provider"]]
        assert (row["fee_per_100"], row["days_held"], row["as_yearly_rate"]) == (c["fee_per_100"], c["days_held"], c["as_yearly_rate"])
    summary = borrowing_summary(session, today)
    assert (summary["fees_90d"], summary["outstanding"], summary["outstanding_total"], summary["since_payday"]["advances"], summary["since_payday"]["advanced"]) == (B["fees_90d"], B["outstanding"], B["outstanding_total"], B["since_payday"]["n"], B["since_payday"]["amount"])
    # The step that says the plan holds the advances back is true only while it does
    holds = [s for s in page["steps"] if "already holds it back" in s["text"]]
    assert bool(holds) == bool(H["advances_due"])
    plan = borrowing_plan(session, today)
    assert [(s["month"], s["total"]) for s in plan["stairs"]] == B["stairs"]
    assert {f["name"]: (f["payments_left"], f["ends"], f["left_to_pay"]) for f in plan["finish_line"]} == B["finish"]
    assert (plan["freed"] or None) == (B["freed"] and {**B["freed"], "peak_month": plan["freed"]["peak_month"]})
    assert plan["totals"]["left_to_pay"] == B["left_to_pay"] and plan["room"]["least_after_a_payday"] == B["least"] and plan["extra_per_month"] == B["extra"]
    stairs_allowed = {*(t for _, t in B["stairs"]), B["left_to_pay"], len(R.plans), len(B["finish"]), len(R.plans) - len(B["finish"]), *(x for f in B["finish"].values() for x in f), *((B["freed"] or {}).values() if B["freed"] else ()),
                      H["pace"]["per_day"], B["least"], B["extra"], H["safe"], abs(H["safe"])}
    for step in plan["steps"]:
        for n in numbers_in(step["text"]):
            assert n in stairs_allowed, (step["text"], n)


@dated
def test_the_debts_page(household):
    from app.engine.payoff import payoff_plan
    from app.engine.reports import debt_mountain

    session, today, R = household
    D = hand_debts(R, today)
    m = debt_mountain(session, today)
    assert m["total"] == D["total"] and m["minimums_month"] == D["minimums_month"] and m["interest_month"] == D["interest_month"]
    assert sorted((i["name"], i["balance"], i["share"], i["minimum"]) for i in m["items"]) == sorted((i["name"], i["balance"], i["share"], i["minimum"]) for i in D["items"])
    p = payoff_plan(session, today)
    assert p["minimums_total"] == r2(sum(i["minimum"] or 0 for i in D["items"] if i["kind"] != "advance"))
    for order, got in (("minimums", p["minimums_only"]), ("avalanche", p["avalanche"]), ("snowball", p["snowball"])):
        want = D["ways"][order]
        assert (got["months"], got["interest"], got["debt_free"]) == (want["months"], want["interest"], want["debt_free"]), order
        assert {d["name"]: (d["paid_on"], d["interest"]) for d in got["debts"]} == want["debts"], order


@dated
def test_budgets_and_the_spending_report(household):
    """With a refund, a split row and two budgets added to the fixture, so the netting and the splits are exercised."""
    from app.engine.budgets import budget_plan
    from app.engine.reports import spending_summary

    session, today, R = household
    checking = next(a for a in R.accounts if a.kind == "checking")
    grocery = next(t for t in sorted(R.tx, key=lambda t: t.date, reverse=True) if t.category == "Groceries" and t.date <= date(2026, 10, 9))
    session.add(Transaction(account_id=checking.id, date=date(2026, 10, 12), merchant="Example Grocery refund", amount=-25.0, category="Groceries", row_hash="sweep10-refund"))
    session.add(TransactionSplit(transaction_id=grocery.id, category="Groceries", amount=r2(grocery.amount - 10.0)))
    session.add(TransactionSplit(transaction_id=grocery.id, category="Household", amount=10.0))
    session.add(CategoryBudget(category="Groceries", per_paycheck=300.0, rollover="next"))
    session.add(CategoryBudget(category="Dining & Drinks", per_paycheck=80.0, rollover="none"))
    session.commit()
    bump_version()
    R = Rows(session)
    starts = [add_months(today.replace(day=1), -k) for k in (2, 1, 0)]
    months = {s.strftime("%Y-%m"): {"spent": 0.0, "income": 0.0} for s in starts}
    groups: dict[str, float] = {}
    for t in R.tx:
        if not (starts[0] <= t.date <= today):
            continue
        m = months[t.date.strftime("%Y-%m")]
        if t.is_income:
            m["income"] += -t.amount
        elif R.spending(t):
            m["spent"] += t.amount
            for c, a in R.parts(t):
                groups[c] = groups.get(c, 0.0) + a
    report = spending_summary(session, today, months=3)
    assert [(x["month"], x["spent"], x["income"]) for x in report["months"]] == [(k, r2(v["spent"]), r2(v["income"])) for k, v in months.items()]
    assert {g["name"]: g["total"] for g in report["groups"]} == {c: r2(v) for c, v in groups.items()}
    income, spent = sum(v["income"] for v in months.values()), sum(v["spent"] for v in months.values())
    assert (report["totals"]["spent"], report["totals"]["income"], report["totals"]["kept"], report["totals"]["savings_rate"]) == (r2(spent), r2(income), r2(income - spent), round((income - spent) / income, 4))
    # The budgets: this paycheck's days, what each category spent in them, what carried in from the days before
    paydays, _ = hand_paydays(R, today)
    start = paydays[0] - timedelta(days=14)
    start = min(start, today)
    days_total, days_gone = 14, min(14, (today - start).days + 1)

    def spent_in(a: date, b: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in R.tx:
            if a <= t.date <= b and R.spending(t):
                for c, amount in R.parts(t):
                    out[c] = out.get(c, 0.0) + amount
        return out

    now, before = spent_in(start, today), spent_in(start - timedelta(days=14), start - timedelta(days=1))
    plan = budget_plan(session, today)
    assert (plan["span"]["start"], plan["span"]["days_gone"]) == (start.isoformat(), days_gone)
    for item, (cat, per, roll) in zip(plan["items"], (("Dining & Drinks", 80.0, "none"), ("Groceries", 300.0, "next")), strict=True):
        carried = max(0.0, per - before.get(cat, 0.0)) if roll == "next" else 0.0
        assert (item["category"], item["carried"], item["allowed"], item["spent"], item["left"], item["expected_by_now"]) == (cat, r2(carried), r2(per + carried), r2(now.get(cat, 0.0)), r2(per + carried - now.get(cat, 0.0)), r2((per + carried) * days_gone / days_total))
    assert plan["unbudgeted"]["spent"] == r2(sum(v for c, v in now.items() if c not in ("Groceries", "Dining & Drinks")))


@dated
def test_paystubs_add_up_and_the_plan_expects_the_usual_net(household):
    from app.engine.paystubs import paystubs_overview
    from app.engine.snapshot import finance_snapshot

    session, today, R = household
    overview = paystubs_overview(session)
    by_date = {row["pay_date"]: row for row in overview["recent"]}
    for p in R.stubs:
        lines = json.loads(p.deductions)
        taxes = sum(d["amount"] for d in lines if TAX_WORDS.search(d["name"]))
        benefits = sum(d["amount"] for d in lines if not TAX_WORDS.search(d["name"]))
        assert r2(p.gross - taxes - benefits) == p.net
        row = by_date[p.pay_date.isoformat()]
        assert (row["gross_total"], row["taxes_total"], row["benefits_total"]) == (p.gross, r2(taxes), r2(benefits))
        usual = usual_net(R, p.pay_date)
        assert (row["expected_net"], row["difference"]) == (usual, r2(p.net - usual))
    paydays, _ = hand_paydays(R, today)
    assert finance_snapshot(session, today)["next_paycheck"]["amount"] == usual_net(R, paydays[0]) == R.incomes[0].expected_net


@dated
def test_every_how_is_this_worked_out_list_adds_up(household):
    from app.engine.cover import account_cover
    from app.engine.decisions import billing_date_suggestions, date_move_check, purchase_check
    from app.engine.snapshot import finance_snapshot

    session, today, R = household
    H = hand_plan(R, today)
    snap = finance_snapshot(session, today)
    steps = snap["safe_to_spend"]["steps"]
    assert r2(sum(s["amount"] for s in steps)) == snap["safe_to_spend"]["amount"] == H["safe"]
    assert steps[0]["amount"] == H["cash"] and {s["label"][:9]: s["amount"] for s in steps[1:]}.get("Comes out", 0.0) == -H["due_before"]
    assert any(s["amount"] == -BUFFER and "buffer" in s["label"] for s in steps)
    # Cover: each window's figures from its own parts, and from the plan's
    cover = account_cover(session, today)
    checking = next(a for a in cover["accounts"] if a["role"] == "spend")
    w1, w2 = checking["windows"]
    pace = H["pace"]["by_account"][checking["id"]]
    p0 = H["paydays"][0]
    for w, days, bills, inflow in ((w1, max(0, (p0 - timedelta(days=1) - today).days), H["due_before"], 0.0), (w2, 14, r2(H["windows"][0]["bills"] + H["windows"][0]["advances"]), H["net"])):
        assert (w["bills"], w["everyday"], w["buffer"], w["inflow"]) == (bills, r2(pace * days), BUFFER, inflow)
        assert w["needs"] == r2(w["bills"] + w["everyday"] + w["buffer"] + w["set_aside"]) and w["has_total"] == r2(w["has"] + w["inflow"]) and w["left"] == r2(w["has_total"] - w["needs"])
    assert w1["has"] == H["cash"] and w2["has"] == r2(w1["left"] + BUFFER)
    # The purchase check at $250: the whole line drops by the price from today
    check = purchase_check(session, today, 250.0)
    low = H["low"]
    assert (check["low_point_before"]["date"], check["low_point_before"]["value"]) == (low["date"].isoformat(), low["at_pace"])
    assert check["low_point"] == {"date": low["date"].isoformat(), "at_pace": r2(low["at_pace"] - 250)}
    hit = next((p for p in H["line"] if p["at_pace"] - 250 < min(0.0, p["at_pace"]) - 0.005), None)
    if hit is None:
        assert check["verdict"] == "yes" and check["breaks"] is None
    else:
        assert check["breaks"]["date"] == hit["date"].isoformat() and check["breaks"]["shortfall"] == r2(250 - hit["at_pace"]) and check["breaks"]["already_short"] == r2(max(0.0, -hit["at_pace"]))
        first_ok = next((p["date"] for p in H["line"] if p["date"] > today and not any(q["at_pace"] - 250 < min(0.0, q["at_pace"]) - 0.005 for q in H["line"] if q["date"] >= p["date"])), None)
        assert (check["verdict"], check["first_ok_date"]) == (("wait", first_ok.isoformat()) if first_ok else ("no", None))
    assert check["fits_safe_to_spend"] == (250.0 <= H["safe"])
    # Billing-date moves: the sixty-day low judged over whole pay periods, before and after
    judge_until = [d for d in H["paydays"] if today < d < today + timedelta(days=60)][-1]

    def judged_low(moved: dict[int, int]) -> tuple[str, float]:
        line = hand_plan(R, today, moved=moved, judge_until=judge_until)["line"]
        p = min((p for p in line if p["date"] < judge_until), key=lambda p: p["at_pace"])
        return p["date"].isoformat(), p["at_pace"]

    base = judged_low({})
    for name, day in (("Phone", 5), ("Rent", 28), ("Car loan", 2)):
        bill = next(b for b in R.bills if b.name == name)
        move = date_move_check(session, today, bill.id, day)
        after = judged_low({bill.id: day})
        assert (move["low_before_date"], move["low_before"]) == base and (move["low_after_date"], move["low_after"]) == after, name
        assert move["change"] == r2(after[1] - base[1]) and move["verdict"] == ("better" if move["change"] >= 1 else "worse" if move["change"] <= -1 else "same")
    offered = billing_date_suggestions(session, today)
    assert (offered["baseline"]["low_date"], offered["baseline"]["low"]) == base
    for s in offered["suggestions"]:
        assert s["improvement"] == r2(s["low_after"] - s["low_before"]) and (s["low_before"], s["low_after"]) == (base[1], judged_low({s["bill_id"]: s["to_day"]})[1])
    current, chosen = base[1], {}
    for step in offered["plan"]:
        chosen[step["bill_id"]] = step["to_day"]
        assert step["improvement"] == r2(step["low_after"] - current) and step["low_after"] == judged_low(chosen)[1]
        current = step["low_after"]
    assert offered["plan_low"] == current


@dated
def test_needs_you_next_steps_and_the_recap(household):
    from app.engine.needsyou import triage
    from app.engine.next_steps import next_steps
    from app.engine.recap import recap, record_plan, week_recap

    session, today, R = household
    H = hand_plan(R, today)
    B = hand_borrowing(R, today, H)
    past = hand_past_due(R, today)
    needs = triage(session, today)
    assert [(p["name"], p["due_date"], p["amount"]) for p in needs["past_due"]] == [(n, d.isoformat(), a) for n, d, a in past]
    assert needs["total"] == len(past) and all(n in {len(past)} for n in numbers_in(needs["word"])), needs["word"]
    borrowing_step = next(s for s in next_steps(session, today)["steps"] if s["id"] == "borrowing")
    assert all(n in {round(B["fees_90d"]), B["outstanding"], 90} for n in numbers_in(borrowing_step["text"])), borrowing_step["text"]
    # The week: what went out in the last seven days, against the usual week
    rows = [t for t in R.tx if today - timedelta(days=7) < t.date <= today and R.spending(t) and t.amount > 0]
    by_cat: dict[str, float] = {}
    for t in rows:
        by_cat[t.category] = by_cat.get(t.category, 0.0) + t.amount
    top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:3]
    week = week_recap(session, today)
    assert (week["spent"], week["usual_week"], [(t["category"], t["spent"]) for t in week["top"]]) == (r2(sum(t.amount for t in rows)), r2(H["pace"]["per_day"] * 7), [(c, r2(v)) for c, v in top])
    assert all(n in {round(week["spent"]), round(week["usual_week"]), *(round(v) for _, v in top), round(H["safe"])} for n in numbers_in(week["text"])), week["text"]
    # The plan kept for the coming check is the first window; what happened is read from the rows after it
    kept = record_plan(session, today)
    w = H["windows"][0]
    assert (kept["paycheck"], kept["bills"], kept["advances"], kept["set_aside"], kept["free"], kept["pace_per_day"]) == (w["paycheck"], w["bills"], w["advances"], w["set_aside"], w["safe"], H["pace"]["per_day"])
    later = w["start"] + timedelta(days=1)
    happened = recap(session, later)["windows"][0]
    came_in = r2(sum(-t.amount for t in R.tx if t.is_income and w["start"] <= t.date <= later))
    everyday = r2(sum(t.amount for t in R.tx if R.spending(t) and t.amount > 0 and w["start"] <= t.date <= later))
    assert (happened["came_in"], happened["everyday"], happened["everyday_against_free"]) == (came_in, everyday, r2(everyday - w["safe"]))


# ---------------------------------------------------------------------------------------------------------------
# What the recomputation found, each pinned by its smallest case.


def test_the_fourth_window_sets_aside_the_whole_fifth_window():
    """Seen from Oct 31 the Dec 25 check set aside $10 for the window after it; seen from Nov 13 the same check set
    aside $220, and the rows say $220 (Music (two) $10, Streaming $17, Tolls $40, Music $11, Electricity $142 between
    Jan 8 and Jan 21). The events stopped at a fixed seventy-five days, short of the fifth window. A monthly paycheck
    was worse off: its third and fourth windows held no bills, so "safe" there was the whole check."""
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        seed(session)
        for today in (date(2026, 10, 31), date(2026, 11, 13)):
            match_payments(session, today)
            w = finance_snapshot(session, today)["windows"][3]
            h = hand_plan(Rows(session), today)["windows"][3]
            assert w["start"] == "2026-12-25" and (w["set_aside"]["total"], w["safe"]) == (h["set_aside"], h["safe"]) == (220.0, 1372.5)
        income = session.exec(select(Income)).one()
        income.cadence, income.next_date, income.pay_day, income.expected_net = "monthly", date(2026, 10, 30), 30, 4480.0
        session.add(income)
        session.commit()
        bump_version()
        windows = finance_snapshot(session, date(2026, 10, 14))["windows"]
        assert [w["start"] for w in windows] == ["2026-10-30", "2026-11-30", "2026-12-30", "2027-01-30"]
        assert all(w["bills"]["total"] > 1500 for w in windows), [w["bills"]["total"] for w in windows]  # every window carries its month of bills
        assert all(w["safe"] < 4480.0 for w in windows)


def test_an_installment_due_yesterday_with_nothing_posted_is_still_in_the_account():
    """On Oct 22 the couch plan's last installment, due Oct 21, had not posted; the plan dropped it, so safe to spend
    was $62 higher than the account could bear, where a bill due the day before with no payment seen leaves today."""
    from app.engine.snapshot import finance_snapshot

    with Session(engine) as session:
        seed(session)
        today = date(2026, 10, 22)
        match_payments(session, today)
        snap = finance_snapshot(session, today)
        H = hand_plan(Rows(session), today)
        late = [e for e in snap["due_before_payday"]["items"] if e["name"] == "Couch plan 4 of 4"]
        assert late and late[0]["date"] == today.isoformat() and late[0]["late"] and late[0]["due"] == "2026-10-21"
        assert snap["safe_to_spend"]["amount"] == H["safe"] and snap["due_before_payday"]["total"] == H["due_before"]
        assert any(e["name"] == "Couch plan 4 of 4" and e.get("late") for e in H["before"])
        # Five days on it is the register's business (the plan's progress job), as a bill past its grace is Needs you's
        later = date(2026, 10, 26)
        assert not any(e["name"].startswith("Couch") for e in finance_snapshot(session, later)["due_before_payday"]["items"])


def test_a_credit_card_payment_bill_is_drawn_as_the_card_payment():
    """The $45 "Card minimum" bill pays the Rewards Card (the plan reserves no second payment for that card because
    of it); the past checks drew the same payment in the card band, the coming ones in the bills band."""
    from app.engine.paychecks import paycheck_views

    with Session(engine) as session:
        seed(session)
        match_payments(session, DATES[0])
        views = paycheck_views(session, DATES[0])
        H = hand_plan(Rows(session), DATES[0])
        for w, h in zip(views["windows"], H["windows"], strict=True):
            parts = {p["key"]: p["amount"] for p in w["parts"]}
            assert parts["card"] == h["card"], w["start"]
            assert r2(parts["housing"] + parts["bills"] + parts["card"] + parts["later"]) == r2(h["bills"] + h["advances"])
        assert {p["key"]: p["amount"] for p in views["windows"][0]["parts"]}["card"] == 70.0
        # The past checks already drew the same $45 payment (the ledger's "Payment to Rewards Card") in the card band
        assert any(next(p for p in h["parts"] if p["key"] == "card")["amount"] == 45.0 for h in views["history"])


def test_an_advance_past_its_repay_day_is_owed_nowhere():
    """On Oct 31 the two advances repaid on Oct 16 were out of the plan (taken back on the day, as the plan counts
    it) yet Debts said $231 owed, Borrowing said the plan already held it back, the watchlist and Next steps said two
    were still out: the register's rows wait for sync_borrowing, which retires them three weeks after they were taken."""
    from app.engine.advance_exit import exit_plan
    from app.engine.borrowing import borrowing_overview, borrowing_summary
    from app.engine.next_steps import next_steps
    from app.engine.reports import owed
    from app.engine.snapshot import finance_snapshot
    from app.engine.watchlist import watchlist

    with Session(engine) as session:
        seed(session)
        today = date(2026, 10, 31)
        match_payments(session, today)
        assert finance_snapshot(session, today)["advances"]["total"] == 0.0
        assert borrowing_summary(session, today)["outstanding"] == 0 and borrowing_overview(session, today)["outstanding_total"] == 0.0
        assert not any("holds it back" in s["text"] for s in borrowing_overview(session, today)["steps"])
        assert not any(i["kind"] == "advance" for i in owed(session, today)["items"])
        assert not any(i["kind"] == "advances" for i in watchlist(session, today)["items"])
        assert "still out" not in next((s["text"] for s in next_steps(session, today)["steps"] if s["id"] == "borrowing"), "")
        assert exit_plan(session, today)["state"] != "none"  # the ninety days' habit is still the way off
        # Up to the repay day they are owed everywhere
        before = date(2026, 10, 16)
        assert sum(1 for i in owed(session, before)["items"] if i["kind"] == "advance") == 2 == borrowing_summary(session, before)["outstanding"] == len(finance_snapshot(session, before)["advances"]["items"])
