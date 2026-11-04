"""Sweep 25: the app run forward through a calendar. Earlier rounds judged single days (hand models, properties,
fuzzing); here a small fictional household is stepped day by day across month ends, Feb 29 2028, both changes of the
clocks, two year ends and paydays on weekends, the way the nightly jobs and the bank would step it: each morning the
jobs run and the plan is read, the day's events post as the plan predicted them, the jobs run again and the plan is
read again. The invariants are the definitions a person would hold it to on every day: the line reconciles, what is
forecast does not change because a day passed, a payday is never in the past, a paid period stays paid, reports add up
to the ledger, a plan ends when its schedule says. The dates and amounts here are worked out from the seed alone."""

from __future__ import annotations

import calendar
import importlib
import inspect
import pkgutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json

import pytest
from sqlmodel import Session, SQLModel, select
from zoneinfo import ZoneInfo

from app import engine as engine_package
from app import services
from app.clock import local_date
from app.config import settings
from app.db import bump_version, engine, init_db
from app.engine.borrowing import borrowing_overview, refresh_plan_progress, sync_borrowing
from app.engine.budgets import budget_plan
from app.engine.custom_reports import run_report, year_in_review
from app.engine.paychecks import paycheck_views
from app.engine.payments import match_payments
from app.engine.recap import recap, record_plan
from app.engine.reports import net_worth, owed, spending_summary
from app.engine.saving import saving_plan
from app.engine.sinking import sinking_funds
from app.engine.snapshot import finance_snapshot
from app.jobs.detect import run_detection
from app.mail.classify import parse_date_text
from app.models import Account, Bill, CategoryBudget, Debt, Goal, Income, InstallmentPlan, Payment, Transaction, TransactionSplit

START = date(2027, 11, 1)
END = date(2029, 3, 15)  # 500 days: two year ends, Feb 29 2028 and Feb 28 2029, DST on 2028-03-12, 2028-11-05 and 2029-03-11
CENT = 0.011


def last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def months_from(d: date, n: int) -> tuple[int, int]:
    k = d.year * 12 + d.month - 1 + n
    return k // 12, k % 12 + 1


def before_weekend(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def month_dates(day: int, start: date, end: date) -> list[date]:
    out, k = [], 0
    while True:
        y, m = months_from(start.replace(day=1), k)
        d = date(y, m, min(day, last_day(y, m)))
        if d > end:
            return out
        if d >= start:
            out.append(d)
        k += 1


def semi_dates(start: date, end: date, pay_days: str = "") -> list[date]:
    out = set()
    for first in month_dates(1, start - timedelta(days=40), end + timedelta(days=40)):
        out.add(before_weekend(first.replace(day=15)))
        out.add(before_weekend(first if pay_days == "1,15" else first.replace(day=last_day(first.year, first.month))))
    return sorted(d for d in out if start <= d <= end)


@dataclass
class Sim:
    """What a run saw: what broke, by invariant, and the figures later tests look at."""

    days: int = 0
    checks: int = 0
    broke: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks += 1
        if not ok:
            self.broke[name].append(detail)

    def none(self, *names: str) -> None:
        assert self.days >= 425, "at least fourteen months"
        found = {n: self.broke[n] for n in names if self.broke.get(n)}
        assert not found, "\n".join(f"{n}: {len(v)} days, first: " + " | ".join(v[:4]) for n, v in found.items())


@dataclass
class Household:
    biweekly_from: date = date(2027, 11, 5)  # a Friday
    biweekly_net: float = 1910.0
    semi_net: float = 1120.0
    semi_end: float = 1265.0
    semi_days: str = ""
    weekend_bills_post_monday: bool = False
    saturday_check_posts_friday: bool = False
    detect: bool = False
    second_income: bool = True
    monthly_only: bool = False  # one check, on the last day of each month
    plain_deposits: bool = False  # the bank names every check alike, so only its day and amount tell them apart
    bills_paid_early: bool = False  # the owner pays each bill three days before it is due
    card_no_minimum: bool = False  # no minimum on file: the card is paid across the checks, as the plan holds it


class World:
    """The fictional household, its bank and its calendar. Nothing here calls the engine's date code."""

    def __init__(self, session: Session, h: Household):
        self.s, self.h = session, h
        self.n = 0
        self.checking = self.add(Account(name="Oak Checking", kind="checking", balance=9000.0, balance_at=datetime(2027, 10, 31, 18, tzinfo=timezone.utc)))
        self.savings = self.add(Account(name="Oak Savings", kind="savings", balance=500.0, balance_at=datetime(2027, 10, 31, 18, tzinfo=timezone.utc)))
        self.card = self.add(Account(name="Harbor Card", kind="card", balance=900.0, balance_at=datetime(2027, 10, 31, 18, tzinfo=timezone.utc)))
        self.card_debt = self.add(Debt(name="Harbor Card", kind="card", balance=900.0, apr=22.0, minimum=0.0 if h.card_no_minimum else 60.0, statement_day=3, due_day=28, account_id=self.card.id))
        self.loan = self.add(Debt(name="Cedar Auto Loan", kind="loan", balance=8000.0, apr=6.0, minimum=251.0, due_day=15))
        if h.monthly_only:
            self.bi = self.add(Income(name="Northwind Payroll", cadence="monthly", expected_net=h.biweekly_net, next_date=date(2027, 11, 30), pay_day=31, account_id=self.checking.id))
        else:
            self.bi = self.add(Income(name="Northwind Payroll", cadence="biweekly", expected_net=h.biweekly_net, next_date=h.biweekly_from, account_id=self.checking.id))
        self.semi = None
        if h.second_income:
            self.semi = self.add(Income(name="Fabrikam Payroll", cadence="semimonthly", expected_net=h.semi_net, expected_net_end=h.semi_end, next_date=semi_dates(START, END, h.semi_days)[0], pay_days=h.semi_days, account_id=self.checking.id))
        common = {"created_on": START, "pay_from_account_id": self.checking.id, "autopay": True}
        self.storage = self.add(Bill(name="Riverbend Storage", amount=123.0, due_day=31, category="Bills & Utilities", **common))
        self.rent = self.add(Bill(name="Lakeside Rent", amount=1450.0, due_day=1, category="Rent", paid_from="end" if h.second_income else "due", **common))
        self.car = self.add(Bill(name="Cedar Auto Payment", amount=251.0, due_day=15, category="Loan Payment", **common))
        self.water = self.add(Bill(name="Tidewater Utility", amount=88.0, cadence="quarterly", due_day=31, anchor_date=date(2028, 1, 31), category="Bills & Utilities", **common))
        self.bills = {self.storage.id: month_dates(31, START, END), self.rent.id: month_dates(1, START, END), self.car.id: month_dates(15, START, END),
                      self.water.id: [date(y, m, min(31, last_day(y, m))) for y, m in ((2028, 1), (2028, 4), (2028, 7), (2028, 10), (2029, 1))]}
        self.gym = self.add(Bill(name="Harbor Gym", amount=17.5, cadence="weekly", due_day=1, anchor_date=date(2027, 11, 8), category="Fitness", kind="subscription", **common))
        self.dues = self.add(Bill(name="Marina Dues", amount=310.0, cadence="yearly", due_day=29, anchor_date=date(2028, 2, 29), category="Fees & Charges", **common))
        self.policy = self.add(Bill(name="Anchor Insurance", amount=402.0, cadence="semiannual", due_day=30, anchor_date=date(2028, 8, 30), category="Insurance", **common))
        self.bills[self.gym.id] = [date(2027, 11, 8) + timedelta(days=7 * k) for k in range(80)]
        self.bills[self.dues.id] = [date(2028, 2, 29), date(2029, 2, 28)]
        self.bills[self.policy.id] = [date(2028, 8, 30), date(2029, 2, 28)]
        self.amounts = {self.storage.id: 123.0, self.rent.id: 1450.0, self.car.id: 251.0, self.water.id: 88.0, self.gym.id: 17.5, self.dues.id: 310.0, self.policy.id: 402.0}
        self.sched = [{"date": d.isoformat(), "amount": 52.18 if d != date(2028, 11, 5) else 52.25} for d in month_dates(5, date(2027, 12, 5), date(2028, 11, 5))]
        self.plan_s = self.add(InstallmentPlan(provider="Affirm", merchant="Summit Bikes", total=626.23, installment=52.18, count=12, remaining=12, next_date=date(2027, 12, 5), cadence_days=30, schedule=json.dumps(self.sched), schedule_as_of=date(2027, 11, 1), balance=626.23, pay_from_account_id=self.checking.id))
        self.plan_c = self.add(InstallmentPlan(provider="Klarna", merchant="Vale Sofa", total=240.0, installment=40.0, count=6, remaining=6, next_date=date(2028, 2, 19), cadence_days=14, pay_from_account_id=self.checking.id))
        self.plan_c_dates = [date(2028, 2, 19) + timedelta(days=14 * k) for k in range(6)]
        self.goal = self.add(Goal(name="Sailing trip", target=3000.0, target_date=date(2028, 12, 20), funded=250.0))
        self.add(CategoryBudget(category="Groceries", per_paycheck=180.0))
        session.commit()
        self.spend_ledger: list[tuple[date, float, str, str]] = []
        self.income_ledger: list[tuple[date, float]] = []
        self.landed: set[tuple[str, date]] = set()
        self.groceries: list[tuple[date, float]] = []
        self.bill_ledger: list[tuple[date, float]] = []

    def add(self, row):  # noqa: ANN001, ANN201
        self.s.add(row)
        self.s.flush()
        return row

    def checks_on(self, d: date) -> dict[str, float]:
        """The checks whose payday is `d`, by the seed's own schedule alone."""
        out: dict[str, float] = {}
        if self.h.monthly_only:
            if d.day == last_day(d.year, d.month):
                out["Northwind Payroll"] = self.h.biweekly_net
            return out
        if (d - self.h.biweekly_from).days % 14 == 0:
            out["Northwind Payroll"] = self.h.biweekly_net
        if self.h.second_income and d in semi_dates(d, d, self.h.semi_days):
            out["Fabrikam Payroll"] = self.h.semi_end if not 8 <= d.day <= 22 else self.h.semi_net
        return out

    def paydays(self, start: date, end: date) -> list[date]:
        return [start + timedelta(days=i) for i in range((end - start).days + 1) if self.checks_on(start + timedelta(days=i))]

    def next_unlanded(self, d: date) -> date:
        """The first payday on or after `d` with a check still to land."""
        return next(x for x in self.paydays(d, d + timedelta(days=45)) if any((n, x) not in self.landed for n in self.checks_on(x)))

    def last_landed(self, d: date) -> date | None:
        """The last payday whose check has come: before `d`, or today's or an early one once it has landed."""
        back = [x for x in self.paydays(d - timedelta(days=45), d + timedelta(days=2)) if x < d or any((n, x) in self.landed for n in self.checks_on(x))]
        return min(back[-1], d) if back else None

    def obligations(self, start: date, end: date) -> float:
        """Every obligation from `start` through `end` the plan has to hold, from the seed."""
        total = 0.0
        for bill, dates in self.bills.items():
            total += sum(self.amounts[bill] for d in dates if start <= d <= end)
        total += sum(s["amount"] for s in self.sched if start.isoformat() <= s["date"] <= end.isoformat())
        total += sum(40.0 for d in self.plan_c_dates if start <= d <= end)
        total += sum(60.0 for d in month_dates(28, start, end))
        return round(total, 2)

    def post(self, d: date, merchant: str, amount: float, *, account: Account | None = None, category: str = "Uncategorized", **flags) -> Transaction:
        """A row the bank posts: money out is positive, and the account's balance moves with it."""
        self.n += 1
        account = account or self.checking
        row = Transaction(account_id=account.id, date=d, merchant=merchant, raw_name=merchant, amount=amount, category=category, source="csv", row_hash=f"sim-{self.n}", **flags)
        self.s.add(row)
        account.balance = round(float(account.balance or 0.0) + (amount if account.kind == "card" else -amount), 2)
        account.balance_at = datetime(d.year, d.month, d.day, 18, tzinfo=timezone.utc)
        self.s.add(account)
        return row

    def everyday(self, d: date) -> float:
        """The spending the plan cannot predict: groceries on Tuesdays from checking, dinner on Saturdays on the card, a
        refund each quarter, one shop split across categories. Returns what left checking."""
        cash = 0.0
        if d.weekday() == 1:
            row = self.post(d, "Greenway Market", 85.4, category="Groceries")
            cash += 85.4
            self.spend_ledger.append((d, 85.4, "Greenway Market", "Groceries"))
            self.groceries.append((d, 60.4 if d.day <= 7 else 85.4))
            if d.day <= 7:
                self.s.flush()
                self.s.add(TransactionSplit(transaction_id=row.id, category="Groceries", amount=60.4))
                self.s.add(TransactionSplit(transaction_id=row.id, category="Household", amount=25.0))
        if d.weekday() == 5:
            self.post(d, "Fennel Bistro", 41.2, account=self.card, category="Restaurants")
            self.card_debt.balance = self.card.balance
            self.s.add(self.card_debt)
            self.spend_ledger.append((d, 41.2, "Fennel Bistro", "Restaurants"))
        if d.day == 20 and d.month % 3 == 0:
            self.post(d, "Greenway Market", -12.75, category="Groceries")
            cash -= 12.75
            self.spend_ledger.append((d, -12.75, "Greenway Market", "Groceries"))
            self.groceries.append((d, -12.75))
        return round(cash, 2)

    def deposit(self, d: date, payroll: str, net: float, payday: date | None = None) -> float:
        self.post(d, "DIRECT DEPOSIT" if self.h.plain_deposits else payroll, -net, is_income=True, category="Income")
        self.income_ledger.append((d, net))
        self.landed.add((payroll, payday or d))
        return -net

    def settle(self, d: date, event: dict) -> float:
        """One predicted event, as the bank shows it. Returns what left checking."""
        name, amount, kind = event["name"], float(event["amount"]), event.get("kind")
        if kind == "paycheck":
            return self.deposit(d, name.removeprefix("Payday: ").replace("Payday", "Northwind Payroll"), -amount)
        if kind == "card_payment":
            self.post(d, "Harbor Card Payment", amount, is_card_payment=True, category="Credit Card Payment")
            self.post(d, "Harbor Card Payment", -amount, account=self.card, is_card_payment=True, category="Credit Card Payment")
            self.card_debt.balance = self.card.balance
            self.s.add(self.card_debt)
            return amount
        if kind == "installment":
            lender = "AFFIRM" if name.startswith("Summit") else "KLARNA"
            self.post(d, f"{lender} PAYMENT", amount, category="Loan Payment")
            self.spend_ledger.append((d, amount, f"{lender} PAYMENT", "Loan Payment"))
            return amount
        self.post(d, name, amount, category=event.get("category") or "Bills & Utilities")
        self.spend_ledger.append((d, amount, name, event.get("category") or "Bills & Utilities"))
        self.bill_ledger.append((d, amount))
        if name == "Cedar Auto Payment":
            interest = round(self.loan.balance * self.loan.apr / 1200.0, 2)
            self.loan.balance = round(self.loan.balance + interest - amount, 2)
            self.s.add(self.loan)
        return amount


def nightly(session: Session, world: World, d: date) -> None:
    if world.h.detect:
        run_detection(session, d)
    sync_borrowing(session, d)
    match_payments(session, d)
    session.commit()
    bump_version()


def plan_of(session: Session, d: date) -> dict:
    return finance_snapshot(session, d)


def projected(snap: dict, x: date) -> float | None:
    for p in snap["projection"]:
        if p["date"] == x.isoformat():
            return p["cash"]
    return None


def line_reconciles(sim: Sim, snap: dict, d: date) -> None:
    """Today's balance plus the events equals the line, each day of it: the calendar is the plan's list of events."""
    by_day: dict[str, float] = defaultdict(float)
    for e in snap["calendar"]["events"]:
        if not e.get("via_card"):
            by_day[e["date"]] += float(e["amount"])  # a paycheck is listed negative
    balance = snap["cash"]
    for p in snap["projection"]:
        balance = round(balance - by_day.get(p["date"], 0.0), 2)
        sim.check("line", abs(balance - p["cash"]) < CENT, f"{d}: on {p['date']} the line holds {p['cash']}, balance and events make {balance}")


def windows_chain(sim: Sim, world: World, snap: dict, d: date) -> None:
    w = snap["windows"]
    for a, b in zip(w, w[1:]):
        sim.check("windows", date.fromisoformat(a["end"]) + timedelta(days=1) == date.fromisoformat(b["start"]), f"{d}: window ending {a['end']} then one starting {b['start']}")
    for one in w:
        expected = sum(world.checks_on(date.fromisoformat(one["start"])).values())
        sim.check("windows", abs(one["paycheck"] - expected) < CENT, f"{d}: the check of {one['start']} is {one['paycheck']}, the schedule says {expected}")
    days = [x["date"] for x in snap["paydays"]]
    sim.check("windows", days == sorted(days) and days[0] == snap["next_paycheck"]["date"], f"{d}: paydays {days}, next {snap['next_paycheck']['date']}")


def rollups(sim: Sim, world: World, session: Session, d: date) -> None:
    """Month, quarter and year add up to one another and to the ledger, whichever way the rows are cut."""
    def ledger(start: date, end: date) -> float:
        return round(sum(a for when, a, _m, _c in world.spend_ledger if start <= when <= end), 2)

    year = run_report(session, d, period="year")
    quarter = run_report(session, d, period="quarter")
    month = run_report(session, d, period="month")
    jan = d.replace(month=1, day=1)
    q = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
    for name, r, start in (("year", year, jan), ("quarter", quarter, q), ("month", month, d.replace(day=1))):
        sim.check("rollup", abs(r["total"] - ledger(start, d)) < CENT, f"{d}: the {name} says {r['total']}, the ledger {ledger(start, d)}")
        sim.check("rollup", abs(sum(r["month_totals"]) - r["total"]) < CENT, f"{d}: the {name}'s months add to {sum(r['month_totals'])}, its total is {r['total']}")
        sim.check("rollup", abs(sum(i["total"] for i in r["items"]) - r["total"]) < 0.05, f"{d}: the {name}'s rows add to {sum(i['total'] for i in r['items'])}, its total is {r['total']}")
    by_month = dict(zip(year["months"], year["month_totals"]))
    for m, v in zip(quarter["months"], quarter["month_totals"]):
        sim.check("rollup", abs(by_month.get(m, -1) - v) < CENT, f"{d}: {m} is {v} in the quarter, {by_month.get(m)} in the year")
    for rows in ("merchant", "account"):
        other = run_report(session, d, rows=rows, period="year")
        sim.check("rollup", abs(other["total"] - year["total"]) < CENT, f"{d}: the year by {rows} is {other['total']}, by category {year['total']}")
    review = year_in_review(session, d)
    sim.check("rollup", abs(review["spent"] - year["total"]) < CENT, f"{d}: the year in review says {review['spent']}, the report {year['total']}")
    income = round(sum(a for when, a in world.income_ledger if jan <= when <= d), 2)
    sim.check("rollup", abs(review["income"] - income) < CENT, f"{d}: the year in review has {review['income']} in, the ledger {income}")
    summary = spending_summary(session, d, months=14)
    for row in summary["months"]:
        y, m = int(row["month"][:4]), int(row["month"][5:])
        start, end = date(y, m, 1), min(d, date(y, m, last_day(y, m)))
        sim.check("rollup", abs(row["spent"] - ledger(start, end)) < CENT, f"{d}: spending summary {row['month']} is {row['spent']}, the ledger {ledger(start, end)}")


def weekly_views(sim: Sim, world: World, session: Session, snap: dict, d: date) -> None:
    views = paycheck_views(session, d, snap=snap)
    hist = views["history"]
    for a, b in zip(hist, hist[1:]):
        sim.check("history", date.fromisoformat(a["end"]) + timedelta(days=1) == date.fromisoformat(b["start"]), f"{d}: history window ends {a['end']}, the next starts {b['start']}")
    if hist:
        start, end = date.fromisoformat(hist[0]["start"]), date.fromisoformat(hist[-1]["end"])
        landed = round(sum(a for when, a in world.income_ledger if start <= when <= end), 2)
        sim.check("history", abs(sum(h["paycheck"] for h in hist) - landed) < CENT, f"{d}: history {hist[0]['start']}..{hist[-1]['end']} has {sum(h['paycheck'] for h in hist)} in, the ledger {landed}")
        if not world.h.saturday_check_posts_friday:  # a check that posts the day before its payday sits in the window before; the sum above still holds
            sim.check("history", end >= d - timedelta(days=1), f"{d}: the last check in the history ends {end}")
    for m in views["months"]:
        start, end = date.fromisoformat(m["from"]), date.fromisoformat(m["to"])
        checks = round(sum(sum(world.checks_on(x).values()) for x in world.paydays(start, end)), 2)
        sim.check("months", abs(m["income"] - checks) < CENT, f"{d}: {m['month']} brings {m['income']}, the schedule says {checks}")
        expected = world.obligations(start, end) + (sum(e["amount"] for e in snap["calendar"]["events"] if e.get("late")) if m["from"] == d.isoformat() else 0.0)  # a bill due on the weekend, posting today
        if end <= END and d < date(2029, 1, 1):  # the registers run on past the end of the run; the seed's list does not
            sim.check("months", abs(m["obligations"] - expected) < CENT, f"{d}: {m['month']} ({m['from']}..{m['to']}) owes {m['obligations']}, the seed says {expected}")


def run(h: Household, *, start: date = START, end: date = END) -> Sim:
    sim = Sim()
    SQLModel.metadata.drop_all(engine)  # each household on an empty database, whichever test runs it first
    init_db()
    bump_version()
    with Session(engine) as session:
        world = World(session, h)
        yesterday_evening: dict | None = None
        yesterday_morning: dict | None = None
        yesterday_cash_spent = 0.0
        last_left: int | None = None
        status: dict[tuple[int, date], str] = {}
        owed_before: dict[str, float] = {}
        days = (end - start).days
        for i in range(days + 1):
            d = start + timedelta(days=i)
            sim.days += 1
            nightly(session, world, d)
            morning = plan_of(session, d)
            line_reconciles(sim, morning, d)
            windows_chain(sim, world, morning, d)

            # A payday is never in the past, and is the one the seed's schedule says.
            nxt = date.fromisoformat(morning["next_paycheck"]["date"])
            sim.check("payday", nxt >= d, f"{d}: the next payday is {nxt}")
            model = world.next_unlanded(d)
            # What the plan says today holds is what the seed says today holds; a bill due on the weekend waits, marked late.
            today_events = [e for e in morning["calendar"]["events"] if e["date"] == d.isoformat() and not e.get("via_card")]
            owed_today = round(sum(float(e["amount"]) for e in today_events if e.get("kind") != "paycheck" and not e.get("late")), 2)
            sim.check("calendar", abs(owed_today - world.obligations(d, d)) < CENT or d >= date(2029, 1, 1), f"{d}: the plan holds {owed_today} today, the seed says {world.obligations(d, d)}: {[(e['name'], e['amount']) for e in today_events]}")
            checks_today = {n: v for n, v in world.checks_on(d).items() if (n, d) not in world.landed}
            landing = round(-sum(float(e["amount"]) for e in today_events if e.get("kind") == "paycheck"), 2)
            sim.check("calendar", abs(landing - sum(checks_today.values())) < CENT, f"{d}: the plan lands {landing} today, the seed says {checks_today}")
            sim.check("payday", nxt == model, f"{d}: the next payday is {nxt}, the schedule says {model}")

            # Nothing happened overnight: the plan is what it was last evening.
            if yesterday_evening is not None:
                a, b = yesterday_evening["safe_to_spend"]["amount"], morning["safe_to_spend"]["amount"]
                sim.check("safe_overnight", abs(a - b) < CENT, f"{d}: safe to spend was {a} last evening and is {b} this morning; last evening's next payday {yesterday_evening['next_paycheck']['date']}, today's {morning['next_paycheck']['date']}")
                sim.check("cash_overnight", abs(yesterday_evening["cash"] - morning["cash"]) < CENT, f"{d}: cash moved overnight")
            # What is forecast for a day does not change because a day passed, but for what was spent yesterday.
            if yesterday_morning is not None:
                for k in range(5 if world.h.bills_paid_early else 1, 31):  # a bill paid early moves the money only before its own day
                    x = d + timedelta(days=k)
                    p0, p1 = projected(yesterday_morning, x), projected(morning, x)
                    if p0 is None or p1 is None:
                        continue
                    sim.check("forecast", abs(p0 - yesterday_cash_spent - p1) < CENT, f"{d}: {x} was forecast at {p0} and is forecast at {p1}, with {yesterday_cash_spent} spent yesterday")

            # A goal's checks only ever count down, and only when a check lands.
            goal = next(g for g in morning["goals"]["items"] if g["name"] == "Sailing trip")
            target = date(2028, 12, 20)
            to_come = [x for x in world.paydays(d, target) if any((n, x) not in world.landed for n in world.checks_on(x))] if d <= target else []
            sim.check("goal", goal["paychecks_left"] == len(to_come), f"{d}: the goal has {goal['paychecks_left']} checks left, the schedule {len(to_come)}")
            if last_left is not None:
                sim.check("goal", goal["paychecks_left"] <= last_left, f"{d}: the goal has {goal['paychecks_left']} checks left, it had {last_left}")
            last_left = goal["paychecks_left"]

            # The budget's span: from the last payday to the day before the next.
            span = budget_plan(session, d)["span"]
            sim.check("budget", span["end"] == (nxt - timedelta(days=1)).isoformat(), f"{d}: the span ends {span['end']}, the next payday is {nxt}")
            budget = budget_plan(session, d)
            came = world.last_landed(d)
            if came is not None:
                in_span = round(sum(a for when, a in world.groceries if date.fromisoformat(span["start"]) <= when <= d), 2)
                row = next(i for i in budget["items"] if i["category"] == "Groceries")
                sim.check("budget", abs(row["spent"] - in_span) < CENT, f"{d}: {row['spent']} on Groceries in the span from {span['start']}, the ledger {in_span}")
                sim.check("budget", 1 <= budget["span"]["days_total"] <= 31, f"{d}: a span of {budget['span']['days_total']} days")
            sim.check("budget", came is None or span["start"] == came.isoformat(), f"{d}: the span starts {span['start']}, the last payday was {came}")

            if d.weekday() == 0:
                weekly_views(sim, world, session, morning, d)
                fund = next((i for i in sinking_funds(session, d)["items"] if i["name"] == "Tidewater Utility"), None)
                if fund is not None:
                    due = date.fromisoformat(fund["due"])
                    sim.check("sinking", due >= d, f"{d}: the fund's bill is due {due}")
                    checks = [x for x in world.paydays(d, due - timedelta(days=1)) if any((n, x) not in world.landed for n in world.checks_on(x))]
                    sim.check("sinking", fund["paychecks_left"] == len(checks), f"{d}: {fund['paychecks_left']} checks before the {due} bill, the schedule has {len(checks)}")
            record_plan(session, d)
            session.commit()
            bump_version()

            # The day's events, as the plan predicted them.
            for e in morning["calendar"]["events"]:
                if e["date"] != d.isoformat() or e.get("via_card"):
                    continue
                if world.h.weekend_bills_post_monday and d.weekday() >= 5 and e.get("kind") == "bill":
                    continue
                world.settle(d, e)
            if world.h.bills_paid_early:
                ahead = d + timedelta(days=3)
                for e in morning["calendar"]["events"]:
                    if e["date"] == ahead.isoformat() and e.get("kind") in ("bill", "subscription") and not e.get("via_card"):
                        world.settle(d, e)
            landed_early = False
            if world.h.saturday_check_posts_friday and d.weekday() == 4:
                for payroll, net in world.checks_on(d + timedelta(days=1)).items():
                    world.deposit(d, payroll, net, d + timedelta(days=1))
                    landed_early = True
            spent = world.everyday(d)
            session.commit()
            nightly(session, world, d)
            evening = plan_of(session, d)
            if not world.checks_on(d) and not landed_early:
                a, b = morning["safe_to_spend"]["amount"], evening["safe_to_spend"]["amount"]
                sim.check("safe_intraday", abs(a - spent - b) < CENT, f"{d}: safe to spend was {a} this morning and is {b} tonight, with {spent} of everyday spending; {[ (e['name'], e['date']) for e in morning['calendar']['events'] if e['date'] == d.isoformat()]}")
                sim.check("safe_intraday", abs(evening["cash"] - (morning["projection"][0]["cash"] - spent)) < CENT or world.h.weekend_bills_post_monday, f"{d}: tonight's cash {evening['cash']}, the plan said {morning['projection'][0]['cash']} less {spent}")
            sim.check("payday", date.fromisoformat(evening["next_paycheck"]["date"]) > d, f"{d} evening: next payday {evening['next_paycheck']['date']}")

            # Bill periods.
            rows = session.exec(select(Payment)).all()
            seen: set[tuple[int, date]] = set()
            for p in rows:
                key = (p.bill_id, p.due_date)
                sim.check("periods", key not in seen, f"{d}: two rows for bill {p.bill_id} due {p.due_date}")
                seen.add(key)
                before = status.get(key)
                sim.check("periods", not (before == "paid" and p.status != "paid"), f"{d}: bill {p.bill_id} due {p.due_date} was paid and is now {p.status}")
                sim.check("periods", p.status != "past_due" or d >= p.due_date, f"{d}: bill {p.bill_id} due {p.due_date} is past due before its day")
                sim.check("periods", p.status != "past_due", f"{d}: bill {p.bill_id} due {p.due_date} is past due, every bill was paid in time")
                if not world.h.weekend_bills_post_monday:
                    sim.check("periods", p.status != "late", f"{d}: bill {p.bill_id} due {p.due_date} is late, every bill was paid on its day")
                status[key] = p.status

            # Pay-later plans: exactly the schedule, and what they owe only falls.
            scheduled = session.get(InstallmentPlan, world.plan_s.id)
            session.refresh(scheduled)
            paid_s = sum(1 for s in world.sched if s["date"] <= d.isoformat())
            sim.check("plans", scheduled.remaining == 12 - paid_s, f"{d}: the schedule leaves {12 - paid_s} payments, the plan {scheduled.remaining}")
            sim.check("plans", scheduled.active == (paid_s < 12), f"{d}: {paid_s} of 12 paid, active is {scheduled.active}")
            cadence_plan = session.get(InstallmentPlan, world.plan_c.id)
            session.refresh(cadence_plan)
            paid_c = sum(1 for x in world.plan_c_dates if x <= d)
            sim.check("plans", cadence_plan.remaining == 6 - paid_c, f"{d}: {paid_c} of 6 paid, the plan has {cadence_plan.remaining} left")
            everything = owed(session, d)["items"]
            for item in everything:
                source, balance = item.get("source"), float(item.get("balance") or 0.0)
                kept = owed_before.get(source)
                if source != f"debt:{world.card_debt.id}" and kept is not None:  # a card rises with its charges, not with a payment
                    sim.check("owed", balance <= kept + CENT, f"{d}: {item['name']} owed {kept} and owes {balance}")
                owed_before[source] = balance
                if source == f"plan:{world.plan_s.id}":
                    sim.check("owed", abs(balance - round(sum(x["amount"] for x in world.sched if x["date"] > d.isoformat()), 2)) < CENT, f"{d}: the plan owes {balance}")
                if source == f"plan:{world.plan_c.id}":
                    sim.check("owed", abs(balance - 40.0 * (6 - paid_c)) < CENT, f"{d}: the plan owes {balance}, {6 - paid_c} payments left")
            for item in everything:
                if item.get("source") == f"plan:{world.plan_s.id}":
                    sim.check("plan_ends", item["ends"] == world.sched[-1]["date"], f"{d}: Debts says the plan ends {item['ends']}, the schedule {world.sched[-1]['date']}")
                if item.get("source") == f"plan:{world.plan_c.id}":
                    sim.check("plan_ends", item["ends"] == world.plan_c_dates[-1].isoformat(), f"{d}: Debts says the plan ends {item['ends']}, its dates {world.plan_c_dates[-1]}")
            if d.day in (1, 16):
                over = borrowing_overview(session, d)
                for row in [*over.get("plans", []), *over.get("schedules", [])]:
                    if row.get("source") == f"plan:{world.plan_s.id}" and row.get("ends"):
                        sim.check("plan_ends", row["ends"] == world.sched[-1]["date"], f"{d}: Borrowing says the plan ends {row['ends']}")
            if d.weekday() == 6 or d.day == 1 or d.day == last_day(d.year, d.month):
                rollups(sim, world, session, d)
            if d.day == 2:
                rc = recap(session, d)
                for w in rc["windows"]:
                    plan = w["plan"]
                    s, e = date.fromisoformat(plan["start"]), min(date.fromisoformat(plan["end"]), d)
                    came = round(sum(a for when, a in world.income_ledger if s <= when <= e), 2)
                    sim.check("recap", abs(w["came_in"] - came) < CENT, f"{d}: the recap of {plan['start']} has {w['came_in']} in, the ledger {came}")
                    sim.check("recap", abs(plan["paycheck"] - sum(world.checks_on(s).values())) < CENT, f"{d}: the kept plan of {plan['start']} has a check of {plan['paycheck']}")
                    bills = round(sum(a for when, a in world.bill_ledger if s <= when <= e), 2)
                    sim.check("recap", abs(w["bills_paid"] - bills) < CENT, f"{d}: the recap of {plan['start']} has {w['bills_paid']} of bills, the ledger {bills}")

            yesterday_evening, yesterday_morning, yesterday_cash_spent = evening, morning, spent

        # Across the whole run: each period has one row and it is paid; none on a day the seed does not have.
        last = end - timedelta(days=1)
        rows = session.exec(select(Payment)).all()
        for bill, dates in world.bills.items():
            mine = sorted(p.due_date for p in rows if p.bill_id == bill)
            wanted = [x for x in dates if start <= x <= last]
            sim.check("periods_all", [x for x in mine if x <= last] == wanted, f"bill {bill}: rows on {mine}, the seed says {wanted}")
            sim.check("periods_all", all(p.status == "paid" for p in rows if p.bill_id == bill and p.due_date <= last), f"bill {bill}: {[(p.due_date, p.status) for p in rows if p.bill_id == bill and p.status != 'paid' and p.due_date <= last]}")
    return sim


@lru_cache(maxsize=None)
def scenario(name: str) -> Sim:
    """Each household once; the tests read what its run saw."""
    return run({
        "two_checks": Household(),
        "weekends": Household(biweekly_from=date(2027, 11, 6), saturday_check_posts_friday=True, weekend_bills_post_monday=True, semi_days="1,15", detect=True),
        "month_end_check": Household(second_income=False, monthly_only=True),
        "plain_deposits": Household(plain_deposits=True, biweekly_net=1500.0, semi_net=1400.0, semi_end=1450.0),
        "paid_early": Household(bills_paid_early=True),
        "card_across_checks": Household(card_no_minimum=True),
    }[name])


SCENARIOS = ("two_checks", "weekends", "month_end_check", "plain_deposits")
ALL = (*SCENARIOS, "paid_early", "card_across_checks")


def test_bills_paid_three_days_early_stay_paid_and_are_not_held_again():
    scenario("paid_early").none("line", "windows", "payday", "periods", "periods_all", "rollup", "plans", "plan_ends", "owed", "goal", "budget", "sinking", "forecast", "safe_overnight", "cash_overnight")


def test_a_card_paid_across_the_checks_keeps_the_line_and_the_books():
    scenario("card_across_checks").none("line", "windows", "payday", "periods", "periods_all", "rollup", "plans", "plan_ends", "owed", "goal", "budget", "sinking", "history", "recap")


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_line_is_the_balance_plus_the_events_every_day(name):
    scenario(name).none("line", "windows", "cash_overnight", "calendar")


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_payday_is_the_schedules_and_never_in_the_past(name):
    scenario(name).none("payday")


@pytest.mark.parametrize("name", SCENARIOS)
def test_safe_to_spend_moves_only_with_an_event(name):
    scenario(name).none("safe_overnight", "safe_intraday")


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_forecast_does_not_change_because_a_day_passed(name):
    scenario(name).none("forecast")


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_period_comes_once_and_stays_paid(name):
    scenario(name).none("periods", "periods_all")


@pytest.mark.parametrize("name", SCENARIOS)
def test_reports_add_up_and_match_the_ledger(name):
    scenario(name).none("rollup", "history", "months", "recap")


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_plan_ends_when_its_schedule_says(name):
    scenario(name).none("plans", "plan_ends", "owed")


@pytest.mark.parametrize("name", SCENARIOS)
def test_goals_budgets_and_funds_count_the_checks_that_come(name):
    scenario(name).none("goal", "budget", "sinking")


def _two_plans(session: Session) -> tuple[InstallmentPlan, InstallmentPlan, Account]:
    a_dates = month_dates(5, date(2028, 1, 5), date(2028, 12, 5))
    b_dates = month_dates(5, date(2028, 6, 5), date(2028, 11, 5))
    account = Account(name="Oak Checking", kind="checking", balance=9000.0)
    session.add(account)
    session.flush()
    a = InstallmentPlan(provider="Affirm", merchant="Summit Bikes", total=600.0, installment=50.0, count=12, remaining=12, next_date=a_dates[0], cadence_days=30, schedule=json.dumps([{"date": d.isoformat(), "amount": 50.0} for d in a_dates]))
    b = InstallmentPlan(provider="Affirm", merchant="Vale Sofa", total=300.0, installment=50.0, count=6, remaining=6, next_date=b_dates[0], cadence_days=30, schedule=json.dumps([{"date": d.isoformat(), "amount": 50.0} for d in b_dates]))
    session.add_all([a, b])
    session.commit()
    return a, b, account


def _paid_on(session: Session, account: Account, days: list[date]) -> None:
    for i, d in enumerate(days):
        session.add(Transaction(account_id=account.id, date=d, merchant="AFFIRM PAYMENT", raw_name="AFFIRM PAYMENT", amount=50.0, category="Loan Payment", source="csv", row_hash=f"two-{d}-{i}"))
    session.commit()


def test_two_plans_of_one_lender_at_one_amount_each_take_their_own_payments():
    """Two loans at the same installment overlap from June: each counted every payment of that amount, so both ran ahead
    of their schedules and the forecast dropped payments still to come. Every day of the year, each keeps its own count."""
    with Session(engine) as session:
        a, b, account = _two_plans(session)
        a_dates, b_dates = month_dates(5, date(2028, 1, 5), date(2028, 12, 5)), month_dates(5, date(2028, 6, 5), date(2028, 11, 5))
        day = date(2028, 1, 1)
        while day <= date(2028, 12, 31):
            _paid_on(session, account, [d for d in [*a_dates, *b_dates] if d == day])
            refresh_plan_progress(session, day)
            session.refresh(a)
            session.refresh(b)
            assert (a.remaining, b.remaining) == (12 - sum(d <= day for d in a_dates), 6 - sum(d <= day for d in b_dates)), day
            assert (a.active, b.active) == (a.remaining > 0, b.remaining > 0), day
            day += timedelta(days=1)


def test_a_payment_made_ahead_of_its_date_still_counts_with_a_second_plan_at_that_amount():
    """A plan's payment ahead of its date is still that payment, as a lone plan has it, when no other plan's date claims it."""
    with Session(engine) as session:
        a, b, account = _two_plans(session)
        _paid_on(session, account, [date(2028, 1, 5), date(2028, 2, 5), date(2028, 3, 5), date(2028, 3, 20)])
        refresh_plan_progress(session, date(2028, 3, 25))
        session.refresh(a)
        session.refresh(b)
        assert (a.remaining, b.remaining) == (12 - 4, 6)  # Jan, Feb, Mar and April's paid ahead


def test_a_notice_due_feb_29_is_read_in_a_leap_year():
    """A date with no year was parsed in 1900, which has no Feb 29: a bill notice "due Feb 29" lost its date."""
    assert parse_date_text("Feb 29", date(2028, 2, 10)) == date(2028, 2, 29)
    assert parse_date_text("February 29th", date(2028, 2, 10)) == date(2028, 2, 29)
    assert parse_date_text("Feb 29", date(2027, 12, 20)) == date(2028, 2, 29)  # the year turns between the mail and the day
    assert parse_date_text("Feb 30", date(2028, 2, 10)) is None
    assert parse_date_text("Jan 5", date(2027, 12, 20)) == date(2028, 1, 5)
    assert parse_date_text("Mar 3", date(2028, 2, 29)) == date(2028, 3, 3)


CHICAGO = ZoneInfo("America/Chicago")
STATEMENT = services.apply_statement_balances


def test_a_stamp_is_on_the_owners_day_across_both_clock_changes(monkeypatch):
    monkeypatch.setattr("app.clock.zone", lambda: CHICAGO)
    assert local_date(datetime(2028, 3, 12, 5, 30)) == date(2028, 3, 11)  # 11:30 pm CST, before the clocks go forward
    assert local_date(datetime(2028, 3, 13, 4, 30)) == date(2028, 3, 12)  # 11:30 pm CDT, after
    assert local_date(datetime(2028, 11, 5, 4, 30)) == date(2028, 11, 4)  # 11:30 pm CDT, the night they go back
    assert local_date(datetime(2028, 11, 6, 5, 30)) == date(2028, 11, 5)  # 11:30 pm CST


def _statement(monkeypatch, on: date, amount: float) -> None:
    monkeypatch.setattr("app.parsers.ofx_balance.statement_balances", lambda _t: [{"account": ""}])
    monkeypatch.setattr("app.parsers.ofx_balance.balance_to_count", lambda _f: (amount, on))


def test_an_evening_balance_is_older_than_the_next_days_statement(monkeypatch):
    """A balance typed at 9 pm in Chicago is stamped the next day in UTC: by the stamp's own date it read as new as a
    statement that closed that next day, and the statement's figure was dropped."""
    monkeypatch.setattr("app.clock.zone", lambda: CHICAGO)
    _statement(monkeypatch, date(2028, 11, 6), 250.0)
    with Session(engine) as session:
        account = Account(name="Oak Checking", kind="checking", balance=100.0, balance_at=datetime(2028, 11, 6, 3, 0, tzinfo=timezone.utc))  # Nov 5, 9 pm in Chicago
        session.add(account)
        session.commit()
        assert STATEMENT(session, "file", account.id)
        session.refresh(account)
        assert account.balance == 250.0
        assert local_date(account.balance_at) == date(2028, 11, 6)  # the statement's own day on the owner's clock, for its age
        assert STATEMENT(session, "file", account.id) == []  # the same file again changes nothing


def test_a_balance_read_on_an_evening_ages_by_the_owners_day(monkeypatch):
    monkeypatch.setattr("app.clock.zone", lambda: CHICAGO)
    with Session(engine) as session:
        session.add(Account(name="Oak Checking", kind="checking", balance=100.0, balance_at=datetime(2028, 11, 7, 3, 0, tzinfo=timezone.utc)))  # Nov 6, 9 pm in Chicago
        session.commit()
        assert net_worth(session, date(2028, 11, 9))["stale"] == []  # three days
        assert net_worth(session, date(2028, 11, 10))["stale"] == ["Oak Checking"]  # four, though the stamp's own date is the 7th


EDGES = (date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1), date(2028, 3, 12), date(2028, 11, 5), date(2028, 12, 31), date(2029, 1, 1), date(2029, 2, 28))


def test_every_view_of_the_engine_runs_on_the_calendars_edges():
    """Each read of the engine that takes (session, today) and nothing else, on the days a date computation breaks."""
    views = []
    for info in pkgutil.iter_modules(engine_package.__path__):
        module = importlib.import_module(f"app.engine.{info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__ or name.startswith("_"):
                continue
            required = [p.name for p in inspect.signature(fn).parameters.values() if p.default is p.empty]
            if required == ["session", "today"]:
                views.append((f"{info.name}.{name}", fn))
    assert len(views) >= 30
    SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as session:
        world = World(session, Household())
        world.post(date(2028, 2, 25), "Greenway Market", 85.4, category="Groceries")
        session.commit()
        for day in EDGES:
            bump_version()
            for label, fn in views:
                try:
                    fn(session, day)
                except Exception as e:  # noqa: BLE001
                    raise AssertionError(f"{label} on {day}: {e!r}") from e
                session.rollback()


def test_the_run_covers_what_it_says():
    """Fourteen months and more, through each edge, on every household."""
    for name in ALL:
        sim = scenario(name)
        assert sim.days >= 425 and sim.checks > 100_000
    assert START <= date(2028, 2, 29) <= END and START <= date(2028, 3, 12) <= END and START <= date(2028, 11, 5) <= END and START <= date(2028, 12, 31) <= END


def test_a_cards_minimum_paid_since_its_statement_is_not_held_back_again():
    """Paid five days early, the minimum left checking and was still held until its due day had gone: safe to spend was
    short by it all that time and came back with no event. A payment before the statement closed is last month's."""
    with Session(engine) as session:
        world = World(session, Household())
        world.post(date(2028, 5, 2), "Harbor Card Payment", 60.0, is_card_payment=True, category="Credit Card Payment")
        session.commit()
        bump_version()
        names = lambda snap, d: [e["name"] for e in snap["calendar"]["events"] if e["date"] == d]  # noqa: E731
        assert "Harbor Card minimum" in names(plan_of(session, date(2028, 5, 20)), "2028-05-28")  # May 2 is before the May 3 statement
        world.post(date(2028, 5, 23), "Harbor Card Payment", 60.0, is_card_payment=True, category="Credit Card Payment")
        session.commit()
        bump_version()
        snap = plan_of(session, date(2028, 5, 23))
        assert "Harbor Card minimum" not in names(snap, "2028-05-28")
        assert "Harbor Card minimum" in names(snap, "2028-06-28")  # the next month's is still owed


def test_a_part_of_the_minimum_does_not_pay_it():
    with Session(engine) as session:
        world = World(session, Household())
        world.post(date(2028, 5, 23), "Harbor Card Payment", 20.0, is_card_payment=True, category="Credit Card Payment")
        session.commit()
        bump_version()
        assert any(e["name"] == "Harbor Card minimum" and e["date"] == "2028-05-28" for e in plan_of(session, date(2028, 5, 23))["calendar"]["events"])


def test_a_new_loan_after_a_finished_plan_is_still_a_stream():
    """A plan's own last payments are not a new stream; a payment past its end, at the same amount, is."""
    with Session(engine) as session:
        account = Account(name="Oak Checking", kind="checking", balance=900.0)
        session.add(account)
        session.flush()
        session.add(InstallmentPlan(provider="Klarna", merchant="Vale Sofa", total=120.0, installment=40.0, count=3, remaining=0, next_date=date(2028, 4, 15), cadence_days=14, active=False))
        for i, d in enumerate((date(2028, 3, 18), date(2028, 4, 1))):
            session.add(Transaction(account_id=account.id, date=d, merchant="KLARNA PAYMENT", raw_name="KLARNA PAYMENT", amount=40.0, category="Loan Payment", source="csv", row_hash=f"k-{i}"))
        session.commit()
        assert sync_borrowing(session, date(2028, 4, 3))["streams_queued"] == 0
        session.add(Transaction(account_id=account.id, date=date(2028, 4, 29), merchant="KLARNA PAYMENT", raw_name="KLARNA PAYMENT", amount=40.0, category="Loan Payment", source="csv", row_hash="k-new"))
        session.commit()
        assert sync_borrowing(session, date(2028, 5, 1))["streams_queued"] == 1


def test_the_ladder_counts_every_check_and_does_not_change_with_the_next_ones_cadence():
    """What a finished plan frees per check was worked out on whichever paycheck came next, so it changed each payday."""
    with Session(engine) as session:
        World(session, Household())
        seen = set()
        for d in (date(2027, 11, 3), date(2027, 11, 10), date(2027, 11, 17), date(2027, 11, 24)):
            bump_version()
            seen.add(tuple(r["per_check"] for r in saving_plan(session, d)["rollover"]["items"]))
        assert len(seen) == 1, seen
        first = next(iter(seen))
        assert first == (round(40.0 * (365 / 50) / 14, 2), round(52.18 * (365 / 50) / 30, 2))  # by last payment, the sooner first
