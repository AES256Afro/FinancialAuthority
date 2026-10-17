"""M52: the data check. Anything counted twice, bills that look alike, the same purchase from two sources, and mail
that may not be from who it says are raised with their records side by side; nothing changes until the owner
answers, except what is certain (the same email kept twice, a bill a lender's plan already pays), which is logged."""

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine import datacheck
from app.engine.borrowing import sync_borrowing
from app.mail.imap import parse_message
from app.mail.trust import judge, lookalike
from app.models import Account, Bill, ExtractedFact, Home, InstallmentPlan, MailAccount, MessageRef, Transaction

TODAY = date(2026, 10, 14)


def test_a_sender_is_judged_by_the_mail_servers_verdict_its_name_and_its_domain():
    passed = "mx.example.test; dkim=pass header.d=affirm.com; spf=pass; dmarc=pass header.from=affirm.com"
    assert judge("notices@affirm.com", "Affirm", passed)[0] == "ok"
    assert judge("alerts@capital0ne-alerts.test", "Capital One", passed) == ("crit", "It comes from capital0ne-alerts.test, a look-alike of capitalone.com.")
    assert judge("alerts@example-mailer.test", "Capital One", passed) == ("crit", "It calls itself Capital One, but it comes from example-mailer.test, not capitalone.com.")
    assert judge("pay@paypa1.com", "Billing", "")[0] == "crit" and lookalike("pay@paypa1.com") == "paypal.com"
    assert judge("bills@example-power.test", "Example Power", "mx.example.test; dmarc=fail")[0] == "crit"
    assert judge("bills@example-power.test", "Example Power", "") == ("", "")  # nothing to judge by
    # a company that shares a word, the brand's own longer domain, and the brand's own ending are not forgeries
    assert judge("noreply@xmail.square-enix.com", "Square Enix Store | North America", passed)[0] == "ok"
    assert judge("donotreply@vanguardretirement.com", "Vanguard", passed)[0] == "ok"
    assert judge("no-reply@notifications.health.amazon", "Amazon One Medical Notifications", passed)[0] == "ok"
    assert judge("x@capitalone-alerts.net", "Card team", passed)[0] == "crit"  # the brand with a bait word
    assert judge("x@statefarmservice.com", "State Farm", passed, reply_to="agent@statefarm.com")[0] == "ok"  # replies to its own other domain
    assert judge("x@statefarmservice.com", "State Farm", passed, reply_to="agent@example-other.test")[0] == "warn"
    assert judge("x@example-property.test", "Example Apartments", "mx.example.test; spf=softfail; dkim=none")[0] == "warn"  # weak, not forged
    # learned: earlier verified mail from this name used another domain of its own
    assert judge("x@notify.example-bank.test", "Capital One", passed, learned={"capital one": {"example-bank.test"}})[0] == "ok"


def test_the_mail_servers_verdict_is_read_from_the_message():
    raw = (b"From: Affirm <notices@affirm.com>\r\nReply-To: help@affirm.com\r\nAuthentication-Results: mx.example.test; dkim=pass; dmarc=pass\r\n"
           b"Subject: Your payment\r\nMessage-ID: <1@affirm.com>\r\n\r\nHello")
    parsed = parse_message(raw)
    assert "dmarc=pass" in parsed.auth_results and parsed.reply_to == "help@affirm.com"


def test_a_bill_a_lenders_plan_already_pays_steps_aside_and_says_why(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Affirm", amount=78.70, due_day=28, autopay=True))
        session.add(InstallmentPlan(provider="Affirm", merchant="Example Store", total=393.5, installment=78.70, count=5, remaining=4, next_date=date(2026, 10, 28), cadence_days=30, loan_id="L-9"))
        session.commit()
        out = sync_borrowing(session, TODAY)
        assert out["bills_retired"] == 1 and session.exec(select(Bill).where(Bill.name == "Affirm")).one().active is False
    fixed = owner.get("/api/v1/data-check").json()["fixed"]
    assert fixed[0]["what"] == "Stopped counting the bill “Affirm” ($78.70)" and "Example Store" in fixed[0]["why"]


def test_doubled_things_are_raised_and_settled_only_by_the_owners_answer(owner: TestClient):
    with Session(engine) as session:
        session.add(Home(name="Example Court 4", payee="Example Leasing Office"))
        session.add_all([Bill(name="Example Leasing Office", amount=1200.0, due_day=1), Bill(name="Example Video", amount=3.24, due_day=25),
                         Bill(name="Example Video", amount=8.65, due_day=1), Bill(name="Example Power", amount=90.0, due_day=12)])
        acct = Account(name="Example Checking", kind="checking")
        session.add(acct)
        session.flush()
        session.add_all([Transaction(account_id=acct.id, date=date(2026, 10, 3), merchant="EXAMPLE GROCER", amount=42.17, source="plaid", row_hash="dc-1"),
                         Transaction(account_id=acct.id, date=date(2026, 10, 4), merchant="Example Grocer", amount=42.17, source="rocketmoney", row_hash="dc-2")])
        session.commit()
    check = owner.get("/api/v1/data-check").json()
    kinds = {f["kind"]: f for f in check["findings"]}
    assert set(kinds) == {"counted_twice", "alike_bills", "duplicate_rows"}
    assert "Nothing changes until you answer" in check["said"]
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.active == True)).all()) == 4  # noqa: E712 - nothing changed yet

    twice = kinds["counted_twice"]
    assert owner.post("/api/v1/data-check/answer", json={"key": twice["key"], "action": "keep:1"}).status_code == 400  # not offered
    assert owner.post("/api/v1/data-check/answer", json={"key": "twice:bill:99:home:1", "action": "stop_bill"}).status_code == 409  # not raised
    out = owner.post("/api/v1/data-check/answer", json={"key": twice["key"], "action": "stop_bill"}).json()
    assert out["said"] == "Stopped counting “Example Leasing Office”. The other register keeps counting it."

    alike = kinds["alike_bills"]
    out = owner.post("/api/v1/data-check/answer", json={"key": alike["key"], "action": "different"}).json()
    assert out["said"] == "Kept both. This is not raised again." and not any(f["kind"] == "alike_bills" for f in out["findings"])

    rows = kinds["duplicate_rows"]
    hide = next(a["id"] for a in rows["actions"] if a["id"].startswith("hide:"))
    out = owner.post("/api/v1/data-check/answer", json={"key": rows["key"], "action": hide}).json()
    assert "Hid the" in out["said"] and out["findings"] == []


def test_the_same_email_in_two_mailboxes_waits_once_and_a_doubtful_sender_is_kept_out_of_answer_together(owner: TestClient):
    with Session(engine) as session:
        boxes = [MailAccount(label="Example A", host="imap.example.test", username="a"), MailAccount(label="Example B", host="imap.example.test", username="b")]
        session.add_all(boxes)
        session.flush()
        refs = [MessageRef(account_id=b.id, folder="INBOX", uid=1, message_id="<same@example.test>", sender="bills@example-power.test", sender_name="Example Power", trust="ok") for b in boxes]
        fake = MessageRef(account_id=boxes[0].id, folder="INBOX", uid=2, message_id="<fake@example.test>", sender="alerts@capital0ne-alerts.test", sender_name="Capital One",
                          trust="crit", trust_why="It calls itself Capital One, but it comes from capital0ne-alerts.test, not capitalone.com.")
        session.add_all([*refs, fake])
        session.flush()
        fields = json.dumps({"name": "Example Power", "amount": 90.0, "due_date": "2026-10-25"})
        session.add_all([ExtractedFact(kind="bill_due", fields=fields, status="pending", message_ref_id=r.id) for r in refs])
        session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Capital One", "amount": 500.0, "due_date": "2026-10-20"}), status="pending", message_ref_id=fake.id))
        session.commit()
        datacheck.run(session, TODAY)
        waiting = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()
        assert len(waiting) == 2  # one copy of the doubled email, and the doubtful one
        ids = [f.id for f in waiting]
    plan = owner.post("/api/v1/needs-you/facts", json={"ids": ids, "action": "preview"}).json()
    assert plan["own_card"] == 1
    check = owner.get("/api/v1/data-check").json()
    sender = next(f for f in check["findings"] if f["kind"] == "sender")
    assert sender["title"] == "Check the sender: Capital One"
    out = owner.post("/api/v1/data-check/answer", json={"key": sender["key"], "action": "discard"}).json()
    assert out["said"] == "Discarded 1 item read from it. Nothing from it is used."
    assert "Set aside 1 waiting item read twice" in [f["what"] for f in check["fixed"]]
