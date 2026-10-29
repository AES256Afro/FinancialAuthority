"""Sweep 8: a simulated quarter, end to end through the real app and jobs.

The synthetic household is seeded as it stands on its own "today", then the clock is advanced one day at a time
for a hundred days. Each day the day's rows reach the ledger the way a bank link's do (a scripted Plaid vendor behind
the plaid connector, read by the bank_sync job), the mailbox gets its weekly upcoming-bills list (a scripted IMAP
server behind the mail connector, read by the mail_sync job), every registered job runs in its scheduled order, and
the screens' routes are read and checked against the invariants in `check`. On a few days the owner taps: names a
series as a bill, confirms a fact, says "I paid it", plans a payment and lets it post, removes the bank link (keeping
the accounts) and links it again, imports a file and undoes it. Each tap's effect must persist across later days.

Sweep 9 widened it to the registers it did not reach: a second person with the Everything role (their own checking,
a shared savings), a home on Housing with the rent as its bill, two utilities and a lease ending inside the quarter
(notice given on a tap) with the next home and its move-in check, a business with an invoice sent and paid, a marked
write-off and a trip (the tax center read at year end), an investment account with a typed balance and a holdings
file, a save-up goal and a sinking fund fed on each payday, a scenario run and a billing-date move accepted, the
daily restore point, the weekly off-box copy and the restore drill (aged a day each day, as the bank link is), a
scripted reviewer whose buffer proposal the owner applies, and the week recap and month report at each month end.
"""

from __future__ import annotations

import io
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto, offbox, restore
from app.api import routes
from app.connectors import CONNECTORS
from app.db import engine
from app.engine.custom_reports import run_report, year_in_review
from app.engine.household import BILLS, NET_PAY, generate, seed
from app.engine.projection import is_spendable
from app.engine.recap import week_recap
from app.jobs.scheduler import jobs
from app.llm import roles
from app.llm.providers import Event, Scripted
from app.main import app
from app.models import Account, AuditEntry, BankLink, Bill, ExtractedFact, Goal, Holding, Home, Invoice, JobRun, MailAccount, Payment, PlannedPayment, RecurringSeries, Transaction, Trip
from app.store import get_setting, set_setting

START = date(2026, 10, 14)  # the household's own today; its ledger is seeded up to here
DAYS = 100  # through 2027-01-22: seven paydays, three month ends, a year end
EXTERNAL = {"checking": "acc-chk", "card": "acc-card", "store": "acc-store", "loan": "acc-loan"}
PAYDAYS_AFTER_FIRST = [date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27), date(2026, 12, 11), date(2026, 12, 25), date(2027, 1, 8)]  # the goal and the fund are fed on each
MONTH_ENDS = {date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 22)}  # the week recap and the month report are read here
WIDE_ROUTES = ("/api/v1/housing", "/api/v1/move/plan", "/api/v1/business", "/api/v1/investments", "/api/v1/goals", "/api/v1/goals/sinking", "/api/v1/taxes", "/api/v1/recap", "/api/v1/reviews",
               "/api/v1/restore-points", "/api/v1/restore-points/offbox", "/api/v1/reports/spending?months=1")
REVIEW_REPLY = json.dumps({"notes": [{"kind": "tighten", "text": "The buffer is thin for a household with advances; I would hold $300.", "action": {"type": "buffer", "value": 300}}]})
PASSPHRASE = "example passphrase for the quarter"
READ_ROUTES = ("/api/v1/snapshot", "/api/v1/today", "/api/v1/needs-you", "/api/v1/paychecks", "/api/v1/cover", "/api/v1/borrowing", "/api/v1/debts/payoff", "/api/v1/bills", "/api/v1/payments", "/api/v1/next_steps")
AUDITED_READS = 3  # /today, /cover and /paychecks are tool calls, and every tool call writes an audit row
EXTRA_ROUTES = ("/api/v1/recurring", "/api/v1/paystubs", "/api/v1/recap", "/api/v1/watch", "/api/v1/data-check", "/api/v1/taxes", "/api/v1/budgets", "/api/v1/accounts", "/api/v1/incomes", "/api/v1/planned", "/api/v1/goals", "/api/v1/housing", "/api/v1/credit", "/api/v1/projections", "/api/v1/glance", "/api/v1/checkin", "/api/v1/saving")
EXTRA_AUDITED = 3  # /glance, /checkin and /saving
PAGES = {"/", "/today", "/transactions", "/bills", "/mail", "/chat", "/paystubs", "/documents", "/debts", "/borrowing", "/housing", "/move", "/cover", "/saving", "/checkin", "/welcome", "/exports", "/glossary", "/switch", "/accounts",
         "/investments", "/outlook", "/business", "/business/write-offs", "/business/invoices", "/business/miles", "/taxes", "/reports", "/confirm", "/shared", "/settings", "/more", "/budgets", "/recap", "/settings/security", "/settings/people",
         "/settings/privacy", "/settings/appearance", "/settings/clients", "/settings/models", "/settings/notifications", "/settings/connectors", "/settings/jobs", "/data-check", "/settings/audit", "/settings/backups", "/settings/setup"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DATE_WORD = re.compile(r"\b(" + "|".join(MONTHS) + r") (\d{1,2})\b")


# ---- the quarter's rows ------------------------------------------------------------------------------------


def future_rows(start: date, end: date) -> dict[date, list[dict]]:
    """The household's rows after its own today: the generator's bills, plans and everyday spending for a longer
    run (its paychecks and advances only reach its own today, so those are written here), with the seeded
    advances repaid on the first payday and a planned card payment on Oct 28 for the owner's tap."""
    h = generate(today=end, months=12, seed=42)
    by_day: dict[date, list[dict]] = {}
    for t in h.transactions:
        if t["date"] <= start or t["merchant"].startswith("Paycheck advance") or t.get("is_income"):
            continue
        by_day.setdefault(t["date"], []).append(dict(t))
    pd = date(2026, 10, 16)
    while pd <= end:
        if pd > start:
            by_day.setdefault(pd, []).append({"date": pd, "account": "checking", "merchant": "Example Employer Payroll", "amount": -NET_PAY, "category": "Income"})
        pd += timedelta(days=14)
    repay = date(2026, 10, 16)
    by_day.setdefault(repay, []).extend([
        {"date": repay, "account": "checking", "merchant": "Paycheck advance repayment", "amount": 156.0, "category": "Fees & Charges"},
        {"date": repay, "account": "checking", "merchant": "Paycheck advance repayment", "amount": 75.0, "category": "Fees & Charges"},
    ])
    extra = date(2026, 10, 28)
    by_day.setdefault(extra, []).extend([
        {"date": extra, "account": "checking", "merchant": "Payment to Rewards Card", "amount": 100.0, "category": "Credit Card Payment"},
        {"date": extra, "account": "card", "merchant": "Payment received", "amount": -100.0, "category": "Credit Card Payment"},
    ])
    # The wider quarter's rows: a business cost, a client's payment of the invoice, the move-in check for the next
    # home, and the phone bill landing on the 5th from December, the day the owner had it moved to.
    by_day.setdefault(date(2026, 11, 3), []).append({"date": date(2026, 11, 3), "account": "card", "merchant": "Example Cloud Hosting", "amount": 24.0, "category": "Software"})
    by_day.setdefault(date(2026, 11, 18), []).append({"date": date(2026, 11, 18), "account": "checking", "merchant": "Example Client", "amount": -450.0, "category": "Income"})
    by_day.setdefault(date(2027, 1, 13), []).append({"date": date(2027, 1, 13), "account": "checking", "merchant": "Example Flats", "amount": 1920.0, "category": "Moving"})
    for d in list(by_day):
        if d >= date(2026, 12, 1):
            for t in by_day[d]:
                if t["merchant"] == "Phone":
                    t["date"] = d.replace(day=5)
                    by_day.setdefault(t["date"], []).append(t)
            by_day[d] = [t for t in by_day[d] if t["merchant"] != "Phone"]
    for day in by_day.values():
        day.sort(key=lambda t: (t["account"], t["merchant"], t["amount"]))
    return by_day


def upcoming_bills_mail(day: date) -> str:
    """Rocket Money's weekly list: the register's bills due in the next seven days and the two charges with no bill."""
    lines = []
    extra = [("Example Cloud Storage", 2.99, 9), ("Example Storage Unit", 89.0, 12)]
    for offset in range(1, 8):
        d = day + timedelta(days=offset)
        for name, amount, due_day, *_ in BILLS:
            if min(due_day, 28) == d.day and name != "Card minimum":
                lines.append(f"{name}\n\n{d.strftime('%A, %B')} {d.day}\n\n${amount:,.2f}\n\n")
        for name, amount, due_day in extra:
            if due_day == d.day:
                lines.append(f"{name}\n\n{d.strftime('%A, %B')} {d.day}\n\n${amount:,.2f}\n\n")
    if not lines:
        return ""
    return f"Heads up Owner...\n\nYou have {len(lines)} recurring charges coming up within the next 7 days.\n\n" + "".join(lines) + "Open Rocket Money\n\nLooking to lower your bills?\n\nLower my bills\n"


def raw_mail(subject: str, text: str, day: date) -> bytes:
    m = EmailMessage()
    m["From"] = "Rocket Money <hello@email.rocketmoney.com>"
    m["To"] = "owner@example.test"
    m["Subject"] = subject
    m["Date"] = datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = f"<{day.isoformat()}-{abs(hash(subject))}@example.test>"
    m.set_content(text)
    return bytes(m)


# ---- the scripted vendors ----------------------------------------------------------------------------------


class ScriptedPlaid:
    """Plaid's endpoints from a script: the household's four accounts, and whatever rows the day queued."""

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.cursor = 0
        self.balances = {"checking": 312.0, "card": 2310.0, "store": 640.0, "loan": 18400.0}
        self.day = START
        self.next_id = 0

    def queue_rows(self, rows: list[dict]) -> None:
        for r in rows:
            self.next_id += 1
            self.queue.append({"transaction_id": f"sim-{self.next_id}", "account_id": EXTERNAL[r["account"]], "amount": r["amount"], "date": r["date"].isoformat(), "name": r["merchant"].upper(), "merchant_name": r["merchant"], "pending": False, "category": [r["category"]]})
            self.balances[r["account"]] = round(self.balances[r["account"]] - r["amount"] if r["account"] == "checking" else self.balances[r["account"]] + r["amount"], 2)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-123"})
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-sandbox-abc", "item_id": "item-1"})
        if path == "/item/get":
            return httpx.Response(200, json={"status": {"transactions": {"last_successful_update": datetime(self.day.year, self.day.month, self.day.day, 6, tzinfo=timezone.utc).isoformat()}}})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": [
                {"account_id": "acc-chk", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": self.balances["checking"], "available": self.balances["checking"]}},
                {"account_id": "acc-card", "name": "Rewards Card", "mask": "9012", "type": "credit", "subtype": "credit card", "balances": {"current": self.balances["card"]}},
                {"account_id": "acc-store", "name": "Store Card", "mask": "5678", "type": "credit", "subtype": "credit card", "balances": {"current": self.balances["store"]}},
                {"account_id": "acc-loan", "name": "Auto Loan", "mask": "7777", "type": "loan", "subtype": "auto", "balances": {"current": self.balances["loan"]}},
            ]})
        if path == "/transactions/sync":
            self.cursor += 1
            page = {"added": self.queue, "modified": [], "removed": [], "next_cursor": f"c{self.cursor}", "has_more": False}
            self.queue = []
            return httpx.Response(200, json=page)
        if path in ("/item/remove", "/item/webhook/update"):
            return httpx.Response(200, json={"removed": True})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


class Replaying(Scripted):
    """The reviewer, scripted: the same reply to every review (a buffer proposal), however often it is asked."""

    def __init__(self, turn: list[Event]) -> None:
        super().__init__([])
        self.turn = turn

    async def chat(self, messages, tools=None, *, max_tokens=1024):  # noqa: ANN001, ANN201
        self.turns = [list(self.turn)]
        async for event in super().chat(messages, tools, max_tokens=max_tokens):
            yield event


class ScriptedMailbox:
    messages: dict[int, bytes] = {}

    def __init__(self, host, port, username, password, use_ssl=True, starttls=False):  # noqa: ANN001
        self.folder = None

    def connect(self) -> None:
        pass

    def folders(self) -> list[str]:
        return ["INBOX"]

    def select_readonly(self, folder: str) -> tuple[int, int]:
        self.folder = folder
        return (len(self.messages), max(self.messages, default=0) + 1)

    def new_uids(self, since_uid: int) -> list[int]:
        return [u for u in sorted(self.messages) if u > since_uid]

    def fetch_raw(self, uid: int) -> bytes:
        return self.messages[uid]

    def close(self) -> None:
        pass


# ---- the harness -------------------------------------------------------------------------------------------


def walk(value, path="$", bad=None):  # noqa: ANN001
    """Every non-finite or negative-zero number in a JSON body, by path."""
    bad = [] if bad is None else bad
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value) or (value == 0 and math.copysign(1, value) < 0):
            bad.append(f"{path}={value!r}")
    elif isinstance(value, dict):
        for k, v in value.items():
            walk(v, f"{path}.{k}", bad)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            walk(v, f"{path}[{i}]", bad)
    return bad


def links_in(value, found=None):  # noqa: ANN001
    found = [] if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "link" and isinstance(v, str) and v.startswith("/"):
                found.append(v)
            else:
                links_in(v, found)
    elif isinstance(value, list):
        for v in value:
            links_in(v, found)
    return found


def dated(text: str, today: date) -> list[date]:
    """The days a brief line names ('Oct 25'), in the year nearest today."""
    out = []
    for month, day in DATE_WORD.findall(text):
        m = MONTHS.index(month) + 1
        try:
            near = min((date(today.year + k, m, int(day)) for k in (-1, 0, 1)), key=lambda d: abs((d - today).days))
        except ValueError:
            continue
        out.append(near)
    return out


class Quarter:
    def __init__(self, owner: TestClient, folder: str = "") -> None:
        self.owner = owner
        self.day = START
        self.plaid = ScriptedPlaid()
        self.rows = future_rows(START, START + timedelta(days=DAYS + 1))
        self.uid = 0
        self.findings: list[str] = []
        self.tx_floor = 0
        self.payments_prev: dict[int, str] = {}
        self.api_paths = {getattr(r, "path", "") for r in app.routes}
        self.remember: dict[str, int] = {}
        self.goal_funded: dict[int, float] = {}
        self.fed_today = False
        with Session(engine) as session:
            seed(session)
            for a in session.exec(select(Account)).all():
                a.connector, a.external_id = "plaid", EXTERNAL[a.external_id]  # a bank linked before: the link's accounts are the household's
                session.add(a)
                self.remember[a.kind if a.kind != "card" else a.name] = a.id
            session.add(MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), use_ssl=False, starttls=True, folders="INBOX"))
            session.commit()
        CONNECTORS["plaid"].client_factory = lambda: httpx.Client(transport=httpx.MockTransport(self.plaid.handler))
        ScriptedMailbox.messages = {}
        CONNECTORS["mail"].client_factory = ScriptedMailbox
        assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"}).status_code == 200
        self.link_id = self.link()
        if folder:
            self.widen(folder)

    def link(self) -> int:
        linked = self.owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-xyz", "institution": "Example Bank"}).json()
        assert linked["sync"]["ok"], linked
        with Session(engine) as session:
            assert len(session.exec(select(Account).where(Account.connector == "plaid")).all()) == 4, "the link took over the household's accounts, none added"
        return linked["link"]["id"]

    def widen(self, folder: str) -> None:
        """The registers the first quarter did not reach, set up as the household stands on its own today."""
        owner = self.owner
        roles.set_override("reviewer", Replaying([Event("text", text=REVIEW_REPLY), Event("usage", input_tokens=900, output_tokens=60), Event("done")]))
        # A second person with the Everything role: their own checking, and a savings account shared with the owner.
        invited = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"})
        assert invited.status_code == 200, invited.text
        self.partner = TestClient(app)
        self.partner.headers.update({"X-Requested-With": "FinancialAuthority"})
        joined = self.partner.post("/api/auth/join", json={"token": invited.json()["invite"]["link"].split("#", 1)[1], "password": "plum river lantern seven"})
        assert joined.status_code == 200, joined.text
        self.partner_id = invited.json()["invite"]["person_id"]
        theirs = owner.post("/api/v1/accounts", json={"name": "Example Partner Checking", "kind": "checking", "balance": 540.0}).json()
        joint = owner.post("/api/v1/accounts", json={"name": "Example Joint Savings", "kind": "savings", "balance": 1200.0}).json()
        assert owner.put(f"/api/v1/accounts/{theirs['id']}/sharing", json={"shared": False, "person_id": self.partner_id}).status_code == 200
        assert owner.put(f"/api/v1/accounts/{joint['id']}/sharing", json={"shared": True}).status_code == 200
        self.remember["theirs"], self.remember["joint"] = theirs["id"], joint["id"]
        # An investment account with a typed balance; its holdings come from a file on Nov 15.
        inv = owner.post("/api/v1/investments", json={"name": "Example Brokerage", "institution": "Example Broker", "subtype": "brokerage", "balance": 5000.0, "as_of": START.isoformat()})
        assert inv.status_code == 200, inv.text
        self.remember["brokerage"] = next(i["id"] for i in inv.json()["items"] if i["name"] == "Example Brokerage")
        # A save-up goal, and a yearly bill with a sinking fund; both are fed on each payday after the first.
        goal = owner.post("/api/v1/goals", json={"name": "Example Trip", "kind": "save", "target": 600.0, "target_date": "2027-01-15", "pace": 100.0})
        assert goal.status_code == 200, goal.text
        self.remember["goal"] = next(g["id"] for g in goal.json()["items"] if g["name"] == "Example Trip")
        bill = owner.post("/api/v1/bills", json={"name": "Example Car Registration", "amount": 180.0, "due_day": 10, "cadence": "yearly", "anchor_date": "2027-03-10", "category": "Auto & Transport", "autopay": False})
        assert bill.status_code == 200, bill.text
        fund = owner.post(f"/api/v1/goals/sinking/{bill.json()['id']}")
        assert fund.status_code == 200, fund.text
        self.remember["fund"] = next(i["goal_id"] for i in fund.json()["items"] if i["bill_id"] == bill.json()["id"])
        # The off-box copy: weekly, sealed with a passphrase, into a folder off the data disk.
        assert owner.put("/api/v1/restore-points/offbox", json={"folder": folder, "passphrase": PASSPHRASE, "every_days": 7}).status_code == 200

    # -- one day --

    def feed(self) -> None:
        self.plaid.day = self.day
        self.plaid.queue_rows(self.rows.get(self.day, []))
        if self.day.weekday() == 4:
            text = upcoming_bills_mail(self.day)
            if text:
                self.uid += 1
                ScriptedMailbox.messages[self.uid] = raw_mail("This week's upcoming bills", text, self.day)
        with Session(engine) as session:
            for link in session.exec(select(BankLink)).all():
                if link.last_sync_at is not None:
                    link.last_sync_at = link.last_sync_at - timedelta(days=1)  # the clock moved a day; the bank is due
                    session.add(link)
            session.commit()
            # What is kept by wall-clock age (the daily copy, the off-box copy, the drill) is a day older too.
            for key, field in ((offbox.SETTING, "last_at"), (offbox.DRILL, "at")):
                kept = get_setting(session, key, None)
                if kept and kept.get(field):
                    kept[field] = (datetime.fromisoformat(kept[field]) - timedelta(days=1)).isoformat()
                    set_setting(session, key, kept)
        for meta in restore.folder().glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                data["created_at"] = (datetime.fromisoformat(data["created_at"]) - timedelta(days=1)).isoformat()
            except (ValueError, KeyError, TypeError):
                continue
            meta.write_text(json.dumps(data), encoding="utf-8")
        self.fed_today = False

    def run_jobs(self) -> list[JobRun]:
        runs = [jobs.run(name) for name in jobs.specs]
        for run in runs:
            if not run.ok:
                self.note(f"job {run.name} failed: {run.summary[:300]}")
        return runs

    def read(self, extra: bool = False) -> dict[str, dict]:
        out = {}
        for path in READ_ROUTES + (EXTRA_ROUTES if extra else ()):
            response = self.owner.get(path)
            if response.status_code != 200:
                self.note(f"{path} answered {response.status_code}: {response.text[:200]}")
                continue
            body = response.json()
            bad = walk(body)
            if bad:
                self.note(f"{path} holds {bad[:5]}")
            out[path] = body
        return out

    def note(self, text: str) -> None:
        self.findings.append(f"{self.day.isoformat()}: {text}")

    def check(self, got: dict[str, dict]) -> None:
        snap = got.get("/api/v1/snapshot")
        if snap is None:
            return
        with Session(engine) as session:
            n_tx = len(session.exec(select(Transaction.id)).all())
            bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
            payments = session.exec(select(Payment)).all()
            series = session.exec(select(RecurringSeries)).all()
        # counts that must be monotone
        if n_tx < self.tx_floor:
            self.note(f"transactions shrank from {self.tx_floor} to {n_tx}")
        self.tx_floor = n_tx
        names = [(b.name, round(b.amount, 2)) for b in bills]
        if len(names) != len(set(names)):
            self.note(f"bills duplicated by name+amount: {[n for n in names if names.count(n) > 1]}")
        by_bill: dict[int, list[Payment]] = {}
        for p in payments:
            by_bill.setdefault(p.bill_id, []).append(p)
        for bid, rows in by_bill.items():
            dues = [p.due_date for p in rows]
            if len(dues) != len(set(dues)):
                self.note(f"bill {bid} holds two payments for one due date {sorted(d.isoformat() for d in dues)}")
        keys = [(s.merchant_key, s.cadence) for s in series if s.kind != "ignored" and s.active]
        if len(keys) != len(set(keys)):
            self.note(f"series split: {[k for k in keys if keys.count(k) > 1]}")
        # a payment that was paid stays paid
        now_status = {p.id: p.status for p in payments}
        for pid, was in self.payments_prev.items():
            if was == "paid" and now_status.get(pid) not in ("paid", None):
                self.note(f"payment {pid} flipped from paid to {now_status.get(pid)}")
        self.payments_prev = now_status
        # the same payday appears once, and never in the past
        paydays = [p["date"] for p in snap["paydays"]]
        if len(paydays) != len(set(paydays)):
            self.note(f"a payday appears twice: {paydays}")
        if snap["next_paycheck"]["days"] < 0:
            self.note(f"next paycheck {snap['next_paycheck']['date']} is in the past")
        # safe to spend equals its parts
        sts = snap["safe_to_spend"]
        parts = sts["parts"]
        expected = parts["cash"] - parts["due_before_payday"] - parts["advances"] - parts["buffer"] - parts["next_window_shortfall"]
        if abs(expected - sts["amount"]) > 0.011:
            self.note(f"safe to spend {sts['amount']} is not its parts {parts} (expected {round(expected, 2)})")
        # nothing upcoming is dated in the past
        for key in ("due_before_payday", "window", "set_aside", "advances"):
            for e in snap[key]["items"]:
                if e["date"] < self.day.isoformat():
                    self.note(f"{key} names a past date {e['date']} for {e['name']}")
        for line in snap["brief"]:
            if line["text"].startswith("Planned:") or " ago" in line["text"] or "since" in line["text"] or "last " in line["text"]:
                continue
            past = [d for d in dated(line["text"], self.day) if d < self.day]
            if past:
                self.note(f"the brief names a past day as upcoming: {line['text']!r}")
        for item in [got.get("/api/v1/today", {}).get("thing") or {}] + list(got.get("/api/v1/today", {}).get("others") or []):
            if item.get("date") and item["date"] < self.day.isoformat() and "need" not in item.get("text", ""):
                self.note(f"today's thing is dated in the past: {item}")
        # the badge counts what the Needs-you page lists
        needs = got.get("/api/v1/needs-you")
        if needs is not None and needs["total"] != snap["pending"]["confirmations"]:
            self.note(f"the badge says {snap['pending']['confirmations']} and the Needs-you page {needs['total']}: {needs['word']}")
        # after the matching job, no period is still "scheduled" long after its due date
        stuck = [p for p in payments if p.status == "scheduled" and p.due_date < self.day - timedelta(days=8)]
        if stuck:
            self.note(f"payments still scheduled long past due: {[(p.bill_id, p.due_date.isoformat()) for p in stuck][:5]}")
        # steps link to pages that exist
        for link in links_in(got.get("/api/v1/next_steps", {})) + links_in(snap["brief"]) + links_in(got.get("/api/v1/today", {})) + links_in(got.get("/api/v1/needs-you", {})):
            page = link.split("#")[0].split("?")[0]
            if page not in PAGES and page not in self.api_paths:
                self.note(f"a step links to {link}, which is no page")

    # -- the wider registers --

    def read_wide(self, got: dict[str, dict]) -> None:
        """The registers the first quarter did not reach, read (outside the audit count) and checked."""
        if not hasattr(self, "partner"):
            return
        owner = self.owner
        d = self.day
        wide: dict[str, dict] = {}
        for path in WIDE_ROUTES + (f"/api/v1/shared?person_id={self.partner_id}",):
            response = owner.get(path)
            if response.status_code != 200:
                self.note(f"{path} answered {response.status_code}: {response.text[:200]}")
                continue
            body = response.json()
            bad = walk(body)
            if bad:
                self.note(f"{path} holds {bad[:5]}")
            wide[path.split("?")[0]] = body
        snap = got.get("/api/v1/snapshot")
        with Session(engine) as session:
            accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
            goals = {g.id: g for g in session.exec(select(Goal)).all()}
            holdings = session.exec(select(Holding).where(Holding.account_id == self.remember["brokerage"])).all()
        owners_alone = {a.name for a in accounts if a.person_id is None and not a.shared}
        # The shared view: the shared accounts and the person's own, never one of the owner's alone; the person's own
        # page (Everything sees the owner's plan, and the shared page shows the shared accounts) the same.
        shared = wide.get("/api/v1/shared")
        if shared is not None:
            names = {a["name"] for a in shared["accounts"]}
            self.expect(names == {"Example Partner Checking", "Example Joint Savings"}, f"the shared view for the second person shows {sorted(names)}")
            self.expect(not (names & owners_alone), f"the shared view shows the owner's own {sorted(names & owners_alone)}")
            self.expect(all(b["account"] in names for b in shared["bills"]) and all(t["account"] in names for t in shared["recent"]), "the shared view lists a bill or a row of an account it does not show")
            theirs = self.partner.get("/api/v1/shared")
            self.expect(theirs.status_code == 200 and {a["name"] for a in theirs.json()["accounts"]} == {"Example Joint Savings"}, f"the person's own shared page: {theirs.text[:200]}")
        # The owner's safe to spend counts the owner's and the shared cash, never the second person's own.
        if snap is not None:
            expected_cash = round(sum(a.balance or 0.0 for a in accounts if (a.shared or a.person_id is None) and is_spendable(a)), 2)
            self.expect(abs(snap["safe_to_spend"]["parts"]["cash"] - expected_cash) <= 0.011, f"safe to spend's cash is {snap['safe_to_spend']['parts']['cash']}, the owner's and shared spendable cash {expected_cash}")
            theirs = self.partner.get("/api/v1/snapshot")
            self.expect(theirs.status_code == 200 and theirs.json()["safe_to_spend"] == snap["safe_to_spend"], "the Everything person's safe to spend differs from the owner's")
        # Goals: funded never beyond the target, never backwards without a tap; the sinking fund is its bill by the checks left.
        plan = wide.get("/api/v1/goals")
        if plan is not None:
            for g in plan["items"]:
                self.expect(g["funded"] <= g["target"] + 0.005, f"goal {g['name']} funded {g['funded']} beyond its target {g['target']}")
                self.expect(abs(g["remaining"] - max(0.0, g["target"] - g["funded"])) <= 0.011, f"goal {g['name']} remaining {g['remaining']} is not target less funded")
                was = self.goal_funded.get(g["id"])
                if was is not None and g["funded"] < was - 0.005 and not self.fed_today:
                    self.note(f"goal {g['name']} went back from {was} to {g['funded']} without a tap")
                self.goal_funded[g["id"]] = g["funded"]
        funds = wide.get("/api/v1/goals/sinking")
        if funds is not None:
            for f in funds["items"]:
                self.expect(f["paychecks_left"] == 0 or abs(f["per_paycheck"] - round(f["amount"] / f["paychecks_left"], 2)) <= 0.011, f"fund {f['name']}: {f['per_paycheck']} a check is not {f['amount']} over {f['paychecks_left']} checks")
                if f["goal_id"] in goals:
                    self.expect(abs(f["funded"] - round(goals[f['goal_id']].funded, 2)) <= 0.011, f"fund {f['name']} shows {f['funded']} funded, the goal {goals[f['goal_id']].funded}")
        # Investments: the holdings add up to the account.
        inv = wide.get("/api/v1/investments")
        if inv is not None:
            mine = next((i for i in inv["items"] if i["id"] == self.remember["brokerage"]), None)
            self.expect(mine is not None, "the brokerage account is gone from Investments")
            if mine is not None and holdings:
                self.expect(abs(mine["holdings_total"] - round(sum(h.value or 0.0 for h in holdings), 2)) <= 0.011, f"holdings total {mine['holdings_total']} is not the holdings' {[h.value for h in holdings]}")
                self.expect(abs((mine["balance"] or 0.0) - mine["holdings_total"]) <= 0.011, f"the brokerage balance {mine['balance']} is not its holdings {mine['holdings_total']}")
        # The tax center's totals are the sum of its lines.
        tax = wide.get("/api/v1/taxes")
        if tax is not None:
            for b in tax["businesses"]:
                self.expect(abs(b["costs"] - sum(r["amount"] for r in b["lines"])) <= 0.011, f"{b['business']} {b['year']}: costs {b['costs']} are not its lines {[(r['key'], r['amount']) for r in b['lines']]}")
                self.expect(abs(b["profit"] - (b["income"] - b["costs"])) <= 0.011, f"{b['business']}: profit {b['profit']} is not income {b['income']} less costs {b['costs']}")
            self.expect(abs(tax["profit"] - sum(b["profit"] for b in tax["businesses"])) <= 0.011 and abs(tax["se_tax"] - sum(b["se_tax"] for b in tax["businesses"])) <= 0.011, f"the tax center's profit {tax['profit']} or SE tax {tax['se_tax']} is not its businesses'")
            self.expect(abs(tax["itemizable"] - sum(c["total"] for c in tax["itemizable_categories"] if c["itemizable"])) <= 0.011, f"itemizable {tax['itemizable']} is not its categories")
        # Housing: each home's monthly total is its parts; the notice to leave is dated right until given, then good.
        housing = wide.get("/api/v1/housing")
        if housing is not None:
            for h in housing["homes"]:
                self.expect(abs(h["monthly_total"] - (h["to_landlord_monthly"] + h["own_bills_monthly"])) <= 0.011, f"{h['name']}: monthly total {h['monthly_total']} is not its parts")
                self.expect(abs(h["costs_total"] - sum(c["amount"] for c in h["costs"] if c["known"] and not c["paid"])) <= 0.011, f"{h['name']}: costs total {h['costs_total']} is not its costs")
            old = next((h for h in housing["homes"] if h["name"] == "Example Court 4"), None)
            if old is not None and "home" in self.remember:
                n = old["notice"]
                self.expect(n is not None and n["by"] == "2026-12-15", f"the notice to leave is {n}")
                if n is not None and d < date(2026, 12, 10):
                    self.expect(n["status"] in ("warn", "crit") and n.get("days_left") == (date(2026, 12, 15) - d).days, f"the notice before it is given: {n}")
                if n is not None and d >= date(2026, 12, 10):
                    self.expect(n["status"] == "good" and n["given_on"] == "2026-12-10", f"the notice after it is given: {n}")
                self.expect(old["state"] == ("ended" if d >= date(2027, 1, 15) else "current"), f"{old['name']} is {old['state']} on {d}")
        move = wide.get("/api/v1/move/plan")
        if move is not None and move["steps"]:
            self.expect(sum(move["counts"].values()) == move["total"] == len(move["steps"]), f"the move plan's counts {move['counts']} are not its {move['total']} steps")
            self.expect(move["next"] is None or move["next"]["id"] == next((s["id"] for s in move["steps"] if not s["done"]), None), "the move plan's next step is not the first undone")
            dates = [s["date"] for s in move["steps"] if s["date"]]
            self.expect(dates == sorted(dates), "the move plan's steps are out of date order")
            notice = next((s for s in move["steps"] if s["id"] == "notice"), None)
            if notice is not None and d >= date(2026, 12, 10):
                self.expect(notice["done"] and notice["done_by"] == "the app", f"the notice step after notice was given: {notice}")
        # Restore points: never more than the retention of each kind; the drill, once run, passed.
        points = restore.list_points()
        for kind, keep in restore.KEEP.items():
            n = len([p for p in points if p["kind"] == kind])
            self.expect(n <= keep, f"{n} restore points of kind {kind}, the retention {keep}")
        box = wide.get("/api/v1/restore-points/offbox")
        if box is not None:
            self.expect(len(box["copies"]) <= offbox.KEEP and not box["last_error"], f"the off-box copy: {box['copies']} {box['last_error']}")
            if box["drill"] is not None:
                self.expect(box["drill"]["ok"], f"the restore drill failed: {box['drill']}")
        # The recap's words follow its figures; the reviewer's runs all answered.
        recap = wide.get("/api/v1/recap")
        if recap is not None:
            for w in recap["windows"]:
                self.expect((w["status"] == "good") == (w["everyday_against_free"] <= 0), f"recap window {w['plan']['start']}: {w['status']} with {w['everyday_against_free']} over")
                self.expect(w["finished"] == (w["plan"]["end"] < d.isoformat()), f"recap window {w['plan']['start']} finished {w['finished']} on {d}")
        reviews = wide.get("/api/v1/reviews")
        if reviews is not None:
            self.expect(all(r["ok"] for r in reviews["reviews"]), f"a review failed: {[r['error'] for r in reviews['reviews'] if not r['ok']]}")
        # The month report's month is the sum of its days.
        report = wide.get("/api/v1/reports/spending")
        if report is not None:
            month = report["months"][-1]
            with Session(engine) as session:
                rows = session.exec(select(Transaction).where(Transaction.date >= d.replace(day=1), Transaction.date <= d)).all()
            by_day: dict[date, float] = {}
            income = 0.0
            for t in rows:
                if t.is_income:
                    income += -t.amount
                elif t.is_spending:
                    by_day[t.date] = by_day.get(t.date, 0.0) + t.amount
            self.expect(abs(month["spent"] - sum(by_day.values())) <= 0.011 and abs(month["income"] - income) <= 0.011, f"the month report says {month}, the days {round(sum(by_day.values()), 2)} spent and {round(income, 2)} in")
        if d in MONTH_ENDS:
            self.month_end()

    def month_end(self) -> None:
        """The week recap and the month report, against the ledger's days."""
        d = self.day
        with Session(engine) as session:
            week = week_recap(session, d)
            month = run_report(session, d, rows="category", period="month")
            rows = session.exec(select(Transaction).where(Transaction.date > d - timedelta(days=7), Transaction.date <= d)).all()
            spent = round(sum(t.amount for t in rows if t.is_spending and t.amount > 0), 2)
            self.expect(abs(week["spent"] - spent) <= 0.011 and sum(t["spent"] for t in week["top"]) <= spent + 0.011, f"the week recap says {week['spent']} out, the days {spent}")
            tax = self.owner.get("/api/v1/taxes/2026")
            self.expect(tax.status_code == 200, f"the 2026 tax center answered {tax.status_code}")
            if tax.status_code == 200 and d >= date(2026, 12, 31):  # the year's books, once the year's invoice, write-off and trip are in
                books = next((b for b in tax.json()["businesses"] if b["business"] == "Example Studio"), None)
                lines = {r["key"]: r["amount"] for r in books["lines"]} if books else {}
                self.expect(books is not None and books["income"] == 450.0 and lines.get("software") == 24.0 and lines.get("home_office", 0) > 0 and abs(lines.get("car", 0) - round(32.5 * 0.76, 2)) <= 0.011,
                            f"the 2026 books: {books and (books['income'], lines)}")
                self.expect(books is not None and abs(books["costs"] - sum(lines.values())) <= 0.011 and books["se_tax"] == 0.0, f"the 2026 books' costs {books and books['costs']} or SE tax {books and books['se_tax']}")
            self.expect(abs(month["total"] - sum(month["month_totals"])) <= 0.011 and abs(month["total"] - sum(i["total"] for i in month["items"])) <= 0.02 * max(1, len(month["items"])), f"the month report's total {month['total']} is not its months {month['month_totals']} or its rows")
            if d.month == 12:
                year = year_in_review(session, d, 2026)
                self.expect(abs(year["spent"] - sum(year["month_totals"])) <= 0.011 and abs(year["kept"] - (year["income"] - year["spent"])) <= 0.011, f"the year in review: {year['spent']} spent, months {year['month_totals']}, kept {year['kept']}")
                pack = self.owner.get("/api/v1/reports/custom/tax/pack", params={"year": 2026})
                self.expect(pack.status_code == 200 and pack.headers["content-type"].startswith("application/zip"), f"the tax pack answered {pack.status_code}")

    def audit_count(self) -> int:
        with Session(engine) as session:
            return len(session.exec(select(AuditEntry.id)).all())

    def snapshot_text(self) -> str:
        body = self.owner.get("/api/v1/snapshot").json()
        body.pop("fresh", None)
        return json.dumps(body, sort_keys=True)

    def day_cycle(self) -> dict[str, dict]:
        self.run_jobs()
        before = self.audit_count()
        extra = self.day.day % 5 == 0 or self.day in TAP_DAYS  # the wider set of screens, on some days, to stay quick
        got = self.read(extra=extra)
        want = AUDITED_READS + (EXTRA_AUDITED if extra else 0)
        if self.audit_count() - before != want:
            self.note(f"reading the screens wrote {self.audit_count() - before} audit rows, not {want}")
        self.check(got)
        return got

    def advance(self) -> dict[str, dict]:
        self.day += timedelta(days=1)
        routes.set_today_override(self.day)
        self.feed()
        return self.day_cycle()

    def same_again(self) -> None:
        """Running the whole day's jobs twice changes nothing."""
        first = self.snapshot_text()
        self.run_jobs()
        second = self.snapshot_text()
        if first != second:
            a, b = json.loads(first), json.loads(second)
            self.note(f"a second run of the day's jobs changed the snapshot: {[k for k in a if a[k] != b.get(k)]}")

    # -- the owner's taps --

    def expect(self, ok: bool, text: str) -> None:
        if not ok:
            self.note(text)

    def tap(self) -> None:
        d = self.day
        owner = self.owner
        if d == date(2026, 10, 19):
            # "I paid it" on the electricity bill due tomorrow; the bank's row for it lands tomorrow.
            with Session(engine) as session:
                bill = session.get(Bill, self.remember["electricity"]) if "electricity" in self.remember else session.exec(select(Bill).where(Bill.name == "Electricity")).one()
                payment = session.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == date(2026, 10, 20))).first()
            self.expect(payment is not None and payment.status == "scheduled", f"no scheduled electricity payment for Oct 20: {payment}")
            if payment is not None:
                r = owner.post(f"/api/v1/needs-you/payments/{payment.id}", json={"answer": "paid", "paid_on": "2026-10-19"})
                self.expect(r.status_code == 200, f"I paid it answered {r.status_code}: {r.text[:200]}")
                self.remember["electricity_payment"] = payment.id
        if d == date(2026, 10, 20):
            # The pending storage-unit series becomes a bill.
            with Session(engine) as session:
                series = session.exec(select(RecurringSeries).where(RecurringSeries.merchant == "Example Storage Unit")).one()
            self.expect(not series.confirmed, "the storage unit series was already confirmed")
            r = owner.post(f"/api/v1/recurring/{series.id}/classify", json={"kind": "bill", "due_day": 12})
            self.expect(r.status_code == 200, f"classify answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                self.remember["storage_bill"] = r.json()["bill"]["id"]
        if d == date(2026, 11, 7):
            # The pending cloud-storage notice (from Friday's list) is confirmed from Needs you: a new bill.
            with Session(engine) as session:
                fact = next((f for f in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all() if "Cloud Storage" in f.fields), None)
            self.expect(fact is not None, "no pending cloud storage fact to confirm")
            if fact is not None:
                r = owner.post("/api/v1/needs-you/facts", json={"ids": [fact.id], "action": "confirm"})
                self.expect(r.status_code == 200 and r.json()["done"] and not r.json()["failed"], f"confirm answered {r.status_code}: {r.text[:300]}")
                with Session(engine) as session:
                    bill = session.exec(select(Bill).where(Bill.name == "Example Cloud Storage")).first()
                self.expect(bill is not None and bill.active, "confirming the notice made no cloud storage bill")
                if bill is not None:
                    self.remember["cloud_bill"] = bill.id
        if d == date(2026, 10, 26):
            # A planned extra card payment, to post on Oct 28.
            r = owner.post("/api/v1/planned", json={"name": "Extra to Rewards Card", "amount": 100.0, "date": "2026-10-28", "kind": "card_payment", "to_account_id": self.remember["Rewards Card"]})
            self.expect(r.status_code == 200, f"planned answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                self.remember["planned"] = r.json()["id"]
        if d == date(2026, 11, 20):
            r = owner.delete(f"/api/v1/banks/{self.link_id}", params={"accounts": "keep"})
            self.expect(r.status_code == 200, f"removing the link answered {r.status_code}: {r.text[:200]}")
            with Session(engine) as session:
                active = [a for a in session.exec(select(Account)).all() if a.active and a.connector == "plaid"]
                links = session.exec(select(BankLink).where(BankLink.active == True)).all()  # noqa: E712
            self.expect(len(active) == 4, f"removing the link with keep left {len(active)} active accounts")
            self.expect(not links, "the removed link is still active")
        if d == date(2026, 11, 21):
            self.link_id = self.link()
        if d == date(2026, 12, 1):
            csv = "Date,Description,Amount\n2026-11-29,EXAMPLE DENTIST,120.00\n2026-11-30,EXAMPLE PHARMACY,23.50\n"
            r = owner.post("/api/v1/imports/file", files={"file": ("dentist.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"account_id": str(self.remember["checking"])})
            self.expect(r.status_code == 200 and r.json()["inserted"] == 2, f"the file import answered {r.status_code}: {r.text[:300]}")
            if r.status_code == 200:
                self.remember["import"] = r.json()["import_id"]
        if d == date(2026, 12, 2):
            r = owner.post(f"/api/v1/imports/{self.remember['import']}/undo")
            self.expect(r.status_code == 200 and r.json()["removed"] == 2, f"undo answered {r.status_code}: {r.text[:300]}")
            self.tx_floor -= 2  # the undo takes back exactly the two rows the import added
            with Session(engine) as session:
                left = session.exec(select(Transaction).where(Transaction.merchant.in_(["EXAMPLE DENTIST", "EXAMPLE PHARMACY", "Example Dentist", "Example Pharmacy"]))).all()  # type: ignore[attr-defined]
            self.expect(not left, f"the undone import left {len(left)} rows")
        if hasattr(self, "partner"):
            self.tap_wide()

    def tap_wide(self) -> None:
        d = self.day
        owner = self.owner
        if d == date(2026, 10, 15):
            # The business, and the home: the rent bill becomes the home lived in now, whose lease ends inside the
            # quarter; the next home starts that day with a move-in check; two utilities are the home's bills.
            r = owner.post("/api/v1/business", json={"name": "Example Studio", "kind": "sole_prop", "state": "TX", "started_on": "2026-01-10", "home_office_sqft": 100})
            self.expect(r.status_code == 200, f"the business answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                self.remember["business"] = next(b["id"] for b in r.json()["businesses"] if b["name"] == "Example Studio")
            with Session(engine) as session:
                rent = session.exec(select(Bill).where(Bill.name == "Rent", Bill.active == True)).one()  # noqa: E712
                electricity = session.exec(select(Bill).where(Bill.name == "Electricity", Bill.active == True)).one()  # noqa: E712
                internet = session.exec(select(Bill).where(Bill.name == "Internet", Bill.active == True)).one()  # noqa: E712
                self.remember["rent"], self.remember["electricity"], self.remember["internet"] = rent.id, electricity.id, internet.id
            r = owner.post("/api/v1/housing/adopt", json={"bill_id": rent.id, "name": "Example Court 4"})
            self.expect(r.status_code == 200, f"adopting the rent answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                home = next(h for h in r.json()["homes"] if h["name"] == "Example Court 4")
                self.remember["home"] = home["id"]
                r = owner.put(f"/api/v1/housing/{home['id']}", json={"name": "Example Court 4", "kind": "rent", "payee": "Example Property LLC", "starts_on": "2025-02-01", "ends_on": "2027-01-15", "lease_ends_on": "2027-01-15",
                                                                     "due_day": 1, "notice_days": 30, "deposit": 400.0, "pay_from_account_id": self.remember["checking"]})
                self.expect(r.status_code == 200, f"the home's lease answered {r.status_code}: {r.text[:200]}")
                for service, bill_id, provider, amount, due_day in (("electric", electricity.id, "Example Power", 142.0, 20), ("internet", internet.id, "Example Fiber", 85.0, 24)):
                    r = owner.post("/api/v1/utilities", json={"home_id": home["id"], "service": service, "provider": provider, "billed": "direct", "amount": amount, "estimate": False, "due_day": due_day, "moves_with_you": True, "bill_id": bill_id})
                    self.expect(r.status_code == 200, f"the {service} utility answered {r.status_code}: {r.text[:200]}")
                r = owner.post("/api/v1/housing", json={"name": "Example Flats 12", "kind": "rent", "payee": "Example Flats", "starts_on": "2027-01-15", "first_due": "2027-02-01", "due_day": 1, "notice_days": 60, "pay_from_account_id": self.remember["checking"]})
                self.expect(r.status_code == 200, f"the next home answered {r.status_code}: {r.text[:200]}")
                if r.status_code == 200:
                    new = next(h for h in r.json()["homes"] if h["name"] == "Example Flats 12")
                    self.remember["next_home"] = new["id"]
                    self.expect(owner.post(f"/api/v1/housing/{new['id']}/charges", json={"name": "Rent", "kind": "rent", "amount": 1295.0}).status_code == 200, "the next home's rent charge failed")
                    self.expect(owner.post(f"/api/v1/housing/{new['id']}/costs", json={"name": "Move-in check", "amount": 1920.0, "due_on": "2027-01-13"}).status_code == 200, "the move-in check failed")
        if d == date(2026, 10, 17):
            # The reviewer ran on the Oct 16 payday and proposed a buffer: the owner applies it.
            runs = owner.get("/api/v1/reviews").json()["reviews"]
            run = next((r for r in runs if r["ok"] and r["trigger"] == "payday"), None)
            self.expect(run is not None and run["notes"] and run["notes"][0]["action"] == {"type": "buffer", "value": 300.0}, f"no payday review with the buffer note: {runs[:2]}")
            if run is not None:
                r = owner.post(f"/api/v1/reviews/{run['id']}/notes/0/apply")
                self.expect(r.status_code == 200 and r.json()["note"]["status"] == "applied", f"applying the note answered {r.status_code}: {r.text[:200]}")
                self.remember["buffer_on"] = 1
        if d in PAYDAYS_AFTER_FIRST:
            for key, amount in (("goal", 100.0), ("fund", 30.0)):
                r = owner.post(f"/api/v1/goals/{self.remember[key]}/fund", json={"amount": amount})
                self.expect(r.status_code == 200, f"funding the {key} answered {r.status_code}: {r.text[:200]}")
            self.fed_today = True
        if d == date(2026, 11, 2):
            r = owner.post(f"/api/v1/business/{self.remember['business']}/invoices", json={"direction": "out", "counterparty": "Example Client", "issued_on": "2026-11-02", "due_on": "2026-11-30", "lines": [{"what": "Design work", "quantity": 10, "price": 45.0}], "status": "sent"})
            self.expect(r.status_code == 200 and r.json()["amount"] == 450.0, f"the invoice answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                self.remember["invoice"] = r.json()["id"]
        if d in (date(2026, 11, 3), date(2026, 12, 3)):
            csv = f"Date,Description,Amount\n{d.year}-{d.month:02d}-01,EXAMPLE PARTNER GROCER,54.10\n{d.year}-{d.month:02d}-02,EXAMPLE PARTNER FUEL,41.00\n"
            r = owner.post("/api/v1/imports/file", files={"file": ("partner.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"account_id": str(self.remember["theirs"])})
            self.expect(r.status_code == 200 and r.json()["inserted"] == 2, f"the partner's import answered {r.status_code}: {r.text[:300]}")
        if d == date(2026, 11, 4):
            with Session(engine) as session:
                row = session.exec(select(Transaction).where(Transaction.merchant == "Example Cloud Hosting")).first()
            self.expect(row is not None, "the cloud hosting row did not land")
            if row is not None:
                r = owner.post("/api/v1/business/mark", json={"ids": [row.id], "business_id": self.remember["business"], "tax_line": "software"})
                self.expect(r.status_code == 200 and r.json()["marked"] == 1, f"marking the write-off answered {r.status_code}: {r.text[:200]}")
                self.remember["write_off"] = row.id
        if d == date(2026, 11, 5):
            r = owner.post(f"/api/v1/business/{self.remember['business']}/trips", json={"day": "2026-11-05", "miles": 32.5, "purpose": "Client meeting", "start": "Home", "end": "Example Client"})
            self.expect(r.status_code == 200 and r.json()["miles"] == 32.5, f"the trip answered {r.status_code}: {r.text[:200]}")
        if d == date(2026, 11, 10):
            r = owner.post("/api/v1/scenarios/run", json={"name": "Example Flats", "move_date": "2027-01-15", "new_rent": 1350.0, "one_offs": [{"name": "Deposit", "amount": 400.0, "date": "2027-01-13"}]})
            self.expect(r.status_code == 200, f"the scenario answered {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                s = r.json()
                path = s["scenario"]["path"]
                self.expect(abs(s["scenario"]["low"]["value"] - min(p["at_pace"] for p in path)) <= 0.011, f"the scenario's low {s['scenario']['low']} is not its path's")
                self.expect((s["verdict"] == "passes") == (s["scenario"]["low"]["value"] >= 0), f"the verdict {s['verdict']} with a low of {s['scenario']['low']['value']}")
                self.expect(s["first_passes"] is None or s["first_passes"] >= s["move_date"], f"first passes {s['first_passes']} before the move {s['move_date']}")
                self.expect(all(abs(w["delta"] - (w["total"] - w["baseline_total"])) <= 0.011 for w in s["windows"]), "a scenario window's delta is not its total less the baseline")
                self.expect(abs(s["monthly_change"] - (1350.0 - 1295.0)) <= 0.011 and s["one_offs_total"] == 400.0, f"the scenario's monthly change {s['monthly_change']} or one-offs {s['one_offs_total']}")
        if d == date(2026, 11, 15):
            csv = "Symbol,Name,Shares,Price,Market Value,Type\nEXTGT,Example Target Fund,40,100.00,4000.00,Mutual Fund\nEXBND,Example Bond Fund,12,100.25,1203.00,Bond\n"
            r = owner.post(f"/api/v1/imports/holdings/{self.remember['brokerage']}", files={"file": ("holdings.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"as_of": d.isoformat()})
            self.expect(r.status_code == 200 and r.json()["holdings"] == 2 and r.json()["total"] == 5203.0, f"the holdings file answered {r.status_code}: {r.text[:300]}")
        if d == date(2026, 11, 19):
            with Session(engine) as session:
                row = session.exec(select(Transaction).where(Transaction.merchant == "Example Client")).first()
            self.expect(row is not None, "the client's payment did not land")
            if row is not None:
                r = owner.post(f"/api/v1/business/invoices/{self.remember['invoice']}/paid", json={"transaction_id": row.id})
                self.expect(r.status_code == 200 and r.json()["status"] == "paid" and r.json()["paid_on"] == "2026-11-18", f"the invoice paid answered {r.status_code}: {r.text[:200]}")
        if d == date(2026, 11, 23):
            # The phone bill's due day, moved to the 5th from December: the check first, then the biller's yes.
            with Session(engine) as session:
                phone = session.exec(select(Bill).where(Bill.name == "Phone", Bill.active == True)).one()  # noqa: E712
            self.remember["phone"] = phone.id
            r = owner.get(f"/api/v1/bills/{phone.id}/move_check", params={"day": 5})
            self.expect(r.status_code == 200 and r.json()["from_day"] == 22 and r.json()["to_day"] == 5 and abs(r.json()["change"] - (r.json()["low_after"] - r.json()["low_before"])) <= 0.011, f"the move check answered {r.status_code}: {r.text[:300]}")
            r = owner.put(f"/api/v1/bills/{phone.id}", json={"due_day": 5})
            self.expect(r.status_code == 200 and r.json()["due_day"] == 5, f"moving the phone bill answered {r.status_code}: {r.text[:200]}")
        if d == date(2026, 12, 10):
            r = owner.post(f"/api/v1/housing/{self.remember['home']}/notice", json={"given_on": "2026-12-10"})
            self.expect(r.status_code == 200, f"notice given answered {r.status_code}: {r.text[:200]}")

    def persisted(self) -> None:
        """What a tap did is still so on later days."""
        d = self.day
        with Session(engine) as session:
            if "electricity_payment" in self.remember and d >= date(2026, 10, 20):
                p = session.get(Payment, self.remember["electricity_payment"])
                self.expect(p is not None and p.status == "paid", f"the electricity payment marked paid by hand is now {p.status if p else 'gone'}")
                bill = session.get(Bill, self.remember["electricity"]) if "electricity" in self.remember else session.exec(select(Bill).where(Bill.name == "Electricity")).one()
                twins = session.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date >= date(2026, 10, 15), Payment.due_date <= date(2026, 10, 25))).all()
                self.expect(len(twins) == 1, f"electricity holds {len(twins)} payments around Oct 20 after the bank row landed")
            if "storage_bill" in self.remember:
                bill = session.get(Bill, self.remember["storage_bill"])
                self.expect(bill is not None and bill.active and bill.name == "Example Storage Unit", "the storage unit bill the owner named is gone")
                if d >= date(2026, 11, 13) and bill is not None:
                    p = session.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == date(2026, 11, 12))).first()
                    self.expect(p is not None and p.status == "paid", f"the storage unit's Nov 12 period is {p.status if p else 'missing'} after its row landed")
            if "cloud_bill" in self.remember:
                bill = session.get(Bill, self.remember["cloud_bill"])
                self.expect(bill is not None and bill.active, "the cloud storage bill from the confirmed notice is gone")
                if d >= date(2026, 11, 10) and bill is not None:
                    p = session.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == date(2026, 11, 9))).first()
                    self.expect(p is not None and p.status == "paid", f"cloud storage's Nov 9 period is {p.status if p else 'missing'} after its row landed")
            if "planned" in self.remember:
                p = session.get(PlannedPayment, self.remember["planned"])
                if d < date(2026, 10, 28):
                    self.expect(p is not None and not p.done, "the planned payment settled before its transaction arrived")
                if d >= date(2026, 10, 28):
                    self.expect(p is not None and p.done and p.transaction_id is not None, f"the planned card payment did not settle on its transaction: {p}")
            if d >= date(2026, 11, 21):
                active = [a for a in session.exec(select(Account)).all() if a.active and a.connector == "plaid"]
                self.expect(len(active) == 4, f"{len(active)} active accounts after the link was removed and linked again")
            if hasattr(self, "partner"):
                self.persisted_wide(session)

    def persisted_wide(self, session: Session) -> None:
        d = self.day
        if "buffer_on" in self.remember:
            self.expect(float(get_setting(session, "buffer", 0.0)) == 300.0, f"the buffer the owner applied is {get_setting(session, 'buffer', None)}")
        if "home" in self.remember:
            home = session.get(Home, self.remember["home"])
            rent = session.get(Bill, self.remember["rent"])
            self.expect(home is not None and home.active and home.bill_id == rent.id and rent.active and rent.home_id == home.id and rent.name == "Rent · Example Court 4", f"the home and its rent: {home} {rent}")
            for bill_id, name in ((self.remember["electricity"], "Electricity · Example Power"), (self.remember["internet"], "Internet · Example Fiber")):
                b = session.get(Bill, bill_id)
                self.expect(b is not None and b.active and b.name == name and b.utility_id is not None and b.ends_on is None, f"the utility's bill: {b}")
            for due in (date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)):
                if d >= due + timedelta(days=2):
                    p = session.exec(select(Payment).where(Payment.bill_id == rent.id, Payment.due_date == due)).first()
                    self.expect(p is not None and p.status == "paid", f"the rent of {due} is {p.status if p else 'missing'} after its row landed")
            if d >= date(2026, 12, 10):
                self.expect(home.notice_given_on == date(2026, 12, 10), f"the notice given is {home.notice_given_on}")
            if d >= date(2027, 1, 15):
                check = session.exec(select(Bill).where(Bill.home_id == self.remember["next_home"], Bill.name == "Move-in check")).first()
                p = session.exec(select(Payment).where(Payment.bill_id == check.id, Payment.due_date == date(2027, 1, 13))).first() if check else None
                self.expect(p is not None and p.status == "paid", f"the move-in check is {p.status if p else 'missing'} after its row landed")
        if "write_off" in self.remember:
            t = session.get(Transaction, self.remember["write_off"])
            self.expect(t is not None and t.business_id == self.remember["business"] and t.tax_line == "software", f"the marked write-off: {t}")
        if "invoice" in self.remember and d >= date(2026, 11, 19):
            i = session.get(Invoice, self.remember["invoice"])
            self.expect(i is not None and i.status == "paid" and i.transaction_id is not None, f"the paid invoice: {i}")
        if d >= date(2026, 11, 5):
            self.expect(len(session.exec(select(Trip)).all()) == 1, "the trip is gone")
        if d >= date(2026, 11, 15):
            self.expect(len(session.exec(select(Holding).where(Holding.account_id == self.remember["brokerage"])).all()) == 2, "the holdings from the file are gone")
        fed = sum(1 for p in PAYDAYS_AFTER_FIRST if p <= d)
        self.expect(session.get(Goal, self.remember["goal"]).funded == 100.0 * fed and session.get(Goal, self.remember["fund"]).funded == 30.0 * fed, f"the goal and the fund after {fed} paydays: {session.get(Goal, self.remember['goal']).funded} {session.get(Goal, self.remember['fund']).funded}")
        if "phone" in self.remember:
            phone = session.get(Bill, self.remember["phone"])
            self.expect(phone.due_day == 5, f"the phone bill's day is {phone.due_day} after the move")
            for due in (date(2026, 12, 5), date(2027, 1, 5)):
                if d >= due + timedelta(days=2):
                    p = session.exec(select(Payment).where(Payment.bill_id == phone.id, Payment.due_date == due)).first()
                    self.expect(p is not None and p.status == "paid", f"the phone bill of {due} is {p.status if p else 'missing'} after its row landed on the moved day")
            stuck = [p for p in session.exec(select(Payment).where(Payment.bill_id == phone.id)).all() if p.due_date >= date(2026, 11, 23) and p.due_date.day != 5]
            self.expect(not stuck, f"the phone bill still holds periods on the old day: {[(p.due_date.isoformat(), p.status) for p in stuck]}")


@pytest.fixture
def quarter(owner: TestClient, tmp_path) -> Quarter:  # noqa: ANN001
    (tmp_path / "offbox").mkdir(exist_ok=True)
    q = Quarter(owner, str(tmp_path / "offbox"))
    yield q
    CONNECTORS["plaid"].client_factory = None
    roles.set_override("reviewer", None)
    from app.mail.imap import ImapClient

    CONNECTORS["mail"].client_factory = ImapClient


TAP_DAYS = {date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 26), date(2026, 11, 7), date(2026, 11, 20), date(2026, 11, 21), date(2026, 12, 1), date(2026, 12, 2),
            date(2026, 10, 15), date(2026, 10, 17), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 10), date(2026, 11, 15), date(2026, 11, 19), date(2026, 11, 23),
            date(2026, 12, 3), date(2026, 12, 10), *PAYDAYS_AFTER_FIRST}
TWICE_DAYS = {date(2026, 10, 16), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 27), date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 8)}
WIDE_DAYS = {date(2026, 10, 15), date(2026, 10, 17), date(2026, 11, 4), date(2026, 11, 15), date(2026, 11, 19), date(2026, 11, 23), date(2026, 12, 10), date(2026, 12, 11), date(2027, 1, 15)}  # the wider registers, read after the taps that change them


def test_a_simulated_quarter_keeps_every_invariant(quarter: Quarter):
    for _ in range(DAYS):
        got = quarter.advance()
        if quarter.day in TAP_DAYS:
            quarter.tap()
            got = quarter.day_cycle()
        quarter.persisted()
        if quarter.day.day % 10 == 0 or quarter.day in WIDE_DAYS | MONTH_ENDS:
            quarter.read_wide(got)
        if quarter.day in TWICE_DAYS or (quarter.day in TAP_DAYS and quarter.day.day % 2 == 0):
            quarter.same_again()
    with Session(engine) as session:
        n_tx = len(session.exec(select(Transaction.id)).all())
        paid = len(session.exec(select(Payment).where(Payment.status == "paid")).all())
    assert n_tx > 600 and paid > 60, (n_tx, paid)  # the quarter was fed: the ledger grew and its bills were matched
    assert not quarter.findings, "\n".join(quarter.findings)


# ---- the findings, reduced ---------------------------------------------------------------------------------


def test_an_advance_taken_back_on_a_past_payday_is_not_due_again(owner: TestClient):
    """Found on day 3 of the quarter (2026-10-17): the two advances repaid on the Oct 16 payday still sat under
    "advances" dated Oct 16, took $231 off safe to spend for two weeks after the bank balance already lacked it,
    and the brief said they would repay "as the paycheck lands" the day after it had landed."""
    from app.engine.household import seed as seed_household
    from app.engine.projection import build_events
    from app.engine.snapshot import finance_snapshot
    from app.models import Advance

    with Session(engine) as session:
        seed_household(session)
        advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
        assert {a.repay_date for a in advances} == {date(2026, 10, 16)}
        assert len(build_events([], [], advances, date(2026, 10, 16))) == 2  # due today: counted
        assert build_events([], [], advances, date(2026, 10, 17)) == []  # taken back yesterday: not again
        snap = finance_snapshot(session, date(2026, 10, 17))
    assert snap["advances"] == {"total": 0.0, "items": []}
    assert snap["safe_to_spend"]["parts"]["advances"] == 0.0 and snap["safe_to_spend"]["parts"]["advances_on_payday"] == 0.0
    assert not any("advance" in line["text"] and "repay" in line["text"] for line in snap["brief"])


def test_the_badge_counts_the_past_due_periods_the_page_lists(owner: TestClient):
    """Found on 2026-10-13 and after: the Needs-you page asked about a period 55 days past due (its list reaches
    90 days back), while the badge counted the payments register's 45-day span, so the badge said 4 and the page
    "5 things wait here"."""
    from app.engine.household import seed as seed_household
    from app.engine.payments import match_payments

    with Session(engine) as session:
        seed_household(session)
        match_payments(session, date(2026, 10, 14))
        for today in (date(2026, 10, 14), date(2026, 10, 22), date(2026, 11, 20), date(2026, 12, 1)):
            routes.set_today_override(today)
            snap = owner.get("/api/v1/snapshot").json()
            page = owner.get("/api/v1/needs-you").json()
            assert snap["pending"]["confirmations"] == page["total"], (today, snap["pending"], page["word"])
            assert snap["pending"]["past_due"] == len(page["past_due"]), (today, snap["pending"], page["word"])
