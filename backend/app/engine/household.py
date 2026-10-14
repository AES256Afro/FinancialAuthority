"""The synthetic household: the fixture every test and demo mode run on.

Deterministic from a seed. Two paychecks a month with one short one, fourteen bills, two cards, an auto
loan, three installment plans, one advance app, a price increase and a duplicate subscription. No real
person's data; the numbers match the mock-ups so the screens can be compared with the drawings.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256

from sqlmodel import Session, select

import json
from hashlib import sha256 as _sha256

from ..models import Account, Advance, Bill, Debt, Goal, Income, InstallmentPlan, Paystub, Transaction

TODAY = date(2026, 10, 14)
NET_PAY = 2240.0
NEXT_PAYDAY = date(2026, 10, 16)


@dataclass
class Household:
    accounts: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    bills: list[dict] = field(default_factory=list)
    debts: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)
    advances: list[dict] = field(default_factory=list)
    incomes: list[dict] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)


BILLS = [
    # name, amount, due_day, autopay, category, kind, movable
    ("Rent", 1295.0, 1, False, "Rent", "bill", False),
    ("Water and trash", 64.0, 2, False, "Bills & Utilities", "bill", False),
    ("Car insurance", 138.0, 3, True, "Insurance", "bill", True),
    ("Gym", 30.0, 5, True, "Health & Fitness", "subscription", True),
    ("Music (two)", 10.0, 11, True, "Entertainment", "subscription", True),
    ("Streaming", 17.0, 15, True, "Entertainment", "subscription", True),
    ("Tolls", 40.0, 15, False, "Auto & Transport", "bill", True),
    ("Music", 11.0, 19, True, "Entertainment", "subscription", True),
    ("Electricity", 142.0, 20, False, "Bills & Utilities", "bill", True),
    ("Phone", 96.0, 22, True, "Bills & Utilities", "bill", True),
    ("Internet", 85.0, 24, False, "Bills & Utilities", "bill", True),
    ("Card minimum", 45.0, 25, True, "Credit Card Payment", "bill", True),
    ("Car loan", 412.0, 28, False, "Loan Payment", "bill", True),
]


def paydays(until: date, count: int = 30) -> list[date]:
    """Every other Friday, ending with the next payday after TODAY."""
    days = []
    d = NEXT_PAYDAY
    while len(days) < count:
        days.append(d)
        d -= timedelta(days=14)
    return sorted(x for x in days if x <= until)


def _row_hash(parts: tuple) -> str:
    return sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def generate(today: date = TODAY, months: int = 12, seed: int = 42) -> Household:
    rng = random.Random(seed)
    h = Household()
    start = today - timedelta(days=30 * months)
    h.accounts = [
        {"key": "checking", "name": "Everyday Checking", "institution": "Example Bank", "kind": "checking"},
        {"key": "card", "name": "Rewards Card", "institution": "Example Bank", "kind": "card"},
        {"key": "store", "name": "Store Card", "institution": "Example Retail Bank", "kind": "card"},
        {"key": "loan", "name": "Auto Loan", "institution": "Example Credit Union", "kind": "loan"},
    ]

    def tx(d: date, account: str, merchant: str, amount: float, category: str, **flags) -> None:
        h.transactions.append({"date": d, "account": account, "merchant": merchant, "amount": round(amount, 2), "category": category, **flags})

    # Paychecks, with one short one two months back (8 fewer hours, overtime missing); the deposit equals the stub's net.
    stub_net = {s["pay_date"]: s["net"] for s in paystubs_for(h, today)}
    for pd in paydays(today):
        if pd < start:
            continue
        net = stub_net.get(pd, NET_PAY)
        tx(pd, "checking", "Example Employer Payroll", -net, "Income", is_income=True)

    # Bills, monthly on their due day, with electricity varying by season and a streaming price rise on Oct 1.
    d = date(start.year, start.month, 1)
    while d <= today:
        for name, amount, due_day, autopay, category, kind, _movable in BILLS:
            due = date(d.year, d.month, min(due_day, 28))
            if due < start or due > today:
                continue
            if name == "Electricity":
                amount = round(110 + 50 * abs(((due.month - 1) % 12) - 6) / 6 + rng.uniform(-8, 8), 2)
            if name == "Streaming" and due < date(2026, 10, 1):
                amount = 15.0
            if name == "Music (two)" and due < date(2026, 7, 1):
                continue  # the duplicate started in July
            if name == "Card minimum":
                tx(due, "checking", "Payment to Rewards Card", amount, "Credit Card Payment", is_card_payment=True)
                tx(due, "card", "Payment received", -amount, "Credit Card Payment", is_card_payment=True)
                continue
            if name == "Car loan" and due < date(2026, 9, 28):
                continue  # the loan was funded in September
            tx(due, "checking", name, amount, category)
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)

    # Installment plans, every two weeks from their start.
    plans = [
        ("Couch plan", 248.0, 62.0, 4, date(2026, 9, 9)),
        ("Laptop plan", 228.0, 38.0, 6, date(2026, 9, 15)),
        ("Tyres plan", 180.0, 45.0, 4, date(2026, 6, 2)),
    ]
    for name, total, inst, count, first in plans:
        paid = 0
        due = first
        while due <= today and paid < count:
            tx(due, "checking", f"{name} installment", inst, "Loan Payment")
            paid += 1
            due += timedelta(days=14)
        if paid < count:
            h.plans.append({"provider": "Example BNPL", "merchant": name, "total": total, "installment": inst, "count": count, "remaining": count - paid, "next_date": due})

    # Advances: taken a week before some paydays, repaid on the payday.
    for pd in paydays(today + timedelta(days=14)):
        if pd < start or rng.random() > 0.45:
            continue
        taken = pd - timedelta(days=rng.randint(5, 9))
        amount = rng.choice([75.0, 100.0, 150.0])
        fee = 6.0 if amount >= 100 else 0.0
        if taken <= today:
            tx(taken, "checking", "Paycheck advance", -amount, "Transfer", is_transfer=True)
        if pd <= today:
            tx(pd, "checking", "Paycheck advance repayment", amount + fee, "Fees & Charges")
        else:
            h.advances.append({"provider": "Example Advance", "amount": amount, "fee": fee, "taken_on": taken, "repay_date": pd})
    if not any(a["repay_date"] == NEXT_PAYDAY for a in h.advances):
        h.advances.append({"provider": "Example Advance", "amount": 150.0, "fee": 6.0, "taken_on": date(2026, 10, 8), "repay_date": NEXT_PAYDAY})
    h.advances.append({"provider": "Example Cash App", "amount": 75.0, "fee": 0.0, "taken_on": date(2026, 10, 10), "repay_date": NEXT_PAYDAY})

    # Two recurring charges the household has no bill for yet, so the Needs-you flow has something to show.
    d = date(start.year, start.month, 1)
    while d <= today:
        for name, amount, day, first in (("Example Cloud Storage", 2.99, 9, date(2026, 3, 1)), ("Example Storage Unit", 89.0, 12, date(2026, 5, 1))):
            due = date(d.year, d.month, day)
            if first <= due <= today and due >= start:
                tx(due, "card" if amount < 10 else "checking", name, amount, "Shopping" if amount < 10 else "Home")
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)

    # Everyday spending.
    d = start
    while d <= today:
        if d.weekday() in (1, 4, 6) and rng.random() < 0.8:
            tx(d, rng.choice(["checking", "checking", "card"]), rng.choice(["Example Grocery", "Example Market"]), rng.uniform(28, 95), "Groceries")
        if d.weekday() in (2, 5) and rng.random() < 0.6:
            tx(d, "card", rng.choice(["Example Taqueria", "Example Coffee", "Example Pizza"]), rng.uniform(9, 42), "Dining & Drinks")
        if d.weekday() == 3 and rng.random() < 0.85:
            tx(d, "checking", "Example Fuel", rng.uniform(34, 58), "Gas")
        if rng.random() < 0.08:
            tx(d, "card", "Example Online Store", rng.uniform(12, 80), "Shopping")
        d += timedelta(days=1)

    # Opening balance chosen so that today's checking balance is $312, the figure on the mock-ups.
    outflow = sum(t["amount"] for t in h.transactions if t["account"] == "checking")
    h.accounts[0]["balance"] = 312.0
    h.accounts[0]["opening"] = round(312.0 + outflow, 2)
    h.accounts[1]["balance"] = 2310.0
    h.accounts[2]["balance"] = 640.0
    h.accounts[3]["balance"] = 18400.0

    h.bills = [
        {"name": n, "amount": a, "due_day": dd, "autopay": ap, "category": c, "kind": k, "movable": m,
         "amount_mode": "estimate" if n == "Electricity" else "fixed", "move_window": "1-28" if m else ""}
        for n, a, dd, ap, c, k, m in BILLS
    ]
    h.debts = [
        {"name": "Auto loan", "kind": "loan", "balance": 18400.0, "apr": 7.9, "minimum": 412.0, "due_day": 28, "account": "loan"},
        {"name": "Rewards Card", "kind": "card", "balance": 2310.0, "apr": 29.9, "minimum": 45.0, "statement_day": 17, "due_day": 25, "account": "card"},
        {"name": "Store Card", "kind": "card", "balance": 640.0, "apr": 31.0, "minimum": 25.0, "statement_day": 26, "due_day": 20, "account": "store"},
    ]
    h.incomes = [{"name": "Example Employer", "cadence": "biweekly", "expected_net": NET_PAY, "next_date": NEXT_PAYDAY, "account": "checking"}]
    h.goals = [{"name": "Emergency fund", "kind": "save", "target": 1000.0, "pace": 50.0, "priority": 1, "funded": 120.0}]
    h.transactions.sort(key=lambda t: t["date"])
    return h


def seed(session: Session, household: Household | None = None) -> dict:
    """Insert the household into an empty database. Idempotent: a second call does nothing."""
    if session.exec(select(Account)).first() is not None:
        return {"seeded": False}
    h = household or generate()
    ids: dict[str, int] = {}
    for a in h.accounts:
        row = Account(name=a["name"], institution=a["institution"], kind=a["kind"], balance=a.get("balance"), connector="demo", external_id=a["key"])
        session.add(row)
        session.flush()
        ids[a["key"]] = row.id
    for t in h.transactions:
        session.add(Transaction(
            account_id=ids[t["account"]], date=t["date"], merchant=t["merchant"], raw_name=t["merchant"], amount=t["amount"],
            category=t["category"], is_transfer=t.get("is_transfer", False), is_card_payment=t.get("is_card_payment", False),
            is_income=t.get("is_income", False), source="demo",
            row_hash=_row_hash((t["date"], t["account"], t["merchant"], t["amount"], t["category"], len(ids))) + str(len(session.new)),
        ))
    for b in h.bills:
        session.add(Bill(name=b["name"], kind=b["kind"], amount=b["amount"], amount_mode=b["amount_mode"], due_day=b["due_day"], autopay=b["autopay"],
                         pay_from_account_id=ids["checking"], movable=b["movable"], move_window=b["move_window"], category=b["category"]))
    for dbt in h.debts:
        session.add(Debt(name=dbt["name"], kind=dbt["kind"], balance=dbt["balance"], apr=dbt["apr"], minimum=dbt["minimum"],
                         statement_day=dbt.get("statement_day"), due_day=dbt.get("due_day"), account_id=ids[dbt["account"]]))
    for p in h.plans:
        session.add(InstallmentPlan(provider=p["provider"], merchant=p["merchant"], total=p["total"], installment=p["installment"], count=p["count"],
                                    remaining=p["remaining"], next_date=p["next_date"]))
    for adv in h.advances:
        session.add(Advance(provider=adv["provider"], amount=adv["amount"], fee=adv["fee"], taken_on=adv["taken_on"], repay_date=adv["repay_date"]))
    for inc in h.incomes:
        session.add(Income(name=inc["name"], cadence=inc["cadence"], expected_net=inc["expected_net"], next_date=inc["next_date"], account_id=ids[inc["account"]]))
    for g in h.goals:
        session.add(Goal(name=g["name"], kind=g["kind"], target=g["target"], pace=g["pace"], priority=g["priority"], funded=g["funded"]))
    session.flush()
    # The last six paystubs, matched to their deposits; one short one, as on the mock-ups.
    deposits = {t.date: t for t in session.exec(select(Transaction).where(Transaction.is_income == True)).all()}  # noqa: E712
    for pd in paystubs_for(h, today=TODAY):
        deposit = deposits.get(pd["pay_date"])
        session.add(Paystub(
            employer="Example Employer", provider="Example Payroll", pay_date=pd["pay_date"], period_start=pd["pay_date"] - timedelta(days=18), period_end=pd["pay_date"] - timedelta(days=5),
            gross=pd["gross"], net=pd["net"], hours=pd["hours"], deductions=json.dumps(pd["deductions"]), source="demo", transaction_id=deposit.id if deposit else None,
            expected_net=NET_PAY, difference=round(pd["net"] - NET_PAY, 2), stub_hash=_sha256(f"demo|{pd['pay_date']}|{pd['net']}".encode()).hexdigest(),
        ))
    session.commit()
    return {"seeded": True, "accounts": len(h.accounts), "transactions": len(h.transactions), "bills": len(h.bills)}


def paystubs_for(h: Household, today: date) -> list[dict]:
    stubs = []
    for pd in paydays(today)[-6:]:
        short = pd == date(2026, 8, 21)
        hours = 72.0 if short else 80.0
        gross = round(2980.77 * hours / 80, 2)
        taxes = {"Federal income tax": round(gross * 0.10, 2), "Social Security": round(gross * 0.062, 2), "Medicare": round(gross * 0.0145, 2)}
        benefits = {"Health insurance": 112.50, "401(k)": round(gross * 0.03, 2), "Dental": 12.74}
        net = round(gross - sum(taxes.values()) - sum(benefits.values()), 2)
        stubs.append({"pay_date": pd, "gross": gross, "net": net, "hours": hours, "deductions": [{"name": k, "amount": v} for k, v in {**taxes, **benefits}.items()]})
    return stubs
