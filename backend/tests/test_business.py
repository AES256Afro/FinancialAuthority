"""M56 to M60: the business. The bare-minimum guide, the write-off finder (each kind with its rule and source; a
purchase the owner calls personal is never suggested again), invoices matched to their deposit, the books by the lines
of Schedule C with self-employment tax, mileage at the rate for the day driven (never the commute), and the tax
center. Read keys read; only the owner changes anything."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine import taxrules as R
from app.models import Account, Transaction


def _ledger() -> dict[str, int]:
    with Session(engine) as session:
        acct = Account(name="Example Checking", kind="checking")
        session.add(acct)
        session.flush()
        rows = {
            "claude": Transaction(account_id=acct.id, date=date(2026, 9, 3), merchant="ANTHROPIC CLAUDE", amount=20.0, row_hash="b-1"),
            "gpu": Transaction(account_id=acct.id, date=date(2025, 4, 28), merchant="MICRO CENTER", amount=892.44, row_hash="b-2"),
            "cable": Transaction(account_id=acct.id, date=date(2026, 5, 1), merchant="MICRO CENTER", amount=12.99, row_hash="b-3"),
            "phone": Transaction(account_id=acct.id, date=date(2026, 9, 10), merchant="VERIZON WIRELESS", amount=85.0, row_hash="b-4"),
            "food": Transaction(account_id=acct.id, date=date(2026, 9, 11), merchant="EXAMPLE GROCER", amount=60.0, row_hash="b-5"),
            "deposit": Transaction(account_id=acct.id, date=date(2026, 10, 20), merchant="EXAMPLE FRIEND ZELLE", amount=-1000.0, row_hash="b-6"),
        }
        session.add_all(rows.values())
        session.commit()
        return {k: v.id for k, v in rows.items()}


def test_every_rule_names_its_source_and_the_rates_follow_the_day():
    assert all(t and u.startswith("https://") for t, u in R.SOURCES.values())
    assert R.mileage_rate(date(2026, 6, 30)) == 72.5 and R.mileage_rate(date(2026, 7, 1)) == 76.0 and R.mileage_rate(date(2024, 5, 1)) is None
    assert R.form_1099_nec_threshold(2025) == 600.0 and R.form_1099_nec_threshold(2026) == 2000.0


def test_the_guide_walks_the_bare_minimum_and_ticks_as_it_goes(owner: TestClient):
    g = owner.get("/api/v1/business").json()["guide"]
    assert g["steps"][0]["id"] == "record" and not g["steps"][0]["done"] and "sole proprietor" in g["bare_minimum"]
    assert [r["what"] for r in g["compare"]][:2] == ["What it is", "To start"] and "$300" in g["compare"][1]["llc"]
    assert all(s["source"]["url"].startswith("https://") for s in g["steps"])
    out = owner.post("/api/v1/business", json={"name": "Example Studio", "what_it_does": "Design work for friends", "started_on": "2026-10-01", "ein_on_file": True}).json()
    done = {s["id"]: s["done"] for s in out["guide"]["steps"]}
    assert done["record"] and done["ein"] and not done["w9"]
    out = owner.post("/api/v1/business/setup/w9", json={"done": True}).json()
    assert {s["id"]: s["done"] for s in out["guide"]["steps"]}["w9"] and out["guide"]["said"].startswith("3 of ")
    assert owner.post("/api/v1/business/setup/nonsense", json={"done": True}).status_code == 404


def test_the_finder_suggests_with_rules_and_never_asks_again_about_what_is_personal(owner: TestClient):
    ids = _ledger()
    bid = owner.post("/api/v1/business", json={"name": "Example Studio"}).json()["businesses"][0]["id"]
    f = owner.get("/api/v1/business/finder").json()
    groups = {g["key"]: g for g in f["groups"]}
    assert set(groups) == {"software", "depreciation", "utilities"}  # the cable and the groceries are not suggested
    assert [i["id"] for i in groups["depreciation"]["items"]] == [ids["gpu"]] and groups["depreciation"]["source"]["url"].startswith("https://")
    assert f["equipment"]["count"] == 1 and "lower of what you paid" in f["equipment"]["words"]
    assert any(n["what"] == "Moving to a new home" for n in f["never"])
    out = owner.post("/api/v1/business/finder/answer", json={"ids": [ids["phone"]], "answer": "personal"}).json()
    assert out["said"] == "Kept 1 as personal. They are not suggested again." and "utilities" not in {g["key"] for g in out["groups"]}
    out = owner.post("/api/v1/business/finder/answer", json={"ids": [ids["claude"]], "answer": "business", "business_id": bid, "tax_line": "software"}).json()
    assert out["said"] == "Marked 1 as the business's." and "software" not in {g["key"] for g in out["groups"]}


def test_an_invoice_is_matched_to_its_deposit_and_the_books_add_up(owner: TestClient, client: TestClient):
    ids = _ledger()
    bid = owner.post("/api/v1/business", json={"name": "Example Studio"}).json()["businesses"][0]["id"]
    inv = owner.post(f"/api/v1/business/{bid}/invoices", json={"counterparty": "<b>Example Friend</b>", "issued_on": "2026-10-10", "status": "sent",
                                                               "lines": [{"what": "Logo design", "quantity": 2, "price": 500}]}).json()
    assert inv["amount"] == 1000.0 and inv["number"] == "2026-001" and inv["candidates"][0]["id"] == ids["deposit"]
    printed = owner.get(f"/api/v1/business/invoices/{inv['id']}/print").text
    assert "&lt;b&gt;Example Friend&lt;/b&gt;" in printed and "<b>Example Friend</b>" not in printed
    owner.post(f"/api/v1/business/invoices/{inv['id']}/paid", json={"transaction_id": ids["deposit"]})
    owner.post("/api/v1/business/mark", json={"ids": [ids["claude"]], "business_id": bid, "tax_line": "software"})
    owner.post("/api/v1/business/mark", json={"ids": [ids["phone"]], "business_id": bid, "tax_line": "utilities", "share": 0.5})
    b = owner.get(f"/api/v1/business/{bid}/books?year=2026").json()
    assert b["income"] == 1000.0 and b["costs"] == 62.5 and b["profit"] == 937.5
    assert b["se_tax"] == round(937.5 * 0.9235 * 0.153, 2)
    assert {r["key"]: r["amount"] for r in b["lines"]} == {"software": 20.0, "utilities": 42.5}
    assert b["payers"] == [{"payer": "<b>Example Friend</b>", "paid": 1000.0, "expect_1099": False}]  # under $2,000 in 2026
    assert owner.delete(f"/api/v1/business/invoices/{inv['id']}").status_code == 409  # a paid invoice is kept
    reader = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    anon = TestClient(client.app)
    assert anon.get(f"/api/v1/business/{bid}/books", headers={"Authorization": f"Bearer {reader}"}).status_code == 200
    assert anon.post("/api/v1/business", headers={"Authorization": f"Bearer {reader}"}, json={"name": "x"}).status_code == 403


def test_miles_count_at_the_rate_for_the_day_and_a_commute_never_does(owner: TestClient):
    bid = owner.post("/api/v1/business", json={"name": "Example Studio"}).json()["businesses"][0]["id"]
    owner.post(f"/api/v1/business/{bid}/trips", json={"day": "2026-03-02", "miles": 10, "purpose": "Client meeting at their office"})
    out = owner.post(f"/api/v1/business/{bid}/trips", json={"day": "2026-07-02", "miles": 30, "purpose": "Delivering prints to a client"}).json()
    assert [t["cents"] for t in out["trips"]] == [72.5, 76.0] and out["amount"] == round(10 * 0.725 + 30 * 0.76, 2)
    assert owner.post(f"/api/v1/business/{bid}/trips", json={"day": "2026-07-03", "miles": 12, "purpose": "Commute to the office"}).status_code == 400
    assert owner.post(f"/api/v1/business/{bid}/trips", json={"day": "2027-01-03", "miles": 12, "purpose": "A trip not yet taken"}).status_code == 400
    assert owner.get(f"/api/v1/business/{bid}/books?year=2026").json()["lines"][0]["key"] == "car"


def test_the_tax_center_lays_out_the_year_and_asks_about_the_car(owner: TestClient):
    t = owner.get("/api/v1/taxes/2026").json()
    assert t["standard_deduction"] == 16100.0 and t["amend"]["years"] == [2023, 2024, 2025]
    car = t["checks"][0]
    assert car["state"] == "ask" and len(car["questions"]) == 4
    out = owner.post("/api/v1/taxes/answers", json={"answers": {"car_new": True, "car_after_2024": True, "car_us_assembly": True, "car_personal": True}}).json()
    assert out["checks"][0]["state"] == "likely" and "$10,000" in out["checks"][0]["words"]
    out = owner.post("/api/v1/taxes/answers", json={"answers": {"car_new": False}}).json()
    assert out["checks"][0]["state"] == "no"
    assert owner.post("/api/v1/taxes/answers", json={"answers": {"ssn": "x"}}).status_code == 400
    assert "Not tax advice" in t["not_advice"]


def test_a_company_that_only_shares_a_word_with_a_provider_is_not_suggested():
    from app.engine.business import FINDER

    utilities = next(p for k, _, p in FINDER if k == "utilities")
    assert utilities.search("SPECTRUM INTERNET 123") and utilities.search("Comcast") and not utilities.search("SPECTRUM LABORATORIES")
