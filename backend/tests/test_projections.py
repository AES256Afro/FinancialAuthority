"""The outlook: compounding that matches the textbook, scenarios in order, today's dollars below nominal,
contributions from the paystubs unless the owner overrides them, the retirement line, and the assumptions route."""

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.projections import grow, outlook
from app.models import Account, Paystub

TODAY = date(2026, 10, 14)


def test_grow_matches_the_textbook():
    assert grow(1000.0, 0.0, 0.0, 12)[-1] == 1000.0
    ten_years = grow(1000.0, 0.07, 0.0, 120)[-1]
    assert abs(ten_years - 1000.0 * 1.07**10) < 0.01  # yearly compounding, split into equal monthly steps
    with_contributions = grow(0.0, 0.0, 100.0, 24)
    assert with_contributions[-1] == 2400.0 and all(b <= a for a, b in zip(with_contributions[1:], with_contributions, strict=False))


def test_outlook_from_the_household(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        session.add(Account(name="Work 401(k)", institution="Example Retirement Co", kind="investment", subtype="401k", connector="manual", external_id="manual:401k", balance=24810.55))
        session.add(Paystub(pay_date=date(2026, 10, 2), net=2240.0, gross=2780.0, stub_hash="stub-outlook", deductions=json.dumps([{"name": "401(k) pre-tax", "amount": 139.0}])))
        session.commit()
        out = outlook(session, TODAY)
    inv, sav = out["investments"], out["savings"]
    assert out["paychecks_per_year"] == 26 and inv["now"] == 24810.55 and inv["contribution_source"] == "paystubs" and inv["own_per_paycheck"] >= 139.0
    assert [s["id"] for s in inv["scenarios"]] == ["low", "mid", "high"]
    last = {s["id"]: s["years"][-1]["balance"] for s in inv["scenarios"]}
    assert last["low"] < last["mid"] < last["high"]
    for s in inv["scenarios"]:
        assert [m["year"] for m in s["milestones"]] == [1, 5, 10, 20, 30]
        assert all(row["real"] <= row["balance"] and row["growth"] == round(row["balance"] - row["contributed"], 2) for row in s["years"])
        assert s["at_retirement"] is None
    assert len(sav["months"]) == 37 and sav["months"][0]["month"] == "2026-10" and sav["milestones"][-1]["months"] == 36
    assert sav["per_paycheck"] >= sav["goals_per_paycheck"] >= 0

    changed = owner.put("/api/v1/projections/assumptions", json={"savings_apy": 0.045, "returns": {"mid": 0.08}, "years_to_retirement": 20, "investment_contribution_per_paycheck": 200, "employer_match_per_paycheck": 50, "inflation": 0.03}).json()
    a = changed["assumptions"]
    assert a["savings_apy"] == 0.045 and a["returns"]["mid"] == 0.08 and a["years_to_retirement"] == 20
    inv2 = changed["outlook"]["investments"]
    assert inv2["contribution_source"] == "assumption" and inv2["per_paycheck"] == 250.0 and inv2["years_to_retirement"] == 20
    mid = next(s for s in inv2["scenarios"] if s["id"] == "mid")
    assert mid["at_retirement"]["year"] == 20 and mid["at_retirement"]["monthly_at_withdrawal_rate"] == round(mid["at_retirement"]["balance"] * 0.04 / 12, 2)
    assert mid["years"][0]["contributed"] == round(24810.55 + 250.0 * 26, 2)
    assert owner.put("/api/v1/projections/assumptions", json={"returns": {"low": 0.2}}).status_code == 400  # low above mid
    assert owner.put("/api/v1/projections/assumptions", json={"savings_apy": 1.5}).status_code == 400
    back = owner.put("/api/v1/projections/assumptions", json={"use_paystubs_for_contributions": True, "clear_retirement": True}).json()
    assert back["outlook"]["investments"]["contribution_source"] == "paystubs" and back["assumptions"]["years_to_retirement"] is None
    listed = owner.get("/api/v1/projections").json()
    assert listed["not_advice"] and listed["assumptions"]["savings_apy"] == 0.045
