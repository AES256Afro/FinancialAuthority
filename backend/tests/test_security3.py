"""Security round three: a second person cannot redirect stored secrets, what they set up ends with them, one
person's sign-in never resets the wait on guesses at another's password, edits keep a send to one recipient, the
erase route is under the lockout, JSON routes keep their small ceiling, and a restore never re-shares an account."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, auth, crypto, restore
from app.db import engine
from app.llm import roles
from app.main import app
from app.models import Account, ApiKey, Approval, MailAccount
from tests.test_people import GOOD, _invite, _join

H = {"X-Requested-With": "FinancialAuthority"}


def test_moving_a_mailbox_to_another_server_needs_its_password_again(owner: TestClient):
    with Session(engine) as session:
        box = MailAccount(label="Example", kind="imap", host="imap.example.test", port=993, username="owner@example.test", password_enc=crypto.encrypt("stored-pass"))
        session.add(box)
        session.commit()
        box_id = box.id
    person = _join(_invite(owner, "alex", "everything"))
    r = person.put(f"/api/v1/mail/accounts/{box_id}", json={"host": "imap.elsewhere.test"})
    assert r.status_code == 400 and "password again" in r.json()["detail"]
    r = person.put(f"/api/v1/mail/accounts/{box_id}", json={"smtp_host": "smtp.elsewhere.test"})
    assert r.status_code == 400
    assert person.put(f"/api/v1/mail/accounts/{box_id}", json={"label": "Renamed"}).status_code == 200  # not where it connects
    assert person.put(f"/api/v1/mail/accounts/{box_id}", json={"host": "imap.elsewhere.test", "password": "a-new-pass"}).status_code == 200


def test_a_model_key_goes_only_to_the_address_it_was_saved_for(owner: TestClient):
    owner.put("/api/v1/models/advisor", json={"provider": "openai", "base_url": "https://models.example.test/v1", "model": "example-model"})
    with Session(engine) as session:
        roles.save_secret(session, "openai", "sk-example-key")
        assert roles.key_for_url(session, "https://models.example.test/v1") == "sk-example-key"
        assert roles.key_for_url(session, "https://attacker.example.test/v1") == ""
    owner.put("/api/v1/models/advisor", json={"base_url": "https://attacker.example.test/v1"})
    with Session(engine) as session:
        assert roles.provider_for(session, "advisor").api_key == ""  # the key stays home


def test_what_a_person_set_up_ends_with_them_and_their_pending_sends_are_cancelled(owner: TestClient, monkeypatch):
    monkeypatch.setattr(approvals.notify, "send", lambda *a, **k: {"ok": True})
    person = _join(_invite(owner, "alex", "everything"))
    token = person.post("/api/v1/keys", json={"name": "theirs", "scopes": ["read"]}).json()["token"]
    feed = person.post("/api/v1/calendar/feed", json={"amounts": True}).json()["url"]
    draft = person.post("/api/v1/approvals", json={"to": "help@example-gym.test", "subject": "Cancel", "body": "Please cancel."}).json()
    reader = TestClient(app)
    assert reader.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    pid = person.get("/api/auth/me").json()["person_id"]
    assert owner.delete(f"/api/v1/people/{pid}").status_code == 200
    assert reader.get("/api/v1/snapshot", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert reader.get(feed.split("://", 1)[1].split("/", 1)[1].join(["/", ""])).status_code == 404
    with Session(engine) as session:
        row = session.get(Approval, draft["id"])
        assert row.status == "denied" and row.for_person_id == -pid
        assert session.exec(select(ApiKey).where(ApiKey.person_id == pid, ApiKey.revoked_at == None)).first() is None  # noqa: E711
    # A new person given the same number never inherits the old draft
    newcomer = _join(_invite(owner, "blair", "everything"))
    assert all(not a["mine"] for a in newcomer.get("/api/v1/approvals", params={"status": "all"}).json()["approvals"])


def test_one_persons_sign_in_does_not_reset_the_wait_on_guesses_at_the_owners_password(owner: TestClient):
    auth.reset_lockouts()
    _join(_invite(owner))
    stranger, sam = TestClient(app), TestClient(app)
    codes = []
    for i in range(12):
        codes.append(stranger.post("/api/auth/login", json={"username": "owner", "password": f"guess {i}"}).status_code)
        if i % 3 == 2:
            sam.post("/api/auth/login", json={"username": "sam", "password": GOOD})
    assert 429 in codes  # the doubling wait on the owner's name holds
    auth.reset_lockouts()


def test_an_edited_send_still_goes_to_one_recipient(owner: TestClient, monkeypatch):
    monkeypatch.setattr(approvals.notify, "send", lambda *a, **k: {"ok": True})
    row = owner.post("/api/v1/approvals", json={"to": "a@example.test", "subject": "Hello", "body": "Hi."}).json()
    assert owner.put(f"/api/v1/approvals/{row['id']}", json={"to": "a@example.test, b@example.test"}).status_code == 400
    assert owner.put(f"/api/v1/approvals/{row['id']}", json={"subject": "   "}).status_code == 400
    assert owner.put(f"/api/v1/approvals/{row['id']}", json={"subject": "Hello again"}).status_code == 200
    with Session(engine) as session:
        row = session.get(Approval, row["id"])
        row.to = "a@example.test, b@example.test"  # even a row changed behind the app's back is not sent
        session.add(row)
        session.commit()
        sent = approvals.send(session, row)
        assert sent.status == "failed" and "one email address" in sent.error


def test_erasing_checks_the_password_under_the_lockout(owner: TestClient):
    auth.reset_lockouts()
    codes = [owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": f"guess {i}"}).status_code for i in range(8)]
    assert codes[0] == 401 and 429 in codes
    with Session(engine) as session:
        assert session.exec(select(Account)).all() is not None
    auth.reset_lockouts()


def test_json_routes_keep_the_small_ceiling_before_sign_in():
    stranger = TestClient(app)
    big = b"{" + b'"x":"' + b"a" * (2 * 1024 * 1024) + b'"}'
    r = stranger.put("/api/v1/appearance", content=big, headers={"Content-Type": "application/json", "Content-Length": str(len(big))})
    assert r.status_code == 413


def test_a_restore_keeps_todays_sharing_and_mailboxes(owner: TestClient):
    with Session(engine) as session:
        a = Account(name="Example Joint", kind="checking", shared=True)
        box = MailAccount(label="Example", kind="imap", host="imap.example.test", username="owner@example.test", password_enc=crypto.encrypt("p"))
        session.add_all([a, box])
        session.commit()
        aid, bid = a.id, box.id
    point = restore.make_point("manual", "before the change")
    with Session(engine) as session:
        a, box = session.get(Account, aid), session.get(MailAccount, bid)
        a.shared, box.active, box.password_enc = False, False, ""
        session.add_all([a, box])
        session.commit()
    assert owner.post(f"/api/v1/restore-points/{point['name']}/restore").status_code == 200
    with Session(engine) as session:
        assert session.get(Account, aid).shared is False
        box = session.get(MailAccount, bid)
        assert box.active is False and box.password_enc == ""
