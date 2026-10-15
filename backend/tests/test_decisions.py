"""The purchase check and the billing-date optimiser: golden checks on the fixture and the invariants
(a no-cost purchase is always yes, a huge one never is, a suggested move never lowers the low point)."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.decisions import billing_date_suggestions, purchase_check
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot
from app.models import Bill

TODAY = date(2026, 10, 14)


def test_purchase_check_verdicts_and_sources():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
        nothing = purchase_check(session, TODAY, 0)
        huge = purchase_check(session, TODAY, 50000)
        safe_amount = snap["safe_to_spend"]["amount"]
        modest = purchase_check(session, TODAY, max(1.0, min(25.0, safe_amount)))
        split = purchase_check(session, TODAY, 400, installments=4)
        lump = purchase_check(session, TODAY, 400)
    assert nothing["verdict"] == "yes" and nothing["breaks"] is None
    assert huge["verdict"] == "no" and huge["breaks"] is not None and huge["breaks"]["shortfall"] > 0 and huge["first_ok_date"] is None
    assert huge["breaks"]["source"].startswith(("bill:", "plan:", "advance:", "setting:"))
    assert modest["low_point"]["at_pace"] <= nothing["low_point"]["at_pace"]
    assert split["charges"][0]["amount"] == 100.0 and len(split["charges"]) == 4 and split["charges"][3]["date"] == (TODAY + timedelta(days=42)).isoformat()
    assert split["low_point"]["at_pace"] >= lump["low_point"]["at_pace"]  # spreading never deepens the first low
    for out in (nothing, huge, modest, split):
        assert "snapshot:projection" in out["sources"] and out["not_advice"]
        if out["verdict"] == "wait":
            assert out["first_ok_date"] and out["first_ok_date"] > out["on"]


def test_purchase_check_wait_finds_the_first_passing_date():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
        already_short = min(p["at_pace"] for p in snap["projection"]) < 0
        headroom = max(0.0, min(p["at_pace"] for p in snap["projection"]))  # what the honest line can lose before it dips under zero
        over = purchase_check(session, TODAY, headroom + 150)
        small = purchase_check(session, TODAY, 20)
    assert over["verdict"] in ("wait", "no")
    if already_short:
        # The household's honest line already dips under zero: even a small purchase before the dip deepens
        # it, so the answer is to wait until the line is clear, and the first clear date is given.
        assert small["verdict"] == "wait" and small["breaks"] is not None and small["first_ok_date"] > TODAY.isoformat()
        assert small["breaks"]["shortfall"] > 0 and small["breaks"]["already_short"] >= 0
    for out in (over, small):
        if out["verdict"] == "wait":
            assert out["first_ok_date"] > TODAY.isoformat()
            with Session(engine) as session:
                later = purchase_check(session, TODAY, out["amount"], on=date.fromisoformat(out["first_ok_date"]))
            assert later["verdict"] == "yes"


def test_billing_date_suggestions_never_worsen_the_low_point():
    with Session(engine) as session:
        seed(session)
        out = billing_date_suggestions(session, TODAY)
        assert out["movable"] > 0
        for s in out["suggestions"]:
            assert s["low_after"] >= out["baseline"]["low"] and s["improvement"] >= 1.0
            assert 1 <= s["to_day"] <= 28 and s["to_day"] != s["from_day"]
            assert "Ask" in s["instruction"] and s["draft"].startswith("Hello")
            # Applying the move really yields the predicted low point.
            bill = session.get(Bill, s["bill_id"])
            original = bill.due_day
            bill.due_day = s["to_day"]
            session.add(bill)
            session.commit()
            check = billing_date_suggestions(session, TODAY)
            assert abs(check["baseline"]["low"] - s["low_after"]) < 0.01
            bill.due_day = original
            session.add(bill)
            session.commit()
        assert out["suggestions"] == sorted(out["suggestions"], key=lambda s: (-s["improvement"], s["name"]))
        # Together: each step lifts the low point by at least a dollar, and the plan's low is what applying every move yields.
        lows = [out["baseline"]["low"], *(p["low_after"] for p in out["plan"])]
        assert all(b - a >= 1.0 for a, b in zip(lows, lows[1:], strict=False))
        assert out["plan_low"] == lows[-1]
        if out["plan"]:
            originals = {}
            for step in out["plan"]:
                bill = session.get(Bill, step["bill_id"])
                originals[bill.id] = bill.due_day
                bill.due_day = step["to_day"]
                session.add(bill)
            session.commit()
            assert abs(billing_date_suggestions(session, TODAY)["baseline"]["low"] - out["plan_low"]) < 0.01
            for bill_id, day in originals.items():
                bill = session.get(Bill, bill_id)
                bill.due_day = day
                session.add(bill)
            session.commit()
        immovable = {b.name for b in session.exec(select(Bill).where(Bill.movable == False)).all()}  # noqa: E712
        assert not immovable & {s["name"] for s in out["suggestions"]}


def test_decision_routes_and_tools(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    check = owner.get("/api/v1/check?amount=120").json()
    assert check["verdict"] in ("yes", "wait", "no") and check["amount"] == 120.0
    split = owner.get("/api/v1/check?amount=120&installments=4&on=2026-10-20").json()
    assert split["installments"] == 4 and split["on"] == "2026-10-20"
    assert owner.get("/api/v1/check?amount=-5").status_code == 400
    moves = owner.get("/api/v1/bills/suggestions").json()
    assert "baseline" in moves and isinstance(moves["suggestions"], list)
