"""Sweep 13: time travel. The simulated quarter of sweep 8 run through the winter (2026-10-20 to 2027-01-05), so
the days cross the fall of daylight saving time (Nov 1, with the owner in America/Chicago), Thanksgiving and
Christmas (a check and a bill that would land on a bank holiday post the business day before), and the year end
(the tax year rolls, the week recap and the month report at Dec 31); and a short run across the leap day of 2028
(a bill due on the 29th, a monthly paycheck on the 31st, a lease ending Feb 29). On the twentieth day the owner
changes the time zone, the buffer, the card payment mode, a bill's paying account, the paycheck's cadence and an
account's spendable flag, and every figure must follow the same day.

Then the clock itself: "today" on every route is the owner's day at half past eleven at night in Chicago (the next
day in UTC); the morning brief, the reminders and the Sunday recap go once per owner day across the change; the
scheduler's intervals are UTC; a payday on a weekend moves the way cadence.py documents (holidays are not modelled,
and a run shows what that means at the bank).

Synthetic data only."""

from __future__ import annotations

import datetime as datetime_module
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_sweep8_bugs_quarter import Quarter

from app import clock, notify
from app.api import routes
from app.db import engine
from app.engine.cadence import semimonthly_anchors
from app.engine.household import NET_PAY, generate, seed
from app.engine.recap import week_recap
from app.jobs import scheduler
from app.main import app
from app.models import Account, Bill, Income, Payment, Transaction
from app.store import set_setting

CHICAGO = ZoneInfo("America/Chicago")
RUN_START = date(2026, 10, 20)
RUN_END = date(2027, 1, 5)
CHANGE_DAY = RUN_START + timedelta(days=19)  # the twentieth day: 2026-11-08
BIWEEKLY = [date(2026, 10, 16) + timedelta(days=14 * k) for k in range(8)]
SEMI = [date(2026, 11, 13), date(2026, 12, 1), date(2026, 12, 15), date(2027, 1, 1), date(2027, 1, 15)]  # the 1st and the 15th, from the change on
CHRISTMAS, NEW_YEAR = date(2026, 12, 25), date(2027, 1, 1)
LEAP_START, LEAP_END = date(2028, 2, 20), date(2028, 3, 10)


def ledger_spending(session: Session, start: date, end: date) -> tuple[float, float]:
    rows = session.exec(select(Transaction).where(Transaction.date >= start, Transaction.date <= end)).all()
    return round(sum(t.amount for t in rows if t.is_spending and t.amount > 0), 2), round(sum(-t.amount for t in rows if t.is_income), 2)


# ---- the winter quarter ------------------------------------------------------------------------------------


class WinterQuarter(Quarter):
    def __init__(self, owner: TestClient) -> None:
        super().__init__(owner)
        self.semimonthly = False
        # A bank holiday: the Christmas card payment and the New Year's rent post on the business day before (a
        # bank does not post on Dec 25 or Jan 1; the autopay goes out early).
        for merchant, on, to in (("Payment to Rewards Card", CHRISTMAS, date(2026, 12, 24)), ("Payment received", CHRISTMAS, date(2026, 12, 24)), ("Rent", NEW_YEAR, date(2027, 1, 4))):
            moved = [t for t in self.rows.get(on, []) if t["merchant"] == merchant]
            self.rows[on] = [t for t in self.rows.get(on, []) if t["merchant"] != merchant]
            for t in moved:
                t["date"] = to
                self.rows.setdefault(to, []).append(t)
        with Session(engine) as session:
            self.remember["card"] = session.exec(select(Account).where(Account.name == "Rewards Card")).one().id
            self.remember["income"] = session.exec(select(Income)).one().id
        # A savings account the owner spends from, until day twenty.
        made = owner.post("/api/v1/accounts", json={"name": "Example Savings", "kind": "savings", "institution": "Example Bank", "balance": 800.0})
        assert made.status_code == 200, made.text
        with Session(engine) as session:
            savings = session.exec(select(Account).where(Account.name == "Example Savings")).one()
            savings.spendable = True
            session.add(savings)
            session.commit()
            self.remember["savings"] = savings.id

    def expected_next_payday(self) -> date:
        """The next paycheck as the schedule and the feed run it: the deposit lands on the payday itself, so from
        payday on the next one is the following check; the New Year's check posts on Dec 31 and counts as landed."""
        d = self.day
        if not self.semimonthly:
            return next(p for p in BIWEEKLY if p > d)
        if d == date(2026, 12, 31):
            return date(2027, 1, 15)
        return next(p for p in SEMI if p > d)

    def season(self, got: dict[str, dict]) -> None:
        d = self.day
        snap = got.get("/api/v1/snapshot")
        if snap is None:
            return
        self.expect(snap["as_of"] == d.isoformat(), f"the snapshot is as of {snap['as_of']}")
        self.expect(snap["next_paycheck"]["date"] == self.expected_next_payday().isoformat(), f"next paycheck {snap['next_paycheck']['date']}, the schedule says {self.expected_next_payday()}")
        if d == NEW_YEAR:
            self.expect(snap["payday"]["today"] and snap["payday"]["landed"], f"New Year's Day is a payday whose check posted the day before: {snap['payday']}")
        paydays = [p["date"] for p in snap["paydays"]]
        self.expect(paydays == sorted(paydays) and all(p >= d.isoformat() for p in paydays), f"the paydays {paydays} on {d}")
        if self.semimonthly:
            self.expect(snap["next_paycheck"]["cadence"] == "semimonthly" and snap["next_paycheck"]["pay_days"] == "1,15", f"the paycheck after the change: {snap['next_paycheck']}")
        with Session(engine) as session:
            if d >= date(2026, 12, 26):
                card = session.exec(select(Bill).where(Bill.name == "Card minimum", Bill.active == True)).one()
                p = session.exec(select(Payment).where(Payment.bill_id == card.id, Payment.due_date == CHRISTMAS)).first()
                self.expect(p is not None and p.status == "paid" and p.paid_date == date(2026, 12, 24), f"the card minimum due on Christmas, paid the day before: {p}")
            if d >= date(2027, 1, 2):
                rent = session.exec(select(Bill).where(Bill.name == "Rent", Bill.active == True)).one()
                p = session.exec(select(Payment).where(Payment.bill_id == rent.id, Payment.due_date == NEW_YEAR)).first()
                if d < date(2027, 1, 4):
                    self.expect(p is not None and p.status == "late", f"the rent due on New Year's Day, before its row lands on the 4th: {p}")
                else:
                    self.expect(p is not None and p.status == "paid" and p.paid_date == date(2027, 1, 4), f"the rent due on New Year's Day, paid the first business day: {p}")
            if date(2026, 11, 1) <= d <= date(2026, 11, 7):
                week = week_recap(session, d)
                self.expect((date.fromisoformat(week["to"]) - date.fromisoformat(week["from"])).days == 6, f"the week recap across the time change runs {week['from']} to {week['to']}")
                spent, _ = ledger_spending(session, d - timedelta(days=6), d)
                self.expect(abs(week["spent"] - spent) <= 0.011, f"the week recap says {week['spent']} out, the ledger's seven days {spent}")
            if d == date(2026, 12, 31) or d == RUN_END:
                report = self.owner.get("/api/v1/reports/spending", params={"months": 1 if d.month == 12 else 2}).json()
                december = next(m for m in report["months"] if m["month"] == "2026-12")
                spent, income = ledger_spending(session, date(2026, 12, 1), date(2026, 12, 31))
                self.expect(abs(december["spent"] - spent) <= 0.011 and abs(december["income"] - income) <= 0.011, f"the month report's December {december} against its days {spent} out, {income} in")
                self.expect(december["partial"] == (d.month == 12), f"December is {'partial' if december['partial'] else 'full'} on {d}")
        if d >= date(2026, 12, 30):
            tax = self.owner.get("/api/v1/taxes")
            self.expect(tax.status_code == 200 and tax.json()["year"] == d.year, f"the tax center on {d} is for {tax.json().get('year')}")
            last = self.owner.get("/api/v1/taxes/2026")
            self.expect(last.status_code == 200 and last.json()["year"] == 2026, f"the 2026 tax center on {d} answered {last.status_code}")

    def change_settings(self, before: dict[str, dict], monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
        """The twentieth day: the owner changes six things; the same day's figures follow each."""
        owner = self.owner
        snap_before = before["/api/v1/snapshot"]
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        r = owner.put("/api/v1/settings/buffer", json={"value": 350.0})
        self.expect(r.status_code == 200 and r.json()["buffer"] == 350.0, f"the buffer: {r.status_code} {r.text[:120]}")
        r = owner.put("/api/v1/borrowing/settings", json={"cards_pay_from": "minimum"})
        self.expect(r.status_code == 200 and r.json()["settings"]["cards_pay_from"] == "minimum", f"the card mode: {r.status_code} {r.text[:120]}")
        with Session(engine) as session:
            streaming = session.exec(select(Bill).where(Bill.name == "Streaming", Bill.active == True)).one()
            self.remember["streaming"] = streaming.id
            savings = session.get(Account, self.remember["savings"])
            savings.spendable = False  # no route writes this flag yet; the owner's change lands on the row
            session.add(savings)
            session.commit()
        r = owner.put(f"/api/v1/bills/{self.remember['streaming']}", json={"pay_from_account_id": self.remember["card"]})
        self.expect(r.status_code == 200 and r.json()["pay_from_account_id"] == self.remember["card"], f"the bill's paying account: {r.status_code} {r.text[:120]}")
        r = owner.put(f"/api/v1/incomes/{self.remember['income']}", json={"cadence": "semimonthly", "pay_days": "1,15"})
        self.expect(r.status_code == 200, f"the paycheck's cadence: {r.status_code} {r.text[:120]}")
        self.semimonthly = True
        for day, rows in list(self.rows.items()):
            if day > self.day:
                self.rows[day] = [t for t in rows if t["merchant"] != "Example Employer Payroll"]
        for payday in SEMI:
            lands = date(2026, 12, 31) if payday == NEW_YEAR else payday  # the bank posts the holiday's check the business day before
            if lands > self.day:
                self.rows.setdefault(lands, []).append({"date": lands, "account": "checking", "merchant": "Example Employer Payroll", "amount": -NET_PAY, "category": "Income"})
        after = self.day_cycle()
        snap = after["/api/v1/snapshot"]
        parts, was = snap["safe_to_spend"]["parts"], snap_before["safe_to_spend"]["parts"]
        self.expect(snap["buffer"] == 350.0 and parts["buffer"] == 350.0, f"the buffer in the plan the same day: {snap['buffer']} {parts['buffer']}")
        self.expect(abs(was["cash"] - parts["cash"] - 800.0) <= 0.011, f"cash went from {was['cash']} to {parts['cash']} when savings stopped being spendable")
        self.expect(snap["next_paycheck"]["date"] == "2026-11-13" and snap["next_paycheck"]["cadence"] == "semimonthly", f"the next paycheck after the change: {snap['next_paycheck']}")
        self.expect(self.owner.get("/api/v1/borrowing").json()["settings"]["cards_pay_from"] == "minimum", "the card mode did not take")
        self.expect(self.owner.get("/api/v1/about").json()["tz"] == "America/Los_Angeles", "the time zone did not take")

        def streaming_items(s: dict) -> list[dict]:
            return [e for key in ("due_before_payday", "window", "set_aside") for e in s[key]["items"] if e["name"] == "Streaming"]

        self.expect(all(not e.get("via_card") for e in streaming_items(snap_before)), "Streaming rode the card before the change")
        self.expect(streaming_items(snap) and all(e.get("via_card") for e in streaming_items(snap)), f"Streaming after it is charged to the card: {streaming_items(snap)}")
        self.expect(any(e["name"] == "Streaming" for e in snap["on_card"]["items"]), f"Streaming is not on the card: {snap['on_card']}")
        for key in ("due_before_payday", "window"):
            own = sum(e["amount"] for e in snap[key]["items"] if not e.get("via_card"))
            self.expect(abs(snap[key]["total"] - own) <= 0.011, f"{key}'s total {snap[key]['total']} is not its own items {own} (a bill on the card counted twice?)")
        return after


@pytest.fixture
def winter(owner: TestClient, monkeypatch: pytest.MonkeyPatch) -> WinterQuarter:
    monkeypatch.setenv("TZ", "America/Chicago")
    q = WinterQuarter(owner)
    yield q
    from app.connectors import CONNECTORS
    from app.mail.imap import ImapClient

    CONNECTORS["plaid"].client_factory = None
    CONNECTORS["mail"].client_factory = ImapClient


def test_the_winter_keeps_every_invariant_across_the_time_change_the_holidays_and_the_year_end(winter: WinterQuarter, monkeypatch: pytest.MonkeyPatch):
    while winter.day < RUN_START - timedelta(days=1):  # the household's own ledger ends Oct 14; the run starts on the 20th
        winter.day += timedelta(days=1)
        routes.set_today_override(winter.day)
        winter.feed()
        winter.run_jobs()
    while winter.day < RUN_END:
        got = winter.advance()
        if winter.day == CHANGE_DAY:
            got = winter.change_settings(got, monkeypatch)
        winter.season(got)
        if winter.day in (CHANGE_DAY + timedelta(days=1), CHANGE_DAY + timedelta(days=2), date(2026, 11, 1), date(2026, 12, 31), NEW_YEAR):
            winter.same_again()
    assert not winter.findings, "\n".join(winter.findings)


# ---- the leap day ------------------------------------------------------------------------------------------


class LeapRun(Quarter):
    """Twenty days around Feb 29, 2028, with the rows written straight to the ledger (no bank link)."""

    def __init__(self, owner: TestClient) -> None:
        self.owner = owner
        self.day = LEAP_START - timedelta(days=1)
        self.uid = 0
        self.findings = []
        self.tx_floor = 0
        self.payments_prev = {}
        self.api_paths = {getattr(r, "path", "") for r in app.routes}
        self.remember = {}
        self.goal_funded = {}
        self.fed_today = False
        with Session(engine) as session:
            seed(session, generate(today=LEAP_START))
            ids = {a.external_id: a.id for a in session.exec(select(Account)).all()}
            self.remember["checking"] = ids["checking"]
            self.remember["income"] = session.exec(select(Income)).one().id
            rent = session.exec(select(Bill).where(Bill.name == "Rent")).one()
        routes.set_today_override(LEAP_START)
        # A monthly paycheck on the 31st (month end: Feb 29 this year), a bill due on the 29th, the lease ending Feb 29.
        r = owner.put(f"/api/v1/incomes/{self.remember['income']}", json={"cadence": "monthly", "next_date": "2028-01-31", "pay_day": 31})
        assert r.status_code == 200, r.text
        r = owner.post("/api/v1/bills", json={"name": "Example Gym Plus", "amount": 29.0, "due_day": 29, "cadence": "monthly", "category": "Health & Fitness", "autopay": True})
        assert r.status_code == 200, r.text
        self.remember["gym"] = r.json()["id"]
        r = owner.post("/api/v1/housing/adopt", json={"bill_id": rent.id, "name": "Example Court 4"})
        assert r.status_code == 200, r.text
        home = next(h for h in r.json()["homes"] if h["name"] == "Example Court 4")
        self.remember["home"], self.remember["rent"] = home["id"], rent.id
        r = owner.put(f"/api/v1/housing/{home['id']}", json={"name": "Example Court 4", "kind": "rent", "payee": "Example Property LLC", "starts_on": "2026-03-01", "ends_on": "2028-02-29", "lease_ends_on": "2028-02-29", "due_day": 1, "notice_days": 30, "pay_from_account_id": ids["checking"]})
        assert r.status_code == 200, r.text
        # The rows: the month's bills and everyday spending as the household generates them, the check on the 29th,
        # the gym on the 29th.
        self.rows: dict[date, list[dict]] = {}
        for t in generate(today=LEAP_END + timedelta(days=1), months=1).transactions:
            if t["date"] > LEAP_START and not t.get("is_income"):
                self.rows.setdefault(t["date"], []).append(dict(t))
        self.rows.setdefault(date(2028, 2, 29), []).extend([
            {"date": date(2028, 2, 29), "account": "checking", "merchant": "Example Employer Payroll", "amount": -NET_PAY, "category": "Income", "is_income": True},
            {"date": date(2028, 2, 29), "account": "checking", "merchant": "Example Gym Plus", "amount": 29.0, "category": "Health & Fitness"},
        ])

    def feed(self) -> None:
        from hashlib import sha256

        with Session(engine) as session:
            ids = {a.external_id: a.id for a in session.exec(select(Account)).all()}
            checking = session.get(Account, ids["checking"])
            for n, t in enumerate(self.rows.get(self.day, [])):
                session.add(Transaction(account_id=ids[t["account"]], date=t["date"], merchant=t["merchant"], raw_name=t["merchant"].upper(), amount=t["amount"], category=t["category"],
                                        is_income=t.get("is_income", False), is_card_payment=t.get("is_card_payment", False), source="csv", row_hash=sha256(f"leap|{self.day}|{n}|{t['merchant']}".encode()).hexdigest()))
                if t["account"] == "checking":
                    checking.balance = round((checking.balance or 0.0) - t["amount"], 2)
            checking.balance_at = datetime.now(UTC)
            session.add(checking)
            session.commit()
        self.fed_today = False

    def leap(self, got: dict[str, dict]) -> None:
        d = self.day
        snap = got.get("/api/v1/snapshot")
        if snap is None:
            return
        expected_payday = date(2028, 2, 29) if d < date(2028, 2, 29) else date(2028, 3, 31)
        self.expect(snap["next_paycheck"]["date"] == expected_payday.isoformat(), f"a monthly check on the 31st: next paycheck {snap['next_paycheck']['date']} on {d}, not {expected_payday}")
        if d == date(2028, 2, 29):
            self.expect(snap["payday"]["today"], f"Feb 29 is the payday: {snap['payday']}")
        events = snap["calendar"]["events"]
        gym = [e["date"] for e in events if e["name"] == "Example Gym Plus"]
        self.expect(gym and gym[0] == ("2028-02-29" if d < date(2028, 2, 29) else "2028-03-29"), f"the bill due on the 29th comes {gym} on {d}")
        rent = [e for e in events if e.get("source") == f"bill:{self.remember['rent']}" or e["name"].startswith("Rent")]
        self.expect(not rent, f"the rent after the lease ended Feb 29 is still coming: {[(e['name'], e['date']) for e in rent]}")
        homes = self.owner.get("/api/v1/housing").json()["homes"]
        mine = next(h for h in homes if h["id"] == self.remember["home"])
        self.expect(mine["state"] == ("current" if d < date(2028, 2, 29) else "ended"), f"the home is {mine['state']} on {d}")
        with Session(engine) as session:
            march = session.exec(select(Payment).where(Payment.bill_id == self.remember["rent"], Payment.due_date >= date(2028, 2, 29))).all()
            self.expect(not march, f"the ended lease still holds periods {[(p.due_date.isoformat(), p.status) for p in march]}")
            if d >= date(2028, 3, 1):
                p = session.exec(select(Payment).where(Payment.bill_id == self.remember["gym"], Payment.due_date == date(2028, 2, 29))).first()
                self.expect(p is not None and p.status == "paid" and p.paid_date == date(2028, 2, 29), f"the bill due Feb 29: {p}")


def test_the_leap_day_with_a_bill_on_the_29th_a_monthly_check_on_the_31st_and_a_lease_ending_feb_29(owner: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    run = LeapRun(owner)
    while run.day < LEAP_END:
        got = run.advance()
        run.leap(got)
        if run.day in (date(2028, 2, 29), date(2028, 3, 1)):
            run.same_again()
    # The brief names the current week by its Sunday ("the week of Feb 20" on the 23rd): the harness's date check
    # reads that as a past day. This household's cash is thin all month, so the line is there every day.
    findings = [f for f in run.findings if "the week of" not in f]
    assert not findings, "\n".join(findings)


# ---- the clock ---------------------------------------------------------------------------------------------


class Clock:
    """A clock the tests move: `now` is an aware UTC moment."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def datetime_class(self):
        clock = self

        class Moved(datetime):
            @classmethod
            def now(cls, tz=None):
                n = clock.now
                return n.astimezone(tz) if tz else n.astimezone().replace(tzinfo=None)

            @classmethod
            def utcnow(cls):
                return clock.now.replace(tzinfo=None)

        return Moved


def at_chicago(y: int, m: int, d: int, hour: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=CHICAGO).astimezone(UTC)


def test_today_on_every_route_is_the_owners_day_at_half_past_eleven(owner: TestClient, monkeypatch: pytest.MonkeyPatch):
    """At 23:30 in Chicago on Dec 31 it is Jan 1 in UTC: nothing the app answers may say Jan 1."""
    monkeypatch.setenv("TZ", "America/Chicago")
    moved = Clock(at_chicago(2026, 12, 31, 23, 30)).datetime_class()
    monkeypatch.setattr(clock, "datetime", moved)
    monkeypatch.setattr(scheduler, "datetime", moved)
    routes.set_today_override(None)
    with Session(engine) as session:
        seed(session)
    assert clock.local_today() == date(2026, 12, 31)
    assert scheduler._today() == date(2026, 12, 31)
    assert clock.local_date(datetime(2027, 1, 1, 5, 30)) == date(2026, 12, 31)  # a stored UTC moment, read back as the owner's day
    paths = sorted(p for p, ops in app.openapi()["paths"].items() if "get" in ops and "{" not in p and p.startswith("/api/v1/"))
    said_tomorrow: list[str] = []

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("as_of", "today") and v == "2027-01-01":
                    said_tomorrow.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    for path in paths:
        response = owner.get(path, params={"amount": 40, "bought_on": "2026-12-31", "day": 5, "q": "example", "rows": "category", "period": "month"})
        if response.status_code != 200 or not response.headers.get("content-type", "").startswith("application/json"):
            continue
        walk(response.json(), path)
    assert not said_tomorrow, said_tomorrow
    about = owner.get("/api/v1/about").json()
    assert about["today"] == "2026-12-31" and about["tz"] == "America/Chicago"
    assert owner.get("/api/v1/snapshot").json()["as_of"] == "2026-12-31"
    assert owner.get("/api/v1/taxes").json()["year"] == 2026


def test_the_brief_the_reminders_and_the_sunday_recap_go_once_per_owner_day_across_the_time_change(owner: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Oct 30 to Nov 2, 2026, Chicago; the clocks fall back on Sunday Nov 1 at 2 am. The morning brief job runs
    every ten minutes, the reminders every fifteen, the recap push every hour, as the scheduler runs them (its
    intervals are UTC): each goes exactly once per owner day, at the owner's hour, never twice on the long day."""
    monkeypatch.setenv("TZ", "America/Chicago")
    routes.set_today_override(None)
    ticking = Clock(at_chicago(2026, 10, 30, 0, 0))
    moved = ticking.datetime_class()
    monkeypatch.setattr(clock, "datetime", moved)
    monkeypatch.setattr(scheduler, "datetime", moved)
    monkeypatch.setattr(datetime_module, "datetime", moved)  # the jobs import datetime inside their bodies
    sent: list[tuple[str, str, datetime]] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body, **kw: (sent.append((title, body, ticking.now.astimezone(CHICAGO))), {"ok": True, "status": 200})[1])
    with Session(engine) as session:
        seed(session)
        set_setting(session, "push", {"kind": "ntfy", "url": "https://push.example.test", "topic": "fa-example", "brief_hour": 7})
        set_setting(session, "reminders", {"hour": 9, "checkin": True, "payday": False, "statement": False, "bills_days": 0, "notice": False})
    end = at_chicago(2026, 11, 3, 0, 0)
    while ticking.now < end:
        scheduler.morning_brief()
        if ticking.now.minute % 30 == 0:
            scheduler.reminders()
        if ticking.now.minute == 0:
            scheduler.week_recap_push()
        ticking.now += timedelta(minutes=10)
    briefs = [when for title, _, when in sent if title.startswith("Safe to spend")]
    assert [b.date() for b in briefs] == [date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)], briefs
    assert all(b.hour == 7 and b.minute < 10 for b in briefs), briefs  # at the owner's hour on both sides of the change
    assert [b.utcoffset() for b in briefs] == [timedelta(hours=-5), timedelta(hours=-5), timedelta(hours=-6), timedelta(hours=-6)]
    checkins = [when for title, _, when in sent if title == "Weekly check-in"]
    assert len(checkins) == 1 and checkins[0].date() == date(2026, 11, 1) and checkins[0].hour == 9, checkins
    recaps = [when for title, _, when in sent if title == "Your week"]
    assert len(recaps) == 1 and recaps[0].date() == date(2026, 11, 1) and recaps[0].hour == 18, recaps
    # The scheduler's intervals are UTC: a daily job fires 24 hours apart through the change, once each UTC day,
    # and the owner's day it lands on moves an hour, never a day.
    trigger = IntervalTrigger(hours=24, timezone="UTC")
    fires = [datetime(2026, 10, 31, 11, 0, tzinfo=UTC)]
    for _ in range(3):
        fires.append(trigger.get_next_fire_time(fires[-1], fires[-1] + timedelta(seconds=1)))
    assert [f - fires[0] for f in fires] == [timedelta(days=k) for k in range(4)]
    assert [f.astimezone(CHICAGO).hour for f in fires] == [6, 5, 5, 5] and [f.astimezone(CHICAGO).day for f in fires] == [31, 1, 2, 3]


def test_a_payday_on_a_weekend_moves_before_it_and_a_holiday_is_not_modelled():
    """cadence.py documents the move: the 15th and the last day (or the 1st) move to the business day before when
    they fall on a weekend. Bank holidays are not days it knows: Jan 1, 2027 (a Friday) stays the payday, and the
    winter run above shows the check that posts on Dec 31 is taken as that payday's, landed early."""
    assert semimonthly_anchors(2026, 11) == (date(2026, 11, 13), date(2026, 11, 30))  # Nov 15 is a Sunday
    assert semimonthly_anchors(2026, 12) == (date(2026, 12, 15), date(2026, 12, 31))
    assert semimonthly_anchors(2027, 1) == (date(2027, 1, 15), date(2027, 1, 29))  # Jan 31 is a Sunday
    assert semimonthly_anchors(2027, 1, "1,15") == (date(2027, 1, 15), date(2027, 1, 1))  # New Year's Day, a Friday: not moved
    assert semimonthly_anchors(2026, 11, "1,15") == (date(2026, 11, 13), date(2026, 10, 30))  # Nov 1 is a Sunday: the Friday before, in October
    assert semimonthly_anchors(2028, 2) == (date(2028, 2, 15), date(2028, 2, 29))  # the leap day is the last day
