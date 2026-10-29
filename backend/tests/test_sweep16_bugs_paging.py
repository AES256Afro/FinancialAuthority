"""Round twelve: paging for the transaction search and the list of recurring charges waiting for the owner."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import services
from app.db import engine
from app.models import Account, RecurringSeries, Transaction


def _ledger(n: int, same_day: int = 0) -> None:
    """n spending rows on distinct days, plus `same_day` more on one shared day so the id has to break the tie."""
    with Session(engine) as session:
        acct = Account(name="Example Checking", institution="Example Bank", kind="checking", balance=1000.0, connector="manual")
        session.add(acct)
        session.commit()
        session.refresh(acct)
        for i in range(n):
            session.add(Transaction(account_id=acct.id, date=date(2026, 10, 1) - timedelta(days=i), merchant=f"Shop {i:03d}", raw_name=f"SHOP {i}", amount=10.0 + i, category="Shopping", row_hash=f"shop-{i}"))
        for i in range(same_day):
            session.add(Transaction(account_id=acct.id, date=date(2026, 10, 5), merchant=f"Tie {i:03d}", raw_name=f"TIE {i}", amount=1.0, category="Shopping", row_hash=f"tie-{i}"))
        session.add(Transaction(account_id=acct.id, date=date(2026, 10, 6), merchant="Payroll", raw_name="PAYROLL", amount=-900.0, category="Income", is_income=True, row_hash="pay"))
        session.commit()


def test_search_pages_with_offset_and_reports_the_total(owner: TestClient):
    _ledger(30, same_day=7)
    first = owner.get("/api/v1/transactions/search", params={"limit": 10}).json()
    assert first["total"] == 37 and first["returned"] == 10 and first["offset"] == 0 and first["limit"] == 10
    seen = [t["id"] for t in first["transactions"]]
    offset = 10
    while offset < first["total"]:
        page = owner.get("/api/v1/transactions/search", params={"limit": 10, "offset": offset}).json()
        assert page["total"] == 37 and page["offset"] == offset
        seen += [t["id"] for t in page["transactions"]]
        offset += 10
    assert len(seen) == 37 and len(set(seen)) == 37  # no row repeated or skipped across pages
    whole = owner.get("/api/v1/transactions/search", params={"limit": 500}).json()
    assert [t["id"] for t in whole["transactions"]] == seen  # pages concatenate to the one-shot order
    keys = [(t["date"], t["id"]) for t in whole["transactions"]]
    assert keys == sorted(keys, reverse=True)  # newest first, the id breaking a tie on one day
    past = owner.get("/api/v1/transactions/search", params={"offset": 1000}).json()
    assert past["returned"] == 0 and past["transactions"] == [] and past["total"] == 37


def test_search_total_counts_the_filter_not_the_page(owner: TestClient):
    _ledger(30, same_day=7)
    tied = owner.get("/api/v1/transactions/search", params={"text": "Tie", "limit": 3}).json()
    assert tied["total"] == 7 and tied["returned"] == 3
    everything = owner.get("/api/v1/transactions/search", params={"include_non_spending": True, "limit": 1}).json()
    assert everything["total"] == 38  # the paycheck counts once non-spending rows are asked for
    bounded = owner.get("/api/v1/transactions/search", params={"start": "2026-09-27", "end": "2026-10-01"}).json()
    assert bounded["total"] == 5 == bounded["returned"]


def test_search_rejects_a_negative_offset(owner: TestClient):
    assert owner.get("/api/v1/transactions/search", params={"offset": -1}).status_code == 422


def test_the_service_clamps_offset_for_the_model_door():
    _ledger(5)
    with Session(engine) as session:
        assert services.search_transactions(session, offset=-5)["offset"] == 0
        out = services.search_transactions(session, limit=2, offset=4)
        assert out["returned"] == 1 and out["total"] == 5
        assert services.search_transactions(session)["total"] == 5  # a caller that does not page still gets the count


def _pending(n: int, confirmed: int = 0) -> None:
    with Session(engine) as session:
        for i in range(n + confirmed):
            # Every third one shares an amount so the order needs the id to be stable.
            amount = 50.0 if i % 3 == 0 else 10.0 + i
            session.add(RecurringSeries(merchant_key=f"example {i}", merchant=f"Example {i}", category="Bills & Utilities", cadence="monthly", typical_amount=amount, latest_amount=amount,
                                        charges_seen=4, first_seen=date(2026, 6, 1), last_date=date(2026, 10, 1), next_expected=date(2026, 11, 1), active=True, confirmed=i >= n, kind="bill" if i >= n else None))
        session.commit()


def test_recurring_pages_the_pending_list_and_keeps_the_full_count(owner: TestClient):
    _pending(25, confirmed=4)
    first = owner.get("/api/v1/recurring", params={"limit": 10}).json()
    assert first["pending"] == 25 and first["total"] == 25 and len(first["series"]) == 10 and first["offset"] == 0 and first["limit"] == 10
    ids = [s["id"] for s in first["series"]]
    for offset in (10, 20):
        page = owner.get("/api/v1/recurring", params={"limit": 10, "offset": offset}).json()
        assert page["pending"] == 25
        ids += [s["id"] for s in page["series"]]
    assert len(ids) == 25 and len(set(ids)) == 25
    whole = owner.get("/api/v1/recurring").json()
    assert [s["id"] for s in whole["series"]] == ids
    keys = [(-s["typical_amount"], s["id"]) for s in whole["series"]]
    assert keys == sorted(keys)  # largest first, the id breaking ties
    assert all(not s["confirmed"] for s in whole["series"])


def test_recurring_with_classified_pages_every_series_but_counts_pending(owner: TestClient):
    _pending(5, confirmed=4)
    out = owner.get("/api/v1/recurring", params={"include_classified": True, "limit": 3, "offset": 6}).json()
    assert out["total"] == 9 and out["pending"] == 5 and len(out["series"]) == 3
    assert owner.get("/api/v1/recurring", params={"limit": 501}).status_code == 422
    assert owner.get("/api/v1/recurring", params={"offset": -1}).status_code == 422
