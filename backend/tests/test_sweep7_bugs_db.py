"""The seventh sweep, against the database: the snapshot's invariants on many random households (random paydays by
each cadence, bills on every due day, cards, advances, plans, a buffer, and a day anywhere in the year), and payment
matching run once, twice and on following days. Every household is made up, from a fixed seed."""

from __future__ import annotations

import calendar
import json
import math
import random
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, select

from app.db import engine, init_db
from app.engine import cadence
from app.engine.payments import match_payments
from app.engine.snapshot import _finance_snapshot
from app.models import Account, Advance, Bill, Debt, Income, InstallmentPlan, Payment, Transaction
from app.store import set_setting

DAYS = [date(2026, 10, 14), date(2026, 12, 31), date(2027, 1, 1), date(2027, 2, 28), date(2028, 2, 29), date(2028, 2, 28), date(2026, 3, 8), date(2026, 11, 1), date(2026, 1, 31), date(2026, 4, 30), date(2027, 3, 14), date(2027, 11, 7)]
CATEGORIES = ["Rent", "Bills & Utilities", "Insurance", "Entertainment", "Loan Payment", "Credit Card Payment", "Health & Fitness", "Auto & Transport", ""]
ADVANCE_PAST_DAYS = 5  # how far back an advance's repay date may fall (an advance the owner has not ticked off)


def _fresh() -> None:
    SQLModel.metadata.drop_all(engine)
    init_db()


def _today(rng: random.Random) -> date:
    return rng.choice(DAYS) if rng.random() < 0.5 else date(2026, 1, 1) + timedelta(days=rng.randint(0, 730))


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


_counter = [0]


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Groceries", **flags) -> Transaction:
    _counter[0] += 1
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=round(amount, 2), category=category, row_hash=f"t-{_counter[0]}-{account.id}-{when}-{merchant}-{amount}", **flags)
    session.add(row)
    return row


def _household(session: Session, rng: random.Random, today: date) -> dict:
    checking = Account(name="Checking", institution="Example Bank", kind="checking", balance=round(rng.uniform(-300, 6000), 2), balance_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc), connector="manual", external_id="chk")
    session.add(checking)
    if rng.random() < 0.4:
        session.add(Account(name="Savings", institution="Example Bank", kind="savings", balance=round(rng.uniform(0, 9000), 2), connector="manual", external_id="sav"))
    session.flush()
    cards: list[Account] = []
    for i in range(rng.randint(0, 2)):
        limit = round(rng.uniform(300, 10000), 2)
        card = Account(name=f"Card {i}", institution="Example Bank", kind="card", balance=round(rng.uniform(-50, limit), 2), credit_limit=limit, connector="manual", external_id=f"card{i}")
        session.add(card)
        session.flush()
        cards.append(card)
        session.add(Debt(name=f"Card {i}", kind="card", balance=max(0.0, card.balance or 0.0), apr=round(rng.uniform(0, 36), 2), minimum=rng.choice([0.0, round(rng.uniform(25, 200), 2)]), due_day=rng.randint(1, 31), account_id=card.id, credit_limit=limit))
    set_setting(session, "buffer", rng.choice([0.0, 50.0, round(rng.uniform(0, 800), 2)]))
    set_setting(session, "cards", {"pay_from": rng.choice(["auto", "auto", "split", "next", "minimum", "mid", "end", "off"])})
    incomes = []
    for i in range(rng.choice([0, 1, 1, 1, 2])):
        kind = rng.choice(["weekly", "biweekly", "biweekly", "semimonthly", "monthly"])
        pay_days, pay_day = "", 0
        next_date = today + timedelta(days=rng.randint(-3, 20))
        if kind == "semimonthly":
            pay_days = rng.choice(["", "1,15"])
            anchors = sorted(a for y, m in cadence._months_around(today) for a in cadence.semimonthly_anchors(y, m, pay_days))
            next_date = next((a for a in anchors if a >= today - timedelta(days=rng.randint(0, 2))), anchors[-1])
        if kind == "monthly":
            pay_day = rng.choice([next_date.day, cadence.pay_day_from(next_date), 31, rng.randint(1, 31)])
        income = Income(name=f"Employer {i}", cadence=kind, expected_net=round(rng.uniform(400, 4000), 2), expected_net_end=round(rng.uniform(400, 4000), 2) if kind == "semimonthly" and rng.random() < 0.5 else None,
                        next_date=next_date, account_id=checking.id, pay_days=pay_days, pay_day=pay_day)
        session.add(income)
        incomes.append(income)
    bills: list[Bill] = []
    for i in range(rng.randint(0, 30)):
        kind = rng.choice(["monthly"] * 6 + ["weekly", "biweekly", "quarterly", "semiannual", "yearly"])
        anchor = today + timedelta(days=rng.randint(-45, 70)) if kind != "monthly" and rng.random() < 0.85 else None
        payer = rng.choice([checking.id, checking.id, None] + [c.id for c in cards])
        category = rng.choice(CATEGORIES)
        name = f"Bill {i}"
        if category == "Credit Card Payment" and cards and rng.random() < 0.5:
            name = f"{rng.choice(cards).name} payment"
        bill = Bill(name=name, kind=rng.choice(["bill", "bill", "subscription"]), amount=round(rng.uniform(1, 1500), 2), amount_mode=rng.choice(["fixed", "fixed", "estimate"]), cadence=kind, due_day=rng.randint(1, 31), anchor_date=anchor,
                    autopay=rng.random() < 0.5, pay_from_account_id=payer, grace_days=rng.choice([0, 0, 3, 10]), movable=rng.random() < 0.5, paid_from=rng.choice(["due", "due", "mid", "end"]), category=category,
                    created_on=today - timedelta(days=rng.randint(0, 400)) if rng.random() < 0.8 else None,
                    starts_on=today + timedelta(days=rng.randint(-20, 60)) if rng.random() < 0.15 else None, ends_on=today + timedelta(days=rng.randint(-5, 120)) if rng.random() < 0.15 else None)
        session.add(bill)
        bills.append(bill)
    session.flush()
    for i in range(rng.randint(0, 5)):
        taken = today - timedelta(days=rng.randint(0, 20))
        session.add(Advance(provider=rng.choice(["Example Advance", "Example Cash App"]), amount=rng.choice([50.0, 75.0, 100.0, 150.0, 250.0]), fee=rng.choice([0.0, 3.0, 6.0]), taken_on=taken, repay_date=today + timedelta(days=rng.randint(-ADVANCE_PAST_DAYS, 40))))
    for i in range(rng.randint(0, 5)):
        count = rng.randint(2, 12)
        remaining = rng.randint(1, count)
        installment = round(rng.uniform(10, 300), 2)
        next_date = today + timedelta(days=rng.randint(0, 30))
        cadence_days = rng.choice([14, 30])
        schedule = "[]"
        if rng.random() < 0.4:
            schedule = json.dumps([{"date": (next_date + timedelta(days=cadence_days * k)).isoformat(), "amount": installment, "principal": installment, "interest": 0.0} for k in range(remaining)])
        session.add(InstallmentPlan(provider="Example BNPL", merchant=f"Plan {i}", total=round(installment * count, 2), installment=installment, count=count, remaining=remaining, next_date=next_date, cadence_days=cadence_days, schedule=schedule,
                                    pay_from_account_id=rng.choice([None, checking.id] + [c.id for c in cards])))
    # Everyday spending over the last eight weeks, for the pace; and some bills paid on their last due date.
    for k in range(rng.randint(0, 40)):
        _tx(session, rng.choice([checking] + cards), today - timedelta(days=rng.randint(1, 56)), rng.choice(["Example Grocery", "Example Fuel", "Example Cafe"]), rng.uniform(3, 120))
    from app.engine.billdates import bill_dates

    for bill in bills:
        if rng.random() < 0.4:
            dues = bill_dates(bill, today - timedelta(days=45), today + timedelta(days=3))
            if dues:
                due = rng.choice(dues)
                if due <= today:
                    _tx(session, checking, due + timedelta(days=rng.randint(-2, 2)), bill.name, bill.amount * rng.uniform(0.97, 1.03), bill.category or "Bills & Utilities")
    session.commit()
    if rng.random() < 0.6:
        match_payments(session, today)
    return {"checking": checking, "cards": cards, "bills": bills, "incomes": incomes}


def _walk(node, path: str, bad: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{path}.{k}", bad)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", bad)
    elif isinstance(node, float):
        if math.isnan(node) or math.isinf(node) or (node == 0.0 and math.copysign(1.0, node) < 0):
            bad.append((path, node))


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.02


def _check_snapshot(snap: dict, today: date, seed_value: int) -> None:
    where = f"seed {seed_value}, {today}"
    bad: list = []
    _walk(json.loads(json.dumps(snap, default=str)), "snap", bad)
    assert not bad, (where, bad[:10])
    parts = snap["safe_to_spend"]["parts"]
    assert _close(snap["safe_to_spend"]["amount"], parts["cash"] - parts["due_before_payday"] - parts["advances"] - parts["buffer"] - parts["next_window_shortfall"]), where
    assert 0.0 <= snap["safe_to_spend"]["share_of_cash"] <= 1.0
    assert parts["due_before_payday"] >= 0 and parts["advances"] >= 0 and parts["buffer"] >= 0 and parts["next_window_shortfall"] >= 0, where
    assert _close(parts["next_window_shortfall"], max(0.0, snap["window"]["total"] + snap["set_aside"]["total"] + parts["advances_on_payday"] - snap["next_paycheck"]["amount"])), where
    assert _close(parts["advances"] + parts["advances_on_payday"], snap["advances"]["total"]), where
    # The accounts' shares add up to the figure.
    assert _close(sum(r["safe"] for r in snap["safe_to_spend"]["by_account"]), snap["safe_to_spend"]["amount"]) or not snap["safe_to_spend"]["by_account"], where
    # Every list's total is the sum of what leaves checking in it; a bill riding a card is listed, not summed.
    for key in ("due_before_payday", "window"):
        items = snap[key]["items"]
        assert _close(snap[key]["total"], sum(e["amount"] for e in items if not e.get("via_card"))), (where, key)
    assert _close(snap["advances"]["total"], sum(e["amount"] for e in snap["advances"]["items"])), where
    aside_items = [e for e in snap["set_aside"]["items"] if not e.get("via_card") and e.get("kind") != "card_payment"]
    assert _close(snap["set_aside"]["total"], sum(e["amount"] for e in aside_items if e["amount"] >= 500) / 2 + sum(e["amount"] for e in aside_items if e["amount"] < 500)), where
    # No charge is in two places: before payday, the window after it, the window after that.
    seen: dict = {}
    for key in ("due_before_payday", "window", "set_aside"):
        for e in snap[key]["items"]:
            k = (e.get("source"), e["date"], e.get("due"))  # a late period is dated today, with its own due date beside it
            assert k not in seen, (where, k, seen[k] if k in seen else None, key)
            seen[k] = key
    # The four windows: each adds up, they meet with no gap or overlap, nothing in them is counted twice.
    windows = snap["windows"]
    assert len(windows) == 4, where
    paydays = [p["date"] for p in snap["paydays"]]
    assert paydays == sorted(paydays) and len(set(paydays)) == len(paydays), where
    assert windows[0]["start"] == snap["next_paycheck"]["date"] == snap["window"]["start"], where
    assert _close(windows[0]["carried_in"], snap["cash"] - snap["due_before_payday"]["total"] - snap["buffer"]), where
    assert _close(windows[0]["bills"]["total"], snap["window"]["total"]) and _close(windows[0]["set_aside"]["total"], snap["set_aside"]["total"]), where
    counted: set = set()
    for k, w in enumerate(windows):
        assert w["start"] == paydays[k], (where, k)
        if k + 1 < len(windows):
            assert (date.fromisoformat(w["end"]) + timedelta(days=1)).isoformat() == windows[k + 1]["start"], (where, k)
            assert _close(windows[k + 1]["carried_in"], w["set_aside"]["total"]), (where, k)
        assert w["start"] <= w["end"], (where, k)
        assert _close(w["bills"]["total"], sum(e["amount"] for e in w["bills"]["items"] if not e.get("via_card"))), (where, k)
        assert _close(w["advances"]["total"], sum(e["amount"] for e in w["advances"]["items"])), (where, k)
        assert _close(w["safe"], w["carried_in"] + w["paycheck"] - w["advances"]["total"] - w["bills"]["total"] - w["set_aside"]["total"]), (where, k)
        for e in w["bills"]["items"]:
            key = (e.get("source"), e["date"])
            assert key not in counted, (where, k, key)
            counted.add(key)
            assert w["start"] <= e["date"] <= w["end"] or e.get("paid_from") in ("mid", "end"), (where, k, e["name"], e["date"])
    # Nothing in the past: a late bill is dated today with its real due date beside it. An advance not yet repaid keeps
    # the day the app set (borrowing never moves it on) and is still counted before payday.
    t = today.isoformat()
    for key in ("due_before_payday", "window", "set_aside"):
        for e in snap[key]["items"]:
            assert e["date"] >= t, (where, key, e)
    for e in snap["advances"]["items"]:
        assert e["date"] <= snap["next_paycheck"]["date"], (where, e)
    for e in snap["calendar"]["events"]:
        assert t <= e["date"] <= (today + timedelta(days=60)).isoformat(), (where, e)
    assert snap["next_paycheck"]["date"] >= t and snap["payday"]["date"] >= t, where
    # The daily path: sixty days from today, each week's low point the least of its days.
    path = snap["projection"]
    assert [p["date"] for p in path] == [(today + timedelta(days=i)).isoformat() for i in range(60)], where
    for p in path:
        assert _close(p["available"], p["cash"] - snap["buffer"]) or p["available"] <= p["cash"] - snap["buffer"] + 0.02, (where, p)  # a reserve only lowers it
    for w in snap["calendar"]["weeks"]:
        in_week = [p for p in path if w["start"] <= p["date"] <= w["end"]]
        if not in_week:
            assert w["low"] is None, (where, w)
            continue
        low = min(in_week, key=lambda p: p["at_pace"])
        assert w["low"] == low["at_pace"] and w["low_date"] == low["date"], (where, w, low)
    assert snap["calendar"]["weeks"][0]["start"] <= t <= snap["calendar"]["weeks"][0]["end"], where


def test_the_snapshot_holds_its_invariants_on_random_households():
    for seed_value in range(1, 33):
        rng = random.Random(7100 + seed_value)
        today = _today(rng)
        _fresh()
        with Session(engine) as session:
            _household(session, rng, today)
            snap = _finance_snapshot(session, today)
        _check_snapshot(snap, today, seed_value)


# ---- payments.match_payments: each transaction and each period matched once; the same on the second run and the next day


def _ledger(session: Session, rng: random.Random, today: date) -> list[Bill]:
    checking = Account(name="Checking", institution="Example Bank", kind="checking", balance=1000.0, connector="manual", external_id="chk")
    session.add(checking)
    session.flush()
    bills = []
    for i in range(rng.randint(1, 8)):
        kind = rng.choice(["monthly", "monthly", "weekly", "biweekly"])
        bill = Bill(name=rng.choice([f"Biller {i}", f"Biller {i} Services"]), amount=round(rng.uniform(5, 900), 2), amount_mode=rng.choice(["fixed", "estimate"]), cadence=kind, due_day=rng.randint(1, 31),
                    anchor_date=today + timedelta(days=rng.randint(-30, 10)) if kind != "monthly" else None, grace_days=rng.choice([0, 3, 10, 30]), created_on=today - timedelta(days=rng.randint(0, 200)), pay_from_account_id=checking.id)
        session.add(bill)
        bills.append(bill)
    session.flush()
    from app.engine.billdates import bill_dates

    for bill in bills:
        for due in bill_dates(bill, today - timedelta(days=80), today):
            for _ in range(rng.choice([0, 1, 1, 1, 2])):
                _tx(session, checking, due + timedelta(days=rng.randint(-8, 8)), bill.name if rng.random() < 0.8 else "Something Else", bill.amount * rng.choice([1.0, 1.0, rng.uniform(0.8, 1.2)]), "Bills & Utilities")
    for _ in range(rng.randint(0, 20)):
        _tx(session, checking, today - timedelta(days=rng.randint(0, 70)), rng.choice(["Example Grocery", "Example Fuel"]), rng.uniform(3, 900))
    session.commit()
    return bills


def _rows(session: Session) -> list[tuple]:
    return sorted((p.bill_id, p.due_date.isoformat(), p.status, p.paid_date.isoformat() if p.paid_date else None, p.transaction_id, p.amount) for p in session.exec(select(Payment)).all())


def test_matching_uses_each_transaction_and_period_once_and_is_stable_across_runs_and_days():
    for seed_value in range(1, 41):
        rng = random.Random(7200 + seed_value)
        today = _today(rng)
        _fresh()
        with Session(engine) as session:
            _ledger(session, rng, today)
            counts = match_payments(session, today)
            first = _rows(session)
            assert sum(counts.values()) == len(first), (seed_value, counts, len(first))
            used = [r[4] for r in first if r[4] is not None]
            assert len(used) == len(set(used)), (seed_value, "a transaction paid two periods", first)
            periods = [(r[0], r[1]) for r in first]
            assert len(periods) == len(set(periods)), (seed_value, "a period twice", first)
            for r in first:
                assert (r[2] == "paid") == (r[4] is not None), (seed_value, r)
                assert r[2] != "scheduled" or r[1] >= today.isoformat(), (seed_value, r)
                assert r[2] not in ("late", "past_due") or r[1] < today.isoformat(), (seed_value, r)
            match_payments(session, today)
            assert _rows(session) == first, (seed_value, "a second run changed the rows")
            for step in range(1, 4):
                match_payments(session, today + timedelta(days=step))
                rows = _rows(session)
                used = [r[4] for r in rows if r[4] is not None]
                assert len(used) == len(set(used)), (seed_value, step, "a transaction paid two periods on a later day", [r for r in rows if used.count(r[4]) > 1])
                before = {(r[0], r[1]): r for r in first if r[2] == "paid"}
                for r in rows:
                    if (r[0], r[1]) in before:
                        assert r == before[(r[0], r[1])], (seed_value, step, "a paid period changed on a later day", before[(r[0], r[1])], r)
