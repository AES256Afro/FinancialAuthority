"""Record the engine's answers for the synthetic household, for the static demo.

Runs the app in-process on a throwaway data directory with the demo household, adds the few rows that make
the screens worth showing (a mailbox, a few messages with their facts, a connected app, a key, a recorded
review, a send waiting for approval), then fetches every route the screens call and writes
frontend/demo/demo-data.json. Nothing real is involved; nothing leaves the machine.

    cd backend && uv run python scripts/capture_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "frontend" / "demo" / "demo-data.json"

_tmp = Path(tempfile.mkdtemp(prefix="fa-demo-"))
os.environ.update({
    "FA_DATA_DIR": str(_tmp), "FA_DEMO": "true", "FA_JOBS": "false", "FA_PASSWORD": "", "FA_USERNAME": "owner",
    "FA_SESSION_SECRET": "demo-session", "FA_DATA_KEY": "demo-data-key", "FA_PUBLIC_URL": "", "TZ": "America/Chicago",
})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import approvals  # noqa: E402
from app.api import routes  # noqa: E402
from app.db import engine  # noqa: E402
from app.jobs.scheduler import jobs  # noqa: E402
from app.llm import roles  # noqa: E402
from app.llm.providers import Event, Scripted  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExtractedFact, MailAccount, MessageRef, utcnow  # noqa: E402

TODAY = date(2026, 10, 14)
routes.set_today_override(TODAY)

GETS = [
    "/api/auth/me", "/api/v1/about", "/api/v1/settings", "/api/v1/snapshot", "/api/v1/transactions/search?limit=500", "/api/v1/bills", "/api/v1/recurring",
    "/api/v1/facts?status=pending", "/api/v1/incomes", "/api/v1/payments", "/api/v1/paystubs", "/api/v1/mail/accounts", "/api/v1/mail/presets",
    "/api/v1/mail/messages?limit=200", "/api/v1/connectors", "/api/v1/banks", "/api/v1/keys", "/api/v1/oauth/clients", "/api/v1/jobs", "/api/v1/models",
    "/api/v1/rules", "/api/v1/push", "/api/v1/reviews?limit=3", "/api/v1/approvals?status=all&limit=30", "/api/v1/goals", "/api/v1/watchlist",
    "/api/v1/bills/suggestions", "/api/v1/debts", "/api/v1/borrowing", "/api/v1/accounts/overview", "/api/v1/audit?limit=300", "/api/v1/investments", "/api/v1/ledger/rules", "/api/v1/next_steps", "/api/v1/projections", *[f"/api/v1/projections/where?amount={x}&years={y}&scenario={sc}" for x in (25, 50, 75, 100, 150, 200, 300, 500) for y in (5, 10, 20, 25, 30) for sc in ("low", "mid", "high")],
    *[f"/api/v1/reports/spending?months={m}&by={b}" for m in (3, 6, 12, 24) for b in ("category", "merchant")],
]

REVIEW = {"notes": [
    {"kind": "tighten", "text": "The buffer of $200 is thin for a household carrying two advances; I would hold $300 until both are gone.", "action": {"type": "buffer", "value": 300}},
    {"kind": "disagree", "text": "Set-asides halve bills at or over $500, so the next window starts $648 short on Rent; I would set the whole of Rent aside.", "action": {"type": "rule", "text": "Set aside the whole of Rent from the paycheck before it."}},
    {"kind": "ask", "text": "The car loan lands on the 28th, two days before payday. Will the lender move it?", "action": {"type": "ask", "text": "Will the car loan's lender move the due date to the 13th?"}},
    {"kind": "ok", "text": "Paying the advances first is right; they take the money as the paycheck lands either way.", "action": {"type": "none"}},
]}


def seed_extras(client: TestClient) -> None:
    client.headers["X-Requested-With"] = "FinancialAuthority"
    jobs.run("detect_recurring")
    jobs.run("match_payments")
    jobs.run("self_check")
    # A mailbox and a few messages with their facts, as the mail connector would have left them.
    proton = client.post("/api/v1/mail/accounts", json={"kind": "proton", "label": "Proton", "username": "owner@example.test", "password": "not-a-real-password"}).json()
    client.post("/api/v1/mail/accounts", json={"kind": "gmail", "label": "Gmail", "username": "owner.example@example.test", "password": "not-a-real-password"})
    with Session(engine) as session:
        account = session.get(MailAccount, proton["id"])
        account.last_sync_at, account.messages_seen = utcnow(), 418
        session.add(account)
        rows = [
            ("Example Water", "billing@example-water.test", "Your bill is ready: $64.00 due Nov 2", "bill_due", "Your statement for October is ready. Amount due $64.00 by 11/02/2026.", 3),
            ("Example Electric", "no-reply@example-electric.test", "October statement: $142.18", "statement", "Your October statement is available. Current charges $142.18.", 5),
            ("Example Bank", "alerts@example-bank.test", "Deposit received: $2,240.00", "bank_alert", "A deposit of $2,240.00 posted to your checking account ending 1234.", 9),
            ("Example Payroll", "paystubs@example-payroll.test", "Your pay statement for 10/02/2026", "paystub", "Your pay statement is attached. Net pay $2,240.00.", 12),
            ("Example BNPL", "hello@example-bnpl.test", "Payment 2 of 4 is coming up", "installment", "Your next payment of $38.00 for Laptop plan is scheduled for 10/21/2026.", 15),
            ("Rocket Money", "support@rocketmoney.test", "Your export is ready", "export", "Your transactions export is ready to download. The link expires in 7 days.", 20),
        ]
        refs = []
        for i, (name, sender, subject, kind, snippet, days_ago) in enumerate(rows, start=1):
            ref = MessageRef(account_id=account.id, folder="INBOX", uid=1000 + i, sender=sender, sender_name=name, subject=subject, date=datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc) - timedelta(days=days_ago), snippet=snippet, kind=kind, has_attachments=kind == "paystub", attachment_names="paystub.pdf" if kind == "paystub" else "")
            session.add(ref)
            refs.append(ref)
        session.commit()
        for ref in refs:
            session.refresh(ref)
        session.add(ExtractedFact(kind="bill_due", message_ref_id=refs[0].id, fields=json.dumps({"name": "Example Water", "amount": 64.0, "due_date": "2026-11-02"}), confidence=0.92, status="pending", excerpt="Amount due $64.00 by 11/02/2026", produced_by="rules"))
        session.add(ExtractedFact(kind="installment", message_ref_id=refs[4].id, fields=json.dumps({"provider": "Example BNPL", "merchant": "Laptop plan", "installment": 38.0, "next_date": "2026-10-21"}), confidence=0.85, status="pending", excerpt="payment of $38.00 for Laptop plan is scheduled for 10/21/2026", produced_by="rules"))
        session.commit()
    # A 401(k) kept by hand, with its holdings and two statements' balances, so the Investing page shows.
    made = client.post("/api/v1/investments", json={"name": "Work 401(k)", "institution": "Example Retirement Co", "subtype": "401k", "balance": 23980.10, "as_of": "2026-06-30"}).json()
    plan_id = next(i["id"] for i in made["items"] if i["name"] == "Work 401(k)")
    client.put(f"/api/v1/investments/{plan_id}", json={"balance": 24810.55, "as_of": "2026-09-30"})
    client.post(f"/api/v1/investments/{plan_id}/holdings", json={"name": "Example Target 2055 Fund", "ticker": "EXTGT", "kind": "mutual fund", "quantity": 412.345, "price": 52.10, "value": 21483.17})
    client.post(f"/api/v1/investments/{plan_id}/holdings", json={"name": "Example Bond Index", "ticker": "EXBND", "kind": "fixed income", "quantity": 300, "price": 11.09, "value": 3327.38})
    client.post("/api/v1/investments", json={"name": "Roth IRA", "institution": "Example Brokerage", "subtype": "roth_ira", "balance": 6120.00, "as_of": "2026-10-01"})
    client.post("/api/v1/ledger/rules", json={"kind": "rename", "pattern": "example taqueria", "value": "Example Taqueria", "match_field": "merchant"})
    client.put("/api/v1/projections/assumptions", json={"years_to_retirement": 25, "employer_match_per_paycheck": 55})
    # A connected app and a key, as they appear on Settings › Clients.
    client.post("/register", json={"client_name": "Claude on the phone", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read confirm"})
    client.post("/api/v1/keys", json={"name": "claude-code", "scopes": ["read"], "redaction": "full"})
    # The reviewer's recorded notes, from a scripted model, so the Chat page shows them.
    client.put("/api/v1/models/reviewer", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "price_in": 3, "price_out": 15})
    client.put("/api/v1/rules", json={"rules": "- Pay the advances first, always.\n- Never let checking go under $100.", "goals": "Clear the advance apps by spring, then a $1,000 buffer."})
    scripted = Scripted([[Event("text", text=json.dumps(REVIEW)), Event("usage", input_tokens=3900, output_tokens=310), Event("done")]], model="claude-sonnet-5-5")
    scripted.id = "anthropic"  # type: ignore[misc]  (the recorded run names the provider the owner would choose)
    roles.set_override("reviewer", scripted)
    client.post("/api/v1/reviews")
    # Keep the override while the screens are recorded, so the reviewer card reads as configured.
    # A send waiting for approval, from the Bills page's date-move request.
    approvals.notify.send = lambda *a, **k: {"ok": True}  # type: ignore[assignment]
    client.post("/api/v1/approvals", json={"to": "billing@example-lender.test", "subject": "Request to move my billing date to the 13th", "body": "Hello,\n\nI would like to move my billing date from the 28th of each month to the 13th, starting with the next cycle. Please confirm the change and any one-time adjustment to the next bill.\n\nThank you.", "reason": "Car loan: 28th to 13th", "source": "bill:13"})


def main() -> None:
    with TestClient(app) as client:
        seed_extras(client)
        responses: dict[str, object] = {}
        for path in GETS:
            r = client.get(path)
            if r.status_code != 200:
                print(f"skip {path}: {r.status_code}", file=sys.stderr)
                continue
            responses[f"GET {path}"] = r.json()
        for msg in responses["GET /api/v1/mail/messages?limit=200"]["messages"]:  # type: ignore[index]
            r = client.get(f"/api/v1/mail/messages/{msg['id']}")
            if r.status_code == 200:
                responses[f"GET /api/v1/mail/messages/{msg['id']}"] = r.json()
        checks: dict[str, object] = {}
        amounts = list(range(0, 1001, 10)) + list(range(1050, 3001, 50))
        for amount in amounts:
            checks[f"{amount}:0"] = client.get(f"/api/v1/check?amount={amount}").json()
            checks[f"{amount}:4"] = client.get(f"/api/v1/check?amount={amount}&installments=4").json()
        brief = [b["text"] for b in responses["GET /api/v1/snapshot"]["brief"]]  # type: ignore[index]
        data = {
            "captured_at": utcnow().isoformat(),
            "household": "synthetic",
            "responses": responses,
            "checks": checks,
            "chat": {"intro": "This demo has no model behind it, so the engine answers in its own words. In the live app the advisor explains these lines and can read every register.", "brief": brief},
        }
        with Session(engine) as session:
            assert not session.exec(select(MailAccount).where(MailAccount.password_enc == "not-a-real-password")).first()
    roles.set_override("reviewer", None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"))
    for forbidden in ("not-a-real-password", "fa_"):
        if forbidden == "not-a-real-password":
            assert forbidden not in text, "a password reached the demo data"
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text) / 1e6:.2f} MB): {len(responses)} responses, {len(checks)} checks")


if __name__ == "__main__":
    main()
