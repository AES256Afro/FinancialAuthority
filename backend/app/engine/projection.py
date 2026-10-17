"""The pure pieces of the projection, shared by the snapshot, the purchase check and the date optimiser:
events from the registers, the paycheck windows, the set-aside rule, and the daily path for sixty days."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from ..models import Account, Advance, Bill, InstallmentPlan
from .billdates import bill_dates
from .cadence import _add_months, next_paydays, payday_for, payday_position

SPENDABLE_KINDS = {"checking", "cash"}
CASH_KINDS = {"checking", "savings", "cash"}


def is_spendable(a: Account) -> bool:
    """Checking and cash are for spending; savings is kept unless the owner says otherwise on the account."""
    return bool(a.spendable) if a.spendable is not None else a.kind in SPENDABLE_KINDS


def cash_of(accounts: list[Account]) -> tuple[float, float]:
    """(cash to spend, all cash including savings)."""
    spend = sum((a.balance or 0.0) for a in accounts if is_spendable(a))
    every = sum((a.balance or 0.0) for a in accounts if a.kind in CASH_KINDS)
    return spend, every

# What the daily path's figures are, in plain words, for any screen or model that names them. `at_pace` is not a
# balance the bank will show: it is what is left to spend after the safety cushion, the money kept for bills and
# the usual everyday spending.
PATH_WORDS = {
    "at_pace": "left to spend: what checking would hold after the safety cushion, the money kept for bills and your usual everyday spending",
    "at_pace_short": "left to spend",
    "available": "left after the safety cushion and the money kept for bills, if nothing else is spent",
    "cash": "in checking",
}

LARGE_BILL = 500.0  # a bill at or over this is set aside by halves across two paychecks
WINDOW_DAYS = 14
HORIZON_DAYS = 75


def _money(v: float) -> float:
    return round(float(v), 2)


def next_due(due_day: int, today: date) -> date:
    """The next date a bill due on `due_day` falls; 29 to 31 mean the last such day the month has."""
    candidate = date(today.year, today.month, min(due_day, calendar.monthrange(today.year, today.month)[1]))
    if candidate < today:
        month = today.month % 12 + 1
        year = today.year + (1 if month == 1 else 0)
        candidate = date(year, month, min(due_day, calendar.monthrange(year, month)[1]))
    return candidate


def window_date(due: date, paid_from: str, pay_days: str = "") -> date:
    """The payday whose window a bill belongs to: its due date, or the mid or end-of-month check the owner
    pays it from (on the paycheck's own semi-monthly days). Cash still leaves on the due date; this only decides
    which paycheck carries it."""
    return payday_for(due, paid_from, pay_days) if paid_from in ("mid", "end") else due


def line_of(category: str, kind: str) -> str:
    """Which line a bill rides on the transit map: housing, utilities or debt."""
    if kind in ("installment", "advance"):
        return "debt"
    c = (category or "").lower()
    if c in {"rent", "home", "housing", "insurance", "mortgage", "hoa"} or any(w in c for w in ("rent", "insurance", "mortgage")):
        return "housing"
    if c in {"loan payment", "credit card payment", "loans", "debt"} or "loan" in c or "credit card" in c:
        return "debt"
    return "utilities"


def _moved_dates(b: Bill, today: date, horizon: date, new_day: int, judge_until: date | None = None) -> list[date]:
    """A bill's dates with its due day moved the way the request to the biller asks: from the next cycle. Its next
    charge stays on the old day (this cycle's bill is owed already); each later charge moves to the nearest date on
    the new day, so the move itself never drops or adds a charge. With `judge_until` (the last payday the plan
    judges by), a charge that was due before that day and would move past it is kept on the day before it: what
    lies past the plan's view is not seen, so pushing a charge out of view never counts as saving it."""
    reach = horizon + timedelta(days=62)
    old = bill_dates(b, today, reach)
    if not old:
        return []
    new = bill_dates(b, today - timedelta(days=31), reach + timedelta(days=31), due_day=new_day)
    out = [old[0]]
    for d in old[1:]:
        if not new:
            break
        moved = min(new, key=lambda c: (abs((c - d).days), -c.toordinal()))  # the nearest; a tie takes the later
        if judge_until is not None and d < judge_until <= moved:
            moved = judge_until - timedelta(days=1)
        out.append(moved)
    return [d for d in out if today <= d <= horizon]


def build_events(bills: list[Bill], plans: list[InstallmentPlan], advances: list[Advance], today: date, *, due_days: dict[int, int] | None = None, accounts: dict[int, Account] | None = None, horizon_days: int = HORIZON_DAYS,
                 paid: set[tuple[int, date]] | None = None, pay_days: str = "", late: set[tuple[int, date]] | None = None, judge_until: date | None = None, checks_by_day: bool = True) -> list[dict]:
    """Every dated obligation for the horizon. `due_days` overrides a bill's due day (the optimiser's what-if),
    from the bill's next cycle on (see _moved_dates). A bill charged to a card (its paying account is a card) is
    marked so: it does not leave checking on its due date; it rides the card payment it is paid from. A due date
    whose payment is already matched in the ledger (`paid`, as (bill id, due date)) is left out: that money has
    left the balance, or the card holds it. A due date that has passed with no payment posted yet (`late`, from
    payments.unposted_periods) is still in the account: it is counted today, marked late, with its real due date.
    `checks_by_day` is false when no paycheck comes twice a month: a bill set to the mid or end-of-month check
    then belongs to the check before it is due, since no such check exists."""
    events: list[dict] = []
    horizon = today + timedelta(days=horizon_days)
    late_by_bill: dict[int, list[date]] = {}
    for bill_id, d in late or ():
        if d < today:
            late_by_bill.setdefault(bill_id, []).append(d)
    for b in bills:
        due_day = (due_days or {}).get(b.id, b.due_day)
        paid_from = getattr(b, "paid_from", "due") or "due"
        if not checks_by_day and paid_from in ("mid", "end"):
            paid_from = "due"
        payer = (accounts or {}).get(b.pay_from_account_id) if b.pay_from_account_id else None
        from_kind = "card" if payer is not None and payer.kind == "card" else "cash"
        base = {"name": b.name, "amount": _money(b.amount), "kind": b.kind, "autopay": b.autopay, "source": f"bill:{b.id}", "line": line_of(b.category, b.kind), "bill_id": b.id, "paid_from": paid_from, "from_kind": from_kind,
                "from_account_id": b.pay_from_account_id, "category": b.category or "", "movable": bool(b.movable), "due_day": due_day}
        for d in sorted(late_by_bill.get(b.id, [])):
            events.append({**base, "date": today, "window_date": today, "due_day": b.due_day, "late": True, "due": d.isoformat(), "note": f"due {d:%a %b} {d.day}, not out of the account yet"})
        moved = due_days is not None and b.id in due_days and int(due_day) != int(b.due_day)
        dates = _moved_dates(b, today, horizon, int(due_day), judge_until) if moved else bill_dates(b, today, horizon, due_day=due_day)
        for d in dates:
            if paid and (b.id, d) in paid:
                continue
            events.append({**base, "date": d, "window_date": window_date(d, paid_from, pay_days)})
    for p in plans:
        payer = (accounts or {}).get(getattr(p, "pay_from_account_id", None)) if getattr(p, "pay_from_account_id", None) else None
        plan_kind = "card" if payer is not None and payer.kind == "card" else "cash"
        schedule = _schedule_of(p)
        if schedule:
            upcoming = [s for s in schedule if date.fromisoformat(s["date"]) >= today][-max(0, p.remaining):] if p.remaining else []
            for i, s in enumerate(upcoming):
                when = date.fromisoformat(s["date"])
                if when > today + timedelta(days=max(90, horizon_days)):
                    break
                events.append({"date": when, "window_date": when, "name": f"{p.merchant or p.provider} {p.count - len(upcoming) + i + 1} of {p.count}", "amount": _money(s["amount"]), "kind": "installment", "autopay": True, "source": f"plan:{p.id}", "line": "debt", "interest": s.get("interest", 0.0), "from_kind": plan_kind, "from_account_id": getattr(p, "pay_from_account_id", None)})
            continue
        d, left = p.next_date, p.remaining
        while left > 0 and d <= today + timedelta(days=max(90, horizon_days)):
            events.append({"date": d, "window_date": d, "name": f"{p.merchant} {p.count - left + 1} of {p.count}", "amount": _money(p.installment), "kind": "installment", "autopay": True, "source": f"plan:{p.id}", "line": "debt", "from_kind": plan_kind, "from_account_id": getattr(p, "pay_from_account_id", None)})
            d, left = d + timedelta(days=p.cadence_days), left - 1
    for a in advances:
        events.append({"date": a.repay_date, "name": f"{a.provider} repays", "amount": _money(a.amount + a.fee), "kind": "advance", "autopay": True, "source": f"advance:{a.id}", "line": "debt"})
    events.sort(key=lambda e: e["date"])
    return events


def card_events(cards: list[Account], paydays: list[date], mode: str, today: date, debts: list | None = None, planned: dict[int, float] | None = None) -> list[dict]:
    """What the card takes from the coming checks. "auto" (the default) reserves the minimum on its due day
    when a debt row knows it, and otherwise the whole balance split across the next two checks; "split",
    "next", "mid" and "end" reserve the whole balance as the owner pays it; "minimum" only the minimum; "off"
    nothing. Spending on the card is already in the pace; this is the money already owed."""
    events: list[dict] = []
    if not paydays or mode == "off":
        return events
    for a in cards:
        balance = float(a.balance or 0.0) - float((planned or {}).get(a.id, 0.0))  # a payment already planned is not held back twice
        if balance <= 0:
            continue
        debt = next((d for d in (debts or []) if (getattr(d, "account_id", None) == a.id) or (d.name or "").strip().lower() == (a.name or "").strip().lower()), None)
        if mode == "minimum" or (mode == "auto" and debt is not None and (debt.minimum or 0) > 0):
            if debt is None or not (debt.minimum or 0):
                continue
            due = next_due(int(debt.due_day or paydays[0].day), today)
            events.append({"date": due, "window_date": due, "name": f"{a.name} minimum", "amount": _money(min(float(debt.minimum), balance)), "kind": "card_payment", "autopay": False, "source": f"debt:{debt.id}", "line": "debt", "paid_from": "due"})
            continue
        if mode in ("split", "auto") and len(paydays) > 1:
            shares = [(paydays[0], round(balance / 2, 2)), (paydays[1], round(balance - round(balance / 2, 2), 2))]
        elif mode in ("mid", "end"):
            when = next((d for d in paydays if payday_position(d) == mode), paydays[0])
            shares = [(when, balance)]
        else:
            shares = [(paydays[0], balance)]
        for when, amount in shares:
            events.append({"date": when, "window_date": when, "name": f"{a.name} payment", "amount": _money(amount), "kind": "card_payment", "autopay": False, "source": f"account:{a.id}", "line": "debt", "paid_from": mode})
    return events


def series_dates(cadence: str, first: date, start: date, end: date) -> list[date]:
    """The days a detected series charges from `start` through `end`, stepped from `first` (its next expected
    date) by its own cadence: weekly and every two weeks by days, twice a month on the 15th and the last day,
    monthly, quarterly and yearly by calendar months on the day of `first`."""
    if cadence in ("weekly", "biweekly"):
        step = timedelta(days=7 if cadence == "weekly" else 14)
        d = first
        while d < start:
            d += step
        out = []
        while d <= end:
            out.append(d)
            d += step
        return out
    if cadence == "semimonthly":
        return [d for d in next_paydays(first, "semimonthly", max(2, (end - first).days // 13 + 3)) if start <= d <= end]
    months = {"quarterly": 3, "semiannual": 6, "yearly": 12}.get(cadence, 1)
    out, k, d = [], 0, first
    while d <= end and k < 400:
        if d >= start:
            out.append(d)
        k += 1
        d = _add_months(first, k * months)
    return out


def pending_installment_events(series_rows: list, today: date, horizon_days: int = HORIZON_DAYS, plans: list | None = None) -> list[dict]:
    """A pay-later stream the ledger found and the owner has not named yet still charges on its day: it is
    counted at its last amount, marked pending, until the payments left are set and it becomes a plan. A stream
    that is a plan's own payments (the plan's merchant, or the same lender at the same installment) is that
    plan, which is counted already."""
    from .borrowing import plan_covers_series

    events: list[dict] = []
    horizon = today + timedelta(days=horizon_days)
    for s in series_rows:
        if s.confirmed or not s.active or getattr(s, "suggested_kind", "") != "installment":
            continue
        if any(p.active and plan_covers_series(p, s) for p in plans or []):
            continue
        for d in series_dates(s.cadence, s.next_expected, today, horizon):
            events.append({"date": d, "window_date": d, "name": f"{s.merchant} (payments left not set)", "amount": _money(s.typical_amount), "kind": "installment", "autopay": True, "source": f"series:{s.id}", "line": "debt", "paid_from": "due", "pending": True})
    return events


def cards_paid_by_bills(bills: list[Bill], cards: list[Account], debts: list | None) -> dict[int, set[int]]:
    """The cards whose payment a bill already holds, each with those bills: an active bill filed under Credit
    Card Payment pays the card it names (the card's or its debt's name is in the bill's name), else the card
    whose minimum it is (on the card's due day, when two cards share that minimum). The card's own payment is
    not reserved again on top of that bill."""
    out: dict[int, set[int]] = {}
    pairs = [(a, _debt_of(a, debts)) for a in cards]
    for b in bills:
        if not b.active or (b.category or "").strip().lower() != "credit card payment":
            continue
        text = (b.name or "").lower()
        named = [a for a, d in pairs if any(len(n.strip()) >= 3 and n.strip().lower() in text for n in (a.name or "", d.name if d is not None else ""))]
        minimum = [(a, d) for a, d in pairs if d is not None and (d.minimum or 0) > 0 and abs(float(b.amount) - float(d.minimum)) < 1.0]
        if len(minimum) > 1:
            minimum = [(a, d) for a, d in minimum if d.due_day and int(d.due_day) == int(b.due_day)] or minimum[:1]
        card = named[0] if named else (minimum[0][0] if minimum else None)
        if card is not None:
            out.setdefault(card.id, set()).add(b.id)
    return out


def _debt_of(a: Account, debts: list | None):  # noqa: ANN202 - a Debt or None
    """The debt row of a card: by its account, else by the same name."""
    return next((d for d in (debts or []) if (getattr(d, "account_id", None) == a.id) or (d.name or "").strip().lower() == (a.name or "").strip().lower()), None)


def card_payoffs(cards: list[Account], paydays: list[date], mode: str, today: date, debts: list | None, planned: dict[int, float] | None, card_bills: list[dict], card_pace_by_account: dict[int, float] | None, horizon_days: int = HORIZON_DAYS) -> list[dict]:
    """The card payments across the horizon, as the owner pays the card. Each carries what the card holds by
    then: its share of today's balance, the bills charged to it since the last payment, and the usual card
    spend over those days. A planned payment already counted nets off the balance. A card the owner carries
    (a minimum on file, or the minimum mode) reserves its minimum on its due day instead."""
    events: list[dict] = []
    if not paydays or mode == "off":
        return events
    horizon = today + timedelta(days=horizon_days)
    for a in cards:
        balance = max(0.0, float(a.balance or 0.0) - float((planned or {}).get(a.id, 0.0)))
        debt = next((d for d in (debts or []) if (getattr(d, "account_id", None) == a.id) or (d.name or "").strip().lower() == (a.name or "").strip().lower()), None)
        if mode == "minimum" or (mode == "auto" and debt is not None and (debt.minimum or 0) > 0):
            if debt is None or not (debt.minimum or 0) or balance <= 0:
                continue
            # A carried card owes its minimum every month, on its due day, for as long as the horizon runs.
            due = next_due(int(debt.due_day or paydays[0].day), today)
            while due <= horizon:
                events.append({"date": due, "window_date": due, "name": f"{a.name} minimum", "amount": _money(min(float(debt.minimum), balance)), "kind": "card_payment", "autopay": False, "source": f"debt:{debt.id}", "line": "debt", "paid_from": "due", "from_kind": "cash", "parts": {"balance": _money(min(float(debt.minimum), balance)), "bills": 0.0, "spend": 0.0}})
                due = next_due(int(debt.due_day or paydays[0].day), due + timedelta(days=1))
            continue
        dates = [d for d in paydays if today <= d <= horizon and (mode in ("split", "next", "auto") or payday_position(d) == mode)]
        if not dates:
            continue
        mine = [e for e in card_bills if e.get("from_account_id") == a.id]
        pace = float((card_pace_by_account or {}).get(a.id, 0.0))
        if balance <= 0 and not mine and pace <= 0:
            continue  # an idle card: nothing rides it, nothing to pay
        shares = [round(balance / 2, 2), round(balance - round(balance / 2, 2), 2)] if mode in ("split", "auto") and len(dates) > 1 else [balance]
        prev = today
        for i, d in enumerate(dates):
            bills_part = sum(e["amount"] for e in mine if prev <= e["date"] < d)
            spend_part = pace * (d - prev).days
            balance_part = shares[i] if i < len(shares) else 0.0
            amount = balance_part + bills_part + spend_part
            if amount > 0.5:
                events.append({"date": d, "window_date": d, "name": f"{a.name} payment", "amount": _money(amount), "kind": "card_payment", "autopay": False, "source": f"account:{a.id}", "line": "debt", "paid_from": mode, "from_kind": "cash",
                               "parts": {"balance": _money(balance_part), "bills": _money(bills_part), "spend": _money(spend_part)}})
            prev = d
    return events


def assemble(*, bills: list[Bill], plans: list[InstallmentPlan], advances: list[Advance], today: date, accounts: dict[int, Account], paydays: list[date], card_mode: str, debts: list | None, planned: dict[int, float] | None, extra: list[dict], card_pace_by_account: dict[int, float] | None, due_days: dict[int, int] | None = None, horizon_days: int = HORIZON_DAYS,
             paid: set[tuple[int, date]] | None = None, pay_days: str = "", late: set[tuple[int, date]] | None = None, judge_until: date | None = None, checks_by_day: bool = True) -> dict:
    """Everything the windows and the daily path count, with the card done right. `math` is what leaves
    checking: cash bills, plans, advances, planned payments and the card payments. `display` adds the bills
    that ride the card, marked via_card, so the picture still names them. A card whose payment is kept as a
    bill is paid by that bill, once: `paid_by_bill` names those bills by card. `paid`, `pay_days`, `late`,
    `judge_until` and `checks_by_day` are as build_events takes them."""
    events = build_events(bills, plans, advances, today, due_days=due_days, accounts=accounts, horizon_days=horizon_days, paid=paid, pay_days=pay_days, late=late, judge_until=judge_until, checks_by_day=checks_by_day) + extra
    card_bills = [e for e in events if e.get("from_kind") == "card"]
    cash = [e for e in events if e.get("from_kind") != "card"]
    cards = [a for a in accounts.values() if a.kind == "card" and a.active]
    covered = cards_paid_by_bills(bills, cards, debts)
    payoffs = card_payoffs([a for a in cards if a.id not in covered], paydays, card_mode, today, debts, planned, card_bills, card_pace_by_account, horizon_days=horizon_days)
    for e in card_bills:
        e["via_card"] = True
    math = sorted(cash + payoffs, key=lambda e: e["date"])
    display = sorted(cash + payoffs + card_bills, key=lambda e: e["date"])
    return {"math": math, "display": display, "card_bills": card_bills, "payoffs": payoffs, "paid_by_bill": covered}


def _schedule_of(p: InstallmentPlan) -> list[dict]:
    import json

    try:
        rows = json.loads(getattr(p, "schedule", "") or "[]")
    except ValueError:
        return []
    return sorted((r for r in rows if r.get("date") and r.get("amount")), key=lambda r: r["date"])


def set_aside_of(items: list[dict]) -> float:
    """What the previous check sets aside for the next window's bills: half of each large one, the whole of each
    small one. A card payment is paid by the check it lands on, from the spending it carries, so it is not set
    aside twice."""
    items = [e for e in items if e.get("kind") != "card_payment"]
    return sum(e["amount"] for e in items if e["amount"] >= LARGE_BILL) / 2 + sum(e["amount"] for e in items if e["amount"] < LARGE_BILL)


def wdate(e: dict) -> date:
    return e.get("window_date") or e["date"]


def split_windows(events: list[dict], today: date, next_payday: date, following_payday: date, after_following: date | None = None) -> dict:
    """What is due before payday, in the window after it, in the window after that, and the advances due.
    A bill sits in the window of the paycheck that pays it (its window date), not merely where its due date
    falls, so rent due on the 1st and paid from the end-of-month check belongs to that check."""
    third = after_following or following_payday + timedelta(days=WINDOW_DAYS)
    before = [e for e in events if today <= e["date"] and wdate(e) < next_payday and e["kind"] != "advance"]
    window = [e for e in events if next_payday <= wdate(e) < following_payday and e["kind"] != "advance"]
    next_window = [e for e in events if following_payday <= wdate(e) < third and e["kind"] != "advance"]
    advances_due = [e for e in events if e["kind"] == "advance" and e["date"] <= next_payday]
    return {"before": before, "window": window, "next_window": next_window, "advances_due": advances_due, "set_aside": set_aside_of(next_window)}


def project(today: date, *, cash: float, buffer: float, expected_net: float, pay_dates: set[date], set_aside: float, events: list[dict], per_day: float, days: int = 60, pay_amounts: dict[date, float] | None = None, reserved_for: set | None = None) -> list[dict]:
    """A daily path: `available` is cash minus the buffer minus what is reserved, if nothing else is spent;
    `at_pace` also spends the usual everyday amount each day, which is the honest line. The next check sets
    aside what the window after it needs (`set_aside`); that reserve is spent only by the bills it was set
    aside for (`reserved_for`, their sources and dates) and whatever is left of it is free again at the check
    after. Anything else that lands, a deposit or a new bill, comes out of what is available."""
    ordered = sorted(pay_dates)
    following_payday = ordered[1] if len(ordered) > 1 else today + timedelta(days=28)
    released_on = ordered[2] if len(ordered) > 2 else following_payday + timedelta(days=14)
    by_day: dict[date, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    balance, reserved, spent = cash, 0.0, 0.0
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        if d == released_on:
            reserved = 0.0
        if d in pay_dates:
            paid = (pay_amounts or {}).get(d, expected_net)
            if paid:
                balance += paid
                if d == ordered[0] and set_aside:
                    reserved = set_aside
        for e in by_day.get(d, []):
            balance -= e["amount"]
            if reserved and following_payday <= d < released_on and (reserved_for is None or (e.get("source"), d) in reserved_for):
                reserved = max(0.0, reserved - e["amount"])
        if i > 0:
            spent += per_day
        available = balance - buffer - reserved
        out.append({"date": d.isoformat(), "available": _money(available), "at_pace": _money(available - spent), "cash": _money(balance)})
    return out


def low_point(projection: list[dict], key: str = "at_pace") -> dict:
    low = min(projection, key=lambda p: p[key])
    return {"date": low["date"], "value": low[key]}


def projection_caveats(*, has_income: bool, bills: int, unnamed: int, pending_installments: int = 0, unanchored: int = 0) -> list[str]:
    """What the projection does not know, said next to every figure it still produces. A payday that is not on
    file is never invented: the windows are anchored two weeks out so the arithmetic has a frame, and the
    caveat says so."""
    out: list[str] = []
    if not has_income:
        out.append("No paycheck is on file, so no income is counted and the windows are anchored two weeks out; name your paycheck on Needs you.")
    if bills == 0:
        out.append("No bills are on the register yet, so none are counted.")
    if unnamed:
        out.append(f"{unnamed} recurring charge{'s' if unnamed != 1 else ''} in the ledger {'are' if unnamed != 1 else 'is'} unnamed and not counted until named.")
    if pending_installments:
        out.append(f"{pending_installments} pay-later stream{'s' if pending_installments != 1 else ''} {'are' if pending_installments != 1 else 'is'} counted at {'their' if pending_installments != 1 else 'its'} last amount until the payments left are set.")
    if unanchored:
        out.append(f"{unanchored} bill{'s' if unanchored != 1 else ''} that come{'' if unanchored != 1 else 's'} every few months {'have' if unanchored != 1 else 'has'} no next due date, so {'they are' if unanchored != 1 else 'it is'} not counted on any day yet; add the date on Bills.")
    return out
