"""Ledger rules: a rule cleans the rows already there and every row a connector adds afterwards."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors.banks import upsert_account, upsert_transaction
from app.db import engine
from app.engine.household import seed
from app.engine.rules import apply_rules, create_rule, load_rules
from app.models import Rule, Transaction


def test_rules_apply_to_existing_rows_and_new_ones(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        sample = session.exec(select(Transaction).where(Transaction.merchant != "")).first()
        merchant = sample.merchant
        before = len(session.exec(select(Transaction).where(Transaction.merchant == merchant)).all())
        assert before > 0
        rule, changed = create_rule(session, kind="rename", pattern=merchant[:6], value="Cleaned Name")
        assert changed >= before and rule.kind == "rename"
        assert session.exec(select(Transaction).where(Transaction.merchant == merchant)).first() is None
        # A connector adds a row that matches: it arrives already cleaned, and categorised by a second rule.
        create_rule(session, kind="categorise", pattern="cleaned name", value="Groceries")
        account = upsert_account(session, provider="plaid", external_id="acc-x", name="Checking", institution="Example Bank", kind="checking", balance=100.0)
        upsert_transaction(session, provider="plaid", tx_id="t-rule-1", account=account, when=date(2026, 10, 10), amount=12.5, merchant=merchant, raw_name=merchant.upper(), category="Shopping")
        session.commit()
        row = session.exec(select(Transaction).where(Transaction.source_ref == "t-rule-1")).one()
        assert row.merchant == "Cleaned Name" and row.category == "Groceries"
        # Hide and regex rules.
        hide, hidden = create_rule(session, kind="hide", pattern="re:^cleaned", match_field="merchant")
        assert hidden >= 1 and session.get(Transaction, row.id).is_ignored is True
        t = Transaction(account_id=account.id, date=date(2026, 10, 1), merchant="Other", raw_name="OTHER", amount=1.0, row_hash="x")
        assert apply_rules(load_rules(session), t) == []

    listed = owner.get("/api/v1/ledger/rules").json()["rules"]
    assert [r["kind"] for r in listed] == ["rename", "categorise", "hide"]
    assert owner.post("/api/v1/ledger/rules", json={"kind": "rename", "pattern": "", "value": "x"}).status_code == 400
    assert owner.post("/api/v1/ledger/rules", json={"kind": "shout", "pattern": "a", "value": "x"}).status_code == 400
    made = owner.post("/api/v1/ledger/rules", json={"kind": "categorise", "pattern": "example taqueria", "value": "Dining & Drinks"}).json()
    assert made["rule"]["value"] == "Dining & Drinks" and made["changed"] >= 0
    assert owner.post("/api/v1/ledger/rules/apply").json()["changed"] >= 0
    assert owner.delete(f"/api/v1/ledger/rules/{made['rule']['id']}").json() == {"ok": True}
    with Session(engine) as session:
        assert session.get(Rule, made["rule"]["id"]) is None
