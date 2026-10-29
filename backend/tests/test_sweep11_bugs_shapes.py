"""Sweep 11, part two: the live box's shapes, rebuilt synthetically (never its data), and a month of days over them.

The household here is the production box's outline: three bank links at the same bank, two reading the same three
accounts (the second by Plaid's own per-item ids, so the sync leaves them out and says so) and one inactive; a
Rocket Money account with eleven rows from 2023 and no balance; one Proton mailbox with twelve thousand message
references and 546 pending facts aged ten to thirteen days across the live kinds; 4,852 rows over three years on
three accounts; 66 recurring streams; 31 bills, 16 on autopay; two debts, one at 28.99% with no minimum on file;
two semi-monthly paychecks; a reviewer whose key is refused; push off.

Then the clock walks thirty days: each day the bank's rows land through the scripted vendor, every job runs in its
order, and every fifth day every GET route is read and timed. What is looked for: a route over 1.5 s, a route that
errors, a job that fails (the refused reviewer is allowed, and must back off), counts that disagree between the
badge, Needs you, the self-check and Next steps, the duplicate-link note repeating every run, old facts never
expiring, a minimum of 0 breaking payoff, the Rocket Money account's missing balance as a stale-balance step for
ever, detection re-splitting series across runs, and job-run growth.
"""

from __future__ import annotations

import calendar
import json
import random
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlmodel import Session, select

from app import crypto
from app.api import routes
from app.connectors import CONNECTORS
from app.db import engine
from app.jobs.scheduler import jobs
from app.llm import roles
from app.llm.providers import Scripted
from app.models import (
    Account,
    BankLink,
    Bill,
    Debt,
    ExtractedFact,
    Income,
    JobRun,
    MailAccount,
    MessageRef,
    RecurringSeries,
    Transaction,
    utcnow,
)
from app.store import set_setting
from tests.test_sweep8_bugs_quarter import ScriptedMailbox, links_in, walk
from tests.test_sweep11_bugs_common import failures, read_every_route, slow

START = date(2026, 10, 14)
DAYS = 30
MASKS = {"acc-1": "1111", "acc-2": "2222", "acc-3": "3333"}
FACT_KINDS = {"receipt": 229, "bill_due": 129, "advance": 61, "statement": 50, "installment": 48, "past_due": 15, "investment_balance": 7, "price_change": 6, "export": 1}
N_TX = 4852
N_REFS = 12000


def month_day(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class Shapes:
    """The streams the box's ledger is made of, deterministic from a seed, with a day's rows on demand."""

    def __init__(self, seed: int = 11) -> None:
        self.rng = random.Random(seed)
        self.bills = [(f"Example Bill {n:02d}", round(20 + 7.5 * n + (n % 3) * 11.0, 2), 1 + (n * 7) % 28, n % 2 == 0, "checking") for n in range(1, 32)]  # name, amount, due day, autopay, account
        self.subs = [(f"Example Sub {n:02d}", round(4.99 + 1.5 * n, 2), 1 + (n * 5) % 28, "card") for n in range(1, 34)]
        self.everyday = [f"Example Shop {n:02d}" for n in range(1, 41)]

    def rows_for(self, d: date, everyday: int | None = None) -> list[dict]:
        rows = []
        for name, amount, day, _auto, account in self.bills:
            if month_day(d.year, d.month, day) == d:
                rows.append({"date": d, "account": account, "merchant": name, "amount": amount, "category": "Bills & Utilities"})
        for name, amount, day, account in self.subs:
            if month_day(d.year, d.month, day) == d:
                rows.append({"date": d, "account": account, "merchant": name, "amount": amount, "category": "Entertainment"})
        last = calendar.monthrange(d.year, d.month)[1]
        if d.day in (15, last):
            rows.append({"date": d, "account": "checking", "merchant": "Example Employer A Payroll", "amount": -1900.0, "category": "Income"})
        if d.day in (1, 15):
            rows.append({"date": d, "account": "checking", "merchant": "Example Employer B Payroll", "amount": -1400.0, "category": "Income"})
        n = self.rng.choice([1, 2, 2, 3]) if everyday is None else everyday
        for _ in range(n):
            rows.append({"date": d, "account": self.rng.choice(["card", "card", "checking"]), "merchant": self.rng.choice(self.everyday), "amount": round(self.rng.uniform(4, 140), 2), "category": self.rng.choice(["Groceries", "Dining", "Shopping", "Gas"])})
        return rows


class LivePlaid:
    """Plaid from a script, by access token: link A reads the three accounts and the day's rows; link B is the same
    bank linked again, listing the same three accounts under its own item's ids (what Plaid does), so the sync
    leaves them to link A."""

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.cursor = 0
        self.balances = {"acc-1": 640.0, "acc-2": 1870.0, "acc-3": 250.0}
        self.day = START
        self.next_id = 0
        self.calls: dict[str, int] = {}
        self.tokens: set[str] = set()

    def queue_rows(self, rows: list[dict]) -> None:
        for r in rows:
            self.next_id += 1
            ext = {"checking": "acc-1", "card": "acc-2", "savings": "acc-3"}[r["account"]]
            self.queue.append({"transaction_id": f"live-{self.next_id}", "account_id": ext, "amount": r["amount"], "date": r["date"].isoformat(), "name": r["merchant"].upper(), "merchant_name": r["merchant"], "pending": False, "category": [r["category"]]})
            self.balances[ext] = round(self.balances[ext] - r["amount"] if ext != "acc-2" else self.balances[ext] + r["amount"], 2)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        token = str(body.get("access_token") or "")
        self.calls[path] = self.calls.get(path, 0) + 1
        if token:
            self.tokens.add(token)
        suffix = "b" if token == "tok-B" else ""
        if path == "/item/get":
            return httpx.Response(200, json={"status": {"transactions": {"last_successful_update": datetime(self.day.year, self.day.month, self.day.day, 6, tzinfo=UTC).isoformat()}}})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": [
                {"account_id": "acc-1" + suffix, "name": "Everyday Checking", "mask": "1111", "type": "depository", "subtype": "checking", "balances": {"current": self.balances["acc-1"], "available": self.balances["acc-1"]}},
                {"account_id": "acc-2" + suffix, "name": "Rewards Card", "mask": "2222", "type": "credit", "subtype": "credit card", "balances": {"current": self.balances["acc-2"], "limit": 3000.0}},
                {"account_id": "acc-3" + suffix, "name": "Savings", "mask": "3333", "type": "depository", "subtype": "savings", "balances": {"current": self.balances["acc-3"], "available": self.balances["acc-3"]}},
            ]})
        if path == "/transactions/sync":
            self.cursor += 1
            if token != "tok-A":
                return httpx.Response(200, json={"added": [], "modified": [], "removed": [], "next_cursor": f"b{self.cursor}", "has_more": False})
            page = {"added": self.queue, "modified": [], "removed": [], "next_cursor": f"c{self.cursor}", "has_more": False}
            self.queue = []
            return httpx.Response(200, json=page)
        if path in ("/item/remove", "/item/webhook/update"):
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


class Refusing(Scripted):
    """A reviewer whose key the provider refuses, every time."""

    async def chat(self, messages, tools=None, *, max_tokens=1024):
        raise RuntimeError("401 authentication_error: invalid x-api-key")
        yield  # pragma: no cover - makes this an async generator


def fact_fields(kind: str, n: int, today: date, shapes: Shapes) -> dict:
    if kind == "receipt":
        return {"merchant": shapes.everyday[n % len(shapes.everyday)], "total": round(10 + (n * 7.3) % 120, 2), "bought_on": (today - timedelta(days=11 + n % 20)).isoformat()}
    if kind == "bill_due":
        name = shapes.bills[n % len(shapes.bills)][0] if n % 3 else f"Example Service {n:02d}"
        return {"name": name, "amount": round(30 + (n * 5.1) % 150, 2), "due_date": (today + timedelta(days=n % 25 - 5)).isoformat()}
    if kind == "advance":
        return {"provider": "Example Advance", "amount": 100.0 + (n % 3) * 50, "fee": 3.99, "repay_date": (today + timedelta(days=n % 14)).isoformat()}
    if kind == "statement":
        return {"provider": "Example Card", "balance": round(400 + n * 13.7, 2), "period": "2026-09", "minimum": 35.0, "due_date": (today + timedelta(days=5 + n % 10)).isoformat()}
    if kind == "installment":
        return {"provider": "affirm", "merchant": f"Example Store {n:02d}", "installment": 40.0 + n, "count": 4, "total": (40.0 + n) * 4, "notice": "approved", "loan_id": f"L{n:04d}"}
    if kind == "past_due":
        return {"name": shapes.bills[n % len(shapes.bills)][0], "amount": shapes.bills[n % len(shapes.bills)][1]}
    if kind == "investment_balance":
        return {"balance": round(12000 + n * 311.5, 2), "account": "Example 401k", "as_of": (today - timedelta(days=12)).isoformat()}
    if kind == "price_change":
        return {"name": shapes.subs[n % len(shapes.subs)][0], "old_amount": shapes.subs[n % len(shapes.subs)][1], "new_amount": round(shapes.subs[n % len(shapes.subs)][1] + 2, 2)}
    return {"url": "https://example.test/export.csv", "host": "example.test", "sender": "hello@example-rocket.test", "subject": "Your export is ready"}


def build(session: Session, shapes: Shapes, today: date) -> dict:
    """The box as it stands on its own today. Returns the ids the loop needs."""
    ids: dict[str, int] = {}
    accounts = {
        "checking": Account(name="Everyday Checking", institution="Example Bank", kind="checking", connector="plaid", external_id="acc-1", last4_enc=crypto.encrypt("1111"), balance=640.0, balance_at=utcnow() - timedelta(hours=4)),
        "card": Account(name="Rewards Card", institution="Example Bank", kind="card", connector="plaid", external_id="acc-2", last4_enc=crypto.encrypt("2222"), balance=1870.0, credit_limit=3000.0, balance_at=utcnow() - timedelta(hours=4)),
        "savings": Account(name="Savings", institution="Example Bank", kind="savings", connector="plaid", external_id="acc-3", last4_enc=crypto.encrypt("3333"), balance=250.0, balance_at=utcnow() - timedelta(hours=4)),
        "old": Account(name="Old Checking", institution="Example Old Bank", kind="checking", connector="rocketmoney", external_id="rocketmoney:old checking:4321", balance=None, balance_at=None),
    }
    session.add_all(accounts.values())
    session.flush()
    for k, a in accounts.items():
        ids[k] = a.id
    # Three years of rows on the three live accounts, and eleven 2023 rows on the Rocket Money account.
    rows: list[dict] = []
    d = today - timedelta(days=3 * 365)
    while d <= today:
        rows.extend(shapes.rows_for(d, everyday=0))
        d += timedelta(days=1)
    rng = random.Random(7)
    while len(rows) < N_TX:
        when = today - timedelta(days=rng.randrange(0, 3 * 365))
        rows.append({"date": when, "account": rng.choice(["card", "card", "checking"]), "merchant": rng.choice(shapes.everyday), "amount": round(rng.uniform(4, 140), 2), "category": rng.choice(["Groceries", "Dining", "Shopping", "Gas"])})
    rows = rows[:N_TX]
    now = utcnow()
    session.execute(insert(Transaction.__table__), [
        {"account_id": ids[r["account"]], "date": r["date"], "merchant": r["merchant"], "raw_name": r["merchant"].upper(), "amount": r["amount"], "category": r["category"],
         "is_income": r["amount"] < 0 and r["category"] == "Income", "source": "plaid", "source_ref": f"seed-{i}", "row_hash": f"seed-{i}", "created_at": now, "business_share": 1.0}
        for i, r in enumerate(rows)])
    session.execute(insert(Transaction.__table__), [
        {"account_id": ids["old"], "date": date(2023, 3 + i % 6, 1 + i * 2), "merchant": shapes.everyday[i], "raw_name": shapes.everyday[i].upper(), "amount": round(12.5 * (i + 1), 2), "category": "Shopping",
         "source": "rocketmoney", "source_ref": f"rm-{i}", "row_hash": f"rm-{i}", "created_at": now, "business_share": 1.0} for i in range(11)])
    # 31 bills (16 on autopay), two debts, two semi-monthly paychecks.
    for name, amount, day, autopay, account in shapes.bills:
        session.add(Bill(name=name, amount=amount, amount_mode="estimate" if day % 5 == 0 else "fixed", due_day=day, autopay=autopay, pay_from_account_id=ids[account], category="Bills & Utilities", created_on=today - timedelta(days=400)))
    session.add(Debt(name="Rewards Card", kind="card", balance=1870.0, apr=28.99, minimum=0.0, statement_day=3, due_day=28, account_id=ids["card"], credit_limit=3000.0))
    session.add(Debt(name="Example Auto Loan", kind="loan", balance=9400.0, apr=6.5, minimum=310.0, due_day=28))
    session.add(Income(name="Example Employer A", cadence="semimonthly", expected_net=1900.0, next_date=date(2026, 10, 15), account_id=ids["checking"], pay_days=""))
    session.add(Income(name="Example Employer B", cadence="semimonthly", expected_net=1400.0, next_date=date(2026, 10, 15), account_id=ids["checking"], pay_days="1,15"))
    # Three links at the bank: A reads the accounts; B is the same bank linked again; C was removed.
    for label, token, accounts_listed, active in (("A", "tok-A", "acc-1,acc-2,acc-3", True), ("B", "tok-B", "", True), ("C", "tok-C", "acc-1,acc-2,acc-3", False)):
        session.add(BankLink(provider="plaid", external_id=f"item-{label}", environment="production", institution="Example Bank", access_token_enc=crypto.encrypt(token), cursor="c0" if label == "A" else "",
                             accounts=accounts_listed, active=active, last_sync_at=utcnow() - timedelta(hours=5), created_at=utcnow() - timedelta(days=200 if label != "B" else 20)))
    # The mailbox: twelve thousand references, 546 pending facts aged ten to thirteen days.
    box = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), use_ssl=False, starttls=True, folders="INBOX",
                      last_sync_at=utcnow() - timedelta(minutes=30), messages_seen=N_REFS)
    session.add(box)
    session.flush()
    ids["box"] = box.id
    senders = [("billing@example-power.test", "Example Power"), ("hello@example-rocket.test", "Rocket Money"), ("no-reply@example-card.test", "Example Card"), ("receipts@example-shop.test", "Example Shop"), ("news@example-news.test", "Example News")]
    kinds = ["none", "none", "none", "receipt", "bill_due", "statement", "newsletter", "none"]
    session.execute(insert(MessageRef.__table__), [
        {"account_id": box.id, "folder": "INBOX", "uid": u, "message_id": f"<m{u}@example.test>", "sender": senders[u % 5][0], "sender_name": senders[u % 5][1], "subject": f"Message {u}",
         "date": datetime.combine(today - timedelta(days=(N_REFS - u) // 16), datetime.min.time(), tzinfo=UTC), "snippet": "Example text", "kind": kinds[u % 8], "created_at": now,
         "trust": "ok" if u % 7 else "", "trust_why": ""} for u in range(1, N_REFS + 1)])
    set_setting(session, f"mail.{box.id}.INBOX.last_uid", N_REFS)
    session.flush()
    ref_ids = session.exec(select(MessageRef.id).where(MessageRef.account_id == box.id).order_by(MessageRef.uid.desc())).all()
    facts = []
    n = 0
    for kind, count in FACT_KINDS.items():
        for _ in range(count):
            age = 10 + n % 4
            facts.append({"kind": kind, "message_ref_id": ref_ids[n], "fields": json.dumps(fact_fields(kind, n, today, shapes)), "confidence": 0.8, "status": "pending", "excerpt": f"Example {kind} {n}",
                          "produced_by": "rules", "applied_to": "", "created_at": now - timedelta(days=age)})
            n += 1
    session.execute(insert(ExtractedFact.__table__), facts)
    set_setting(session, "models", {"reviewer": {"provider": "anthropic", "model": "claude-sonnet-4-5"}})
    set_setting(session, "banks.freshness", {"sync_every_hours": 4})
    session.commit()
    return ids


class Month:
    def __init__(self, owner: TestClient) -> None:
        self.owner = owner
        self.day = START
        self.shapes = Shapes()
        self.plaid = LivePlaid()
        self.findings: list[str] = []
        self.timings: list[str] = []
        self.slowest: dict[str, float] = {}
        self.series_seen: dict[tuple, int] = {}
        self.old_balance_steps = 0
        self.reads = 0
        self.run_counts: list[int] = []
        with Session(engine) as session:
            self.ids = build(session, self.shapes, START)
            self.ids["bill_id"] = session.exec(select(Bill.id)).first()
            self.ids["message_id"] = session.exec(select(MessageRef.id)).first()
        CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(self.plaid.handler))
        ScriptedMailbox.messages = {}
        CONNECTORS["mail"].client_factory = ScriptedMailbox
        roles.set_override("reviewer", Refusing([]))
        assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "production"}).status_code == 200

    def note(self, text: str) -> None:
        self.findings.append(f"{self.day.isoformat()}: {text}")

    def feed(self) -> None:
        self.plaid.day = self.day
        self.plaid.queue_rows(self.shapes.rows_for(self.day))
        with Session(engine) as session:
            for link in session.exec(select(BankLink)).all():
                if link.last_sync_at is not None:
                    link.last_sync_at = link.last_sync_at - timedelta(days=1)
                    session.add(link)
            session.commit()

    def run_jobs(self) -> list[JobRun]:
        runs = [jobs.run(name) for name in jobs.specs]
        for run in runs:
            if not run.ok and run.name != "review_if_due":
                self.note(f"job {run.name} failed: {run.summary[:300]}")
        return runs

    def check_counts(self, got: dict) -> None:
        snap = got["/api/v1/snapshot"][2]
        needs = got["/api/v1/needs-you"][2]
        facts = got["/api/v1/facts"][2]
        steps = got["/api/v1/next_steps"][2]
        if needs["total"] != snap["pending"]["confirmations"]:
            self.note(f"the badge says {snap['pending']['confirmations']}, Needs you {needs['total']}: {needs['word']}")
        if facts["pending"] != snap["pending"]["facts"]:
            self.note(f"the facts page counts {facts['pending']} pending, the badge {snap['pending']['facts']}")
        listed = sum(g["count"] for g in needs["groups"])
        if listed != facts["pending"]:
            self.note(f"Needs you lists {listed} facts, {facts['pending']} are pending")
        step = next((s for s in steps["steps"] if s["id"] == "facts"), None)
        said = int(step["text"].split()[0]) if step else 0
        if said != facts["pending"]:
            self.note(f"Next steps says {said} things from the mail need a look, {facts['pending']} are pending")
        for link in links_in(steps) + links_in(needs) + links_in(snap["brief"]):
            if not link.startswith("/"):
                self.note(f"a step links to {link}")
        # The Rocket Money account has no balance to be stale: no step should nag about it, and not for ever.
        for s in steps["steps"]:
            if "Old Checking" in s["text"] and ("old" in s["text"] or "stale" in s["text"] or "balance" in s["text"]):
                self.old_balance_steps += 1
        # A minimum of 0 on the card: the payoff plan still ends, with the assumed minimum said.
        payoff = got["/api/v1/debts/payoff"][2]
        names = {d["name"] for d in payoff["debts"]} if "debts" in payoff else set()
        if "Rewards Card" not in names:
            self.note(f"the card with no minimum is missing from payoff: {sorted(names)}")
        for way in ("minimums_only", "avalanche", "snowball"):
            if way in payoff and walk(payoff[way]):
                self.note(f"payoff {way} holds a non-finite number: {walk(payoff[way])[:3]}")

    def check_series(self) -> None:
        with Session(engine) as session:
            series = session.exec(select(RecurringSeries)).all()
        keys = [(s.merchant_key, s.cadence) for s in series if s.kind != "ignored" and s.active]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            self.note(f"series split: {dupes[:5]}")
        n = len([s for s in series if s.active])
        if self.series_seen and n > max(self.series_seen.values()) + 3:
            self.note(f"active series grew from {max(self.series_seen.values())} to {n}")
        self.series_seen[(self.day,)] = n

    def read_day(self) -> dict:
        got = read_every_route(self.owner, self.day, self.ids)
        self.reads += 1
        for f in failures(got):
            self.note(f)
        for s in slow(got):
            self.timings.append(f"{self.day.isoformat()}: {s}")
        for path, (_st, seconds, _b) in got.items():
            self.slowest[path] = max(self.slowest.get(path, 0.0), seconds)
        for path, (status, _s, body) in got.items():
            if status == 200 and body is not None and walk(body):
                self.note(f"{path} holds {walk(body)[:3]}")
        self.check_counts(got)
        return got


@pytest.fixture
def month(owner: TestClient) -> Month:
    m = Month(owner)
    yield m
    CONNECTORS["plaid"].client_factory = None
    roles.set_override("reviewer", None)
    from app.mail.imap import ImapClient

    CONNECTORS["mail"].client_factory = ImapClient


def test_a_month_over_the_live_boxs_shapes(month: Month):
    import resource

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    duplicate_notes = 0
    review_failures = 0
    pending_at_start = None
    first_series = None
    for _ in range(DAYS):
        month.day += timedelta(days=1)
        routes.set_today_override(month.day)
        month.feed()
        runs = month.run_jobs()
        for r in runs:
            if r.name == "bank_sync" and "already read through your other" in r.summary:
                duplicate_notes += 1
            if r.name == "review_if_due" and not r.ok:
                review_failures += 1
        month.check_series()
        with Session(engine) as session:
            pending = len(session.exec(select(ExtractedFact.id).where(ExtractedFact.status == "pending")).all())
            month.run_counts.append(len(session.exec(select(JobRun.id)).all()))
            if first_series is None:
                first_series = len(session.exec(select(RecurringSeries.id).where(RecurringSeries.active == True)).all())
        pending_at_start = pending if pending_at_start is None else pending_at_start
        if month.day.day % 5 == 0:
            month.read_day()
    with Session(engine) as session:
        pending = len(session.exec(select(ExtractedFact.id).where(ExtractedFact.status == "pending")).all())
        links = {l.external_id: l for l in session.exec(select(BankLink)).all()}
        active_series = len(session.exec(select(RecurringSeries.id).where(RecurringSeries.active == True)).all())
        accounts = session.exec(select(Account)).all()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"\nshapes: peak rss +{(rss_after - rss_before) / 1e6:.0f} MB over the month; {duplicate_notes} duplicate-link notes, {review_failures} refused reviews, {pending_at_start}->{pending} pending facts, {first_series}->{active_series} series, "
          f"job runs {month.run_counts[0]}->{month.run_counts[-1]}; slowest routes: " + ", ".join(f"{p} {t:.2f}s" for p, t in sorted(month.slowest.items(), key=lambda kv: -kv[1])[:6]))
    # What the month must show.
    assert month.reads == 6, month.reads
    assert first_series is not None and first_series >= 60, first_series
    assert not month.findings, "\n".join(month.findings)
    assert not month.timings, "\n".join(month.timings)
    assert len(accounts) == 4, [a.name for a in accounts]  # link B added no account of its own
    assert links["item-B"].active and not links["item-B"].last_error, links["item-B"].last_error
    # Link B records the ids it listed (only_duplicates judges it by them) and no account carries them.
    assert links["item-B"].accounts == "acc-1b,acc-2b,acc-3b" and not {a.external_id for a in accounts} & set(links["item-B"].accounts.split(","))
    assert "tok-C" not in month.plaid.tokens, "the removed link was read"
    assert 0 < duplicate_notes, "link B never said it leaves the accounts to link A"
    assert review_failures >= 1, "the refused key never failed a review run"
    assert review_failures <= 6, f"a refused reviewer key failed {review_failures} runs in {DAYS} days: it does not back off"
    assert pending <= pending_at_start, (pending, pending_at_start)
    assert month.old_balance_steps == 0, f"the Rocket Money account with no balance was a stale-balance step on {month.old_balance_steps} reads"
    assert active_series <= first_series + 3, (first_series, active_series)
    growth = [b - a for a, b in zip(month.run_counts, month.run_counts[1:])]
    assert max(growth) <= len(jobs.specs) + 2, growth
