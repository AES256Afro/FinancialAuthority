"""Borrowing from pay, read from the ledger alone: advances paired with their repayments and the fee between
them, partial repayments that add up, an advance still out and due on payday, tips with no advance behind
them; installment streams with their cadence; the Borrowing category filed by rule; a stream turned into a plan
with an end date; the card payoff held back from the coming checks; the brief, the watchlist and the next
steps that say it. Every figure is synthetic."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.borrowing import borrowing_overview, installment_streams, pair_advances, sync_borrowing
from app.engine.projection import card_events
from app.engine.snapshot import finance_snapshot
from app.models import Account, Advance, Bill, Income, RecurringSeries, Transaction

TODAY = date(2026, 10, 15)


def _rows(spec):
    return [{"id": i, "date": d, "amount": a, "provider": p} for i, (d, a, p) in enumerate(spec, start=1)]


def test_advances_pair_with_their_repayments_and_fees():
    rows = _rows([
        (date(2026, 9, 17), -150.0, "Earnin"), (date(2026, 9, 17), -150.0, "Earnin"), (date(2026, 9, 30), 161.99, "Earnin"), (date(2026, 9, 30), 161.99, "Earnin"),
        (date(2026, 9, 16), -600.0, "Dave"), (date(2026, 9, 17), 629.0, "Dave"), (date(2026, 9, 15), 1.0, "Dave"),
        (date(2026, 8, 31), -600.0, "Dave"), (date(2026, 9, 1), 225.0, "Dave"), (date(2026, 9, 2), 179.0, "Dave"), (date(2026, 9, 3), 64.93, "Dave"), (date(2026, 9, 4), 160.07, "Dave"),
        (date(2026, 10, 8), -150.0, "Earnin"),
    ])
    out = pair_advances(rows, next_payday=date(2026, 10, 30))
    pairs = {(p["provider"], p["taken_on"].isoformat(), i): p for i, p in enumerate(out["pairs"])}
    earnin = [p for p in out["pairs"] if p["provider"] == "Earnin" and p["repaid"]]
    assert len(earnin) == 2 and all(p["fee"] == 11.99 and p["days"] == 13 and p["repay_date"] == date(2026, 9, 30) for p in earnin)
    dave_whole = next(p for p in out["pairs"] if p["provider"] == "Dave" and p["taken_on"] == date(2026, 9, 16))
    assert dave_whole["repaid"] and dave_whole["fee"] == 29.0 and dave_whole["days"] == 1
    dave_partial = next(p for p in out["pairs"] if p["provider"] == "Dave" and p["taken_on"] == date(2026, 8, 31))
    assert dave_partial["repaid"] and dave_partial["fee"] == 29.0 and dave_partial["repay_date"] == date(2026, 9, 4) and len(dave_partial["sources"]) == 5
    out_still = next(p for p in out["pairs"] if not p["repaid"])
    assert out_still["provider"] == "Earnin" and out_still["repay_date"] == date(2026, 10, 30) and out_still["fee"] == 0.0
    assert [t["amount"] for t in out["tips"]] == [1.0] and out["unmatched_repayments"] == []
    assert pairs  # the dict is only here to show each pair is distinct


def test_installment_streams_by_provider_and_amount():
    rows = _rows([
        (date(2026, 7, 25), 101.81, "Affirm"), (date(2026, 8, 16), 101.81, "Affirm"), (date(2026, 9, 28), 101.81, "Affirm"),
        (date(2026, 8, 16), 248.79, "Affirm"), (date(2026, 9, 22), 248.79, "Affirm"),
        (date(2026, 10, 2), 45.5, "Affirm"),
        (date(2026, 9, 1), 30.0, "Klarna"), (date(2026, 9, 15), 30.0, "Klarna"), (date(2026, 9, 29), 30.0, "Klarna"),
    ])
    streams = {(s["provider"], s["amount"]): s for s in installment_streams(rows, TODAY)}
    assert streams[("Affirm", 101.81)]["cadence_days"] == 30 and streams[("Affirm", 101.81)]["payments_seen"] == 3 and streams[("Affirm", 101.81)]["next_date"] == date(2026, 10, 28)
    assert streams[("Affirm", 248.79)]["cadence_days"] == 30 and streams[("Affirm", 248.79)]["active"]
    assert streams[("Affirm", 45.5)]["cadence_assumed"] and streams[("Affirm", 45.5)]["cadence_days"] == 30 and streams[("Affirm", 45.5)]["next_date"] == date(2026, 11, 1)
    assert streams[("Klarna", 30.0)]["cadence_days"] == 14 and streams[("Klarna", 30.0)]["paid_so_far"] == 90.0


def test_sync_files_borrowing_retires_the_fake_bill_and_queues_streams(owner: TestClient):
    with Session(engine) as session:
        chk = Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=2500.0, connector="demo", external_id="b:chk")
        card = Account(name="Everyday Card", institution="Example Bank", kind="card", balance=1200.0, connector="demo", external_id="b:card")
        session.add_all([chk, card])
        session.flush()
        spec = [
            (date(2026, 9, 17), "EARNIN ACTIVEHOURS", -150.0, "Financial"), (date(2026, 9, 30), "EARNIN ACTIVEHOURS", 161.99, "Loan Payment"),
            (date(2026, 10, 8), "EARNIN ACTIVEHOURS", -150.0, "Financial"),
            (date(2026, 9, 16), "DAVE INC", -600.0, "Credit"), (date(2026, 9, 17), "DAVE INC", 629.0, "Loan Payment"),
            (date(2026, 8, 16), "AFFIRM PAYMENT", 248.79, "Loan Payment"), (date(2026, 9, 22), "AFFIRM PAYMENT", 248.79, "Loan Payment"),
            (date(2026, 10, 2), "AFFIRM PAYMENT", 78.7, "Loan Payment"),
            (date(2026, 10, 3), "EXAMPLE GROCER", 54.12, "Groceries"),
        ]
        for i, (d, name, amount, category) in enumerate(spec):
            session.add(Transaction(account_id=chk.id, date=d, merchant=name.title(), raw_name=name, description=name, amount=amount, category=category, is_income=amount < 0, source="demo", source_ref=str(i), row_hash=f"borrow-{i}"))
        series = RecurringSeries(merchant_key="earnin activehours", merchant="Earnin Activehours", category="Loan Payment", cadence="biweekly", typical_amount=161.99, latest_amount=161.99, charges_seen=4, first_seen=date(2026, 8, 15), last_date=date(2026, 9, 30), next_expected=date(2026, 10, 15), kind="bill", confirmed=True)
        session.add(series)
        session.flush()
        session.add(Bill(name="Earnin Activehours", kind="bill", amount=161.99, amount_mode="fixed", cadence="biweekly", due_day=15, series_id=series.id, category="Loan Payment"))
        session.add(Bill(name="Affirm", kind="bill", amount=78.7, amount_mode="fixed", cadence="monthly", due_day=2, category="Loan Payment"))
        session.add(Income(name="Example Staffing", cadence="semimonthly", expected_net=2200.0, expected_net_end=2000.0, next_date=date(2026, 10, 30)))
        session.commit()
        out = sync_borrowing(session, TODAY)
        assert out["advances_created"] == 3 and out["bills_retired"] == 1 and out["streams_queued"] == 1 and out["rows_recategorised"] == 8
        advances = session.exec(select(Advance)).all()
        assert sum(1 for a in advances if a.repaid) == 2 and sum(a.fee for a in advances) == 40.99
        still = next(a for a in advances if not a.repaid)
        assert still.repay_date == date(2026, 10, 30) and still.amount == 150.0
        assert session.exec(select(Bill).where(Bill.name == "Earnin Activehours")).one().active is False
        session.refresh(series)
        assert series.kind == "advance" and series.confirmed
        queued = session.exec(select(RecurringSeries).where(RecurringSeries.merchant == "Affirm", RecurringSeries.confirmed == False)).all()  # noqa: E712
        assert len(queued) == 1 and queued[0].typical_amount == 248.79 and queued[0].suggested_kind == "installment"
        grocer = session.exec(select(Transaction).where(Transaction.raw_name == "EXAMPLE GROCER")).one()
        earnin_row = session.exec(select(Transaction).where(Transaction.raw_name == "EARNIN ACTIVEHOURS")).first()
        assert grocer.category == "Groceries" and earnin_row.category == "Borrowing" and earnin_row.is_income is False
        assert sync_borrowing(session, TODAY)["advances_created"] == 0  # idempotent
        snap = finance_snapshot(session, TODAY)
        assert snap["borrowing"]["fees_90d"] == 40.99 and snap["borrowing"]["outstanding_total"] == 150.0 and snap["borrowing"]["since_payday"]["advances"] == 1
        assert snap["usual_pace"]["transactions"] == 1  # borrowing never counts as everyday spending
        # the card balance is held back from the next two checks, half each, in their windows and not before payday
        card_rows = [e for e in snap["window"]["items"] if e["kind"] == "card_payment"]
        assert len(card_rows) == 1 and card_rows[0]["amount"] == 600.0 and card_rows[0]["date"] == "2026-10-30"
        assert not any(e["kind"] == "card_payment" for e in snap["due_before_payday"]["items"])
        assert snap["safe_to_spend"]["parts"]["advances_on_payday"] == 150.0
        over = borrowing_overview(session, TODAY)
    assert over["cost"]["fees_90d"] == 40.99 and over["cost"]["advances_90d"] == 3 and over["outstanding_total"] == 150.0
    assert {p["provider"] for p in over["providers"]} == {"Earnin", "Dave"} and next(p for p in over["providers"] if p["provider"] == "Earnin")["annual_rate"] > 1.0
    assert any(c["payday"] == "2026-09-30" and c["repaid_on_payday"] == 161.99 for c in over["cycle"])
    streams = {(s["provider"], s["amount"]): s for s in over["installments"]}
    assert streams[("Affirm", 248.79)]["status"] == "pending" and streams[("Affirm", 78.7)]["status"] == "bill"
    assert over["becomes"] and over["becomes"]["after_tax"] > 0 and over["steps"] and any("advance" in s["text"] for s in over["steps"])
    via = owner.get("/api/v1/borrowing").json()
    assert via["cost"]["fees_90d"] == 40.99 and via["settings"]["cards_pay_from"] == "auto"
    bill_id = next(s["bill_id"] for s in via["installments"] if s["status"] == "bill")
    made = owner.post("/api/v1/borrowing/plan", json={"bill_id": bill_id, "remaining": 3}).json()
    assert made["ok"] and made["plan"]["remaining"] == 3 and made["plan"]["installment"] == 78.7
    series_id = next(s["series_id"] for s in via["installments"] if s["status"] == "pending")
    assert owner.post("/api/v1/borrowing/plan", json={"series_id": series_id, "remaining": 2}).json()["plan"]["ends"] == "2026-11-21"
    after = owner.get("/api/v1/borrowing").json()
    assert all(s["status"] == "plan" for s in after["installments"] if s["active"])
    assert owner.put("/api/v1/borrowing/settings", json={"cards_pay_from": "end"}).json()["settings"]["cards_pay_from"] == "end"
    brief = owner.get("/api/v1/snapshot").json()["brief"]
    assert any("Borrowing cost" in b["text"] for b in brief) and any("taken since payday" in b["text"] for b in brief)
    steps = {s["id"]: s for s in owner.get("/api/v1/next_steps").json()["steps"]}
    assert steps["borrowing"]["link"] == "/borrowing"
    watch = owner.get("/api/v1/watchlist").json()["items"]
    assert any(i["kind"] == "borrowing" and i["severity"] == "critical" for i in watch)


def test_card_events_follow_the_payoff_setting():
    card = Account(name="Everyday Card", institution="Example Bank", kind="card", balance=1001.0, connector="demo", external_id="c:1")
    paydays = [date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 30)]
    split = card_events([card], paydays, "split", TODAY)
    assert [(e["date"], e["amount"]) for e in split] == [(date(2026, 10, 30), 500.5), (date(2026, 11, 13), 500.5)]
    assert [(e["date"], e["amount"]) for e in card_events([card], paydays, "auto", TODAY)] == [(e["date"], e["amount"]) for e in split]  # no debt row knows a minimum, so the whole balance is held back
    from app.models import Debt

    known = Debt(name="Everyday Card", kind="card", balance=1001.0, apr=24.9, minimum=40.0, due_day=25)
    auto = card_events([card], paydays, "auto", TODAY, [known])
    assert [(e["date"], e["amount"], e["name"]) for e in auto] == [(date(2026, 10, 25), 40.0, "Everyday Card minimum")]
    assert card_events([card], paydays, "off", TODAY, [known]) == []
    assert [(e["date"], e["amount"]) for e in card_events([card], paydays, "mid", TODAY)] == [(date(2026, 11, 13), 1001.0)]
    assert [(e["date"], e["amount"]) for e in card_events([card], paydays, "next", TODAY)] == [(date(2026, 10, 30), 1001.0)]
    assert card_events([Account(name="Paid off", institution="x", kind="card", balance=0.0, connector="demo", external_id="c:0")], paydays, "split", TODAY) == []
