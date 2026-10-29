"""Sweep 12: what the mail-fed quarter (test_sweep12_bugs_mail_quarter.py) found, reduced. Three faults in the engine
are fixed and pinned here; three in the classifier and the mail connector are outside this sweep's area and are
pinned as strict xfails, so they turn green the day they are fixed there."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.db import engine
from app.engine.household import seed
from app.engine.recurring import bill_named
from app.engine.snapshot import finance_snapshot
from app.models import Bill, ExtractedFact, InstallmentPlan, MailAccount, MessageRef

TODAY = date(2026, 10, 14)


def test_the_exact_bill_name_wins_over_a_loose_match_listed_before_it():
    """Day 3 of the mail quarter (2026-10-16): Rocket Money's list named "Music" ($11, the 19th); bill_named's loose pass
    took the first bill that starts with the key, "Music (two)" ($10, the 11th), listed before "Music" on the register.
    The charge was not "the same", so it waited as a fact, and confirming it rewrote Music (two) to $11 on the 19th,
    leaving two bills for one subscription and none for the other."""
    bills = [Bill(name="Music (two)", amount=10.0, due_day=11), Bill(name="Music", amount=11.0, due_day=19)]
    assert bill_named(bills, "music", loose=True).name == "Music"
    assert bill_named(bills, "music", loose=False).name == "Music"
    assert bill_named(bills, "music (two)", loose=True).name == "Music (two)"
    # the loose pass still reads a biller's longer name as the bill it starts with
    assert bill_named(bills, "music billing", loose=True).name == "Music"
    assert bill_named(bills, "music (two) billing", loose=True).name == "Music (two)"
    assert bill_named(bills, "music billing", loose=False) is None


def _mailbox(session: Session) -> MailAccount:
    box = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
    session.add(box)
    session.commit()
    return box


def _alert(session: Session, box: MailAccount, uid: int, trust: str, fields: dict) -> None:
    ref = MessageRef(account_id=box.id, folder="INBOX", uid=uid, message_id=f"<alert-{uid}@example.test>", sender="alerts@bank.example.test", sender_name="Example Bank",
                     subject="Your account is overdrawn" if fields["alert"] == "overdraft" else "A purchase was made with your card", date=datetime(2026, 10, 14, 9, tzinfo=UTC), kind="bank_alert",
                     trust=trust, trust_why="The mail server could not confirm it came from bank.example.test: its domain did not vouch for it." if trust == "crit" else "")
    session.add(ref)
    session.flush()
    session.add(ExtractedFact(kind="bank_alert", message_ref_id=ref.id, fields=__import__("json").dumps({**fields, "message_date": TODAY.isoformat()}), confidence=0.9, status="auto", produced_by="rule:overdraft"))
    session.commit()


def test_a_forged_bank_alert_never_reaches_the_brief_or_the_in_flight_list(owner: TestClient):
    """Day 64 (2026-12-16): an overdraft alert whose DMARC check failed was kept as an auto fact (an alert needs no
    confirmation) and the brief said, in red, that the bank had sent an overdraft alert for $412; a forged purchase
    alert the same way sat in the in-flight list. A message is data; one the trust check found forged is not a fact
    about the account."""
    with Session(engine) as session:
        seed(session)
        box = _mailbox(session)
        _alert(session, box, 1, "crit", {"alert": "overdraft", "amount": 412.0, "last4": "1234"})
        _alert(session, box, 2, "crit", {"alert": "purchase", "amount": 77.77, "merchant": "Example Bait", "last4": "9012"})
        _alert(session, box, 3, "ok", {"alert": "overdraft", "amount": 38.2, "last4": "1234"})
        _alert(session, box, 4, "ok", {"alert": "purchase", "amount": 23.45, "merchant": "Example Coffee", "last4": "9012"})
        snap = finance_snapshot(session, TODAY)
    overdrafts = [b["text"] for b in snap["brief"] if "overdraft" in b["text"].lower()]
    assert overdrafts and all("$38" in t for t in overdrafts) and not any("412" in t for t in overdrafts), overdrafts
    flight = snap["in_flight"]["mail"]["items"]
    assert [i["amount"] for i in flight] == [23.45] and snap["in_flight"]["mail"]["total"] == 23.45, flight
    assert [a["fields"]["amount"] for a in snap["mail"]["alerts"]] == [23.45, 38.2]


def test_a_scheduled_period_is_owed_at_the_bills_amount_as_it_stands(owner: TestClient):
    """Day 86 (2027-01-08): a confirmed notice raised Streaming to $19 after the day's matching had run; the payments
    register's scheduled period for Jan 15 still read $17 until the next run (up to six hours in the live app), so a
    second run of the same day's jobs changed the snapshot. What is still to come is owed at the bill's amount now."""
    from app.engine.payments import match_payments, payments_overview

    with Session(engine) as session:
        seed(session)
        match_payments(session, TODAY)
        streaming = session.exec(select(Bill).where(Bill.name == "Streaming")).one()
        register = payments_overview(session, TODAY)
        before = [p for p in register["scheduled"] if p["bill_id"] == streaming.id]
        paid_before = [p for p in register["paid"] if p["bill_id"] == streaming.id]
        assert before and paid_before and all(p["amount"] == 17.0 for p in before)
        streaming.amount = 19.0
        session.add(streaming)
        session.commit()
        register = payments_overview(session, TODAY)
        after = [p for p in register["scheduled"] if p["bill_id"] == streaming.id]
        assert [p["due_date"] for p in after] == [p["due_date"] for p in before] and all(p["amount"] == 19.0 for p in after), after
        assert [p for p in register["paid"] if p["bill_id"] == streaming.id] == paid_before, "a matched period keeps what it was matched at"


# ---- outside this sweep's area: pinned, so they turn green when fixed there ----------------------------------------


def test_a_price_change_notice_reads_the_new_price():
    """Day 37 (2026-11-20): "your monthly price will go from $17.00 to $19.00" was read as $17.00, so confirming the
    notice left the bill at the old price and the plan short by $2 a month until December's notice said $19."""
    from app.mail.classify import extract

    text = "Your Streaming plan price is changing. Starting December 15, 2026, your monthly price will go from $17.00 to $19.00. No action is needed."
    assert extract("price_change", "A price change for your Streaming plan", text, date(2026, 11, 20))["amount"] == 19.0


def test_an_export_mail_saying_the_export_is_ready_is_the_export():
    from app.mail.classify import classify

    assert classify("no-reply@rocketmoney.com", "Your export is ready", "Your transactions export is ready.")[0] == "export"


def test_the_affirm_statement_pdf_by_mail_lands_on_the_plans(owner: TestClient):
    """Days 18, 48 and 79 (the 1st of each month): the lender's statement PDF came by mail and was not read; the mail
    read as a bill notice ("statement is ready") that waited on its own card. Handed to the import folder's reader,
    the same PDF makes the plans."""
    from email.message import EmailMessage

    from test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    from app.connectors.mail import handle_message
    from app.mail.imap import parse_message

    m = EmailMessage()
    m["From"] = "Affirm <statements@affirm.test>"
    m["To"] = "owner@example.test"
    m["Subject"] = "Your Affirm Loans Statement is ready"
    m["Date"] = "Sun, 01 Nov 2026 08:00:00 +0000"
    m["Message-ID"] = "<affirm-statement-1@affirm.test>"
    m.set_content("Your Affirm Loans Statement for October is attached.\n")
    m.add_attachment(pdf_of(affirm_statement_lines(date(2026, 10, 31))), maintype="application", subtype="pdf", filename="affirm-statement-2026-10.pdf")
    with Session(engine) as session:
        box = _mailbox(session)
        handle_message(session, box, "INBOX", 7, parse_message(bytes(m)), date(2026, 11, 1))
        session.commit()
        assert {p.loan_id for p in session.exec(select(InstallmentPlan)).all()} == {"7QRS-11AB", "7QRS-22CD"}
