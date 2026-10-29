"""The eleventh sweep, first lens: the MCP protocol itself, driven with raw JSON-RPC over /mcp.

Session ids crossing credentials, sessions outliving a revoked or expired token, batches, notification floods,
cancellation in flight, resumption from another session, oversized messages, methods outside the tool set, a
second initialize, unknown protocol versions, what tools/list shows a redacted key, and how many sessions one
credential may hold. Every crossing must be refused, nothing may leak between sessions, resources stay bounded,
and every refusal is a clean JSON-RPC error."""

import json
import threading
import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import seed
from app.mcp_server import SESSIONS_PER_CREDENTIAL, mcp_server
from app.models import Account, AuditEntry, ExtractedFact, OAuthToken, utcnow

from tests.test_sweep15_security_injection import _oauth_token

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
MODERN = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}, "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"}}


def _key(owner: TestClient, name: str, scopes: list[str], redaction: str = "full") -> dict:
    made = owner.post("/api/v1/keys", json={"name": name, "scopes": scopes, "redaction": redaction}).json()
    return {"id": made["id"], "h": {"Authorization": f"Bearer {made['token']}"}}


def _open(live: TestClient, bearer: dict) -> str:
    r = live.post("/mcp", headers={**bearer, **H}, json=INIT)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    live.post("/mcp", headers={**bearer, **H, "mcp-session-id": sid}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return sid


def _rpc(live: TestClient, bearer: dict, sid: str | None, body, **extra):  # noqa: ANN001, ANN201
    headers = {**bearer, **H, **({"mcp-session-id": sid} if sid else {}), **extra}
    return live.post("/mcp", headers=headers, json=body)


def _data(r) -> dict:  # noqa: ANN001
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return json.loads(next(x for x in r.text.splitlines() if x.startswith("data: "))[6:])


def _pending_fact() -> int:
    with Session(engine) as session:
        fact = ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Water", "amount": 64.0, "due_date": "2026-11-02"}), confidence=0.9, status="pending", excerpt="due $64.00")
        session.add(fact)
        session.commit()
        return fact.id


def test_a_session_never_crosses_to_another_credential_even_one_with_the_same_name(owner: TestClient, live: TestClient):
    full = _key(owner, "claude", ["read", "confirm"])
    namesake = _key(owner, "claude", ["read"], "aggregates")  # key names are the owner's words and need not be unique
    other = _key(owner, "script", ["read"])
    sid = _open(live, full["h"])
    listed = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    assert _rpc(live, full["h"], sid, listed).status_code == 200
    for intruder in (namesake, other):
        r = _rpc(live, intruder["h"], sid, listed)
        assert r.status_code == 404 and _data(r)["error"]["message"] == "Session not found", r.text
        stream = live.get("/mcp", headers={**intruder["h"], "Accept": "text/event-stream", "mcp-session-id": sid, "last-event-id": "1"})
        assert stream.status_code == 404  # nor resume its stream from another credential
        assert live.delete("/mcp", headers={**intruder["h"], "mcp-session-id": sid}).status_code == 404  # nor end it
    assert _rpc(live, full["h"], sid, listed).status_code == 200  # the owner's session is untouched
    assert _rpc(live, full["h"], "0" * 32, listed).status_code == 404  # a guessed id is no session
    assert _rpc(live, {}, sid, listed).status_code == 401  # and the id alone is no credential


def test_a_session_ends_with_its_token_revoked_or_expired(owner: TestClient, live: TestClient, client: TestClient):
    key = _key(owner, "laptop", ["read"])
    sid = _open(live, key["h"])
    owner.delete(f"/api/v1/keys/{key['id']}")
    assert _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).status_code == 401
    token = _oauth_token(client, ["read"])
    bearer = {"Authorization": f"Bearer {token}"}
    sid = _open(live, bearer)
    with Session(engine) as session:
        for row in session.exec(select(OAuthToken).where(OAuthToken.kind == "access")).all():
            row.expires_at = utcnow() - timedelta(seconds=1)
            session.add(row)
        session.commit()
    r = _rpc(live, bearer, sid, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "finance_snapshot", "arguments": {}}})
    assert r.status_code == 401 and "finance" not in r.text


def test_batches_floods_huge_messages_and_methods_outside_the_tools_answer_with_clean_errors_and_run_nothing(owner: TestClient, live: TestClient):
    key = _key(owner, "claude", ["read", "confirm"])
    fact = _pending_fact()
    sid = _open(live, key["h"])
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "facts_confirm" if i % 2 else "finance_snapshot", "arguments": {"fact_id": fact} if i % 2 else {}}} for i in range(1000)]
    r = _rpc(live, key["h"], sid, batch)
    assert r.status_code == 400 and "error" in _data(r)
    flood = [{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": i, "progress": 1}} for i in range(5000)]
    assert _rpc(live, key["h"], sid, flood).status_code == 400
    for i in range(200):  # one notification per request, as fast as they come: each acknowledged, nothing kept
        assert _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": i}}).status_code == 202
    huge = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "documents_search", "arguments": {"query": "x" * (10 * 1024 * 1024)}}})
    assert live.post("/mcp", headers={**key["h"], **H, "mcp-session-id": sid}, content=huge).status_code == 413
    with Session(engine) as session:
        assert session.get(ExtractedFact, fact).status == "pending"
        assert session.exec(select(AuditEntry)).all() == []  # nothing in a refused batch ran
    for method, code in (("sampling/createMessage", -32601), ("logging/setLevel", -32601), ("roots/list", -32601), ("tasks/list", -32601), ("resources/read", -32602), ("prompts/get", -32602), ("completion/complete", -32602)):
        out = _data(_rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 9, "method": method, "params": {"level": "debug", "uri": "file:///etc/passwd", "name": "x"}}))
        assert out["error"]["code"] == code, (method, out)
    assert _data(_rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 9, "method": "resources/list"}))["result"]["resources"] == []
    assert _data(_rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 9, "method": "prompts/list"}))["result"]["prompts"] == []
    # A second initialize and a version the server does not know: a known version back, the session still serves.
    again = _data(_rpc(live, key["h"], sid, {**INIT, "params": {**INIT["params"], "protocolVersion": "1999-01-01"}}))
    assert again["result"]["protocolVersion"] != "1999-01-01"
    assert _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 10, "method": "ping"}).status_code == 200
    assert _rpc(live, key["h"], None, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, **{"mcp-protocol-version": "2099-01-01"}).status_code == 400


def test_the_newer_protocol_path_keeps_the_same_scopes_and_redaction(owner: TestClient, live: TestClient):
    reader = _key(owner, "reader", ["read"], "aggregates")
    fact = _pending_fact()
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "facts_confirm", "arguments": {"fact_id": fact}, "_meta": MODERN}}
    r = live.post("/mcp", headers={**reader["h"], **H, "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": "facts_confirm"}, json=call)
    assert r.status_code == 200 and "no confirm scope" in r.text
    assert live.post("/mcp", headers={**H, "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": "facts_confirm"}, json=call).status_code == 401
    with Session(engine) as session:
        assert session.get(ExtractedFact, fact).status == "pending"


def test_tools_list_carries_no_household_data_for_a_redacted_key(owner: TestClient, live: TestClient):
    with Session(engine) as session:
        seed(session)
        names = [a.name for a in session.exec(select(Account)).all()]
    key = _key(owner, "reader", ["read"], "aggregates")
    sid = _open(live, key["h"])
    listed = _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).text
    assert names and not [n for n in names if n in listed]
    assert "facts_confirm" in listed  # listed for everyone; refused by scope when called


def test_one_credential_holds_a_bounded_number_of_sessions_and_cannot_lock_others_out(owner: TestClient, live: TestClient):
    noisy = _key(owner, "leaked", ["read"])
    quiet = _key(owner, "owner's claude", ["read"])
    first = _open(live, noisy["h"])
    for _ in range(SESSIONS_PER_CREDENTIAL * 3):
        assert live.post("/mcp", headers={**noisy["h"], **H}, json=INIT).status_code == 200
    manager = mcp_server.session_manager
    held = [sid for sid, who in manager._session_owners.items() if who.get("client_id") == "leaked" and str(who.get("subject", "")).startswith(f"key:{noisy['id']}:")]
    assert len(held) <= SESSIONS_PER_CREDENTIAL
    assert _rpc(live, noisy["h"], first, {"jsonrpc": "2.0", "id": 2, "method": "ping"}).status_code == 404  # its oldest was ended
    assert _open(live, quiet["h"])  # another credential is unaffected


def test_a_cancelled_write_does_not_land_and_says_so(owner: TestClient, live: TestClient, monkeypatch):
    from app.api import mail as mail_api

    key = _key(owner, "claude", ["read", "confirm"])
    fact = _pending_fact()
    sid = _open(live, key["h"])
    real = mail_api.apply_fact

    def slow(*a, **k):  # noqa: ANN002, ANN003, ANN202
        time.sleep(1.0)
        return real(*a, **k)

    monkeypatch.setattr(mail_api, "apply_fact", slow)
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 77, "method": "tools/call", "params": {"name": "facts_confirm", "arguments": {"fact_id": fact}}})))
    t.start()
    time.sleep(0.4)
    assert _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 77}}).status_code == 202
    t.join(10)
    assert _data(out["r"])["error"]["message"] == "Request cancelled"
    deadline = time.time() + 5
    while time.time() < deadline:
        with Session(engine) as session:
            tools = [a.tool for a in session.exec(select(AuditEntry)).all()]
        if tools:
            break
        time.sleep(0.1)
    with Session(engine) as session:
        assert session.get(ExtractedFact, fact).status == "pending"  # told "cancelled", and nothing landed
    assert tools == ["facts_confirm_cancelled"]
    assert _rpc(live, key["h"], sid, {"jsonrpc": "2.0", "id": 78, "method": "ping"}).status_code == 200  # the session lives on
