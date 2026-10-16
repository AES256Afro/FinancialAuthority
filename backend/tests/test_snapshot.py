"""Safe to spend, taken apart: by account and by step, each adding up to the one figure."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot


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
