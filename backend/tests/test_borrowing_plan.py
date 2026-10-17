"""M54: the borrowing plan with its reasons. The stairs add up to the schedules, the finish line names what ends in
one or two payments, money beyond the schedule goes to the highest rate first, a 0% loan is paid on schedule, and a
loan without AutoPay is named."""

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.borrowing_plan import payoff_estimate
from app.models import InstallmentPlan


def _plan(merchant: str, amount: float, dates: list[str], apr: float | None, autopay: bool | None, last: float | None = None) -> InstallmentPlan:
    sched = [{"date": d, "amount": amount} for d in dates]
    if last is not None:
        sched[-1]["amount"] = last
    return InstallmentPlan(provider="Affirm", merchant=merchant, total=amount * len(dates), installment=amount, count=len(dates) + 2, remaining=len(dates),
                           next_date=date.fromisoformat(dates[0]), cadence_days=30, schedule=json.dumps(sched), apr=apr, autopay=autopay)


def test_the_plan_lays_out_the_stairs_the_finish_line_and_the_order_with_reasons(owner: TestClient):
    with Session(engine) as session:
        session.add_all([
            _plan("Example Glasses", 100.30, ["2026-11-16"], 34.3, True),
            _plan("Example Headphones", 48.30, ["2026-11-16", "2026-12-16"], 29.99, True, last=47.41),
            _plan("Example Computer", 320.22, ["2026-11-16", "2026-12-16", "2027-01-16", "2027-02-16"], 31.48, True),
            _plan("Example Desk", 78.70, ["2026-10-28", "2026-11-28", "2026-12-28"], 34.26, False),
            _plan("Example Chair", 50.00, ["2026-11-20", "2026-12-20", "2027-01-20"], 0.0, True),
        ])
        session.commit()
    out = owner.get("/api/v1/borrowing/plan").json()
    stairs = {s["month"]: s["total"] for s in out["stairs"]}
    assert stairs["2026-10"] == 78.70 and stairs["2026-11"] == round(100.30 + 48.30 + 320.22 + 78.70 + 50.00, 2) and stairs["2027-01"] == round(320.22 + 50.00, 2)
    assert sum(stairs.values()) == round(out["totals"]["left_to_pay"], 2)
    assert {f["name"] for f in out["finish_line"]} == {"Example Glasses", "Example Headphones"}
    kinds = [s["kind"] for s in out["steps"]]
    assert kinds[:4] == ["keep", "finish", "extra", "zero"]
    assert "AutoPay is off on Example Desk (Wed Oct 28, $78.70)" in out["steps"][0]["text"]
    assert "After Wed Dec 16 you watch 3 loans instead of 5" in out["steps"][1]["text"]
    assert out["order"][0]["name"] == "Example Glasses" and out["order"][0]["apr"] == 34.3  # the highest rate first
    assert "Example Chair: no interest ahead" in out["steps"][3]["text"]
    assert "Not financial advice" in out["not_advice"]


def test_paying_off_today_costs_less_than_the_payments_left_only_when_there_is_interest():
    pays = [{"date": "2026-11-16", "amount": 100.0}, {"date": "2026-12-16", "amount": 100.0}]
    assert payoff_estimate(pays, 0.0) == 200.0
    assert payoff_estimate(pays, 30.0) < 200.0
