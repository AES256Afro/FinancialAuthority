"""Cover, Saving and the guide on the synthetic household: what each account needs and the payday sheet, the
saving ladder and its share, the one thing today and its streaks, why each headline figure is what it is, the
weekly check-in and the first run. Golden figures on the fixture, and the invariants each must keep."""

import re
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.cover import account_cover
from app.engine.guide import one_thing
from app.engine.household import seed
from app.engine.saving import save_settings, saving_plan
from app.engine.snapshot import finance_snapshot
from app.models import Account, Debt, Transaction

TODAY = date(2026, 10, 14)


def _all(session: Session) -> tuple[dict, dict, dict]:
    snap = finance_snapshot(session, TODAY)
    sv = saving_plan(session, TODAY, snap=snap)
    return snap, sv, account_cover(session, TODAY, saving=sv)


def test_cover_says_what_each_account_needs_before_each_payday():
    with Session(engine) as session:
        seed(session)
        snap, _sv, cov = _all(session)
    assert cov["paydays"] == ["2026-10-16", "2026-10-30"]
    checking = next(a for a in cov["accounts"] if a["role"] == "spend")
    assert checking["deposit"] and checking["balance"] == 312.0
    w1, w2 = checking["windows"]
    assert w1["has"] == 312.0 and w1["bills"] == 57.0 and w1["buffer"] == 200.0
    # the arithmetic holds in every window: what it has, plus the paycheck, less what it needs
    for w in (w1, w2):
        assert w["left"] == round(w["has"] + w["inflow"] - w["needs"], 2)
        assert w["needs"] == round(w["bills"] + w["everyday"] + w["buffer"], 2)
    assert w2["inflow"] == snap["next_paycheck"]["amount"] and w2["has"] == round(w1["left"] + w1["buffer"], 2)
    # what leaves checking before payday is the snapshot's own figure (cash bills before the first payday)
    assert w1["bills"] == snap["due_before_payday"]["total"]
    assert checking["status"] == "good" and checking["headline"].startswith("Covered through")
    cards = [a for a in cov["accounts"] if a["role"] == "card"]
    assert {c["name"] for c in cards} == {"Rewards Card", "Store Card"}
    assert all(c["headline"].startswith("Next payment") for c in cards)
    dates = [r["date"] for r in cov["sheet"]]
    assert dates == sorted(dates) and len({r["id"] for r in cov["sheet"]}) == len(cov["sheet"])
    assert any(r["kind"] == "keep" and r["amount"] == snap["set_aside"]["total"] for r in cov["sheet"])


def test_a_card_over_thirty_percent_at_its_statement_gets_one_early_payment():
    with Session(engine) as session:
        seed(session)
        card = session.exec(select(Account).where(Account.name == "Rewards Card")).one()
        card.credit_limit = 3000.0
        session.add(card)
        session.commit()
        debt = session.exec(select(Debt).where(Debt.account_id == card.id)).one()
        _snap, _sv, cov = _all(session)
    c = next(a for a in cov["accounts"] if a["name"] == "Rewards Card")
    assert c["status"] == "warn" and c["statement_on"] == f"2026-10-{debt.statement_day:02d}"
    assert c["statement_utilisation"] > 0.3 and c["to_target"] == round(c["statement_balance"] - 900.0, 2)
    rows = [r for r in cov["sheet"] if r["to"] == "Rewards Card"]
    early = [r for r in rows if "reports under 30% used" in r["so_that"]]
    assert len(early) == 1 and early[0]["date"] == c["pay_by"] and early[0]["amount"] >= c["to_target"]
    # the payment it covers is not asked for a second time
    assert len(rows) == 1


def test_the_sheet_ticks_and_untick_over_the_api(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    cov = owner.get("/api/v1/cover").json()
    row = cov["sheet"][0]
    assert owner.put("/api/v1/cover/sheet", json={"id": row["id"], "done": True}).json()["done"] is True
    assert next(r for r in owner.get("/api/v1/cover").json()["sheet"] if r["id"] == row["id"])["done"] is True
    owner.put("/api/v1/cover/sheet", json={"id": row["id"], "done": False})
    assert next(r for r in owner.get("/api/v1/cover").json()["sheet"] if r["id"] == row["id"])["done"] is False


def test_the_ladder_rises_and_a_bigger_share_never_arrives_later():
    with Session(engine) as session:
        seed(session)
        snap, sv, _cov = _all(session)
        assert sv["share"] is None and not sv["chosen"] and sv["share_used"] == 25
        targets = [r["target"] for r in sv["rungs"]]
        assert targets[0] == 500.0 and targets[1:] == sorted(targets[1:])
        # each check keeps the share of what it truly leaves, never of a shortfall
        for pc, w in zip(sv["per_check"], snap["windows"]):
            assert pc["keep"] == round(max(0.0, pc["left"]) * 0.25, 2)
        # a larger share reaches every rung on or before a smaller one
        def as_key(e):
            return e or "9999-12-31"
        for small, big in zip(sv["by_share"], sv["by_share"][1:]):
            assert all(as_key(b) <= as_key(a) for a, b in zip(small["etas"], big["etas"]))
        # the rollover never makes a rung later
        assert all(as_key(r["eta"]) <= as_key(r["eta_without_rollover"]) for r in sv["rungs"])
        save_settings(session, share=10)
        chosen = saving_plan(session, TODAY, snap=snap)
    assert chosen["chosen"] and chosen["share_used"] == 10
    assert chosen["per_check"][1]["keep"] == round(max(0.0, chosen["per_check"][1]["left"]) * 0.10, 2)


def test_saving_settings_over_the_api(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    out = owner.put("/api/v1/saving", json={"share": 25, "rollover": False, "starter": 1000}).json()
    assert out["share"] == 25 and out["rollover"]["enabled"] is False and out["rungs"][0]["target"] == 1000.0
    assert owner.put("/api/v1/saving", json={"share": 95}).status_code == 400
    assert owner.put("/api/v1/saving", json={"clear_share": True}).json()["share"] is None


def test_the_one_thing_leads_with_the_biggest_effect_and_streaks_come_from_the_ledger():
    with Session(engine) as session:
        seed(session)
        snap, _sv, cov = _all(session)
        out = one_thing(session, TODAY, snap=snap, cover=cov)
    assert out["thing"]["text"] and out["thing"]["link"].startswith("/") and out["status"] in ("good", "warn", "crit")
    # the household has two advances repaying on payday: keeping the larger one funded comes first
    assert out["thing"]["source"].startswith("advance:") and out["thing"]["amount"] == max(a["amount"] for a in snap["advances"]["items"])
    streak = next(s for s in out["streaks"] if s["key"] == "no_advance")
    assert streak["value"] == (TODAY - date.fromisoformat(streak["since"])).days


def test_why_says_only_numbers_the_engine_returned():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
    numbers: set[float] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            numbers.add(round(abs(float(v)), 2))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk({k: v for k, v in snap.items() if k != "why"})
    assert set(snap["why"]) >= {"safe_to_spend", "due_before_payday", "window:2026-10-16"}
    for text in snap["why"].values():
        for m in re.findall(r"\$([0-9,]+\.[0-9]{2})", text):
            assert round(float(m.replace(",", "")), 2) in numbers, (m, text)
    assert "$55.00 to spend through Oct 15" in snap["why"]["safe_to_spend"]


def test_the_weekly_checkin_asks_and_keeps_the_answers(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    ci = owner.get("/api/v1/checkin").json()
    assert ci["week"] == "2026-W42" and not ci["done"] and len(ci["unexpected"]) <= 3 and ci["subscription"]
    flagged = ci["unexpected"][0]["id"]
    assert owner.post("/api/v1/checkin", json={"flagged": [flagged], "subscription": "drop", "saved": False}).json()["done"]
    again = owner.get("/api/v1/checkin").json()
    assert again["done"] and again["answers"]["subscription"] == "drop"
    with Session(engine) as session:
        assert "questioned in a weekly check-in" in session.get(Transaction, flagged).note
    assert owner.post("/api/v1/checkin", json={"subscription": "maybe"}).status_code == 400
    today = owner.get("/api/v1/today").json()
    assert any(s["key"] == "checkin" and s["value"] == 1 for s in today["streaks"])


def test_the_first_run_follows_the_registers(owner: TestClient):
    fresh = owner.get("/api/v1/firstrun").json()
    assert fresh["show"] and fresh["done"] == 0 and [s["id"] for s in fresh["steps"]] == ["paid", "data", "bills", "checks", "picture"]
    with Session(engine) as session:
        seed(session)
    seeded = owner.get("/api/v1/firstrun").json()
    assert {s["id"] for s in seeded["steps"] if s["done"]} >= {"paid", "data"}
    marked = owner.put("/api/v1/firstrun", json={"mark": "checks"}).json()
    assert next(s for s in marked["steps"] if s["id"] == "checks")["done"]
    assert owner.put("/api/v1/firstrun", json={"dismissed": True}).json()["show"] is False
    assert owner.put("/api/v1/firstrun", json={"mark": "nope"}).status_code == 400


def test_the_early_payment_comes_off_the_first_payment_after_the_statement():
    with Session(engine) as session:
        seed(session)
        card = session.exec(select(Account).where(Account.name == "Rewards Card")).one()
        card.credit_limit = 3000.0
        session.add(card)
        debt = session.exec(select(Debt).where(Debt.account_id == card.id)).one()
        debt.minimum = 0.0  # a card paid in full: the payment splits across the next two checks
        session.add(debt)
        session.commit()
        _snap, _sv, cov = _all(session)
    c = next(a for a in cov["accounts"] if a["name"] == "Rewards Card")
    before, after = c["payments"][0], c["payments"][1]
    assert before["date"] < c["statement_on"] <= after["date"] and 0 < c["to_target"] < after["amount"]
    rows = {r["date"]: r for r in cov["sheet"] if r["to"] == "Rewards Card"}
    assert rows[c["pay_by"]]["amount"] == c["to_target"]
    # the payment before the statement is untouched; the one after it is smaller by what went early
    assert rows[before["date"]]["amount"] == before["amount"]
    assert round(rows[after["date"]]["amount"] + c["to_target"], 2) == after["amount"]
