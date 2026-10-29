"""The fifth bug sweep, planning modules (business and taxes, reports, budgets, housing and the move): each finding
held by a test on rows made here, with the synthetic household's clock."""

import json
from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine import housing as hs
from app.engine.budgets import budget_plan
from app.engine.business import books
from app.engine.custom_reports import run_report, tax_totals
from app.engine.moving import moving_plan
from app.engine.taxcenter import tax_year
from app.models import Account, Business, CategoryBudget, Home, HomeCost, Transaction

TODAY = date(2026, 10, 14)


def _checking(session: Session) -> Account:
    account = Account(name="Checking", institution="Example Bank", kind="checking", balance=1500.0, connector="manual", external_id="manual:checking")
    session.add(account)
    session.flush()
    return account


def _tx(session: Session, account: Account, when: date, merchant: str, amount: float, category: str = "Shopping", **fields) -> Transaction:
    row = Transaction(account_id=account.id, date=when, merchant=merchant, raw_name=merchant.upper(), amount=amount, category=category,
                      row_hash=f"s5b-{account.id}-{when}-{merchant}-{amount}", **fields)
    session.add(row)
    session.flush()
    return row


# ---- business and taxes ---------------------------------------------------------------------------------------

def test_self_employment_tax_floor_is_on_the_taxed_share_of_profit():
    """Schedule SE taxes 92.35% of the profit, and applies only when that share is $400 or more: a $420 profit is
    $387.87 of net earnings, under the floor, so no self-employment tax; $440 is over it."""
    with Session(engine) as session:
        account = _checking(session)
        biz = Business(name="Side work")
        session.add(biz)
        session.flush()
        _tx(session, account, date(2026, 6, 1), "A client", -420.0, "Income", business_id=biz.id)
        session.commit()
        under = books(session, biz, 2026)
        assert under["profit"] == 420.0
        assert under["se_tax"] == 0.0
        _tx(session, account, date(2026, 7, 1), "A client", -20.0, "Income", business_id=biz.id)
        session.commit()
        over = books(session, biz, 2026)
        assert over["profit"] == 440.0
        assert over["se_tax"] == round(440.0 * 0.9235 * 0.153, 2)


def test_standard_deduction_is_not_borrowed_from_another_year():
    """A year the table does not have shows no standard deduction, never another year's figure under its name."""
    with Session(engine) as session:
        _checking(session)
        session.commit()
        assert tax_year(session, TODAY, 2026)["standard_deduction"] == 16100.0
        assert tax_year(session, TODAY, 2024)["standard_deduction"] is None


def test_tax_year_ahead_of_today_is_empty_not_a_crash():
    """The tax route allows next year; its totals are empty, not an error."""
    with Session(engine) as session:
        _checking(session)
        session.commit()
        out = tax_year(session, TODAY, TODAY.year + 1)
        assert out["year"] == TODAY.year + 1 and out["itemizable"] == 0.0 and out["itemizable_categories"] == []


# ---- reports and budgets --------------------------------------------------------------------------------------

def test_a_tagged_purchase_counts_whole_under_each_tag():
    """A purchase with two tags belongs to both in full: the "tax" total is the whole purchase, and a tag report's
    total counts each purchase once."""
    with Session(engine) as session:
        account = _checking(session)
        _tx(session, account, date(2026, 3, 3), "A pharmacy", 100.0, "Medical", tags="tax,work")
        _tx(session, account, date(2026, 3, 4), "A clinic", 50.0, "Medical", tags="tax")
        session.commit()
        tagged = tax_totals(session, TODAY, 2026)["tagged_tax"]
        assert tagged["total"] == 150.0 and tagged["count"] == 2
        report = run_report(session, TODAY, rows="tag", period="year")
        by_name = {i["name"]: i["total"] for i in report["items"]}
        assert by_name == {"tax": 150.0, "work": 100.0}
        assert report["total"] == 150.0


def test_a_refund_gives_the_budget_its_money_back():
    """A return in a budgeted category is spending that came back: the budget's spent nets it off, as the reports do."""
    with Session(engine) as session:
        account = _checking(session)
        session.add(CategoryBudget(category="Groceries", per_paycheck=200.0, rollover="none"))
        _tx(session, account, TODAY, "A grocer", 80.0, "Groceries")
        _tx(session, account, TODAY, "A grocer", -30.0, "Groceries")
        session.commit()
        plan = budget_plan(session, TODAY)
        item = next(i for i in plan["items"] if i["category"] == "Groceries")
        assert item["spent"] == 50.0 and item["left"] == 150.0
        report = run_report(session, TODAY, rows="category", period="month")
        assert next(i["total"] for i in report["items"] if i["name"] == "Groceries") == 50.0


# ---- housing and the move -------------------------------------------------------------------------------------

def test_refundable_in_costs_counts_a_deposit_once():
    """A move-in check itemised into parts: what comes back is the refundable parts, and ticking the check itself
    as refundable does not count them again. A cost with no parts follows its own tick."""
    with Session(engine) as session:
        home = Home(name="New place", starts_on=date(2026, 11, 17), due_day=1)
        session.add(home)
        session.flush()
        parts = json.dumps([{"name": "Deposit", "amount": 500.0, "refundable": True}, {"name": "First month", "amount": 1200.0, "refundable": False}])
        session.add(HomeCost(home_id=home.id, name="Move-in check", amount=1700.0, due_on=date(2026, 11, 10), refundable=True, parts=parts))
        session.add(HomeCost(home_id=home.id, name="Utility deposit", amount=150.0, due_on=date(2026, 11, 12), refundable=True))
        session.commit()
        hs.sync_bills(session, home, TODAY)
        out = hs.home_out(session, home, TODAY)
        assert out["costs_total"] == 1850.0
        assert out["refundable_in_costs"] == 650.0


def test_first_regular_payment_falls_on_the_due_day():
    """A home moved into on the 17th with rent due on the 1st: its bill first comes due on the 1st after moving in,
    and the first regular payment shown on Housing and in the moving plan is that day, not the move-in day."""
    with Session(engine) as session:
        _checking(session)
        home = Home(name="New place", starts_on=date(2026, 11, 17), due_day=1, payee="The property")
        session.add(home)
        session.flush()
        from app.models import HomeCharge

        session.add(HomeCharge(home_id=home.id, name="Rent", kind="rent", amount=1200.0))
        session.commit()
        hs.sync_bills(session, home, TODAY)
        out = hs.home_out(session, home, TODAY)
        assert out["next_due"] == "2026-12-01"
        assert out["first_due"] == "2026-12-01"
        assert out["first_due_entered"] is None
        step = next(s for s in moving_plan(session, TODAY)["steps"] if s["id"] == "first-rent")
        assert step["date"] == "2026-12-01"
        assert "on the 1st" in step["words"]
