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
from app.models import Account, Advance, Bill, Debt, Document, ExtractedFact, Home, InstallmentPlan, Payment, Transaction


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
    assert len(out["done"]) == 2 and out["triage"]["total"] == 0 and out["triage"]["word"] == "Nothing from your mail, documents or bills waits."


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
    assert tri["word"] == "2 things wait here: 2 bills with no payment seen."
    item = next(p for p in tri["past_due"] if p["payment_id"] == late_id)
    assert item["candidates"][0]["merchant"] == "EXAMPLE PWR AUTOPAY"
    out = owner.post(f"/api/v1/needs-you/payments/{late_id}", json={"answer": "paid", "transaction_id": item["candidates"][0]["id"]}).json()
    assert "marked paid with the EXAMPLE PWR AUTOPAY payment on Tue Oct 6" in out["said"]
    out = owner.post(f"/api/v1/needs-you/payments/{older_id}", json={"answer": "stopped"}).json()
    assert "no longer counted from Thu Sep 3" in out["said"]
    with Session(engine) as session:
        assert session.get(Payment, late_id).status == "paid" and session.get(Bill, bill_id).ends_on == date(2026, 9, 2)
    assert owner.post(f"/api/v1/needs-you/payments/{late_id}", json={"answer": "maybe"}).status_code == 409  # answered already: nothing changes (sweep 14)
    assert owner.post(f"/api/v1/needs-you/payments/{older_id}", json={"answer": "paid", "paid_on": "2099-01-01"}).status_code == 409  # answered already (sweep 14)


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


def test_many_notices_for_one_bill_count_only_by_the_newest_in_any_order(owner: TestClient):
    """A mailbox read back over months holds many notices per bill. Confirming them together, in parts and in any
    order, leaves each bill with its newest notice's figures; an old notice for a bill not on file starts nothing."""
    with Session(engine) as session:
        session.add(Bill(name="Example Water", amount=40.0, due_day=25))
        notices = [
            ("Example Water", 44.0, "2026-10-25"), ("Example Water", 38.0, "2026-08-25"), ("Example Water", 41.0, "2026-09-25"),
            ("Example Internet", 60.0, "2026-10-28"), ("Example Old Gym", 30.0, "2026-06-01"),
        ]
        rows = [ExtractedFact(kind="bill_due", fields=json.dumps({"name": n, "amount": a, "due_date": d}), status="pending") for n, a, d in notices]
        session.add_all(rows)
        session.commit()
        newest, older_a, older_b, internet, gym = [r.id for r in rows]
    ids = [older_a, older_b, internet, gym, newest]
    preview = owner.post("/api/v1/needs-you/facts", json={"ids": ids, "action": "preview"}).json()
    assert preview["updates"] == ["Example Water"] and preview["adds"] == ["Example Internet"] and preview["set_aside"] == 3
    assert preview["said"] == "Confirming these 5 brings 1 bill up to date (Example Water), adds 1 new bill (Example Internet) and sets aside 3 older notices."
    with Session(engine) as session:
        assert session.get(ExtractedFact, newest).status == "pending"  # a preview changes nothing
    # The older notices go first, alone: still set aside, since the newest is judged against every notice held.
    first = owner.post("/api/v1/needs-you/facts", json={"ids": [older_a, older_b, gym], "action": "confirm"}).json()
    assert first["said"] == "Confirmed 0 and set aside 3 older notices."
    owner.post("/api/v1/needs-you/facts", json={"ids": [newest, internet], "action": "confirm"})
    with Session(engine) as session:
        bills = {b.name: b for b in session.exec(select(Bill)).all()}
        assert bills["Example Water"].amount == 44.0 and "Example Internet" in bills and "Example Old Gym" not in bills
        gone = session.get(ExtractedFact, gym)
        assert gone.status == "rejected" and gone.applied_to.startswith("set aside: over 60 days old")


def test_a_notice_from_someone_counted_another_way_never_becomes_a_second_bill(owner: TestClient):
    """The landlord on Housing, a card on Debts and an advance app on Borrowing are counted already; their notices
    wait on their own cards, which say where each is counted, and a Housing bill is changed only on Housing."""
    with Session(engine) as session:
        session.add(Home(name="Example Court 4", payee="Example Leasing Office"))
        session.add(Home(name="Example Tower 9", payee="Example at the Riverside"))
        session.add(Debt(name="Example Rewards Card", kind="card", balance=300.0, apr=24.0, minimum=35.0))
        session.add(Advance(provider="Example Advance App", amount=100.0, taken_on=date(2026, 10, 1), repay_date=date(2026, 10, 30)))
        session.add(Bill(name="Rent · Example Court 4", amount=1200.0, due_day=1, home_id=1, pays_to="Example Leasing Office"))
        rows = [ExtractedFact(kind="bill_due", fields=json.dumps({"name": n, "amount": 50.0, "due_date": "2026-10-28"}), status="pending")
                for n in ("Example Leasing Office", "Example Rewards Card", "Example Advance App", "Example Phone Co", "Example at Riverside Leasing")]
        session.add_all(rows)
        session.commit()
        ids = [r.id for r in rows]
    plan = owner.post("/api/v1/needs-you/facts", json={"ids": ids, "action": "preview"}).json()
    assert plan["adds"] == ["Example Phone Co"] and plan["own_card"] == 4
    facts = {f["id"]: f for f in owner.get("/api/v1/facts?status=pending").json()["facts"]}
    assert facts[ids[0]]["counted_elsewhere"] == "Rent · Example Court 4 is made from Housing, where the rent is changed"
    assert facts[ids[1]]["counted_elsewhere"] == "Example Rewards Card is counted on Debts"
    assert facts[ids[2]]["counted_elsewhere"] == "advances from Example Advance App are counted on Borrowing"
    assert facts[ids[3]].get("counted_elsewhere") is None
    assert facts[ids[4]]["counted_elsewhere"] == "the rent for Example Tower 9 is counted from Housing"  # "the" and "Leasing" tell nothing apart
    assert owner.post(f"/api/v1/facts/{ids[0]}/confirm", json={"apply": True}).status_code == 400
    with Session(engine) as session:
        assert session.exec(select(Bill).where(Bill.home_id == 1)).one().amount == 1200.0


def test_lender_emails_make_one_plan_per_loan_and_record_only_ones_change_nothing(owner: TestClient):
    """Pay-later emails with the lender's schedule set up one plan per loan however many arrive; one without a
    schedule waits on its card. Statement and advance emails change nothing in the plan: they are marked read."""
    sched = [{"date": "2026-11-01", "amount": 25.0}, {"date": "2026-12-01", "amount": 25.0}]
    def plan_mail(loan: str, schedule: list) -> ExtractedFact:
        return ExtractedFact(kind="installment", status="pending", fields=json.dumps({"provider": "Example Lender", "loan_id": loan, "schedule": schedule, "total": 50.0}))
    with Session(engine) as session:
        autopay = ExtractedFact(kind="installment", status="pending", fields=json.dumps({"amount": 25.0, "subject": "You've enabled AutoPay!"}))
        rows = [plan_mail("L-1", sched), plan_mail("L-1", sched), plan_mail("L-2", sched), plan_mail("", []), autopay,
                ExtractedFact(kind="advance", status="pending", fields=json.dumps({"amount": 100.0})),
                ExtractedFact(kind="statement", status="pending", fields=json.dumps({"amount": 300.0, "due_date": "2026-10-25"}))]
        session.add_all(rows)
        session.commit()
        ids = [r.id for r in rows]
    tri = owner.get("/api/v1/needs-you").json()
    assert not any(g["one_at_a_time"] for g in tri["groups"] if g["kind"] in ("installment", "advance"))
    plan = owner.post("/api/v1/needs-you/facts", json={"ids": ids, "action": "preview"}).json()
    assert plan["said"] == ("Confirming these 7 sets up or updates 2 pay-later plans with Example Lender, one per loan and marks 3 as read "
                            "(they change nothing in the plan). 1 stays for its own card.")
    owner.post("/api/v1/needs-you/facts", json={"ids": ids, "action": "confirm"})
    with Session(engine) as session:
        assert sorted(p.loan_id for p in session.exec(select(InstallmentPlan)).all()) == ["L-1", "L-2"]
        assert session.get(ExtractedFact, ids[3]).status == "pending"
        assert session.get(ExtractedFact, ids[4]).status == "confirmed" and session.get(ExtractedFact, ids[4]).applied_to.startswith("read")
