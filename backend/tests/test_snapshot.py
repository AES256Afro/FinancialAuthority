"""Safe to spend, taken apart: by account and by step, each adding up to the one figure."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot

TODAY = date(2026, 10, 14)


def test_safe_to_spend_comes_apart_by_account_and_by_step():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, date(2026, 10, 14))
    safe = snap["safe_to_spend"]
    assert round(sum(a["safe"] for a in safe["by_account"]), 2) == safe["amount"]
    assert round(sum(s["amount"] for s in safe["steps"]), 2) == safe["amount"]
    assert sum(1 for a in safe["by_account"] if a["paycheck_lands"]) == 1
    assert any(x.get("owes") for x in safe["left_out"]) and all(x["why"] for x in safe["left_out"])
    before = next(s for s in safe["steps"] if s["label"].startswith("Comes out before payday"))
    assert round(-before["amount"], 2) == safe["parts"]["due_before_payday"]


def test_after_payday_the_next_paycheck_is_still_known_and_never_counted_twice():
    from app.models import Account, Income, Transaction
    from sqlmodel import select

    with Session(engine) as session:
        seed(session)
        income = session.exec(select(Income)).first()
        before = finance_snapshot(session, date(2026, 10, 16))
        assert before["next_paycheck"]["date"] == "2026-10-16"  # today's check has not landed yet
        checking = session.exec(select(Account).where(Account.kind == "checking")).first()
        session.add(Transaction(account_id=checking.id, date=date(2026, 10, 16), merchant="Example Employer", amount=-income.expected_net, is_income=True, category="Income", row_hash="pay-1016"))
        checking.balance = (checking.balance or 0) + income.expected_net
        session.add(checking)
        session.commit()
        landed = finance_snapshot(session, date(2026, 10, 16))
        later = finance_snapshot(session, date(2026, 10, 17))
    # once it lands, the next payday is the one after, and the deposit is not counted again
    assert landed["next_paycheck"]["date"] == "2026-10-30" and landed["next_paycheck"]["known"]
    assert later["next_paycheck"]["date"] == "2026-10-30" and later["next_paycheck"]["amount"] > 0  # without detection having run
    assert landed["projection"][0]["cash"] <= before["projection"][0]["cash"] + income.expected_net + 0.01


def test_a_purchase_alert_shows_until_the_bank_has_it_and_the_plan_says_how_fresh_it_is():
    import json as _json

    from app.mail.classify import extract
    from app.models import Account, ExtractedFact, Transaction

    fields = extract("bank_alert", "A purchase was made on your card", "A charge of $6.49 at Corner Store on Oct 14 using card ending 4321.")
    assert fields["alert"] == "purchase" and fields["amount"] == 6.49 and fields["merchant"] == "Corner Store"
    with Session(engine) as session:
        acct = Account(name="Checking", kind="checking", connector="manual", external_id="m:1", balance=400.0)
        session.add(acct)
        session.add(ExtractedFact(kind="bank_alert", fields=_json.dumps({**fields, "message_date": "2026-10-14"}), status="auto"))
        session.commit()
        snap = finance_snapshot(session, TODAY)
        seen = snap["in_flight"]["mail"]
        assert seen["counted"] is False and [i["merchant"] for i in seen["items"]] == ["Corner Store"] and seen["total"] == 6.49
        assert "fresh" in snap and "balances_as_of" in snap["fresh"]
        session.add(Transaction(account_id=acct.id, date=date(2026, 10, 15), merchant="CORNER STORE", amount=6.49, source="plaid", row_hash="p-corner", pending=True))
        session.commit()
        later = finance_snapshot(session, TODAY)
        assert later["in_flight"]["mail"]["items"] == []  # the bank has it now, pending
        assert [i["merchant"] for i in later["in_flight"]["pending"]["items"]] == ["CORNER STORE"]
