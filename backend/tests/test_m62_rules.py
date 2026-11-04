"""M62, Confirm less, trust more: what waits on Needs you is answered by a rule only where nothing the plan counts changes
or a second source says the same; a sender's earned way files and never moves a figure; everything a rule did is named,
listed and undone by a tap; only the owner turns a rule on; and Skip all takes back what it takes. The invariants of
round five, as property tests: nothing counts twice, nothing counts without a source, nothing is confirmed by anything
but the owner or a rule the owner can see and switch off."""

import json
import os
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import Session, select

from app import auth
from app.connectors.mail import vouched_domains
from app.db import engine
from app.engine import autokeep
from app.engine.household import seed
from app.engine.needsyou import triage
from app.engine.next_steps import next_steps
from app.engine.snapshot import _finance_snapshot
from app.jobs.scheduler import jobs
from app.main import app
from app.models import Account, Advance, BalanceSnapshot, Bill, Debt, Document, ExtractedFact, Income, InstallmentPlan, MailAccount, MessageRef, Payment, Paystub, Transaction
from app.store import get_setting

from tests.conftest import TODAY

GOLDEN = Path(__file__).parent / "golden" / "autokeep.json"
NOT_OK = ("warn", "crit", "unknown", "")
_uid = [1000]


# ---- the mail, as a sync leaves it --------------------------------------------------------------------------------


def mailbox(s: Session, person_id: int | None = None) -> int:
    box = MailAccount(label="Example box", kind="imap", host="imap.example.test", port=993, username="owner@example.test", password_enc="", person_id=person_id)
    s.add(box)
    s.commit()
    return box.id


def message(s: Session, box: int, sender: str, name: str, *, trust: str = "ok", day: date = TODAY, subject: str = "A message") -> MessageRef:
    _uid[0] += 1
    ref = MessageRef(account_id=box, folder="INBOX", uid=_uid[0], sender=sender, sender_name=name, subject=subject, date=datetime(day.year, day.month, day.day, 12, tzinfo=UTC), trust=trust, trust_why="")
    s.add(ref)
    s.commit()
    return ref


def said(s: Session, box: int, kind: str, sender: str, name: str, *, trust: str = "ok", day: date = TODAY, **fields) -> ExtractedFact:
    ref = message(s, box, sender, name, trust=trust, day=day)
    fact = ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps({"sender": sender, "sender_name": name, "message_date": day.isoformat(), **fields}), confidence=0.9, status="pending", excerpt=f"{name}: the words kept")
    s.add(fact)
    s.commit()
    return fact


def answered(s: Session, box: int, kind: str, sender: str, name: str, *, by: str = "owner", status: str = "confirmed", applied_to: str = "", trust: str = "ok", day: date = TODAY, **fields) -> ExtractedFact:
    fact = said(s, box, kind, sender, name, trust=trust, day=day, **fields)
    fact.status, fact.applied_to, fact.resolved_by, fact.resolved_at = status, applied_to, by, datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    s.add(fact)
    s.commit()
    return fact


def earn(s: Session, box: int, kind: str, sender: str, name: str, amount: float | None, *, applied_to: str = "") -> None:
    for back in (30, 20, 10):
        extra = {"amount": amount} if amount is not None else {}
        answered(s, box, kind, sender, name, day=TODAY - timedelta(days=back), applied_to=applied_to, **extra)


def live_account(s: Session, name: str = "Everyday Checking", institution: str = "Example Bank", kind: str = "checking", balance: float = 1000.0, at: date = TODAY) -> Account:
    a = Account(name=name, institution=institution, kind=kind, connector="plaid", balance=balance, balance_at=datetime(at.year, at.month, at.day, 8, tzinfo=UTC))
    s.add(a)
    s.commit()
    return a


def row(s: Session, account: Account, merchant: str, amount: float, day: date = TODAY, **flags) -> Transaction:
    _uid[0] += 1
    t = Transaction(account_id=account.id, date=day, merchant=merchant, raw_name=merchant, amount=amount, row_hash=f"m62-{_uid[0]}", source="plaid", **flags)
    s.add(t)
    s.commit()
    return t


def registers(s: Session) -> str:
    """Everything the plan counts, as it stands."""
    out = {}
    for model, fields in ((Bill, ("id", "name", "amount", "due_day", "active", "autopay")), (InstallmentPlan, ("id", "provider", "installment", "remaining", "active", "autopay")), (Paystub, ("id", "net")), (Income, ("id", "amount")),
                          (Debt, ("id", "balance", "minimum")), (Account, ("id", "balance", "kind", "active")), (Advance, ("id", "amount", "repaid")), (Payment, ("id", "status", "amount", "transaction_id")),
                          (Transaction, ("id", "amount", "is_transfer", "is_ignored", "is_card_payment", "category"))):
        out[model.__tablename__] = sorted([[str(getattr(r, f, "")) for f in fields] for r in s.exec(select(model)).all()])
    return json.dumps(out, sort_keys=True)


def state(s: Session, fact_id: int) -> ExtractedFact:
    s.expire_all()
    return s.get(ExtractedFact, fact_id)


def person_and_key() -> dict[str, TestClient]:
    owner = TestClient(app)
    assert owner.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).status_code == 200
    owner.headers["X-Requested-With"] = "FinancialAuthority"
    made = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"})
    assert made.status_code == 200, made.text
    person = TestClient(app)
    person.headers["X-Requested-With"] = "FinancialAuthority"
    assert person.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": "maple harbour winter nine"}).status_code == 200
    with Session(engine) as s:
        _, token = auth.create_key(s, "Example Helper", {"read", "confirm"}, "full")
    key = TestClient(app)
    key.headers["Authorization"] = f"Bearer {token}"
    return {"owner": owner, "person": person, "key": key}


# ---- each path acts, and only where a second source says the same ---------------------------------------------------


def test_a_receipt_matching_one_row_within_a_day_is_kept_against_it():
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s)
        t = row(s, acct, "Example Store", 42.50, TODAY - timedelta(days=1))
        fact = said(s, box, "receipt", "orders@examplestore.test", "Example Store", total=42.50, bought_on=TODAY.isoformat(), merchant="Example Store", order_no="A-1234")
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.RECEIPT: 1}
        fact = state(s, fact.id)
        assert fact.status == "applied" and fact.resolved_by == f"rule:{autokeep.RECEIPT}" and "Example Store" in fact.rule_why and fact.made == fact.applied_to
        doc = s.get(Document, int(fact.applied_to.split(":")[1]))
        assert doc.attach_to == f"transaction:{t.id}" and doc.kind == "receipt" and doc.amount == 42.50 and registers(s) == before


@pytest.mark.parametrize("case", ["two rows", "two days off", "other amount", "other store", "no store", "row has one", "two receipts", "refund", "pending row", "transfer"])
def test_a_receipt_waits_where_the_ledger_does_not_say_one_row(case):
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s)
        day, total, merchant = TODAY, 42.50, "Example Store"
        if case == "two days off":
            day = TODAY - timedelta(days=2)
        if case == "other amount":
            total = 43.50
        if case == "other store":
            merchant = "Another Shop"
        amount = -42.50 if case == "refund" else 42.50
        t = row(s, acct, "Example Store", amount, day, **({"pending": True} if case == "pending row" else {"is_transfer": True} if case == "transfer" else {}))
        if case == "two rows":
            row(s, acct, "Example Store Online", 42.50, TODAY)
        if case == "row has one":
            s.add(Document(title="x", kind="receipt", attach_to=f"transaction:{t.id}", filename="x.txt", content_type="text/plain", size=1, sha256="0" * 64))
            s.commit()
        fields = {"total": total if case != "other amount" else 43.50, "bought_on": TODAY.isoformat(), "merchant": "" if case == "no store" else merchant}
        fact = said(s, box, "receipt", "orders@examplestore.test", "Example Store", **fields)
        second = said(s, box, "receipt", "orders@examplestore.test", "Example Store", **fields) if case == "two receipts" else None
        assert autokeep.run(s, TODAY)["total"] == 0
        assert state(s, fact.id).status == "pending" and (second is None or state(s, second.id).status == "pending")


def test_a_statement_within_a_dollar_of_the_bank_is_a_reading_and_never_the_balance():
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s, balance=1000.0)
        fact = said(s, box, "statement", "statements@examplebank.test", "Example Bank", amount=1000.75, day=TODAY - timedelta(days=2))
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.STATEMENT: 1}
        fact = state(s, fact.id)
        assert fact.applied_to == f"account:{acct.id}" and fact.resolved_by == f"rule:{autokeep.STATEMENT}" and "within a dollar" in fact.rule_why
        rows = s.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == acct.id)).all()
        assert [(r.date, r.balance) for r in rows] == [(TODAY - timedelta(days=2), 1000.75)]
        assert registers(s) == before and s.get(Account, acct.id).balance == 1000.0


@pytest.mark.parametrize("case", ["over a dollar", "manual account", "old reading", "other name", "two accounts"])
def test_a_statement_waits_unless_one_live_account_agrees(case):
    with Session(engine) as s:
        box = mailbox(s)
        amount, acct = 1000.0, live_account(s, balance=1000.0)
        if case == "over a dollar":
            amount = 1001.01
        if case == "manual account":
            acct.connector = "manual"
            s.add(acct)
            s.commit()
        if case == "two accounts":
            live_account(s, name="Everyday Savings", kind="savings", balance=1000.5)
        day = TODAY - timedelta(days=40) if case == "old reading" else TODAY
        name = "Another Bank" if case == "other name" else "Example Bank"
        fact = said(s, box, "statement", "statements@examplebank.test", name, amount=amount, day=day)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"
        assert not s.exec(select(BalanceSnapshot)).all()


def test_a_day_the_bank_has_read_keeps_the_banks_reading():
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s, balance=1000.0)
        s.add(BalanceSnapshot(account_id=acct.id, date=TODAY, balance=1000.0))
        s.commit()
        fact = said(s, box, "statement", "statements@examplebank.test", "Example Bank", amount=1000.5)
        assert autokeep.run(s, TODAY)["total"] == 1 and state(s, fact.id).made == ""
        assert [r.balance for r in s.exec(select(BalanceSnapshot)).all()] == [1000.0]


def paid_period(s: Session, name: str = "Example Power", amount: float = 80.0, *, day: date = TODAY, with_row: bool = True) -> Payment:
    bill = Bill(name=name, amount=amount, due_day=day.day)
    s.add(bill)
    s.flush()
    t = row(s, live_account(s, "Bills Checking", "Another Credit Union", balance=1.0), name, amount, day) if with_row else None
    p = Payment(bill_id=bill.id, due_date=day, amount=amount, status="paid", paid_date=day, transaction_id=t.id if t else None)
    s.add(p)
    s.commit()
    return p


def test_a_payment_confirmation_a_paid_period_explains_is_filed():
    with Session(engine) as s:
        box = mailbox(s)
        p = paid_period(s)
        fact = said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0, paid_date=TODAY.isoformat())
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.CONFIRMATION: 1}
        fact = state(s, fact.id)
        assert fact.status == "confirmed" and fact.applied_to == f"payment:{p.id}" and registers(s) == before


@pytest.mark.parametrize("case", ["other amount", "other biller", "weeks off", "not paid"])
def test_a_payment_confirmation_nothing_explains_waits(case):
    with Session(engine) as s:
        box = mailbox(s)
        p = paid_period(s, day=TODAY - timedelta(days=30) if case == "weeks off" else TODAY)
        if case == "not paid":
            p.status, p.transaction_id, p.paid_date = "past_due", None, None
            s.add(p)
            s.commit()
        fact = said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Water" if case == "other biller" else "Example Power", amount=800.0 if case == "other amount" else 80.0, paid_date=TODAY.isoformat())
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


def test_a_bill_notice_older_than_the_newest_from_the_same_biller_expires():
    with Session(engine) as s:
        box = mailbox(s)
        old = said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=70.0, due_date="2026-09-20", day=TODAY - timedelta(days=30))
        new = said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=80.0, due_date="2026-10-20")
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.OLDER: 1}
        assert state(s, new.id).status == "pending"
        old = state(s, old.id)
        assert old.status == "rejected" and old.applied_to.startswith("set aside: an older notice") and old.resolved_by == f"rule:{autokeep.OLDER}" and old.rule_why


def test_expiring_cannot_be_steered_by_a_domain_a_date_or_a_kind():
    """A notice from a domain somebody registered under the same name, one dated in 2099, one nobody vouches for: none of
    them makes the real one the older. An ordinary notice does not expire a past-due one, which may still be true."""
    with Session(engine) as s:
        box = mailbox(s)
        real = said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=70.0, due_date="2026-10-20", day=TODAY - timedelta(days=10))
        past = said(s, box, "past_due", "billing@examplepower.test", "Example Power", amount=70.0, day=TODAY - timedelta(days=20))
        said(s, box, "bill_due", "billing@examplepower-pay.test", "Example Power", amount=900.0, due_date="2026-11-20")
        said(s, box, "bill_due", "billing@examplepower.test", "Example Power", trust="warn", amount=900.0, due_date="2026-11-20")
        said(s, box, "bill_due", "billing@examplepower.test", "Example Power", trust="", amount=900.0, due_date="2026-11-20")
        said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=900.0, due_date="2099-11-20", day=date(2099, 1, 1))
        assert autokeep.run(s, TODAY)["total"] == 0
        assert state(s, real.id).status == "pending" and state(s, past.id).status == "pending"
        said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=75.0, due_date="2026-11-20")
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.OLDER: 1}
        assert state(s, real.id).status == "rejected" and state(s, past.id).status == "pending"


def test_an_advance_the_ledgers_own_rows_show_is_linked():
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s)
        row(s, acct, "Earnin advance", -100.0, TODAY - timedelta(days=2))
        adv = Advance(provider="Earnin", amount=100.0, fee=4.0, taken_on=TODAY - timedelta(days=2), repay_date=TODAY + timedelta(days=5))
        s.add(adv)
        s.commit()
        fact = said(s, box, "advance", "hello@earnin.test", "Earnin", amount=100.0)
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.BORROWING: 1}
        fact = state(s, fact.id)
        assert fact.status == "confirmed" and fact.applied_to == f"advance:{adv.id}" and registers(s) == before


@pytest.mark.parametrize("case", ["no ledger row", "no register row", "other amount"])
def test_an_advance_waits_unless_both_the_ledger_and_the_register_show_it(case):
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s)
        if case != "no ledger row":
            row(s, acct, "Earnin advance", -100.0, TODAY - timedelta(days=2))
        if case != "no register row":
            s.add(Advance(provider="Earnin", amount=100.0, fee=4.0, taken_on=TODAY - timedelta(days=2), repay_date=TODAY + timedelta(days=5)))
            s.commit()
        fact = said(s, box, "advance", "hello@earnin.test", "Earnin", amount=500.0 if case == "other amount" else 100.0)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


def plan(s: Session, **kw) -> InstallmentPlan:
    p = InstallmentPlan(provider="Affirm", merchant="Example Store", total=200.0, installment=50.0, count=4, remaining=3, next_date=TODAY + timedelta(days=20), loan_id="LOAN-1", **kw)
    s.add(p)
    s.commit()
    return p


def test_an_installment_the_ledger_shows_is_linked_and_one_with_news_waits():
    with Session(engine) as s:
        box, acct = mailbox(s), live_account(s)
        p = plan(s, schedule_as_of=TODAY)
        row(s, acct, "Affirm payment", 50.0, TODAY - timedelta(days=9))
        linked = said(s, box, "installment", "notice@affirm.test", "Affirm", provider="Affirm", loan_id="LOAN-1", installment=50.0, schedule=[{"date": "2026-11-01"}], day=TODAY - timedelta(days=4))
        news = said(s, box, "installment", "notice@affirm.test", "Affirm", provider="Affirm", loan_id="LOAN-1", installment=50.0, schedule=[{"date": "2026-12-01"}], day=TODAY + timedelta(days=1))
        autopay = said(s, box, "installment", "notice@affirm.test", "Affirm", provider="Affirm", loan_id="LOAN-1", installment=50.0, notice="autopay", autopay=True)
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.BORROWING: 1}
        assert state(s, linked.id).applied_to == f"plan:{p.id}" and state(s, linked.id).status == "confirmed"
        assert state(s, news.id).status == "pending" and state(s, autopay.id).status == "pending" and registers(s) == before


# ---- trust: nothing a rule does rests on a message no one vouches for -----------------------------------------------


def _every_path(s: Session, box: int, trust: str) -> list[int]:
    acct = live_account(s, balance=1000.0)
    row(s, acct, "Example Store", 42.50)
    row(s, acct, "Earnin advance", -100.0)
    s.add(Advance(provider="Earnin", amount=100.0, fee=4.0, taken_on=TODAY, repay_date=TODAY + timedelta(days=5)))
    s.commit()
    paid_period(s)
    plan(s)
    row(s, acct, "Affirm payment", 50.0, TODAY - timedelta(days=9))
    said(s, box, "bill_due", "billing@examplewater.test", "Example Water", amount=9.0, day=TODAY)  # the newer one, vouched
    return [f.id for f in (
        said(s, box, "receipt", "orders@examplestore.test", "Example Store", trust=trust, total=42.50, bought_on=TODAY.isoformat(), merchant="Example Store"),
        said(s, box, "statement", "statements@examplebank.test", "Example Bank", trust=trust, amount=1000.0),
        said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", trust=trust, amount=80.0),
        said(s, box, "bill_due", "billing@examplewater.test", "Example Water", trust=trust, amount=8.0, day=TODAY - timedelta(days=9)),
        said(s, box, "advance", "hello@earnin.test", "Earnin", trust=trust, amount=100.0),
        said(s, box, "installment", "notice@affirm.test", "Affirm", trust=trust, provider="Affirm", loan_id="LOAN-1", installment=50.0),
    )]


def test_every_path_acts_when_the_message_is_ok():
    with Session(engine) as s:
        box = mailbox(s)  # the setup the next test uses, shown to act when the message is ok
        ids = _every_path(s, box, "ok")
        out = autokeep.run(s, TODAY)
        assert out["total"] == 6 and out["kept"] == {autokeep.RECEIPT: 1, autokeep.STATEMENT: 1, autokeep.CONFIRMATION: 1, autokeep.OLDER: 1, autokeep.BORROWING: 2}
        assert all(state(s, i).resolved_by.startswith("rule:") for i in ids)


@pytest.mark.parametrize("trust", NOT_OK)
def test_no_path_touches_a_message_whose_trust_is_not_ok(trust):
    with Session(engine) as s:
        box = mailbox(s)
        ids = _every_path(s, box, trust)
        before = registers(s)
        assert autokeep.run(s, TODAY)["total"] == 0
        for i in ids:
            fact = state(s, i)
            assert fact.status == "pending" and fact.resolved_by == "" and fact.applied_to == ""
        assert registers(s) == before


def test_mail_through_a_second_persons_mailbox_is_never_acted_on_even_if_it_reads_ok():
    with Session(engine) as s:
        ids = _every_path(s, mailbox(s, person_id=7), "ok")
        assert autokeep.run(s, TODAY)["total"] == 0 and all(state(s, i).status == "pending" for i in ids)


def test_a_fact_with_no_message_is_not_a_rules_to_answer():
    with Session(engine) as s:
        fact = ExtractedFact(kind="statement", fields=json.dumps({"amount": 10.0}), status="pending")
        s.add(fact)
        s.commit()
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


# ---- earned auto-confirm --------------------------------------------------------------------------------------------


SENDER = ("statements@examplelender.test", "Example Lender")


def test_three_identical_answers_earn_a_sender_which_then_files():
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        assert list(autokeep.earned_senders(s)) == [(SENDER[0], "statement")]
        fact = said(s, box, "statement", *SENDER, amount=210.0)
        before = registers(s)
        assert autokeep.run(s, TODAY)["kept"] == {autokeep.EARNED: 1}
        fact = state(s, fact.id)
        assert fact.status == "confirmed" and fact.applied_to == autokeep.READ and fact.resolved_by == f"rule:{autokeep.EARNED}" and "same way 3 times" in fact.rule_why
        assert registers(s) == before


def test_two_answers_earn_nothing_and_a_no_ends_it():
    with Session(engine) as s:
        box = mailbox(s)
        for back in (20, 10):
            answered(s, box, "statement", *SENDER, amount=200.0, day=TODAY - timedelta(days=back))
        fact = said(s, box, "statement", *SENDER, amount=200.0)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"
        answered(s, box, "statement", *SENDER, amount=200.0, day=TODAY - timedelta(days=5))
        assert list(autokeep.earned_senders(s))
        answered(s, box, "statement", *SENDER, amount=200.0, status="rejected", day=TODAY - timedelta(days=1))
        assert not autokeep.earned_senders(s)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


@pytest.mark.parametrize("by", ["person:1", "key:Example Helper", "app:Example Client", f"rule:{autokeep.EARNED}", "rule", "datacheck"])
def test_only_the_owners_answers_earn(by):
    with Session(engine) as s:
        box = mailbox(s)
        for back in (30, 20, 10):
            answered(s, box, "statement", *SENDER, amount=200.0, by=by, day=TODAY - timedelta(days=back))
        assert not autokeep.earned_senders(s)


@pytest.mark.parametrize("trust", NOT_OK)
def test_an_answer_on_an_unvouched_message_earns_nothing(trust):
    with Session(engine) as s:
        box = mailbox(s)
        for back in (30, 20, 10):
            answered(s, box, "statement", *SENDER, amount=200.0, trust=trust, day=TODAY - timedelta(days=back))
        assert not autokeep.earned_senders(s)
        earn(s, box, "statement", *SENDER, 200.0)  # earned, an unvouched message from that very address still waits
        fact = said(s, box, "statement", *SENDER, amount=200.0, trust=trust)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


@pytest.mark.parametrize("skip", ["set aside: an older notice", autokeep.SKIP_MARK + "1"])
def test_a_set_aside_or_a_skip_is_not_an_answer_and_ends_nothing(skip):
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        answered(s, box, "statement", *SENDER, amount=200.0, status="rejected", applied_to=skip, day=TODAY - timedelta(days=1))
        assert list(autokeep.earned_senders(s)) == [(SENDER[0], "statement")]


@pytest.mark.parametrize("amount", [2000.0, 200_000.0, 251.0, 100.0, None])
def test_a_genuine_sender_that_changes_only_the_amount_does_not_get_through(amount):
    """The attacker forges nothing: the mail is verified, from the earned address, and only the figure differs."""
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        fields = {"amount": amount} if amount is not None else {}
        fact = said(s, box, "statement", *SENDER, **fields)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


def test_a_figure_cannot_walk_up_through_what_the_rule_let_by():
    """Each a quarter over the last: the usual comes from the owner's answers only, so the rule's own never widen it."""
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        figure, filed = 200.0, 0
        for _ in range(8):
            figure = round(figure * 1.24, 2)
            fact = said(s, box, "statement", *SENDER, amount=figure)
            filed += autokeep.run(s, TODAY)["total"]
            if figure > 250.0:
                assert state(s, fact.id).status == "pending"
        assert filed == 1 and autokeep.earned_senders(s)[(SENDER[0], "statement")]["high"] == 200.0


def test_earned_never_makes_a_bill_or_moves_one():
    with Session(engine) as s:
        box = mailbox(s)
        bill = Bill(name="Example Power", amount=80.0, due_day=20)
        s.add(bill)
        s.commit()
        sender = ("billing@examplepower.test", "Example Power")
        earn(s, box, "bill_due", *sender, 80.0, applied_to=f"bill:{bill.id}")
        same = said(s, box, "bill_due", *sender, amount=80.0, due_date="2026-11-20")
        near = said(s, box, "bill_due", *sender, amount=84.0, due_date="2026-11-20")  # within the usual, and it moves the plan
        moved = said(s, box, "bill_due", *sender, amount=80.0, due_date="2026-11-25")
        far = said(s, box, "bill_due", *sender, amount=8000.0, due_date="2026-11-20")
        # A new bill under an address the owner answered for: never from a rule.
        other = said(s, box, "bill_due", sender[0], "Example Power Solar", amount=80.0, due_date="2026-11-20")
        before = registers(s)
        # A rule that expires older notices would set the earlier ones aside: it is off here, this test is about earning.
        autokeep.set_rule(s, autokeep.OLDER, False)
        out = autokeep.run(s, TODAY)
        assert out["kept"] == {autokeep.EARNED: 1} and state(s, same.id).applied_to == f"bill:{bill.id}" and state(s, same.id).status == "confirmed"
        assert all(state(s, f.id).status == "pending" for f in (near, moved, far, other)) and registers(s) == before


@pytest.mark.parametrize("kind", ["past_due", "paystub", "receipt", "investment_balance", "export", "document_terms"])
def test_earned_never_answers_a_kind_that_counts(kind):
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, kind, *SENDER, 100.0)
        fact = said(s, box, kind, *SENDER, amount=100.0, net=100.0, total=100.0, balance=100.0, pay_date=TODAY.isoformat())
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


def test_earned_is_per_address_and_a_neighbour_at_the_same_domain_has_not_earned_it():
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        fact = said(s, box, "statement", "someone-else@examplelender.test", SENDER[1], amount=200.0)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"


def test_a_rules_answer_vouches_for_no_domain_a_key_could_then_confirm_from():
    with Session(engine) as s:
        box = mailbox(s)
        paid_period(s)
        said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0)
        assert autokeep.run(s, TODAY)["total"] == 1
        assert "examplepower.test" not in vouched_domains(s)


# ---- seen, undone, stopped ------------------------------------------------------------------------------------------


def _one_of_each(s: Session) -> list[int]:
    box = mailbox(s)
    earn(s, box, "advance", "promos@examplecash.test", "Example Cash", 25.0)
    ids = _every_path(s, box, "ok")
    return [*ids, said(s, box, "advance", "promos@examplecash.test", "Example Cash", amount=25.0).id]


def test_turning_a_rule_off_stops_it_and_each_rule_stops_alone():
    for name in autokeep.RULES:
        from sqlmodel import SQLModel

        from app.db import init_db

        SQLModel.metadata.drop_all(engine)
        init_db()
        with Session(engine) as s:
            ids = _one_of_each(s)
            autokeep.set_rule(s, name, False)
            out = autokeep.run(s, TODAY)
            assert name not in out["kept"] and out["total"] >= 4
            autokeep.set_rule(s, name, True)
            assert name in autokeep.run(s, TODAY)["kept"] and all(state(s, i).status != "pending" for i in ids)


def test_switching_a_sender_off_stops_it():
    with Session(engine) as s:
        box = mailbox(s)
        earn(s, box, "statement", *SENDER, 200.0)
        fact = said(s, box, "statement", *SENDER, amount=200.0)
        autokeep.set_sender(s, SENDER[0].upper(), False)
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fact.id).status == "pending"
        autokeep.set_sender(s, SENDER[0], True)
        assert autokeep.run(s, TODAY)["total"] == 1


def test_undo_takes_back_each_action_and_no_rule_answers_it_again(owner: TestClient):
    with Session(engine) as s:
        ids = _one_of_each(s)
        before = registers(s)
        assert autokeep.run(s, TODAY)["total"] == 7
        assert s.exec(select(BalanceSnapshot)).all() and s.exec(select(Document).where(Document.kind == "receipt")).all()
    for i in ids:
        r = owner.post(f"/api/v1/facts/{i}/unconfirm")
        assert r.status_code == 200, r.text
        assert "waits for your answer" in r.json()["said"]
    with Session(engine) as s:
        assert all(state(s, i).status == "pending" and state(s, i).rule_hold and not state(s, i).applied_to and not state(s, i).resolved_by and not state(s, i).rule_why for i in ids)
        assert not s.exec(select(BalanceSnapshot)).all() and not s.exec(select(Document).where(Document.kind == "receipt")).all() and registers(s) == before
        assert autokeep.run(s, TODAY)["total"] == 0
        # An undo of what a sender's earned way filed turns that sender off, and it says so where the switch is.
        assert autokeep.senders_off(s) == {"promos@examplecash.test": "you undid what it filed"}


def test_the_owner_undoing_their_own_answer_is_not_answered_by_a_rule(owner: TestClient):
    with Session(engine) as s:
        box = mailbox(s)
        paid_period(s)
        fid = answered(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0).id
    assert owner.post(f"/api/v1/facts/{fid}/unconfirm").status_code == 200
    with Session(engine) as s:
        assert autokeep.run(s, TODAY)["total"] == 0 and state(s, fid).status == "pending"


def test_a_plain_reject_still_cannot_be_unconfirmed(owner: TestClient):
    with Session(engine) as s:
        box = mailbox(s)
        fid = answered(s, box, "statement", *SENDER, amount=1.0, status="rejected").id
    r = owner.post(f"/api/v1/facts/{fid}/unconfirm")
    assert r.status_code == 409 and "no confirmation to take back" in r.json()["detail"]


def test_every_action_is_listed_with_its_rule_and_named_on_the_recent_list(owner: TestClient):
    with Session(engine) as s:
        _one_of_each(s)
        autokeep.run(s, TODAY)
    out = owner.get("/api/v1/needs-you/rules").json()
    assert out["can_change"] is True and out["kept"] == 7 and {r["name"] for r in out["rules"]} == set(autokeep.RULES) and all(r["on"] for r in out["rules"])
    assert {a["rule"] for a in out["actions"]} == set(autokeep.RULES) and all(a["why"] and a["rule_title"] for a in out["actions"])
    assert {r["name"]: r["kept"] for r in out["rules"]}[autokeep.BORROWING] == 2
    assert out["senders"] == [{"sender": "promos@examplecash.test", "name": "Example Cash", "on": True, "off_why": "", "filed": 1,
                               "kinds": [{"kind": "advance", "words": "Advances", "answers": 3, "usual_low": 25.0, "usual_high": 25.0}]}]
    recent = owner.get("/api/v1/facts?status=confirmed").json()["facts"]
    named = [f for f in recent if f["kind"] == "payment_confirmation"]
    assert named[0]["resolved_by"] == f"a rule: {autokeep.RULES[autokeep.CONFIRMATION]['title']}" and named[0]["undoable"] is True and named[0]["rule_why"]


def test_counts_on_needs_you_stay_right_with_a_source_card_beside_them(owner: TestClient):
    """M61's card is unmoved; the page, the badge and the next step all lose the kept fact and get it back on undo."""
    with Session(engine) as s:
        seed(s)
        box = mailbox(s)
        s.get(MailAccount, box).last_error = "It refused the sign-in"
        s.commit()
        paid_period(s)
        fid = said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0).id
        waits = said(s, box, "statement", "statements@examplelender.test", "Example Lender", amount=5.0, trust="warn").id

    def counts() -> tuple:
        with Session(engine) as s:
            t = triage(s, TODAY)
            p = _finance_snapshot(s, TODAY)["pending"]
            step = next((x for x in next_steps(s, TODAY)["steps"] if x.get("id") == "facts" or x.get("kind") == "facts"), {})
            return t["total"], p["total"], p["facts"], p["sources"], len(t["sources"]), sum(g["count"] for g in t["groups"]), t["kept_by_rules"], str(step)

    first = counts()
    assert first[2] == 2 and first[3] == first[4] == 1 and first[5] == 2 and first[6] == 0
    with Session(engine) as s:
        assert autokeep.run(s, TODAY)["total"] == 1
        assert state(s, waits).status == "pending"
    kept = counts()
    assert kept[0] == first[0] - 1 and kept[1] == first[1] - 1 and kept[2] == 1 and kept[3] == kept[4] == 1 and kept[5] == 1 and kept[6] == 1
    assert owner.post(f"/api/v1/facts/{fid}/unconfirm").status_code == 200
    assert counts() == first


# ---- who may turn a rule on -----------------------------------------------------------------------------------------


def test_only_the_owner_turns_a_rule_or_a_sender_on_or_off():
    c = person_and_key()
    assert c["owner"].put(f"/api/v1/needs-you/rules/{autokeep.EARNED}", json={"on": False}).json()["rules"][-1]["on"] is False
    for who, want in (("person", 403), ("key", 403)):
        for on in (True, False):
            assert c[who].put(f"/api/v1/needs-you/rules/{autokeep.EARNED}", json={"on": on}).status_code == want
            assert c[who].post("/api/v1/needs-you/rules/senders", json={"sender": SENDER[0], "on": on}).status_code == want
    with Session(engine) as s:
        assert autokeep.switches(s)[autokeep.EARNED] is False and not autokeep.senders_off(s)
    # The second person looks, and is told they cannot change it; a key does not get the list at all: it names senders.
    look = c["person"].get("/api/v1/needs-you/rules")
    assert look.status_code == 200 and look.json()["can_change"] is False
    assert c["key"].get("/api/v1/needs-you/rules").status_code == 403
    # Nor through the general settings door.
    for who in ("owner", "person", "key"):
        assert c[who].put(f"/api/v1/settings/{autokeep.SWITCHES}", json={"value": {}}).status_code in (400, 403)
        assert c[who].put(f"/api/v1/settings/{autokeep.SENDERS_OFF}", json={"value": {}}).status_code in (400, 403)
    assert c["owner"].put(f"/api/v1/needs-you/rules/{autokeep.EARNED}", json={"on": True}).json()["rules"][-1]["on"] is True
    assert c["owner"].put("/api/v1/needs-you/rules/nope", json={"on": True}).status_code == 404


def test_a_second_persons_or_a_keys_answers_never_earn_through_the_routes():
    c = person_and_key()
    with Session(engine) as s:
        box = mailbox(s)
        ids = [said(s, box, "statement", *SENDER, amount=200.0, day=TODAY - timedelta(days=d)).id for d in (30, 20, 10)]
        # A key may answer only for a sender someone vouched for: the domain is marked so its answer is taken and proven not to earn.
        s.add(MessageRef(account_id=box, folder="INBOX", uid=1, sender=SENDER[0], sender_name=SENDER[1], trust="ok", trust_why="You marked this sender as genuine."))
        s.commit()
    assert c["person"].post(f"/api/v1/facts/{ids[0]}/confirm", json={"apply": True}).status_code == 200
    assert c["person"].post(f"/api/v1/facts/{ids[1]}/confirm", json={"apply": True}).status_code == 200
    assert c["key"].post(f"/api/v1/facts/{ids[2]}/confirm", json={"apply": True}).status_code == 200
    with Session(engine) as s:
        assert not autokeep.earned_senders(s)
    assert c["owner"].post(f"/api/v1/facts/{ids[0]}/unconfirm").status_code == 200
    assert c["owner"].post(f"/api/v1/facts/{ids[0]}/confirm", json={"apply": True}).status_code == 200
    with Session(engine) as s:
        assert not autokeep.earned_senders(s)  # one answer of the owner's is one answer


def test_the_rule_routes_are_the_owners_for_a_shared_sign_in_too():
    c = person_and_key()
    made = c["owner"].post("/api/v1/people", json={"username": "lee", "display_name": "Lee Example", "role": "shared"})
    shared = TestClient(app)
    shared.headers["X-Requested-With"] = "FinancialAuthority"
    assert shared.post("/api/auth/join", json={"token": made.json()["invite"]["link"].split("#", 1)[1], "password": "copper lantern autumn seven"}).status_code == 200
    assert shared.get("/api/v1/needs-you/rules").status_code == 403
    assert shared.put(f"/api/v1/needs-you/rules/{autokeep.RECEIPT}", json={"on": True}).status_code == 403
    assert shared.post("/api/v1/needs-you/older/skip", json={"token": "x"}).status_code == 403


# ---- older than two months ------------------------------------------------------------------------------------------


def _old(s: Session) -> tuple[list[int], list[int]]:
    box = mailbox(s)
    day = TODAY - timedelta(days=61)
    batch = [said(s, box, "statement", *SENDER, amount=5.0, day=day).id, said(s, box, "receipt", "orders@examplestore.test", "Example Store", total=9.0, day=day - timedelta(days=30)).id,
             said(s, box, "bill_due", "billing@examplewater.test", "Example Water", trust="warn", amount=8.0, day=day).id]
    stays = [said(s, box, "statement", *SENDER, amount=5.0, day=TODAY - timedelta(days=60)).id,  # sixty days is not more than sixty
             said(s, box, "export", "export@rocketmoney.test", "Rocket Money", day=day).id,  # one at a time, always
             said(s, box, "document_terms", *SENDER, day=day).id]
    return batch, stays


def test_the_older_batch_lists_what_has_waited_more_than_sixty_days(owner: TestClient):
    with Session(engine) as s:
        _old(s)
    out = owner.get("/api/v1/needs-you").json()
    older = out["older"]
    assert older["count"] == 3 and {k["kind"]: k["count"] for k in older["kinds"]} == {"statement": 1, "receipt": 1, "bill_due": 1}
    assert older["said"] == "Older than two months: 3 facts, with nothing newer to replace them." and "bring them back" in older["asks"] and older["token"]
    assert out["total"] == sum(g["count"] for g in out["groups"]) == 6  # they stay in the count once


def test_a_superseded_notice_is_not_in_the_batch_with_the_expiring_rule_off():
    with Session(engine) as s:
        box = mailbox(s)
        autokeep.set_rule(s, autokeep.OLDER, False)
        old = said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=7.0, day=TODAY - timedelta(days=90))
        keep = said(s, box, "bill_due", "billing@examplepower.test", "Example Power", amount=8.0, day=TODAY - timedelta(days=80))
        assert [f.id for f in autokeep.older_facts(s, TODAY)] == [keep.id] and old.id != keep.id


def test_skip_all_asks_by_token_and_every_one_comes_back(owner: TestClient):
    with Session(engine) as s:
        batch, stays = _old(s)
        before = {i: state(s, i).fields for i in batch}
    assert owner.post("/api/v1/needs-you/older/skip", json={"token": "not what was shown"}).status_code == 409
    token = owner.get("/api/v1/needs-you").json()["older"]["token"]
    with Session(engine) as s:  # one came since it was shown: nothing is skipped
        late = said(s, mailbox(s), "statement", *SENDER, amount=5.0, day=TODAY - timedelta(days=70)).id
    r = owner.post("/api/v1/needs-you/older/skip", json={"token": token})
    assert r.status_code == 409 and "nothing was skipped" in r.json()["detail"]
    with Session(engine) as s:
        assert all(state(s, i).status == "pending" for i in batch)
    token = owner.get("/api/v1/needs-you").json()["older"]["token"]
    out = owner.post("/api/v1/needs-you/older/skip", json={"token": token}).json()
    assert out["count"] == 4 and "bring them back" in out["said"] and out["triage"]["older"] is None
    assert out["triage"]["skipped"][0]["batch"] == out["batch"] and out["triage"]["skipped"][0]["count"] == 4
    assert out["triage"]["total"] == len(stays)
    with Session(engine) as s:
        for i in [*batch, late]:
            f = state(s, i)
            assert f.status == "rejected" and f.resolved_by == "owner" and f.applied_to.startswith(autokeep.SKIP_MARK)
        assert all(state(s, i).status == "pending" for i in stays)
    back = owner.post("/api/v1/needs-you/older/back", json={"batch": out["batch"]}).json()
    assert back["count"] == 4 and back["triage"]["skipped"] == [] and back["triage"]["total"] == 4 + len(stays)
    with Session(engine) as s:
        for i in batch:
            f = state(s, i)
            assert f.status == "pending" and f.fields == before[i] and f.applied_to == "" and f.resolved_by == "" and f.resolved_at is None
        assert autokeep.run(s, TODAY)["total"] == 0  # the owner asked for them back: no rule takes them again
    assert owner.post("/api/v1/needs-you/older/back", json={"batch": out["batch"]}).status_code == 409


def test_a_skip_is_answered_by_a_person_never_a_key():
    c = person_and_key()
    with Session(engine) as s:
        _old(s)
    token = c["owner"].get("/api/v1/needs-you").json()["older"]["token"]
    assert c["key"].post("/api/v1/needs-you/older/skip", json={"token": token}).status_code == 403
    assert c["key"].post("/api/v1/needs-you/older/back", json={"batch": 1}).status_code == 403
    out = c["person"].post("/api/v1/needs-you/older/skip", json={"token": token})
    assert out.status_code == 200
    with Session(engine) as s:
        row_ = s.exec(select(ExtractedFact).where(ExtractedFact.applied_to.like(f"{autokeep.SKIP_MARK}%"))).first()
        assert row_.resolved_by.startswith("person:")
    assert c["owner"].post("/api/v1/needs-you/older/back", json={"batch": out.json()["batch"]}).status_code == 200


# ---- the job --------------------------------------------------------------------------------------------------------


def test_the_job_is_on_the_jobs_page_says_what_it_did_and_a_second_run_writes_nothing():
    with Session(engine) as s:
        box = mailbox(s)
        paid_period(s)
        said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0)
    assert jobs.specs["keep_by_rule"].seconds == 3600
    first = jobs.run("keep_by_rule")
    assert first.ok is True and "1 by payment confirmations already explained" in first.summary
    assert "nothing to answer" in jobs.run("keep_by_rule").summary


def test_a_fact_that_cannot_be_read_stops_nothing_else():
    with Session(engine) as s:
        box = mailbox(s)
        paid_period(s)
        ref = message(s, box, "billing@examplepower.test", "Example Power")
        s.add(ExtractedFact(kind="payment_confirmation", message_ref_id=ref.id, fields="{not json", status="pending"))
        s.commit()
        good = said(s, box, "payment_confirmation", "billing@examplepower.test", "Example Power", amount=80.0)
        out = autokeep.run(s, TODAY)
        assert out["failed"] == 1 and out["total"] == 1 and state(s, good.id).status == "confirmed"


# ---- the property tests: many households, any mail --------------------------------------------------------------------


def _household(rng: random.Random, s: Session) -> dict:
    box = mailbox(s)
    second = mailbox(s, person_id=9)
    account = live_account(s, balance=round(rng.uniform(100, 5000), 2))
    rows = [row(s, account, rng.choice(("Example Store", "Example Shop", "Example Market")), round(rng.uniform(5, 300), 2), TODAY - timedelta(days=rng.randint(0, 6))) for _ in range(rng.randint(2, 8))]
    bills = [Bill(name=n, amount=round(rng.uniform(20, 300), 2), due_day=rng.randint(1, 28)) for n in ("Example Power", "Example Water", "Example Net")]
    s.add_all(bills)
    s.commit()
    for b in bills[:2]:
        t = row(s, account, b.name, b.amount, TODAY - timedelta(days=rng.randint(0, 4)))
        s.add(Payment(bill_id=b.id, due_date=t.date, amount=b.amount, status="paid", paid_date=t.date, transaction_id=t.id))
    s.commit()
    return {"box": box, "second": second, "account": account, "rows": rows, "bills": bills}


def _random_mail(rng: random.Random, s: Session, h: dict, n: int) -> list[int]:
    ids = []
    senders = [("billing@examplepower.test", "Example Power"), ("billing@examplewater.test", "Example Water"), ("no-reply@examplenet.test", "Example Net"), ("statements@examplebank.test", "Example Bank"), ("orders@examplestore.test", "Example Store")]
    for _ in range(n):
        kind = rng.choice(("receipt", "statement", "payment_confirmation", "bill_due", "past_due", "price_change", "advance", "installment", "paystub", "investment_balance", "export"))
        sender, name = rng.choice(senders)
        trust = rng.choice(("ok", "ok", "ok", "warn", "crit", "unknown", ""))
        day = TODAY - timedelta(days=rng.randint(0, 90))
        t = rng.choice(h["rows"])
        b = rng.choice(h["bills"])
        amount = rng.choice((round(t.amount, 2), b.amount, h["account"].balance, round(rng.uniform(1, 9000), 2)))
        fields = {"amount": amount, "total": amount, "bought_on": (t.date + timedelta(days=rng.randint(-2, 2))).isoformat(), "merchant": rng.choice((t.merchant, name)), "net": amount, "balance": amount, "pay_date": day.isoformat()}
        if rng.random() < 0.3:
            fields["due_date"] = (day + timedelta(days=rng.randint(0, 400))).isoformat()
        ids.append(said(s, rng.choice((h["box"], h["box"], h["box"], h["second"])), kind, sender, name, trust=trust, day=day, **fields).id)
    # Some of the owner's own answers, so senders earn their way.
    for _ in range(rng.randint(0, 12)):
        sender, name = rng.choice(senders)
        answered(s, h["box"], rng.choice(("statement", "advance", "payment_confirmation")), sender, name, amount=round(rng.uniform(5, 400), 2), status=rng.choice(("confirmed", "confirmed", "confirmed", "rejected")), day=TODAY - timedelta(days=rng.randint(10, 120)))
    return ids


SEEDS = range(12)


@pytest.mark.parametrize("seed_", SEEDS)
def test_property_no_rule_touches_what_nobody_vouches_for_counts_anything_or_answers_twice(seed_):
    rng = random.Random(seed_)
    with Session(engine) as s:
        h = _household(rng, s)
        ids = _random_mail(rng, s, h, 60)
        before = registers(s)
        out = autokeep.run(s, TODAY)
        assert out["failed"] == 0
        assert registers(s) == before  # nothing the plan counts has moved
        mine = {h["box"]}
        for i in ids:
            f = state(s, i)
            ref = s.get(MessageRef, f.message_ref_id)
            if ref.trust != "ok" or ref.account_id not in mine:  # hard requirement one
                assert f.status == "pending" and f.resolved_by == ""
            if f.status == "pending":
                assert f.resolved_by == "" and not f.rule_why
                continue
            name = autokeep.rule_of(f)  # nothing left the queue but by a named rule, with its reason
            assert name in autokeep.RULES and f.rule_why
            assert f.kind not in ("paystub", "investment_balance", "export")  # kinds that count are the owner's
            if f.kind == "receipt":  # a source: a real row, of that amount, within a day
                doc = s.get(Document, int(f.applied_to.split(":")[1]))
                t = s.get(Transaction, int(doc.attach_to.split(":")[1]))
                fields = json.loads(f.fields)
                assert abs(t.amount - fields["total"]) <= 0.01 and abs((t.date - date.fromisoformat(fields["bought_on"])).days) <= 1
            if name == autokeep.EARNED:  # the earned way only files, and only inside the usual
                entry = autokeep.earned_senders(s)[(ref.sender.lower(), f.kind)]
                assert autokeep.within_usual(entry, json.loads(f.fields).get("amount")) and f.applied_to == autokeep.READ
        # Nothing counts twice: no row holds two receipts, and no day two readings.
        attached = [d.attach_to for d in s.exec(select(Document).where(Document.kind == "receipt")).all()]
        assert len(attached) == len(set(attached))
        days = [(r.account_id, r.date) for r in s.exec(select(BalanceSnapshot)).all()]
        assert len(days) == len(set(days))
        # A second pass finds nothing and writes nothing.
        settled = {i: (state(s, i).status, state(s, i).applied_to) for i in ids}
        assert autokeep.run(s, TODAY)["total"] == 0
        assert settled == {i: (state(s, i).status, state(s, i).applied_to) for i in ids}


@pytest.mark.parametrize("seed_", SEEDS)
def test_property_all_off_means_nothing_and_undoing_all_puts_everything_back(seed_):
    rng = random.Random(1000 + seed_)
    with Session(engine) as s:
        h = _household(rng, s)
        ids = _random_mail(rng, s, h, 60)
        before, docs, readings = registers(s), s.exec(select(func.count()).select_from(Document)).one(), s.exec(select(func.count()).select_from(BalanceSnapshot)).one()
        pending = {i for i in ids if state(s, i).status == "pending"}
        for name in autokeep.RULES:
            autokeep.set_rule(s, name, False)
        assert autokeep.run(s, TODAY)["total"] == 0 and all(state(s, i).status == "pending" for i in pending)
        for name in autokeep.RULES:
            autokeep.set_rule(s, name, True)
        autokeep.run(s, TODAY)
        from app.engine.facts import unconfirm_fact

        done = [i for i in pending if state(s, i).status != "pending"]
        for i in done:
            assert unconfirm_fact(s, i, by="owner")["ok"] is True
        assert all(state(s, i).status == "pending" for i in pending)
        assert registers(s) == before and s.exec(select(func.count()).select_from(Document)).one() == docs and s.exec(select(func.count()).select_from(BalanceSnapshot)).one() == readings
        assert autokeep.run(s, TODAY)["total"] == 0 or any(not state(s, i).rule_hold for i in pending)
        assert all(state(s, i).status == "pending" for i in done)  # those taken back stay the owner's


@pytest.mark.parametrize("seed_", SEEDS)
def test_property_skip_all_then_bring_back_is_the_same_queue(seed_):
    rng = random.Random(2000 + seed_)
    with Session(engine) as s:
        h = _household(rng, s)
        ids = _random_mail(rng, s, h, 40)
        waiting = {i: state(s, i).fields for i in ids if state(s, i).status == "pending"}
        total = triage(s, TODAY)["total"]
        batch = autokeep.older_batch(s, TODAY)
        if batch is None:
            pytest.skip("no fact this old in this draw")
        out = autokeep.skip_older(s, batch["token"], "owner", TODAY)
        assert out["count"] == batch["count"] and triage(s, TODAY)["total"] == total - batch["count"]
        assert all(state(s, i).kind not in ("export", "document_terms") or state(s, i).status == "pending" for i in waiting)
        autokeep.bring_back(s, out["batch"])
        assert {i: state(s, i).fields for i in ids if state(s, i).status == "pending"} == waiting and triage(s, TODAY)["total"] == total


@pytest.mark.parametrize("seed_", SEEDS)
def test_property_an_earned_sender_never_files_a_figure_outside_the_owners_answers(seed_):
    rng = random.Random(3000 + seed_)
    with Session(engine) as s:
        box = mailbox(s)
        figures = [round(rng.uniform(10, 500), 2) for _ in range(3)]
        for back, figure in zip((30, 20, 10), figures, strict=True):
            answered(s, box, "statement", *SENDER, amount=figure, day=TODAY - timedelta(days=back))
        low, high = min(figures), max(figures)
        floor, ceiling = min(low * 0.75, low - 5), max(high * 1.25, high + 5)
        for _ in range(30):
            figure = round(rng.choice((rng.uniform(low, high), rng.uniform(ceiling, ceiling * 50), rng.uniform(0.01, max(floor, 0.02)))), 2)
            fact = said(s, box, "statement", *SENDER, amount=figure)
            autokeep.run(s, TODAY)
            f = state(s, fact.id)
            assert (f.status == "confirmed") == (floor <= figure <= ceiling), (figure, floor, ceiling)


# ---- the golden, on the synthetic household ---------------------------------------------------------------------------


def _golden() -> dict:
    with Session(engine) as s:
        seed(s)
        checking = s.exec(select(Account).where(Account.name == "Everyday Checking")).one()
        checking.connector, checking.balance_at = "plaid", datetime(2026, 10, 14, 8, tzinfo=UTC)
        s.add(checking)
        store = row(s, checking, "Example Garden Store", 63.25, TODAY - timedelta(days=1))
        row(s, checking, "Earnin advance", -100.0, TODAY - timedelta(days=2))
        s.add(Advance(provider="Earnin", amount=100.0, fee=4.0, taken_on=TODAY - timedelta(days=2), repay_date=TODAY + timedelta(days=2)))
        s.commit()
        internet = s.exec(select(Bill).where(Bill.name == "Internet")).one()
        paid = s.exec(select(Transaction).where(Transaction.merchant == "Internet").order_by(Transaction.date.desc())).first()
        box = mailbox(s)
        kept = {
            "receipt": said(s, box, "receipt", "orders@examplegarden.test", "Example Garden Store", total=63.25, bought_on=TODAY.isoformat(), merchant="Example Garden Store"),
            "statement": said(s, box, "statement", "statements@examplebank.test", "Example Bank", amount=round(checking.balance + 0.40, 2)),
            "older notice": said(s, box, "bill_due", "billing@examplepower.test", "Electricity", amount=120.0, due_date="2026-09-20", day=TODAY - timedelta(days=32)),
            "advance": said(s, box, "advance", "hello@earnin.test", "Earnin", amount=100.0),
        }
        if paid is not None:
            s.add(Payment(bill_id=internet.id, due_date=paid.date, amount=paid.amount, status="paid", paid_date=paid.date, transaction_id=paid.id))
            s.commit()
            kept["confirmation"] = said(s, box, "payment_confirmation", "billing@examplenet.test", "Internet", amount=paid.amount, paid_date=paid.date.isoformat(), day=paid.date)
        earn(s, box, "statement", "statements@examplelender.test", "Example Lender", 200.0)
        kept["earned"] = said(s, box, "statement", "statements@examplelender.test", "Example Lender", amount=205.0)
        waits = {
            "newest notice": said(s, box, "bill_due", "billing@examplepower.test", "Electricity", amount=142.0, due_date="2026-10-20"),
            "forged statement": said(s, box, "statement", "statements@examplebank.test", "Example Bank", trust="crit", amount=round(checking.balance, 2)),
            "unjudged receipt": said(s, box, "receipt", "orders@examplegarden.test", "Example Garden Store", trust="", total=63.25, bought_on=TODAY.isoformat(), merchant="Example Garden Store"),
            "amount changed": said(s, box, "statement", "statements@examplelender.test", "Example Lender", amount=2000.0),
            "paystub": said(s, box, "paystub", "payroll@example.test", "Example Employer", net=2240.0, pay_date=TODAY.isoformat()),
            "old statement": said(s, box, "statement", "statements@exampleother.test", "Example Other", amount=11.0, day=TODAY - timedelta(days=70)),
        }
        snap_before = _finance_snapshot(s, TODAY)
        run = autokeep.run(s, TODAY)
        s.expire_all()
        snap_after = _finance_snapshot(s, TODAY)

        def rec(f: ExtractedFact) -> dict:
            f = s.get(ExtractedFact, f.id)
            return {"kind": f.kind, "status": f.status, "rule": f.resolved_by, "why": f.rule_why, "to": f.applied_to.split(":")[0]}

        older = autokeep.older_batch(s, TODAY)
        out = {
            "run": run, "kept": {k: rec(v) for k, v in kept.items()}, "waits": {k: rec(v) for k, v in waits.items()},
            "plan_unmoved": {k: snap_before[k] == snap_after[k] for k in ("safe_to_spend", "cash", "bills", "debts_cards_loans_total") if k in snap_before},
            "receipt_row": s.get(Document, int(s.get(ExtractedFact, kept["receipt"].id).applied_to.split(":")[1])).attach_to == f"transaction:{store.id}",
            "older": {k: older[k] for k in ("count", "kinds", "said", "oldest", "newest")} if older else None,
            "rules": [{"name": r["name"], "kept": r["kept"], "on": r["on"]} for r in autokeep.overview(s, TODAY)["rules"]],
            "senders": autokeep.overview(s, TODAY)["senders"],
            "pending": snap_after["pending"]["facts"],
        }
        return out


def test_the_golden_on_the_synthetic_household():
    got = json.loads(json.dumps(_golden(), default=str))
    if os.environ.get("FA_GOLDEN_WRITE") == "1":
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8")), "If that is meant, write the golden file again (see tests/test_golden.py) and say why in the commit."
    assert all(got["plan_unmoved"].values()) and got["plan_unmoved"]
    assert all(v["rule"].startswith("rule:") and v["why"] for v in got["kept"].values())
    assert all(v["status"] == "pending" and not v["rule"] for v in got["waits"].values())


def test_get_setting_untouched_is_all_on():
    with Session(engine) as s:
        assert get_setting(s, autokeep.SWITCHES) is None and all(autokeep.switches(s).values())
