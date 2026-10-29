"""M33: a second person. The owner invites; the person sets their own password; a "shared" sign-in sees the
shared accounts only; "everything" sees what the owner sees but not what is the owner's alone; each approves
only their own sends."""

import time
from datetime import date

import pyotp
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals
from app.db import engine
from app.main import app
from app.models import Account, Bill, Person, Transaction

H = {"X-Requested-With": "FinancialAuthority"}
GOOD = "plum river lantern seven"


def _invite(owner: TestClient, username: str = "sam", role: str = "shared") -> str:
    r = owner.post("/api/v1/people", json={"username": username, "display_name": "Sam Example", "role": role})
    assert r.status_code == 200, r.text
    link = r.json()["invite"]["link"]
    assert "/join#" in link
    return link.split("#", 1)[1]


def _join(token: str, password: str = GOOD) -> TestClient:
    person = TestClient(app)
    person.headers.update(H)
    r = person.post("/api/auth/join", json={"token": token, "password": password})
    assert r.status_code == 200, r.text
    return person


def test_the_person_sets_their_own_password_from_a_one_time_link(owner: TestClient):
    token = _invite(owner)
    stranger = TestClient(app)
    check = stranger.post("/api/auth/join/check", json={"token": token}).json()
    assert check["username"] == "sam" and check["role_words"].startswith("You see the accounts the owner marks shared")
    assert stranger.post("/api/auth/join", json={"token": token, "password": "short"}).status_code == 400
    assert stranger.post("/api/auth/join", json={"token": token, "password": "sam is my password ok"}).status_code == 400  # the username in it
    person = _join(token)
    me = person.get("/api/auth/me").json()
    assert me["kind"] == "person" and me["role"] == "shared" and me["name"] == "Sam Example"
    assert TestClient(app).post("/api/auth/join", json={"token": token, "password": GOOD}).status_code == 400  # used once
    with Session(engine) as session:
        row = session.exec(select(Person).where(Person.username == "sam")).one()
        assert row.password_hash.startswith("scrypt$") and GOOD not in row.password_hash and row.invite_hash == ""
    # Signing in again, with the username and the password they chose
    again = TestClient(app)
    assert again.post("/api/auth/login", json={"username": "sam", "password": "wrong password here"}).status_code == 401
    r = again.post("/api/auth/login", json={"username": "Sam", "password": GOOD})
    assert r.status_code == 200 and r.json()["kind"] == "person"
    assert owner.post("/api/v1/people", json={"username": "owner", "role": "shared"}).status_code == 400  # the owner's own name


def test_a_shared_sign_in_sees_the_shared_accounts_and_nothing_else(owner: TestClient):
    with Session(engine) as session:
        joint = Account(name="Example Joint Checking", kind="checking", balance=900.0, shared=True)
        mine = Account(name="Example Owner Card", kind="card", balance=-300.0)
        session.add_all([joint, mine])
        session.flush()
        session.add(Transaction(account_id=joint.id, date=date(2026, 10, 10), merchant="Example Grocer", amount=42.5, category="Groceries", row_hash="sh-1"))
        session.add(Transaction(account_id=mine.id, date=date(2026, 10, 11), merchant="Example Private Shop", amount=99.0, category="Shopping", row_hash="sh-2"))
        session.add(Bill(name="Example Power", amount=80.0, due_day=20, pay_from_account_id=joint.id))
        session.add(Bill(name="Example Private Gym", amount=30.0, due_day=21, pay_from_account_id=mine.id))
        session.commit()
        joint_id, mine_id = joint.id, mine.id
    person = _join(_invite(owner))
    view = person.get("/api/v1/shared").json()
    assert [a["name"] for a in view["accounts"]] == ["Example Joint Checking"] and view["cash"] == 900.0
    assert [b["name"] for b in view["bills"]] == ["Example Power"] and view["bills"][0]["date"] == "2026-10-20"
    assert [t["merchant"] for t in view["recent"]] == ["Example Grocer"] and view["spending"] == [{"category": "Groceries", "spent": 42.5}]
    text = str(view)
    assert "Private" not in text
    for path in ("/api/v1/snapshot", "/api/v1/accounts", "/api/v1/transactions/search", "/api/v1/bills", "/api/v1/documents", "/api/v1/people", "/api/v1/next_steps"):
        assert person.get(path).status_code == 403, path
    assert person.post("/api/v1/approvals", json={"to": "a@example.test", "subject": "s", "body": "b"}).status_code == 403
    # Their own account shows too, marked theirs; the owner marks it
    pid = person.get("/api/auth/me").json()["person_id"]
    assert owner.put(f"/api/v1/accounts/{mine_id}/sharing", json={"shared": False, "person_id": pid}).status_code == 200
    names = {a["name"]: a["whose"] for a in person.get("/api/v1/shared").json()["accounts"]}
    assert names == {"Example Joint Checking": "shared", "Example Owner Card": "theirs"}
    assert person.put(f"/api/v1/accounts/{joint_id}/sharing", json={"shared": False}).status_code == 403
    # The owner can see exactly what they see
    assert {a["name"] for a in owner.get("/api/v1/shared", params={"person_id": pid}).json()["accounts"]} == set(names)


def test_everything_sees_all_but_not_what_is_the_owners_alone(owner: TestClient):
    person = _join(_invite(owner, "alex", "everything"))
    assert person.get("/api/v1/snapshot").status_code == 200
    assert person.get("/api/v1/accounts").status_code == 200
    assert person.get("/api/v1/people").status_code == 403
    assert person.post("/api/v1/privacy/erase", json={"phrase": "erase everything", "password": "x", "code": ""}).status_code == 403
    assert person.post("/api/v1/restore-points/anything/restore").status_code == 403


def test_each_person_approves_only_their_own_sends(owner: TestClient, monkeypatch):
    monkeypatch.setattr(approvals.notify, "send", lambda *a, **k: {"ok": True})
    sent = []
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: sent.append(kw["to"]) or "<id@example.test>")
    from app.models import MailAccount

    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test"))
        session.commit()
    person = _join(_invite(owner, "alex", "everything"))
    owners = owner.post("/api/v1/approvals", json={"to": "billing@example-power.test", "subject": "Move my date", "body": "Please move it."}).json()
    theirs = person.post("/api/v1/approvals", json={"to": "help@example-gym.test", "subject": "Cancel", "body": "Please cancel."}).json()
    assert theirs["for_person_id"] is not None and owners["for_person_id"] is None
    assert person.post(f"/api/v1/approvals/{owners['id']}/approve", json={}).status_code == 403
    assert owner.post(f"/api/v1/approvals/{theirs['id']}/approve", json={}).status_code == 403
    assert owner.post(f"/api/v1/approvals/{theirs['id']}/deny").status_code == 403
    listed = {a["id"]: a for a in person.get("/api/v1/approvals").json()["approvals"]}
    assert listed[theirs["id"]]["mine"] and listed[theirs["id"]]["whose"] == "yours" and listed[owners["id"]]["whose"] == "owner"
    assert person.post(f"/api/v1/approvals/{theirs['id']}/approve", json={"allow_always": True}).json()["status"] == "sent"
    # Their standing allowance is theirs: the owner's send to the same address still waits for the owner
    again = owner.post("/api/v1/approvals", json={"to": "help@example-gym.test", "subject": "Also", "body": "Hello."}).json()
    assert again["status"] == "pending" and sent == ["help@example-gym.test"]
    assert owner.get("/api/v1/approvals").json()["allowances"] == [] and person.get("/api/v1/approvals").json()["allowances"] == ["help@example-gym.test"]


def test_the_owner_ends_a_persons_access_and_a_new_link_resets_their_sign_in(owner: TestClient):
    token = _invite(owner)
    person = _join(token)
    pid = person.get("/api/auth/me").json()["person_id"]
    sessions = owner.get("/api/auth/sessions").json()["sessions"]
    assert {s["who"] for s in sessions} >= {"you", "Sam Example"}
    assert [s["who"] for s in person.get("/api/auth/sessions").json()["sessions"]] == ["you"]
    out = owner.post(f"/api/v1/people/{pid}/invite").json()
    assert person.get("/api/auth/me").status_code == 401  # every session ended
    assert TestClient(app).post("/api/auth/login", json={"username": "sam", "password": GOOD}).status_code == 401  # the old password is gone
    person = _join(out["invite"]["link"].split("#", 1)[1], "a brand new long passphrase")
    assert owner.put(f"/api/v1/people/{pid}", json={"active": False}).status_code == 200
    assert person.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"username": "sam", "password": "a brand new long passphrase"}).status_code == 401
    assert owner.delete(f"/api/v1/people/{pid}").status_code == 200
    with Session(engine) as session:
        assert session.exec(select(Person)).first() is None


def test_a_persons_second_step_is_their_own(owner: TestClient):
    person = _join(_invite(owner))
    start = person.post("/api/auth/totp/start").json()
    codes = person.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(start["secret"]).now()}).json()["recovery_codes"]
    assert len(codes) == 10 and person.get("/api/auth/factors").json()["totp"]["enabled"]
    assert owner.get("/api/auth/factors").json()["totp"]["enabled"] is False  # the owner's is untouched
    fresh = TestClient(app)
    first = fresh.post("/api/auth/login", json={"username": "sam", "password": GOOD}).json()
    assert first["second_step"] == "code"
    assert fresh.post("/api/auth/login/second", json={"ticket": first["ticket"], "code": "000000"}).status_code == 401
    r = fresh.post("/api/auth/login/second", json={"ticket": first["ticket"], "code": codes[0]})
    assert r.status_code == 200 and fresh.get("/api/auth/me").json()["kind"] == "person"
    # The owner signs in with no code still
    plain = TestClient(app)
    assert plain.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).json()["ok"] is True
    time.sleep(0)
