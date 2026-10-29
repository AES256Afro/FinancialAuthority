"""Security sweep 9: the fifth round's replication pass. Each test is a sibling of a fix made earlier today, found by
walking every other code path that does the same thing: a refresh token spent twice at once (the authorization
code was fixed), a join on the old link beside a new invitation (the join itself was fixed), a draft edited as it
is approved (approve and deny were fixed), names that rode under keys the redactor did not know, a key's refused
REST call with no audit row (refused tool calls were fixed), and a register write beside a running detection."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, notify, people
from app import mcp_server as mcp
from app.db import engine
from app.engine.household import seed
from app.main import app
from app.models import (
    Approval,
    AuditEntry,
    Bill,
    Business,
    Goal,
    Home,
    MailAccount,
    OAuthClient,
    OAuthToken,
    Person,
    utcnow,
)

H = {"X-Requested-With": "FinancialAuthority"}
GOOD = "plum river lantern seven"
TODAY = date(2026, 10, 14)


def _call(name: str, args: dict, level: str) -> dict:
    token = mcp.CALLER.set(("sweep9", level))
    try:
        result = asyncio.run(mcp.mcp_server.call_tool(name, args))
    finally:
        mcp.CALLER.reset(token)
    return result.structured_content or json.loads("".join(getattr(c, "text", "") for c in result.content))


# ---- a refresh token is spent once --------------------------------------------------------------------


def test_a_refresh_token_is_spent_once(client: TestClient):
    """The SDK loads the refresh token (live for both of two refreshes in the same instant) and then exchanges it;
    the exchange itself spends the row in one statement, so the second refresh gets no second pair."""
    from mcp.server.auth.provider import RefreshToken, TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from app import oauth

    with Session(engine) as session:
        session.add(OAuthClient(client_id="app-1", name="An app", redirect_uris='["http://localhost:9999/callback"]'))
        session.add(OAuthToken(token_hash=oauth._hash("fa_or_once"), kind="refresh", client_id="app-1", scopes="read", resource=oauth.issuer(), expires_at=utcnow() + timedelta(days=1)))
        session.commit()
    info = OAuthClientInformationFull(client_id="app-1", redirect_uris=[AnyUrl("http://localhost:9999/callback")])
    old = RefreshToken(token="fa_or_once", client_id="app-1", scopes=["read"], expires_at=int(time.time()) + 3600, resource=oauth.issuer())

    async def twice():
        first = await oauth.provider.exchange_refresh_token(info, old, ["read"])
        try:
            await oauth.provider.exchange_refresh_token(info, old, ["read"])
        except TokenError as error:
            return first, error
        return first, None

    tokens, refused = asyncio.run(twice())
    assert tokens.access_token.startswith("fa_oa_") and tokens.refresh_token.startswith("fa_or_")
    assert refused is not None and refused.error == "invalid_grant"
    with Session(engine) as session:
        rows = session.exec(select(OAuthToken).where(OAuthToken.client_id == "app-1")).all()
        assert len(rows) == 3  # the spent one, one access, one refresh: never a second pair
        spent = next(r for r in rows if r.token_hash == oauth._hash("fa_or_once"))
        assert spent.revoked_at is not None
        assert all(r.revoked_at is None for r in rows if r is not spent)


# ---- a join on the old link beside a new invitation ------------------------------------------------------


def test_a_join_on_the_old_link_never_outlives_a_reset_made_at_the_same_moment(owner: TestClient, monkeypatch):
    """The owner replaces a person's invitation (a lost phone, a link that leaked) while someone redeems the old link
    in the same instant. Both take the invitation lock, so either the join lands first and the reset undoes it, or
    the reset lands first and the old link is gone: the old link can never set a password that survives."""
    made = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "shared"}).json()
    token, pid = made["invite"]["link"].split("#", 1)[1], made["invite"]["person_id"]
    inside, go = threading.Event(), threading.Event()
    real, calls = people.by_invite, []

    def slow(session, tok):
        found = real(session, tok)
        calls.append(tok)
        if len(calls) == 2:  # the read under the invitation lock: the join holds the lock here with the person loaded
            inside.set()
            go.wait(timeout=10)
        return found

    monkeypatch.setattr(people, "by_invite", slow)
    joiner = TestClient(app)
    joiner.headers.update(H)
    joined: list = []
    reset: list = []
    threading.Thread(target=lambda: joined.append(joiner.post("/api/auth/join", json={"token": token, "password": GOOD}))).start()
    assert inside.wait(timeout=10)
    other = TestClient(app)
    assert other.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).status_code == 200
    other.headers.update(H)
    resetter = threading.Thread(target=lambda: reset.append(other.post(f"/api/v1/people/{pid}/invite")))
    resetter.start()
    resetter.join(timeout=0.5)
    assert reset == [], "the reset ran beside the join"  # it waits for the invitation lock
    go.set()
    resetter.join(timeout=20)
    time.sleep(0.1)
    assert joined and joined[0].status_code == 200, joined and joined[0].text  # the join landed, first
    assert reset and reset[0].status_code == 200, reset and reset[0].text  # then the reset undid it
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"username": "sam", "password": GOOD}).status_code == 401  # the password from the old link is gone
    new_token = reset[0].json()["invite"]["link"].split("#", 1)[1]
    assert new_token != token
    with Session(engine) as session:
        person = session.get(Person, pid)
        assert person.password_hash == "" and person.invite_hash != ""
    monkeypatch.setattr(people, "by_invite", real)
    again = TestClient(app)
    again.headers.update(H)
    assert again.post("/api/auth/join", json={"token": token, "password": GOOD}).status_code == 400  # the old link is dead
    assert again.post("/api/auth/join", json={"token": new_token, "password": GOOD}).status_code == 200


# ---- a draft edited as it is approved ---------------------------------------------------------------------


def test_a_draft_cannot_be_rewritten_once_it_is_approved_even_from_a_stale_read(owner: TestClient, monkeypatch):
    """Approve and deny claim the row under the decision lock; an edit must read it again under the same lock, or an
    edit that read the draft as pending could change the recipient after the approve sent it, and the record would
    say the owner approved a send to someone else."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: sent.append((kw["to"], kw["subject"])) or "<id@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test", smtp_port=465))
        session.commit()
    queued = owner.post("/api/v1/approvals", json={"to": "billing@example-power.test", "subject": "Move my date", "body": "Please move it."}).json()
    stale = Session(engine)
    row = stale.get(Approval, queued["id"])
    assert row.status == "pending"  # read as pending, before the approve
    with Session(engine) as session:
        approvals.approve(session, session.get(Approval, queued["id"]), decided_by="owner")
    assert sent == [("billing@example-power.test", "Move my date")]
    with pytest.raises(ValueError):
        approvals.edit_draft(stale, row, {"to": "elsewhere@example-power.test", "subject": "Cancel everything"})
    stale.close()
    with Session(engine) as session:
        kept = session.get(Approval, queued["id"])
        assert kept.status == "sent" and kept.to == "billing@example-power.test" and kept.subject == "Move my date"
    assert owner.put(f"/api/v1/approvals/{queued['id']}", json={"to": "elsewhere@example-power.test"}).status_code == 409
    # and the edit waits for the decision lock, as approve and deny do
    pending = owner.post("/api/v1/approvals", json={"to": "office@example-flats.test", "subject": "Notice", "body": "Moving out."}).json()
    release, held = threading.Event(), threading.Event()

    def hold() -> None:
        with approvals._decide:
            held.set()
            release.wait(timeout=20)

    threading.Thread(target=hold).start()
    assert held.wait(timeout=5)
    done: list = []
    editor = threading.Thread(target=lambda: done.append(owner.put(f"/api/v1/approvals/{pending['id']}", json={"subject": "Notice to vacate"})))
    editor.start()
    editor.join(timeout=0.5)
    assert done == [], "the edit ran beside a decision"
    release.set()
    editor.join(timeout=20)
    assert done and done[0].status_code == 200 and done[0].json()["subject"] == "Notice to vacate"


def test_ending_a_persons_access_cancels_their_sends_under_the_decision_lock(owner: TestClient, monkeypatch):
    """Ending access denies a person's pending sends. It claims each under the decision lock, as approve and deny do:
    one the person approved in the same instant is sent and stays sent, never marked denied after the fact."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: sent.append(kw["to"]) or "<id@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test", smtp_port=465))
        session.commit()
    made = owner.post("/api/v1/people", json={"username": "alex", "display_name": "Alex Example", "role": "everything"}).json()
    token, pid = made["invite"]["link"].split("#", 1)[1], made["invite"]["person_id"]
    person = TestClient(app)
    person.headers.update(H)
    assert person.post("/api/auth/join", json={"token": token, "password": GOOD}).status_code == 200
    queued = person.post("/api/v1/approvals", json={"to": "office@example-flats.test", "subject": "Notice", "body": "Moving out."}).json()
    assert queued["for_person_id"] == pid and queued["status"] == "pending"
    real_claim = approvals._claim
    read, go = threading.Event(), threading.Event()

    def claim_after_the_approve(session, row, status, decided_by):
        if decided_by == "access ended":  # ending access has read the row as pending; the person's approve lands now
            read.set()
            go.wait(timeout=10)
        return real_claim(session, row, status, decided_by)

    monkeypatch.setattr(approvals, "_claim", claim_after_the_approve)
    done: list = []
    ender = threading.Thread(target=lambda: done.append(owner.put(f"/api/v1/people/{pid}", json={"active": False})))
    ender.start()
    assert read.wait(timeout=10)
    with Session(engine) as session:
        approvals.approve(session, session.get(Approval, queued["id"]), decided_by="alex")
    go.set()
    ender.join(timeout=20)
    assert done and done[0].status_code == 200, done and done[0].text
    assert sent == ["office@example-flats.test"]
    with Session(engine) as session:
        row = session.get(Approval, queued["id"])
        assert row.status == "sent" and row.decided_by == "alex"  # ending access read it again under the lock, found it decided and left it


# ---- names under keys the redactor did not know --------------------------------------------------------------


def test_names_under_their_own_keys_go_at_the_aggregates_level():
    """Walking every tool's answer at the aggregates level: the advance apps used since payday, the debts in payoff
    order, a plan's lender, the loans a tax check saw, a business, the one thing the glance names and the cover
    sheet's actions all carried names under keys of their own."""
    with Session(engine) as session:
        seed(session)
        session.add(Business(name="Example Side Studio", what_it_does="design work", started_on=date(2026, 3, 1)))
        session.commit()
    names = {
        "finance_snapshot": ["Example Advance", "Example Cash App"], "debts_list": ["Rewards Card", "Store Card", "Auto loan"], "borrowing_plan": ["Example BNPL"],
        "tax_year": ["Auto loan", "Example Side Studio"], "business_books": ["Example Side Studio"], "glance": ["Example Advance"], "accounts_cover": ["Everyday Checking", "Rewards Card"],
    }
    for tool, words in names.items():
        args = {"year": 2026} if tool == "tax_year" else {}
        plain = json.dumps(_call(tool, args, "full"))
        for word in words:
            assert word in plain, (tool, word)  # the name is in the data, so its absence below is the redactor's doing
        hidden = json.dumps(_call(tool, args, "aggregates"))
        for word in words:
            assert word not in hidden, (tool, word)
    sheet = _call("accounts_cover", {}, "aggregates")["sheet"]
    assert sheet and all(line["action"] == line["kind"] for line in sheet)  # the verb stays: pay, keep, move


# ---- a key's refused REST call leaves a row -------------------------------------------------------------------


def test_a_keys_refused_rest_call_is_audited_too(owner: TestClient):
    """Refused tool calls write a row; a key refused on a REST route (no scope, no approve for a key, a key that hides
    details) must leave one as well, so the log shows a key asking for more than it holds."""
    reader = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"]}).json()["token"]
    hider = owner.post("/api/v1/keys", json={"name": "hider", "scopes": ["read"], "redaction": "merchants"}).json()["token"]
    bare = TestClient(app)
    assert bare.post("/api/v1/approvals", headers={"Authorization": f"Bearer {reader}"}, json={"to": "a@example.test", "subject": "s", "body": "b"}).status_code == 403
    assert bare.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {reader}"}).status_code == 200
    assert bare.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {hider}"}).status_code == 403
    with Session(engine) as session:
        rows = session.exec(select(AuditEntry).where(AuditEntry.client_kind == "key")).all()
    tools = {(r.client, r.tool) for r in rows}
    assert ("reader", "rest POST /api/v1/approvals_refused") in tools, tools
    assert ("reader", "rest GET /api/v1/snapshot") in tools, tools
    assert ("hider", "rest GET /api/v1/snapshot_refused") in tools, tools
    assert all(r.rows == 0 for r in rows)


# ---- a register write beside a running detection --------------------------------------------------------------


def test_a_register_write_waits_for_the_writer_then_says_so(owner: TestClient, monkeypatch):
    """A bill typed while a detection runs came out twice: the detection read the registers before the bill and added
    its own. Register writes take the writer lock; one that cannot get it in a moment answers 409 with the reason,
    never a duplicate and never a request held open."""
    from app.api import routes
    from app.jobs.writing import one_writer

    monkeypatch.setattr(routes, "WRITER_WAIT", 0.3)
    release, held = threading.Event(), threading.Event()

    def hold() -> None:
        with one_writer():  # a detection mid-run
            held.set()
            release.wait(timeout=20)

    threading.Thread(target=hold).start()
    assert held.wait(timeout=5)
    tries = [
        lambda: owner.post("/api/v1/bills", json={"name": "Example Water", "amount": 40.0, "due_day": 5}),
        lambda: owner.post("/api/v1/goals", json={"name": "Cushion", "target": 500.0}),
        lambda: owner.post("/api/v1/housing", json={"name": "Example Flats", "kind": "rent"}),
        lambda: owner.post("/api/v1/investments", json={"name": "Example IRA", "subtype": "ira", "balance": 100.0}),
    ]
    for attempt in tries:
        answer = attempt()
        assert answer.status_code == 409 and "try again" in answer.json()["detail"], answer.text
    with Session(engine) as session:
        assert session.exec(select(Bill).where(Bill.name == "Example Water")).all() == []
        assert session.exec(select(Goal)).all() == [] and session.exec(select(Home)).all() == []
    release.set()
    time.sleep(0.05)
    for attempt in tries:
        assert attempt().status_code == 200
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.name == "Example Water")).all()) == 1


# The enumeration of register writes that held a hand list of modules and exceptions lives on, derived from the
# running app, as test_every_register_write_route_runs_under_the_writer in test_sweep20_security_access.py: every
# write route is called and every statement it sends watched, so a route added anywhere without the lock fails.
