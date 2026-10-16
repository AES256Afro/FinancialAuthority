"""Affirm's loan confirmation: the schedule, the totals and the loan ID read deterministically; the mail
becomes a fact; confirming it makes a plan on the lender's own dates with its finance charge; the ledger
ticks payments off; the Borrowing page shows the loans and what they cost. Every figure is synthetic."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.connectors.mail import handle_message
from app.db import engine
from app.engine.borrowing import borrowing_overview, refresh_plan_progress
from app.engine.projection import build_events
from app.mail.classify import classify
from app.mail.imap import parse_message
from app.models import Account, ExtractedFact, InstallmentPlan, MailAccount, Transaction
from app.parsers.affirm import parse_loan_confirmation

TODAY = date(2026, 10, 15)

CONFIRMATION = """Here are your loan details, including information about how to repay.

Your loan confirmation

Jane – Example Outfitters has processed your order, and your loan has been confirmed!

Your Loan Breakdown

$687.65
Loan amount
The amount of credit you borrowed

$726.94
Total of payments
The amount you will have paid after making all scheduled payments

$39.29
Finance charge
The amount this credit will cost you

Your Payment Schedule

Oct 23, 2026

$242.32
Principal: $222.75
Interest: $19.57

Nov 23, 2026

$242.32
Principal: $229.09
Interest: $13.23

Dec 23, 2026

$242.30
Principal: $235.81
Interest: $6.49

Loan ID: 9ABC-DEF2

If you need help, visit our Help Center.
"""


def _raw(subject: str, text: str, message_id: str = "<loan-1@example.test>") -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"] = "Affirm <affirm-confirmation@affirm.example>"
    m["To"] = "owner@example.test"
    m["Subject"] = subject
    m["Date"] = "Thu, 15 Oct 2026 10:00:00 -0500"
    m["Message-ID"] = message_id
    m.set_content(text)
    return bytes(m)


def test_the_confirmation_reads_the_schedule():
    loan = parse_loan_confirmation(CONFIRMATION, TODAY)
    assert loan["merchant"] == "Example Outfitters" and loan["loan_id"] == "9ABC-DEF2" and loan["count"] == 3
    assert loan["loan_amount"] == 687.65 and loan["total"] == 726.94 and loan["finance_charge"] == 39.29 and loan["installment"] == 242.32
    assert [s["date"] for s in loan["schedule"]] == ["2026-10-23", "2026-11-23", "2026-12-23"] and loan["schedule"][2]["interest"] == 6.49
    assert loan["confidence"] == 0.95 and parse_loan_confirmation("Thanks for shopping", TODAY) is None
    assert classify("affirm-confirmation@affirm.example", "Your loan confirmation")[0] == "installment"
    assert classify("affirm-confirmation@affirm.example", "Your payment plan at Example Outfitters is approved")[0] == "installment"


def test_the_mail_becomes_a_plan_on_the_lenders_dates(owner: TestClient):
    with Session(engine) as session:
        account = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="o", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
        session.add(account)
        chk = Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=900.0, connector="demo", external_id="af:chk")
        session.add(chk)
        session.commit()
        chk_id = chk.id
        parsed = parse_message(_raw("Your loan confirmation", CONFIRMATION))
        outcome = handle_message(session, account, "INBOX", 501, parsed, TODAY)
        session.commit()
        assert outcome["kind"] == "installment" and outcome["fact"] == "pending"
        fact = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "installment")).one()
        fact_id = fact.id
        assert fact.confidence >= 0.95 and "9ABC-DEF2" in fact.fields
    done = owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}).json()
    assert done["applied_to"].startswith("plan:")
    with Session(engine) as session:
        plan = session.exec(select(InstallmentPlan)).one()
        assert plan.loan_id == "9ABC-DEF2" and plan.count == 3 and plan.remaining == 3 and plan.next_date == date(2026, 10, 23) and plan.finance_charge == 39.29 and plan.merchant == "Example Outfitters"
        events = [e for e in build_events([], [plan], [], TODAY) if e["kind"] == "installment"]
        assert [(e["date"].isoformat(), e["amount"]) for e in events] == [("2026-10-23", 242.32), ("2026-11-23", 242.32), ("2026-12-23", 242.3)]
        assert events[0]["name"] == "Example Outfitters 1 of 3"
        # the first payment posts: the ledger ticks it off
        session.add(Transaction(account_id=chk_id, date=date(2026, 10, 23), merchant="Affirm", raw_name="AFFIRM PAYMENT", description="", amount=242.32, category="Borrowing", source="demo", source_ref="a1", row_hash="affirm-pay-1"))
        session.commit()
        assert refresh_plan_progress(session, date(2026, 10, 24)) == 1
        session.refresh(plan)
        assert plan.remaining == 2 and plan.next_date == date(2026, 11, 23)
        later = [e for e in build_events([], [plan], [], date(2026, 10, 24)) if e["kind"] == "installment"]
        assert [e["date"].isoformat() for e in later] == ["2026-11-23", "2026-12-23"] and later[0]["name"] == "Example Outfitters 2 of 3"
        over = borrowing_overview(session, date(2026, 10, 24))
    loan = over["loans"][0]
    assert loan["loan_id"] == "9ABC-DEF2" and loan["remaining"] == 2 and loan["left_to_pay"] == 484.62 and loan["interest_left"] == 19.72 and loan["ends"] == "2026-12-23" and loan["from_lender"]
    assert over["loans_finance_charge"] == 39.29 and over["loans_left_to_pay"] == 484.62
    # the same confirmation sent again (a message of its own) updates the plan rather than doubling it
    with Session(engine) as session:
        account = session.exec(select(MailAccount)).one()
        parsed = parse_message(_raw("Your loan confirmation", CONFIRMATION, "<loan-1-resent@example.test>"))
        handle_message(session, account, "INBOX", 502, parsed, TODAY)
        session.commit()
        again = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "installment", ExtractedFact.status == "pending")).one()
        again_id = again.id
    owner.post(f"/api/v1/facts/{again_id}/confirm", json={"apply": True})
    with Session(engine) as session:
        assert len(session.exec(select(InstallmentPlan)).all()) == 1
