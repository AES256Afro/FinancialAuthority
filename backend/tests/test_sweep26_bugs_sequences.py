"""Sweep 26: long random sequences of what a person does, through the HTTP API on the synthetic household, and after
every step what must still be true: a figure agrees with itself from one page to the next, undo is exact, nothing counts
twice, every source resolves, no call answers 500, and a read changes nothing. Earlier rounds checked definitions, hand
models, fuzzed single inputs and ran a calendar forward; none drove a sequence and then checked.

hypothesis is not installed, so the driver is seeded: an action is a rule's name and a few integers, each integer read
modulo what exists at that moment, so any action can run after any other. That is what makes a failure shrink: drop
actions from the list and replay it from a fresh household until nothing more can go (ddmin). A failing sequence reads
as a list of plain tuples.

To hunt: FA_SEQ_SEQUENCES=200 FA_SEQ_STEPS=60 FA_SEQ_SEED=1000 pytest -q tests/test_sweep26_bugs_sequences.py -k driver
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app import auth
from app.api import routes
from app.db import engine, init_db, session_scope
from app.engine.household import seed
from app.engine.payments import match_payments
from app.jobs.detect import run_detection
from app.main import app
from app.models import Account, Bill, ExtractedFact, MailAccount, MessageRef, Payment, Transaction, TransactionSplit
from conftest import TODAY

CENT = 0.011
Action = tuple  # (rule name, int, int, int)

# Every page the owner reads: compared in full, not sampled. The figures of one engine, however many screens show them.
PAGES = [
    "snapshot", "bills", "incomes", "debts", "debts/payoff", "borrowing", "borrowing/exit", "borrowing/plan", "budgets", "needs-you", "reports/spending",
    "reports/custom/year", "accounts/overview", "accounts", "goals", "goals/sinking", "facts", "payments", "pairs", "recurring", "data-check", "cover", "saving",
    "today", "glance", "checkin", "paychecks", "recap", "watchlist", "next_steps", "scenarios/readiness", "credit", "planned", "assets", "watch", "projections",
    "firstrun", "suggestions", "housing", "utilities", "moves", "move/plan", "investments",
]
# The pages an undo need not return: the record that it happened, which is the point of them.
HISTORY_TABLES = {"audit_entries", "job_runs", "web_sessions", "health_samples", "oauth_clients", "oauth_codes", "oauth_tokens"}


TALLY: dict[str, int] = {}  # what the rules really did, for the hunter: a rule that always answers 400 tests nothing
TALLY_WHY: dict[str, str] = {}


class Broke(AssertionError):
    """An invariant failed: its name and the detail."""

    def __init__(self, name: str, detail: str):
        super().__init__(f"{name}: {detail}")
        self.name = name
        self.detail = detail


def drop_clock(value: Any) -> Any:
    """A page as the engine figures it: what only says when it was read goes, and so does the time of a row."""
    if isinstance(value, dict):
        return {k: drop_clock(v) for k, v in value.items() if k not in {"created_at", "resolved_at", "updated_at", "fetched_at", "at", "taken_at"}}
    if isinstance(value, list):
        return [drop_clock(v) for v in value]
    return value


def drop_ids(value: Any) -> Any:
    """A page without what only names a row: the same figures on rows made again carry new ids."""
    if isinstance(value, dict):
        return {k: drop_ids(v) for k, v in value.items() if k not in {"id", "source", "sources", "sourcelink", "evidence", "key", "per_day_by_account", "fixed", "last", "import_id"} and not k.endswith("_id")}
    if isinstance(value, list):
        return [drop_ids(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\b(bill|debt|income|plan|advance|account|transaction|tx|paystub|fact|goal|payment|series):\d+", r"\1:#", value)
    return value


def periods_in_order(value: Any) -> Any:
    """A bill's periods as figures: one made again after its day moved and moved back holds the same date and amount under a new row
    number, and two on one day may trade places. That row number names a period; no figure stands on it. A bill's series_id is the same:
    detection links a series only of the bill's own name, so the link matches what the name already matches, and it outlives an undo."""
    if isinstance(value, dict):
        return {k: periods_in_order(v) for k, v in value.items() if k not in ("payment_id", "series_id")}
    if isinstance(value, list):
        rows = [periods_in_order(v) for v in value]
        if rows and all(isinstance(r, dict) and "due_date" in r and "bill_id" in r for r in rows):
            return sorted(rows, key=lambda r: (r["due_date"], r["bill_id"]))
        return rows
    return re.sub(r"\bpayment:\d+", "payment:#", value) if isinstance(value, str) else value


def diff(a: Any, b: Any, path: str = "", out: list[str] | None = None, limit: int = 12) -> list[str]:
    """Where two pages differ, every leaf: the failure names the figure, not the page."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: {'gone' if k in a else 'new'} {json.dumps(a.get(k, b.get(k)), default=str)[:120]}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} rows, then {len(b)}: {json.dumps(a, default=str)[:300]} -> {json.dumps(b, default=str)[:300]}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {json.dumps(a, default=str)[:160]} -> {json.dumps(b, default=str)[:160]}")
    return out


def tables(skip: set[str] = HISTORY_TABLES) -> dict[str, list]:
    """Every row of every table, by SQL, for the comparisons a page could hide."""
    with engine.connect() as c:
        return {t.name: [tuple(str(x) for x in row) for row in c.execute(text(f'SELECT * FROM "{t.name}" ORDER BY rowid')).fetchall()]
                for t in SQLModel.metadata.sorted_tables if t.name not in skip}


def fresh_household() -> TestClient:
    SQLModel.metadata.drop_all(engine)
    init_db()
    auth.reset_lockouts()
    routes.set_today_override(TODAY)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    client.headers["X-Requested-With"] = "FinancialAuthority"
    with session_scope() as session:
        seed(session)
        session.add(MailAccount(id=1, label="Fictional mail", host="imap.example.test", username="owner@example.test"))
        session.commit()
        run_detection(session, TODAY)
        match_payments(session, TODAY)
    return client


class World:
    def __init__(self, client: TestClient):
        self.c = client
        self.calls = 0
        self.uid = 0
        self.names = 0
        self.taken: list[str] = []
        self.csv_n = 0

    # ---- calling the API ----------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, **kw: Any):  # noqa: ANN202
        self.calls += 1
        r = self.c.request(method, path if path.startswith("/api") else f"/api/v1/{path}", **kw)
        if method != "GET":
            key = f"{method} {re.sub(r'[0-9]+', '#', path)[:40]} {r.status_code}"
            TALLY[key] = TALLY.get(key, 0) + 1
            if r.status_code >= 400:
                TALLY_WHY[key] = r.text[:150]
        if r.status_code >= 500:
            raise Broke("no 500", f"{method} {path} {kw.get('json') or ''} answered {r.status_code}: {r.text[:300]}")
        return r

    def get(self, path: str) -> dict:
        r = self._call("GET", path)
        if r.status_code != 200:
            raise Broke("every page answers", f"GET {path} answered {r.status_code}: {r.text[:300]}")
        return r.json()

    def post(self, path: str, body: dict | None = None):  # noqa: ANN201
        return self._call("POST", path, json=body or {})

    def put(self, path: str, body: dict | None = None):  # noqa: ANN201
        return self._call("PUT", path, json=body or {})

    def delete(self, path: str):  # noqa: ANN201
        return self._call("DELETE", path)

    def read(self) -> dict[str, Any]:
        return {p: drop_clock(self.get(p)) for p in PAGES}

    # ---- what exists now, for an action to pick from ------------------------------------------------------------

    def rows(self, model: Any, *where: Any) -> list:
        with Session(engine) as s:
            rows = s.exec(select(model).where(*where).order_by(model.id)).all()
            s.expunge_all()
            return list(rows)

    @staticmethod
    def pick(rows: list, i: int):  # noqa: ANN205
        return rows[i % len(rows)] if rows else None

    def name(self, stem: str) -> str:
        self.names += 1
        return f"{stem} {self.names}"

    # ---- settle: what the nightly jobs do ------------------------------------------------------------------------

    def settle(self) -> None:
        with session_scope() as s:
            run_detection(s, TODAY)
            match_payments(s, TODAY)
            s.commit()

    # ---- bills -----------------------------------------------------------------------------------------------------

    def add_bill(self, a: int, b: int, c: int) -> None:
        accounts = self.rows(Account, Account.active == True)  # noqa: E712
        cadence = ["monthly", "monthly", "monthly", "weekly", "biweekly", "quarterly", "yearly"][a % 7]
        body: dict[str, Any] = {"name": self.name("Sample bill"), "amount": 5 + b % 300, "due_day": 1 + c % 28, "cadence": cadence, "kind": ["bill", "subscription"][a % 2]}
        if cadence in ("weekly", "biweekly", "quarterly", "yearly"):
            body["anchor_date"] = (TODAY + timedelta(days=b % 40)).isoformat()
        if b % 3 == 0 and accounts:
            body["pay_from_account_id"] = self.pick(accounts, c).id
        self.post("bills", body)

    def edit_bill(self, a: int, b: int, c: int) -> None:
        bill = self.pick(self.rows(Bill, Bill.active == True), a)  # noqa: E712
        if bill is None:
            return
        accounts = self.rows(Account, Account.active == True)  # noqa: E712
        change: dict[str, Any] = [
            {"amount": 1 + b % 500}, {"due_day": 1 + b % 31}, {"autopay": bool(b % 2)}, {"category": ["Insurance", "Bills & Utilities", "Rent"][b % 3]},
            {"pay_from_account_id": self.pick(accounts, b).id if accounts else None}, {"pay_from_account_id": None}, {"kind": ["bill", "subscription"][b % 2]},
            {"cadence": "quarterly", "anchor_date": (TODAY + timedelta(days=b % 90)).isoformat()}, {"cadence": "monthly", "anchor_date": None},
        ][c % 9]
        self.put(f"bills/{bill.id}", change)

    def delete_bill(self, a: int, b: int, c: int) -> None:
        bill = self.pick(self.rows(Bill, Bill.active == True), a)  # noqa: E712
        if bill is not None:
            self.put(f"bills/{bill.id}", {"active": False})

    def bring_back_bill(self, a: int, b: int, c: int) -> None:
        bill = self.pick(self.rows(Bill, Bill.active == False), a)  # noqa: E712
        if bill is not None:
            self.put(f"bills/{bill.id}", {"active": True})

    # ---- incomes ---------------------------------------------------------------------------------------------------

    def add_income(self, a: int, b: int, c: int) -> None:
        cadence = ["biweekly", "weekly", "semimonthly", "monthly"][a % 4]
        self.post("incomes", {"name": self.name("Sample employer"), "cadence": cadence, "expected_net": 100 + b % 3000, "next_date": (TODAY + timedelta(days=1 + c % 30)).isoformat()})

    def edit_income(self, a: int, b: int, c: int) -> None:
        from app.models import Income

        income = self.pick(self.rows(Income, Income.active == True), a)  # noqa: E712
        if income is None:
            return
        change: dict[str, Any] = [{"expected_net": 100 + b % 3000}, {"next_date": (TODAY + timedelta(days=b % 20)).isoformat()}, {"cadence": ["biweekly", "weekly", "semimonthly", "monthly"][b % 4]}, {"expected_net_end": 200 + b % 2000}][c % 4]
        self.put(f"incomes/{income.id}", change)

    def delete_income(self, a: int, b: int, c: int) -> None:
        from app.models import Income

        income = self.pick(self.rows(Income, Income.active == True), a)  # noqa: E712
        if income is not None:
            self.put(f"incomes/{income.id}", {"active": False})

    # ---- accounts, debts and balances ------------------------------------------------------------------------------

    def add_account(self, a: int, b: int, c: int) -> None:
        kind = ["card", "loan", "checking", "savings", "cash"][a % 5]
        self.post("accounts", {"name": self.name(f"Sample {kind}"), "kind": kind, "institution": "Example Bank", "balance": b % 5000, "credit_limit": 1000 + c % 9000 if kind == "card" else None})

    def set_balance(self, a: int, b: int, c: int) -> None:
        """The owner types a balance: the same manual account, named again with a new figure."""
        manual = self.rows(Account, Account.connector == "manual", Account.active == True, Account.kind != "investment")  # noqa: E712
        account = self.pick(manual, a)
        if account is None:
            return
        self.post("accounts", {"name": account.name, "kind": account.kind, "institution": account.institution, "balance": b % 6000})

    def edit_debt(self, a: int, b: int, c: int) -> None:
        from app.models import Debt

        debt = self.pick(self.rows(Debt), a)
        if debt is None:
            return
        change: dict[str, Any] = [{"apr": b % 40}, {"minimum": b % 200}, {"due_day": 1 + b % 28}, {"statement_day": 1 + b % 28}, {"pay_in_full": bool(b % 2)}, {"credit_limit": 500 + b % 5000}, {"minimum": None}, {"name": self.name("Sample debt")}][c % 8]
        self.put(f"debts/{debt.id}", change)

    def add_investment(self, a: int, b: int, c: int) -> None:
        self.post("investments", {"name": self.name("Sample brokerage"), "institution": "Example Funds", "subtype": ["401k", "brokerage", "ira"][a % 3], "balance": 100 + b % 20000})

    def delete_investment(self, a: int, b: int, c: int) -> None:
        account = self.pick(self.rows(Account, Account.kind == "investment", Account.active == True), a)  # noqa: E712
        if account is not None:
            self.delete(f"investments/{account.id}")

    def add_asset(self, a: int, b: int, c: int) -> None:
        self.post("assets", {"name": self.name("Sample car"), "kind": "car", "value": 1000 + b % 20000, "owed": c % 900})

    # ---- budgets and goals -----------------------------------------------------------------------------------------

    CATEGORIES = ["Groceries", "Dining", "Shopping", "Gas", "Entertainment"]

    def put_budget(self, a: int, b: int, c: int) -> None:
        self.put(f"budgets/{self.CATEGORIES[a % 5]}", {"per_paycheck": 10 + b % 400, "rollover": ["next", "none"][c % 2]})

    def delete_budget(self, a: int, b: int, c: int) -> None:
        self.delete(f"budgets/{self.CATEGORIES[a % 5]}")

    def add_goal(self, a: int, b: int, c: int) -> None:
        self.post("goals", {"name": self.name("Sample goal"), "kind": "save", "target": 100 + b % 5000, "pace": a % 120, "priority": 1 + c % 5})

    def edit_goal(self, a: int, b: int, c: int) -> None:
        from app.models import Goal

        goal = self.pick(self.rows(Goal, Goal.active == True), a)  # noqa: E712
        if goal is None:
            return
        self.put(f"goals/{goal.id}", [{"pace": b % 200}, {"target": 50 + b % 4000}, {"funded": b % 300}, {"priority": 1 + b % 5}][c % 4])

    def fund_goal(self, a: int, b: int, c: int) -> None:
        from app.models import Goal

        goal = self.pick(self.rows(Goal, Goal.active == True), a)  # noqa: E712
        if goal is not None:
            self.post(f"goals/{goal.id}/fund", {"amount": 1 + b % 500})

    def delete_goal(self, a: int, b: int, c: int) -> None:
        from app.models import Goal

        goal = self.pick(self.rows(Goal, Goal.active == True), a)  # noqa: E712
        if goal is not None:
            self.delete(f"goals/{goal.id}")

    # ---- fictional mail --------------------------------------------------------------------------------------------

    def _mail(self, kind: str, fields: dict) -> int:
        """A message arrives and the classifier files what it said, waiting for the owner."""
        self.uid += 1
        with session_scope() as s:
            ref = MessageRef(account_id=1, folder="INBOX", uid=self.uid, sender="billing@example.test", sender_name=fields.get("sender_name", "Example Sender"),
                             subject=f"Fictional notice {self.uid}", date=datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc), trust="ok", kind=kind)
            s.add(ref)
            s.flush()
            fact = ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps(fields), confidence=0.9, excerpt="fictional")
            s.add(fact)
            s.commit()
            return fact.id

    def _fact_fields(self, a: int, b: int, c: int) -> tuple[str, dict]:
        which = a % 7
        due = (TODAY + timedelta(days=c % 40 - 5)).isoformat()
        bills = self.rows(Bill, Bill.active == True)  # noqa: E712
        if which == 0:  # a new biller
            return "bill_due", {"biller": self.name("Sample biller"), "sender_name": "Sample biller", "amount": 10 + b % 200, "due_date": due, "message_date": "2026-10-12"}
        if which == 1 and bills:  # the bill already on file
            return "bill_due", {"biller": self.pick(bills, b).name, "sender_name": self.pick(bills, b).name, "amount": 10 + b % 200, "due_date": due, "message_date": "2026-10-12"}
        if which == 2:
            return "past_due", {"biller": self.name("Sample overdue"), "sender_name": "Sample overdue", "amount": 10 + b % 90, "due_date": (TODAY - timedelta(days=3 + c % 9)).isoformat(), "message_date": "2026-10-12"}
        if which == 3:
            stub_day = (TODAY - timedelta(days=c % 14)).isoformat()
            return "paystub", {"employer": "Example Employer", "net": 2000 + b % 500, "gross": 3000, "pay_date": stub_day, "period_start": "2026-09-14", "period_end": "2026-09-27"}
        if which == 4:
            first = TODAY + timedelta(days=3 + c % 20)
            schedule = [{"date": (first + timedelta(days=30 * k)).isoformat(), "amount": 20 + b % 80} for k in range(4)]
            return "installment", {"provider": "Example Later", "merchant": self.name("Sample shop"), "loan_id": f"L{self.names}", "schedule": schedule, "message_date": "2026-10-12"}
        if which == 5:
            return "investment_balance", {"what": "401(k)", "balance": 1000 + b % 9000, "as_of": "2026-10-10", "sender_name": "Example Funds"}
        return "price_change", {"biller": self.name("Sample service"), "sender_name": "Sample service", "amount": 5 + b % 50, "due_date": due, "message_date": "2026-10-12"}

    def mail_arrives(self, a: int, b: int, c: int) -> None:
        kind, fields = self._fact_fields(a, b, c)
        self._mail(kind, fields)

    def confirm_fact(self, a: int, b: int, c: int) -> None:
        fact = self.pick(self.rows(ExtractedFact, ExtractedFact.status == "pending"), a)
        if fact is not None:
            self.post(f"facts/{fact.id}/confirm", {"apply": True})

    def reject_fact(self, a: int, b: int, c: int) -> None:
        fact = self.pick(self.rows(ExtractedFact, ExtractedFact.status == "pending"), a)
        if fact is not None:
            self.post(f"facts/{fact.id}/reject")

    def unconfirm_fact(self, a: int, b: int, c: int) -> None:
        fact = self.pick(self.rows(ExtractedFact, ExtractedFact.status.in_(("applied", "confirmed"))), a)  # type: ignore[attr-defined]
        if fact is not None:
            self.post(f"facts/{fact.id}/unconfirm")

    def answer_many(self, a: int, b: int, c: int) -> None:
        facts = self.rows(ExtractedFact, ExtractedFact.status == "pending")
        if facts:
            self.post("needs-you/facts", {"ids": [f.id for f in facts[: 1 + b % 4]], "action": ["confirm", "reject"][a % 2]})

    # ---- files -----------------------------------------------------------------------------------------------------

    def _csv(self, a: int, b: int, c: int) -> str:
        """A bank's export: dated rows, some of them this household's own billers."""
        self.csv_n += 1
        bills = self.rows(Bill, Bill.active == True)  # noqa: E712
        lines = ["Date,Description,Amount,Category"]
        for k in range(3 + a % 4):
            day = TODAY - timedelta(days=1 + (b + 3 * k) % 25)
            if k == 0 and bills:
                bill = self.pick(bills, c)
                lines.append(f"{day.isoformat()},{bill.name},{-bill.amount},{bill.category or 'Bills & Utilities'}")
            elif k == 1 and a % 3 == 0:
                lines.append(f"{day.isoformat()},Example Advance,{-(20 + b % 80)},Borrowing")
            elif k == 2 and a % 2 == 0:
                lines.append(f"{day.isoformat()},Sample deposit,{100 + c % 500},Income")
            else:
                lines.append(f"{day.isoformat()},Sample store {self.csv_n}-{k},{-(3 + (b + k) % 90)},{self.CATEGORIES[(c + k) % 5]}")
        return "\n".join(lines) + "\n"

    def _ofx(self, a: int, b: int, c: int) -> str:
        """A statement with the balance it closed on: the bank's own figure for the account it lands in."""
        self.csv_n += 1
        day = TODAY - timedelta(days=2 + b % 9)
        return (f"OFXHEADER:100\n<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>Example Bank</ORG></FI></SONRS></SIGNONMSGSRSV1>\n<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD"
                f"<BANKACCTFROM><ACCTID>0000999{self.csv_n:05d}<ACCTTYPE>CHECKING</BANKACCTFROM>\n<BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{day:%Y%m%d}<TRNAMT>-{5 + b % 70}.25"
                f"<FITID>s{self.csv_n}<NAME>Sample statement store {self.csv_n}</STMTTRN></BANKTRANLIST>\n<LEDGERBAL><BALAMT>{400 + c % 3000}.75<DTASOF>{TODAY - timedelta(days=1):%Y%m%d}120000</LEDGERBAL>\n"
                "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")

    def _bills_list(self, a: int, b: int, c: int) -> str:
        """A bills list: one bill already on file at a new amount and a new day, one new."""
        bills = self.rows(Bill, Bill.active == True, Bill.home_id == None, Bill.utility_id == None)  # noqa: E711, E712
        lines = ["Name,Amount,Frequency,Due day"]
        if bills:
            bill = self.pick(bills, a)
            lines.append(f"{bill.name},{bill.amount + 1 + b % 40},monthly,{1 + c % 28}")
        lines.append(f"{self.name('Sample listed')},{5 + b % 90},monthly,{1 + a % 28}")
        return "\n".join(lines) + "\n"

    def import_ofx(self, a: int, b: int, c: int) -> int | None:
        return self._upload("statement.ofx", self._ofx(a, b, c), self._target(a, c))

    def import_bills_list(self, a: int, b: int, c: int) -> int | None:
        return self._upload("bills.csv", self._bills_list(a, b, c), {})

    def import_to_card(self, a: int, b: int, c: int) -> int | None:
        cards = self.rows(Account, Account.active == True, Account.kind == "card")  # noqa: E712
        form: dict[str, Any] = {"kind": "card"}
        if a % 2 == 0 or not cards:
            form["account_name"] = self.name("Sample imported card")
        else:
            form["account_id"] = str(self.pick(cards, c).id)
        return self._upload("card.csv", self._csv(a, b, c), form)

    def _target(self, a: int, c: int) -> dict[str, Any]:
        accounts = self.rows(Account, Account.active == True, Account.kind == "checking")  # noqa: E712
        if a % 3 == 0 or not accounts:
            return {"kind": "checking", "account_name": self.name("Sample import")}
        return {"kind": "checking", "account_id": str(self.pick(accounts, c).id)}

    def _upload(self, name: str, body: str, form: dict[str, Any]) -> int | None:
        r = self._call("POST", "imports/file", files={"file": (name, body.encode(), "text/plain")}, data=form)
        return r.json().get("import_id") if r.status_code == 200 else None

    def import_csv(self, a: int, b: int, c: int) -> int | None:
        accounts = self.rows(Account, Account.active == True, Account.kind == "checking")  # noqa: E712
        form: dict[str, Any] = {"kind": "checking"}
        if a % 3 == 0 or not accounts:
            form["account_name"] = self.name("Sample import")
        else:
            form["account_id"] = str(self.pick(accounts, c).id)
        r = self._call("POST", "imports/file", files={"file": ("sample.csv", self._csv(a, b, c).encode(), "text/csv")}, data=form)
        return r.json().get("import_id") if r.status_code == 200 else None

    def undo_import(self, a: int, b: int, c: int) -> None:
        from app.models import ImportBatch

        batch = self.pick(self.rows(ImportBatch, ImportBatch.status != "undone"), a)
        if batch is not None:
            self.post(f"imports/{batch.id}/undo")

    # ---- the ledger ------------------------------------------------------------------------------------------------

    def _tx(self, i: int) -> Transaction | None:
        return self.pick(self.rows(Transaction, Transaction.date >= TODAY - timedelta(days=40), Transaction.amount > 0), i)

    def recategorise(self, a: int, b: int, c: int) -> None:
        tx = self._tx(a)
        if tx is not None:
            self._call("PATCH", f"transactions/{tx.id}", json={"category": self.CATEGORIES[b % 5] if c % 4 else "Credit Card Payment"})

    def hide_tx(self, a: int, b: int, c: int) -> None:
        tx = self._tx(a)
        if tx is not None:
            self._call("PATCH", f"transactions/{tx.id}", json={"hidden": bool(b % 2)})

    def split_tx(self, a: int, b: int, c: int) -> None:
        tx = self._tx(a)
        if tx is None:
            return
        part = round(tx.amount * (1 + b % 8) / 10, 2)
        parts = [{"category": self.CATEGORIES[c % 5], "amount": part}, {"category": self.CATEGORIES[(c + 1) % 5], "amount": round(tx.amount - part, 2)}] if b % 5 else []
        self.put(f"transactions/{tx.id}/split", {"parts": parts})

    def bulk_tx(self, a: int, b: int, c: int) -> None:
        rows = self.rows(Transaction, Transaction.date >= TODAY - timedelta(days=20), Transaction.amount > 0)
        if rows:
            self.post("transactions/bulk", {"ids": [t.id for t in rows[a % len(rows): a % len(rows) + 3]], "category": self.CATEGORIES[b % 5]})

    # ---- paid and not paid -----------------------------------------------------------------------------------------

    OPEN = ("past_due", "late", "scheduled")

    def _open_payment(self, i: int) -> Payment | None:
        return self.pick(self.rows(Payment, Payment.status.in_(self.OPEN)), i)  # type: ignore[attr-defined]

    def mark_paid(self, a: int, b: int, c: int) -> None:
        p = self._open_payment(a)
        if p is not None:
            self.post(f"needs-you/payments/{p.id}", {"answer": "paid"})

    def unmark_paid(self, a: int, b: int, c: int) -> None:
        p = self.pick(self.rows(Payment, Payment.status.in_(("paid", "missed"))), a)  # type: ignore[attr-defined]
        if p is not None:
            self.post(f"needs-you/payments/{p.id}/undo")

    def link_payment(self, a: int, b: int, c: int) -> None:
        """Paid with this row of the ledger."""
        p, tx = self._open_payment(a), self._tx(b)
        if p is not None and tx is not None:
            self.post(f"needs-you/payments/{p.id}", {"answer": "paid", "transaction_id": tx.id})

    def not_yet(self, a: int, b: int, c: int) -> None:
        p = self._open_payment(a)
        if p is not None:
            self.post(f"needs-you/payments/{p.id}", {"answer": "not_yet"})

    def bill_stopped(self, a: int, b: int, c: int) -> None:
        p = self._open_payment(a)
        if p is not None:
            self.post(f"needs-you/payments/{p.id}", {"answer": "stopped"})

    # ---- pay-later and advances ------------------------------------------------------------------------------------

    def bill_to_plan(self, a: int, b: int, c: int) -> None:
        bill = self.pick(self.rows(Bill, Bill.active == True), a)  # noqa: E712
        if bill is not None:
            self.post("borrowing/plan", {"bill_id": bill.id, "remaining": 1 + b % 8})

    def plan_paid(self, a: int, b: int, c: int) -> None:
        from app.models import InstallmentPlan

        plan = self.pick(self.rows(InstallmentPlan, InstallmentPlan.active == True), a)  # noqa: E712
        if plan is not None:
            self.post(f"borrowing/plans/{plan.id}/paid", {})

    def end_plan(self, a: int, b: int, c: int) -> None:
        from app.models import InstallmentPlan

        plan = self.pick(self.rows(InstallmentPlan, InstallmentPlan.active == True), a)  # noqa: E712
        if plan is not None:
            self.delete(f"borrowing/plans/{plan.id}")

    def add_planned(self, a: int, b: int, c: int) -> None:
        self.post("planned", {"name": self.name("Sample planned"), "amount": 5 + b % 300, "date": (TODAY + timedelta(days=c % 20)).isoformat(), "kind": ["bill", "other", "transfer"][a % 3]})

    def add_planned_seen(self, a: int, b: int, c: int) -> None:
        """A plan for a payment the ledger already shows."""
        tx = self._tx(a)
        if tx is not None:
            self.post("planned", {"name": tx.merchant, "amount": tx.amount, "date": tx.date.isoformat(), "kind": "other"})

    def delete_planned(self, a: int, b: int, c: int) -> None:
        from app.models import PlannedPayment

        row = self.pick(self.rows(PlannedPayment), a)
        if row is not None:
            self.delete(f"planned/{row.id}")

    def classify_series(self, a: int, b: int, c: int) -> None:
        from app.models import RecurringSeries

        series = self.pick(self.rows(RecurringSeries, RecurringSeries.confirmed == False), a)  # noqa: E712
        if series is not None:
            kind = ["bill", "subscription", "ignored", "installment", "income"][b % 5]
            self.post(f"recurring/{series.id}/classify", {"kind": kind, **({"remaining": 1 + c % 6} if kind == "installment" else {})})

    # ---- housing, rules, settings -------------------------------------------------------------------------------------

    def add_home(self, a: int, b: int, c: int) -> None:
        accounts = self.rows(Account, Account.active == True, Account.kind == "checking")  # noqa: E712
        body: dict[str, Any] = {"name": self.name("Sample home"), "kind": ["rent", "mortgage"][a % 2], "due_day": 1 + b % 28, "starts_on": (TODAY - timedelta(days=c % 200)).isoformat()}
        if accounts and b % 2:
            body["pay_from_account_id"] = self.pick(accounts, c).id
        r = self.post("housing", body)
        if r.status_code == 200 and c % 2:
            homes = self.get("housing")["homes"]
            if homes:
                self.post(f"housing/{homes[-1]['id']}/charges", {"kind": "rent", "name": "Rent", "amount": 500 + b % 1500})

    def delete_home(self, a: int, b: int, c: int) -> None:
        home = self.pick(self.get("housing")["homes"], a)
        if home is not None:
            self.delete(f"housing/{home['id']}")

    def add_utility(self, a: int, b: int, c: int) -> None:
        self.post("utilities", {"service": "electric", "provider": self.name("Sample power"), "amount": 20 + b % 200, "due_day": 1 + c % 28})

    def delete_utility(self, a: int, b: int, c: int) -> None:
        row = self.pick(self.get("utilities")["utilities"], a)
        if row is not None:
            self.delete(f"utilities/{row['id']}")

    def start_fund(self, a: int, b: int, c: int) -> None:
        bill = self.pick(self.rows(Bill, Bill.active == True, Bill.cadence.in_(("quarterly", "yearly", "semiannual"))), a)  # type: ignore[attr-defined]  # noqa: E712
        if bill is not None:
            self.post(f"goals/sinking/{bill.id}")

    def add_rule(self, a: int, b: int, c: int) -> None:
        tx = self._tx(a)
        if tx is not None:
            self.post("ledger/rules", {"kind": ["categorise", "hide", "rename"][b % 3], "pattern": tx.merchant, "value": self.CATEGORIES[c % 5] if b % 3 == 0 else (f"Sample name {c % 5}" if b % 3 == 2 else "")})

    def delete_rule(self, a: int, b: int, c: int) -> None:
        rule = self.pick(self.get("ledger/rules")["rules"], a)
        if rule is not None:
            self.delete(f"ledger/rules/{rule['id']}")

    def set_card_mode(self, a: int, b: int, c: int) -> None:
        self.put("borrowing/settings", {"cards_pay_from": ["auto", "split", "next", "mid", "end", "minimum", "off"][a % 7]})

    def set_buffer(self, a: int, b: int, c: int) -> None:
        self.put("settings/buffer", {"value": a % 500})

    def planned_done(self, a: int, b: int, c: int) -> None:
        from app.models import PlannedPayment

        row = self.pick(self.rows(PlannedPayment, PlannedPayment.done == False), a)  # noqa: E712
        if row is not None:
            self.post(f"planned/{row.id}/done")

    # ---- the data check, the pairs, the night -----------------------------------------------------------------------

    def run_check(self, a: int, b: int, c: int) -> None:
        self.post("data-check/run")

    def answer_check(self, a: int, b: int, c: int) -> None:
        found = self.get("data-check")["findings"]
        finding = self.pick(found, a)
        if finding is not None and finding.get("actions"):
            self.post("data-check/answer", {"key": finding["key"], "action": self.pick(finding["actions"], b)["id"]})

    def decide_pair(self, a: int, b: int, c: int) -> None:
        pairs = self.get("pairs")
        rows = pairs["waiting"] + pairs["matched"]
        pair = self.pick(rows, a)
        if pair is not None:
            self.post(f"pairs/{pair['id']}", {"decision": ["yes", "no"][b % 2]})

    def night(self, a: int, b: int, c: int) -> None:
        self.settle()

    # ---- restore ---------------------------------------------------------------------------------------------------

    def take_point(self, a: int, b: int, c: int) -> None:
        r = self.post("restore-points")
        if r.status_code == 200:
            self.taken.append(r.json()["made"]["name"])

    def restore_point(self, a: int, b: int, c: int) -> None:
        if self.taken:
            self.post(f"restore-points/{self.taken[a % len(self.taken)]}/restore")

    # ---- the round trips: undo is exact ----------------------------------------------------------------------------

    def round_import(self, a: int, b: int, c: int) -> None:
        """An import, some handling of its rows, its undo: every engine figure is back."""
        self.settle()
        before = self.read()
        made = [self.import_csv, self.import_csv, self.import_ofx, self.import_bills_list, self.import_to_card][a % 5](a, b, c)
        if made is None:
            return
        with Session(engine) as s:
            ids = [t.id for t in s.exec(select(Transaction).where(Transaction.import_id == made)).all()]
        if ids and c % 3:
            self._call("PATCH", f"transactions/{ids[0]}", json={"category": self.CATEGORIES[a % 5]})
            self.get("snapshot")
        if ids and c % 3 == 2:
            amount = abs(self.rows(Transaction, Transaction.id == ids[-1])[0].amount)
            self.put(f"transactions/{ids[-1]}/split", {"parts": [{"category": "Gas", "amount": round(amount / 2, 2)}, {"category": "Dining", "amount": round(amount - round(amount / 2, 2), 2)}]})
        if ids and b % 3 == 0:
            p = self._open_payment(a)
            if p is not None:
                self.post(f"needs-you/payments/{p.id}", {"answer": "paid", "transaction_id": ids[0]})
                self.get("snapshot")
        r = self.post(f"imports/{made}/undo")
        if r.status_code != 200:
            raise Broke("undo is exact", f"undo answered {r.status_code}: {r.text[:200]}")
        self.compare("an import undone", before, self.read())

    def round_import_again(self, a: int, b: int, c: int) -> None:
        """The same file twice is one file; undone, it is none; imported once more, it is the same again, whatever ids it took."""
        self.settle()
        before = self.read()
        which = a % 3
        if which == 0:
            name, body, form = "again.csv", self._csv(a, b, c), self._target(a, c)
        elif which == 1:
            name, body, form = "again.ofx", self._ofx(a, b, c), self._target(a, c)
        else:
            name, body, form = "bills.csv", self._bills_list(a, b, c), {}
        made = self._upload(name, body, form)
        if made is None:
            return
        if "account_name" in form:
            form = {"kind": form["kind"], "account_name": form["account_name"]}
        self.settle()
        once = self.read()
        self._upload(name, body, form)
        self.settle()
        twice = self.read()
        self.compare("the same file twice", once, twice)
        if self.post(f"imports/{made}/undo").status_code != 200:
            raise Broke("undo is exact", "undo refused")
        self.compare("an import undone after it was read in twice", before, self.read())
        again = self._upload(name, body, form)
        self.settle()
        found = [f"{p}{d}" for p in PAGES for d in diff(drop_ids(once[p]), drop_ids(self.read()[p]), limit=3)]
        if found:
            raise Broke("undo is exact", f"undone and read in again, it is not the same import ({again}): " + " | ".join(found[:8]))

    def round_two_imports(self, a: int, b: int, c: int) -> None:
        """Two imports, undone in the order they came: the first gone leaves the second exactly, the second gone leaves neither."""
        self.settle()
        before = self.read()
        one = self.import_csv(a, b, c)
        two = self.import_csv(b, c, a)
        if one is None or two is None:
            return
        for first in (one, two) if c % 2 else (two, one):
            if self.post(f"imports/{first}/undo").status_code != 200:
                raise Broke("undo is exact", "undo refused")
            self.get("snapshot")
        self.compare("two imports undone", before, self.read())

    def round_import_point(self, a: int, b: int, c: int) -> None:
        """The restore point an import took before it began: put back, it is the moment before the file."""
        self.settle()
        before = self.read()
        made = self.import_csv(a, b, c)
        if made is None:
            return
        point = next((i["restore_point"] for i in self.get("imports/history")["imports"] if i["id"] == made), None)
        if not point:
            return
        r = self.post(f"restore-points/{point}/restore")
        if r.status_code != 200:
            raise Broke("undo is exact", f"restore answered {r.status_code}: {r.text[:200]}")
        self.compare("the import's own restore point", before, self.read())

    def round_mail_twice(self, a: int, b: int, c: int) -> None:
        """The same message filed twice and both confirmed is one bill, one plan, one paystub."""
        from app.models import InstallmentPlan, Paystub

        kind, fields = self._fact_fields(a, b, c)
        if kind not in ("bill_due", "past_due", "price_change", "paystub", "installment"):
            return
        count = lambda model: len(self.rows(model))  # noqa: E731
        start = (count(Bill), count(InstallmentPlan), count(Paystub))
        first, second = self._mail(kind, fields), self._mail(kind, fields)
        self.post(f"facts/{first}/confirm", {"apply": True})
        self.post(f"facts/{second}/confirm", {"apply": True})
        grown = (count(Bill) - start[0], count(InstallmentPlan) - start[1], count(Paystub) - start[2])
        if sum(grown) > 1:
            raise Broke("nothing counts twice", f"the same {kind} message, confirmed twice, made {grown} bills, plans, paystubs")

    def round_mark(self, a: int, b: int, c: int) -> None:
        """A mark, then taken back: paid another way, paid with a row of the ledger, or stopped."""
        self.settle()
        p = self._open_payment(a)
        if p is None:
            return
        before = self.read()
        body: dict[str, Any] = [{"answer": "paid"}, {"answer": "stopped"}, {"answer": "paid", "transaction_id": 0}][b % 3]
        if "transaction_id" in body:
            with Session(engine) as s:
                used = {t for t in s.exec(select(Payment.transaction_id)).all() if t}
                free = [t.id for t in s.exec(select(Transaction).where(Transaction.amount > 0, Transaction.date >= TODAY - timedelta(days=30))).all() if t.id not in used]
            if not free:
                return
            body["transaction_id"] = free[c % len(free)]
        if self.post(f"needs-you/payments/{p.id}", body).status_code != 200:
            return
        self.get("snapshot")
        r = self.post(f"needs-you/payments/{p.id}/undo")
        if r.status_code != 200:
            raise Broke("undo is exact", f"a mark taken back answered {r.status_code}: {r.text[:200]}")
        self.compare(f"{body} taken back", before, self.read())

    def round_confirm(self, a: int, b: int, c: int) -> None:
        """A message confirmed, then taken back. The fact waits again and every figure is as it was."""
        self.settle()
        kind, fields = self._fact_fields(a, b, c)
        fact_id = self._mail(kind, fields)
        before = self.read()
        r = self.post(f"facts/{fact_id}/confirm", {"apply": True})
        if r.status_code != 200:
            return
        self.get("snapshot")
        if self.post(f"facts/{fact_id}/unconfirm").status_code != 200:
            return  # refused, in words: nothing was claimed
        self.compare(f"a {kind} unconfirmed", before, self.read())

    def round_restore(self, a: int, b: int, c: int) -> None:
        """A restore point, some of the day's actions, the restore: all of it back, row for row."""
        r = self.post("restore-points")
        assert r.status_code == 200, r.text
        name = r.json()["made"]["name"]
        before, rows = self.read(), tables(HISTORY_TABLES | {"settings"})
        rng = random.Random(a * 1000 + b * 10 + c)
        for _ in range(2 + a % 3):
            rule = rng.choice(PLAIN_RULES)
            getattr(self, rule)(rng.randrange(997), rng.randrange(997), rng.randrange(997))
        r = self.post(f"restore-points/{name}/restore")
        assert r.status_code == 200, r.text
        self.compare("a restore", before, self.read())
        after = tables(HISTORY_TABLES | {"settings"})
        wrong = {t: (len(rows[t]), len(after[t])) for t in rows if rows[t] != after[t]}
        if wrong:
            raise Broke("undo is exact", f"a restore: tables differ: {wrong}")

    def compare(self, what: str, before: dict, after: dict) -> None:
        found = [f"{p}{d}" for p in PAGES for d in diff(periods_in_order(before[p]), periods_in_order(after[p]), limit=4)]
        if found:
            raise Broke("undo is exact", f"{what}: {len(found)} differences: " + " | ".join(found[:10]))

    # ---- the invariants, after every step ---------------------------------------------------------------------------

    def check(self) -> None:
        rows_before = tables()
        first = self.read()
        second = self.read()
        found = [f"{p}{d}" for p in PAGES for d in diff(first[p], second[p], limit=3)]
        if found:
            raise Broke("the same read twice", " | ".join(found[:8]))
        rows_after = tables()
        wrote = {t: [str(x)[:200] for x in rows_after[t] if x not in rows_before[t]][:2] for t in rows_before if rows_before[t] != rows_after[t]}
        if wrote:
            raise Broke("a GET changes nothing", str(wrote)[:800])
        agree(first)
        no_dangling(first)
        no_orphan_rows()


def same(name: str, a: Any, b: Any) -> None:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(float(a) - float(b)) > CENT:
            raise Broke("figures agree", f"{name}: {a} against {b}")
    elif a != b:
        raise Broke("figures agree", f"{name}: {json.dumps(a, default=str)[:300]} against {json.dumps(b, default=str)[:300]}")


def agree(p: dict[str, Any]) -> None:
    """Every figure on the snapshot against the same figure on the page's own endpoint."""
    s = p["snapshot"]
    # Safe to spend
    same("goals' safe to spend", p["goals"]["safe_to_spend"], s["safe_to_spend"])
    same("goals per paycheck", p["goals"]["per_paycheck"], s["goals"]["per_paycheck"])
    same("goals items", p["goals"]["items"], s["goals"]["items"])
    same("budgets' safe to spend", p["budgets"]["safe_to_spend"], s["safe_to_spend"]["amount"])
    same("glance safe to spend", p["glance"]["safe_to_spend"], s["safe_to_spend"]["amount"])
    # Debts
    same("Debts total, snapshot", p["debts"]["total"], s["debts_total"])
    same("Debts total, net worth", p["debts"]["total"], p["accounts/overview"]["debts"]["total"])
    same("Debts rows add up", sum(i["balance"] for i in p["debts"]["items"]), p["debts"]["total"])
    same("Debts rows add up, snapshot", sum(i["balance"] for i in s["debts"]), s["debts_total"])
    if all(i.get("minimum") for i in p["debts"]["items"]):  # one not known is taken as 2% on Payoff, and says so
        same("Debts minimums, payoff", p["debts"]["minimums_month"], p["debts/payoff"]["minimums_total"])
    # Borrowing
    same("advances out, snapshot", p["borrowing"]["outstanding_total"], s["borrowing"]["outstanding_total"])
    same("advances due before payday add up", sum(e["amount"] for e in s["advances"]["items"]), s["advances"]["total"])
    same("advances out, exit plan", p["borrowing"]["outstanding_total"], p["borrowing/exit"]["owed_now"])
    same("fees 90 days", p["borrowing"]["cost"]["fees_90d"], s["borrowing"]["fees_90d"])
    same("pay-later left", p["borrowing"]["loans_left_to_pay"], p["debts"]["bnpl"]["total"])
    # Needs you, and its badge
    n, q = p["needs-you"], s["pending"]
    same("Needs you total", n["total"], q["total"])
    same("Needs you past due", len(n["past_due"]), q["past_due"])
    same("Needs you series", n["series"], q["series"])
    same("Needs you series, recurring", n["series"], p["recurring"]["pending"])
    same("Needs you pairs", n["pairs"], q["pairs"])
    same("Needs you pairs, pairs page", n["pairs"], p["pairs"]["counts"]["waiting"])
    same("waiting facts, snapshot", p["facts"]["pending"], s["mail"]["pending_count"])
    same("waiting facts, pending", p["facts"]["pending"], q["facts"])
    same("Needs you facts", sum(g.get("count", len(g.get("items", []))) for g in n["groups"]), p["facts"]["pending"])
    same("the badge", q["confirmations"], n["total"] - q["approvals"] if "approvals" in q else n["total"])
    # Payments and accounts
    same("payments page", p["payments"], s["payments"])
    same("accounts' cash", s["cash"], sum(a["balance"] or 0 for a in s["accounts"] if a["kind"] in ("checking", "cash")))
    same("net worth", p["accounts/overview"]["net"], p["accounts/overview"]["assets"]["total"] - p["accounts/overview"]["debts"]["total"])
    same("Accounts page", {a["id"]: a["balance"] for a in p["accounts"]["accounts"] if a["id"] in {b["id"] for b in s["accounts"]}}, {a["id"]: a["balance"] for a in s["accounts"]})
    # Windows
    w = s["windows"]
    if w:
        same("window against windows[0]", s["window"]["total"], w[0]["bills"]["total"])
    for i, win in enumerate(w):
        same(f"window {i} bills add up", sum(e["amount"] for e in win["bills"]["items"] if not e.get("via_card")), win["bills"]["total"])
        same(f"window {i} set aside adds up", sum(e.get("kept", e["amount"]) for e in win["set_aside"]["items"]), win["set_aside"]["total"])
    same("Paychecks windows", [(x["start"], x["paycheck"], x["inflow"], x["safe"]) for x in p["paychecks"]["windows"]], [(x["start"], x["paycheck"], x["inflow"], x["safe"]) for x in w][: len(p["paychecks"]["windows"])])
    same("Saving windows", [(x["start"], x["set_aside"]) for x in p["saving"]["windows"]], [(x["start"], x["set_aside"]["total"]) for x in w][: len(p["saving"]["windows"])])
    same("Watchlist safe to spend", p["watchlist"]["safe_to_spend"], s["safe_to_spend"])
    same("glance through", p["glance"]["through"], s["safe_to_spend"]["through"])
    same("glance payday", (p["glance"]["payday"]["date"], p["glance"]["payday"]["amount"]), (s["next_paycheck"]["date"], s["next_paycheck"]["amount"]))
    same("Cover buffer", p["cover"]["buffer"], s["buffer"])
    parts = s["safe_to_spend"]["parts"]
    if not s["no_balance"]:
        worked = parts["cash"] - parts["due_before_payday"] - parts["advances"] - parts["buffer"] - parts["next_window_shortfall"]
        same("safe to spend worked out", s["safe_to_spend"]["amount"], max(0.0, worked) if s["safe_to_spend"]["amount"] >= 0 else worked)
    same("due before payday adds up", sum(e["amount"] for e in s["due_before_payday"]["items"] if not e.get("via_card")), s["due_before_payday"]["total"])
    if w:
        same("set aside against windows[0]", s["set_aside"]["total"], w[0]["set_aside"]["total"])
    # Bills on the plan are bills on the page, at the page's amount
    live = {f"bill:{b['id']}": b for b in p["bills"]["bills"]}
    same("Bills monthly sources", set(p["bills"]["monthly_sources"]) - set(live), set())
    # Reports
    r = p["reports/spending"]
    same("Reports months add up", sum(m["spent"] for m in r["months"]), r["totals"]["spent"])
    y = p["reports/custom/year"]
    same("Year months add up", sum(y["month_totals"]), y["spent"])
    by_month = dict(zip(y["months"], y["month_totals"]))
    for m in r["months"]:
        if m.get("month") in by_month:
            same(f"Reports {m['month']} against the year", m["spent"], by_month[m["month"]])
    # Budgets
    b = p["budgets"]
    same("Budgets add up", sum(i["spent"] for i in b["items"]), b["totals"]["spent"])
    # The same card, the same loan, the same balance, wherever it is shown
    debts_by_source = {i["source"]: i for i in p["debts"]["items"]}
    same("Payoff balances", {d["source"]: d["balance"] for d in p["debts/payoff"]["debts"] if d["source"] in debts_by_source}, {d["source"]: debts_by_source[d["source"]]["balance"] for d in p["debts/payoff"]["debts"] if d["source"] in debts_by_source})
    same("Credit against Borrowing", {c["account_id"]: c["balance"] for c in p["credit"]["cards"]}, {c["account_id"]: c["balance"] for c in p["borrowing"]["credit"]})
    cards = sum(i["balance"] for i in p["debts"]["items"] if i["kind"] == "card")
    same("card balance, Borrowing", p["borrowing"]["cost"]["card_balance"], cards)
    same("loans left, Borrowing plan", p["borrowing/plan"]["totals"]["left_to_pay"], p["borrowing"]["loans_left_to_pay"])
    same("loans count, Borrowing plan", p["borrowing/plan"]["totals"]["loans"], len(p["borrowing"]["loans"]))
    same("Debts rows, net worth", {i["source"]: i["balance"] for i in p["accounts/overview"]["debts"]["items"]}, {i["source"]: i["balance"] for i in p["debts"]["items"]})
    same("savings, Saving", p["saving"]["balance"], s["savings"])
    same("savings, Projections", p["projections"]["savings"]["now"], s["savings"])
    same("investments, net worth", p["investments"]["total"], sum(i["balance"] for i in p["accounts/overview"]["assets"]["items"] if i["kind"] == "investment"))
    same("Readiness cushion", next(i["value"] for i in p["scenarios/readiness"]["items"] if i["key"] == "buffer"), s["safe_to_spend"]["amount"])
    same("Cover balances", {a["id"]: a["balance"] for a in p["cover"]["accounts"]}, {a["id"]: a["balance"] for a in s["accounts"] if a["id"] in {b["id"] for b in p["cover"]["accounts"]}})
    same("Board problems", s["board"]["problems"]["total"], sum(x["amount"] for x in s["payments"]["past_due"] + s["payments"]["late"]))
    same("Past due, Needs you and snapshot", sorted(x["source"] for x in s["past_due_waiting"]), sorted(x["source"] for x in n["past_due"]))
    for item in p["watchlist"]["items"]:
        if item["kind"] == "advances" and "amount" in item:
            same("Watchlist advances", item["amount"], p["borrowing"]["outstanding_total"])
    # Nothing waits on, and nothing in the plan stands on, a bill that was deleted
    live_ids = {b["id"] for b in p["bills"]["bills"]}
    for where, rows in (("past due", s["payments"]["past_due"]), ("late", s["payments"]["late"]), ("scheduled", s["payments"]["scheduled"])):
        for row in rows:
            if row["bill_id"] not in live_ids:
                raise Broke("every source resolves", f"{where} on Bills names deleted bill {row['bill_id']} ({row['name']})")
    for x in n["past_due"]:
        if x.get("bill_id") is not None and x["bill_id"] not in live_ids:
            raise Broke("every source resolves", f"Needs you asks about deleted bill {x['bill_id']} ({x.get('name')})")
    amounts = {b["id"]: b["amount"] for b in p["bills"]["bills"]}
    for win in w:
        for e in win["bills"]["items"]:
            if e.get("bill_id") is not None:
                if e["bill_id"] not in live_ids:
                    raise Broke("every source resolves", f"window {win['start']} counts deleted bill {e['bill_id']} ({e['name']})")
    # Nothing counts twice
    seen: set = set()
    for win in w:
        for e in win["bills"]["items"]:
            if e.get("bill_id") is None:
                continue
            key = (e["bill_id"], e.get("due_date") or e["date"], e.get("name"))
            if key in seen:
                raise Broke("nothing counts twice", f"{key} in two rows of the plan")
            seen.add(key)
    sources = [i.get("source") for i in p["debts"]["items"] if i.get("source")]
    if len(sources) != len(set(sources)):
        raise Broke("nothing counts twice", f"a debt twice on Debts: {sources}")


SOURCE = re.compile(r"^(bill|debt|income|plan|advance|account|transaction|tx|paystub|fact|goal|payment|home|series|planned|utility):(\d+)$")
SOURCE_TABLES = {"bill": "bills", "debt": "debts", "income": "incomes", "plan": "installment_plans", "advance": "advances", "account": "accounts", "transaction": "transactions",
                 "tx": "transactions", "paystub": "paystubs", "fact": "extracted_facts", "goal": "goals", "payment": "payments", "home": "homes", "series": "recurring_series",
                 "planned": "planned_payments", "utility": "utilities"}
# A row that was switched off is gone to the plan: a figure of today must not still stand on it.
ACTIVE_TABLES = {"bills", "incomes", "goals", "accounts"}


def source_refs(value: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(source_refs(v, f"{path}.{k}"))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(source_refs(v, f"{path}[{i}]"))
    elif isinstance(value, str) and SOURCE.match(value):
        out.append((path, value))
    return out


def no_dangling(pages: dict[str, Any]) -> None:
    """Every figure's source resolves: the row is there, and for a figure of today's plan it is still in use."""
    with engine.connect() as c:
        for page in ("snapshot", "bills", "debts", "borrowing", "budgets", "goals", "paychecks", "cover", "debts/payoff", "borrowing/plan", "needs-you"):
            for path, ref in source_refs(pages[page]):
                kind, ident = ref.split(":")
                table = SOURCE_TABLES[kind]
                row = c.execute(text(f'SELECT {"active" if table in ACTIVE_TABLES else "id"} FROM "{table}" WHERE id = :i'), {"i": int(ident)}).first()
                if row is None:
                    raise Broke("every source resolves", f"{page}{path} names {ref}, and no such row")
                if table in ACTIVE_TABLES and not row[0] and ".calendar" not in path and "paid" not in path:
                    raise Broke("every source resolves", f"{page}{path} names {ref}, which was deleted")


# (table, column, table named): ids the schema does not hold a foreign key for, or does.
REFS = [
    ("payments", "bill_id", "bills"), ("payments", "transaction_id", "transactions"), ("transaction_splits", "transaction_id", "transactions"),
    ("transaction_pairs", "a_id", "transactions"), ("transaction_pairs", "b_id", "transactions"), ("paystubs", "transaction_id", "transactions"), ("paystubs", "fact_id", "extracted_facts"),
    ("planned_payments", "transaction_id", "transactions"), ("invoices", "transaction_id", "transactions"), ("bills", "pay_from_account_id", "accounts"), ("bills", "series_id", "recurring_series"),
    ("incomes", "account_id", "accounts"), ("incomes", "series_id", "recurring_series"), ("debts", "account_id", "accounts"), ("installment_plans", "debt_id", "debts"),
    ("installment_plans", "pay_from_account_id", "accounts"), ("advances", "debt_id", "debts"), ("goals", "debt_id", "debts"), ("transactions", "account_id", "accounts"), ("holdings", "account_id", "accounts"),
    ("balance_snapshots", "account_id", "accounts"), ("extracted_facts", "message_ref_id", "message_refs"),
]


def no_orphan_rows() -> None:
    """Deleting a row leaves nothing naming it, and no one row of the ledger pays two bills."""
    with engine.connect() as c:
        for table, column, target in REFS:
            row = c.execute(text(f'SELECT id, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" NOT IN (SELECT id FROM "{target}") LIMIT 1')).first()
            if row is not None:
                raise Broke("no orphan", f"{table} {row[0]}.{column} names {target} {row[1]}, which is gone")
        row = c.execute(text("SELECT transaction_id, COUNT(*), GROUP_CONCAT(bill_id) FROM payments WHERE transaction_id IS NOT NULL GROUP BY transaction_id HAVING COUNT(*) > 1 LIMIT 1")).first()
        if row is not None:
            raise Broke("nothing counts twice", f"transaction {row[0]} is the payment of {row[1]} periods, bills {row[2]}")
        for row in c.execute(text("SELECT id, status, applied_to, made FROM extracted_facts WHERE status = 'applied' AND applied_to LIKE '%:%'")).fetchall():
            kind, _, ident = row[2].partition(":")
            if kind in SOURCE_TABLES and ident.isdigit() and c.execute(text(f'SELECT id FROM "{SOURCE_TABLES[kind]}" WHERE id = :i'), {"i": int(ident)}).first() is None:
                raise Broke("no orphan", f"fact {row[0]} says it applied to {row[2]}, which is gone")
        for row in c.execute(text("SELECT s.transaction_id, ROUND(SUM(s.amount), 2), t.amount FROM transaction_splits s JOIN transactions t ON t.id = s.transaction_id GROUP BY s.transaction_id")).fetchall():
            if abs(abs(row[1]) - abs(row[2])) > 0.011:
                raise Broke("nothing counts twice", f"transaction {row[0]} is {row[2]}, its splits add to {row[1]}")


# ---- the driver ------------------------------------------------------------------------------------------------------

PLAIN_RULES = [
    "add_bill", "edit_bill", "delete_bill", "bring_back_bill", "add_income", "edit_income", "delete_income", "add_account", "set_balance", "edit_debt", "add_investment", "delete_investment",
    "add_asset", "put_budget", "delete_budget", "add_goal", "edit_goal", "fund_goal", "delete_goal", "mail_arrives", "mail_arrives", "confirm_fact", "confirm_fact", "reject_fact", "unconfirm_fact", "answer_many",
    "import_csv", "undo_import", "recategorise", "hide_tx", "split_tx", "bulk_tx", "mark_paid", "unmark_paid", "link_payment", "not_yet", "bill_stopped", "bill_to_plan", "plan_paid", "end_plan", "add_planned", "add_planned_seen", "delete_planned",
    "classify_series", "import_ofx", "import_bills_list", "import_to_card", "add_home", "delete_home", "add_utility", "delete_utility", "start_fund", "add_rule", "delete_rule", "set_card_mode", "set_buffer", "planned_done", "run_check", "answer_check", "decide_pair", "night", "night",
]
ROUND_TRIPS = ["round_import", "round_import", "round_import_again", "round_two_imports", "round_import_point", "round_confirm", "round_confirm", "round_mail_twice", "round_mark", "round_mark", "round_restore", "take_point", "restore_point"]
RULES = PLAIN_RULES + ROUND_TRIPS


def generate(seed_value: int, steps: int) -> list[Action]:
    rng = random.Random(seed_value)
    return [(rng.choice(RULES), rng.randrange(997), rng.randrange(997), rng.randrange(997)) for _ in range(steps)]


def run_sequence(actions: list[Action], stats: dict | None = None) -> Broke | None:
    """A sequence from a fresh household. The first broken invariant, or None."""
    client = fresh_household()
    world = World(client)
    try:
        world.check()
        for action in actions:
            getattr(world, action[0])(*action[1:])
            if stats is not None:
                stats["steps"] = stats.get("steps", 0) + 1
            world.check()
    except Broke as broke:
        return broke
    finally:
        if stats is not None:
            stats["calls"] = stats.get("calls", 0) + world.calls
    return None


def shrink(actions: list[Action], name: str, limit: float = 90.0) -> list[Action]:
    """The shortest list that still breaks the same invariant (ddmin, then one at a time)."""
    started = time.monotonic()

    def still(candidate: list[Action]) -> bool:
        found = run_sequence(candidate)
        return found is not None and found.name == name

    chunk = max(1, len(actions) // 2)
    while chunk >= 1 and time.monotonic() - started < limit:
        i, removed = 0, False
        while i < len(actions) and time.monotonic() - started < limit:
            candidate = actions[:i] + actions[i + chunk:]
            if still(candidate):
                actions, removed = candidate, True
            else:
                i += chunk
        if chunk == 1 and not removed:
            break
        chunk = max(1, chunk // 2) if chunk > 1 else 1
    return actions


def hunt(sequences: int, steps: int, base: int) -> tuple[dict, list[str]]:
    stats: dict = {"sequences": 0}
    failures: list[str] = []
    seen: set[str] = set()
    for k in range(sequences):
        actions = generate(base + k, steps)
        stats["sequences"] += 1
        broke = run_sequence(actions, stats)
        if broke is None:
            continue
        key = f"{broke.name}|{re.sub(r'[0-9]+', '#', broke.detail)[:70]}"
        if key in seen:
            continue
        seen.add(key)
        short = shrink(actions, broke.name, limit=float(os.environ.get("FA_SEQ_SHRINK", "60")))
        again = run_sequence(short)
        failures.append(f"seed {base + k}: {again or broke}\n  shortest ({len(short)}): {short}")
    return stats, failures


def test_driver_random_sequences_keep_every_invariant():
    sequences = int(os.environ.get("FA_SEQ_SEQUENCES", "3"))
    steps = int(os.environ.get("FA_SEQ_STEPS", "20"))
    base = int(os.environ.get("FA_SEQ_SEED", "26000"))
    stats, failures = hunt(sequences, steps, base)
    print(f"\nsequences {stats['sequences']}, steps {stats.get('steps', 0)}, calls {stats.get('calls', 0)}")
    if os.environ.get("FA_SEQ_TALLY"):
        for key in sorted(TALLY):
            print(f"T {TALLY[key]:5d} {key}  {TALLY_WHY.get(key, '')}")
    assert not failures, "\n\n".join(failures)


# ---- what the driver found, written out plainly: each is the shortest sequence that showed it --------------------------


@pytest.fixture
def home() -> World:
    return World(fresh_household())


def first_open_payment() -> Payment:
    rows = World(None).rows(Payment, Payment.status.in_(World.OPEN))  # type: ignore[arg-type, attr-defined]
    assert rows, "the household has an open period"
    return rows[0]


def test_one_row_of_the_ledger_pays_one_bill(home: World):
    """[link_payment]: a row already paying one period was accepted for a second, and that bill stood settled by money counted once."""
    held = home.rows(Payment, Payment.transaction_id != None)[0]  # noqa: E711
    open_period = first_open_payment()
    r = home.post(f"needs-you/payments/{open_period.id}", {"answer": "paid", "transaction_id": held.transaction_id})
    assert r.status_code == 400, r.text
    assert "already matched" in r.json()["detail"]
    assert home.rows(Payment, Payment.id == open_period.id)[0].status != "paid"
    no_orphan_rows()


def test_budgets_answers_when_the_only_paycheck_starts_more_than_a_period_away(home: World):
    """[add_income, delete_income]: a new job's first check a month out, the old paycheck deleted: Budgets answered 500."""
    from app.models import Income

    made = home.post("incomes", {"name": "Example New Job", "cadence": "semimonthly", "expected_net": 1500, "next_date": (TODAY + timedelta(days=27)).isoformat()})
    assert made.status_code == 200, made.text
    for income in home.rows(Income, Income.id != made.json()["id"]):
        assert home.put(f"incomes/{income.id}", {"active": False}).status_code == 200
    r = home._call("GET", "budgets")
    assert r.status_code == 200, r.text
    assert r.json()["span"]["start"] == TODAY.isoformat()
    assert home.get("snapshot")["next_paycheck"]["date"] == made.json()["next_date"]


def test_undoing_a_statement_puts_its_balance_back(home: World):
    """[import OFX, undo]: the rows went and the balance the statement stated stayed, so safe to spend stood on a file taken back."""
    before = home.read()
    checking = home.rows(Account, Account.kind == "checking")[0]
    assert checking.balance == 312.0
    made = home._upload("statement.ofx", home._ofx(1, 2, 700), {"kind": "checking", "account_id": str(checking.id)})
    assert made is not None
    assert home.rows(Account, Account.id == checking.id)[0].balance == 1100.75
    assert home.get("snapshot")["cash"] == 1100.75
    assert home.post(f"imports/{made}/undo").status_code == 200
    back = home.rows(Account, Account.id == checking.id)[0]
    assert (back.balance, back.balance_at) == (312.0, None)
    assert home.get("snapshot")["cash"] == 312.0
    home.compare("a statement undone", before, home.read())
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM balance_snapshots WHERE account_id = :i"), {"i": checking.id}).scalar() == 0


def test_a_balance_typed_after_the_statement_survives_its_undo(home: World):
    """Everything since stays: the owner's own figure is not taken back with the file."""
    made = home._upload("statement.ofx", home._ofx(0, 2, 700), {"kind": "checking", "account_name": "Example Statement Account"})
    assert made is not None
    account = home.rows(Account, Account.name == "Example Statement Account")[0]
    assert account.balance == 1100.75
    with session_scope() as s:
        row = s.get(Account, account.id)
        row.balance = 55.0
        s.add(row)
        s.commit()
    assert home.post(f"imports/{made}/undo").status_code == 200
    assert home.rows(Account, Account.id == account.id)[0].balance == 55.0


def test_taking_back_a_paystub_puts_the_paycheck_back(home: World):
    """[paystub confirmed, unconfirmed]: the stub went and the net it set on the paycheck stayed: every plan after it stood on a
    message the owner had taken back, a forged one included."""
    from app.models import Income

    assert home.put("incomes/1", {"cadence": "semimonthly"}).status_code == 200
    before = home.rows(Income, Income.id == 1)[0]
    assert before.expected_net_end is None
    # Not near a deposit, so it stays unmatched and can be taken back. The end of a month: the semi-monthly check's second figure.
    fact = home._mail("paystub", {"employer": "Example Employer", "net": 1777.0, "gross": 3000, "pay_date": "2026-09-30"})
    pages = home.read()
    assert home.post(f"facts/{fact}/confirm", {"apply": True}).status_code == 200
    assert home.rows(Income, Income.id == 1)[0].expected_net_end is not None  # the check at month's end has its own figure now
    r = home.post(f"facts/{fact}/unconfirm")
    assert r.status_code == 200, r.text
    assert home.rows(Income, Income.id == 1)[0].expected_net_end is None
    assert "back as it was" in r.json()["said"]
    home.compare("a paystub unconfirmed", pages, home.read())


def test_a_figure_changed_since_the_paystub_stays(home: World):
    from app.models import Income

    assert home.put("incomes/1", {"cadence": "semimonthly"}).status_code == 200
    fact = home._mail("paystub", {"employer": "Example Employer", "net": 1777.0, "gross": 3000, "pay_date": "2026-09-30"})
    assert home.post(f"facts/{fact}/confirm", {"apply": True}).status_code == 200
    assert home.put("incomes/1", {"expected_net_end": 1900.0}).status_code == 200
    assert home.post(f"facts/{fact}/unconfirm").status_code == 200
    assert home.rows(Income, Income.id == 1)[0].expected_net_end == 1900.0


@pytest.mark.parametrize("answer", [{"answer": "paid"}, {"answer": "paid", "paid_on": "2026-10-10"}, {"answer": "stopped"}])
def test_a_mark_can_be_taken_back_and_every_figure_returns(home: World, answer: dict):
    """A wrong tap on "paid" or "stopped" held for good: no route took it back. Now it does, exactly."""
    period = first_open_payment()
    before = home.read()
    assert home.post(f"needs-you/payments/{period.id}", answer).status_code == 200
    assert home.read() != before
    r = home.post(f"needs-you/payments/{period.id}/undo")
    assert r.status_code == 200, r.text
    home.compare(f"{answer} taken back", before, home.read())
    assert home.post(f"needs-you/payments/{period.id}/undo").status_code == 400  # nothing left to take back


def test_a_wrong_link_can_be_taken_back(home: World):
    with Session(engine) as s:
        used = {t for t in s.exec(select(Payment.transaction_id)).all() if t}
        free = next(t.id for t in s.exec(select(Transaction).where(Transaction.amount > 0, Transaction.date >= TODAY - timedelta(days=14), Transaction.category == "Groceries")).all() if t.id not in used)
    period = first_open_payment()
    before = home.read()
    assert home.post(f"needs-you/payments/{period.id}", {"answer": "paid", "transaction_id": free}).status_code == 200
    assert home.rows(Payment, Payment.id == period.id)[0].transaction_id == free
    assert home.post(f"needs-you/payments/{period.id}/undo").status_code == 200
    assert home.rows(Payment, Payment.id == period.id)[0].transaction_id is None
    home.compare("a link taken back", before, home.read())


def test_a_pay_later_plan_can_be_ended(home: World):
    """The refusal on Needs you says "End it on Borrowing"; nothing could. A plan with a ticked payment stayed counted for good."""
    from app.models import InstallmentPlan

    plan = max(home.rows(InstallmentPlan, InstallmentPlan.active == True), key=lambda p: p.remaining)  # noqa: E712
    assert plan.remaining > 1
    left = plan.installment * plan.remaining
    before = home.get("debts")["total"]
    assert home.post(f"borrowing/plans/{plan.id}/paid", {}).status_code == 200
    assert home.get("debts")["total"] < before
    now = home.get("debts")["total"]
    assert home.delete(f"borrowing/plans/{plan.id}").status_code == 200
    assert home.rows(InstallmentPlan, InstallmentPlan.id == plan.id)[0].active is False
    assert home.get("debts")["total"] == round(now - max(0.0, left - plan.installment), 2)
    assert f"plan:{plan.id}" not in json.dumps(home.get("snapshot"))
    home.settle()
    assert home.rows(InstallmentPlan, InstallmentPlan.id == plan.id)[0].active is False  # nothing brings it back
    assert home.delete(f"borrowing/plans/{plan.id}").status_code == 404
    home.check()


def test_reading_planned_payments_changes_nothing(home: World):
    """[add_planned for a row already in the ledger]: opening Bills settled it, so the figures on Today changed between two reads."""
    from app.models import PlannedPayment

    tx = home.rows(Transaction, Transaction.date >= TODAY - timedelta(days=10), Transaction.amount > 0)[0]
    made = home.post("planned", {"name": tx.merchant, "amount": tx.amount, "date": tx.date.isoformat(), "kind": "other"})
    assert made.status_code == 200, made.text
    row = home.rows(PlannedPayment, PlannedPayment.id == made.json()["id"])[0]
    assert row.done is True and row.transaction_id == tx.id  # settled as it was added, not by the next page opened
    with session_scope() as s:
        p = s.get(PlannedPayment, row.id)
        p.done, p.transaction_id = False, None  # as a sync would leave one its row has just reached
        s.add(p)
        s.commit()
    before = tables()
    assert home.get("planned")["items"][0]["done"] is False
    assert tables() == before


def test_taking_back_a_mark_on_a_period_the_bill_has_left(home: World):
    """[mark paid, move the due day, take the mark back]: the matcher removes a period on a day the bill no longer comes due, and the route, reading it after, answered 500."""
    period = first_open_payment()
    bill = home.rows(Bill, Bill.id == period.bill_id)[0]
    assert home.post(f"needs-you/payments/{period.id}", {"answer": "paid"}).status_code == 200
    assert home.put(f"bills/{bill.id}", {"due_day": bill.due_day % 28 + 1}).status_code == 200
    r = home.post(f"needs-you/payments/{period.id}/undo")
    assert r.status_code == 200, r.text
    assert "not marked paid" in r.json()["said"]
    home.check()


def test_a_paystub_two_messages_name_is_not_taken_back_from_under_the_second(home: World):
    """[the same stub mailed twice, both confirmed, the first taken back]: the stub went and the second fact still said it applied to it."""
    fields = {"employer": "Example Employer", "net": 1777.0, "gross": 3000, "pay_date": "2026-09-30"}
    first, second = home._mail("paystub", fields), home._mail("paystub", fields)
    assert home.post(f"facts/{first}/confirm", {"apply": True}).status_code == 200
    assert home.post(f"facts/{second}/confirm", {"apply": True}).status_code == 200
    r = home.post(f"facts/{first}/unconfirm")
    assert r.status_code == 409, r.text
    assert "Another message" in r.json()["detail"]
    no_orphan_rows()
    assert home.post(f"facts/{second}/unconfirm").status_code == 200
    assert home.post(f"facts/{first}/unconfirm").status_code == 200
    no_orphan_rows()
