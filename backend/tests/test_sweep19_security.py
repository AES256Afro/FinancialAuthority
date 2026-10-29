"""The fifteenth sweep: a hostile read of everything that changed between 0.9.3 and 0.10.6.

Each test is a finding: a path one of the day's fixes opened, or a guard a new path skipped."""

from datetime import date
from email.message import EmailMessage

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.db import engine
from app.main import app
from app.models import ExtractedFact, InstallmentPlan, MailAccount

H = {"X-Requested-With": "FinancialAuthority"}
AUTH_PASS = "mx.example.test; dmarc=pass; dkim=pass; spf=pass"


def _mailbox(session: Session) -> MailAccount:
    box = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
    session.add(box)
    session.commit()
    return box


def _statement_mail(sender: str, name: str, auth: str, n: int) -> bytes:
    from test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    m = EmailMessage()
    m["From"] = f"{name} <{sender}>"
    m["To"] = "owner@example.test"
    m["Subject"] = "Your Affirm Loans Statement is ready"
    m["Date"] = "Sun, 01 Nov 2026 08:00:00 +0000"
    m["Message-ID"] = f"<statement-{n}@example.test>"
    if auth:
        m["Authentication-Results"] = auth
    m.set_content("Your Affirm Loans Statement for October is attached.\n")
    m.add_attachment(pdf_of(affirm_statement_lines(date(2026, 10, 31))), maintype="application", subtype="pdf", filename="affirm-statement-2026-10.pdf")
    return bytes(m)


# ---- a lender's statement by mail wrote the plans with no confirmation, whoever sent it ------------------------


def test_a_statement_pdf_from_anyone_does_not_write_the_plans():
    """0.10.1 read a lender's statement PDF from any message and applied it at once: new plans, the plans typed from
    payments retired, dates and balances changed. Nothing checked who sent it, so a forged message (or any stranger's,
    unauthenticated, or from a domain of their own that passes its own DMARC) wrote the borrowing register with no
    confirmation, past the rule that a fact from mail waits for the owner and the rule that forged mail is the owner's."""
    from app.connectors.mail import handle_message
    from app.mail.imap import parse_message

    with Session(engine) as session:
        box = _mailbox(session)
        typed = InstallmentPlan(provider="Affirm", merchant="Example Store", total=400.0, installment=100.0, count=4, remaining=3, next_date=date(2026, 11, 10), cadence_days=30)
        session.add(typed)
        session.commit()
        hostile = [
            ("statements@affirm.com", "Affirm", "mx.example.test; dmarc=fail; dkim=fail; spf=fail"),  # forged From: the server says so
            ("statements@affirm.com", "Affirm", ""),  # no verdict at all
            ("statements@affirm-loans.example.test", "Affirm Loans", AUTH_PASS),  # a domain of the sender's own, passing its own checks
            ("billing@example.test", "Example Billing", AUTH_PASS),  # a stranger who does not even claim to be the lender
        ]
        for n, (sender, name, auth) in enumerate(hostile, start=1):
            handle_message(session, box, "INBOX", n, parse_message(_statement_mail(sender, name, auth, n)), date(2026, 11, 1))
            session.commit()
        plans = session.exec(select(InstallmentPlan)).all()
        assert [p.merchant for p in plans] == ["Example Store"], [(p.loan_id, p.merchant) for p in plans]
        session.refresh(typed)
        assert typed.active and typed.remaining == 3, "a message from a stranger retired a plan"


def test_a_statement_pdf_the_lender_sent_still_lands():
    """The same PDF from the lender's own domain, vouched for by the mail server, lands as it did."""
    from app.connectors.mail import handle_message
    from app.mail.imap import parse_message

    with Session(engine) as session:
        box = _mailbox(session)
        handle_message(session, box, "INBOX", 1, parse_message(_statement_mail("statements@affirm.com", "Affirm", AUTH_PASS, 1)), date(2026, 11, 1))
        session.commit()
        assert {p.loan_id for p in session.exec(select(InstallmentPlan)).all()} == {"7QRS-11AB", "7QRS-22CD"}


# ---- a key's confirmation through REST was never pushed -------------------------------------------------------


def _pending_bill_fact(session: Session, name: str) -> int:
    import json

    fact = ExtractedFact(kind="bill_due", fields=json.dumps({"name": name, "amount": 64.0, "due_date": "2026-11-02"}), confidence=0.9, status="pending", excerpt="due $64.00")
    session.add(fact)
    session.commit()
    return fact.id


def test_a_keys_confirmation_through_rest_is_pushed(owner: TestClient, monkeypatch):
    """Round eleven pushed a confirmation a key or a connected app made, so the owner sees it the day it happens and
    can undo it; only the MCP tool did. The same key (or an app's OAuth token, which REST takes too) confirming through
    the fact's own route or the batch on Needs you changed the registers with no push."""
    from app import notify

    pushed: list = []
    monkeypatch.setattr(notify, "send", lambda session, title, body, **k: pushed.append((title, body)) or {"ok": True})
    token = owner.post("/api/v1/keys", json={"name": "phone assistant", "scopes": ["read", "confirm"]}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    with Session(engine) as session:
        one, two = _pending_bill_fact(session, "Example Water"), _pending_bill_fact(session, "Example Power")
    app_client = TestClient(app)
    r = app_client.post(f"/api/v1/facts/{one}/confirm", headers=bearer, json={"apply": True})
    assert r.status_code == 200, r.text
    assert any("phone assistant" in t and "confirmed" in t for t, _ in pushed), pushed
    pushed.clear()
    r = app_client.post("/api/v1/needs-you/facts", headers=bearer, json={"ids": [two], "action": "confirm"})
    assert r.status_code == 200 and r.json()["done"], r.text
    assert any("phone assistant" in t and "confirmed" in t for t, _ in pushed), pushed


def test_the_owners_own_confirmation_is_not_pushed(owner: TestClient, monkeypatch):
    from app import notify

    pushed: list = []
    monkeypatch.setattr(notify, "send", lambda session, title, body, **k: pushed.append((title, body)) or {"ok": True})
    with Session(engine) as session:
        one = _pending_bill_fact(session, "Example Water")
    assert owner.post(f"/api/v1/facts/{one}/confirm", json={"apply": True}).status_code == 200
    assert not pushed


# ---- strangers' guesses at the calendar feed shut the owner's calendar out ------------------------------------


def test_wrong_feed_guesses_do_not_use_up_the_owners_feed(owner: TestClient, monkeypatch):
    """0.10.x counted the calendar feed per caller address, and with no proxy named every request carrying a
    forwarding header is one caller ("forwarded"). Behind a tunnel or Tailscale Serve that is everyone, so sixty
    wrong guesses an hour from a stranger answered the owner's own calendar 429 until the hour passed."""
    from app.api import exports

    monkeypatch.setattr(exports, "_hits", {})
    url = owner.post("/api/v1/calendar/feed", json={"amounts": False}).json()["url"]
    path = "/api/calendar/" + url.rsplit("/api/calendar/", 1)[1]
    stranger = TestClient(app)
    for n in range(exports.FEED_LIMIT):
        assert stranger.get(f"/api/calendar/{'x' * 40}{n}.ics", headers={"X-Forwarded-For": f"203.0.113.{n % 250}"}).status_code == 404
    assert stranger.get(f"/api/calendar/{'y' * 40}.ics", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429  # the guesser is still slowed
    calendar = TestClient(app)
    r = calendar.get(path, headers={"X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar"), r.status_code


# ---- the open-install refusal names FA_DEMO, and demo mode served real data open -------------------------------


def test_demo_mode_never_runs_open_on_real_data(monkeypatch):
    """0.10.x refuses to start an installed copy with no password, and says FA_DEMO=true is the way to look around;
    FA_DEMO skipped the refusal without looking at the data. On an install that already held the owner's household,
    following that sentence started the app open: anyone who reached the port was the owner of real data."""
    import inspect

    from app import auth, main
    from app.config import settings
    from app.engine.household import seed
    from app.models import Account

    monkeypatch.setattr(settings, "password", "")
    monkeypatch.setattr(settings, "install", "compose")
    monkeypatch.setattr(settings, "demo", True)
    assert auth.open_install_problem() is None  # the demo may run without a password...
    with Session(engine) as session:
        seed(session)
        assert auth.open_demo_problem(session) is None  # ...on the example household
        session.add(Account(name="Everyday Checking", institution="Example Bank", kind="checking", connector="plaid", external_id="acct-1"))
        session.commit()
        problem = auth.open_demo_problem(session)
    assert problem and "FA_PASSWORD" in problem
    source = inspect.getsource(main.lifespan)
    assert "open_demo_problem" in source and source.index("open_demo_problem") < source.index("if settings.demo:")
    monkeypatch.setattr(settings, "install", "")  # a developer's machine keeps its open demo
    with Session(engine) as session:
        assert auth.open_demo_problem(session) is None
