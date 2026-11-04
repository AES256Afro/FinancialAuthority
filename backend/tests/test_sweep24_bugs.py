"""Sweep 24: the findings of the correctness read of 0.10.8's engine, parsers and statement-by-mail path, and of the hand
model widened to every household (test_sweep24_bugs_hand.py), each pinned by its smallest case."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from types import SimpleNamespace

from sqlmodel import Session, select
from tests.test_affirm_statement import STATEMENT

from app.db import bump_version, engine
from app.engine.borrowing import apply_affirm_statement, borrowing_overview, refresh_plan_progress, statement_fact_from_file
from app.engine.snapshot import finance_snapshot
from app.models import Account, Bill, Debt, ExtractedFact, Income, InstallmentPlan, Paystub, Setting, Transaction
from app.parsers.affirm_statement import parse_affirm_statement

TODAY = date(2026, 10, 15)
# The month after STATEMENT: the computers loan paid off and closed, the optics loan a payment on.
NEWER = (STATEMENT.replace("September 15 - October 14, 2026", "October 15 - November 14, 2026")
         .replace("$293.46\n$198.18\n10/24/2026\n$101.81\nActive", "$198.18\n$99.00\n11/24/2026\n$101.81\nActive")
         .replace("$2835.55\n$2582.78\n10/23/2026\n$320.22\nActive", "$2582.78\n$0.00\n-\n-\nPaid Off")
         .replace("$74.29", "$0.00").replace("$11.56", "$2.10").replace("$85.85", "$2.10"))


def _plans(s: Session) -> dict[str, InstallmentPlan]:
    return {p.loan_id or p.merchant: p for p in s.exec(select(InstallmentPlan)).all()}


# ---- the lender's statement: read twice, read late, and what it does to the plans the owner typed --------------------


def test_an_older_statement_read_after_a_newer_one_opens_nothing_and_retires_nothing():
    """Two mailboxes carry the same mail, and a mailbox added later is read from its oldest message. The September
    statement read after October's put the $2,582.78 loan October closed back on the plan at $320.22 a month, rolled the
    optics loan back to September's balance and date, and retired the plan the owner typed in between."""
    with Session(engine) as s:
        apply_affirm_statement(s, parse_affirm_statement(NEWER), date(2026, 11, 20))
        typed = InstallmentPlan(provider="Affirm", merchant="Example Sofa", total=330.0, installment=33.0, count=10, remaining=10, next_date=date(2026, 12, 10), cadence_days=30)
        s.add(typed)
        s.commit()
        out = apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 11, 21))
        plans = _plans(s)
        computers = plans.get("BBBB-2222")
        assert computers is None or not computers.active, "a loan the newer statement closed is open again"
        optics = plans["AAAA-1111"]
        assert (optics.balance, optics.balance_at, optics.next_date) == (99.0, date(2026, 11, 14), date(2026, 11, 24))
        assert plans["Example Sofa"].active and plans["Example Sofa"].remaining == 10
        assert out["typed_retired"] == 0 and out["created"] == 0
        assert optics.interest_paid == round(2.10 + 11.56, 2)  # the older period's interest, once: the reader had not seen that month's yet
        apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 11, 22))
        assert _plans(s)["AAAA-1111"].interest_paid == round(2.10 + 11.56, 2)


def test_a_statement_older_than_the_lenders_list_read_on_screen_changes_nothing():
    """The owner read the lender's list on Oct 20. A statement closed Oct 14 arriving after that made a plan of a loan the
    list had closed and retired a plan typed since."""
    from app.store import set_setting

    with Session(engine) as s:
        set_setting(s, "baseline.affirm", {"as_of": "2026-10-20"})
        s.add(InstallmentPlan(provider="Affirm", merchant="Example Sofa", total=330.0, installment=33.0, count=10, remaining=10, next_date=date(2026, 12, 10), cadence_days=30))
        s.commit()
        out = apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 10, 25))
        assert out["created"] == 0 and out["typed_retired"] == 0 and list(_plans(s)) == ["Example Sofa"] and _plans(s)["Example Sofa"].active


def test_the_same_statement_read_twice_leaves_what_happened_since():
    """The same statement from the second mailbox a month on: the payment the ledger ticked off came back as still owed,
    the loan the owner ended was running again, and a plan typed after the first read was retired."""
    with Session(engine) as s:
        chk = Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk")
        s.add(chk)
        s.flush()
        apply_affirm_statement(s, parse_affirm_statement(STATEMENT), TODAY)
        s.add(Transaction(account_id=chk.id, date=date(2026, 10, 24), merchant="Affirm", raw_name="AFFIRM PAYMENT", amount=101.81, category="Borrowing", row_hash="optics-oct"))
        s.commit()
        refresh_plan_progress(s, date(2026, 10, 26))
        optics, computers = _plans(s)["AAAA-1111"], _plans(s)["BBBB-2222"]
        assert (optics.remaining, optics.next_date) == (1, date(2026, 11, 23))
        computers.active, computers.remaining = False, 0  # the owner ended it on Borrowing
        typed = InstallmentPlan(provider="Affirm", merchant="Example Sofa", total=330.0, installment=33.0, count=10, remaining=10, next_date=date(2026, 12, 10), cadence_days=30)
        s.add_all([computers, typed])
        s.commit()
        out = apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 10, 27))
        plans = _plans(s)
        assert (plans["AAAA-1111"].remaining, plans["AAAA-1111"].next_date) == (1, date(2026, 11, 23))
        assert not plans["BBBB-2222"].active and plans["BBBB-2222"].remaining == 0
        assert plans["Example Sofa"].active and out["typed_retired"] == 0
        assert out["updated"] == 2 and out["created"] == 0


def test_a_statements_period_reads_with_any_dash_and_one_with_no_date_is_not_applied():
    """A PDF's text layer may carry an en or em dash. Unread, the statement went in as of today: read again the next day
    it added the period's interest again and undid what had happened since."""
    period = "September 15 - October 14, 2026"
    for dash in ("\u2013", "\u2014"):
        assert parse_affirm_statement(STATEMENT.replace(period, period.replace("-", dash)))["period_end"] == "2026-10-14"
    assert parse_affirm_statement(STATEMENT.replace(period, "last month")) is None


def test_a_plan_named_after_the_banks_text_takes_the_statements_loan():
    """A plan named from a ledger stream carries the bank's words as its provider ("AFFIRM PAYMENT"). The statement looked
    for the provider "affirm" alone, so it made a second plan beside it: Oct 23 took the $320.22 payment twice, and a typed
    plan the statement does not list stayed on the plan."""
    with Session(engine) as s:
        s.add(Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk"))
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16)))
        s.add(InstallmentPlan(provider="AFFIRM PAYMENT", merchant="AFFIRM PAYMENT", total=0.0, installment=320.22, count=12, remaining=9, next_date=date(2026, 10, 23), cadence_days=30))
        s.add(InstallmentPlan(provider="Affirm.com", merchant="Affirm.com", total=0.0, installment=44.0, count=12, remaining=9, next_date=date(2026, 10, 23), cadence_days=30))
        s.commit()
        out = apply_affirm_statement(s, parse_affirm_statement(STATEMENT), TODAY)
        assert (out["created"], out["updated"], out["typed_retired"]) == (1, 1, 1)
        bump_version()
        items = [(e["amount"], e["date"]) for w in finance_snapshot(s, TODAY)["windows"][:1] for e in w["bills"]["items"]]
        assert sorted(items) == [(101.81, "2026-10-24"), (320.22, "2026-10-23")], items


def test_a_plan_from_mail_sent_after_the_statement_closed_is_not_overtaken_by_it():
    """A purchase approved on Oct 20, a statement that closed on Oct 14 read on Oct 22: it lists every loan as of its own
    last day, so it says nothing of this one. The plan was retired as "a guess the statement has overtaken" and its $40
    a month left the plan. A plan the mail made before the statement closed is overtaken, as before."""
    with Session(engine) as s:
        after = InstallmentPlan(provider="Affirm", merchant="Example Sofa", total=400.0, installment=40.0, count=10, remaining=10, next_date=date(2026, 11, 19), cadence_days=30, dates_estimated=True)
        before = InstallmentPlan(provider="Affirm", merchant="Example Chair", total=100.0, installment=25.0, count=4, remaining=2, next_date=date(2026, 10, 30), cadence_days=30, dates_estimated=True)
        s.add_all([after, before])
        s.flush()
        for plan, sent in ((after, "2026-10-20"), (before, "2026-09-30")):
            s.add(ExtractedFact(kind="installment", fields=json.dumps({"notice": "approved", "provider": "Affirm", "message_date": sent}), confidence=0.9, status="applied", applied_to=f"plan:{plan.id}", made=f"plan:{plan.id}:{plan.remaining}"))
        s.commit()
        out = apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 10, 22))
        plans = _plans(s)
        assert plans["Example Sofa"].active and plans["Example Sofa"].remaining == 10
        assert not plans["Example Chair"].active and out["typed_retired"] == 1


def test_a_statement_read_after_its_payment_posted_does_not_count_that_payment_again():
    """Read on Oct 25 with the Oct 24 payment already in the ledger: the plan stood at Oct 24 and came out of today's
    money as "not out of the account yet", $101.81 low on safe to spend until the next detection, up to six hours on."""
    with Session(engine) as s:
        chk = Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk")
        s.add(chk)
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 30)))
        s.flush()
        s.add(Transaction(account_id=chk.id, date=date(2026, 10, 24), merchant="Affirm", raw_name="AFFIRM PAYMENT", amount=101.81, category="Borrowing", row_hash="optics-oct"))
        s.commit()
        apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 10, 25))
        bump_version()
        snap = finance_snapshot(s, date(2026, 10, 25))
        assert not [e for e in snap["due_before_payday"]["items"] if e["source"].startswith("plan:") and e.get("late") and e["amount"] == 101.81]
        assert _plans(s)["AAAA-1111"].next_date == date(2026, 11, 23)


def test_a_payment_before_the_statement_closed_is_not_taken_off_what_the_statement_says_is_next():
    """The statement closed Oct 14 and says the next $101.81 is due Oct 16; a row of Oct 12 is inside its closing balance.
    It sat within five days of the next date, so it was ticked again: Oct 16's payment left the plan, $101.81 high."""
    text = STATEMENT.replace("$293.46\n$198.18\n10/24/2026", "$293.46\n$198.18\n10/16/2026")
    with Session(engine) as s:
        chk = Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk")
        s.add(chk)
        s.flush()
        s.add(Transaction(account_id=chk.id, date=date(2026, 10, 12), merchant="Affirm", raw_name="AFFIRM PAYMENT", amount=101.81, category="Borrowing", row_hash="optics-before"))
        s.commit()
        apply_affirm_statement(s, parse_affirm_statement(text), TODAY)
        assert _plans(s)["AAAA-1111"].next_date == date(2026, 10, 16) and _plans(s)["AAAA-1111"].remaining == 2
        s.add(Transaction(account_id=chk.id, date=date(2026, 10, 16), merchant="Affirm", raw_name="AFFIRM PAYMENT", amount=101.81, category="Borrowing", row_hash="optics-after"))
        s.commit()
        refresh_plan_progress(s, date(2026, 10, 17))
        assert _plans(s)["AAAA-1111"].next_date == date(2026, 11, 15) and _plans(s)["AAAA-1111"].remaining == 1


def test_a_statement_pdf_with_no_pdf_in_its_name_is_read():
    """An attachment typed application/pdf and named "Loans Statement": the mail path passes it on as a PDF, the reader
    went by the name alone and took its bytes for text, so the statement was not read."""
    from tests.test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    with Session(engine) as s:
        out = statement_fact_from_file(s, pdf_of(affirm_statement_lines(date(2026, 10, 31))), "Loans Statement", date(2026, 11, 1), "mail:statements@affirm.com")
        assert out and out["loans"] == 2


def test_the_lenders_statement_by_mail_twice_and_out_of_order_changes_nothing_but_an_unseen_periods_interest():
    """Through the mail path itself: the newest statement from one mailbox, the same from the second, then last month's."""
    from tests.test_sweep12_bugs_mail_quarter import affirm_statement_lines, pdf_of

    from app.connectors.mail import statement_from_attachments
    from app.mail.imap import Attachment, Parsed
    from app.models import MessageRef

    def mail(period_end: date) -> Parsed:
        data = pdf_of(affirm_statement_lines(period_end))
        return Parsed(message_id=f"<{period_end}@affirm.com>", sender="notices@affirm.com", sender_name="Affirm", subject="Your statement", date=None, text="", html="", attachments=[Attachment("Loans Statement", "application/pdf", len(data), data)])

    def state(s: Session) -> dict:
        s.expire_all()
        return {p.loan_id: (p.active, p.remaining, p.next_date, p.balance, p.balance_at, p.installment) for p in s.exec(select(InstallmentPlan)).all()}

    ref = MessageRef(account_id=1, folder="INBOX", uid=1, trust="ok")
    with Session(engine) as s:
        assert statement_from_attachments(s, mail(date(2026, 11, 14)), date(2026, 11, 15), "test", ref)
        newest, interest = state(s), {p.loan_id: p.interest_paid for p in s.exec(select(InstallmentPlan)).all()}
        kept = json.loads(s.get(Setting, "affirm").value)["period_end"]
        assert newest and kept == "2026-11-14"
        assert statement_from_attachments(s, mail(date(2026, 11, 14)), date(2026, 11, 16), "test", ref)
        assert state(s) == newest and {p.loan_id: p.interest_paid for p in s.exec(select(InstallmentPlan)).all()} == interest
        assert statement_from_attachments(s, mail(date(2026, 10, 14)), date(2026, 11, 17), "test", ref)
        assert state(s) == newest and json.loads(s.get(Setting, "affirm").value)["period_end"] == kept
        assert {p.loan_id: p.interest_paid for p in s.exec(select(InstallmentPlan)).all()} == {k: round(v + 2.0, 2) for k, v in interest.items()}  # the month it had not seen, once
        statement_from_attachments(s, mail(date(2026, 10, 14)), date(2026, 11, 18), "test", ref)
        assert {p.loan_id: p.interest_paid for p in s.exec(select(InstallmentPlan)).all()} == {k: round(v + 2.0, 2) for k, v in interest.items()}


def test_a_second_persons_typed_plan_is_not_the_owners_lenders_guess():
    """The owner's statement lists the owner's loans. A plan drawn from a second person's own account is theirs."""
    with Session(engine) as s:
        theirs = Account(name="Example Partner Checking", kind="checking", balance=300.0, person_id=7, connector="manual", external_id="p")
        s.add(theirs)
        s.flush()
        s.add(InstallmentPlan(provider="Affirm", merchant="Example Bike", total=500.0, installment=50.0, count=10, remaining=5, next_date=date(2026, 10, 20), cadence_days=30, pay_from_account_id=theirs.id))
        s.commit()
        apply_affirm_statement(s, parse_affirm_statement(STATEMENT), TODAY)
        assert _plans(s)["Example Bike"].active


# ---- Borrowing ---------------------------------------------------------------------------------------------------------


def test_each_plan_counts_once_in_what_installments_take_a_month():
    """Klarna's $49.50 plan, paid at $49.40 and $49.60 around a rounding edge, reads as two ledger streams and counted its
    $99 twice. Affirm's $50 plan, whose payments moved to an account the app cannot see, has a stream gone quiet and
    was not counted at all, though Debts and the plan's own row say $50. Rows say $149; the page said $198."""
    with Session(engine) as s:
        chk = Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk")
        s.add(chk)
        s.flush()
        s.add(InstallmentPlan(provider="Affirm", merchant="Example Store", total=500.0, installment=50.0, count=10, remaining=6, next_date=date(2026, 10, 20), cadence_days=30))
        s.add(InstallmentPlan(provider="Klarna", merchant="Example Shoes", total=200.0, installment=49.5, count=4, remaining=2, next_date=date(2026, 10, 22), cadence_days=14))
        for k, (d, name, amount) in enumerate(((date(2026, 6, 1), "Affirm", 50.0), (date(2026, 7, 1), "Affirm", 50.0), (date(2026, 8, 1), "Affirm", 50.0), (date(2026, 9, 10), "Klarna", 49.4), (date(2026, 9, 24), "Klarna", 49.6), (date(2026, 10, 8), "Klarna", 49.4))):
            s.add(Transaction(account_id=chk.id, date=d, merchant=name, raw_name=name.upper(), amount=amount, category="Borrowing", row_hash=f"lender-{k}"))
        # A stream with no plan counts while it runs and only then
        for k, d in enumerate((date(2026, 9, 1), date(2026, 10, 1))):
            s.add(Transaction(account_id=chk.id, date=d, merchant="Sezzle", raw_name="SEZZLE", amount=20.0, category="Borrowing", row_hash=f"sezzle-{k}"))
        s.add(Transaction(account_id=chk.id, date=date(2026, 4, 1), merchant="Afterpay", raw_name="AFTERPAY", amount=30.0, category="Borrowing", row_hash="afterpay-0"))
        s.commit()
        page = borrowing_overview(s, TODAY)
        assert page["cost"]["installments_month"] == round(50.0 + 99.0 + 20.0, 2), page["installments"]
        rows = [i for i in page["installments"] if i["plan_id"]]
        assert sorted(i["plan_id"] for i in rows) == [1, 2], "one row per plan"
        assert all(i["active"] for i in rows), "a plan on the register runs, whatever the ledger last showed of it"
        assert next(i for i in rows if i["plan_id"] == 2)["payments_seen"] == 3


def test_a_plan_with_no_stream_is_among_the_pages_sources():
    with Session(engine) as s:
        plan = InstallmentPlan(provider="Klarna", merchant="Example Shoes", total=200.0, installment=50.0, count=4, remaining=2, next_date=date(2026, 10, 22), cadence_days=30)
        s.add(plan)
        s.commit()
        assert f"plan:{plan.id}" in borrowing_overview(s, TODAY)["sources"]


def test_a_second_persons_own_plan_stays_out_of_borrowing_the_stairs_and_the_rollover():
    """Sweep 19 took a second person's own plan out of the owner's paycheck plan, and their advance rows out of Borrowing;
    0.10.7's register plans then listed theirs on the owner's page: "Installments take about $50 a month ... that payment
    is free", the stairs and the extra-payment figure counted it, and the saving ladder's rollover moved it to the owner's
    savings the day it ends."""
    from app.engine.borrowing_plan import borrowing_plan
    from app.engine.saving import saving_plan

    with Session(engine) as s:
        s.add(Account(name="Checking", kind="checking", balance=900.0, connector="manual", external_id="chk"))
        theirs = Account(name="Example Partner Checking", kind="checking", balance=300.0, person_id=7, connector="manual", external_id="p")
        s.add(theirs)
        s.flush()
        s.add(Income(name="Example Employer", cadence="biweekly", expected_net=2000.0, next_date=date(2026, 10, 16)))
        s.add(InstallmentPlan(provider="Klarna", merchant="Example Bike", total=300.0, installment=50.0, count=6, remaining=3, next_date=date(2026, 10, 20), cadence_days=30, pay_from_account_id=theirs.id))
        s.commit()
        page = borrowing_overview(s, TODAY)
        assert page["cost"]["installments_month"] == 0.0 and page["installments"] == [] and page["loans"] == []
        assert borrowing_plan(s, TODAY)["loans"] == []
        assert saving_plan(s, TODAY)["rollover"]["items"] == []


# ---- Cover -------------------------------------------------------------------------------------------------------------


def test_covers_second_window_reaches_the_second_payday_when_a_monthly_check_lands_early():
    """A month-end check that posted two days early: on Jul 29 the windows run to Aug 30 and Sep 29, sixty-two days out.
    Cover read sixty days of events: its second window lost the bills of Sep 28 and 29 ($570 of them) and, seeing one
    payday in reach, took the card's whole $1,000 on the first. The plan's own window says $500 on Aug 31."""
    today = date(2027, 7, 29)
    from app.engine.cover import account_cover

    with Session(engine) as s:
        chk = Account(name="Checking", kind="checking", balance=5000.0, connector="manual", external_id="c")
        card = Account(name="Card", kind="card", balance=1000.0, credit_limit=9000.0, connector="manual", external_id="k")
        s.add_all([chk, card])
        s.flush()
        s.add(Income(name="Monthly", cadence="monthly", expected_net=4000.0, next_date=date(2027, 7, 31), pay_day=31, account_id=chk.id))
        s.add(Transaction(account_id=chk.id, date=today, merchant="Monthly Payroll", raw_name="MONTHLY PAYROLL", amount=-4000.0, is_income=True, category="Income", row_hash="deposit"))
        s.add(Bill(name="Example Rent", amount=500.0, due_day=29, pay_from_account_id=chk.id, created_on=today))
        s.add(Bill(name="Example Dues", amount=70.0, due_day=28, cadence="yearly", anchor_date=date(2027, 9, 28), pay_from_account_id=chk.id, created_on=today))
        s.commit()
        bump_version()
        snap = finance_snapshot(s, today)
        check = next(a for a in account_cover(s, today)["accounts"] if a["role"] == "spend")
        assert check["windows"][1]["bills"] == snap["windows"][0]["bills"]["total"] == 1070.0, check["windows"][1]["items"]


# ---- a bill's days ------------------------------------------------------------------------------------------------------


def test_a_weekly_bill_with_no_next_date_keeps_its_days_whenever_it_is_asked():
    """The bills-list import and POST /bills take a weekly bill with a due day and no next date. Its days were stepped from
    the first due day on or after the day asked about: asked on Oct 14 it came Oct 20, 27, Nov 3; asked on Oct 21 its next
    charge was Nov 20, so four weeks of it left the plan the day after its day passed, and every period moved each month."""
    from app.engine.billdates import bill_dates

    for cadence, step in (("weekly", 7), ("biweekly", 14)):
        bill = Bill(name="Example Lessons", amount=40.0, due_day=20, cadence=cadence, created_on=date(2026, 9, 1))
        days = bill_dates(bill, date(2026, 10, 1), date(2027, 3, 1))
        assert all((b - a).days == step for a, b in zip(days, days[1:])) and days[0] < date(2026, 10, 1) + timedelta(days=step)
        for start in (date(2026, 10, 14), date(2026, 10, 21), date(2026, 11, 1), date(2027, 1, 31)):
            assert bill_dates(bill, start, date(2027, 3, 1)) == [d for d in days if d >= start], (cadence, start)
        assert bill_dates(Bill(name="Old", amount=40.0, due_day=20, cadence=cadence), date(2026, 10, 21), date(2026, 11, 20)) == [d for d in bill_dates(Bill(name="Old", amount=40.0, due_day=20, cadence=cadence), date(2026, 10, 1), date(2026, 11, 20)) if d >= date(2026, 10, 21)]
    # The first date stays the bill's own day, never before it was added
    assert bill_dates(Bill(name="New", amount=40.0, due_day=20, cadence="weekly", created_on=date(2026, 10, 10)), date(2026, 10, 1), date(2026, 10, 30)) == [date(2026, 10, 20), date(2026, 10, 27)]
    assert bill_dates(Bill(name="Set", amount=40.0, due_day=20, cadence="weekly", anchor_date=date(2026, 10, 15)), date(2026, 10, 21), date(2026, 11, 1)) == [date(2026, 10, 22), date(2026, 10, 29)]
    # One that starts later comes first on its day after it starts, added whenever: never a day off its due day
    later = Bill(name="Later", amount=40.0, due_day=20, cadence="weekly", created_on=date(2026, 9, 1), starts_on=date(2026, 11, 5))
    assert bill_dates(later, date(2026, 10, 1), date(2026, 12, 1)) == [date(2026, 11, 20), date(2026, 11, 27)]
    assert bill_dates(later, date(2026, 11, 22), date(2026, 12, 1)) == [date(2026, 11, 27)]


# ---- paystubs -----------------------------------------------------------------------------------------------------------


def _stub(d: date, net: float, lines: list[dict], gross: float | None = 2000.0, employer: str = "Example Employer") -> Paystub:
    return Paystub(employer=employer, pay_date=d, gross=gross, net=net, deductions=json.dumps(lines), stub_hash=f"s-{employer}-{d}")


def test_two_deduction_lines_of_one_name_add_up_in_what_moved():
    """The columns layout names "401K Pre-Tax" and "Roth 401K" both 401(k). What moved kept the last of each name: a
    pre-tax line up $100 was not named at all, and a Roth line up $50 read "401(k) $50 to $100" for a $150 to $200 line."""
    from app.engine.paystubs import lines_moved

    a = _stub(date(2026, 9, 25), 1500.0, [{"name": "401(k)", "label": "401K Pre-Tax", "amount": 100.0}, {"name": "401(k)", "label": "Roth 401K", "amount": 50.0}])
    b = _stub(date(2026, 10, 9), 1400.0, [{"name": "401(k)", "label": "401K Pre-Tax", "amount": 200.0}, {"name": "401(k)", "label": "Roth 401K", "amount": 50.0}])
    assert [(m["name"], m["before"], m["after"]) for m in lines_moved(b, a)] == [("401(k)", 150.0, 250.0)]


def test_a_stub_read_without_a_figure_is_not_a_figure_of_zero():
    """A stub with no gross or no deduction lines read says nothing of them. The stub after it said "gross pay $0 to $2,000;
    federal income tax $0 to $300" in the brief, beside a real short check, and left no room for the line that moved."""
    from app.engine.paystubs import lines_moved

    lines = [{"name": "Federal income tax", "amount": 300.0}, {"name": "Medical", "amount": 100.0}]
    full = _stub(date(2026, 10, 9), 1400.0, lines)
    assert lines_moved(full, _stub(date(2026, 9, 25), 1500.0, [], gross=None)) == []
    assert lines_moved(_stub(date(2026, 10, 23), 1500.0, [], gross=None), full) == []
    # A line that is new on a stub that lists its lines did move from nothing
    more = _stub(date(2026, 10, 23), 1300.0, [*lines, {"name": "Garnishment", "amount": 100.0}])
    assert [(m["name"], m["before"], m["after"]) for m in lines_moved(more, full)] == [("Garnishment", 0.0, 100.0)]


def test_two_paychecks_each_keep_their_own_usual_net():
    """Two jobs: $1,000 every two weeks, $3,000 a month. The usual net was the median of every stub whoever paid it, so
    each $1,000 stub read "$1,000 under the usual net of $2,000" in the brief every payday, and the third stub set the
    first paycheck's expected net to that mix: the plan then counted $3,000 or $1,000 for the wrong check."""
    from app.engine.paystubs import expected_net, paystubs_overview, record_paystub

    def fields(d: date, net: float, employer: str) -> dict:
        return {"pay_date": d.isoformat(), "net": net, "gross": round(net * 1.4, 2), "employer": employer, "deductions": [{"name": "Federal income tax", "amount": round(net * 0.4, 2)}]}

    with Session(engine) as s:
        small = Income(name="Example Cafe", cadence="biweekly", expected_net=1000.0, next_date=date(2026, 10, 30))
        big = Income(name="Northwind Tools", cadence="monthly", expected_net=3000.0, next_date=date(2026, 10, 31), pay_day=31)
        s.add_all([small, big])
        s.commit()
        for d, net, who in ((date(2026, 8, 7), 1000.0, "Example Cafe LLC"), (date(2026, 8, 21), 1000.0, "Example Cafe LLC"), (date(2026, 8, 31), 3000.0, "NORTHWIND TOOLS INC"), (date(2026, 9, 4), 1000.0, "Example Cafe LLC"),
                            (date(2026, 9, 18), 1000.0, "Example Cafe LLC"), (date(2026, 9, 30), 3000.0, "NORTHWIND TOOLS INC"), (date(2026, 10, 2), 1000.0, "Example Cafe LLC"), (date(2026, 10, 16), 1000.0, "Example Cafe LLC")):
            record_paystub(s, fields(d, net, who), "upload", None, TODAY)
        s.refresh(small)
        s.refresh(big)
        assert (small.expected_net, big.expected_net) == (1000.0, 3000.0)
        got = paystubs_overview(s)
        assert got["short"] == [], [(p["pay_date"], p["difference"]) for p in got["short"]]
        assert all(abs(p["difference"]) < 1.0 for p in got["recent"]), [(p["pay_date"], p["difference"]) for p in got["recent"]]
        assert expected_net(s, date(2026, 10, 31), "Northwind Tools Inc", 2500.0) == 3000.0
        assert not [row["moved"] for row in got["recent"] if row["moved"]], "a stub read against the other job's"
        # One paycheck, under whatever names its stubs carry, is judged as before
    from sqlmodel import SQLModel

    from app.db import init_db

    SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as s:
        only = Income(name="Example Cafe", cadence="biweekly", expected_net=1000.0, next_date=date(2026, 10, 30))
        s.add(only)
        s.commit()
        for d, who in ((date(2026, 9, 4), "Payroll Services Inc"), (date(2026, 9, 18), "Example Cafe"), (date(2026, 10, 2), "ACME PAYROLL")):
            record_paystub(s, fields(d, 1100.0, who), "upload", None, TODAY)
        s.refresh(only)
        assert only.expected_net == 1100.0


# ---- a saved rule -------------------------------------------------------------------------------------------------------


def test_an_optional_group_of_alternatives_still_matches():
    """`(amzn|amazon)? mktp` is linear: an optional group runs at most once. 0.10.7 refuses a quantified group that holds an
    alternation, and read `?` as a repeat, so the owner's rule silently stopped filing its rows. What repeats is still
    refused, an optional group inside a repeated one too."""
    from app.engine.rules import matches

    def row(merchant: str) -> SimpleNamespace:
        return SimpleNamespace(merchant=merchant, raw_name=merchant, description="")

    for pattern, merchant in ((r"re:(amzn|amazon)? *mktp", "AMZN Mktp US"), (r"re:(amzn|amazon)? *mktp", "Mktp US"), (r"re:^(sq|tst)?\s*\*?\s*joes", "SQ *JOES COFFEE"), (r"re:acme( inc| llc)?$", "ACME LLC"), (r"re:(?:the |a )?home depot", "The Home Depot")):
        assert matches(SimpleNamespace(match_field="merchant", pattern=pattern), row(merchant)), pattern
    for bad in (r"re:(a|aa)+$", r"re:(a+)+$", r"re:(?:a|aa)*b", r"re:(\w+\s?)*$", r"re:(?x)(a|aa) +$", r"re:((a|aa)?)+$", r"re:((?:a|aa)?)*b", r"re:(a|aa){2,}$", r"re:((a+)?)+$"):
        assert not matches(SimpleNamespace(match_field="merchant", pattern=bad), row("aaaa")), bad


def test_a_rule_too_deep_or_too_large_to_compile_is_refused_and_stops_nothing():
    """Parsing such a pattern raises RecursionError or OverflowError, not re.error: one saved rule stopped every rule run."""
    from app.engine.rules import matches

    row = SimpleNamespace(merchant="aaaa", raw_name="aaaa", description="")
    for bad in ("re:" + "(" * 3000 + "a" + ")" * 3000, "re:a{99999999999}"):
        assert not matches(SimpleNamespace(match_field="merchant", pattern=bad), row)


# ---- the parsers: every layout, every kind of blank ----------------------------------------------------------------------


def test_a_label_split_by_a_non_breaking_space_or_a_tab_is_read():
    """An HTML paystub or lender email writes "Net&nbsp;Pay" to keep a label on one line, and the mail reader keeps the
    character. Every label is matched with a plain space, so such a stub was not read as a stub at all, and such a loan
    confirmation not as a loan. The rewritten line breaks read \\r\\n, tabs and non-breaking spaces at line ends and
    between lines as before (test_the_rewritten_patterns_read_what_the_old_ones_read)."""
    from tests.test_sweep7_bugs_pure import CONFIRMATION, LOAN_SCHEDULE, STUB_DEDUCTIONS, STUB_TEXT, STUB_TRUTH

    from app.parsers.affirm import parse_autopay, parse_loan_confirmation, parse_plan_approved
    from app.parsers.paystub import looks_like_paystub, parse_paystub_text

    for name, swap in (("nbsp", lambda t: t.replace(" ", "\xa0")), ("tab", lambda t: t.replace(" ", "\t")), ("thin", lambda t: t.replace(" ", " "))):
        for ending in ("\n", "\r\n"):
            stub = swap(STUB_TEXT).replace("\n", ending)
            assert looks_like_paystub(stub), name
            got = parse_paystub_text(stub, TODAY)
            assert got and {k: got[k] for k in STUB_TRUTH} == STUB_TRUTH, (name, got)
            assert {d["name"]: d["amount"] for d in got["deductions"]} == STUB_DEDUCTIONS, name
            loan = parse_loan_confirmation(swap(CONFIRMATION).replace("\n", ending), TODAY)
            assert loan and loan["confidence"] == 0.95 and {s["date"]: (s["amount"], s["principal"], s["interest"]) for s in loan["schedule"]} == LOAN_SCHEDULE, (name, loan)
            assert loan["merchant"] == swap("Example Outfitters").replace("\xa0", " ").replace("\t", " ").replace(" ", " ") and loan["loan_id"] == "9ABC-DEF2"
    assert parse_autopay("You've\xa0enabled\xa0AutoPay", "AutoPay\xa0schedule\nOct\xa023,\xa02026 $242.32\n", TODAY)["installment"] == 242.32
    assert parse_plan_approved("Your\xa0payment\xa0plan\xa0at\xa0Example\xa0Store\xa0is\xa0approved", "$83.76\xa0every\xa0month\xa0for\xa03\xa0months")["installment"] == 83.76
    columns = "EARNINGS\xa0STATEMENT\nCompany:\xa0ABC12 - Example Employer Inc   Emp #: 1\nPay\xa0Date:\xa010/16/2026\nCurrent\xa0Period\nGross\xa0Pay 3092.55 61851.00\nNet\xa0Pay 2364.52 47290.40\n"
    got = parse_paystub_text(columns, TODAY)
    assert got and got["layout"] == "columns" and (got["gross"], got["net"], got["pay_date"]) == (3092.55, 2364.52, "2026-10-16")
    statement = parse_affirm_statement(STATEMENT.replace("Affirm Loans Statement for September 15 - October 14, 2026", "Affirm\xa0Loans\xa0Statement\xa0for\xa0September\xa015 - October\xa014,\xa02026").replace("Loans Summary", "Loans\xa0Summary"))
    assert statement and statement["period_end"] == "2026-10-14" and len(statement["loans"]) == 3


def test_the_rewritten_patterns_read_what_the_old_ones_read():
    """The linear rewrites of 0.10.7 against the patterns they replaced, on the fixtures' documents and the mutation
    sets' damage, under every line ending and blank the mail reader can hand over: no layout was lost."""
    import re

    from tests.test_sweep7_bugs_pure import CONFIRMATION, STUB_TEXT, _mutate

    from app.mail.classify import DATE_WORDS
    from app.parsers import affirm
    from app.parsers.paystub import _columns_layout

    money = r"\$\s?([\d,]+\.\d{2})"
    old = {"loan": re.compile(money + r"\s*\n\s*Loan amount", re.IGNORECASE), "total": re.compile(money + r"\s*\n\s*Total of payments", re.IGNORECASE), "finance": re.compile(money + r"\s*\n\s*Finance charge", re.IGNORECASE),
           "entry": re.compile(DATE_WORDS.pattern + r"\s*\n\s*" + money + r"\s*\n\s*Principal:\s*" + money + r"\s*\n\s*Interest:\s*" + money, re.IGNORECASE)}
    new = {"loan": affirm.LOAN_AMOUNT, "total": affirm.TOTAL, "finance": affirm.FINANCE, "entry": affirm.ENTRY}
    old_hours = re.compile(r"(?:regular|reg)\s*(?:hours|hrs)\s*[:\s]\s*(\d{1,3}(?:\.\d{1,2})?)", re.IGNORECASE)
    new_hours = re.compile(r"(?:regular|reg)\s*+(?:hours|hrs)(?=[:\s])\s*+:?\s*+(\d{1,3}(?:\.\d{1,2})?)", re.IGNORECASE)
    old_columns = re.compile(r"earnings statement.*pay date:.*(?:current period|net pay distribution)", re.IGNORECASE | re.DOTALL)
    blanks = {
        "crlf": lambda t: t.replace("\n", "\r\n"), "trailing space": lambda t: t.replace("\n", "   \n"), "trailing tab": lambda t: t.replace("\n", "\t\n"), "trailing nbsp": lambda t: t.replace("\n", "\xa0\n"),
        "crlf and nbsp": lambda t: t.replace("\n", "\xa0\r\n"), "leading nbsp": lambda t: t.replace("\n", "\n\xa0"), "blank nbsp line": lambda t: t.replace("\n\n", "\n\xa0\n"), "tabs": lambda t: re.sub(r" {2,}", "\t", t),
        "nbsp runs": lambda t: re.sub(r" {2,}", lambda m: "\xa0" * len(m.group()), t), "bare cr": lambda t: t.replace("\n", "\r"), "form feed": lambda t: t.replace("\n", "\f\n"), "as is": lambda t: t,
    }
    rng = random.Random(24)
    for _ in range(400):
        text = rng.choice(list(blanks.values()))(_mutate(rng, CONFIRMATION, rng.choice(["Total: $1,000.00", "$999.99", "Jan 1, 2027"]))[0])
        for key, pattern in old.items():
            assert [m.groups() for m in pattern.finditer(text)] == [m.groups() for m in new[key].finditer(text)], (key, text)
        stub = rng.choice(list(blanks.values()))(_mutate(rng, STUB_TEXT, rng.choice(["Total Deductions  740.77  14,815.40", "Regular Hours: 80", "reg hrs\t12.5"]))[0])
        got_old, got_new = old_hours.search(stub), new_hours.search(stub)
        assert (got_old.group(1) if got_old else None) == (got_new.group(1) if got_new else None), stub
        assert bool(old_columns.search(stub)) == _columns_layout(stub), stub
    for word in ("Regular", "reg"):
        for sep in ("", " ", "\t", "\xa0", "\r\n"):
            for tail in (":", " :", ": ", " ", "\t", "\xa0", "\r\n", "::", "", ":\xa0", "\xa0:\xa0"):
                text = f"{word}{sep}Hours{tail}80.5"
                got_old, got_new = old_hours.search(text), new_hours.search(text)
                assert (got_old.group(1) if got_old else None) == (got_new.group(1) if got_new else None), repr(text)


# ---- what the statement left on the register stays right from the register ----------------------------------------------


def test_the_debt_row_of_no_interest_keeps_its_setting_untouched_by_an_older_statement():
    """The newest statement's own line on Borrowing (its period and interest) is not moved by an older one, as before."""
    with Session(engine) as s:
        apply_affirm_statement(s, parse_affirm_statement(NEWER), date(2026, 11, 20))
        apply_affirm_statement(s, parse_affirm_statement(STATEMENT), date(2026, 11, 21))
        kept = json.loads(s.get(Setting, "affirm").value)
        assert kept["period_end"] == "2026-11-14" and kept["interest_period"] == 2.10
        assert s.exec(select(Debt)).first() is None
