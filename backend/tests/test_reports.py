"""The reports: spending by month and group, net worth, the debt mountain. Golden checks on the fixture and
the invariants each must keep."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.reports import debt_mountain, net_worth, spending_summary

TODAY = date(2026, 10, 14)


def test_spending_summary_months_groups_and_rate():
    with Session(engine) as session:
        seed(session)
        out = spending_summary(session, TODAY, months=6, by="category")
        merchants = spending_summary(session, TODAY, months=3, by="merchant", redaction="aggregates")
    assert [m["month"] for m in out["months"]] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert out["months"][-1]["partial"] is True and all(not m["partial"] for m in out["months"][:-1])
    assert all(m["income"] >= 0 and m["spent"] >= 0 for m in out["months"])
    assert out["totals"]["spent"] == round(sum(m["spent"] for m in out["months"]), 2)
    assert abs(sum(g["total"] for g in out["groups"]) - out["totals"]["spent"]) < 0.01
    assert abs(sum(g["share"] for g in out["groups"]) - 1.0) < 0.01
    assert out["groups"] == sorted(out["groups"], key=lambda g: -g["total"])
    assert "Groceries" in {g["name"] for g in out["groups"]} and out["totals"]["savings_rate"] is not None
    assert out["totals"]["full_months"] == 5
    assert all(g["name"] == "(redacted)" for g in merchants["groups"]) and all(m["name"] == "(redacted)" for m in merchants["top_merchants"])


def test_net_worth_and_mountain_reconcile():
    with Session(engine) as session:
        seed(session)
        nw = net_worth(session, TODAY)
        mountain = debt_mountain(session, TODAY)
    assert nw["net"] == round(nw["assets"]["total"] - nw["debts"]["total"], 2)
    assert nw["assets"]["total"] == round(sum((a["balance"] or 0) for a in nw["assets"]["items"]), 2)
    assert mountain["total"] == nw["debts"]["total"]
    assert abs(sum(d["share"] for d in mountain["items"]) - 1.0) < 0.01
    assert mountain["items"] == sorted(mountain["items"], key=lambda d: -(d["balance"] or 0))
    kinds = {d["kind"] for d in mountain["items"]}
    assert "bnpl" in kinds and "advance" in kinds  # plans and advances count as debt
    assert mountain["order"]["avalanche"] and mountain["order"]["snowball"]
    assert mountain["interest_month"] >= 0 and mountain["minimums_month"] >= 0
    assert all(d["source"] for d in mountain["items"])


def test_report_routes_and_audit_page(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    spending = owner.get("/api/v1/reports/spending?months=3&by=merchant").json()
    assert spending["by"] == "merchant" and len(spending["months"]) == 3
    assert owner.get("/api/v1/reports/spending?by=colour").status_code == 400
    accounts = owner.get("/api/v1/accounts/overview").json()
    assert "net" in accounts and accounts["assets"]["items"]
    debts = owner.get("/api/v1/debts").json()
    assert debts["items"] and debts["order"]["avalanche"]
    audit = owner.get("/api/v1/audit").json()
    assert audit["entries"][0]["tool"] == "spending_summary" and audit["week"]["by_tool"]["spending_summary"] >= 1
