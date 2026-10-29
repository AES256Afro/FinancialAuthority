"""Sweep 15 wiring: the routes record who answered a fact and can take a confirmation back; a paycheck's end check
can be cleared; a saved scenario keeps a recurring addition's start; a bank row withdrawn lets go of what it settled."""

from __future__ import annotations

import datetime as dt
import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth
from app.db import engine
from app.engine.household import seed
from app.main import app
from app.models import Account, AuditEntry, Bill, ExtractedFact, Invoice, MailAccount, MessageRef, OAuthClient, OAuthToken, PlannedPayment, Transaction, utcnow

APP_TOKEN = "fa_oa_example_access"


def _fact(session: Session, kind: str, fields: dict, uid: int = 1) -> int:
    box = session.exec(select(MailAccount)).first()
    if box is None:
        box = MailAccount(label="Example", kind="imap", host="mail.example.test", port=993, username="owner@example.test", password_enc="")
        session.add(box)
        session.flush()
    ref = MessageRef(account_id=box.id, folder="INBOX", uid=uid, sender="notices@example.test", sender_name="Example Power", subject="A notice", trust="ok")
    session.add(ref)
    session.flush()
    fact = ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps(fields), confidence=0.9)
    session.add(fact)
    session.commit()
    return fact.id  # type: ignore[return-value]


def _connected_app(session: Session) -> None:
    session.add(OAuthClient(client_id="client-1", name="Example Client", redirect_uris='["https://example.test/cb"]', scopes="read confirm", registration="{}"))
    session.add(OAuthToken(token_hash=auth.hash_key(APP_TOKEN), kind="access", client_id="client-1", scopes="read confirm", expires_at=utcnow() + dt.timedelta(hours=1)))
    session.commit()


def test_a_fact_an_app_confirmed_says_so_and_the_owner_takes_it_back(owner: TestClient):
    with Session(engine) as s:
        seed(s)
        _connected_app(s)
        bills_before = sorted(b.id for b in s.exec(select(Bill)).all())
        fid = _fact(s, "bill_due", {"name": "Example New Service", "amount": 25.0, "due_date": "2026-10-28"})
    connected = TestClient(app)
    connected.headers["Authorization"] = f"Bearer {APP_TOKEN}"
    done = connected.post(f"/api/v1/facts/{fid}/confirm", json={})
    assert done.status_code == 200, done.text
    with Session(engine) as s:
        fact = s.get(ExtractedFact, fid)
        assert fact.status == "applied" and fact.resolved_by == "app:Example Client" and fact.made == fact.applied_to
    listed = next(f for f in owner.get("/api/v1/facts?status=applied").json()["facts"] if f["id"] == fid)
    assert listed["resolved_by"] == "Example Client" and listed["undoable"] is True
    assert connected.post(f"/api/v1/facts/{fid}/unconfirm").status_code == 403  # an app never takes one back
    undone = owner.post(f"/api/v1/facts/{fid}/unconfirm")
    assert undone.status_code == 200, undone.text
    assert "Example New Service" in undone.json()["said"]
    with Session(engine) as s:
        fact = s.get(ExtractedFact, fid)
        assert fact.status == "pending" and fact.resolved_by == "" and fact.applied_to == ""
        assert sorted(b.id for b in s.exec(select(Bill)).all()) == bills_before
        assert s.exec(select(AuditEntry).where(AuditEntry.tool == "facts_unconfirm")).first() is not None
    again = owner.post(f"/api/v1/facts/{fid}/unconfirm")
    assert again.status_code == 409 and "pending" in again.json()["detail"]
    assert owner.post("/api/v1/facts/999999/unconfirm").status_code == 404


def test_unconfirm_is_a_person_signed_in_only(owner: TestClient, client: TestClient):
    with Session(engine) as s:
        seed(s)
        fid = _fact(s, "bill_due", {"name": "Example Other Service", "amount": 12.0, "due_date": "2026-10-28"})
    reader = owner.post("/api/v1/keys", json={"name": "Example Reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    helper = owner.post("/api/v1/keys", json={"name": "Example Helper", "scopes": ["read", "confirm"], "redaction": "full"}).json()["token"]
    assert owner.post(f"/api/v1/facts/{fid}/confirm", json={}).status_code == 200
    for token in (reader, helper):
        assert TestClient(app).post(f"/api/v1/facts/{fid}/unconfirm", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    del owner.headers["X-Requested-With"]
    assert owner.post(f"/api/v1/facts/{fid}/unconfirm").status_code == 403  # a cookie write without the app's header
    with Session(engine) as s:
        fact = s.get(ExtractedFact, fid)
        assert fact.status == "applied" and fact.resolved_by == "owner"


def test_a_key_answer_and_a_bulk_answer_say_who(owner: TestClient):
    with Session(engine) as s:
        seed(s)
        one = _fact(s, "bill_due", {"name": "Example Key Service", "amount": 9.0, "due_date": "2026-10-28"}, uid=1)
        two = _fact(s, "past_due", {"amount": 40.0}, uid=2)
    helper = owner.post("/api/v1/keys", json={"name": "Example Helper", "scopes": ["read", "confirm"], "redaction": "full"}).json()["token"]
    assert TestClient(app).post(f"/api/v1/facts/{one}/reject", headers={"Authorization": f"Bearer {helper}"}).status_code == 200
    answered = owner.post("/api/v1/needs-you/facts", json={"ids": [two], "action": "reject"})
    assert answered.status_code == 200, answered.text
    with Session(engine) as s:
        assert s.get(ExtractedFact, one).resolved_by == "key:Example Helper"
        assert s.get(ExtractedFact, two).resolved_by == "owner" and s.get(ExtractedFact, two).status == "rejected"


def test_an_oauth_principal_is_marked_as_a_connected_app():
    with Session(engine) as s:
        _connected_app(s)
        principal = auth.principal_from_oauth(s, APP_TOKEN)
    assert principal is not None and principal.via_oauth and principal.kind == "key"
    assert auth.Principal(kind="key", name="Example Key").via_oauth is False


def test_a_fact_the_mail_connector_files_itself_is_answered_by_a_rule():
    from app.connectors.mail import upcoming_bills_facts
    from app.mail.imap import Parsed

    with Session(engine) as s:
        box = MailAccount(label="Example", kind="imap", host="mail.example.test", port=993, username="owner@example.test", password_enc="")
        s.add(box)
        s.flush()
        ref = MessageRef(account_id=box.id, folder="INBOX", uid=1, sender="hello@example.test", sender_name="Example Money", subject="Your week")
        s.add(ref)
        s.flush()
        parsed = Parsed(message_id="<1@example.test>", sender="hello@example.test", sender_name="Example Money", subject="Your week", date=None, text="Nothing due this week.", html="")
        assert upcoming_bills_facts(s, ref, parsed, date(2026, 10, 14), "test")["fact"] == "auto"
        s.commit()
        fact = s.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref.id)).one()
        assert fact.status == "auto" and fact.resolved_by == "rule"


def test_the_end_check_can_be_cleared_and_is_kept_when_not_sent(owner: TestClient):
    iid = owner.post("/api/v1/incomes", json={"name": "Example Job", "cadence": "semimonthly", "expected_net": 1850.25, "next_date": "2026-10-30"}).json()["id"]
    body = {"cadence": "semimonthly", "pay_days": "", "expected_net": 1850.25, "next_date": "2026-10-30"}

    def end() -> float | None:
        return next(i for i in owner.get("/api/v1/incomes").json()["incomes"] if i["id"] == iid)["expected_net_end"]

    assert owner.put(f"/api/v1/incomes/{iid}", json={**body, "expected_net_end": 1900.01}).status_code == 200
    assert owner.put(f"/api/v1/incomes/{iid}", json=body).status_code == 200 and end() == 1900.01  # not sent: kept
    assert owner.put(f"/api/v1/incomes/{iid}", json={**body, "expected_net_end": None}).status_code == 200 and end() is None


def test_a_scenario_start_date_is_taken_as_from_or_from_(owner: TestClient):
    for key in ("from", "from_"):
        spec = {"name": f"Example {key}", "move_date": "2027-01-15", "recurring": [{"name": "Example Parking", "amount": 25.0, "due_day": 3, key: "2027-02-01"}]}
        saved = owner.post("/api/v1/scenarios", json=spec)
        assert saved.status_code == 200, saved.text
        row = next(s for s in owner.get("/api/v1/scenarios").json()["items"] if s["id"] == saved.json()["id"])
        assert row["spec"]["recurring"][0]["from"] == "2027-02-01" and "from_" not in row["spec"]["recurring"][0]


def test_a_withdrawn_bank_row_lets_go_of_an_invoice_and_reopens_a_planned_payment():
    from app.connectors.banks import remove_transaction, tx_hash

    with Session(engine) as s:
        a = Account(name="Example Checking", kind="checking", connector="plaid")
        s.add(a)
        s.flush()
        gone = Transaction(account_id=a.id, date=date(2026, 10, 5), merchant="Example Client Co", amount=-300.0, row_hash=tx_hash("plaid", "t-1"), source="plaid")
        posted = Transaction(account_id=a.id, date=date(2026, 10, 6), merchant="Example Client Co", amount=-300.0, row_hash=tx_hash("plaid", "t-2"), source="plaid")
        moved = Transaction(account_id=a.id, date=date(2026, 10, 6), merchant="Example Card", amount=150.0, row_hash=tx_hash("plaid", "t-3"), source="plaid", pending=True)
        s.add_all([gone, posted, moved])
        s.flush()
        invoice = Invoice(business_id=1, issued_on=date(2026, 10, 1), amount=300.0, status="paid", paid_on=date(2026, 10, 5), transaction_id=gone.id)
        plan = PlannedPayment(name="Example Card", amount=150.0, date=date(2026, 10, 6), done=True, transaction_id=moved.id)
        s.add_all([invoice, plan])
        s.commit()
        assert remove_transaction(s, "plaid", "t-1")
        assert remove_transaction(s, "plaid", "t-3", replaced_by=posted)
        s.commit()
        s.refresh(invoice)
        s.refresh(plan)
        assert invoice.transaction_id is None and invoice.status == "paid"  # the owner's "paid" stays
        assert plan.transaction_id == posted.id and plan.done  # a pending row's posted row takes it over, still done
        third = Transaction(account_id=a.id, date=date(2026, 10, 7), merchant="Example Card", amount=150.0, row_hash=tx_hash("plaid", "t-4"), source="plaid")
        s.add(third)
        s.flush()
        plan.transaction_id, plan.done = third.id, True
        s.add(plan)
        s.commit()
        assert remove_transaction(s, "plaid", "t-4")
        s.commit()
        s.refresh(plan)
        assert plan.transaction_id is None and plan.done is False
