"""Security sweep 13, part three: the logs. Two weeks of the simulated quarter (every job, the taps, the mail, the
bank) and a dozen tool calls run with everything captured at DEBUG; the captured log must hold no secret at any
level, and no row either: no account name, balance, merchant, sender, subject, body, token, key, password, data
key, session id or document text. The audit log records who called which tool and how many rows, never the rows."""

from __future__ import annotations

import logging
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.engine.household import NET_PAY
from app.models import AuditEntry, BankLink, MessageRef, Transaction
from tests.test_oauth import MCP_HEADERS
from tests.test_sweep8_bugs_quarter import PASSPHRASE, TAP_DAYS, Quarter, quarter  # noqa: F401 - the fixture

DAYS = 16  # through the first payday after the start, two mail Fridays and two week-ends of jobs
TOOLS = [("finance_snapshot", {}), ("accounts_list", {}), ("transactions_search", {"query": "grocery"}), ("bills_list", {}), ("paystubs_list", {}), ("spending_summary", {}),
         ("mail_search", {"query": "bills"}), ("brief_today", {}), ("data_check", {}), ("jobs_status", {}), ("glance", {}), ("next_steps", {}), ("facts_pending", {})]


def test_two_weeks_of_jobs_and_a_dozen_tool_calls_log_no_secret_and_no_row(quarter: Quarter, live: TestClient, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    for _ in range(DAYS):
        quarter.advance()
        if quarter.day in TAP_DAYS:
            quarter.tap()
            quarter.day_cycle()
    key = quarter.owner.post("/api/v1/keys", json={"name": "script", "scopes": ["read"], "redaction": "full"}).json()["token"]
    bearer = {"Authorization": f"Bearer {key}"}
    first = live.post("/mcp", headers={**bearer, **MCP_HEADERS}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
    sid = first.headers["mcp-session-id"]
    live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    for n, (name, arguments) in enumerate(TOOLS, start=2):
        r = live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        assert r.status_code == 200, (name, r.text[:200])
    with Session(engine) as session:
        messages = session.exec(select(MessageRef)).all()
        assert messages, "the quarter's mail arrived"
        rows = session.exec(select(Transaction)).all()
        links = session.exec(select(BankLink)).all()
        audits = session.exec(select(AuditEntry)).all()
        merchants = {t.merchant for t in rows if t.merchant}
        amounts = {f"{abs(t.amount):.2f}" for t in rows}
    assert quarter.day >= date(2026, 10, 30) and len(rows) > 100 and len(audits) > len(TOOLS)
    # The audit log: who, which tool, how many rows, a keyed hash of the arguments, never the rows.
    for a in audits:
        assert set(a.model_dump()) <= {"id", "client", "client_kind", "tool", "args_hash", "rows", "redaction", "at", "created_at"}, a
        assert "grocery" not in (a.args_hash or "") and len(a.args_hash or "") <= 16

    text = caplog.text + "\n" + "\n".join(str(r.args) for r in caplog.records if r.args) + "\n" + "\n".join(str(r.getMessage()) for r in caplog.records)
    assert len(caplog.records) > 50, "the jobs and the tools did log at DEBUG"
    cookie = quarter.owner.cookies.get("fa_session") or next(iter(quarter.owner.cookies.values()))
    never = {
        "the mailbox password": "app-pass", "the Plaid secret": "s3cret-value-77", "the off-box passphrase": PASSPHRASE, "the API key": key, "the session cookie": cookie,
        "the second person's password": "plum river lantern seven", "FA_PASSWORD": "correct horse battery", "FA_SESSION_SECRET": settings.session_secret, "FA_DATA_KEY": settings.data_key,
        "the MCP session id": sid, "the Plaid access token": next((l.access_token_enc for l in links if l.access_token_enc), "fa1:"),
        "an account name": "Everyday Checking", "another account name": "Rewards Card", "the loan": "Auto Loan", "the partner's account": "Example Partner Checking",
        "the sender": "hello@email.rocketmoney.com", "the owner's address": "owner@example.test", "the subject": "This week's upcoming bills", "the body": "Heads up Owner",
        "the body's tail": "Looking to lower your bills", "the employer": "Example Employer", "the net pay": f"{NET_PAY:.2f}", "the net pay, rounded": "$2,240",
    }
    for merchant in sorted(merchants)[:12]:
        never[f"merchant {merchant}"] = merchant
    for amount in sorted(amounts)[:40]:
        never[f"amount {amount}"] = f"${amount}"
    for message in messages[:3]:
        if message.snippet:
            never[f"snippet of message {message.uid}"] = message.snippet[:40]
    found = [name for name, value in never.items() if value and value in text]
    assert found == [], found
