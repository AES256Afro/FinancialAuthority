"""Security sweep 20 (the sixteenth sweep): who can reach what, taken from the running app.

Every route the app serves is called in-process as each kind of caller: a stranger, the owner, a second person with
"everything", a second person with "shared", an API key at read and at read and confirm (full, and hiding details),
and a connected app's OAuth token at read and at read and confirm. Writes are sent a body that passes validation, so
the guard decides and not the schema, and the database is put back after each one, so no call changes what the next
one finds. The answers (the status code per route and caller) are kept in `tests/golden/route_access.json`: a change
to who reaches what fails here until the golden file is regenerated on purpose (FA_ACCESS_UPDATE=1).

The same table is then read against what docs/SECURITY.md ("Who can reach it") and docs/PLAN.md section 12 allow
each caller (`allowed` below, written from the documents, not from the code), and a cell where the app grants more,
or refuses what the documents allow, fails with the route and the caller.

The same run watches every statement each write sends to the database: a route that writes a table a writing job
also writes (derived by running those jobs over the example household) must hold the writer lock while it does, so
a write beside a running sync or detection is neither doubled nor lost. The list of routes is the app's own; there is
no hand list to forget a new route in."""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel

from app import auth
from app.db import engine, init_db
from app.engine.household import seed
from app.jobs import writing
from app.main import app
from app.models import OAuthClient, OAuthToken, utcnow

GOLDEN = Path(__file__).parent / "golden" / "route_access.json"
UPDATE = os.environ.get("FA_ACCESS_UPDATE") == "1"
PW = "correct horse battery"
SAM_PW = "maple harbour winter nine"
LEE_PW = "copper lantern autumn seven"
H = {"X-Requested-With": "FinancialAuthority"}
WRITES = {"POST", "PUT", "PATCH", "DELETE"}

PRINCIPALS = ("stranger", "owner", "everything", "shared", "key_read", "key_confirm", "key_read_merchants", "key_confirm_aggregates", "oauth_read", "oauth_confirm")

# ---- what the documents allow ---------------------------------------------------------------------------------------
# docs/PLAN.md section 12 and docs/SECURITY.md's "Who can reach it", route by route. Written from the documents.

# The public surface: the sign-in (with its second step, passkeys and joining), the OAuth server and consent page,
# the Plaid webhook, the calendar feed, health, and /mcp (a bearer token decides there).
PUBLIC = {
    ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/login/second"), ("POST", "/api/auth/passkeys/login/options"),
    ("POST", "/api/auth/passkeys/login"), ("POST", "/api/auth/join/check"), ("POST", "/api/auth/join"), ("POST", "/api/plaid/webhook"),
    ("GET", "/api/calendar/{token}.ics"), ("GET", "/api/health"), ("GET", "/oauth/consent"), ("POST", "/oauth/consent"),
    ("GET", "/.well-known/oauth-authorization-server"), ("GET", "/.well-known/oauth-protected-resource"), ("GET", "/authorize"), ("POST", "/authorize"),
    ("POST", "/token"), ("POST", "/register"), ("POST", "/revoke"), ("POST", "/mcp"),
}
# A person's own sign-in settings: their sessions, their second step and passkeys. Any person signed in.
OWN_SIGN_IN = re.compile(r"^/api/auth/(me|sessions|sessions/end|factors|totp/.*|passkeys/register.*|passkeys/\{passkey_id\})$")
# What only the owner does: add, change or remove people, erase everything, restore the database, the off-box copy,
# connecting an app through OAuth (the consent page's allow is the owner's: tested on that page, not here).
OWNER_ONLY = {
    ("GET", "/api/v1/people"), ("POST", "/api/v1/people"), ("PUT", "/api/v1/people/{person_id}"), ("POST", "/api/v1/people/{person_id}/invite"),
    ("DELETE", "/api/v1/people/{person_id}"), ("POST", "/api/v1/privacy/erase"), ("POST", "/api/v1/restore-points/{name}/restore"),
    ("PUT", "/api/v1/restore-points/offbox"), ("POST", "/api/v1/restore-points/offbox/copy"),
}
# Deciding on a send queued by someone else: the approval the run sends is the owner's draft, so a second person is
# refused it ("each person approves only their own sends").
OWNERS_SEND = {("PUT", "/api/v1/approvals/{approval_id}"), ("POST", "/api/v1/approvals/{approval_id}/approve"), ("POST", "/api/v1/approvals/{approval_id}/deny")}
# A "shared" sign-in: one page (the shared accounts), what the app needs to draw it, and its own sign-in settings.
SHARED = {("GET", "/api/v1/shared"), ("GET", "/api/v1/about"), ("GET", "/api/v1/appearance")}
# A key or a connected app holding confirm: confirming what waits on a person (a fact, a charge's name, a transfer
# pair, an expected charge, Needs you's facts) and queuing a send that waits for approval (a draft, a move's letter).
CONFIRM = {
    ("POST", "/api/v1/facts/{fact_id}/confirm"), ("POST", "/api/v1/facts/{fact_id}/reject"), ("POST", "/api/v1/needs-you/facts"),
    ("POST", "/api/v1/recurring/{series_id}/classify"), ("POST", "/api/v1/pairs/{pair_id}"), ("POST", "/api/v1/watch/{key}/expected"),
    ("POST", "/api/v1/approvals"), ("POST", "/api/v1/move/plan/letter"),
}
# Reads a key never makes: the keys page and what holds secrets or the household's own record of its callers, the
# built-in chat, a document's file (the owner's), what is the owner's to hand out (exports, the calendar feed), and
# the shared person's own page (it is drawn for a person signed in).
KEY_NEVER_READS = re.compile(r"^/api/v1/(keys|shared|oauth/clients|audit|models|banks|push.*|restore-points.*|imports/history|exports.*|calendar/feed|people|chat"
                             r"|documents/\{doc_id\}/file|business/invoices/\{invoice_id\}/print|reports/custom/tax/pack)$")
# Reads that are not "read": they compute, never write, so a read key may send them.
READ_POSTS = {("POST", "/api/v1/ledger/rules/preview"), ("POST", "/api/v1/scenarios/run")}


def allowed(principal: str, method: str, path: str) -> bool:
    """Whether the documents let this caller past the guard on this route."""
    if (method, path) in PUBLIC:
        return True
    if principal == "stranger":
        return False
    if principal == "owner":
        return True
    person = principal in ("everything", "shared")
    if path.startswith("/api/auth/"):
        return person and bool(OWN_SIGN_IN.match(path))  # a key or an app never reaches a person's sign-in settings
    if principal == "everything":
        return (method, path) not in OWNER_ONLY and (method, path) not in OWNERS_SEND
    if principal == "shared":
        return (method, path) in SHARED
    # a key or a connected app
    if principal.endswith(("_merchants", "_aggregates")):
        return path in ("/api/v1/ha",) and method == "GET"  # a key that hides details reads through /mcp only
    if path == "/api/version" or path.startswith("/api/tools/"):
        return method == "GET"
    if not path.startswith("/api/v1/"):
        return False
    if method == "GET" or (method, path) in READ_POSTS:
        return not KEY_NEVER_READS.match(path)
    return principal.endswith("_confirm") and (method, path) in CONFIRM


# ---- the routes -----------------------------------------------------------------------------------------------------


def _walk(routes):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _walk(r.original_router.routes)
        else:
            yield r


def operations() -> list[tuple[str, str, Any]]:
    """Every (method, path) the app answers, with its route: the FastAPI routes and the OAuth server's and MCP's own."""
    out = []
    for r in _walk(app.routes):
        path = getattr(r, "path", None)
        if not path or (r is None):
            continue
        if isinstance(r, APIRoute):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, path, r))
        elif path == "/mcp":
            out.append(("POST", path, r))
        elif getattr(r, "methods", None):
            for m in sorted(set(r.methods) - {"HEAD", "OPTIONS"}):
                out.append((m, path, r))
    unique = {(m, p): r for m, p, r in out}
    return [(m, p, r) for (m, p), r in sorted(unique.items(), key=lambda o: (o[0][1], o[0][0]))]


# Bodies that pass validation where the schema alone does not build one, keyed by (method, path); and path values
# where the household's first row is not the one to use, keyed by (method, path) and then the parameter's name.
D = "2026-10-01"
_BILL = {"name": "Example Water", "kind": "bill", "amount": 40.0, "due_day": 5, "paid_from": "due"}
_GOAL = {"name": "Cushion", "kind": "save", "target": 500.0}
_RULE = {"kind": "categorise", "pattern": "Example Grocer", "value": "Groceries"}
_INV = {"name": "Example IRA", "subtype": "ira", "balance": 100.0, "as_of": D}
_MAIL = {"label": "Example", "kind": "imap", "host": "imap.example.test", "port": 993, "username": "owner@example.test", "password": "example-password"}
_TEMPLATE = {"name": "Example", "layout": "board", "palette": "app", "mascot": "none", "sections": ["summary"]}
_LOAN = {"person": "Example Friend", "direction": "lent", "amount": 20.0, "on": D}
BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/facts/{fact_id}/confirm"): {"apply": True},
    ("POST", "/api/v1/approvals"): {"to": "office@example-flats.test", "subject": "Notice", "body": "Moving out."},
    ("POST", "/api/v1/assets"): {"name": "Example Car", "kind": "car", "value": 5000.0},
    ("PUT", "/api/v1/assets/{asset_id}"): {"name": "Example Car", "kind": "car", "value": 4500.0},
    ("PUT", "/api/v1/banks/plaid/settings"): {"client_id": "example-client", "secret": "example-secret", "environment": "sandbox"},
    ("PUT", "/api/v1/banks/teller/settings"): {"application_id": "example-app", "environment": "sandbox"},
    ("POST", "/api/v1/bills"): _BILL, ("PUT", "/api/v1/bills/{bill_id}"): {"amount": 41.0},
    ("POST", "/api/v1/borrowing/plans/{plan_id}/paid"): {"on": "2026-10-14"},
    ("PUT", "/api/v1/borrowing/settings"): {"cards_pay_from": "auto"},
    ("POST", "/api/v1/business"): {"name": "Example Studio"},
    ("POST", "/api/v1/business/finder/answer"): {"ids": [1], "answer": "personal"},
    ("POST", "/api/v1/business/{business_id}/invoices"): {"counterparty": "Example Client", "issued_on": D, "lines": [{"what": "Design", "quantity": 1, "price": 100.0}]},
    ("PUT", "/api/v1/business/invoices/{invoice_id}"): {"counterparty": "Example Client", "issued_on": D, "lines": [{"what": "Design", "quantity": 1, "price": 120.0}]},
    ("POST", "/api/v1/business/invoices/{invoice_id}/paid"): {"paid_on": D},
    ("POST", "/api/v1/business/{business_id}/trips"): {"day": D, "miles": 12.0, "purpose": "Client visit"},
    ("POST", "/api/v1/chat"): {"messages": [{"role": "user", "content": "How am I doing?"}], "tools": False},
    ("POST", "/api/v1/checkin"): {"subscription": "keep"},
    ("POST", "/api/v1/debts/payoff/people"): _LOAN, ("PUT", "/api/v1/debts/payoff/people/{index}"): _LOAN,
    ("PUT", "/api/v1/documents/{doc_id}"): {"title": "Lease, signed"},
    ("POST", "/api/v1/exports/preview"): _TEMPLATE, ("POST", "/api/v1/exports/templates"): _TEMPLATE,
    ("PUT", "/api/v1/firstrun"): {"mark": "paid"},
    ("POST", "/api/v1/goals"): _GOAL, ("PUT", "/api/v1/goals/{goal_id}"): {**_GOAL, "target": 600.0},
    ("PUT", "/api/v1/incomes/{income_id}"): {"expected_net": 1500.0},
    ("POST", "/api/v1/investments"): _INV, ("PUT", "/api/v1/investments/{account_id}"): _INV,
    ("POST", "/api/v1/investments/{account_id}/holdings"): {"name": "Example Index Fund", "quantity": 1.0, "price": 100.0, "value": 100.0},
    ("POST", "/api/v1/ledger/rules"): _RULE, ("POST", "/api/v1/ledger/rules/preview"): _RULE,
    ("POST", "/api/v1/mail/accounts"): _MAIL, ("PUT", "/api/v1/mail/accounts/{account_id}"): {**_MAIL, "label": "Example two"},
    ("POST", "/api/v1/mail/accounts/test"): {k: v for k, v in _MAIL.items() if k != "label"},
    ("POST", "/api/v1/models/catalog"): {"provider": "ollama"},
    ("PUT", "/api/v1/models/secrets"): {"name": "anthropic", "value": "example-not-a-key"},
    ("PUT", "/api/v1/models/{role}"): {"provider": "none"},
    ("POST", "/api/v1/move/plan/letter"): {"to": "office@example-flats.test", "body": "Moving out."},
    ("POST", "/api/v1/moves"): {"source_account_id": 2, "target_name": "Example New Bank", "name": "Example move"},
    ("PUT", "/api/v1/moves/{move_id}"): {"target_name": "Example Newer Bank"},
    ("PUT", "/api/v1/moves/{move_id}/items/{item_id}"): {"decision": "move"},
    ("POST", "/api/v1/needs-you/facts"): {"ids": [1], "action": "preview"},
    ("POST", "/api/v1/needs-you/payments/{payment_id}"): {"answer": "paid", "paid_on": D},
    ("PUT", "/api/v1/oauth/clients/{client_id}"): {"redaction": "merchants"},
    ("POST", "/api/v1/pairs/{pair_id}"): {"decision": "yes"},
    ("PUT", "/api/v1/people/{person_id}"): {"display_name": "Sam E."},
    ("POST", "/api/v1/privacy/erase"): {"phrase": "not the phrase", "password": "wrong"},
    ("PUT", "/api/v1/projections/assumptions"): {"savings_apy": 0.04},
    ("PUT", "/api/v1/push"): {"kind": "off"},
    ("POST", "/api/v1/push/devices"): {"endpoint": "https://push.example.test/device", "keys": {"p256dh": "BExample", "auth": "example"}},
    ("POST", "/api/v1/recurring/{series_id}/classify"): {"kind": "subscription", "name": "Example Stream"},
    ("PUT", "/api/v1/restore-points/offbox"): {"every_days": 7},
    ("PUT", "/api/v1/settings/{key}"): {"value": 150.0},
    ("POST", "/api/v1/suggestions/answer"): {"key": "selfcheck-1", "answer": "done"},
    ("POST", "/api/v1/suggestions/bring-back"): {"kind": "selfcheck"},
    ("PUT", "/api/v1/transactions/{tx_id}/split"): lambda w: {"parts": [{"category": "Groceries", "amount": round(w.tx_amount - 1, 2)}, {"category": "Household", "amount": 1.0}]},
    ("POST", "/api/v1/reviews/{run_id}/notes/{n}/apply"): {"answer": "Yes"},
}
QUERIES: dict[tuple[str, str], dict] = {
    ("GET", "/api/v1/bills/{bill_id}/move_check"): {"day": 10}, ("GET", "/api/v1/check"): {"amount": 25},
    ("GET", "/api/v1/documents/search"): {"q": "lease"}, ("GET", "/api/v1/exports/file"): {"format": "csv"},
    ("GET", "/api/v1/projections/where"): {"amount": 50}, ("GET", "/api/v1/receipts/candidates"): {"amount": 12.5, "bought_on": D},
}
ROUTE_PATHS: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/goals/sinking/{bill_id}"): {"bill_id": lambda w: w.quarterly_bill},
    ("POST", "/api/v1/recurring/{series_id}/classify"): {"series_id": lambda w: w._first("recurring_series", "confirmed = 0 and kind = 'unknown'") or w._first("recurring_series", "confirmed = 0")},
    ("POST", "/api/v1/jobs/{name}/run"): {"name": "balance_history"},
    ("PUT", "/api/v1/settings/{key}"): {"key": "buffer"},
    ("POST", "/api/v1/business/setup/{step}"): {"step": "record"},
}


def _spec_bodies() -> dict[tuple[str, str], Any]:
    from test_sweep14_security_fuzz import _resolve, _valid

    spec = app.openapi()
    out = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                out[(method.upper(), path)] = _valid(_resolve(content["application/json"]["schema"], spec), spec)
            elif "multipart/form-data" in content or "application/x-www-form-urlencoded" in content:
                kind = "multipart/form-data" if "multipart/form-data" in content else "application/x-www-form-urlencoded"
                schema = _resolve(content[kind]["schema"], spec)
                out[(method.upper(), path)] = ("form", {k: v for k, v in ((k, _valid(s, spec, k)) for k, s in schema.get("properties", {}).items() if k != "file") if v not in (None, "")})
    return out


CSV = b"date,description,amount\n2026-10-01,Example Grocer,12.50\n2026-10-02,Example Cafe,4.25\n"


def _files() -> dict[tuple[str, str], tuple[str, bytes, str]]:
    """The file each upload route is sent: one it reads, so the route goes as far as it can."""
    from datetime import date

    from test_rocketmoney import HEADER, ROWS
    from test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    return {
        ("POST", "/api/v1/imports/rocketmoney"): ("export.csv", (HEADER + ROWS).encode(), "text/csv"),
        ("POST", "/api/v1/borrowing/statement"): ("statement.pdf", pdf_of(affirm_statement_lines(date(2026, 9, 30))), "application/pdf"),
        ("POST", "/api/v1/imports/paystub"): ("stub.pdf", b"%PDF-1.4 example", "application/pdf"),
        ("POST", "/api/v1/imports/holdings/{account_id}"): ("holdings.csv", b"Symbol,Description,Quantity,Price,Value\nEXFND,Example Index Fund,2,100,200\n", "text/csv"),
    }


# ---- the world ------------------------------------------------------------------------------------------------------


class World:
    """The example household, the owner, two second people, four keys and two connected apps."""

    def __init__(self) -> None:
        SQLModel.metadata.drop_all(engine)
        init_db()
        auth.reset_lockouts()
        with Session(engine) as session:
            seed(session)
        self.clients: dict[str, TestClient] = {}
        owner = TestClient(app)
        assert owner.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 200
        owner.headers.update(H)
        self.clients["owner"] = owner
        for name, role, pw, principal in (("sam", "everything", SAM_PW, "everything"), ("lee", "shared", LEE_PW, "shared")):
            made = owner.post("/api/v1/people", json={"username": name, "display_name": name.title() + " Example", "role": role})
            assert made.status_code == 200, made.text
            c = TestClient(app)
            c.headers.update(H)
            assert c.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": pw}).status_code == 200
            self.clients[principal] = c
        stranger = TestClient(app)
        stranger.headers.update(H)
        self.clients["stranger"] = stranger
        with Session(engine) as session:
            for principal, scopes, redaction in (("key_read", {"read"}, "full"), ("key_confirm", {"read", "confirm"}, "full"),
                                                  ("key_read_merchants", {"read"}, "merchants"), ("key_confirm_aggregates", {"read", "confirm"}, "aggregates")):
                _, token = auth.create_key(session, principal, scopes, redaction)
                self.clients[principal] = self._bearer(token)
            for principal, scopes in (("oauth_read", "read"), ("oauth_confirm", "read confirm")):
                client = OAuthClient(client_id=f"client-{principal}", name=f"Example app ({principal})", redirect_uris='["https://example.test/cb"]', scopes=scopes)
                token = f"fa_oa_{principal}_" + "x" * 32
                session.add(client)
                session.add(OAuthToken(token_hash=auth.hash_key(token), client_id=client.client_id, scopes=scopes, expires_at=utcnow() + timedelta(days=1)))
                session.commit()
                self.clients[principal] = self._bearer(token)
        self._rows(owner)

    @staticmethod
    def _bearer(token: str) -> TestClient:
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        return c

    def _rows(self, owner: TestClient) -> None:
        """One row of each register the example household lacks, so a route's path names a row that exists."""
        from app.models import ExtractedFact, MessageRef, ReviewRun

        def made(path: str, body: Any = None, **kw: Any) -> dict:
            r = owner.post(path, json=body, **kw) if body is not None else owner.post(path, **kw)
            assert r.status_code == 200, (path, r.text)
            return r.json()

        made("/api/v1/approvals", BODIES[("POST", "/api/v1/approvals")])
        made("/api/v1/documents", files={"file": ("lease.txt", b"Example lease. Rent 1200 a month.", "text/plain")}, data={"title": "Lease"})
        from app.jobs.scheduler import jobs

        for job in ("detect_recurring", "match_payments"):
            jobs.run(job)
        made("/api/v1/housing", {"name": "Example Flats", "kind": "rent", "starts_on": "2025-01-01", "ends_on": "2026-12-31", "notice_given_on": "2026-10-01"})
        made("/api/v1/bills", {**_BILL, "name": "Example Insurance", "amount": 300.0, "cadence": "quarterly", "anchor_date": "2026-11-05"})
        self.quarterly_bill = self._first("bills", "name = 'Example Insurance'")
        made("/api/v1/banks/accounts/1/shows", BODIES.get(("POST", "/api/v1/banks/accounts/{account_id}/shows")) or _spec_bodies().get(("POST", "/api/v1/banks/accounts/{account_id}/shows")))
        home = self._first("homes")
        made(f"/api/v1/housing/{home}/charges", {"name": "Rent", "amount": 1200.0})
        made(f"/api/v1/housing/{home}/costs", {"name": "Deposit", "amount": 500.0})
        made("/api/v1/utilities", {"home_id": home, "service": "electric", "provider": "Example Power"})
        made("/api/v1/goals", _GOAL)
        made("/api/v1/business", BODIES[("POST", "/api/v1/business")])
        biz = self._first("businesses")
        made(f"/api/v1/business/{biz}/invoices", BODIES[("POST", "/api/v1/business/{business_id}/invoices")])
        made(f"/api/v1/business/{biz}/trips", BODIES[("POST", "/api/v1/business/{business_id}/trips")])
        made("/api/v1/planned", {"name": "Example Repair", "amount": 200.0, "date": "2026-11-01"})
        made("/api/v1/scenarios", {"name": "Example", "move_date": "2026-12-01"})
        made("/api/v1/assets", BODIES[("POST", "/api/v1/assets")])
        made("/api/v1/ledger/rules", _RULE)
        made("/api/v1/exports/templates", _TEMPLATE)
        made("/api/v1/debts/payoff/people", _LOAN)
        made("/api/v1/investments", {**_INV, "name": "Example Brokerage"})
        made(f"/api/v1/investments/{self._first('accounts', "kind = 'investment'")}/holdings", BODIES[("POST", "/api/v1/investments/{account_id}/holdings")])
        made("/api/v1/moves", {**BODIES[("POST", "/api/v1/moves")], "source_account_id": 1})
        made("/api/v1/mail/accounts", _MAIL)
        r = owner.put("/api/v1/budgets/Groceries", json={"per_paycheck": 100.0})
        assert r.status_code == 200, r.text
        made("/api/v1/imports/file", files={"file": ("example.csv", CSV, "text/csv")}, data={"account_name": "Example Imported"})
        with Session(engine) as session:
            box = self._first("mail_accounts")
            session.add(MessageRef(account_id=box, folder="INBOX", uid=1, message_id="<example-1@example.test>", sender="billing@example-water.test", subject="Your bill"))
            session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Water", "amount": 40.0, "due_day": 5}), confidence=0.9, status="pending"))
            from app.models import BankLink

            session.add(BankLink(provider="plaid", external_id="example-item", environment="sandbox", institution="Example Bank"))
            session.add(ReviewRun(ok=True, notes=json.dumps([{"n": 0, "kind": "tighten", "text": "Keep a cushion.", "action": {"type": "rule", "text": "Keep a cushion."}, "status": "open"}])))
            session.commit()
        with engine.connect() as c:
            self.tx_amount = float(c.execute(text("select amount from transactions where id = (select min(id) from transactions)")).scalar())
        self.confirmed_fact = None
        r = owner.post(f"/api/v1/facts/{self._first('extracted_facts')}/confirm", json={"apply": False})
        if r.status_code == 200:
            self.confirmed_fact = self._first("extracted_facts")
            with Session(engine) as session:
                session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Gas", "amount": 30.0, "due_day": 9}), confidence=0.9, status="pending"))
                session.commit()

    @staticmethod
    def _first(table: str, where: str = "") -> int:
        with engine.connect() as c:
            return c.execute(text(f'select min(id) from "{table}"' + (f" where {where}" if where else ""))).scalar() or 0

    def path_value(self, name: str, method: str, path: str) -> str:
        if name in ROUTE_PATHS.get((method, path), {}):
            value = ROUTE_PATHS[(method, path)][name]
            return str(value(self) if callable(value) else value)
        if name == "fact_id":
            if path.endswith("/unconfirm") and self.confirmed_fact:
                return str(self.confirmed_fact)
            return str(self._first("extracted_facts", "status = 'pending'"))
        if name == "account_id" and ("/investments/" in path or "/imports/holdings/" in path):
            return str(self._first("accounts", "kind = 'investment'"))
        if name == "account_id" and "/mail/accounts/" in path:
            return str(self._first("mail_accounts"))
        if name == "step_id":
            r = self.clients["owner"].get("/api/v1/move/plan")
            steps = r.json().get("steps", []) if r.status_code == 200 else []
            return str(steps[0]["id"]) if steps else "1"
        if name == "payment_id":
            return str(self._first("payments", "status != 'paid'") or self._first("payments"))
        if name == "message_id":
            return str(self._first("message_refs"))
        tables = {
            "account_id": "accounts", "home_id": "homes", "business_id": "businesses", "move_id": "account_moves", "invoice_id": "invoices", "doc_id": "documents",
            "person_id": "people", "goal_id": "goals", "bill_id": "bills", "approval_id": "approvals", "utility_id": "utilities",
            "tx_id": "transactions", "run_id": "review_runs", "planned_id": "planned_payments", "link_id": "bank_links",
            "cost_id": "home_costs", "charge_id": "home_charges", "asset_id": "other_assets", "trip_id": "trips", "series_id": "recurring_series",
            "scenario_id": "scenarios", "rule_id": "rules", "plan_id": "installment_plans", "paystub_id": "paystubs",
            "passkey_id": "passkeys", "pair_id": "transaction_pairs", "key_id": "api_keys", "item_id": "move_items", "income_id": "incomes",
            "import_id": "import_batches", "holding_id": "holdings", "debt_id": "debts",
        }
        if name in tables:
            return str(self._first(tables[name]) or 1)
        if name == "client_id":
            return "client-oauth_read"
        return {"role": "advisor", "key": "brief", "category": "Groceries", "index": "0", "n": "0", "page": "1", "year": "2026", "token": "x" * 43,
                "recipient": "office@example-flats.test", "name": "example", "step": "record", "template_id": "1", "device_id": "1"}.get(name, "1")


# ---- the database put back after each write -------------------------------------------------------------------------


class Restorer:
    def __init__(self) -> None:
        self.saved = sqlite3.connect(":memory:", check_same_thread=False)
        raw = engine.raw_connection()
        try:
            raw.driver_connection.backup(self.saved)
        finally:
            raw.close()

    def restore(self) -> None:
        raw = engine.raw_connection()
        try:
            self.saved.backup(raw.driver_connection)
        finally:
            raw.close()
        auth.reset_lockouts()


# ---- the statements each write sends --------------------------------------------------------------------------------

_WRITE = re.compile(r'^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+"?(\w+)"?', re.IGNORECASE)


class Watch:
    """Records, while on, each write statement: its table, the setting's key for the settings table, and whether the
    writer lock was held by the thread that sent it."""

    def __init__(self) -> None:
        self.on = False
        self.seen: list[tuple[str, str | None, bool]] = []
        event.listen(engine, "before_cursor_execute", self._hook)

    def close(self) -> None:
        event.remove(engine, "before_cursor_execute", self._hook)

    def _hook(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not self.on:
            return
        m = _WRITE.match(statement)
        if not m:
            return
        table = m.group(1).lower()
        key = None
        if table == "settings":
            params = parameters if isinstance(parameters, (list, tuple)) else list((parameters or {}).values())
            key = next((p for p in reversed(params) if isinstance(p, str) and not p.startswith(("{", "[", '"')) and len(p) < 80), None) if params else None
        self.seen.append((table, key, writing._lock._is_owned()))

    def take(self) -> list[tuple[str, str | None, bool]]:
        out, self.seen = self.seen, []
        return out


QUIET = {"audit_entries", "job_runs", "health_samples", "web_sessions", "api_keys", "oauth_tokens", "oauth_clients"}


def job_registers(watch: Watch) -> tuple[set[str], set[str]]:
    """The tables, and the settings' keys, a writing job writes: what a route must hold the writer lock to write."""
    from app.jobs.scheduler import jobs

    watch.take()
    watch.on = True
    for name, spec in jobs.specs.items():
        if spec.writes:
            try:
                jobs.run(name)
            except Exception:
                pass
    watch.on = False
    seen = watch.take()
    tables = {t for t, _, _ in seen if t not in QUIET and t != "settings"}
    keys = {k for t, k, _ in seen if t == "settings" and k}
    return tables, keys


# The registers the brief names, whatever the jobs happened to write over the example household.
NAMED_REGISTERS = {"accounts", "transactions", "bills", "payments", "debts", "installment_plans", "advances", "incomes", "recurring_series", "extracted_facts",
                   "homes", "utilities", "goals", "category_budgets", "rules", "businesses", "invoices", "trips", "holdings"}


# ---- the run --------------------------------------------------------------------------------------------------------


def _no_network(monkeypatch) -> None:
    real = socket.getaddrinfo

    def local_only(host, *a, **k):
        if host in ("localhost", "127.0.0.1", "::1", "testserver"):
            return real(host, *a, **k)
        raise socket.gaierror("no network in the access run")

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


def _call(client: TestClient, method: str, url: str, body: Any, params: dict | None = None, upload: tuple | None = None) -> int:
    kw: dict = {"params": params} if params else {}
    if isinstance(body, tuple) and body and body[0] == "form":
        kw["data"] = body[1]
        kw["files"] = {"file": upload or ("example.csv", CSV, "text/csv")}
    elif body is not None:
        kw["json"] = body
    try:
        return client.request(method, url, **kw).status_code
    except Exception:
        return 500


def _mcp_status(live: TestClient, client: TestClient) -> int:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if "authorization" in client.headers:
        headers["Authorization"] = client.headers["authorization"]
    cookies = dict(client.cookies)
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "access", "version": "1"}}}
    live.cookies.clear()
    for k, v in cookies.items():
        live.cookies.set(k, v)
    try:
        return live.post("/mcp", json=body, headers=headers).status_code
    finally:
        live.cookies.clear()


@pytest.fixture(scope="module")
def run(live):
    mp = pytest.MonkeyPatch()
    _no_network(mp)
    from test_cadence import STATEMENT

    from app.parsers import paystub as paystub_parser

    real = paystub_parser.pdf_text
    mp.setattr(paystub_parser, "pdf_text", lambda data: STATEMENT if data.startswith(b"%PDF-1.4 example") else real(data))
    from app.api import routes

    routes.set_today_override(__import__("datetime").date(2026, 10, 14))
    try:
        world = World()
        watch = Watch()
        restorer = Restorer()
        tables, keys = job_registers(watch)
        restorer.restore()
        registers = tables | NAMED_REGISTERS
        bodies = {**_spec_bodies(), **BODIES}
        uploads = _files()
        table: dict[str, dict[str, int]] = {}
        unlocked: dict[str, list[str]] = {}
        for method, path, _route in operations():
            cell = f"{method} {path}"
            url = path
            for name in re.findall(r"{(\w+)}", path):
                url = url.replace("{" + name + "}", world.path_value(name, method, path))
            body = bodies.get((method, path))
            body = body(world) if callable(body) else body
            row = {}
            for principal in PRINCIPALS:
                client = world.clients[principal]
                if path == "/mcp":
                    row[principal] = _mcp_status(live, client)
                    continue
                watch.take()
                watch.on = method in WRITES
                jar = dict(client.cookies)
                row[principal] = _call(client, method, url, body, QUERIES.get((method, path)), uploads.get((method, path)))
                client.cookies.clear()  # a sign-out or a new sign-in replaced the cookie; the database is put back below
                for k, v in jar.items():
                    client.cookies.set(k, v)
                watch.on = False
                seen = watch.take()
                if principal in ("owner", "everything"):
                    bad = sorted({t if t != "settings" else f"settings[{k}]" for t, k, held in seen if not held and (t in registers or (t == "settings" and k in keys))})
                    if bad:
                        unlocked.setdefault(cell, []).extend(f"{principal}:{t}" for t in bad if f"{principal}:{t}" not in unlocked.get(cell, []))
                if method in WRITES:
                    restorer.restore()
            table[cell] = row
        watch.close()
        yield {"table": table, "unlocked": unlocked, "registers": sorted(registers), "setting_keys": sorted(keys)}
    finally:
        routes.set_today_override(None)
        mp.undo()


def granted(status: int) -> bool:
    return status not in (401, 403)


def test_who_reaches_what_matches_the_golden_file(run):
    table = run["table"]
    if UPDATE or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps({"principals": list(PRINCIPALS), "routes": table}, indent=1, sort_keys=True) + "\n")
        if not UPDATE:
            pytest.fail(f"{GOLDEN.name} was missing and has been written; commit it")
    golden = json.loads(GOLDEN.read_text())["routes"]
    changed = []
    for cell in sorted(set(table) | set(golden)):
        if table.get(cell) != golden.get(cell):
            changed.append(f"{cell}: was {golden.get(cell)}, now {table.get(cell)}")
    assert not changed, "Who reaches what changed; if on purpose, run with FA_ACCESS_UPDATE=1 and commit the file:\n" + "\n".join(changed[:60])


def test_no_caller_reaches_more_or_less_than_the_documents_allow(run):
    over, under = [], []
    for cell, row in run["table"].items():
        method, path = cell.split(" ", 1)
        if (method, path) in PUBLIC:
            continue
        for principal, status in row.items():
            want = allowed(principal, method, path)
            if granted(status) and not want:
                over.append(f"{cell} {principal} -> {status}")
            elif want and not granted(status):
                under.append(f"{cell} {principal} -> {status}")
    assert not over, "granted beyond the documents:\n" + "\n".join(over)
    assert not under, "refused what the documents allow:\n" + "\n".join(under)


def test_every_register_write_route_runs_under_the_writer(run):
    """Derived, not listed: every write route called as the owner and as a second person, every statement it sent
    watched; a register (a table or a setting a writing job also writes) written without the writer lock fails."""
    assert run["registers"], "the jobs wrote nothing over the example household; the derivation is broken"
    assert not run["unlocked"], "wrote a register without the writer lock:\n" + "\n".join(f"{k}: {v}" for k, v in sorted(run["unlocked"].items()))


def test_the_run_reached_the_routes(run):
    """The run is only as good as its bodies: most writes the owner sends land, so the guard decided, not validation."""
    table = run["table"]
    writes = [c for c in table if c.split(" ", 1)[0] in WRITES and c.split(" ", 1)[1].startswith("/api/v1/")]
    landed = [c for c in writes if 200 <= table[c]["owner"] < 300]
    assert len(table) >= 300 and len(landed) >= 0.8 * len(writes), (len(table), len(landed), len(writes), sorted(set(writes) - set(landed)))
    assert not [c for c, row in table.items() if 500 in row.values()], [c for c, row in table.items() if 500 in row.values()]
