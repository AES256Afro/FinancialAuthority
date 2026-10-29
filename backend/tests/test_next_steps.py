"""Next steps: every unmet need names its place; a met one disappears."""

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.next_steps import next_steps
from app.models import Account, BankLink, MailAccount

TODAY = date(2026, 10, 14)


def test_empty_app_lists_the_setup_with_links(owner: TestClient):
    out = owner.get("/api/v1/next_steps").json()
    ids = {s["id"]: s for s in out["steps"]}
    assert {"bank-keys", "mail-connect", "invest-add", "paystub-none", "reviewer", "push", "publish"} <= set(ids)
    assert ids["bank-keys"]["link"] == "/settings/connectors#banks" and ids["invest-add"]["link"] == "/investments#add"
    assert all(s["action"] and s["link"].startswith("/") for s in out["steps"])
    severities = [s["severity"] for s in out["steps"]]
    assert severities == sorted(severities, key={"critical": 0, "warning": 1, "info": 2}.get)


def test_needs_follow_the_state(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        session.add(BankLink(provider="plaid", external_id="item-1", institution="Example Bank", accounts="acc-1", active=True, needs_login=True))
        session.add(BankLink(provider="teller", external_id="enr-1", institution="Example Credit Union", active=True, last_error="enrollment.disconnected"))
        session.add(MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc="", last_error="LOGIN failed"))
        session.add(Account(name="Work 401(k)", institution="Example Retirement Co", kind="investment", subtype="401k", connector="manual", external_id="manual:401k", balance=24810.55, balance_at=datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=60)))
        session.add(Account(name="Roth IRA", institution="Example Brokerage", kind="investment", subtype="roth_ira", connector="plaid", external_id="acc-1", balance=6120.0, balance_at=datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=40)))
        session.commit()
        out = next_steps(session, TODAY)
    ids = {s["id"]: s for s in out["steps"]}
    link_signin = next(s for s in out["steps"] if s["id"].startswith("link-signin-"))
    assert link_signin["severity"] == "critical" and "Example Bank" in link_signin["text"] and link_signin["link"].startswith("/settings/connectors?signin=")
    link_error = next(s for s in out["steps"] if s["id"].startswith("link-error-"))
    assert "Example Credit Union" in link_error["text"] and link_error["link"].startswith("/settings/connectors?sync=")
    mail_error = next(s for s in out["steps"] if s["id"].startswith("mail-error-"))
    assert "Proton" in mail_error["text"] and mail_error["link"] == "/settings/connectors#mail"
    stale = [s for s in out["steps"] if s["id"].startswith("invest-stale-")]
    manual = next(s for s in stale if "Work 401(k)" in s["text"])
    assert manual["link"].startswith("/investments?update=") and "60 days old" in manual["text"]
    linked = next(s for s in stale if "Roth IRA" in s["text"])
    assert linked["link"].startswith("/settings/connectors?sync=") and linked["action"] == "Sync"
    assert "bank-keys" not in ids and "bank-connect" not in ids and "mail-connect" not in ids and "invest-add" not in ids
    assert out["steps"][0]["severity"] == "critical"
