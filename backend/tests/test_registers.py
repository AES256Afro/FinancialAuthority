from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import seed
from app.jobs.detect import pending_series, run_detection
from app.models import Bill, Income, RecurringSeries

TODAY = date(2026, 10, 14)


def test_detection_links_known_bills_and_income():
    with Session(engine) as session:
        seed(session)
        result = run_detection(session, TODAY)
        assert result["found"] >= 10 and result["created"] == result["found"]
        series = session.exec(select(RecurringSeries)).all()
        by_name = {s.merchant: s for s in series}
        assert by_name["Rent"].confirmed and by_name["Rent"].kind == "bill"
        assert by_name["Phone"].cadence == "monthly" and by_name["Phone"].typical_amount == 96.0
        payroll = by_name["Example Employer Payroll"]
        assert payroll.kind == "income" and payroll.confirmed and payroll.cadence == "biweekly"
        unknown = [s for s in series if not s.confirmed]
        assert {s.merchant for s in unknown} == {"Example Cloud Storage", "Example Storage Unit"}
        rent = session.exec(select(Bill).where(Bill.name == "Rent")).one()
        assert rent.series_id == by_name["Rent"].id
        income = session.exec(select(Income)).one()
        assert income.series_id == payroll.id
        # Running again changes nothing but the counters.
        again = run_detection(session, TODAY)
        assert again["created"] == 0 and again["updated"] == result["found"]


def test_classify_unknown_series_into_a_bill(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        # An unknown recurring charge the household does not have a bill for yet.
        unknown = RecurringSeries(merchant_key="example cloud", merchant="Example Cloud", category="Bills & Utilities", cadence="monthly", typical_amount=12.0, latest_amount=12.0,
                                  charges_seen=6, first_seen=date(2026, 4, 9), last_date=date(2026, 10, 9), next_expected=date(2026, 11, 8), active=True)
        session.add(unknown)
        session.commit()
        session.refresh(unknown)
        assert len(pending_series(session)) == 1
        unknown_id = unknown.id
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["pending"]["confirmations"] == 1
    listed = owner.get("/api/v1/recurring").json()
    assert listed["pending"] == 1 and listed["series"][0]["merchant"] == "Example Cloud"
    done = owner.post(f"/api/v1/recurring/{unknown_id}/classify", json={"kind": "subscription", "due_day": 8, "autopay": True}).json()
    assert done["series"]["confirmed"] and done["bill"]["kind"] == "subscription" and done["bill"]["due_day"] == 8 and done["pending"] == 0
    bills = owner.get("/api/v1/bills").json()["bills"]
    assert any(b["name"] == "Example Cloud" and b["amount"] == 12.0 for b in bills)
    assert owner.get("/api/v1/snapshot").json()["pending"]["confirmations"] == 0
    # The two unknown charges in the fixture show up once detection runs, and classifying them clears the count.
    detected = owner.post("/api/v1/recurring/detect").json()
    assert detected["created"] >= 2
    pending = owner.get("/api/v1/recurring").json()
    assert pending["pending"] == 2
    for s in pending["series"]:
        kind = "subscription" if s["typical_amount"] < 10 else "bill"
        owner.post(f"/api/v1/recurring/{s['id']}/classify", json={"kind": kind})
    assert owner.get("/api/v1/recurring").json()["pending"] == 0


def test_bill_update_clamps_due_day(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        rent_id = session.exec(select(Bill).where(Bill.name == "Rent")).one().id
    changed = owner.put(f"/api/v1/bills/{rent_id}", json={"due_day": 31, "movable": False}).json()
    assert changed["due_day"] == 28 and changed["movable"] is False
    assert owner.put(f"/api/v1/bills/{rent_id}", json={"kind": "loan"}).status_code == 400
