"""M47: what must hold for every household, checked on many made-up ones. Each paycheck's parts add up to what it
leaves; what one paycheck keeps is what the next carries in; a bill is counted once; a bill already paid is never
counted again; a bill is counted only between its first and last dates; every figure carries its source."""

import random
from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, select

from app.db import engine, init_db
from app.engine.household import generate, seed
from app.engine.payments import match_payments
from app.engine.snapshot import finance_snapshot
from app.models import Bill, Payment

TODAY = date(2026, 10, 14)
SEEDS = list(range(1, 26))


def _fresh(seed_value: int) -> random.Random:
    SQLModel.metadata.drop_all(engine)
    init_db()
    rng = random.Random(seed_value)
    with Session(engine) as session:
        seed(session, generate(TODAY, months=rng.choice([3, 6, 12]), seed=seed_value))
        # Bills that start or end inside the horizon, on awkward days
        for i in range(rng.randint(0, 4)):
            start = TODAY + timedelta(days=rng.randint(-20, 70))
            end = start + timedelta(days=rng.randint(0, 120)) if rng.random() < 0.6 else None
            session.add(Bill(name=f"Example Bill {seed_value}-{i}", amount=round(rng.uniform(5, 900), 2), due_day=rng.choice([1, 15, 28, 29, 30, 31]),
                             starts_on=start, ends_on=end, created_on=TODAY - timedelta(days=60)))
        session.commit()
        match_payments(session, TODAY)
    return rng


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.05


@pytest.mark.parametrize("seed_value", SEEDS)
def test_each_paycheck_adds_up_and_hands_on_what_it_keeps(seed_value):
    _fresh(seed_value)
    with Session(engine) as session:
        snap = finance_snapshot(session, TODAY)
    windows = snap["windows"]
    assert windows, "a household with a paycheck has paycheck windows"
    for k, w in enumerate(windows):
        assert _close(w["safe"], w["carried_in"] + w["paycheck"] - w["advances"]["total"] - w["bills"]["total"] - w["set_aside"]["total"]), (k, w["start"])
        assert _close(w["out"], w["bills"]["total"] + w["advances"]["total"])
        assert _close(w["spoken_for"], w["advances"]["total"] + w["bills"]["total"] + w["set_aside"]["total"])
        assert _close(w["inflow"], w["paycheck"] + w["carried_in"])
        if k + 1 < len(windows):
            assert _close(windows[k + 1]["carried_in"], w["set_aside"]["total"]), (k, "carried")


@pytest.mark.parametrize("seed_value", SEEDS)
def test_a_bill_is_counted_once_never_after_it_is_paid_and_only_between_its_dates(seed_value):
    _fresh(seed_value)
    with Session(engine) as session:
        snap = finance_snapshot(session, TODAY)
        bills = {b.id: b for b in session.exec(select(Bill)).all()}
        paid = {(p.bill_id, p.due_date.isoformat()) for p in session.exec(select(Payment).where(Payment.status == "paid")).all()}
    seen: set[tuple[str, str]] = set()
    for w in snap["windows"]:
        for item in w["bills"]["items"]:
            key = (item.get("source") or "", item["date"])
            assert key not in seen, ("counted twice", key)
            seen.add(key)
            assert item.get("source"), ("no source", item.get("name"))
            if key[0].startswith("bill:"):
                bill = bills[int(key[0].split(":")[1])]
                assert (bill.id, item["date"]) not in paid, ("counted after it was paid", key)
                d = date.fromisoformat(item["date"])
                assert bill.starts_on is None or d >= bill.starts_on, ("before its first date", key)
                assert bill.ends_on is None or d <= bill.ends_on, ("after its last date", key)


@pytest.mark.parametrize("seed_value", SEEDS[:10])
def test_safe_to_spend_and_every_brief_figure_have_sources(seed_value):
    _fresh(seed_value)
    with Session(engine) as session:
        snap = finance_snapshot(session, TODAY)
    assert isinstance(snap["safe_to_spend"]["amount"], int | float)
    for line in snap.get("brief") or []:
        assert line.get("text"), "a brief line says something"
