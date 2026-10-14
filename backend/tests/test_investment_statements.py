"""A retirement vendor's statement email becomes a pending balance fact; confirming it keeps the vendor's
account current by hand, with the balance on its day."""

from datetime import date
from email.message import EmailMessage

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.connectors.mail import handle_message
from app.db import engine
from app.mail.classify import classify, extract
from app.mail.imap import parse_message
from app.models import Account, BalanceSnapshot, ExtractedFact, MailAccount

TODAY = date(2026, 10, 14)
TEXT = "Your quarterly statement is ready.\nAccount: 401(k) Savings Plan\nEnding balance as of 09/30/2026: $24,810.55\nVested balance: $24,810.55\nContributions this period: $834.00"


def statement() -> bytes:
    m = EmailMessage()
    m["From"] = "Example Retirement Co <statements@example-retirement.test>"
    m["To"] = "owner@example.test"
    m["Subject"] = "Your 401(k) quarterly statement is available"
    m["Date"] = "Thu, 08 Oct 2026 06:00:00 -0500"
    m["Message-ID"] = "<q3-1@example.test>"
    m.set_content(TEXT)
    return bytes(m)


def test_statement_classifies_extracts_and_confirms_into_an_account(owner: TestClient):
    kind, confidence, rule = classify("statements@netbenefits.example", "Your 401(k) quarterly statement is available")
    assert kind == "investment_balance" and rule == "rule:investment-statement"
    fields = extract("investment_balance", "Your 401(k) quarterly statement is available", TEXT, TODAY)
    assert fields["balance"] == 24810.55 and fields["as_of"] == "2026-09-30" and fields["what"] == "401(k)"

    with Session(engine) as session:
        account = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("app-pass"), use_ssl=False, starttls=True)
        session.add(account)
        session.commit()
        parsed = parse_message(statement())
        # The sender is not one of the named vendors, so the rule falls back to the body; classify by hand here.
        handle_message(session, account, "INBOX", 11, parsed, TODAY)
        fact = session.exec(select(ExtractedFact)).first()
        if fact.kind != "investment_balance":
            fact.kind, fact.fields = "investment_balance", __import__("json").dumps(fields)
            session.add(fact)
            session.commit()
        fact_id = fact.id
    confirmed = owner.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}).json()
    assert confirmed["status"] == "applied" and confirmed["applied_to"].startswith("account:")
    with Session(engine) as session:
        created = session.exec(select(Account).where(Account.kind == "investment")).one()
        assert created.subtype == "401k" and created.balance == 24810.55 and created.institution == "Example Retirement Co" and created.balance_at.date() == date(2026, 9, 30)
        assert session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == created.id)).one().date == date(2026, 9, 30)
    listed = owner.get("/api/v1/investments").json()
    assert listed["retirement"] == 24810.55 and listed["items"][0]["what"] == "401(k)"
