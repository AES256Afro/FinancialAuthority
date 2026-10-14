"""Where should $X a paycheck go: the same dollars to each destination, ranked by what is kept, every option
with its sources, its tax treatment, whether it is guaranteed, and the step that starts it."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.where_to_put import months_to_retire, where_to_put
from app.models import Account

TODAY = date(2026, 10, 14)


def test_ranks_the_same_dollars_with_sources_and_steps(owner: TestClient):
    with Session(engine) as session:
        seed(session)  # the household carries cards near 30% APR and an auto loan at 7.9%
        session.add(Account(name="Work 401(k)", institution="Example Retirement Co", kind="investment", subtype="401k", connector="manual", external_id="manual:401k", balance=24810.55))
        session.commit()
        out = where_to_put(session, TODAY, 50.0, years=10)
    ids = [o["id"] for o in out["options"]]
    assert {"savings", "k401_pretax", "roth_ira", "brokerage"} <= set(ids) and any(i.startswith("debt:") for i in ids) and "hsa" not in ids
    assert [o["rank"] for o in out["options"]] == list(range(1, len(ids) + 1))
    assert [o["after_tax"] for o in out["options"]] == sorted((o["after_tax"] for o in out["options"]), reverse=True)
    by = {o["id"]: o for o in out["options"]}
    assert by["k401_pretax"]["in_per_paycheck"] == round(50 / (1 - 0.22), 2) and by["k401_pretax"]["link"] == "/paystubs" and by["k401_pretax"]["exists"]
    assert by["roth_ira"]["after_tax"] == by["roth_ira"]["value"] and by["roth_ira"]["link"] == "/investments" and not by["roth_ira"]["exists"]
    assert by["brokerage"]["after_tax"] < by["brokerage"]["value"] and by["savings"]["guaranteed"] and len(by["savings"]["months"]) == 37
    card = max((o for o in out["options"] if o["id"].startswith("debt:")), key=lambda o: o["rate"])
    assert card["guaranteed"] and card["link"] == "/debts" and card["source"].startswith("debt:") and "APR" in card["note"]
    assert out["best"] == out["options"][0]["id"] and out["summary"].startswith("$50.00 a paycheck for 10 years") and "not a recommendation" in out["not_advice"]
    assert all(o["action"] and o["link"].startswith("/") for o in out["options"])

    owner.put("/api/v1/projections/assumptions", json={"employer_match_rate": 0.5, "employer_match_cap_per_paycheck": 100, "marginal_tax_rate": 0.24})
    matched = owner.get("/api/v1/projections/where?amount=50&years=10").json()
    k = next(o for o in matched["options"] if o["id"] == "k401_pretax")
    assert k["match_per_paycheck"] == round(0.5 * min(50 / (1 - 0.24), 100), 2) and "employer adds" in k["note"]
    assert owner.get("/api/v1/projections/where?amount=-1").status_code == 400
    assert owner.get("/api/v1/projections/where?amount=50&scenario=wild").status_code == 400
    low = owner.get("/api/v1/projections/where?amount=50&years=10&scenario=low").json()
    assert next(o for o in low["options"] if o["id"] == "roth_ira")["value"] < next(o for o in matched["options"] if o["id"] == "roth_ira")["value"]


def test_months_to_retire_a_balance():
    assert months_to_retire(1000.0, 0.0, 100.0, 120) == 10
    assert months_to_retire(1000.0, 2.0, 100.0, 120) < 10  # avoided interest at a steep APR shortens it
    assert months_to_retire(1000.0, 0.30, 0.0, 120) == 120
