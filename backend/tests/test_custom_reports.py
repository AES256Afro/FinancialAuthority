"""M32: reports the owner builds, a year in review, and tax-time totals."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine import custom_reports as cr
from app.models import Account, Transaction

TODAY = date(2026, 10, 14)


def _rows(session: Session) -> None:
    chk = Account(name="Checking", kind="checking", connector="plaid", external_id="r-chk", balance=900.0)
    session.add(chk)
    session.flush()
    data = [(date(2026, 9, 3), 60.0, "Groceries", "Example Grocer", ""), (date(2026, 10, 2), 40.0, "Dining & Drinks", "Example Cafe", ""),
            (date(2026, 10, 5), 120.0, "Medical", "Example Clinic", "tax"), (date(2026, 10, 6), 30.0, "Groceries", "Example Grocer", ""),
            (date(2026, 10, 1), -2000.0, "Income", "Example Employer", "")]
    for i, (when, amount, category, merchant, tags) in enumerate(data):
        session.add(Transaction(account_id=chk.id, date=when, merchant=merchant, amount=amount, category=category, tags=tags, is_income=amount < 0, source="plaid", row_hash=f"r-{i}"))
    session.commit()


def test_a_built_report_sums_by_what_was_chosen_and_groups_categories():
    with Session(engine) as session:
        _rows(session)
        month = cr.run_report(session, TODAY, rows="category", period="month")
        assert {i["name"]: i["total"] for i in month["items"]} == {"Medical": 120.0, "Dining & Drinks": 40.0, "Groceries": 30.0} and month["total"] == 190.0
        cr.save_groups(session, {"Food": ["Groceries", "Dining & Drinks"]})
        grouped = cr.run_report(session, TODAY, rows="group", period="last_90")
        assert {i["name"]: i["total"] for i in grouped["items"]} == {"Medical": 120.0, "Food": 130.0}
        by_tag = cr.run_report(session, TODAY, rows="tag", period="month", only=["tax"])
        assert by_tag["items"][0]["total"] == 120.0
        saved = cr.save_report(session, {"name": "Food this quarter", "rows": "group", "period": "quarter", "picture": "line"})
        assert saved["picture"] == "line" and [r["name"] for r in cr.saved(session)] == ["Food this quarter"]


def test_a_year_in_review_and_tax_totals():
    with Session(engine) as session:
        _rows(session)
        review = cr.year_in_review(session, TODAY, 2026)
        assert review["spent"] == 250.0 and review["income"] == 2000.0 and review["kept"] == 1750.0 and review["busiest_month"] == "2026-10"
        tax = cr.tax_totals(session, TODAY, 2026)
        assert [c["name"] for c in tax["categories"]] == ["Medical"] and tax["tagged_tax"]["total"] == 120.0
