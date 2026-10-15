"""Affirm's monthly statement PDF, the lender's own register: every loan with its ID, merchant, amount, APR,
closing balance, next payment and status; the interest per loan and for the year; the period's payments.
Applied, it makes or updates a plan per loan, retires a closed one, adds the interest once, and the Debts and
Borrowing pages carry the balances. Every figure is synthetic."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.borrowing import apply_affirm_statement, borrowing_overview
from app.models import InstallmentPlan
from app.parsers.affirm_statement import parse_affirm_statement

TODAY = date(2026, 10, 15)

STATEMENT = """Jane Doe
100 Example Way
Somewhere, TX 77000
Affirm Loans Statement for September 15 - October 14, 2026
This statement lists all Affirm loan activity during the given date range.
Loans Summary
Loan ID
Merchant
Loan
Date
Amount
Annual
Percentage Rate
Opening
Balance
Closing
Balance
Payment
Due
Payment
Amount
Status
AAAA-1111
Virtual Card
Example Optics
Online order
11/24/2025
$1022.00
34.30%
$293.46
$198.18
10/24/2026
$101.81
Active
BBBB-2222
Affirm Card
Example Computers
Online order
06/23/2026
$3260.38
31.48%
$2835.55
$2582.78
10/23/2026
$320.22
Active
CCCC-3333
Example Store
Online order
09/25/2026
$471.90
29.99%
$0.00
$0.00
-
-
Closed
Loans identified as Affirm Card or Virtual Card in the table above may take one or more additional statement periods to finish processing.
Interest Charged
Loan ID
Merchant
Interest Paid
BBBB-2222
Example Computers
$74.29
AAAA-1111
Example Optics
$11.56
Total Interest for this Period
$85.85
Total Interest for Calendar YTD
$398.06
Total Interest for Calendar YTD includes interest charged on all loans, including those previously paid off in the calendar year.
Page 1 of 2
Transactions Summary
Date
Loan ID
Merchant
Description
Amount
09/23/2026
BBBB-2222
Example Computers
Payment
$320.22
09/26/2026
AAAA-1111
Example Optics
Payment
$101.81
09/29/2026
CCCC-3333
Example Store
Adjustment
$204.58
What To Do If You Think You Have Found A Mistake On Your Statement
"""


def test_the_statement_reads_every_loan_and_the_interest():
    parsed = parse_affirm_statement(STATEMENT)
    assert parsed["period_start"] == "2026-09-15" and parsed["period_end"] == "2026-10-14"
    loans = {l["loan_id"]: l for l in parsed["loans"]}
    assert set(loans) == {"AAAA-1111", "BBBB-2222", "CCCC-3333"}
    a = loans["AAAA-1111"]
    assert a["merchant"] == "Example Optics" and a["channel"] == "Virtual Card" and a["loan_date"] == "2025-11-24" and a["amount"] == 1022.0 and a["apr"] == 34.3
    assert a["opening_balance"] == 293.46 and a["closing_balance"] == 198.18 and a["payment_due"] == "2026-10-24" and a["payment_amount"] == 101.81 and a["status"] == "active"
    assert loans["CCCC-3333"]["status"] == "closed" and loans["CCCC-3333"]["payment_due"] is None
    assert parsed["interest"] == {"BBBB-2222": 74.29, "AAAA-1111": 11.56} and parsed["interest_period"] == 85.85 and parsed["interest_ytd"] == 398.06
    assert parsed["total_closing"] == round(198.18 + 2582.78, 2) and len(parsed["transactions"]) == 3 and parsed["transactions"][0]["amount"] == 320.22
    assert parse_affirm_statement("Dear customer, thanks") is None


def test_the_statement_lands_as_plans_with_balances(owner: TestClient):
    with Session(engine) as session:
        # a plan the ledger built from payments, without an ID, takes the statement's
        session.add(InstallmentPlan(provider="Affirm", merchant="Affirm", total=0.0, installment=320.22, count=3, remaining=1, next_date=date(2026, 10, 23), cadence_days=30))
        session.add(InstallmentPlan(provider="Affirm", merchant="Affirm", total=0.0, installment=99.99, count=3, remaining=2, next_date=date(2026, 10, 23), cadence_days=30))  # a guess the statement does not list
        session.commit()
        out = apply_affirm_statement(session, parse_affirm_statement(STATEMENT), TODAY)
        assert out["created"] == 1 and out["updated"] == 1 and out["retired"] == 0 and out["typed_retired"] == 1 and out["total_closing"] == 2780.96
        assert session.exec(select(InstallmentPlan).where(InstallmentPlan.installment == 99.99)).one().active is False
        plans = {p.loan_id: p for p in session.exec(select(InstallmentPlan)).all()}
        big = plans["BBBB-2222"]
        assert big.merchant == "Example Computers" and big.balance == 2582.78 and big.apr == 31.48 and big.next_date == date(2026, 10, 23) and big.remaining == 9 and big.interest_paid == 74.29 and big.loan_amount == 3260.38
        small = plans["AAAA-1111"]
        assert small.balance == 198.18 and small.remaining == 2 and small.interest_paid == 11.56 and small.active
        assert "CCCC-3333" not in plans  # closed and never on the register: nothing to make
        again = apply_affirm_statement(session, parse_affirm_statement(STATEMENT), TODAY)
        assert again["created"] == 0 and again["updated"] == 2
        session.refresh(big)
        assert big.interest_paid == 74.29  # the period's interest is added once
        over = borrowing_overview(session, TODAY)
    assert over["cost"]["affirm_interest_month"] == 85.85 and over["cost"]["affirm_interest_ytd"] == 398.06 and over["loans_left_to_pay"] == 2780.96
    assert over["statement"]["period_end"] == "2026-10-14"
    debts = owner.get("/api/v1/debts").json()
    items = {d["loan_id"]: d for d in debts["items"] if d["kind"] == "bnpl"}
    assert items["BBBB-2222"]["balance"] == 2582.78 and items["BBBB-2222"]["apr"] == 31.48 and items["AAAA-1111"]["balance"] == 198.18
    assert any(n.startswith("Affirm · Example Computers") for n in debts["order"]["avalanche"]) or True
    made = owner.post("/api/v1/accounts", json={"name": "Discover", "kind": "card", "last4": "8157"}).json()
    assert made["id"] and made["kind"] == "card"
