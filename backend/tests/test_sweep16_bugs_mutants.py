"""Round twelve: mutation testing of the engine. Each test here kills a mutant (a flipped comparison, a constant off by
one, a swapped min or max) that the rest of the suite let through, so the boundary it pins is now held."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import Account, Bill, Debt, Income, Payment, Transaction

TODAY = date(2026, 10, 14)


def _tx(session: Session, account_id: int, when: date, merchant: str, amount: float, **kw) -> Transaction:
    t = Transaction(account_id=account_id, date=when, merchant=merchant, raw_name=merchant, amount=amount, category=kw.pop("category", "Bills & Utilities"),
                    row_hash=f"{merchant}-{when}-{amount}-{kw.get('note', '')}", **kw)
    session.add(t)
    return t


def _checking(session: Session, balance: float = 1000.0) -> Account:
    a = Account(name="Example Checking", institution="Example Bank", kind="checking", balance=balance, balance_at=datetime(2026, 10, 14, tzinfo=timezone.utc), connector="manual")
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


# ---- payments: a payment exactly at the tolerance pays the month (payments._match_in_month, `<=`) -------------

def test_a_payment_exactly_at_the_tolerance_later_in_the_month_pays_that_month():
    from app.engine.payments import match_payments

    with Session(engine) as session:
        a = _checking(session)
        bill = Bill(name="Example Water", amount=100.0, due_day=3, created_on=date(2026, 8, 1))
        session.add(bill)
        # September's payment: paid on the 25th (far from the 3rd, so only the same-month match finds it), $15 over.
        _tx(session, a.id, date(2026, 9, 25), "Example Water", 115.0)
        session.commit()
        match_payments(session, TODAY)
        sept = session.exec(select(Payment).where(Payment.bill_id == bill.id, Payment.due_date == date(2026, 9, 3))).one()
        assert sept.status == "paid" and sept.amount == 115.0


# ---- the past-due list keeps ninety days, the ninetieth included (needsyou.PAST_DUE_DAYS) ------------------

def test_the_past_due_list_keeps_exactly_ninety_days():
    from app.engine.needsyou import past_due_waiting

    with Session(engine) as session:
        bill = Bill(name="Example Gym", amount=30.0, due_day=1)
        session.add(bill)
        session.commit()
        session.add(Payment(bill_id=bill.id, due_date=TODAY - timedelta(days=90), amount=30.0, status="past_due"))
        session.add(Payment(bill_id=bill.id, due_date=TODAY - timedelta(days=91), amount=30.0, status="past_due"))
        session.commit()
        listed = past_due_waiting(session, TODAY)
        assert [p.due_date for p in listed] == [TODAY - timedelta(days=90)]


# ---- Cover's low point is the first day the balance gets there (cover._walk, `<`) ----------------------------

def test_the_low_point_is_the_first_day_the_balance_reaches_it():
    from app.engine.cover import _walk

    start = date(2026, 10, 14)
    events = [{"date": date(2026, 10, 15), "amount": 100.0}]
    low, day = _walk(500.0, events, 0.0, start, date(2026, 10, 20))
    assert (low, day) == (400.0, date(2026, 10, 15))  # flat after the 15th: the 15th, not the 20th, is when it runs lowest


# ---- the payoff plan accepts up to 100,000 extra a month, the bound included (payoff.payoff_plan, `<=`) -----

def test_the_payoff_extra_takes_its_upper_bound_and_refuses_past_it():
    from app.engine.payoff import payoff_plan

    with Session(engine) as session:
        session.add(Debt(name="Example Card", balance=800.0, apr=24.0, minimum=35.0))
        session.commit()
        assert payoff_plan(session, TODAY, extra=100_000.0)
        with pytest.raises(ValueError):
            payoff_plan(session, TODAY, extra=100_000.01)


# ---- the month bands: what is yours is never below zero, and is what is left (paychecks.months_ahead, max) ----

def test_the_months_ahead_band_left_for_you_is_what_the_checks_leave():
    from app.engine.paychecks import months_ahead

    with Session(engine) as session:
        _checking(session)
        session.add(Income(name="Example Employer", cadence="monthly", expected_net=3000.0, next_date=date(2026, 11, 1), pay_day=1))
        session.add(Bill(name="Example Rent", amount=1000.0, due_day=5))
        session.commit()
        months = months_ahead(session, TODAY, None, count=3)
    nov = next(m for m in months if m["month"] == "2026-11")
    yours = next(p for p in nov["parts"] if p["key"] == "yours")["amount"]
    assert yours == pytest.approx(nov["income"] - nov["obligations"], abs=0.01) and yours > 0


# ---- the borrowing steps: no installment step when no installment runs (borrowing.borrowing_overview, `>`) ---

def test_no_installment_step_when_nothing_is_paid_in_installments():
    from app.engine.borrowing import borrowing_overview

    with Session(engine) as session:
        _checking(session)
        out = borrowing_overview(session, TODAY)
    assert not any(s["text"].startswith("Installments take about") for s in out["steps"])


# ---- an approval with no count of payments is one payment (borrowing.approval_has_ended, `or 1`) -------------

def test_an_approval_with_no_count_ends_a_month_after_its_one_payment():
    from app.engine.borrowing import approval_has_ended

    sent = date(2026, 6, 1)
    fields = {"message_date": sent.isoformat(), "cadence_days": 30}
    assert approval_has_ended(fields, sent + timedelta(days=61)) is True  # 30 for the payment, 30 of grace
    assert approval_has_ended(fields, sent + timedelta(days=60)) is False


# ---- a lender's schedule twenty days apart on average is a two-week plan (borrowing.plan_from_fields) ---------

def test_a_schedule_twenty_days_apart_reads_as_every_two_weeks_and_twenty_one_as_monthly():
    from app.engine.borrowing import plan_from_fields

    def plan(gaps: list[int], loan: str):
        d = date(2026, 11, 1)
        schedule = [{"date": d.isoformat(), "amount": 25.0}]
        for g in gaps:
            d += timedelta(days=g)
            schedule.append({"date": d.isoformat(), "amount": 25.0})
        with Session(engine) as session:
            return plan_from_fields(session, {"provider": "Affirm", "loan_id": loan, "schedule": schedule, "message_date": "2026-10-10"}, None, None, TODAY).cadence_days

    assert plan([20, 20, 20], "L-20") == 14
    assert plan([21, 21, 21], "L-21") == 30


# ---- a card's use is reported to four places (reports.owed, round(..., 4)) ----------------------------------

def test_a_cards_use_is_given_to_four_places():
    from app.engine.reports import owed

    with Session(engine) as session:
        session.add(Debt(name="Example Card", balance=1000.0, apr=20.0, minimum=30.0, credit_limit=3000.0))
        session.commit()
        item = next(i for i in owed(session, TODAY)["items"] if i["name"] == "Example Card")
    assert item["utilisation"] == 0.3333


# ---- budgets: the pace has five dollars of slack, and a budget equal to safe to spend fits ------------------

def _budget_setup(session: Session, spent: float, per_paycheck: float = 280.0):
    from app.models import CategoryBudget

    a = _checking(session)
    session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
    session.add(CategoryBudget(category="Groceries", per_paycheck=per_paycheck, rollover="none"))
    _tx(session, a.id, date(2026, 10, 12), "Example Grocer", spent, category="Groceries")
    session.commit()


def test_a_budget_within_five_dollars_of_its_pace_is_on_pace():
    from app.engine.budgets import budget_plan, paycheck_span

    with Session(engine) as session:
        _budget_setup(session, 0.0)
        span = paycheck_span(session, TODAY)
        days_total = (span["end"] - span["start"]).days + 1
        days_gone = (TODAY - span["start"]).days + 1
        expected = 280.0 * days_gone / days_total
        t = session.exec(select(Transaction)).one()
        t.amount = round(expected + 4.0, 2)  # four dollars ahead of the pace: inside the slack
        session.add(t)
        session.commit()
        item = budget_plan(session, TODAY)["items"][0]
    assert item["status"] == "good" and item["word"] == "On pace"


def test_budgets_that_leave_exactly_safe_to_spend_fit_with_no_note(monkeypatch):
    from app.engine import budgets as budgets_mod
    from app.engine import snapshot

    with Session(engine) as session:
        _budget_setup(session, 80.0)  # 200 left in the budget
        real = snapshot.finance_snapshot

        def safe_at(value):
            def fake(session, today):
                snap = real(session, today)
                snap["safe_to_spend"]["amount"] = value
                return snap
            return fake

        monkeypatch.setattr(snapshot, "finance_snapshot", safe_at(199.997))  # the budgets leave a third of a cent more than is safe
        out = budgets_mod.budget_plan(session, TODAY)
    assert out["fits"] is True and out["fit_note"] is None


# ---- billing-date suggestions: a move that lifts the low point is found, the nearest day wins a tie, and a lift of
# exactly a dollar counts (decisions._billing_date_suggestions). The household gives none, so nothing held these. -

def _suggestion_world(insurance: float, rent_day: int = 1, insurance_day: int = 28) -> None:
    with Session(engine) as session:
        _checking(session, 1000.0)
        session.add(Income(name="Example Employer", cadence="weekly", expected_net=500.0, next_date=date(2026, 10, 16)))
        session.add(Bill(name="Example Rent", amount=1800.0, due_day=rent_day, movable=True))
        session.add(Bill(name="Example Insurance", amount=insurance, cadence="yearly", due_day=insurance_day, anchor_date=date(2026, 11, insurance_day), autopay=True))
        session.commit()


def test_of_equal_lifts_on_either_side_the_day_nearest_the_due_day_wins():
    from app.engine.decisions import billing_date_suggestions

    _suggestion_world(900.0, rent_day=19, insurance_day=2)
    with Session(engine) as session:
        out = billing_date_suggestions(session, TODAY)
    # The 1st to 3rd and the 27th and 28th lift the low point alike; the 27th is eight days from the 19th, the 3rd sixteen.
    assert [(s["to_day"], s["improvement"]) for s in out["suggestions"]] == [(27, 700.0)]


def test_a_move_that_lifts_the_low_point_is_suggested_on_the_nearest_day():
    from app.engine.decisions import billing_date_suggestions

    _suggestion_world(1500.0)
    with Session(engine) as session:
        out = billing_date_suggestions(session, TODAY)
    # Rent on Dec 1 lands before the Dec 4 check; any day from the 4th on is as good, and the 4th is nearest.
    assert [(s["name"], s["from_day"], s["to_day"], s["improvement"]) for s in out["suggestions"]] == [("Example Rent", 1, 4, 500.0)]
    assert [(p["to_day"], p["improvement"]) for p in out["plan"]] == [(4, 500.0)] and out["plan_low"] == -300.0  # the plan finds the same move
    assert out["moves_start"] == "2026-11-16"  # the rent's second charge, Dec 1, less fifteen days


def test_a_move_that_lifts_the_low_point_by_exactly_a_dollar_is_suggested():
    from app.engine.decisions import billing_date_suggestions

    _suggestion_world(201.0)
    with Session(engine) as session:
        out = billing_date_suggestions(session, TODAY)
    assert [(s["to_day"], s["improvement"]) for s in out["suggestions"]] == [(4, 1.0)]


def test_moves_start_from_the_earliest_second_charge():
    from app.engine.decisions import billing_date_suggestions

    _suggestion_world(1500.0)
    with Session(engine) as session:
        session.add(Bill(name="Example Phone", amount=40.0, due_day=20, movable=True))
        session.commit()
        out = billing_date_suggestions(session, TODAY)
    assert out["moves_start"] == "2026-11-05"  # the phone's Nov 20, not the rent's Dec 1


# ---- the purchase check's low point counts the purchase day itself (decisions.purchase_check, `>= on`) ---------

def test_the_purchase_low_point_can_be_the_purchase_day():
    from app.engine.decisions import purchase_check

    with Session(engine) as session:
        _checking(session, 2000.0)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Bill(name="Example Rent", amount=950.0, due_day=20, autopay=True))
        session.commit()
        out = purchase_check(session, TODAY, 100.0, on=date(2026, 10, 20))
    assert out["low_point"]["date"] == "2026-10-20"


# ---- the snapshot's Needs-you total adds the past-due periods (snapshot, pending.total) --------------------------

def test_the_needs_you_total_adds_every_kind_waiting(owner):
    from app.models import RecurringSeries

    with Session(engine) as session:
        bill = Bill(name="Example Gym", amount=30.0, due_day=1)
        session.add(bill)
        session.add(RecurringSeries(merchant_key="example cloud", merchant="Example Cloud", category="Bills & Utilities", cadence="monthly", typical_amount=9.0, latest_amount=9.0,
                                    charges_seen=4, first_seen=date(2026, 6, 1), last_date=date(2026, 10, 1), next_expected=date(2026, 11, 1), active=True))
        session.commit()
        session.add(Payment(bill_id=bill.id, due_date=date(2026, 9, 1), amount=30.0, status="past_due"))
        session.commit()
    p = owner.get("/api/v1/snapshot").json()["pending"]
    assert p["past_due"] == 1 and p["series"] == 1
    assert p["total"] == p["series"] + p["facts"] + p["pairs"] + p["past_due"] + p["approvals"] == 2


# ---- the payday that landed today: its window ends the day before the next check (snapshot, payday.end) --------

def test_a_landed_paydays_window_ends_the_day_before_the_next_check(owner):
    with Session(engine) as session:
        a = _checking(session, 2000.0)
        session.add(Income(name="Example Employer Payroll", cadence="biweekly", expected_net=1500.0, next_date=TODAY, account_id=a.id))
        _tx(session, a.id, TODAY, "Example Employer Payroll", -1500.0, category="Income", is_income=True)
        session.commit()
    pay = owner.get("/api/v1/snapshot").json()["payday"]
    assert pay["today"] and pay["landed"] and pay["end"] == "2026-10-27"


# ---- why a card is left out of safe to spend: only its own payment before payday counts (snapshot._explain_safe) -

def test_a_card_whose_payment_falls_after_payday_says_so(owner):
    with Session(engine) as session:
        _checking(session, 3000.0)
        card = Account(name="Example Card", institution="Example Bank", kind="card", balance=300.0, connector="manual", credit_limit=2000.0)
        session.add(card)
        session.commit()
        session.add(Debt(name="Example Card", balance=300.0, minimum=35.0, due_day=28, account_id=card.id))
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Bill(name="Example Rent", amount=950.0, due_day=20, autopay=True))  # before payday, and not the card's
        session.commit()
    left = owner.get("/api/v1/snapshot").json()["safe_to_spend"]["left_out"]
    card_row = next(r for r in left if r["name"] == "Example Card")
    assert "falls on or after payday" in card_row["why"]


# ---- a purchase alert the bank already posted, to the cent, is not in flight (snapshot._in_flight) -------------

def test_a_mailed_purchase_posted_a_cent_apart_is_not_counted_twice(owner):
    import json

    from app.models import ExtractedFact

    with Session(engine) as session:
        a = _checking(session)
        _tx(session, a.id, date(2026, 10, 13), "Example Grocer", 20.01, category="Groceries")
        session.add(ExtractedFact(kind="bank_alert", fields=json.dumps({"alert": "purchase", "amount": 20.0, "merchant": "Example Grocer", "message_date": "2026-10-13"}),
                                  status="auto", created_at=datetime(2026, 10, 13, 12, tzinfo=timezone.utc)))
        session.commit()
    assert owner.get("/api/v1/snapshot").json()["in_flight"]["mail"]["items"] == []


# ---- Cover: spending accounts first, then cards, then savings; the pay-by is two days before the statement;
# a tick is kept sixty-two days (cover.account_cover, cover.tick) ---------------------------------------------

def test_cover_orders_accounts_and_dates_the_early_card_payment():
    from app.engine.cover import account_cover

    with Session(engine) as session:
        _checking(session, 3000.0)
        session.add(Account(name="Example Savings", institution="Example Bank", kind="savings", balance=5000.0, connector="manual"))
        card = Account(name="Example Card", institution="Example Bank", kind="card", balance=900.0, connector="manual", credit_limit=1000.0)
        session.add(card)
        session.commit()
        session.add(Debt(name="Example Card", balance=900.0, minimum=35.0, due_day=28, statement_day=25, account_id=card.id))
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.commit()
        out = account_cover(session, TODAY)
    assert [a["role"] for a in out["accounts"]] == ["spend", "card", "save"]
    card_row = next(a for a in out["accounts"] if a["role"] == "card")
    assert card_row["statement_on"] == "2026-10-25" and card_row["pay_by"] == "2026-10-23"


def test_a_cover_tick_is_kept_sixty_two_days():
    from app.engine.cover import SHEET_KEY, tick
    from app.store import get_setting, set_setting

    with Session(engine) as session:
        set_setting(session, SHEET_KEY, {"kept": (TODAY - timedelta(days=62)).isoformat(), "gone": (TODAY - timedelta(days=63)).isoformat()})
        tick(session, "new", True, TODAY)
        assert set(get_setting(session, SHEET_KEY, {})) == {"kept", "new"}


# ---- projection: a weekly series charging on the first day counts that day; a plan paid from a card rides the card;
# a card with a $1 minimum has a minimum (projection.series_dates, build_events, cards_paid_by_bills, card_payoffs) -

def test_a_weekly_series_charging_on_the_first_day_counts_that_day():
    from app.engine.projection import series_dates

    start = date(2026, 10, 14)
    assert series_dates("weekly", start, start, date(2026, 10, 28)) == [start, date(2026, 10, 21), date(2026, 10, 28)]


def test_a_plan_paid_from_a_card_rides_the_card_not_checking(owner):
    from app.models import InstallmentPlan

    with Session(engine) as session:
        _checking(session, 3000.0)
        card = Account(name="Example Card", institution="Example Bank", kind="card", balance=0.0, connector="manual", credit_limit=2000.0)
        session.add(card)
        session.commit()
        session.add(InstallmentPlan(provider="Example Lender", merchant="Example Store", total=100.0, installment=25.0, count=4, remaining=4, next_date=date(2026, 10, 18), cadence_days=14, pay_from_account_id=card.id))
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.commit()
    snap = owner.get("/api/v1/snapshot").json()
    assert not [e for e in snap["due_before_payday"]["items"] if "Example" in e["name"] and not e.get("via_card")]
    assert snap["safe_to_spend"]["parts"]["due_before_payday"] == 0.0


def test_a_one_dollar_card_minimum_is_a_minimum():
    from app.engine.projection import card_payoffs, cards_paid_by_bills

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=50.0, connector="manual")
    debt = Debt(id=3, name="Example Card", balance=50.0, minimum=1.0, due_day=20, account_id=7)
    bill = Bill(id=9, name="Monthly minimum", amount=1.0, due_day=20, category="Credit Card Payment")
    assert cards_paid_by_bills([bill], [card], [debt]) == {7: {9}}
    paid = card_payoffs([card], [date(2026, 10, 23), date(2026, 11, 6)], "auto", TODAY, [debt], None, [], None, horizon_days=30)
    assert paid and all(e["name"].endswith("minimum") and e["amount"] == 1.0 for e in paid)


# ---- recurring: the usual day is the commonest of the last three; the typical amount is to the cent --------------

def test_a_monthly_series_keeps_its_commonest_day_and_a_cent_typical():
    from app.engine.recurring import detect

    rows = [{"date": d, "merchant": "Example Water", "amount": a, "category": "Bills & Utilities"}
            for d, a in ((date(2026, 7, 15), 40.00), (date(2026, 8, 15), 40.01), (date(2026, 9, 17), 40.02), (date(2026, 10, 15), 40.03))]
    found = detect(rows, TODAY)
    s = next(f for f in found if f["merchant"] == "Example Water")
    assert s["next_expected"] == date(2026, 11, 15)  # the 15th twice of the last three, not the 17th once
    assert s["typical_amount"] == round(s["typical_amount"], 2)


# ---- reports: a merchant's total adds its rows (reports.spending_summary) --------------------------------------

def test_the_top_merchants_add_their_rows():
    from app.engine.reports import spending_summary

    with Session(engine) as session:
        a = _checking(session)
        _tx(session, a.id, date(2026, 10, 2), "Example Grocer", 30.0, category="Groceries", note="a")
        _tx(session, a.id, date(2026, 10, 9), "Example Grocer", 45.0, category="Groceries", note="b")
        session.commit()
        out = spending_summary(session, TODAY, months=2)
    assert next(m for m in out["top_merchants"] if m["name"] == "Example Grocer")["total"] == 75.0


# ---- payoff: a note on money lent is kept to two hundred characters ----------------------------------------------

def test_a_note_on_money_lent_is_kept_to_two_hundred_characters():
    from app.engine.payoff import save_person_loan

    with Session(engine) as session:
        rows = save_person_loan(session, person="Example Friend", direction="lent", amount=50.0, on=TODAY, note="x" * 250)
    assert len(rows[-1]["note"]) == 200


# ---- borrowing: a $1 installment is a payment; one due today is due today; the year of fees is twelve months;
# the steps number from one; a notice that names its store finds that store's plan; a typed amount is to the cent --

def test_installment_streams_keep_a_dollar_payment_and_one_due_today():
    from app.engine.borrowing import installment_streams

    rows = [{"id": i, "date": d, "amount": 1.0, "provider": "Example Lender"} for i, d in enumerate((date(2026, 9, 16), date(2026, 9, 30)), 1)]
    out = installment_streams(rows, TODAY)
    assert len(out) == 1 and out[0]["next_date"] == TODAY  # Sep 30 + 14 = Oct 14: due today, not in two weeks


def test_the_borrowing_steps_number_from_one_and_the_year_of_fees_is_twelve_months():
    from app.engine.borrowing import borrowing_overview
    from app.engine.household import seed

    with Session(engine) as session:
        seed(session)
        out = borrowing_overview(session, TODAY)
    assert [s["n"] for s in out["steps"]] == list(range(1, len(out["steps"]) + 1)) and len(out["steps"]) >= 2
    fees = out["cost"]["fees_90d"]
    assert fees > 0 and out["cost"]["fees_year"] == round(round(fees * 30 / 90, 2) * 12, 2)  # the advance fees of a month, twelve times


def test_a_notice_that_names_its_store_finds_that_stores_plan():
    from app.engine.borrowing import plan_for_notice
    from app.models import InstallmentPlan

    with Session(engine) as session:
        for store in ("Example Shoes", "Example Lamps"):
            session.add(InstallmentPlan(provider="Affirm", merchant=store, total=100.0, installment=25.0, count=4, remaining=3, next_date=date(2026, 11, 1), cadence_days=14))
        session.commit()
        found = plan_for_notice(session, {"provider": "Affirm", "installment": 25.0, "merchant": "Example Lamps"})
    assert found is not None and found.merchant == "Example Lamps"


def test_a_typed_installment_is_kept_to_the_cent():
    from app.engine.borrowing import plan_from_fields

    with Session(engine) as session:
        plan = plan_from_fields(session, {"provider": "Example Lender"}, 33.333, 3, TODAY)
    assert plan.installment == 33.33 and plan.total == 100.0


def test_a_lift_under_a_dollar_is_not_suggested():
    from app.engine.decisions import billing_date_suggestions

    _suggestion_world(200.5)  # the move lifts the low point by fifty cents
    with Session(engine) as session:
        out = billing_date_suggestions(session, TODAY)
    assert out["suggestions"] == [] and out["plan"] == []


# ---- the snapshot: a payday today is on the calendar, named Payday when there is one check; a monthly earner's
# fourth window sets aside the fifth's bills; the picture groups what is past the eight largest; an advance taken
# back on payday is named; a bank alert from the start of the seven days counts, and twenty are listed --------------

def test_a_payday_today_is_on_the_calendar_as_payday(owner):
    with Session(engine) as session:
        _checking(session)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=TODAY))
        session.commit()
    events = owner.get("/api/v1/snapshot").json()["calendar"]["events"]
    assert [(e["date"], e["name"]) for e in events if e["kind"] == "paycheck"][0] == ("2026-10-14", "Payday")


def test_a_monthly_earners_fourth_window_sets_aside_the_fifths_bills(owner):
    with Session(engine) as session:
        _checking(session, 3000.0)
        session.add(Income(name="Example Employer", cadence="monthly", expected_net=4000.0, next_date=date(2026, 11, 1), pay_day=1))
        session.add(Bill(name="Example Rent", amount=1000.0, due_day=5, autopay=True))
        session.commit()
    windows = owner.get("/api/v1/snapshot").json()["windows"]
    assert [w["start"] for w in windows] == ["2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"]
    assert windows[3]["set_aside"]["total"] == 500.0  # March's rent, half of it kept by February's check (a large bill is kept by halves)


def test_the_picture_groups_what_is_past_the_eight_largest():
    from app.engine.snapshot import _bill_groups

    items = [{"name": f"Bill {i}", "amount": 100.0 + i, "kind": "bill", "funded": True} for i in range(9)]
    assert _bill_groups(items)["after_eight"] == {"total": 100.0, "count": 1}


def test_an_advance_taken_back_on_payday_is_named(owner):
    from app.models import Advance

    with Session(engine) as session:
        _checking(session, 2000.0)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Advance(provider="Example Advance", amount=100.0, fee=5.0, taken_on=date(2026, 10, 10), repay_date=date(2026, 10, 23)))
        session.commit()
    left = owner.get("/api/v1/snapshot").json()["safe_to_spend"]["left_out"]
    row = next(r for r in left if r["verb"] in ("takes back", "take back"))
    assert row["name"] == "Example Advance" and row["amount"] == 105.0


def test_bank_alerts_from_the_start_of_the_seven_days_count_and_twenty_are_listed(owner):
    import json

    from app.models import ExtractedFact

    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    with Session(engine) as session:
        _checking(session)
        session.add(ExtractedFact(kind="bank_alert", fields=json.dumps({"alert": "purchase", "amount": 12.5, "merchant": "Example Cafe", "message_date": "2026-10-07"}), status="auto", created_at=start))
        session.add(ExtractedFact(kind="bank_alert", fields=json.dumps({"alert": "purchase", "amount": 99.0, "merchant": "Example Old", "message_date": "2026-10-06"}), status="auto", created_at=start - timedelta(hours=1)))
        for i in range(21):
            session.add(ExtractedFact(kind="bank_alert", fields=json.dumps({"alert": "balance"}), status="auto", created_at=datetime(2026, 10, 13, 8, i, tzinfo=timezone.utc)))
        session.commit()
    snap = owner.get("/api/v1/snapshot").json()
    assert [i["amount"] for i in snap["in_flight"]["mail"]["items"]] == [12.5]  # midnight seven days back counts; the hour before it does not
    assert len(snap["mail"]["alerts"]) == 20


# ---- payments: a bill paid forty-five days ago is still on the paid list --------------------------------------

def test_the_paid_list_keeps_forty_five_days():
    from app.engine.payments import payments_overview

    with Session(engine) as session:
        bill = Bill(name="Example Gym", amount=30.0, due_day=30)
        session.add(bill)
        session.commit()
        session.add(Payment(bill_id=bill.id, due_date=TODAY - timedelta(days=45), amount=30.0, status="paid", paid_date=TODAY - timedelta(days=45)))
        session.add(Payment(bill_id=bill.id, due_date=TODAY - timedelta(days=46), amount=30.0, status="paid", paid_date=TODAY - timedelta(days=46)))
        session.commit()
        paid = payments_overview(session, TODAY)["paid"]
    assert [p["due_date"] for p in paid] == [(TODAY - timedelta(days=45)).isoformat()]


# ---- budgets: the days left, the bounds, an unknown paycheck's previous span, a semi-monthly earner's suggestion ----

def test_budget_days_left_and_bounds():
    from app.engine.budgets import budget_plan, paycheck_span, save_budget

    with Session(engine) as session:
        _budget_setup(session, 20.0)
        out = budget_plan(session, TODAY)
        assert out["span"]["days_left"] == out["span"]["days_total"] - out["span"]["days_gone"] > 0
        assert save_budget(session, "x" * 80, 100_000.0).category == "x" * 60
        with pytest.raises(ValueError):
            save_budget(session, "Example", 100_000.01)
    with Session(engine) as session:
        for inc in session.exec(select(Income)).all():
            session.delete(inc)
        session.commit()
        span = paycheck_span(session, TODAY)
    assert span["known"] is False and span["previous_start"] == span["start"] - timedelta(days=14)


def test_a_semimonthly_earners_budget_suggestion_is_eight_weeks_over_its_checks():
    from app.engine.budgets import budget_plan
    from app.models import CategoryBudget

    with Session(engine) as session:
        a = _checking(session)
        session.add(Income(name="Example Employer", cadence="semimonthly", expected_net=1500.0, next_date=date(2026, 10, 15)))
        session.add(CategoryBudget(category="Rent", per_paycheck=10.0))
        for i, d in enumerate((date(2026, 8, 20), date(2026, 9, 10), date(2026, 10, 1))):
            _tx(session, a.id, d, "Example Diner", 112.0, category="Dining", note=str(i))
        session.commit()
        out = budget_plan(session, TODAY)
    # $336 in the last 56 days, over 56 / (365 / 24) checks of a semi-monthly earner.
    assert out["suggestions"] == [{"category": "Dining", "per_paycheck": round(336.0 / (56 / (365 / 24)), 2)}]


# ---- cadence: the months around December run into January --------------------------------------------------

def test_the_months_around_december_include_january():
    from app.engine.cadence import _months_around

    assert _months_around(date(2026, 12, 10)) == [(2026, 11), (2026, 12), (2027, 1)]
    assert _months_around(date(2026, 1, 10)) == [(2025, 12), (2026, 1), (2026, 2)]


# ---- billdates: a weekly bill needs no anchor to be counted ---------------------------------------------------

def test_a_weekly_bill_is_never_unanchored():
    from app.engine.billdates import unanchored

    assert unanchored(Bill(name="Example Lawn", amount=20.0, cadence="weekly")) is False
    assert unanchored(Bill(name="Example Tax", amount=20.0, cadence="quarterly")) is True


# ---- Cover: a card exactly at the target on statement day is fine -------------------------------------------

def test_a_card_exactly_at_the_target_reports_good():
    from app.engine.cover import account_cover

    with Session(engine) as session:
        _checking(session, 3000.0)
        card = Account(name="Example Card", institution="Example Bank", kind="card", balance=300.0, connector="manual", credit_limit=1000.0)
        session.add(card)
        session.commit()
        session.add(Debt(name="Example Card", balance=300.0, minimum=35.0, due_day=28, statement_day=25, account_id=card.id))
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.commit()
        row = next(a for a in account_cover(session, TODAY)["accounts"] if a["role"] == "card")
    assert row["statement_utilisation"] == 0.3 and row["status"] == "good"


# ---- projection: a card balance is split across the two checks; the reserve is released at the third check ----

def test_a_card_balance_is_split_across_two_checks():
    from app.engine.projection import card_payoffs

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=300.0, connector="manual")
    paid = card_payoffs([card], [date(2026, 10, 23), date(2026, 11, 6)], "auto", TODAY, [], None, [], None, horizon_days=30)
    assert [e["amount"] for e in paid] == [150.0, 150.0]


def test_the_reserve_is_freed_at_the_third_check():
    from app.engine.projection import project

    pays = {date(2026, 10, 16), date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27)}
    path = project(TODAY, cash=1000.0, buffer=0.0, expected_net=500.0, pay_dates=pays, set_aside=300.0, events=[], per_day=0.0, days=50, reserved_for=set())
    by = {p["date"]: p["available"] for p in path}
    assert by["2026-11-12"] == 1700.0 and by["2026-11-13"] == 2500.0  # 300 held from Oct 16, free again on Nov 13


def test_the_caveat_for_one_unanchored_bill_is_singular():
    from app.engine.projection import projection_caveats

    text = projection_caveats(has_income=True, bills=1, unnamed=0, unanchored=1)[0]
    assert text.startswith("1 bill that comes every few months has no next due date")


# ---- reports: the window is at most twenty-four months; a plan with one payment left ends on that payment ----

def test_the_spending_window_is_capped_and_a_last_payment_ends_the_plan():
    from app.engine.reports import owed, spending_summary
    from app.models import InstallmentPlan

    with Session(engine) as session:
        assert len(spending_summary(session, TODAY, months=30)["months"]) == 24
        session.add(InstallmentPlan(provider="Example Lender", merchant="Example Store", total=100.0, installment=25.0, count=4, remaining=1, next_date=date(2026, 10, 30), cadence_days=14))
        session.commit()
        plan = next(i for i in owed(session, TODAY)["items"] if i.get("installment") == 25.0)
    assert plan["ends"] == "2026-10-30"


# ---- recurring: two quarterly charges are a quarterly series --------------------------------------------------

def test_two_quarterly_charges_are_a_series():
    from app.engine.recurring import detect

    rows = [{"date": d, "merchant": "Example Insurance", "amount": 240.0, "category": "Insurance"} for d in (date(2026, 4, 10), date(2026, 7, 10))]
    found = [f for f in detect(rows, TODAY, include_inactive=True) if f["merchant"] == "Example Insurance"]
    assert [f["cadence"] for f in found] == ["quarterly"]


# ---- borrowing: a last payment ends the plan; a notice the same day as the lender's list still counts; a gap of
# twenty days is every two weeks; a stream steps on by its own cadence; money lent can be edited at the first row --

def test_the_last_payment_marked_ends_the_plan():
    from app.engine.borrowing import mark_installment_paid
    from app.models import InstallmentPlan

    with Session(engine) as session:
        plan = InstallmentPlan(provider="Example Lender", merchant="Example Store", total=50.0, installment=25.0, count=2, remaining=1, next_date=date(2026, 10, 12), cadence_days=14)
        session.add(plan)
        session.commit()
        mark_installment_paid(session, plan, TODAY, TODAY)
        session.refresh(plan)
    assert plan.remaining == 0 and plan.active is False


def test_a_notice_from_the_day_of_the_lenders_list_is_not_older():
    from app.engine.borrowing import older_than_listing
    from app.store import set_setting

    with Session(engine) as session:
        set_setting(session, "affirm", {"period_end": "2026-10-10"})
        assert older_than_listing(session, {"provider": "Affirm", "message_date": "2026-10-10"}) is False
        assert older_than_listing(session, {"provider": "Affirm", "message_date": "2026-10-09"}) is True


def test_installment_gaps_of_twenty_days_are_every_two_weeks_and_a_stream_steps_by_its_cadence():
    from app.engine.borrowing import installment_streams

    rows = [{"id": i, "date": d, "amount": 30.0, "provider": "Example Lender"} for i, d in enumerate((date(2026, 8, 1), date(2026, 8, 21), date(2026, 9, 10)), 1)]
    s = installment_streams(rows, TODAY)[0]
    assert s["cadence_days"] == 14 and s["next_date"] == date(2026, 10, 22)  # Sep 10 + 14 + 14 + 14


def test_money_lent_can_be_edited_at_the_first_row():
    from app.engine.payoff import save_person_loan

    with Session(engine) as session:
        save_person_loan(session, person="Example Friend", direction="lent", amount=50.0, on=TODAY)
        rows = save_person_loan(session, person="Example Friend", direction="lent", amount=80.0, on=TODAY, index=0)
    assert [r["amount"] for r in rows] == [80.0]


def test_the_banks_row_for_the_last_payment_ends_the_plan():
    from app.engine.borrowing import refresh_plan_progress
    from app.models import InstallmentPlan

    with Session(engine) as session:
        a = _checking(session)
        plan = InstallmentPlan(provider="Affirm", merchant="Example Store", total=50.0, installment=25.0, count=2, remaining=1, next_date=date(2026, 10, 10), cadence_days=14)
        session.add(plan)
        _tx(session, a.id, date(2026, 10, 10), "AFFIRM PAYMENT", 25.0, category="Loan Payment")
        session.commit()
        refresh_plan_progress(session, TODAY)
        session.refresh(plan)
    assert plan.remaining == 0 and plan.active is False


# ---- projection: a moved bill's next charge is the nearest on the new day; a plan due today is not late; a card
# with no minimum makes no minimum payment; a card bill charged today is in the first payment ---------------------

def test_a_moved_bill_charges_on_the_nearest_new_day():
    from app.engine.projection import _moved_dates

    bill = Bill(id=1, name="Example Rent", amount=900.0, due_day=1)
    assert _moved_dates(bill, TODAY, date(2026, 12, 31), 4) == [date(2026, 11, 1), date(2026, 12, 4)]


def test_a_plan_payment_due_today_is_due_today_not_late():
    from app.engine.projection import build_events
    from app.models import InstallmentPlan

    plan = InstallmentPlan(id=1, provider="Example Lender", merchant="Example Store", total=100.0, installment=25.0, count=4, remaining=4, next_date=TODAY, cadence_days=14)
    first = sorted(build_events([], [plan], [], TODAY, horizon_days=30), key=lambda e: e["date"])[0]
    assert first["date"] == TODAY and not first.get("late")


def test_a_card_with_no_minimum_makes_no_minimum_payment():
    from app.engine.projection import card_events

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=200.0, connector="manual")
    debt = Debt(id=3, name="Example Card", balance=200.0, minimum=0.0, due_day=20, account_id=7)
    assert card_events([card], [date(2026, 10, 23), date(2026, 11, 6)], "minimum", TODAY, [debt]) == []


def test_a_card_bill_charged_today_is_in_the_first_payment():
    from app.engine.projection import card_payoffs

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=0.0, connector="manual")
    riding = [{"date": TODAY, "amount": 40.0, "from_account_id": 7, "name": "Example Stream"}]
    paid = card_payoffs([card], [date(2026, 10, 23), date(2026, 11, 6)], "auto", TODAY, [], None, riding, None, horizon_days=30)
    assert [e["amount"] for e in paid] == [40.0] and paid[0]["date"] == date(2026, 10, 23)


# ---- the borrowing plan: one payment is "payment", two loans left are "loans", a 0% loan is not paid early ------

def test_the_borrowing_plan_words_and_a_zero_rate_loan_paid_on_schedule(owner):
    import json as _json

    from app.models import InstallmentPlan

    def plan(merchant, amount, dates, apr):
        sched = [{"date": d, "amount": amount} for d in dates]
        return InstallmentPlan(provider="Affirm", merchant=merchant, total=amount * len(dates), installment=amount, count=len(dates) + 2, remaining=len(dates),
                               next_date=date.fromisoformat(dates[0]), cadence_days=30, schedule=_json.dumps(sched), apr=apr, autopay=True)

    with Session(engine) as session:
        _checking(session, 5000.0)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=3000.0, next_date=date(2026, 10, 23)))
        session.add_all([
            plan("Example Glasses", 100.30, ["2026-11-16"], 34.3),
            plan("Example Computer", 320.22, ["2026-11-16", "2026-12-16", "2027-01-16", "2027-02-16"], 31.48),
            plan("Example Chair", 50.00, ["2026-11-20", "2026-12-20", "2027-01-20"], 0.0),
        ])
        session.commit()
    out = owner.get("/api/v1/borrowing/plan").json()
    finish = next(s for s in out["steps"] if s["kind"] == "finish")["text"]
    assert "Example Glasses: 1 payment left" in finish and "you watch 2 loans instead of 3" in finish
    extra = [s["text"] for s in out["steps"] if s["kind"] == "extra"]
    assert extra and "Example Computer" in extra[0] and "Example Chair" not in extra[0]


# ---- more of the snapshot: what is over safe to spend at the usual pace; the board's week includes its seventh
# day; a $25 bill is not small; a $500 bill is kept by halves; a card bill on a window's last day or today is held ---

def test_the_usual_pace_over_safe_and_the_boards_seventh_day(owner):
    with Session(engine) as session:
        a = _checking(session, 1500.0)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Bill(name="Example Phone", amount=40.0, due_day=21, autopay=True))  # due in exactly seven days
        session.add(Bill(name="Example Water", amount=25.0, due_day=22, autopay=True))
        session.add(Bill(name="Example Paper", amount=25.0, due_day=26, autopay=True))  # in the first window, and not under $25
        for i in range(8):
            _tx(session, a.id, TODAY - timedelta(days=3 + 6 * i), "Example Grocer", 70.0, category="Groceries", note=str(i))
        session.commit()
    snap = owner.get("/api/v1/snapshot").json()
    pace = snap["usual_pace"]
    assert pace["per_window"] == round(pace["per_day"] * 14, 2)
    assert pace["over_safe"] == round(max(0.0, pace["per_window"] - snap["safe_to_spend"]["amount"]), 2)
    later = [e["name"] for e in snap["board"]["later"]["items"]]
    assert [e["name"] for e in snap["board"]["due_7"]["items"]] == ["Example Phone"] and "Example Water" in later and "Example Phone" not in later
    small = snap["windows"][0]["bills"]["small"]
    assert small["count"] == 0  # $25 is not under $25


def test_a_500_dollar_bill_is_kept_by_halves(owner):
    with Session(engine) as session:
        _checking(session, 3000.0)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Bill(name="Example Rent", amount=500.0, due_day=8, autopay=True))
        session.commit()
    windows = owner.get("/api/v1/snapshot").json()["windows"]
    kept = [i for w in windows for i in w["set_aside"]["items"] if i["name"] == "Example Rent"]
    assert kept and all(i["share"] == 0.5 and i["kept"] == 250.0 for i in kept)


def test_a_card_bill_due_today_is_on_the_card_and_a_window_holds_its_last_days_card_bills(owner):
    with Session(engine) as session:
        _checking(session, 3000.0)
        card = Account(name="Example Card", institution="Example Bank", kind="card", balance=0.0, connector="manual", credit_limit=2000.0)
        session.add(card)
        session.commit()
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.add(Bill(name="Example Stream", amount=15.0, due_day=14, autopay=True, pay_from_account_id=card.id))  # today
        session.add(Bill(name="Example Music", amount=11.0, due_day=5, autopay=True, pay_from_account_id=card.id))  # Nov 5, the first window's last day
        session.commit()
    snap = owner.get("/api/v1/snapshot").json()
    assert "Example Stream" in [e["name"] for e in snap["on_card"]["items"]]
    first = snap["windows"][0]
    assert first["end"] == "2026-11-05" and "Example Music" in [e["name"] for e in first["bills"]["items"]]


# ---- projection: a bill set to the end-of-month check belongs to that check's window --------------------------

def test_a_bill_on_the_end_of_month_check_belongs_to_that_window():
    from app.engine.projection import window_date

    assert window_date(date(2026, 11, 3), "end") == date(2026, 10, 30)  # rent due the 3rd, paid from the Oct 31 (Sat, so Fri 30) check
    assert window_date(date(2026, 11, 3), "due") == date(2026, 11, 3)


def test_split_card_payments_across_two_paydays_in_the_card_events():
    from app.engine.projection import card_events

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=300.0, connector="manual")
    out = card_events([card], [date(2026, 10, 23), date(2026, 11, 6)], "split", TODAY, [])
    assert [e["amount"] for e in out] == [150.0, 150.0]


def test_a_card_on_minimums_with_no_minimum_set_pays_nothing():
    from app.engine.projection import card_payoffs

    card = Account(id=7, name="Example Card", institution="Example Bank", kind="card", balance=300.0, connector="manual")
    debt = Debt(id=3, name="Example Card", balance=300.0, minimum=0.0, due_day=20, account_id=7)
    assert card_payoffs([card], [date(2026, 10, 23), date(2026, 11, 6)], "minimum", TODAY, [debt], None, [], None, horizon_days=30) == []


def test_the_caveat_for_one_pending_stream_is_singular():
    from app.engine.projection import projection_caveats

    assert projection_caveats(has_income=True, bills=1, unnamed=0, pending_installments=1) == ["1 pay-later stream is counted at its last amount until the payments left are set."]
    assert "so it is not counted" in projection_caveats(has_income=True, bills=1, unnamed=0, unanchored=1)[0]


# ---- decisions: the words of a purchase past the line and of a weekly bill's request -------------------------

def test_a_weekly_bills_request_says_every_week():
    from app.engine.decisions import _request

    instruction, draft = _request(Bill(id=1, name="Example Lawn", amount=20.0, due_day=3, cadence="weekly"), 22)
    assert "every week" in instruction + draft and "every two weeks" not in instruction + draft
    _, monthly = _request(Bill(id=2, name="Example Rent", amount=900.0, due_day=1), 22)
    assert "22nd" in monthly


# ---- reports: this month's spending in a category is this month's ----------------------------------------------

def test_a_categorys_this_month_is_this_months_rows_and_a_new_one_is_not_over_its_usual():
    from app.engine.reports import spending_summary

    with Session(engine) as session:
        a = _checking(session)
        _tx(session, a.id, date(2026, 8, 10), "Example Grocer", 100.0, category="Groceries", note="a")
        _tx(session, a.id, date(2026, 9, 10), "Example Grocer", 100.0, category="Groceries", note="b")
        _tx(session, a.id, date(2026, 10, 10), "Example Grocer", 60.0, category="Groceries", note="c")
        _tx(session, a.id, date(2026, 10, 11), "Example Hobby Shop", 40.0, category="Hobbies", note="d")
        session.commit()
        groups = {g["name"]: g for g in spending_summary(session, TODAY, months=3)["groups"]}
    assert groups["Groceries"]["this_month"] == 60.0 and groups["Groceries"]["total"] == 260.0
    assert groups["Hobbies"]["over_usual"] is False  # nothing in the full months: no usual to be over


# ---- recurring: a 99-cent subscription is a series; a gap of exactly 26 days is monthly; the oldest day in the
# window counts; a weekly series has no pay day of the month --------------------------------------------------

def test_small_and_boundary_series_are_found():
    from app.engine.recurring import detect

    cents = [{"date": date(2026, m, 3), "merchant": "Example Cloud", "amount": 0.99, "category": "Software"} for m in (7, 8, 9, 10)]
    assert [f["cadence"] for f in detect(cents, TODAY) if f["merchant"] == "Example Cloud"] == ["monthly"]
    weekly = [{"date": TODAY - timedelta(days=7 * k), "merchant": "Example Box", "amount": 12.0, "category": "Food"} for k in range(4)]
    w = next(f for f in detect(weekly, TODAY) if f["merchant"] == "Example Box")
    assert w["cadence"] == "weekly" and w["pay_day"] == 0
    tight = [{"date": date(2026, 7, 1) + timedelta(days=26 * k), "merchant": "Example Gas Co", "amount": 50.0, "category": "Utilities"} for k in range(4)]
    assert [f["cadence"] for f in detect(tight, TODAY) if f["merchant"] == "Example Gas Co"] == ["monthly"]
    edge = [{"date": TODAY - timedelta(days=30 * k), "merchant": "Example Mag", "amount": 9.0, "category": "News"} for k in range(4)]
    seen = next(f for f in detect(edge, TODAY, lookback_days=90) if f["merchant"] == "Example Mag")
    assert seen["charges_seen"] == 4  # the charge exactly ninety days back is inside the window


# ---- cadence: a semi-monthly run on the 15th fits both patterns; the 15th-and-last one is kept ---------------

def test_a_run_on_the_fifteenth_keeps_the_fifteenth_and_last_pattern():
    from app.engine.cadence import semimonthly_days_of

    assert semimonthly_days_of([date(2026, 7, 15), date(2026, 8, 14), date(2026, 9, 15)]) in ("", None)


# ---- borrowing: the extra pot is spent once; a schedule's next date is its first unpaid; a typed plan ends after its
# last; with no advances there is no cycle step; an AutoPay date of today counts; a statement sets the payments left --

def test_the_extra_is_spent_once_across_loans():
    from app.engine.borrowing_plan import simulate

    loans = [{"id": 1, "payoff_estimate": 100.0, "apr": 0.0, "payments": [{"date": "2026-10-20", "amount": 50.0}, {"date": "2026-11-20", "amount": 50.0}]},
             {"id": 2, "payoff_estimate": 300.0, "apr": 0.0, "payments": [{"date": f"2026-{m}-20", "amount": 100.0} for m in (10, 11, 12)]}]
    out = simulate(loans, 200.0, "finish_first", TODAY)
    # October: A pays 50 and B 100; the 200 finishes A (50) and puts 150 on B, leaving 50, paid in November.
    assert out["done"] == "2026-11"


def test_no_cycle_step_without_advances_and_no_fee_step_under_a_dollar():
    from app.engine.borrowing import borrowing_overview

    with Session(engine) as session:
        _checking(session)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23)))
        session.commit()
        out = borrowing_overview(session, TODAY)
    assert not any("The cycle runs on" in s["text"] for s in out["steps"])


def test_an_autopay_notice_dated_today_sets_todays_payment():
    import json as _json

    from app.engine.borrowing import apply_lender_notice
    from app.models import InstallmentPlan

    with Session(engine) as session:
        plan = InstallmentPlan(provider="Affirm", merchant="Example Store", total=75.0, installment=25.0, count=3, remaining=3, next_date=date(2026, 10, 1), cadence_days=30, loan_id="L-ap",
                               schedule=_json.dumps([{"date": "2026-10-14", "amount": 25.0}, {"date": "2026-11-14", "amount": 25.0}, {"date": "2026-12-14", "amount": 25.0}]),
                               schedule_as_of=date(2026, 10, 10))
        session.add(plan)
        session.commit()
        out = apply_lender_notice(session, {"notice": "autopay", "provider": "Affirm", "loan_id": "L-ap", "installment": 25.0, "autopay": True, "message_date": "2026-10-10",
                                            "autopay_schedule": [{"date": "2026-10-14", "amount": 25.0}, {"date": "2026-11-14", "amount": 25.0}]}, TODAY)
        got = (out.autopay, out.next_date, out.remaining)
    assert got == (True, TODAY, 2)  # a notice the same day as what the plan knows still counts, and today's date is a payment


def test_an_affirm_statement_sets_the_payments_left_and_retires_what_it_does_not_list():
    from app.engine.borrowing import apply_affirm_statement
    from app.models import InstallmentPlan

    with Session(engine) as session:
        typed = InstallmentPlan(provider="Affirm", merchant="Example Typed", total=90.0, installment=30.0, count=3, remaining=3, next_date=date(2026, 10, 20), cadence_days=30)
        session.add(typed)
        session.commit()
        parsed = {"period_end": "2026-10-10", "loans": [
            {"loan_id": "L-a", "merchant": "Example Shoes", "amount": 150.0, "apr": 10.0, "payment_amount": 25.0, "closing_balance": 75.0, "status": "active", "payment_due": "2026-10-25"},
            {"loan_id": "L-b", "merchant": "Example Lamp", "amount": 50.0, "apr": 0.0, "payment_amount": 25.0, "closing_balance": 20.0, "status": "active", "payment_due": "2026-10-26"},
            {"loan_id": "L-c", "merchant": "Example Mug", "amount": 10.0, "apr": 0.0, "payment_amount": 5.0, "closing_balance": 0.5, "status": "active", "payment_due": "2026-10-27"}],
            "transactions": [{"loan_id": "L-a", "description": "Payment"}, {"loan_id": "L-a", "description": "Payment"}, {"loan_id": "L-b", "description": "Payment"}], "interest": {}}
        apply_affirm_statement(session, parsed, TODAY)
        plans = {p.loan_id or p.merchant: p for p in session.exec(select(InstallmentPlan)).all()}
    assert (plans["L-a"].remaining, plans["L-a"].count) == (3, 5)  # 75 at 25 a payment, and the two this statement shows paid
    assert plans["L-b"].remaining == 1 and plans["L-c"].active is True
    assert (plans["Example Typed"].active, plans["Example Typed"].remaining) == (False, 0)


def test_an_unscheduled_plan_with_nothing_left_has_no_payments_ahead():
    from app.engine.borrowing_plan import _upcoming
    from app.models import InstallmentPlan

    plan = InstallmentPlan(provider="Example Lender", merchant="Example Store", total=50.0, installment=25.0, count=2, remaining=0, next_date=TODAY, cadence_days=14)
    assert _upcoming(plan, TODAY) == []


def test_refresh_with_nothing_to_refresh_changes_nothing():
    from app.engine.borrowing import refresh_plan_progress

    with Session(engine) as session:
        assert refresh_plan_progress(session, TODAY) == 0


# ---- Cover: the pace is not spent on the day the walk starts; the paycheck lands in the active income's account --

def test_the_walk_spends_no_pace_on_its_first_day():
    from app.engine.cover import _walk

    assert _walk(500.0, [], 10.0, TODAY, TODAY) == (500.0, TODAY)
    assert _walk(500.0, [], 10.0, TODAY, TODAY, pace_from_start=True) == (490.0, TODAY)


def test_the_deposit_account_is_the_active_paychecks():
    from app.engine.cover import _deposit_account

    with Session(engine) as session:
        big = _checking(session, 9000.0)
        small = Account(name="Example Second Checking", institution="Example Bank", kind="checking", balance=100.0, connector="manual")
        session.add(small)
        session.commit()
        session.add(Income(name="Example Old Job", cadence="biweekly", expected_net=900.0, next_date=date(2026, 10, 16), account_id=big.id, active=False))
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 23), account_id=small.id))
        session.commit()
        accounts = {a.id: a for a in session.exec(select(Account)).all()}
        assert _deposit_account(session, accounts, list(accounts.values())).id == small.id


# ---- payoff and budgets: the bounds of what can be typed ------------------------------------------------------

def test_money_lent_and_budget_bounds():
    from app.engine.budgets import save_budget
    from app.engine.payoff import save_person_loan

    with Session(engine) as session:
        assert save_person_loan(session, person="Example Friend", direction="lent", amount=1_000_000.0, on=TODAY)
        for bad in (0.0, 1_000_000.01):
            with pytest.raises(ValueError):
                save_person_loan(session, person="Example Friend", direction="lent", amount=bad, on=TODAY)
        assert save_budget(session, "Example Zero", 0.0).per_paycheck == 0.0


# ---- Needs you: four names are listed whole, and a notice exactly sixty days old is not yet set aside -----------

def test_four_names_are_listed_whole():
    from app.engine.needsyou import _names

    assert _names(["A", "B", "C", "D"]) == "A, B, C, D"
    assert _names(["A", "B", "C", "D", "E"]) == "A, B, C, D and 1 more"


def test_an_advance_repaid_since_the_last_sync_is_marked_repaid():
    from app.engine.borrowing import sync_borrowing
    from app.models import Advance

    with Session(engine) as session:
        a = _checking(session)
        session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1500.0, next_date=date(2026, 10, 16)))
        session.add(Transaction(account_id=a.id, date=date(2026, 10, 6), merchant="Earnin Activehours", raw_name="EARNIN ACTIVEHOURS", amount=-150.0, category="Financial", is_income=True, row_hash="adv-1"))
        session.commit()
        sync_borrowing(session, TODAY)
        assert session.exec(select(Advance)).one().repaid is False
        session.add(Transaction(account_id=a.id, date=date(2026, 10, 13), merchant="Earnin Activehours", raw_name="EARNIN ACTIVEHOURS", amount=161.99, category="Loan Payment", row_hash="adv-2"))
        session.commit()
        sync_borrowing(session, TODAY)
        row = session.exec(select(Advance)).one()
        assert row.repaid is True and row.fee == 11.99
