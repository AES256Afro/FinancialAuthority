"""Sweep 23: the findings of the sixteenth correctness read of 0.10.7's engine changes, each pinned by its smallest
case. The hand model's new comparisons are in test_sweep23_bugs_hand.py."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlmodel import Session, select

from app.db import engine
from app.engine.payments import match_payments
from app.models import Account, Bill, Payment, Transaction


def test_a_late_payment_days_before_the_next_due_day_still_pays_its_own_month():
    """Rent due the 2nd: September paid on the day, October paid late on Oct 28, November paid on Nov 2. 0.10.7's
    early-payment rule skipped the Oct 28 row for October because it sits within six days of Nov 2, though the
    Nov 2 row pays November: October stood past due with two payments under the bill's name in October and November."""
    with Session(engine) as s:
        chk = Account(name="Checking", institution="Example Bank", kind="checking", balance=2000.0, connector="manual", external_id="chk")
        s.add(chk)
        s.flush()
        rent = Bill(name="Example Rent", amount=1000.0, due_day=2, category="Rent", pay_from_account_id=chk.id, created_on=date(2026, 8, 1))
        s.add(rent)
        for k, d in enumerate((date(2026, 9, 2), date(2026, 10, 28), date(2026, 11, 2))):
            s.add(Transaction(account_id=chk.id, date=d, merchant="Example Rent", raw_name="EXAMPLE RENT", amount=1000.0, category="Rent", row_hash=f"rent-{k}"))
        s.commit()
        for today in (date(2026, 11, 10), date(2026, 11, 11)):
            match_payments(s, today)
            got = {p.due_date.isoformat(): p.status for p in s.exec(select(Payment).where(Payment.bill_id == rent.id)).all() if date(2026, 10, 1) <= p.due_date <= today}
            assert got == {"2026-10-02": "paid", "2026-11-02": "paid"}, got


def test_an_early_payment_before_the_next_due_day_still_pays_the_next_period():
    """The case the 0.10.7 rule was written for stays: October missed, one row on Oct 28 and none on Nov 2: the row is
    November's (paid early), October is past due."""
    with Session(engine) as s:
        chk = Account(name="Checking", institution="Example Bank", kind="checking", balance=2000.0, connector="manual", external_id="chk")
        s.add(chk)
        s.flush()
        rent = Bill(name="Example Rent", amount=1000.0, due_day=2, category="Rent", pay_from_account_id=chk.id, created_on=date(2026, 8, 1))
        s.add(rent)
        for k, d in enumerate((date(2026, 9, 2), date(2026, 10, 28))):
            s.add(Transaction(account_id=chk.id, date=d, merchant="Example Rent", raw_name="EXAMPLE RENT", amount=1000.0, category="Rent", row_hash=f"rent-{k}"))
        s.commit()
        match_payments(s, date(2026, 11, 10))
        got = {p.due_date.isoformat(): p.status for p in s.exec(select(Payment).where(Payment.bill_id == rent.id)).all() if date(2026, 10, 1) <= p.due_date <= date(2026, 11, 10)}
        assert got == {"2026-10-02": "past_due", "2026-11-02": "paid"}, got


def test_a_saved_rule_with_a_space_quantified_after_a_group_still_matches():
    """0.10.7 refuses a quantified group that holds an alternation; its check let whitespace stand between the group
    and the quantifier, so `(amzn|amazon) ?mktp` (an optional space, linear) and `(sq|tst) *\\*` were refused and the
    owner's rule silently stopped matching. `(?:the )?` was refused for the `?` of its own `(?:`."""
    from app.engine.rules import matches

    def row(merchant: str) -> SimpleNamespace:
        return SimpleNamespace(merchant=merchant, raw_name=merchant, description="")

    for pattern, merchant in ((r"re:(amzn|amazon) ?mktp", "AMZN Mktp US"), (r"re:^(sq|tst) *\*\s*joes", "SQ *JOES COFFEE"), (r"re:(?:the )?home depot", "The Home Depot")):
        assert matches(SimpleNamespace(match_field="merchant", pattern=pattern), row(merchant)), pattern
    for bad in (r"re:(a|aa)+$", r"re:(a+)+$", r"re:(?:a|aa)*b", r"re:(\w+\s?)*$", r"re:(?x)(a|aa) +$"):
        assert not matches(SimpleNamespace(match_field="merchant", pattern=bad), row("aaaa")), bad


def test_a_short_end_of_month_check_names_the_lines_that_moved_from_the_last_end_of_month_check():
    """A semi-monthly paycheck whose end-of-month check carries the month's benefit line: the Oct 31 check was judged
    short against the end-of-month usual (as expected_net judges it), but "what moved" was read against the Oct 15
    check, so the benefit line that moves every month was named beside the retirement line that truly moved."""
    import json as _json

    from app.engine.paystubs import paystubs_overview
    from app.models import Income, Paystub

    def stub(d: date, net: float, benefits: float, retirement: float) -> Paystub:
        lines = [{"name": "Federal income tax", "amount": 300.0}, {"name": "Medical", "amount": benefits}, {"name": "401k", "amount": retirement}]
        return Paystub(employer="Example Employer", pay_date=d, gross=round(net + 300.0 + benefits + retirement, 2), net=net, hours=86.67, deductions=_json.dumps(lines), stub_hash=f"s-{d}")

    with Session(engine) as s:
        s.add(Income(name="Example Employer", cadence="semimonthly", expected_net=1500.0, expected_net_end=1400.0, next_date=date(2026, 11, 13)))
        s.add(stub(date(2026, 9, 15), 1500.0, 0.0, 100.0))
        s.add(stub(date(2026, 9, 30), 1400.0, 100.0, 100.0))
        s.add(stub(date(2026, 10, 15), 1500.0, 0.0, 100.0))
        s.add(stub(date(2026, 10, 30), 1300.0, 100.0, 200.0))
        s.commit()
        got = paystubs_overview(s)
        (short,) = got["short"]
        assert short["pay_date"] == "2026-10-30"
        assert [(m["name"], m["before"], m["after"]) for m in short["moved"]] == [("401k", 100.0, 200.0)], short["moved"]


def test_the_brief_never_says_a_past_due_count_beside_a_shorter_list_of_names():
    """Needs you keeps a past-due period ninety days, the payments register shows forty-five. With one period sixty
    days old and one ten, the brief said "2 bill periods past due with no payment seen: Example Water $30." (two
    counted, one named); with only the sixty-day one it said nothing, while the badge said 1."""
    from datetime import timedelta

    from app.engine.snapshot import finance_snapshot

    today = date(2026, 10, 14)
    with Session(engine) as s:
        s.add(Account(name="Checking", institution="Example Bank", kind="checking", balance=900.0, connector="manual", external_id="chk"))
        water, gym = Bill(name="Example Water", amount=30.0, due_day=4), Bill(name="Example Gym", amount=45.0, due_day=15)
        s.add(water)
        s.add(gym)
        s.commit()
        s.add(Payment(bill_id=gym.id, due_date=today - timedelta(days=60), amount=45.0, status="past_due"))
        s.commit()
        lines = [i["text"] for i in finance_snapshot(s, today)["brief"] if "past due with no payment" in i["text"]]
        assert lines == ["1 bill period past due with no payment seen: Example Gym $45."], lines
        s.add(Payment(bill_id=water.id, due_date=today - timedelta(days=10), amount=30.0, status="past_due"))
        s.commit()
        from app.db import bump_version

        bump_version()
        lines = [i["text"] for i in finance_snapshot(s, today)["brief"] if "past due with no payment" in i["text"]]
        assert lines == ["2 bill periods past due with no payment seen: Example Gym $45 and Example Water $30."], lines
