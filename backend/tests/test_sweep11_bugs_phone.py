"""Sweep 11, part three: three defects found by driving the real app on a phone, each pinned here.

- A pay-later plan made from a series (no lender schedule: next_date, installment and remaining only) showed its
  payments left on Borrowing, but "I paid it" answered 400 "Nothing is left to pay on this plan".
- The "is it paid?" candidates for a $142 electricity period offered an $89 row that is another bill's own charge
  and an $88.70 market trip (and the phone preselects the first candidate). Candidates follow the matcher's own rule
  now: under the bill's names within its drift (40% for an estimate), or at its exact amount, or an unnamed row within
  the fixed 15%; never a row that carries another bill's name. With nothing close the list is empty, and the only
  answer on the card is "paid another way".
- A home with lease_ends_on and notice_days but no move-out date had `notice` null, so there was no "I gave notice"
  form, though lease_word named the give-by day from the same fields.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.borrowing import refresh_plan_progress
from app.engine.needsyou import payment_candidates
from app.engine.payments import match_payments
from app.models import Account, Bill, InstallmentPlan, Payment, RecurringSeries, Transaction

TODAY = date(2026, 10, 14)


def test_a_plan_without_a_schedule_can_be_marked_paid_and_the_banks_row_takes_the_claims_place(owner: TestClient):
    with Session(engine) as session:
        plan = InstallmentPlan(provider="affirm", merchant="Example Desk", total=240.0, installment=80.0, count=3, remaining=3, next_date=date(2026, 10, 16), cadence_days=14)
        acct = Account(name="Example Checking", kind="checking")
        session.add_all([plan, acct])
        session.commit()
        pid, aid = plan.id, acct.id
    page = owner.get("/api/v1/borrowing/plan").json()
    mine = next(l for l in page["loans"] if l["id"] == pid)
    assert mine["payments_left"] == 3
    out = owner.post(f"/api/v1/borrowing/plans/{pid}/paid", json={"on": "2026-10-14"})
    assert out.status_code == 200, out.text
    assert "2 payments left" in out.json()["said"] and "Oct 30" in out.json()["said"], out.json()["said"]
    assert next(l for l in out.json()["loans"] if l["id"] == pid)["payments_left"] == 2
    with Session(engine) as session:
        # The bank's row for that payment lands two days later: it stands in the owner's word's place, no second tick.
        session.add(Transaction(account_id=aid, date=date(2026, 10, 16), merchant="AFFIRM", amount=80.0, row_hash="phone-1"))
        session.commit()
        refresh_plan_progress(session, date(2026, 10, 17))
        p = session.get(InstallmentPlan, pid)
        assert (p.remaining, p.next_date, p.active) == (2, date(2026, 10, 30), True)
        # The next real payment ticks as before.
        session.add(Transaction(account_id=aid, date=date(2026, 10, 30), merchant="AFFIRM", amount=80.0, row_hash="phone-2"))
        session.commit()
        refresh_plan_progress(session, date(2026, 10, 31))
        p = session.get(InstallmentPlan, pid)
        assert (p.remaining, p.next_date) == (1, date(2026, 11, 13))
    # The last one by the owner's word ends the plan.
    out = owner.post(f"/api/v1/borrowing/plans/{pid}/paid", json={"on": "2026-11-13"})
    assert out.status_code == 400  # not a day to come: today is Oct 14 in the tests
    with Session(engine) as session:
        p = session.get(InstallmentPlan, pid)
        assert p.remaining == 1 and p.active


def test_paid_candidates_never_offer_another_bills_own_charge(owner: TestClient):
    with Session(engine) as session:
        acct = Account(name="Example Checking", kind="checking", balance=500.0)
        session.add(acct)
        session.flush()
        power = Bill(name="Example Power", amount=142.0, amount_mode="estimate", due_day=20, category="Bills & Utilities", pay_from_account_id=acct.id, created_on=date(2026, 6, 1))
        net = Bill(name="Example Internet", amount=89.0, due_day=22, category="Bills & Utilities", pay_from_account_id=acct.id, created_on=date(2026, 6, 1))
        session.add_all([power, net])
        session.flush()
        series = RecurringSeries(merchant_key="example internet", merchant="Example Internet", cadence="monthly", typical_amount=89.0, latest_amount=89.0, charges_seen=4,
                                 first_seen=date(2026, 6, 22), last_date=date(2026, 9, 22), next_expected=date(2026, 10, 22), kind="bill", confirmed=True)
        session.add(series)
        session.flush()
        net.series_id = series.id
        session.add(net)
        for m in (6, 7, 8):
            session.add(Transaction(account_id=acct.id, date=date(2026, m, 22), merchant="Example Internet", amount=89.0, category="Bills & Utilities", row_hash=f"net-{m}"))
            session.add(Transaction(account_id=acct.id, date=date(2026, m, 20), merchant="Example Power", amount=140.0 + m, category="Bills & Utilities", row_hash=f"pow-{m}"))
        # September: no power payment at all; the internet charge and a market trip sit near the due day.
        session.add(Transaction(account_id=acct.id, date=date(2026, 9, 22), merchant="Example Internet", amount=89.0, category="Bills & Utilities", row_hash="net-9"))
        session.add(Transaction(account_id=acct.id, date=date(2026, 9, 25), merchant="Example Market", amount=88.70, category="Groceries", row_hash="mkt-9"))
        session.commit()
        match_payments(session, TODAY)
        past = session.exec(select(Payment).where(Payment.bill_id == power.id, Payment.due_date == date(2026, 9, 20))).one()
        assert past.status == "past_due", past.status
        assert payment_candidates(session, past, power) == [], "an $89 internet charge or an $88.70 market trip offered as the $142 power payment"
        # A power payment under its own name (within the estimate's 40%), an unnamed row within 15%, and a row at the
        # exact amount are still offered: the named one first, then the nearest.
        session.add(Transaction(account_id=acct.id, date=date(2026, 9, 27), merchant="Online payment", amount=142.0, category="Bills & Utilities", row_hash="exact-9"))
        session.add(Transaction(account_id=acct.id, date=date(2026, 9, 30), merchant="Example Power", amount=190.0, category="Bills & Utilities", row_hash="pow-9"))
        session.add(Transaction(account_id=acct.id, date=date(2026, 9, 29), merchant="EX PWR AUTOPAY", amount=150.0, category="Bills & Utilities", row_hash="near-9"))
        session.commit()
        got = payment_candidates(session, past, power)
        assert [c["merchant"] for c in got] == ["Example Power", "Online payment", "EX PWR AUTOPAY"], got
    page = owner.get("/api/v1/needs-you").json()
    row = next(r for r in page["past_due"] if r["name"] == "Example Power")
    assert [c["merchant"] for c in row["candidates"]] == ["Example Power", "Online payment", "EX PWR AUTOPAY"]


def test_a_lease_end_with_notice_days_names_the_notice_without_a_move_out_date(owner: TestClient):
    made = owner.post("/api/v1/housing", json={"name": "Example Court 4", "lease_ends_on": "2026-12-31", "notice_days": 60, "due_day": 1})
    assert made.status_code == 200, made.text
    home = next(h for h in made.json()["homes"] if h["name"] == "Example Court 4")
    assert home["ends_on"] is None and home["lease_word"] and "give written notice by Sat Oct 31" in home["lease_word"], home["lease_word"]
    assert home["notice"] is not None, "no notice block, so no form to mark the notice given"
    assert home["notice"]["by"] == "2026-10-31" and home["notice"]["status"] == "warn" and home["notice"]["days_left"] == 17, home["notice"]
    given = owner.post(f"/api/v1/housing/{home['id']}/notice", json={"given_on": "2026-10-14"})
    assert given.status_code == 200, given.text
    after = next(h for h in given.json()["homes"] if h["id"] == home["id"])
    assert after["notice"]["status"] == "good" and after["notice"]["given_on"] == "2026-10-14"
    # A lease that has already ended runs month to month: nothing to give notice by, as before.
    old = owner.post("/api/v1/housing", json={"name": "Example Court 2", "lease_ends_on": "2026-03-31", "notice_days": 60, "due_day": 1}).json()
    assert next(h for h in old["homes"] if h["name"] == "Example Court 2")["notice"] is None
