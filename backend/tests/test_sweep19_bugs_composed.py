"""Sweep 19: the engine diff from 0.9.3 to 0.10.6 read whole, for fixes that broke a figure or two fixes that meet.

What it found, reduced:

- the month's row under the bill's names (0.10.6) took the next period's early payment for a missed month: rent due
  the 2nd, September not seen, October's rent paid on Sep 28. September stood paid by October's money, October stayed
  "scheduled", so safe to spend took the rent out a second time and Needs you lost the missed month;
- paid periods follow a row the bank changed (0.10.4), but a period kept on a moved day's old date (0.10.6) did not:
  the register showed the amount and the day the row no longer had;
- no balance is not $0 (0.10.5) left the daily line (the snapshot's `projection`) drawn from $0: the home's sixty-day
  chart said "under your safety cushion" on an install with no balance, though its weekly lows were None;
- a second person's own account left the owner's cash (0.10.3), but the bills and plans drawn from it, their
  everyday spending and their advances stayed in the owner's plan and came out of the owner's checking.

And the property tests for today's new features: undo of a confirmation, paging, no balance.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api import routes
from app.api.mail import ConfirmIn, apply_fact
from app.db import bump_version, engine
from app.engine.borrowing import sync_borrowing
from app.engine.decisions import purchase_check
from app.engine.facts import resolve_fact, unconfirm_fact, watermark
from app.engine.payments import match_payments, paid_periods
from app.engine.snapshot import finance_snapshot
from app.models import Account, Advance, Bill, ExtractedFact, Income, InstallmentPlan, MailAccount, MessageRef, Payment, Paystub, Transaction
from app.services import search_transactions

from tests.test_sweep15_bugs_undo import changed, household, tables

TODAY = date(2026, 10, 14)


def _checking(session: Session, balance: float | None = 2000.0) -> Account:
    a = session.exec(select(Account).where(Account.kind == "checking")).first()
    if a is None:
        a = Account(name="Example Checking", kind="checking", balance=balance)
        session.add(a)
        session.flush()
    return a


def _row(session: Session, account: Account, on: date, name: str, amount: float, tag: str = "") -> Transaction:
    t = Transaction(account_id=account.id, date=on, merchant=name, raw_name=name.upper(), amount=amount, category="Bills & Utilities", row_hash=f"{name}-{on.isoformat()}-{amount}-{tag}")
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


# --- payments.py: two of today's fixes meeting --------------------------------------------------------------------


def test_an_early_payment_for_next_month_is_not_taken_by_a_missed_month():
    """Rent due the 2nd: August paid, September not seen, October's rent paid early on Sep 28. That row sits in
    October's window (six days before), so it pays October; September stays past due. Before the fix the month's-row
    fallback gave it to September, October stood unpaid and the plan took the rent out again."""
    with Session(engine) as s:
        chk = _checking(s)
        bill = Bill(name="Example Rent", amount=900.0, due_day=2, pay_from_account_id=chk.id)
        s.add(bill)
        s.commit()
        _row(s, chk, date(2026, 8, 1), "Example Rent", 900.0)
        match_payments(s, date(2026, 9, 20))
        _row(s, chk, date(2026, 9, 28), "Example Rent", 900.0)
        match_payments(s, date(2026, 9, 29))
        got = {p.due_date: p.status for p in s.exec(select(Payment).where(Payment.bill_id == bill.id)).all()}
        assert got[date(2026, 9, 2)] == "past_due" and got[date(2026, 10, 2)] == "paid", got
        assert (bill.id, date(2026, 10, 2)) in paid_periods(s, date(2026, 9, 29))  # the plan does not take October's rent again


def test_a_payment_late_in_its_own_month_still_pays_that_month():
    """The fallback's own case stands: due the 5th, paid on the 20th (past the grace), no other period near it."""
    with Session(engine) as s:
        chk = _checking(s)
        bill = Bill(name="Example Water", amount=55.0, due_day=5, pay_from_account_id=chk.id)
        s.add(bill)
        s.commit()
        _row(s, chk, date(2026, 9, 20), "Example Water", 55.0)
        match_payments(s, date(2026, 9, 29))
        got = {p.due_date: p.status for p in s.exec(select(Payment).where(Payment.bill_id == bill.id)).all()}
        assert got[date(2026, 9, 5)] == "paid", got


def test_a_period_kept_on_a_moved_days_old_date_follows_the_banks_change_to_its_row():
    """The 5th moved to the 22nd; October's period stays on the 5th (no row near the 22nd takes it). The bank then
    changed that row to $62 on the 6th: the kept period says so, as one on the bill's day would."""
    with Session(engine) as s:
        chk = _checking(s)
        bill = Bill(name="Example Phone", amount=60.0, due_day=5, pay_from_account_id=chk.id)
        s.add(bill)
        s.commit()
        _row(s, chk, date(2026, 9, 5), "Example Phone", 60.0)
        t = _row(s, chk, date(2026, 10, 5), "Example Phone", 60.0)
        match_payments(s, date(2026, 10, 10))
        t.date, t.amount = date(2026, 10, 6), 62.0
        bill.due_day = 22
        s.add_all([t, bill])
        s.commit()
        match_payments(s, date(2026, 10, 29))
        kept = s.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.transaction_id == t.id)).one()
        assert (kept.status, kept.amount, kept.paid_date) == ("paid", 62.0, date(2026, 10, 6))


# --- snapshot.py: no balance and the daily line --------------------------------------------------------------------


def _no_balance_household(s: Session, rng: random.Random | None = None) -> None:
    rng = rng or random.Random(0)
    s.add(Account(name="Example Checking", institution="Example Bank", kind="checking", balance=None, connector="csv"))
    if rng.random() < 0.5:
        s.add(Account(name="Example Card", kind="card", balance=round(rng.uniform(0, 900), 2), credit_limit=2000.0))
    if rng.random() < 0.5:
        s.add(Account(name="Example Savings", kind="savings", balance=round(rng.uniform(0, 3000), 2)))
    if rng.random() < 0.85:
        s.add(Income(name="Example Employer", cadence=rng.choice(["biweekly", "semimonthly", "monthly", "weekly"]), expected_net=round(rng.uniform(200, 2500), 2),
                     next_date=TODAY + timedelta(days=rng.randint(0, 13))))
    for k in range(rng.randint(0, 6)):
        s.add(Bill(name=f"Example Bill {k}", amount=round(rng.uniform(10, 1500), 2), due_day=rng.randint(1, 31), autopay=rng.random() < 0.5))
    if rng.random() < 0.5:
        s.add(InstallmentPlan(provider="Klarna", merchant="Example Store", total=200.0, installment=50.0, count=4, remaining=rng.randint(1, 4), next_date=TODAY + timedelta(days=rng.randint(-3, 20)), cadence_days=14))
    if rng.random() < 0.5:
        s.add(Advance(provider="Dave", amount=100.0, fee=5.0, taken_on=TODAY - timedelta(days=3), repay_date=TODAY + timedelta(days=rng.randint(-2, 12))))
    s.commit()


def test_no_balance_leaves_the_daily_line_unworked_and_the_purchase_check_answers():
    with Session(engine) as s:
        _no_balance_household(s, random.Random(3))
        s.add(Bill(name="Example Loan", amount=700.0, due_day=20))
        s.commit()
        snap = finance_snapshot(s, TODAY)
        assert snap["no_balance"] is True and snap["projection"]
        assert all(p["available"] is None and p["at_pace"] is None and p["cash"] is None for p in snap["projection"])
        check = purchase_check(s, TODAY, 40.0)
        assert check["verdict"] == "outside" and check["no_balance"] and check["low_point"]["at_pace"] is None and check["low_point_before"] is None


def _negatives(obj, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _negatives(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _negatives(v, f"{path}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool) and obj < 0:
        yield path, obj


# A minus sign the snapshot carries by its own convention, not a balance: money in on the calendar, the steps taken off
# on the way to safe to spend, a later check's own window (it starts from set-asides, known without a balance), and a
# card's balance or a line about what it owes.
SIGNED = ("calendar.events", "safe_to_spend.steps", "windows[1]", "windows[2]", "windows[3]", "card_payments", "on_card", "debts")


@pytest.mark.parametrize("seed", range(25))
def test_property_no_balance_never_yields_a_figure_built_on_nothing(seed: int):
    """Randomised households with no balance anywhere the plan spends from: every figure that starts from the balance
    is None, no other figure is negative, and no sentence says short, under or dips."""
    with Session(engine) as s:
        _no_balance_household(s, random.Random(seed))
        snap = finance_snapshot(s, TODAY)
    assert snap["no_balance"] is True
    assert snap["cash"] is None and snap["safe_to_spend"]["amount"] is None and snap["safe_to_spend"]["after_goals"] is None
    assert snap["windows"][0]["carried_in"] is None and snap["windows"][0]["safe"] is None and snap["windows"][0]["inflow"] is None
    assert snap["usual_pace"]["over_safe"] is None and snap["payday"].get("left") is None
    assert all(w["low"] is None for w in snap["calendar"]["weeks"])
    assert all(p["at_pace"] is None for p in snap["projection"])
    bad = [(p, v) for p, v in _negatives(snap) if not any(p.startswith(f".{k}") for k in SIGNED)]
    assert not bad, bad
    words = " ".join(b["text"] for b in snap["brief"]) + " " + " ".join(snap["why"].values())
    assert "short" not in words.lower().replace("shortfall", "") or "next" in words.lower(), words
    assert "under the" not in words.lower() and "dips" not in words.lower(), words


# --- a second person's own account -----------------------------------------------------------------------------------


def test_a_second_persons_own_bills_plans_spending_and_advances_stay_out_of_the_owners_plan():
    """Their balance left the owner's cash in 0.10.3; the bills and the plan drawn from their account (and their card),
    their everyday spending and their advance then still came out of the owner's checking."""
    with Session(engine) as s:
        s.add(Account(name="Example Checking", kind="checking", balance=2000.0))
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16)))
        s.commit()
        before = finance_snapshot(s, TODAY)
        theirs = Account(name="Example Partner Checking", kind="checking", balance=300.0, person_id=7)
        card = Account(name="Example Partner Card", kind="card", balance=0.0, person_id=7)
        s.add_all([theirs, card])
        s.commit()
        s.add(Bill(name="Example Partner Phone", amount=80.0, due_day=15, pay_from_account_id=theirs.id))
        s.add(Bill(name="Example Partner Gym", amount=40.0, due_day=15, pay_from_account_id=card.id))
        s.add(InstallmentPlan(provider="Klarna", merchant="Example Store", total=200, installment=50, count=4, remaining=4, next_date=date(2026, 10, 15), cadence_days=14, pay_from_account_id=theirs.id))
        for k in range(20):
            s.add(Transaction(account_id=theirs.id, date=TODAY - timedelta(days=k + 1), merchant="Example Cafe", raw_name="EXAMPLE CAFE", amount=12.0, category="Food & Dining", row_hash=f"cafe-{k}"))
        s.add(Transaction(account_id=theirs.id, date=date(2026, 10, 10), merchant="Dave", raw_name="DAVE ADVANCE", amount=-100.0, category="Transfer", row_hash="dave-1"))
        s.commit()
        sync_borrowing(s, TODAY)
        bump_version()
        after = finance_snapshot(s, TODAY)
    assert after["safe_to_spend"]["amount"] == before["safe_to_spend"]["amount"]
    assert after["usual_pace"]["per_day"] == before["usual_pace"]["per_day"]
    assert after["advances"]["total"] == before["advances"]["total"] == 0.0
    assert [w["safe"] for w in after["windows"]] == [w["safe"] for w in before["windows"]]


def test_a_shared_accounts_bills_still_count():
    with Session(engine) as s:
        s.add(Account(name="Example Checking", kind="checking", balance=2000.0))
        joint = Account(name="Example Joint Checking", kind="checking", balance=0.0, shared=True, person_id=7)
        s.add(joint)
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16)))
        s.commit()
        before = finance_snapshot(s, TODAY)["safe_to_spend"]["amount"]
        s.add(Bill(name="Example Joint Power", amount=80.0, due_day=15, pay_from_account_id=joint.id))
        s.commit()
        bump_version()
        assert finance_snapshot(s, TODAY)["safe_to_spend"]["amount"] == round(before - 80.0, 2)


# --- property: undo of a confirmation round-trips the database ------------------------------------------------------


def _fact(s: Session, kind: str, fields: dict, uid: int) -> ExtractedFact:
    box = s.exec(select(MailAccount)).first()
    if box is None:
        box = MailAccount(label="Example", kind="imap", host="mail.example.test", port=993, username="owner@example.test", password_enc="")
        s.add(box)
        s.flush()
    ref = MessageRef(account_id=box.id, folder="INBOX", uid=uid, sender="notices@example.test", sender_name=fields.get("sender_name", "Example Sender"), subject="A notice", trust="ok")
    s.add(ref)
    s.flush()
    f = ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps(fields), confidence=0.9)
    s.add(f)
    s.commit()
    return f


def _random_fact(rng: random.Random, k: int) -> tuple[str, dict, dict]:
    kind = rng.choice(["bill_due", "bill_due", "installment", "installment_schedule", "paystub"])
    when = TODAY + timedelta(days=rng.randint(1, 40))
    if kind == "bill_due":
        name = rng.choice(["Example Power", "Example Internet", "Example Water", f"Example Service {k}"])
        return "bill_due", {"name": name, "amount": round(rng.uniform(10, 300), 2), "due_date": when.isoformat()}, {}
    if kind == "installment":
        return "installment", {"provider": "Example Pay Later", "merchant": f"Example Store {k}", "amount": round(rng.uniform(10, 90), 2)}, {"remaining": rng.randint(1, 6)}
    if kind == "installment_schedule":
        each = round(rng.uniform(10, 90), 2)
        schedule = [{"date": (when + timedelta(days=14 * i)).isoformat(), "amount": each} for i in range(rng.randint(2, 5))]
        loan = rng.choice(["", f"EX{rng.randint(1, 3)}"])
        return "installment", {"provider": "Example Pay Later", "merchant": f"Example Shop {k}", "schedule": schedule, "loan_id": loan}, {}
    return "paystub", {"net": round(rng.uniform(800, 2500), 2), "pay_date": (TODAY + timedelta(days=50 + k)).isoformat(), "employer": "Example Employer", "gross": 3000.0}, {}


@pytest.mark.parametrize("seed", range(12))
def test_property_undoing_confirmations_round_trips_the_database(owner: TestClient, seed: int):
    """Random facts of every kind that makes something (a bill, a typed plan, a lender's plan, a paystub), with names
    that collide, confirmed in turn and taken back newest first. Each undo either refuses with its sentence and changes
    nothing, or takes back exactly what its confirmation made. When every one is taken back, every table is as it was
    (the usual net a paystub set is the one thing said to stay); no applied fact is ever left naming a row that is gone."""
    rng = random.Random(seed)
    household(TODAY)
    with Session(engine) as s:
        facts = [(_fact(s, kind, fields, uid=k + 1).id, body, fields) for k, (kind, fields, body) in enumerate(_random_fact(rng, k) for k in range(rng.randint(1, 5)))]
    before = tables()
    with Session(engine) as s:
        for fid, body, _ in facts:
            f = s.get(ExtractedFact, fid)
            b = watermark(s)
            try:
                applied = apply_fact(s, f, s.get(MessageRef, f.message_ref_id), ConfirmIn(**body), owner=True)
            except Exception:  # noqa: BLE001 - a fact the apply refuses stays pending, as the route answers 400
                s.rollback()
                continue
            resolve_fact(s, f, "applied" if applied else "confirmed", applied_to=applied, by="owner", before=b)
            s.commit()
    all_back = True
    with Session(engine) as s:
        for fid, _, _ in reversed(facts):
            mid = tables()
            try:
                unconfirm_fact(s, fid, by="owner")
            except ValueError:
                s.rollback()
                all_back = False
                assert not changed(mid, tables())  # a refusal changes nothing
            for row in s.exec(select(ExtractedFact).where(ExtractedFact.status == "applied")).all():
                kind, _, ident = row.applied_to.partition(":")
                model = {"bill": Bill, "plan": InstallmentPlan, "paystub": Paystub}.get(kind)
                if model is not None and ident.isdigit():
                    assert s.get(model, int(ident)) is not None, f"fact {row.id} applied to {row.applied_to}, which is gone"
    if all_back:
        diff = changed(before, tables())
        if any(fields.get("net") for _, _, fields in facts):
            diff = [d for d in diff if not d.startswith("incomes:")]
        assert not diff, diff


def test_undoing_a_confirmation_whose_bill_a_later_one_changed_refuses():
    """A notice made a bill; a second notice for the same biller changed it. Taking the first back deleted the bill
    the second still named (and SQLite gives its id to the next bill made): it refuses instead."""
    with Session(engine) as s:
        first = _fact(s, "bill_due", {"name": "Example Power", "amount": 50.0, "due_date": "2026-10-28"}, uid=1)
        second = _fact(s, "bill_due", {"name": "Example Power", "amount": 55.0, "due_date": "2026-11-28"}, uid=2)
        for f in (first, second):
            b = watermark(s)
            applied = apply_fact(s, f, s.get(MessageRef, f.message_ref_id), ConfirmIn(), owner=True)
            resolve_fact(s, f, "applied", applied_to=applied, by="owner", before=b)
            s.commit()
        with pytest.raises(ValueError, match="changed it since"):
            unconfirm_fact(s, first.id)
        s.rollback()
        assert s.exec(select(Bill).where(Bill.name == "Example Power")).one().amount == 55.0


# --- property: paging returns every row exactly once ----------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_property_paging_returns_every_row_once_in_order_across_writes_between_pages(seed: int):
    """Pages walked with the cursor each page hands back: rows added between pages (a sync landing, newer or older
    than the page) and rows deleted between pages (a pending charge replaced) never repeat or skip a row that stood
    for the whole walk, and the order is the one-shot order."""
    rng = random.Random(seed)
    with Session(engine) as s:
        acct = _checking(s)
        made = 0

        def add(n: int, newest: bool) -> None:
            nonlocal made
            for _ in range(n):
                made += 1
                on = TODAY - timedelta(days=0 if newest else rng.randint(0, 60))
                s.add(Transaction(account_id=acct.id, date=on, merchant=f"Example Shop {made}", raw_name="SHOP", amount=round(rng.uniform(1, 99), 2), category="Shopping", row_hash=f"r{seed}-{made}"))
            s.commit()

        add(rng.randint(5, 60), newest=False)
        standing = {t.id for t in s.exec(select(Transaction)).all()}
        limit = rng.randint(1, 9)
        seen: list[int] = []
        after = None
        gone: set[int] = set()
        for _ in range(500):
            page = search_transactions(s, limit=limit, after=after)
            seen += [t["id"] for t in page["transactions"]]
            after = page.get("next")
            if not after:
                break
            add(rng.randint(0, 3), newest=rng.random() < 0.7)
            if rng.random() < 0.3:
                victim = rng.choice(sorted(standing - set(seen) - gone) or [0])
                row = s.get(Transaction, victim)
                if row is not None:
                    s.delete(row)
                    s.commit()
                    gone.add(victim)
        survived = standing - gone
        assert len(seen) == len(set(seen)), "a row came twice"
        assert survived <= set(seen), f"skipped {sorted(survived - set(seen))}"
        rows = {t.id: t for t in s.exec(select(Transaction)).all()}
        keys = [(rows[i].date, i) for i in seen if i in rows]
        assert keys == sorted(keys, reverse=True)
