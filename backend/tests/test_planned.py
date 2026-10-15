"""Planned payments and the card's terms: a card payment scheduled for tonight comes off the plan today and
nets off the card, settles itself when the transaction posts, and the brief says so; the available balance is
what a spending account holds; the card's line arrives from the bank, its APR from the owner, and the credit
picture follows. Every figure is synthetic."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors.banks import upsert_account
from app.db import engine
from app.engine.borrowing import borrowing_overview
from app.engine.planned import settle_planned
from app.engine.snapshot import finance_snapshot
from app.models import Account, Debt, Income, PlannedPayment, Transaction

TODAY = date(2026, 10, 15)


def _household(session: Session) -> tuple[Account, Account]:
    chk = Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=3000.0, connector="demo", external_id="p:chk")
    session.add(chk)
    session.flush()
    card = upsert_account(session, provider="demo", external_id="p:card", name="Everyday Card", institution="Example Bank", kind="card", balance=1600.0, credit_limit=1650.0)
    session.add(Income(name="Example Staffing", cadence="semimonthly", expected_net=2200.0, expected_net_end=2000.0, next_date=date(2026, 10, 30)))
    session.commit()
    return chk, card


def test_a_planned_card_payment_is_counted_today_and_nets_off_the_card(owner: TestClient):
    with Session(engine) as session:
        chk, card = _household(session)
        debt = session.exec(select(Debt).where(Debt.account_id == card.id)).one()
        assert debt.credit_limit == 1650.0 and debt.balance == 1600.0  # the card's debt row follows the account
        before = finance_snapshot(session, TODAY)
        assert not any(e.get("planned") for e in before["due_before_payday"]["items"])
        made = owner.post("/api/v1/planned", json={"name": "Everyday Card, in full", "amount": 1600.0, "date": "2026-10-15", "kind": "card_payment", "to_account_id": card.id}).json()
        assert made["done"] is False and made["kind"] == "card_payment"
        snap = finance_snapshot(session, TODAY)
        planned = [e for e in snap["due_before_payday"]["items"] if e.get("planned")]
        assert len(planned) == 1 and planned[0]["amount"] == 1600.0 and planned[0]["date"] == "2026-10-15"
        assert not any(e["kind"] == "card_payment" for e in snap["window"]["items"] + snap["set_aside"]["items"])  # the card is not held back twice
        assert snap["safe_to_spend"]["parts"]["due_before_payday"] == 1600.0 and snap["planned"][0]["name"] == "Everyday Card, in full"
        assert any(b["text"].startswith("Planned: $1,600 to Everyday Card") for b in snap["brief"])
        # the payment posts: money out of checking, flagged as a card payment; the plan settles and the event is gone
        session.add(Transaction(account_id=chk.id, date=date(2026, 10, 16), merchant="Payment to Everyday Card", raw_name="CAPITAL ONE CRCARDPMT", description="payment", amount=1600.0, category="Credit Card Payment", is_card_payment=True, is_transfer=True, source="demo", source_ref="pm", row_hash="planned-pm"))
        session.commit()
        assert settle_planned(session, date(2026, 10, 16)) == 1
        row = session.get(PlannedPayment, made["id"])
        assert row.done is True and row.transaction_id is not None
        after = finance_snapshot(session, date(2026, 10, 16))
        assert not any(e.get("planned") for e in after["due_before_payday"]["items"]) and after["planned"] == []
    listed = owner.get("/api/v1/planned").json()
    assert listed["items"][0]["done"] is True and listed["cards"][0]["name"] == "Everyday Card"
    assert owner.post("/api/v1/planned", json={"name": "x", "amount": 0, "date": "2026-10-15"}).status_code == 400


def test_the_card_terms_make_the_credit_picture(owner: TestClient):
    with Session(engine) as session:
        _chk, card = _household(session)
        debt_id = session.exec(select(Debt).where(Debt.account_id == card.id)).one().id
    out = owner.put(f"/api/v1/debts/{debt_id}", json={"apr": 28.99, "statement_day": 23, "due_day": 18, "pay_in_full": True}).json()
    item = next(d for d in out["items"] if d["source"] == f"debt:{debt_id}")
    assert item["apr"] == 28.99 and item["credit_limit"] == 1650.0 and item["utilisation"] == round(1600.0 / 1650.0, 4) and item["statement_day"] == 23
    with Session(engine) as session:
        over = borrowing_overview(session, TODAY)
    credit = over["credit"][0]
    assert credit["name"] == "Everyday Card" and credit["utilisation"] == round(1600.0 / 1650.0, 4) and credit["next_statement"] == "2026-10-23" and credit["next_due"] == "2026-10-18"
    assert over["cost"]["card_interest_month"] == 0.0  # paid in full, so nothing is carried
    assert any("97% of its $1,650 line" in s["text"] and "closes on Oct 23" in s["text"] for s in over["steps"])
    assert owner.put(f"/api/v1/debts/{debt_id}", json={"pay_in_full": False}).status_code == 200
    with Session(engine) as session:
        carried = borrowing_overview(session, TODAY)
    assert carried["cost"]["card_interest_month"] == round(1600.0 * 28.99 / 100 / 12, 2)


def test_plaid_balances_use_available_for_spending_and_the_line_for_cards():
    from app.connectors.plaid import account_kind

    assert account_kind("depository", "checking") == "checking" and account_kind("credit", "credit card") == "card"
    with Session(engine) as session:
        a = upsert_account(session, provider="plaid", external_id="x1", name="Checking", institution="Example Bank", kind="checking", balance=3022.07)
        c = upsert_account(session, provider="plaid", external_id="x2", name="Card", institution="Example Bank", kind="card", balance=1629.31, credit_limit=1650.0)
        session.commit()
        assert a.balance == 3022.07 and c.credit_limit == 1650.0
        debt = session.exec(select(Debt).where(Debt.account_id == c.id)).one()
        assert debt.balance == 1629.31 and debt.credit_limit == 1650.0 and debt.name == "Card"
        upsert_account(session, provider="plaid", external_id="x2", name="Card", institution="Example Bank", kind="card", balance=0.0, credit_limit=1650.0)
        session.commit()
        session.refresh(debt)
        assert debt.balance == 0.0 and len(session.exec(select(Debt)).all()) == 1
