"""M30: tags, splits, notes, many at once, and a rule tried before it is kept."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.models import Account, Transaction


def _rows() -> list[int]:
    with Session(engine) as session:
        chk = Account(name="Checking", kind="checking", connector="plaid", external_id="a-chk", balance=500.0)
        session.add(chk)
        session.flush()
        ids = []
        for i, (merchant, amount, category) in enumerate([("Big Box Store", 120.0, "Shopping"), ("Example Cafe", 8.5, "Dining & Drinks"), ("Example Cafe", 6.0, "Dining & Drinks")]):
            t = Transaction(account_id=chk.id, date=date(2026, 10, 3 + i), merchant=merchant, amount=amount, category=category, source="plaid", row_hash=f"e-{i}")
            session.add(t)
            session.flush()
            ids.append(t.id)
        session.commit()
        return ids


def test_a_split_must_add_up_and_reports_count_its_parts(owner: TestClient):
    big, *_ = _rows()
    assert owner.put(f"/api/v1/transactions/{big}/split", json={"parts": [{"category": "Groceries", "amount": 70}, {"category": "Household", "amount": 40}]}).status_code == 400  # 110, not 120
    assert owner.put(f"/api/v1/transactions/{big}/split", json={"parts": [{"category": "Groceries", "amount": 120}]}).status_code == 400  # one part is not a split
    ok = owner.put(f"/api/v1/transactions/{big}/split", json={"parts": [{"category": "Groceries", "amount": 80}, {"category": "Household", "amount": 40, "note": "towels"}]}).json()
    assert [p["category"] for p in ok["parts"]] == ["Groceries", "Household"]
    report = owner.get("/api/v1/reports/spending", params={"by": "category"}).json()
    names = {g["name"]: g["total"] for g in report["groups"]}
    assert names.get("Groceries") == 80.0 and names.get("Household") == 40.0 and "Shopping" not in names
    found = owner.get("/api/v1/transactions/search", params={"text": "Big Box"}).json()["transactions"][0]
    assert len(found["splits"]) == 2
    assert owner.put(f"/api/v1/transactions/{big}/split", json={"parts": []}).json()["parts"] == []  # and it can be undone


def test_tags_notes_and_many_at_once(owner: TestClient):
    big, cafe1, cafe2 = _rows()
    edited = owner.patch(f"/api/v1/transactions/{big}", json={"note": "Birthday present", "tags": ["Gift", "gift", "  family "]}).json()
    assert edited["tags"] == ["gift", "family"] and edited["note"] == "Birthday present"
    assert owner.patch(f"/api/v1/transactions/{big}", json={"category": "  "}).status_code == 400
    assert owner.patch("/api/v1/transactions/99999", json={"note": "x"}).status_code == 404
    changed = owner.post("/api/v1/transactions/bulk", json={"ids": [cafe1, cafe2], "category": "Coffee", "tags_add": ["work"]}).json()
    assert changed == {"changed": 2}
    tagged = owner.get("/api/v1/transactions/search", params={"tag": "work"}).json()["transactions"]
    assert {t["id"] for t in tagged} == {cafe1, cafe2} and all(t["category"] == "Coffee" for t in tagged)
    owner.post("/api/v1/transactions/bulk", json={"ids": [cafe1], "tags_remove": ["work"]})
    assert {t["id"] for t in owner.get("/api/v1/transactions/search", params={"tag": "work"}).json()["transactions"]} == {cafe2}


def test_a_rule_is_tried_before_it_is_kept(owner: TestClient):
    _rows()
    preview = owner.post("/api/v1/ledger/rules/preview", json={"kind": "categorise", "pattern": "cafe", "value": "Coffee"}).json()
    assert preview["count"] == 2 and preview["sample"][0]["becomes"] == {"category": "Coffee"}
    with Session(engine) as session:
        assert all(t.category == "Dining & Drinks" for t in session.query(Transaction).filter(Transaction.merchant == "Example Cafe"))  # nothing changed
    assert owner.post("/api/v1/ledger/rules/preview", json={"kind": "explode", "pattern": "x"}).status_code == 400
