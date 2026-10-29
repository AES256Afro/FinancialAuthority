"""The registers: recurring series and how they become bills, subscriptions, incomes or installment plans."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth
from ..db import get_session
from ..jobs.detect import pending_series, run_detection
from ..llm.reviewer import mark_pending
from ..models import Bill, Income, InstallmentPlan, RecurringSeries
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1", tags=["registers"])

KINDS = {"bill", "subscription", "income", "installment", "ignored"}


def series_out(s: RecurringSeries) -> dict:
    return {
        "id": s.id, "merchant": s.merchant, "category": s.category, "cadence": s.cadence, "typical_amount": s.typical_amount, "latest_amount": s.latest_amount,
        "charges_seen": s.charges_seen, "first_seen": s.first_seen.isoformat(), "last_date": s.last_date.isoformat(), "next_expected": s.next_expected.isoformat(),
        "active": s.active, "kind": s.kind, "confirmed": s.confirmed, "suggested_kind": s.suggested_kind,
    }


def bill_out(b: Bill) -> dict:
    return {
        "id": b.id, "name": b.name, "kind": b.kind, "amount": b.amount, "amount_mode": b.amount_mode, "cadence": b.cadence, "due_day": b.due_day, "anchor_date": b.anchor_date.isoformat() if b.anchor_date else None, "autopay": b.autopay,
        "pay_from_account_id": b.pay_from_account_id, "grace_days": b.grace_days, "late_fee": b.late_fee, "movable": b.movable, "move_window": b.move_window,
        "category": b.category, "series_id": b.series_id, "active": b.active, "paid_from": getattr(b, "paid_from", "due") or "due",
    }


@router.get("/recurring")
def list_recurring(include_classified: bool = False, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    pending = pending_series(session)
    rows = session.exec(select(RecurringSeries).order_by(RecurringSeries.typical_amount.desc())).all() if include_classified else pending
    return {"series": [series_out(s) for s in rows], "pending": len(pending)}


@router.post("/recurring/detect")
@under_writer
def detect_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return run_detection(session, today())


class ClassifyIn(BaseModel):
    kind: str
    name: str | None = None
    amount: float | None = None
    due_day: int | None = None
    autopay: bool = False
    movable: bool = True
    remaining: int | None = None


@router.get("/paystubs")
def list_paystubs(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.paystubs import paystubs_overview

    return paystubs_overview(session, limit=12)


@router.get("/payments")
def list_payments(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.payments import payments_overview

    return payments_overview(session, today())


@router.post("/recurring/{series_id}/classify")
@under_writer
def classify(series_id: int, body: ClassifyIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    series = session.get(RecurringSeries, series_id)
    if series is None:
        raise HTTPException(404, "No such series")
    if not principal.full:
        if series.confirmed:
            raise HTTPException(403, "Only the owner changes a charge that is already named")
        if body.amount is not None and abs(body.amount - series.typical_amount) > max(1.0, 0.25 * series.typical_amount):
            raise HTTPException(403, "Only the owner names a charge at an amount far from what was seen")
    if body.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(sorted(KINDS))}")
    name = (body.name or series.merchant).strip()
    amount = body.amount if body.amount is not None else series.typical_amount
    created: dict = {}
    if body.kind in ("bill", "subscription"):
        due_day = max(1, min(31, int(body.due_day or series.next_expected.day)))
        cadence = series.cadence if series.cadence in ("weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly") else "monthly"
        from ..jobs.detect import account_for_series

        bill = Bill(name=name, kind=body.kind, amount=round(amount, 2), amount_mode="fixed", cadence=cadence, due_day=due_day, autopay=body.autopay or body.kind == "subscription", movable=body.movable, pay_from_account_id=account_for_series(session, series),
                    move_window="1-28" if body.movable else "", category=series.category, series_id=series.id, anchor_date=series.next_expected if cadence != "monthly" else None, created_on=today())
        session.add(bill)
        session.flush()
        created = {"bill": bill_out(bill)}
    elif body.kind == "income":
        from ..engine.cadence import pay_day_from

        next_date = series.next_expected if series.next_expected >= today() else today()
        income = Income(name=name, cadence=series.cadence, expected_net=round(amount, 2), next_date=next_date, series_id=series.id, pay_day=pay_day_from(series.next_expected) if series.cadence == "monthly" else 0)
        session.add(income)
        session.flush()
        created = {"income": {"id": income.id, "name": income.name, "cadence": income.cadence, "expected_net": income.expected_net, "next_date": income.next_date.isoformat()}}
    elif body.kind == "installment":
        # The payments left are the owner's to say: a plan never defaults to one, which would stop counting it after
        # the next charge and make the money it still takes look free.
        if body.remaining is None or body.remaining < 1 or body.remaining > 120:
            raise HTTPException(400, "Type how many payments are left, like 4. The lender's app shows it.")
        remaining = body.remaining
        plan = InstallmentPlan(provider=name, merchant=name, total=round(amount * (series.charges_seen + remaining), 2), installment=round(amount, 2), count=series.charges_seen + remaining,
                               remaining=remaining, next_date=series.next_expected, cadence_days={"weekly": 7, "biweekly": 14, "monthly": 30}.get(series.cadence, 14))
        session.add(plan)
        session.flush()
        created = {"installment_plan": {"id": plan.id, "merchant": plan.merchant, "installment": plan.installment, "remaining": plan.remaining, "next_date": plan.next_date.isoformat()}}
    series.kind = body.kind
    series.confirmed = True
    session.add(series)
    session.commit()
    session.refresh(series)
    return {"series": series_out(series), **created, "pending": len(pending_series(session))}


# ---- bills and incomes ---------------------------------------------------------------------


@router.get("/bills")
def list_bills(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.billdates import monthly_cost

    rows = session.exec(select(Bill).where(Bill.active == True).order_by(Bill.due_day)).all()  # noqa: E712
    cost = monthly_cost(rows, today())
    return {"bills": [bill_out(b) for b in rows], "monthly_total": cost["total"], "monthly_basis": cost["basis"], "monthly_sources": cost["sources"],
            "one_time": cost["one_time"], "starting_later": cost["starting_later"], "paid_from_choices": _paid_from_choices(session)}


def _paid_from_choices(session: Session) -> list[dict]:
    """Which paycheck a bill can be paid from, as the real pay schedule runs (engine.cadence.paid_from_choices)."""
    from ..engine.cadence import paid_from_choices

    return paid_from_choices(session.exec(select(Income).where(Income.active == True)).all(), today())  # noqa: E712


def _check_paid_from(session: Session, value: str) -> None:
    if value not in PAID_FROM:
        raise HTTPException(400, "paid_from is due, mid or end")
    if value not in {c["value"] for c in _paid_from_choices(session)}:
        raise HTTPException(400, "Your paycheck does not come twice a month, so there is no 15th or end-of-month check to pay it from. It is paid from the check before it is due.")


def _need_anchor(session: Session, bill: Bill) -> None:
    """A bill that comes every few months needs its next due date: from the ledger when it shows one, else asked."""
    from ..engine.billdates import unanchored
    from ..engine.payments import anchor_from_ledger

    if not unanchored(bill):
        return
    found = anchor_from_ledger(session, bill, today())
    if found is None:
        every = {"quarterly": "quarterly", "semiannual": "twice-a-year", "yearly": "yearly"}.get(bill.cadence or "", "")
        raise HTTPException(400, f"A {every} bill needs its next due date: pick the day it is next due.")
    bill.anchor_date = found


class BillIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    amount: float | None = None
    amount_mode: str | None = None
    due_day: int | None = None
    autopay: bool | None = None
    movable: bool | None = None
    grace_days: int | None = None
    late_fee: float | None = None
    category: str | None = None
    active: bool | None = None
    paid_from: str | None = None  # due | mid | end
    pay_from_account_id: int | None = None  # the account the bill is charged to; a card means it rides the card payment
    cadence: str | None = None  # weekly | biweekly | monthly | quarterly | semiannual | yearly
    anchor_date: date | None = None  # the next due date, for a bill that is not monthly


PAID_FROM = {"due", "mid", "end"}
CLEARABLE = {"pay_from_account_id", "anchor_date"}  # sent as null, these clear; any other null leaves the field as it is
CADENCES = ("weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly")


def _check_money(body: BillIn) -> None:
    """A bill's figures are money that leaves: never below zero, never past what a bill could be."""
    if body.amount is not None and not 0 <= body.amount <= 1_000_000:
        raise HTTPException(400, "amount is dollars, from 0 to 1,000,000")
    if body.late_fee is not None and not 0 <= body.late_fee <= 10_000:
        raise HTTPException(400, "late_fee is dollars, from 0 to 10,000")
    if body.grace_days is not None and not 0 <= body.grace_days <= 90:
        raise HTTPException(400, "grace_days is 0 to 90")


def _check_payer(session: Session, account_id: int) -> None:
    from ..models import Account

    account = session.get(Account, int(account_id))
    if account is None or not account.active:
        raise HTTPException(400, "That account is not on file")


@router.post("/bills")
@under_writer
def create_bill(body: BillIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if not body.name or body.amount is None or body.due_day is None:
        raise HTTPException(400, "A bill needs a name, an amount and a due day")
    _check_money(body)
    if body.paid_from:
        _check_paid_from(session, body.paid_from)
    if body.kind is not None and body.kind not in ("bill", "subscription"):
        raise HTTPException(400, "kind must be bill or subscription")
    if body.cadence and body.cadence not in CADENCES:
        raise HTTPException(400, f"cadence is one of {', '.join(CADENCES)}")
    if body.pay_from_account_id is not None:
        _check_payer(session, body.pay_from_account_id)
    bill = Bill(name=body.name, kind=body.kind or "bill", amount=body.amount, amount_mode=body.amount_mode or "fixed", due_day=max(1, min(31, body.due_day)), paid_from=body.paid_from or "due",
                cadence=body.cadence or "monthly", anchor_date=body.anchor_date, pay_from_account_id=body.pay_from_account_id,
                autopay=bool(body.autopay), movable=body.movable if body.movable is not None else True, grace_days=body.grace_days or 0, late_fee=body.late_fee or 0.0, category=body.category or "", created_on=today())
    _need_anchor(session, bill)
    session.add(bill)
    session.commit()
    session.refresh(bill)
    mark_pending(session, f"bill added: {bill.name}")
    return bill_out(bill)


@router.put("/bills/{bill_id}")
@under_writer
def update_bill(bill_id: int, body: BillIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    bill = session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(404, "No such bill")
    rematch = False
    sent = body.model_dump(exclude_unset=True)
    _check_money(body)
    # A field left out stays as it is. A field sent empty stays too, except the two that can be cleared: the paying
    # account (back to checking, so the bill no longer rides a card) and the next due date.
    for field, value in sent.items():
        if value is None and field not in CLEARABLE:
            continue
        if field == "pay_from_account_id" and value is not None:
            _check_payer(session, value)
        if field == "due_day":
            value = max(1, min(31, int(value)))
            rematch = rematch or value != bill.due_day
        if field == "paid_from" and value != (bill.paid_from or "due"):
            _check_paid_from(session, value)
        if field == "paid_from" and value not in PAID_FROM:
            raise HTTPException(400, "paid_from is due, mid or end")
        if field == "kind" and value not in ("bill", "subscription"):
            raise HTTPException(400, "kind must be bill or subscription")
        if field == "cadence" and value not in CADENCES:
            raise HTTPException(400, f"cadence is one of {', '.join(CADENCES)}")
        if field in ("cadence", "anchor_date"):
            rematch = True
        setattr(bill, field, value)
    if bill.active and ("cadence" in sent or "anchor_date" in sent):
        _need_anchor(session, bill)  # a change of how often it comes, or of its date, needs a date it can be placed on
    session.add(bill)
    if rematch:
        from ..engine.payments import match_payments
        from ..models import Payment

        # Only the periods nothing settled are rebuilt: one matched to a bank row, or marked paid by the owner, keeps
        # its match and its mark; the new dates place the rest.
        for p in session.exec(select(Payment).where(Payment.bill_id == bill.id)).all():
            if p.status in ("scheduled", "late", "past_due", "missed"):
                session.delete(p)
        session.flush()
        match_payments(session, today())
    session.commit()
    session.refresh(bill)
    mark_pending(session, f"bill changed: {bill.name}")
    return bill_out(bill)


def income_out(i: Income) -> dict:
    return {"id": i.id, "name": i.name, "cadence": i.cadence, "expected_net": i.expected_net, "expected_net_end": i.expected_net_end, "next_date": i.next_date.isoformat(), "series_id": i.series_id,
            "pay_days": i.pay_days, "pay_day": i.pay_day}


@router.get("/incomes")
def list_incomes(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    return {"incomes": [income_out(i) for i in rows]}


PAY_CADENCES = ("weekly", "biweekly", "semimonthly", "monthly")


class NewIncomeIn(BaseModel):
    name: str = ""
    cadence: str = "biweekly"
    expected_net: float | str | None = None
    next_date: str | None = None  # YYYY-MM-DD
    pay_days: str = ""
    pay_day: int | None = None  # monthly only: the day of the month it pays, 31 for month end


def _new_income(session: Session, *, name: str, cadence: str, expected_net: float | None, next_date: date | None, pay_days: str, pay_day: int | None) -> Income:
    """A paycheck typed by the owner, checked in plain words: what it is called, how often it comes, what lands, and
    the next payday (today or later)."""
    from ..engine.cadence import PAY_DAYS, pay_day_from

    if cadence not in PAY_CADENCES:
        raise HTTPException(400, "How often it comes is one of: every week, every two weeks, twice a month, monthly (weekly, biweekly, semimonthly, monthly).")
    if expected_net is None or not 0 < float(expected_net) <= 1_000_000:
        raise HTTPException(400, "Type the take-home pay, what lands in the account, like 1850 or 1850.25.")
    if next_date is None:
        raise HTTPException(400, "Pick the next payday.")
    if next_date < today():
        raise HTTPException(400, "The next payday is today or later.")
    if (pay_days or "") not in PAY_DAYS:
        raise HTTPException(400, 'Twice a month is "the 15th and the last day" (pay_days "") or "the 1st and the 15th" (pay_days "1,15").')
    if pay_day is not None and not 1 <= int(pay_day) <= 31:
        raise HTTPException(400, "The day of the month it pays is 1 to 31; 31 means the last day of every month.")
    income = Income(name=(name or "").strip()[:80] or "Paycheck", cadence=cadence, expected_net=round(float(expected_net), 2), next_date=next_date,
                    pay_days=(pay_days or "") if cadence == "semimonthly" else "", pay_day=(int(pay_day) if pay_day else pay_day_from(next_date)) if cadence == "monthly" else 0)
    session.add(income)
    session.commit()
    session.refresh(income)
    mark_pending(session, f"paycheck added: {income.name}")
    return income


@router.post("/incomes")
@under_writer
def create_income(body: NewIncomeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Add a paycheck by hand, so the plan counts real pay on a real payday instead of a placeholder."""
    try:
        net = float(str(body.expected_net).replace(",", "").replace("$", "").strip()) if body.expected_net not in (None, "") else None
    except ValueError as error:
        raise HTTPException(400, "Type the take-home pay, what lands in the account, like 1850 or 1850.25.") from error
    try:
        when = date.fromisoformat(body.next_date) if body.next_date else None
    except ValueError as error:
        raise HTTPException(400, "Pick the next payday, as a date like 2026-10-30.") from error
    income = _new_income(session, name=body.name, cadence=body.cadence, expected_net=net, next_date=when, pay_days=body.pay_days, pay_day=body.pay_day)
    return income_out(income)


class FromPaystubIn(BaseModel):
    cadence: str = "biweekly"
    name: str = ""
    pay_days: str = ""
    pay_day: int | None = None


@router.post("/incomes/from-paystub/{paystub_id}")
@under_writer
def income_from_paystub(paystub_id: int, body: FromPaystubIn | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Use a confirmed paystub as the paycheck: its take-home pay, and its pay date stepped by the cadence to the
    next payday that is today or later."""
    from ..engine.cadence import next_paydays
    from ..models import Paystub

    body = body or FromPaystubIn()
    stub = session.get(Paystub, paystub_id)
    if stub is None:
        raise HTTPException(404, "No such paystub")
    if body.cadence not in PAY_CADENCES:
        raise HTTPException(400, "How often it comes is one of: every week, every two weeks, twice a month, monthly (weekly, biweekly, semimonthly, monthly).")
    next_date = stub.pay_date
    if next_date < today():
        later = [d for d in next_paydays(stub.pay_date, body.cadence, 60, body.pay_days or "", int(body.pay_day or 0)) if d >= today()]
        if not later:
            raise HTTPException(400, "That paystub is too old to step to the next payday; type the paycheck instead.")
        next_date = later[0]
    income = _new_income(session, name=body.name or stub.employer, cadence=body.cadence, expected_net=stub.net, next_date=next_date, pay_days=body.pay_days, pay_day=body.pay_day)
    return income_out(income)


class IncomeIn(BaseModel):
    name: str | None = None
    cadence: str | None = None
    expected_net: float | None = None
    expected_net_end: float | None = None
    next_date: date | None = None
    active: bool | None = None
    pay_days: str | None = None
    pay_day: int | None = None  # monthly only: the day of the month it pays, 31 for month end


@router.put("/incomes/{income_id}")
@under_writer
def update_income(income_id: int, body: IncomeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    income = session.get(Income, income_id)
    if income is None:
        raise HTTPException(404, "No such income")
    from ..engine.cadence import PAY_DAYS, pay_day_from

    if body.pay_days is not None and body.pay_days not in PAY_DAYS:
        raise HTTPException(400, 'pay_days is "" (the 15th and the last day) or "1,15" (the 1st and the 15th)')
    if body.pay_day is not None and not 1 <= body.pay_day <= 31:
        raise HTTPException(400, "pay_day is a day of the month, 1 to 31; 31 is the last day of every month")
    if body.cadence is not None and body.cadence not in PAY_CADENCES:
        # An unknown cadence would make every detection run fail when it steps this paycheck to its next payday.
        raise HTTPException(400, "How often it comes is one of: every week, every two weeks, twice a month, monthly (weekly, biweekly, semimonthly, monthly).")
    for field in ("expected_net", "expected_net_end"):
        value = getattr(body, field)
        if value is not None and not 0 < value <= 1_000_000:
            raise HTTPException(400, "Type the take-home pay, what lands in the account, like 1850 or 1850.25.")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(income, field, value)
    # A monthly paycheck keeps its own day of the month, so a date in a short month never pulls it earlier for good:
    # a new date that the day already lands on (Feb 28 for month end) keeps it; any other date sets it.
    if body.pay_day is None and (income.cadence or "") == "monthly" and (body.next_date is not None or not income.pay_day):
        if not (income.pay_day and _lands_on(income.pay_day, income.next_date)):
            income.pay_day = pay_day_from(income.next_date)
    session.add(income)
    session.commit()
    return {"ok": True}


def _lands_on(day: int, d: date) -> bool:
    """Whether a monthly paycheck kept on `day` pays on `d` (day 31 pays on the last day of every month)."""
    import calendar

    return min(day, calendar.monthrange(d.year, d.month)[1]) == d.day


# ---- matched pairs: transfers, card payments, the same purchase twice ----------------------------------------


@router.get("/pairs")
def list_pairs(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.pairs import pairs_overview

    return pairs_overview(session)


class PairDecisionIn(BaseModel):
    decision: str


@router.post("/pairs/{pair_id}")
@under_writer
def decide_pair(pair_id: int, body: PairDecisionIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    from .. import services
    from ..engine.pairs import decide

    try:
        out = decide(session, pair_id, body.decision)
    except KeyError as error:
        raise HTTPException(404, "No such pair") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="pair_decide", args={"pair": pair_id, "decision": body.decision}, rows=1, redaction=principal.redaction)
    return out


# ---- budgets for each paycheck ------------------------------------------------------------------------------


@router.get("/budgets")
def get_budgets(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.budgets import budget_plan

    return budget_plan(session, today())


class BudgetIn(BaseModel):
    per_paycheck: float
    rollover: str = "next"


@router.put("/budgets/{category}")
@under_writer
def put_budget(category: str, body: BudgetIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.budgets import budget_plan, save_budget

    try:
        save_budget(session, category, body.per_paycheck, body.rollover)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return budget_plan(session, today())


@router.delete("/budgets/{category}")
@under_writer
def delete_budget(category: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.budgets import budget_plan
    from ..models import CategoryBudget

    row = session.exec(select(CategoryBudget).where(CategoryBudget.category == category)).first()
    if row is None:
        raise HTTPException(404, "No budget for that category")
    row.active = False
    session.add(row)
    session.commit()
    return budget_plan(session, today())


# ---- the owner's own edits: category, note, tags, splits, many at once, a rule tried first --------------------


class TxEditIn(BaseModel):
    category: str | None = None
    note: str | None = None
    tags: list[str] | None = None
    hidden: bool | None = None


@router.patch("/transactions/{tx_id}")
@under_writer
def edit_transaction(tx_id: int, body: TxEditIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.edits import tags_of, update

    try:
        t = update(session, tx_id, category=body.category, note=body.note, tags=body.tags, hidden=body.hidden)
    except KeyError as error:
        raise HTTPException(404, "No such transaction") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"id": t.id, "category": t.category, "note": t.note, "tags": tags_of(t), "hidden": t.is_ignored}


class SplitPart(BaseModel):
    category: str
    amount: float
    note: str = ""


class SplitIn(BaseModel):
    parts: list[SplitPart]


@router.put("/transactions/{tx_id}/split")
@under_writer
def split_transaction(tx_id: int, body: SplitIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.edits import set_splits

    try:
        parts = set_splits(session, tx_id, [p.model_dump() for p in body.parts])
    except KeyError as error:
        raise HTTPException(404, "No such transaction") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"id": tx_id, "parts": [{"category": p.category, "amount": p.amount, "note": p.note} for p in parts]}


class BulkIn(BaseModel):
    ids: list[int]
    category: str | None = None
    tags_add: list[str] = []
    tags_remove: list[str] = []
    hidden: bool | None = None


@router.post("/transactions/bulk")
@under_writer
def bulk_edit(body: BulkIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    from .. import services
    from ..engine.edits import bulk

    try:
        changed = bulk(session, body.ids, category=body.category, tags_add=body.tags_add, tags_remove=body.tags_remove, hidden=body.hidden)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="transactions_bulk", args={"n": len(body.ids), "category": body.category}, rows=changed)
    return {"changed": changed}


class RulePreviewIn(BaseModel):
    kind: str
    pattern: str
    value: str = ""
    match_field: str = "merchant"


@router.post("/ledger/rules/preview")
def rule_preview(body: RulePreviewIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.edits import preview_rule

    try:
        return preview_rule(session, today(), kind=body.kind, pattern=body.pattern, value=body.value, match_field=body.match_field)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


# ---- other assets: a home, a car, anything else worth something -----------------------------------------------


@router.get("/assets")
def list_assets(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.assets import assets_overview, holdings_returns

    return {**assets_overview(session, today()), "returns": holdings_returns(session)}


class AssetIn(BaseModel):
    name: str
    kind: str = "other"
    value: float
    value_on: date | None = None
    yearly_change: float | None = None
    update_every_days: int = 180
    owed: float = 0.0
    note: str = ""


@router.post("/assets")
@under_writer
def add_asset(body: AssetIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return _save_asset(None, body, session)


@router.put("/assets/{asset_id}")
@under_writer
def update_asset(asset_id: int, body: AssetIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..models import OtherAsset

    if session.get(OtherAsset, asset_id) is None:
        raise HTTPException(404, "No such asset")
    return _save_asset(asset_id, body, session)


def _save_asset(asset_id: int | None, body: AssetIn, session: Session) -> dict:
    from ..engine.assets import assets_overview, save_asset

    try:
        save_asset(session, asset_id=asset_id, name=body.name, kind=body.kind, value=body.value, value_on=body.value_on or today(), yearly_change=body.yearly_change,
                   update_every_days=body.update_every_days, owed=body.owed, note=body.note)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return assets_overview(session, today())


@router.delete("/assets/{asset_id}")
@under_writer
def remove_asset(asset_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    from ..engine.assets import assets_overview
    from ..models import OtherAsset

    row = session.get(OtherAsset, asset_id)
    if row is None:
        raise HTTPException(404, "No such asset")
    row.active = False
    session.add(row)
    session.commit()
    return assets_overview(session, today())
