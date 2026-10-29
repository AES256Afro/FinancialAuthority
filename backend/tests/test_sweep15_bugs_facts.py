"""Sweep 15, asked by the MCP and injection round: a forged past-due notice said by name in the brief (and so in
the reviewer's prompt), and a confirmed fact that kept no record of who confirmed it and could not be taken back."""

from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.mail import ConfirmIn, apply_fact
from app.engine.brief import build_brief
from app.engine.facts import resolve_fact, unconfirm_fact, watermark, who
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot
from app.db import engine
from app.models import Bill, ExtractedFact, InstallmentPlan, MailAccount, MessageRef, Payment, Paystub

TODAY = date(2026, 10, 14)


def _fact(session: Session, kind: str, fields: dict, trust: str = "ok", uid: int = 1, sender_name: str = "Example Power") -> ExtractedFact:
    box = session.exec(select(MailAccount)).first()
    if box is None:
        box = MailAccount(label="Example", kind="imap", host="mail.example.test", port=993, username="owner@example.test", password_enc="")
        session.add(box)
        session.flush()
    ref = MessageRef(account_id=box.id, folder="INBOX", uid=uid, sender="notices@example.test", sender_name=sender_name, subject="A notice", trust=trust)
    session.add(ref)
    session.flush()
    fact = ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps(fields), confidence=0.9)
    session.add(fact)
    session.commit()
    return fact


def _confirm(session: Session, fact: ExtractedFact, by: str = "app:Example Client", **body) -> str:  # noqa: ANN003
    before = watermark(session)
    applied = apply_fact(session, fact, session.get(MessageRef, fact.message_ref_id), ConfirmIn(**body), owner=True)
    resolve_fact(session, fact, "applied" if applied else "confirmed", applied_to=applied, by=by, before=before)
    session.commit()
    return applied


def test_a_forged_past_due_notice_is_not_named_in_the_brief(owner: TestClient):
    with Session(engine) as s:
        seed(s)
        _fact(s, "past_due", {"amount": 99.0}, trust="crit", uid=1, sender_name="Example Forged Lender")
        _fact(s, "past_due", {"amount": 64.0}, trust="ok", uid=2, sender_name="Example Water")
        text = " ".join(i["text"] for i in build_brief(finance_snapshot(s, TODAY)))
    assert "Example Water" in text and "Example Forged Lender" not in text


def test_who_answered_is_kept_and_a_bill_a_confirmation_made_can_be_taken_back(owner: TestClient):
    class App:
        kind, name, person_id, via_oauth = "key", "Example Client", None, True

    assert who(App()) == "app:Example Client" and who(App(), app=False) == "key:Example Client"
    with Session(engine) as s:
        seed(s)
        bills_before = sorted(b.id for b in s.exec(select(Bill)).all())
        fact = _fact(s, "bill_due", {"name": "Example New Service", "amount": 25.0, "due_date": "2026-10-28"})
        applied = _confirm(s, fact, by=who(App()))
        s.refresh(fact)
        assert fact.resolved_by == "app:Example Client" and fact.made == applied
        bill_id = int(applied.split(":")[1])
        s.add(Payment(bill_id=bill_id, due_date=date(2026, 10, 28), amount=25.0))  # a period scheduled since: nothing real
        s.commit()
        out = unconfirm_fact(s, fact.id, by="owner")
        s.refresh(fact)
        assert out["undone"] == applied and fact.status == "pending" and fact.resolved_by == "" and fact.applied_to == ""
        assert sorted(b.id for b in s.exec(select(Bill)).all()) == bills_before and not s.exec(select(Payment).where(Payment.bill_id == bill_id)).all()


def test_a_confirmation_is_not_taken_back_where_that_would_lose_something(owner: TestClient):
    with Session(engine) as s:
        seed(s)
        existing = s.exec(select(Bill).where(Bill.active == True)).first()  # noqa: E712
        changed = _fact(s, "bill_due", {"name": existing.name, "amount": existing.amount + 5, "due_date": "2026-10-28"}, uid=1)
        _confirm(s, changed)
        made = _fact(s, "bill_due", {"name": "Example Paid Service", "amount": 30.0, "due_date": "2026-10-20"}, uid=2)
        bill_id = int(_confirm(s, made).split(":")[1])
        s.add(Payment(bill_id=bill_id, due_date=date(2026, 10, 20), amount=30.0, status="paid", paid_date=date(2026, 10, 20)))
        s.commit()
        for fact, word in ((changed, "already had"), (made, "matched or marked")):
            try:
                unconfirm_fact(s, fact.id)
                raise AssertionError("taken back")
            except ValueError as error:
                assert word in str(error)
            s.refresh(fact)
            assert fact.status == "applied"
        assert s.get(Bill, bill_id) is not None


def test_a_plan_and_a_paystub_a_confirmation_made_come_back_out(owner: TestClient):
    with Session(engine) as s:
        seed(s)
        plans_before = len(s.exec(select(InstallmentPlan)).all())
        plan_fact = _fact(s, "installment", {"provider": "Example Pay Later", "merchant": "Example Store", "amount": 40.0}, uid=1)
        assert _confirm(s, plan_fact, remaining=4).startswith("plan:")
        stub_fact = _fact(s, "paystub", {"net": 1234.56, "pay_date": "2026-12-04", "employer": "Example Employer"}, uid=2)
        stub_tag = _confirm(s, stub_fact)
        assert unconfirm_fact(s, plan_fact.id)["ok"] and len(s.exec(select(InstallmentPlan)).all()) == plans_before
        assert "usual net" in unconfirm_fact(s, stub_fact.id)["said"] and s.get(Paystub, int(stub_tag.split(":")[1])) is None
