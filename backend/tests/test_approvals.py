"""Sends only with an approval: queue, approve, deny, allowances, the push, the scopes, the MCP tool. The
SMTP sender is replaced by a recorder; no mail leaves."""

import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, notify
from app.db import engine
from app.models import Approval, MailAccount

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_queue_approve_deny_allowances_and_scopes(owner: TestClient, client: TestClient, monkeypatch):
    sent: list[dict] = []
    pushed: list[dict] = []
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: (sent.append({"account": account.label, "password": password, **kw}) or "<id-1@test>"))
    monkeypatch.setattr(notify, "send", lambda session, title, body, **kw: (pushed.append({"title": title, "body": body, **kw}) or {"ok": True}))

    # No mailbox can send yet: an approved draft fails cleanly and says why.
    queued = owner.post("/api/v1/approvals", json={"to": "billing@example-utility.test", "subject": "Move my billing date", "body": "Hello,\nplease move it.", "reason": "Electricity to the 13th", "source": "bill:1"})
    assert queued.status_code == 200, queued.text
    first = queued.json()
    assert first["status"] == "pending" and pushed and pushed[-1]["title"] == "A send waits for you" and "billing@example-utility.test" in pushed[-1]["body"]
    assert owner.get("/api/v1/approvals").json()["pending"] == 1
    failed = owner.post(f"/api/v1/approvals/{first['id']}/approve").json()
    assert failed["status"] == "failed" and "no mailbox can send" in failed["error"] and sent == []

    # With a mailbox that has an SMTP server, approving sends through it with the stored password.
    made = owner.post("/api/v1/mail/accounts", json={"kind": "proton", "label": "Proton", "username": "owner@example.test", "password": "bridge-pass"}).json()
    assert made["smtp_host"] == "bridge" and made["smtp_port"] == 1025
    second = owner.post("/api/v1/approvals", json={"to": "support@example-loan.test", "subject": "Due date", "body": "Please move it to the 13th."}).json()
    edited = owner.put(f"/api/v1/approvals/{second['id']}", json={"subject": "Due date change request"}).json()
    assert edited["subject"] == "Due date change request"
    approved = owner.post(f"/api/v1/approvals/{second['id']}/approve", json={"allow_always": True}).json()
    assert approved["status"] == "sent" and approved["message_id"] if "message_id" in approved else approved["status"] == "sent"
    assert sent[-1]["to"] == "support@example-loan.test" and sent[-1]["subject"] == "Due date change request" and sent[-1]["password"] == "bridge-pass" and sent[-1]["account"] == "Proton"
    assert owner.get("/api/v1/approvals").json()["allowances"] == ["support@example-loan.test"]

    # The allowance sends the next draft to that recipient at once; a different recipient still waits.
    auto = owner.post("/api/v1/approvals", json={"to": "Support@example-loan.test", "subject": "Follow-up", "body": "Thanks."}).json()
    assert auto["status"] == "sent" and auto["decided_by"] == "allowance" and len(sent) == 2
    waits = owner.post("/api/v1/approvals", json={"to": "other@example.test", "subject": "x", "body": "y"}).json()
    assert waits["status"] == "pending"
    denied = owner.post(f"/api/v1/approvals/{waits['id']}/deny").json()
    assert denied["status"] == "denied" and len(sent) == 2
    assert owner.post(f"/api/v1/approvals/{waits['id']}/approve").status_code == 409
    assert owner.delete("/api/v1/approvals/allowances/support@example-loan.test").json()["allowances"] == []

    # A read key cannot queue; a confirm key can queue but never approve.
    read_key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    confirm_key = owner.post("/api/v1/keys", json={"name": "confirmer", "scopes": ["read", "confirm"], "redaction": "full"}).json()["token"]
    anon = TestClient(client.app)
    assert anon.post("/api/v1/approvals", headers={"Authorization": f"Bearer {read_key}"}, json={"to": "a@b.test", "subject": "s", "body": "b"}).status_code == 403
    by_key = anon.post("/api/v1/approvals", headers={"Authorization": f"Bearer {confirm_key}"}, json={"to": "a@b.test", "subject": "s", "body": "b"})
    assert by_key.status_code == 200 and by_key.json()["created_by"] == "confirmer" and by_key.json()["status"] == "pending"
    assert anon.post(f"/api/v1/approvals/{by_key.json()['id']}/approve", headers={"Authorization": f"Bearer {confirm_key}"}).status_code == 403
    assert owner.post("/api/v1/approvals", json={"to": "not an address", "subject": "s", "body": "b"}).status_code == 400

    with Session(engine) as session:
        rows = session.exec(select(Approval)).all()
        assert {r.status for r in rows} == {"failed", "sent", "denied", "pending"}
        assert session.exec(select(MailAccount)).first().password_enc.startswith("fa1:")


def test_mcp_tool_queues_a_draft_only_with_the_confirm_scope(owner: TestClient, live: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    confirm_key = owner.post("/api/v1/keys", json={"name": "claude-phone", "scopes": ["read", "confirm"], "redaction": "full"}).json()["token"]
    read_key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]

    def call(key: str) -> dict:
        bearer = {"Authorization": f"Bearer {key}"}
        opened = live.post("/mcp", headers={**bearer, **MCP_HEADERS}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        sid = opened.headers["mcp-session-id"]
        live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = live.post("/mcp", headers={**bearer, **MCP_HEADERS, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "mail_send_request", "arguments": {"to": "billing@example-utility.test", "subject": "Due date", "body": "Please move it.", "reason": "Electricity to the 13th"}}})
        text = r.text
        start = text.find("{")
        return json.loads(text[start:]) if start >= 0 else {"raw": text}

    refused = call(read_key)
    assert "confirm" in json.dumps(refused)
    queued = call(confirm_key)
    assert "pending" in json.dumps(queued) and "must approve" in json.dumps(queued)
    listed = owner.get("/api/v1/approvals").json()
    assert listed["pending"] == 1 and listed["approvals"][0]["created_by"] == "claude-phone" and listed["approvals"][0]["created_kind"] == "key"
