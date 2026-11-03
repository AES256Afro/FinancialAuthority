"""Security sweep 23 (the eighteenth sweep): five attacker stories, end to end.

a. A stolen, unlocked iPhone: the Lock Screen widget's figures are privacy sensitive.
b. A mail campaign: a look-alike domain the attacker registered and signed (DMARC passes) was "confirmed from the
   mail server", learned as the brand's own, and a confirm-scoped app could confirm what it said.
d. A second person with "everything": they could point the owner's notifications at their own server or remove the
   owner's phone, and after one "always" their sends left in the mailbox's name with no word to the owner.

Attacker domains are under .test; the household is the synthetic one."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, auth, notify, webpush
from app.connectors.mail import handle_message
from app.db import engine
from app.engine.datacheck import _forget_learned, learned_senders
from app.mail.imap import parse_message
from app.mail.trust import judge
from app.main import app
from app.models import Bill, ExtractedFact, MailAccount, MessageRef
from app.store import get_setting, set_setting

APPLE = Path(__file__).resolve().parents[2] / "apple"
PASSED = "mx.example.test; dmarc=pass header.from=capitalonebank.test; dkim=pass; spf=pass"
H = {"X-Requested-With": "FinancialAuthority"}


# ---- a. the stolen phone ----------------------------------------------------------------------------------------


@pytest.mark.skipif(not (APPLE / "Widgets" / "GlanceWidget.swift").exists(), reason="the Apple app is not in this checkout")
def test_a_every_figure_the_lock_screen_widget_draws_is_privacy_sensitive():
    """A Lock Screen or a watch face is seen by whoever holds the device: safe to spend and the one thing are drawn
    redacted while it is locked, whatever Hide amounts says in the web app (the app never read it)."""
    source = (APPLE / "Widgets" / "GlanceWidget.swift").read_text()
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"Text\((g\.safe_to_spend|Format\.safe\(g\.safe_to_spend\)|g\.one_thing)", line):
            window = "\n".join(lines[i : i + 4])
            assert ".privacySensitive()" in window, f"line {i + 1} draws a figure without .privacySensitive(): {line.strip()}"


# ---- b. the mail campaign ---------------------------------------------------------------------------------------


def test_b_a_brand_name_from_a_domain_that_only_starts_like_the_brands_is_not_confirmed():
    """capitalonebank.test, registered and signed by the attacker, passes DMARC; it was "Confirmed from
    capitalonebank.test by the mail server" because a domain starting with the brand's name counted as the brand's."""
    for sender, name in (("alerts@capitalonebank.test", "Capital One"), ("alerts@capitalone-servicing.test", "Capital One"), ("notice@chasebank.test", "Chase")):
        verdict, why = judge(sender, name, PASSED)
        assert verdict == "warn", (sender, verdict, why)
        assert "not one of its known domains" in why
    # The brand's own domains, the brand's own ending, and a domain the owner marked genuine stay confirmed.
    assert judge("alerts@capitalone.com", "Capital One", PASSED)[0] == "ok"
    assert judge("no-reply@notifications.health.amazon", "Amazon", PASSED)[0] == "ok"
    assert judge("alerts@capitalonebank.test", "Capital One", PASSED, "", {"capital one": {"capitalonebank.test"}})[0] == "ok"


def _mailbox(session: Session) -> MailAccount:
    box = MailAccount(label="Household", kind="imap", host="imap.example.test", username="owner@example.test")
    session.add(box)
    session.commit()
    session.refresh(box)
    return box


def _raw(sender: str, name: str, subject: str, body: str, auth_results: str = PASSED, reply_to: str = "") -> bytes:
    head = f"Authentication-Results: {auth_results}\r\nFrom: {name} <{sender}>\r\nTo: owner@example.test\r\nSubject: {subject}\r\n"
    head += f"Reply-To: {reply_to}\r\n" if reply_to else ""
    head += "Message-ID: <" + subject.replace(" ", "-") + "@example.test>\r\nDate: Tue, 13 Oct 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n"
    return (head + body + "\r\n").encode()


def _key(scopes: set[str]) -> TestClient:
    with Session(engine) as session:
        _, token = auth.create_key(session, "Example assistant", scopes)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def test_b_the_campaign_a_confirm_scoped_app_confirms_nothing_from_a_signed_look_alike(owner: TestClient, monkeypatch):
    """A month of mail from a lender the owner banks at, read daily by a confirm-scoped app: a signed look-alike,
    a bait domain, a reply-to elsewhere and a fake past due. None of it is the app's to confirm; the bill on the
    register keeps its amount; the look-alike is never learned as the brand's own."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    _forget_learned()
    campaign = [
        ("alerts@capitalonebank.test", "Capital One", "Your payment is due", "Your Capital One payment of $480.00 is due on 10/20/2026.", ""),
        ("billing@capitalone-alerts.test", "Capital One", "Your payment is past due", "Your account is past due. Amount due $950.00 by 10/18/2026.", ""),
        ("alerts@capitalonebank.test", "Capital One", "A price change to your account", "Your new annual fee is $395.00 starting 10/25/2026.", ""),
        ("notices@capitalonebank.test", "Capital One", "Your bill is due", "Amount due $480.00 on 10/20/2026.", "pay@collect-now.test"),
    ]
    with Session(engine) as session:
        session.add(Bill(name="Capital One", amount=120.0, due_day=20))
        session.commit()
        box = _mailbox(session)
        for uid, (sender, name, subject, body, reply) in enumerate(campaign, start=1):
            handle_message(session, box, "INBOX", uid, parse_message(_raw(sender, name, subject, body, PASSED.replace("capitalonebank.test", sender.split("@")[1]), reply)), date(2026, 10, 14))
        session.commit()
        refs = {r.uid: r.trust for r in session.exec(select(MessageRef)).all()}
        facts = [(f.id, f.kind, f.message_ref_id) for f in session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()]
    assert refs == {1: "warn", 2: "crit", 3: "warn", 4: "warn"}, refs
    assert facts, "the campaign's notices wait on Needs you"
    _forget_learned()
    with Session(engine) as session:
        assert "capitalonebank.test" not in learned_senders(session).get("capital one", set())
    app_client = _key({"read", "confirm"})
    with Session(engine) as session:
        warned = {r.id for r in session.exec(select(MessageRef).where(MessageRef.trust.in_(["warn", "crit"]))).all()}
    for fid, _kind, ref_id in facts:
        if ref_id in warned:
            answer = app_client.post(f"/api/v1/facts/{fid}/confirm", json={})
            assert answer.status_code == 403, answer.text
    many = app_client.post("/api/v1/needs-you/facts", json={"ids": [fid for fid, _k, r in facts if r in warned], "action": "confirm"})
    assert many.status_code == 200, many.text
    with Session(engine) as session:
        answered = {f.id: (f.status, f.applied_to) for f in session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id.in_(warned))).all()}
    assert not [a for a in answered.values() if a[0] in ("confirmed", "applied")], answered
    with Session(engine) as session:
        assert session.exec(select(Bill).where(Bill.name == "Capital One")).one().amount == 120.0
    # The owner, who sees the reason on the card, may still confirm it.
    fid = next(fid for fid, (status, _a) in answered.items() if status == "pending")
    assert owner.post(f"/api/v1/facts/{fid}/confirm", json={}).status_code == 200


def test_b_the_mcp_tool_refuses_the_same(monkeypatch):
    """facts_confirm, the tool a connected app calls, goes through the same refusal."""
    from app.api.mail import ConfirmIn, apply_fact
    from fastapi import HTTPException

    with Session(engine) as session:
        box = _mailbox(session)
        ref = MessageRef(account_id=box.id, folder="INBOX", uid=1, message_id="<w@example.test>", sender="alerts@capitalonebank.test", sender_name="Capital One", trust="warn")
        session.add(ref)
        session.commit()
        fact = ExtractedFact(kind="bill_due", message_ref_id=ref.id, fields=json.dumps({"amount": 480.0, "due_date": "2026-10-20", "name": "Capital One"}), confidence=0.9, status="pending")
        session.add(fact)
        session.commit()
        with pytest.raises(HTTPException) as refused:
            apply_fact(session, fact, ref, ConfirmIn(), owner=False)
        assert refused.value.status_code == 403


# ---- d. the second person with "everything" -------------------------------------------------------------------


def _everything(owner: TestClient) -> TestClient:
    made = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"})
    assert made.status_code == 200, made.text
    c = TestClient(app)
    c.headers.update(H)
    assert c.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": "maple harbour winter nine"}).status_code == 200
    return c


def test_d_a_second_person_cannot_point_the_owners_notifications_elsewhere(owner: TestClient):
    person = _everything(owner)
    assert owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "owner-topic"}).status_code == 200
    answer = person.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.attacker.test", "topic": "mine"})
    assert answer.status_code == 403
    assert "attacker" not in json.dumps(owner.get("/api/v1/push").json())


def test_d_a_second_person_removes_only_the_devices_they_added(owner: TestClient):
    person = _everything(owner)
    pid = person.get("/api/auth/me").json()["person_id"]
    with Session(engine) as session:
        set_setting(session, webpush.SUBS, [{"id": "owner-phone", "label": "Owner phone", "endpoint": "https://push.example.test/a"},
                                            {"id": "sam-phone", "label": "Sam phone", "endpoint": "https://push.example.test/b", "person_id": pid}])
    assert person.delete("/api/v1/push/devices/owner-phone").status_code == 403
    assert person.delete("/api/v1/push/devices/sam-phone").status_code == 200
    with Session(engine) as session:
        assert [s["id"] for s in get_setting(session, webpush.SUBS, [])] == ["owner-phone"]
    assert owner.delete("/api/v1/push/devices/owner-phone").status_code == 200


def test_d_every_send_a_second_person_makes_reaches_the_owner(owner: TestClient, monkeypatch):
    """After one approval with "always", later sends to that address went out under the person's own allowance in
    the mailbox's name, and nothing told the owner."""
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, body, **k: pushed.append(title) or {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: "<id@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Household", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test"))
        session.commit()
    person = _everything(owner)
    first = person.post("/api/v1/approvals", json={"to": "help@lender.test", "subject": "Change my address", "body": "Please."}).json()
    assert person.post(f"/api/v1/approvals/{first['id']}/approve", json={"allow_always": True}).json()["status"] == "sent"
    later = person.post("/api/v1/approvals", json={"to": "help@lender.test", "subject": "And the phone", "body": "Please."}).json()
    assert later["status"] == "sent"
    sent_pushes = [t for t in pushed if t.startswith("Sam Example sent mail from Household")]
    assert len(sent_pushes) == 2, pushed


# ---- c. a compromised model provider ---------------------------------------------------------------------------


def _chat_with(provider) -> tuple[list[dict], list[list[dict]]]:  # noqa: ANN001
    import asyncio

    from app.db import session_scope
    from app.llm.advisor import advise

    async def collect():
        return [e async for e in advise([{"role": "user", "content": "What is due?"}], provider=provider, session_factory=session_scope, today=date(2026, 10, 14))]

    return asyncio.run(collect()), provider.seen


@pytest.mark.parametrize("cloud", ["anthropic", "openai"])
def test_c_a_cloud_provider_is_never_handed_a_message_body_or_a_document(cloud):
    """A provider behind a cloud key that answers whatever it likes asked for every message and document: the owner
    was told those servers get the plan and never the mail, and at "full" the chat read them out to it."""
    from app.engine.documents import save_text
    from app.llm.providers import Event, Scripted

    with Session(engine) as session:
        box = _mailbox(session)
        session.add(MessageRef(account_id=box.id, folder="INBOX", uid=1, message_id="<b@example.test>", sender="billing@example-power.test", sender_name="Example Power", subject="Bill"))
        session.commit()
        doc = save_text(session, "Lease for Unit 4. The tenant's private note.", filename="lease", title="Lease", kind="lease")

    class Cloud(Scripted):
        id = cloud

    provider = Cloud([[Event("tool_call", id="1", name="mail_read", arguments={"message_id": 1}), Event("tool_call", id="2", name="document_read", arguments={"document_id": doc.id}),
                       Event("tool_call", id="3", name="documents_search", arguments={"query": "tenant"}), Event("tool_call", id="4", name="mail_attachment", arguments={"message_id": 1, "name": "a.pdf"})],
                      [Event("text", text="Done.")]])
    events, seen = _chat_with(provider)
    assert events[-1]["type"] == "done"
    offered_names = json.dumps(seen[0][0]["content"])  # the prompt; the specs are checked below through the answers
    sent_back = [m for m in seen[1] if m.get("role") == "tool"]
    assert len(sent_back) == 4 and all("not available in the chat" in m["content"] for m in sent_back), sent_back
    assert "private note" not in json.dumps(seen) and "private note" not in offered_names


def test_c_ollama_on_the_box_still_reads_the_mail():
    """The owner's own model on the box keeps the tools (the owner chose "full" for it)."""
    from app.llm.advisor import BODY_TOOLS, CHAT_TOOLS

    assert BODY_TOOLS <= set(CHAT_TOOLS)
