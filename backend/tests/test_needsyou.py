"""M44: Needs you that empties: alike items answered together (only the complete ones confirmed, the rest kept with a
reason), and each bill with no payment seen asked plainly: is it paid? Also the round-three handoffs: a removed
document's file kept while a restore point names it, a card's terms cleared, money between people removed."""

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import restore
from app.db import engine
from app.engine import documents as docs
from app.models import Account, Bill, Debt, Document, ExtractedFact, Payment, Transaction


def _facts(session: Session) -> list[int]:
    rows = [
        ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Water", "amount": 40.0, "due_date": "2026-10-25"}), status="pending"),
        ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Internet", "amount": 60.0, "due_date": "2026-10-28"}), status="pending"),
        ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Gym"}), status="pending"),  # no amount: needs its own card
        ExtractedFact(kind="export", fields=json.dumps({"url": "https://example.test/x"}), status="pending"),
    ]
    session.add_all(rows)
    session.commit()
    return [r.id for r in rows]


def test_alike_items_are_grouped_and_only_complete_ones_confirm_together(owner: TestClient):
    with Session(engine) as session:
        ids = _facts(session)
    tri = owner.get("/api/v1/needs-you").json()
    groups = {g["kind"]: g for g in tri["groups"]}
    assert groups["bill_due"]["count"] == 3 and groups["bill_due"]["complete"] == 2 and groups["export"]["one_at_a_time"] is True
    assert tri["groups"][-1]["kind"] == "export"  # one-at-a-time groups come last
    out = owner.post("/api/v1/needs-you/facts", json={"ids": ids[:3], "action": "confirm"}).json()
    assert len(out["done"]) == 2 and out["failed"] == [{"id": ids[2], "error": "needs your answer on its own card"}]
    assert "still wait" in out["said"]
    with Session(engine) as session:
        assert {b.name for b in session.exec(select(Bill)).all()} >= {"Example Water", "Example Internet"}
    out = owner.post("/api/v1/needs-you/facts", json={"ids": [ids[2], ids[3]], "action": "reject"}).json()
    assert len(out["done"]) == 2 and out["triage"]["total"] == 0 and out["triage"]["word"] == "Nothing waits for you."


def test_a_bill_with_no_payment_seen_is_asked_is_it_paid(owner: TestClient):
    with Session(engine) as session:
        acct = Account(name="Example Checking", kind="checking")
        session.add(acct)
        session.flush()
        bill = Bill(name="Example Power", amount=80.0, due_day=3)
        session.add(bill)
        session.flush()
        late = Payment(bill_id=bill.id, due_date=date(2026, 10, 3), amount=80.0, status="past_due")
        older = Payment(bill_id=bill.id, due_date=date(2026, 9, 3), amount=80.0, status="past_due")
        session.add_all([late, older])
        session.add(Transaction(account_id=acct.id, date=date(2026, 10, 6), merchant="EXAMPLE PWR AUTOPAY", amount=81.5, row_hash="ny-1"))
        session.commit()
        late_id, older_id, bill_id = late.id, older.id, bill.id
    tri = owner.get("/api/v1/needs-you").json()
    item = next(p for p in tri["past_due"] if p["payment_id"] == late_id)
    assert item["candidates"][0]["merchant"] == "EXAMPLE PWR AUTOPAY"
    out = owner.post(f"/api/v1/needs-you/payments/{late_id}", json={"answer": "paid", "transaction_id": item["candidates"][0]["id"]}).json()
    assert "marked paid with the EXAMPLE PWR AUTOPAY payment on Tue Oct 6" in out["said"]
    out = owner.post(f"/api/v1/needs-you/payments/{older_id}", json={"answer": "stopped"}).json()
    assert "no longer counted from Thu Sep 3" in out["said"]
    with Session(engine) as session:
        assert session.get(Payment, late_id).status == "paid" and session.get(Bill, bill_id).ends_on == date(2026, 9, 2)
    assert owner.post(f"/api/v1/needs-you/payments/{late_id}", json={"answer": "maybe"}).status_code == 400


def test_a_removed_documents_file_stays_while_a_restore_point_names_it(owner: TestClient):
    with Session(engine) as session:
        doc = docs.save_document(session, b"Example lease text", filename="lease.txt", kind="lease")
        sha = doc.sha256
    restore.make_point("manual", "with the lease")
    with Session(engine) as session:
        docs.remove_document(session, session.get(Document, doc.id))
    assert (docs.folder() / f"{sha}.bin").exists()  # a restore can still bring it back


def test_a_cards_terms_can_be_cleared_and_money_between_people_removed(owner: TestClient):
    with Session(engine) as session:
        d = Debt(name="Example Card", kind="card", balance=300.0, apr=24.0, minimum=35.0, statement_day=5)
        session.add(d)
        session.commit()
        did = d.id
    assert owner.put(f"/api/v1/debts/{did}", json={"apr": None, "statement_day": None}).status_code == 200
    with Session(engine) as session:
        d = session.get(Debt, did)
        assert d.apr == 0.0 and d.statement_day is None and d.minimum == 35.0
    owner.post("/api/v1/debts/payoff/people", json={"person": "Example Friend", "direction": "lent", "amount": 50})
    assert owner.delete("/api/v1/debts/payoff/people/0").json()["people"] == []
    assert owner.delete("/api/v1/debts/payoff/people/0").status_code == 404
