"""Security sweep 25 (the nineteenth sweep): the life of a record.

Earlier sweeps asked who may reach a record now. This one asks what is left of a right or a record after the event that
should have ended it: a person removed or moved to "shared", a mailbox ended, a sign-out, a key or app revoked, an erase,
a restore to an earlier moment, and whether a "warn" or "crit" verdict can come back as a clean one by a later sync, a
copy of the message, or a name written a little differently.

Attacker domains are under .test; the household is synthetic."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, crypto, notify, restore, webpush
from app.connectors import CONNECTORS
from app.connectors.mail import domains_under_name, handle_message, vouched_domains
from app.db import engine
from app.engine import datacheck
from app.engine.datacheck import TRUSTED, _forget_learned, learned_senders, recheck_senders
from app.mail.imap import parse_message
from app.main import app
from app.models import ApiKey, Approval, BankLink, ExtractedFact, MailAccount, MessageRef, OAuthClient, Person
from app.oauth import revoke_client
from app.store import get_setting, set_setting

TODAY = date(2026, 10, 14)
H = {"X-Requested-With": "FinancialAuthority"}
PW = "correct horse battery"


def passed(domain: str) -> str:
    return f"mx.example.test; dmarc=pass header.from={domain}; dkim=pass header.d={domain}; spf=pass smtp.mailfrom={domain}"


def failed(domain: str) -> str:
    return f"mx.example.test; dmarc=fail header.from={domain}; dkim=fail header.d={domain}; spf=fail smtp.mailfrom={domain}"


@pytest.fixture(autouse=True)
def fresh_learning(monkeypatch):
    _forget_learned()
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    yield
    _forget_learned()


def _raw(sender: str, name: str, auth_results: str, *, subject: str = "Your bill is due", body: str = "Amount due $120.00 on 10/20/2026.", message_id: str = "<a@example.test>") -> bytes:
    return (f"Authentication-Results: {auth_results}\r\nFrom: {name} <{sender}>\r\nTo: owner@example.test\r\nSubject: {subject}\r\nMessage-ID: {message_id}\r\n"
            f"Date: Tue, 13 Oct 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n{body}\r\n").encode()


def _box(session: Session, person_id: int | None = None, username: str = "owner@example.test") -> MailAccount:
    box = MailAccount(label="A mailbox", kind="imap", host="imap.example.test", username=username, password_enc=crypto.encrypt("pw"), person_id=person_id)
    session.add(box)
    session.commit()
    session.refresh(box)
    return box


def _land(session: Session, box: MailAccount, uid: int, raw: bytes) -> MessageRef | None:
    handle_message(session, box, "INBOX", uid, parse_message(raw), TODAY)
    session.commit()
    return session.exec(select(MessageRef).where(MessageRef.account_id == box.id, MessageRef.uid == uid)).first()


def _join(owner: TestClient, role: str = "everything", username: str = "sam") -> tuple[TestClient, int]:
    made = owner.post("/api/v1/people", json={"username": username, "display_name": "Sam Example", "role": role})
    assert made.status_code == 200, made.text
    person_id = made.json()["invite"]["person_id"]
    c = TestClient(app)
    c.headers.update(H)
    assert c.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": "maple harbour winter nine"}).status_code == 200
    return c, person_id


# ---- what a removed person leaves behind ---------------------------------------------------------------------------


class FakePlaid:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": "access-sandbox-sam", "item_id": "item-sam"})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": [
                {"account_id": "sam-chk", "name": "Sam Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": 900.0}}]})
        if path == "/transactions/sync":
            return httpx.Response(200, json={"added": [], "modified": [], "removed": [], "next_cursor": "c1", "has_more": False})
        if path == "/item/remove":
            self.removed.append(body["access_token"])
            return httpx.Response(200, json={"removed": True})
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-1"})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


@pytest.fixture
def fake_plaid():
    fake = FakePlaid()
    connector = CONNECTORS["plaid"]
    before = connector.client_factory
    connector.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(fake.handler))
    yield fake
    connector.client_factory = before


@pytest.mark.parametrize("how", ["removed", "moved to shared", "turned off"])
def test_a_bank_a_second_person_linked_ends_with_their_access(owner: TestClient, fake_plaid: FakePlaid, how: str):
    """Their bank sign-in, through the owner's vendor keys: the app went on reading their accounts every few hours,
    for good, after they were gone, and nothing they could do from outside would stop it."""
    assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"}).status_code == 200
    sam, person_id = _join(owner)
    linked = sam.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-sam", "institution": "Example Bank"})
    assert linked.status_code == 200, linked.text
    if how == "removed":
        assert owner.delete(f"/api/v1/people/{person_id}").status_code == 200
    elif how == "moved to shared":
        assert owner.put(f"/api/v1/people/{person_id}", json={"role": "shared"}).status_code == 200
    else:
        assert owner.put(f"/api/v1/people/{person_id}", json={"active": False}).status_code == 200
    with Session(engine) as session:
        link = session.exec(select(BankLink)).one()
        assert link.active is False and link.access_token_enc == "", "the link goes on reading their bank"
    assert fake_plaid.removed == ["access-sandbox-sam"], "the token is ended at the vendor too"
    names = [a["name"] for a in owner.get("/api/v1/accounts").json()["accounts"]]
    assert "Sam Checking" not in names, "a balance nothing refreshes goes on counting in the plan"


def test_the_owners_own_link_is_not_ended_with_a_second_persons_access(owner: TestClient, fake_plaid: FakePlaid):
    owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "s3cret-value-77", "environment": "sandbox"})
    assert owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-own", "institution": "Example Bank"}).status_code == 200
    sam, person_id = _join(owner)
    # The same sign-in linked again by the second person does not make the owner's link theirs.
    sam.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox-own", "institution": "Example Bank"})
    assert owner.delete(f"/api/v1/people/{person_id}").status_code == 200
    with Session(engine) as session:
        assert session.exec(select(BankLink)).one().active is True
    assert fake_plaid.removed == []


def _their_mail(session: Session, person_id: int) -> MailAccount:
    box = _box(session, person_id, username="sam@example.test")
    _land(session, box, 1, _raw("billing@sams-lender.example.test", "Sams Lender", passed("sams-lender.example.test"), subject="Sam's private loan", body="Amount due $120.00 on 10/20/2026.", message_id="<s1@example.test>"))
    return box


def test_a_removed_persons_mail_is_no_longer_readable(owner: TestClient):
    """Their mailbox stopped, but every subject, snippet and sender it had yielded stayed searchable by every sign-in,
    key and model, for good, and what it said went on waiting to be confirmed."""
    _, person_id = _join(owner)
    with Session(engine) as session:
        box_id = _their_mail(session, person_id).id
        assert session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).first() is not None
    assert owner.get("/api/v1/mail/messages", params={"q": "private loan"}).json()["returned"] == 1
    assert owner.delete(f"/api/v1/people/{person_id}").status_code == 200
    assert owner.get("/api/v1/mail/messages", params={"q": "private loan"}).json()["returned"] == 0
    assert owner.get("/api/v1/mail/messages", params={"sender": "sams-lender"}).json()["returned"] == 0
    assert owner.get("/api/v1/facts").json()["facts"] == []
    assert "sam@example.test" not in json.dumps(owner.get("/api/v1/mail/accounts").json())
    with Session(engine) as session:
        assert session.get(MailAccount, box_id).username == ""


def test_what_a_removed_person_read_and_the_owner_confirmed_stays_as_a_source(owner: TestClient):
    _, person_id = _join(owner)
    with Session(engine) as session:
        _their_mail(session, person_id)
        fact = session.exec(select(ExtractedFact)).one()
        fact_id = fact.id
    assert owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": False}).status_code == 200
    assert owner.delete(f"/api/v1/people/{person_id}").status_code == 200
    with Session(engine) as session:
        kept = session.get(ExtractedFact, fact_id)
        assert kept.status == "confirmed"
        ref = session.get(MessageRef, kept.message_ref_id)
        assert ref.snippet == "" and ref.sender  # the words go, who it is from stays, as the row it became names it


def test_a_person_moved_to_shared_leaves_nothing_waiting_from_a_server_they_chose(owner: TestClient):
    """Mail through a server of theirs is theirs to write. After their access ended, what it said still waited to be
    answered, with the mailbox gone so that the message could not even be opened."""
    _, person_id = _join(owner)
    with Session(engine) as session:
        _their_mail(session, person_id)
    assert owner.put(f"/api/v1/people/{person_id}", json={"role": "shared"}).status_code == 200
    assert owner.get("/api/v1/facts").json()["facts"] == []


def test_a_restore_does_not_bring_a_removed_persons_mail_back(owner: TestClient):
    _, person_id = _join(owner)
    with Session(engine) as session:
        _their_mail(session, person_id)
    point = restore.make_point("manual", "before they went")
    assert owner.delete(f"/api/v1/people/{person_id}").status_code == 200
    restore.restore(point["name"])
    assert owner.get("/api/v1/mail/messages", params={"q": "private loan"}).json()["returned"] == 0
    assert owner.get("/api/v1/facts").json()["facts"] == []


def test_a_restore_does_not_set_waiting_again_what_an_ended_mailbox_said(owner: TestClient):
    _, person_id = _join(owner)
    with Session(engine) as session:
        _their_mail(session, person_id)
    point = restore.make_point("manual", "before they moved")
    assert owner.put(f"/api/v1/people/{person_id}", json={"role": "shared"}).status_code == 200
    restore.restore(point["name"])
    assert owner.get("/api/v1/facts").json()["facts"] == []


def test_a_new_person_given_a_removed_persons_number_does_not_inherit_their_mailbox(owner: TestClient):
    """SQLite gives a deleted row's number to the next row. Approvals and accounts already let go of it; a mailbox did
    not, so the next person could change it, password typed, as "a mailbox they added"."""
    _, first = _join(owner)
    with Session(engine) as session:
        box_id = _box(session, first).id
    assert owner.delete(f"/api/v1/people/{first}").status_code == 200
    other, second = _join(owner, username="robin")
    assert second == first, "the case this test is about: the number was reused"
    answer = other.put(f"/api/v1/mail/accounts/{box_id}", json={"label": "Mine now"})
    assert answer.status_code == 403, answer.text


def test_a_standing_allowance_does_not_outlast_a_role_change(owner: TestClient, monkeypatch):
    """Moved to "shared" and back, the person's old "always send to this address" still sent with no tap, though a new
    role is meant to carry nothing they set up."""
    monkeypatch.setattr(approvals, "SENDER", lambda *a, **k: "<sent@example.test>")
    sam, person_id = _join(owner)
    with Session(engine) as session:
        approvals.allow(session, "friend@example.test", person_id)
        _box(session, person_id, username="sam@example.test")
    assert owner.put(f"/api/v1/people/{person_id}", json={"role": "shared"}).status_code == 200
    assert owner.put(f"/api/v1/people/{person_id}", json={"role": "everything"}).status_code == 200
    with Session(engine) as session:
        assert approvals.allowances(session, person_id) == []


def test_the_erase_takes_a_second_persons_allowed_addresses(owner: TestClient):
    """The owner's own list goes with the settings. A second person's stayed on their row: addresses of the people the
    household writes to, kept after "delete everything"."""
    _, person_id = _join(owner)
    with Session(engine) as session:
        approvals.allow(session, "friend@example.test", person_id)
    assert owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": PW}).status_code == 200
    with Session(engine) as session:
        assert session.get(Person, person_id).allowances == "[]"


# ---- signing out, revoking ---------------------------------------------------------------------------------------


def test_signing_out_ends_that_browsers_notifications(owner: TestClient, monkeypatch):
    """Ending a session on Settings ended its notifications. Signing out of it did not: a borrowed computer signed out
    of went on showing the brief and the sends waiting."""
    sent: list[dict] = []
    monkeypatch.setattr(webpush, "webpush", lambda **kw: sent.append(kw))
    assert owner.post("/api/v1/push/devices", json={"endpoint": "https://push.example.test/abc", "keys": {"p256dh": "k", "auth": "a"}, "label": "Borrowed"}).status_code == 200
    with Session(engine) as session:
        assert len(webpush.subscriptions(session)) == 1
    assert owner.post("/api/auth/logout").status_code == 200
    with Session(engine) as session:
        assert webpush.subscriptions(session) == []


def _queued(session: Session, created_by: str, kind: str = "key") -> int:
    row = approvals.create(session, to="x@attacker.test", subject="Please pay", body="Pay this.", created_by=created_by, created_kind=kind)
    assert row.status == "pending"
    return row.id


def test_a_revoked_keys_draft_does_not_wait_for_a_tap(owner: TestClient):
    """A key revoked because the model behind it was reading hostile mail still had its draft on the approvals page, one
    tap from going out."""
    made = owner.post("/api/v1/keys", json={"name": "A script", "scopes": ["read"]}).json()
    key_id = made.get("id") or made["key"]["id"]
    with Session(engine) as session:
        draft = _queued(session, "A script")
        other = _queued(session, "owner", kind="owner")
    assert owner.delete(f"/api/v1/keys/{key_id}").status_code == 200
    with Session(engine) as session:
        assert session.get(Approval, draft).status == "denied"
        assert session.get(Approval, other).status == "pending"


def test_a_revoked_apps_draft_does_not_wait_for_a_tap(owner: TestClient):
    with Session(engine) as session:
        session.add(OAuthClient(client_id="app-1", name="An app"))
        session.commit()
        draft = _queued(session, "An app")
        assert revoke_client(session, "app-1") == 0
        assert session.get(Approval, draft).status == "denied"


# ---- caches ------------------------------------------------------------------------------------------------------


def test_a_restore_forgets_what_the_trust_check_had_learned(owner: TestClient):
    """A sender marked genuine by mistake, then a restore to undo it: for two more minutes the sender was still the brand's."""
    point = restore.make_point("manual", "before the mistake")
    with Session(engine) as session:
        set_setting(session, TRUSTED, {"capital one": ["capital-1.test"]})
        _forget_learned()
        assert "capital-1.test" in learned_senders(session)["capital one"]
    restore.restore(point["name"])
    with Session(engine) as session:
        assert "capital one" not in learned_senders(session)


def test_an_erase_forgets_what_the_trust_check_had_learned(owner: TestClient):
    with Session(engine) as session:
        set_setting(session, TRUSTED, {"capital one": ["capital-1.test"]})
        _forget_learned()
        assert learned_senders(session)
    assert owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": PW}).status_code == 200
    with Session(engine) as session:
        assert learned_senders(session) == {}


# ---- a verdict does not come back clean ----------------------------------------------------------------------------


def test_a_forgery_under_a_marked_messages_id_is_its_own_message(owner: TestClient):
    """The owner's word holds for a message's copies, and a copy was anything with its sender and Message-ID. A forged From
    under that id, failing the server's check, was dropped as seen and took the reference's number: opening the message
    the owner marked genuine then showed the forger's words, attachments and all, under "You marked this"."""
    with Session(engine) as session:
        box = _box(session)
        real = _raw("billing@lender.example.test", "Lender", failed("lender.example.test"), message_id="<m1@lender.example.test>")
        ref = _land(session, box, 1, real)
        assert ref.trust == "crit"
        ref_id = ref.id
        assert datacheck.answer(session, f"sender:{ref_id}", "trust", TODAY)
        forged = _raw("billing@lender.example.test", "Lender", failed("lender.example.test"), subject="Your bill is due: pay here", body="Amount due $9,120.00 on 10/20/2026. Pay at attacker.test.", message_id="<m1@lender.example.test>")
        handle_message(session, box, "INBOX", 2, parse_message(forged), TODAY)
        session.commit()
        marked = session.get(MessageRef, ref_id)
        assert marked.uid == 1, "the owner's message now opens as the forger's"
        new = session.exec(select(MessageRef).where(MessageRef.uid == 2)).one()
        assert new.trust == "crit" and new.trust_why != marked.trust_why


def test_a_worse_message_under_a_warned_messages_id_is_its_own_message(owner: TestClient):
    """The same under a "warn": what the card said was checked was no longer what would be opened."""
    with Session(engine) as session:
        box = _box(session, 1)
        ref = _land(session, box, 1, _raw("a@sender.example.test", "Sender", passed("sender.example.test"), message_id="<m2@example.test>"))
        assert ref.trust == "warn"
        ref_id = ref.id
        handle_message(session, box, "INBOX", 2, parse_message(_raw("a@sender.example.test", "Sender", failed("sender.example.test"), subject="Another message", body="Amount due $9.00 on 10/20/2026.", message_id="<m2@example.test>")), TODAY)
        session.commit()
        assert session.get(MessageRef, ref_id).uid == 1
        assert session.exec(select(MessageRef).where(MessageRef.uid == 2)).one().trust == "crit"


def test_the_same_message_renumbered_is_still_one_message(owner: TestClient):
    with Session(engine) as session:
        box = _box(session)
        raw = _raw("alerts@capitalone.com", "Capital One", failed("capitalone.com"), message_id="<m3@capitalone.com>")
        ref = _land(session, box, 1, raw)
        ref_id = ref.id
        assert handle_message(session, box, "INBOX", 7, parse_message(raw), TODAY) == {"seen": True}
        session.commit()
        assert session.get(MessageRef, ref_id).uid == 7
        assert session.get(MessageRef, ref_id).trust == "crit"


@pytest.mark.parametrize("written", ["Acme  Lending", "  Acme\tLending ", "Acme Lending.", "ACME LENDING", "Acme-Lending"])
def test_a_lenders_name_written_a_little_differently_is_still_that_name(owner: TestClient, written: str):
    """A lender the table does not hold is judged by the domains its name used before. The name was compared as stored, so
    one more space, which every mail client draws as one, took the "warn" away: the attacker's own signed domain read
    "Confirmed by the mail server" under the lender's name."""
    with Session(engine) as session:
        box = _box(session)
        assert _land(session, box, 1, _raw("billing@acme-lending.test", "Acme Lending", passed("acme-lending.test"), message_id="<n1@example.test>")).trust == "ok"
        assert domains_under_name(session, written, "x@attacker.test") == {"acme-lending.test"}
        ref = _land(session, box, 2, _raw("x@attacker.test", written, passed("attacker.test"), message_id="<n2@example.test>"))
        assert ref.trust == "warn", ref.trust_why


def test_the_recheck_judges_as_a_first_reading_does(owner: TestClient):
    """The job that judges old waiting items believed a second person's own server, where a first reading does not, and
    looked at no earlier domains for the name: on a message with no verdict yet, "dmarc=pass" from their server was "ok",
    and "ok" is what teaches a brand's domain and lets a statement land with no tap."""
    raw = _raw("alerts@acme.example.test", "Example Sender", passed("acme.example.test"), message_id="<r1@example.test>")

    class Client:
        def connect(self) -> None: ...
        def select_readonly(self, folder: str) -> None: ...
        def fetch_raw(self, uid: int) -> bytes:
            return raw
        def close(self) -> None: ...

    with Session(engine) as session:
        box = _box(session, 1)
        ref = _land(session, box, 1, raw)
        ref.trust, ref.trust_why = "", ""
        session.add(ref)
        session.commit()
        ref_id = ref.id
        assert session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref_id, ExtractedFact.status == "pending")).first() is not None
        assert recheck_senders(session, client_factory=lambda *a: Client()) == 1
        session.commit()
        assert session.get(MessageRef, ref_id).trust == "warn"


def test_the_recheck_compares_a_names_earlier_domains(owner: TestClient):
    raw = _raw("x@attacker.test", "Acme Lending", passed("attacker.test"), message_id="<r2@example.test>")

    class Client:
        def connect(self) -> None: ...
        def select_readonly(self, folder: str) -> None: ...
        def fetch_raw(self, uid: int) -> bytes:
            return raw
        def close(self) -> None: ...

    with Session(engine) as session:
        box = _box(session)
        _land(session, box, 1, _raw("billing@acme-lending.test", "Acme Lending", passed("acme-lending.test"), message_id="<r0@example.test>"))
        ref = _land(session, box, 2, raw)
        ref.trust, ref.trust_why = "", ""
        session.add(ref)
        session.commit()
        ref_id = ref.id
        assert recheck_senders(session, client_factory=lambda *a: Client()) == 1
        session.commit()
        assert session.get(MessageRef, ref_id).trust == "warn"


# ---- what held, kept held ------------------------------------------------------------------------------------------


def test_a_restore_brings_back_no_key_person_invite_or_feed(owner: TestClient):
    key = owner.post("/api/v1/keys", json={"name": "Old key", "scopes": ["read"]}).json()
    token = key["token"] if "token" in key else key["key"]["token"]
    key_id = key.get("id") or key["key"]["id"]
    _, gone = _join(owner, username="gone")
    invited = owner.post("/api/v1/people", json={"username": "invited", "role": "everything"}).json()["invite"]
    link_token = invited["link"].split("#", 1)[1]
    feed = owner.post("/api/v1/calendar/feed", json={"amounts": False}).json()["url"].rsplit("/", 1)[1]
    point = restore.make_point("manual", "all of it open")

    assert owner.delete(f"/api/v1/keys/{key_id}").status_code == 200
    assert owner.delete(f"/api/v1/people/{gone}").status_code == 200
    joiner = TestClient(app)
    joiner.headers.update(H)
    assert joiner.post("/api/auth/join", json={"token": link_token, "password": "maple harbour winter nine"}).status_code == 200
    assert owner.delete("/api/v1/calendar/feed").status_code == 200

    restore.restore(point["name"])
    fresh = TestClient(app)
    assert fresh.get("/api/v1/facts", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"username": "gone", "password": "maple harbour winter nine"}).status_code == 401
    again = TestClient(app)
    again.headers.update(H)
    assert again.post("/api/auth/join", json={"token": link_token, "password": "another long pass phrase"}).status_code != 200
    assert fresh.get(f"/api/calendar/{feed}").status_code == 404
    with Session(engine) as session:
        assert session.get(ApiKey, key_id).revoked_at is not None


def test_a_verdict_is_written_once_and_no_later_sync_rewrites_it(owner: TestClient):
    """The same id from the same sender, now passing, is not a way to make the first one "ok"."""
    with Session(engine) as session:
        box = _box(session)
        ref = _land(session, box, 1, _raw("alerts@capitalone.com", "Capital One", failed("capitalone.com"), message_id="<v1@capitalone.com>"))
        ref_id = ref.id
        handle_message(session, box, "INBOX", 2, parse_message(_raw("alerts@capitalone.com", "Capital One", passed("capitalone.com"), message_id="<v1@capitalone.com>")), TODAY)
        session.commit()
        assert session.get(MessageRef, ref_id).trust == "crit"
        assert "capitalone.com" not in vouched_domains(session)


def test_a_confirmation_taken_back_stops_vouching_for_the_sender(owner: TestClient):
    """An app may go on confirming a sender the owner has answered for; an answer taken back is no answer."""
    with Session(engine) as session:
        box = _box(session)
        ref = _land(session, box, 1, _raw("billing@new-lender.test", "New Lender", passed("new-lender.test"), message_id="<u1@new-lender.test>"))
        fact_id = session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref.id)).one().id
    assert owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": False}).status_code == 200
    with Session(engine) as session:
        assert "new-lender.test" in vouched_domains(session)
    assert owner.post(f"/api/v1/facts/{fact_id}/unconfirm").status_code == 200
    with Session(engine) as session:
        assert "new-lender.test" not in vouched_domains(session)


def test_a_revoked_app_gets_nothing_from_its_old_code_or_refresh_token(owner: TestClient):
    from tests.test_oauth import REDIRECT, pkce

    verifier, challenge = pkce()
    client = owner.post("/register", json={"redirect_uris": [REDIRECT], "client_name": "An app", "token_endpoint_auth_method": "none", "scope": "read"}).json()
    start = owner.get("/authorize", params={"client_id": client["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read", "state": "s"}, follow_redirects=False)
    req = parse_qs(urlparse(start.headers["location"]).query)["req"][0]
    allowed = owner.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": "read"}, follow_redirects=False)
    code = parse_qs(urlparse(allowed.headers["location"]).query)["code"][0]
    tokens = owner.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": client["client_id"], "redirect_uri": REDIRECT, "code_verifier": verifier}).json()

    # A second code, allowed and not yet spent when the app is revoked.
    allowed = owner.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": "read"}, follow_redirects=False)
    unspent = parse_qs(urlparse(allowed.headers["location"]).query)["code"][0]
    with Session(engine) as session:
        assert revoke_client(session, client["client_id"]) == 2
    assert owner.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client["client_id"]}).status_code != 200
    assert owner.post("/token", data={"grant_type": "authorization_code", "code": unspent, "client_id": client["client_id"], "redirect_uri": REDIRECT, "code_verifier": verifier}).status_code != 200
    fresh = TestClient(app)
    assert fresh.get("/api/v1/facts", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401


def test_twenty_forged_alerts_do_not_push_a_real_one_out_of_the_brief(owner: TestClient):
    """Unvouched alerts were left out after the newest twenty were taken, so a flood of them left the brief with none."""
    from datetime import timedelta

    from app.engine.snapshot import _mail_facts
    from app.models import utcnow

    with Session(engine) as session:
        box = _box(session)
        now = utcnow()
        real = MessageRef(account_id=box.id, folder="INBOX", uid=1, sender="alerts@chase.com", sender_name="Chase", trust="ok")
        session.add(real)
        session.commit()
        session.add(ExtractedFact(kind="bank_alert", message_ref_id=real.id, fields="{}", status="auto", created_at=now - timedelta(hours=1)))
        for i in range(20):
            forged = MessageRef(account_id=box.id, folder="INBOX", uid=i + 2, sender="alerts@chase.com", sender_name="Chase", trust="crit")
            session.add(forged)
            session.commit()
            session.add(ExtractedFact(kind="bank_alert", message_ref_id=forged.id, fields="{}", status="auto", created_at=now))
        session.commit()
        alerts = _mail_facts(session, now.date())["alerts"]
        assert [a["sender_name"] for a in alerts] == ["Chase"] and alerts[0]["forged"] is False
