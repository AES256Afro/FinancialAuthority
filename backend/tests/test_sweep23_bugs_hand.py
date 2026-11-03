"""Sweep 23: the hand model of sweep 10 (test_sweep10_bugs.py, figures recomputed from the rows without the engine)
extended to the definitions 0.10.7 moved: Cover per account and per window with the check's set-aside, Saving's
per-check table, Borrowing's per-plan monthly figure and installments_month, the brief's past-due count, and the
purchase check's by_paycheck. Checked at sweep 10's three dates, at five random days of the simulated quarter
(test_sweep8_bugs_quarter.py's rows fed to the ledger through that day, the register matched), and on five of
sweep 7's random households (test_sweep7_bugs_db.py), where the bills' cadences, cards and second incomes are beyond
the hand model's events: there the new figures are recomputed from the rows they read (plans, the past-due
register) and from the windows the engine states, so each new definition is checked even where the events are not."""

from __future__ import annotations

import random
import re
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import seed
from app.engine.payments import match_payments
from app.models import Account, Bill, Payment, Transaction
from tests.test_sweep10_bugs import BUFFER, DATES, LARGE, Rows, hand_past_due, hand_plan, key, r2

SHARE = 25  # the suggested share; no share is chosen on the fixture
QUARTER_START = date(2026, 10, 14)
QUARTER_DAYS = [QUARTER_START + timedelta(days=d) for d in sorted(random.Random(23).sample(range(1, 101), 5))]
HOUSEHOLD_SEEDS = [1, 2, 3, 4, 5]  # sweep 7's first five random households (random.Random(7100 + seed))


# ---------------------------------------------------------------------------------------------------------------
# The hand model's new definitions, from the rows and hand_plan's figures.


def hand_landed(R: Rows, today: date, H: dict) -> bool:
    """Today is a scheduled payday whose deposit has landed: the windows start at the next check."""
    (inc,) = R.incomes
    d = inc.next_date
    while d < today:
        d += timedelta(days=14)
    return d == today and H["paydays"][0] > today


def hand_saving(R: Rows, today: date, H: dict) -> list[dict]:
    """What each check leaves after the everyday cash pace over its window's days; the first window also pays the
    days before its payday (today excluded, as the daily line spends from day two), and a check that landed today
    leaves what it keeps for its window and the pace through the day before the next check."""
    pace = H["pace"]["per_day_cash"]
    rows = []
    if hand_landed(R, today, H):
        keep = sum(e["amount"] for e in H["window"] if e["amount"] >= LARGE and e["kind"] != "card_payment") / 2 + sum(e["amount"] for e in H["window"] if e["amount"] < LARGE and e["kind"] != "card_payment")
        left = H["cash"] - H["due_before"] - H["advances_before"] - BUFFER - keep - pace * (H["paydays"][0] - today).days
        rows.append({"date": today, "left": r2(left), "keep": r2(max(0.0, left) * SHARE / 100)})
    for k, w in enumerate(H["windows"]):
        days = (w["end"] - w["start"]).days + 1 + (max(0, (w["start"] - today).days - 1) if k == 0 else 0)
        left = w["safe"] - pace * days
        rows.append({"date": w["start"], "left": r2(left), "keep": r2(max(0.0, left) * SHARE / 100)})
    return rows


def hand_cover(R: Rows, today: date, H: dict) -> dict:
    """The deposit account (the fixture's one checking account) over two windows: today to the day before payday,
    and payday to the day before the next. Each needs its bills, the account's own everyday pace over its days, the
    buffer, and in the second the check's set-aside for the window after (it stays in the account)."""
    (inc,) = R.incomes
    pace = H["pace"]["by_account"].get(inc.account_id, 0.0)
    p0, p1 = H["paydays"][:2]
    events = H["events"]  # bills, plans, advances and the card minimums all leave the one checking account
    w1_bills = sum(e["amount"] for e in events if today <= e["date"] < p0)
    w2_bills = sum(e["amount"] for e in events if p0 <= e["date"] < p1)
    w1 = {"has": H["cash"], "inflow": 0.0, "bills": w1_bills, "everyday": pace * max(0, (p0 - timedelta(days=1) - today).days), "buffer": BUFFER, "set_aside": 0.0}
    w1["needs"] = w1["bills"] + w1["everyday"] + w1["buffer"]
    w1["left"] = w1["has"] - w1["needs"]
    aside = H["windows"][0]["set_aside"]
    w2 = {"has": w1["left"] + BUFFER, "inflow": H["net"], "bills": w2_bills, "everyday": pace * (p1 - p0).days, "buffer": BUFFER, "set_aside": aside}
    w2["needs"] = w2["bills"] + w2["everyday"] + w2["buffer"] + aside
    w2["left"] = w2["has"] + w2["inflow"] - w2["needs"]
    status = "crit" if r2(w1["left"]) < 0 else "warn" if r2(w2["left"]) < 0 else "good"
    return {"id": inc.account_id, "status": status, "windows": [{k: r2(v) for k, v in w.items()} for w in (w1, w2)]}


def hand_card_overdue(R: Rows, today: date) -> dict[int, list[tuple[str, date, float]]]:
    """Each card's own bill's periods past due (the bill that pays it: named after it, or at its minimum)."""
    past = hand_past_due(R, today)
    out: dict[int, list] = {}
    for a in R.accounts:
        if a.kind != "card":
            continue
        debt = next((d for d in R.debts if d.account_id == a.id), None)
        payers = {b.name for b in R.bills if key(b.category) == "credit card payment" and (key(a.name) in key(b.name) or (debt is not None and debt.minimum and abs(b.amount - debt.minimum) < 1.0))}
        out[a.id] = [p for p in past if p[0] in payers]
    return out


def plan_month(installment: float, cadence_days: int, remaining: int, balance: float | None = None) -> float:
    """A plan's monthly figure: the installment over its own cadence, never more than is left."""
    per = installment * 30.0 / max(1, cadence_days or 30)
    left = balance if balance else installment * max(0, remaining)
    return r2(min(per, left) if left > 0 else per)


def by_paycheck(amount: float, on: date, n: int, paydays: list[date]) -> list[dict]:
    """What a pay-in-n purchase adds to each of the next three checks' windows."""
    part = round(amount / n, 2)
    charges = [(on + timedelta(days=14 * i), part if i < n - 1 else round(amount - part * (n - 1), 2)) for i in range(n)]
    return [{"payday": paydays[k].isoformat(), "through": (paydays[k + 1] - timedelta(days=1)).isoformat(), "adds": r2(sum(a for d, a in charges if paydays[k] <= d < paydays[k + 1]))} for k in range(3)]


def brief_past_due(past: list[tuple[str, date, float]]) -> str | None:
    if not past:
        return None
    names = [f"{n} ${a:,.0f}" if a == int(a) else f"{n} ${a:,.2f}" for n, _, a in past]
    listed = (", ".join(names[:-1]) + " and " + names[-1]) if 1 < len(names) <= 3 else names[0] if len(names) == 1 else f"{', '.join(names[:3])} and {len(names) - 3} more"
    return f"{len(past)} bill period{'s' if len(past) != 1 else ''} past due with no payment seen: {listed}."


# ---------------------------------------------------------------------------------------------------------------
# The comparisons, on a session at a date.


def compare(session: Session, today: date) -> list[str]:
    """Every difference between the engine and the hand model's new figures, as words."""
    from app.engine.borrowing import borrowing_overview
    from app.engine.borrowing_plan import borrowing_plan
    from app.engine.cover import account_cover
    from app.engine.decisions import purchase_check
    from app.engine.reports import debt_mountain
    from app.engine.saving import saving_plan
    from app.engine.snapshot import finance_snapshot

    R = Rows(session)
    H = hand_plan(R, today)
    out: list[str] = []

    def same(what: str, got, want) -> None:  # noqa: ANN001
        if got != want:
            out.append(f"{today} {what}: engine {got!r}, hand {want!r}")

    snap = finance_snapshot(session, today)
    same("safe to spend (the base the new figures stand on)", snap["safe_to_spend"]["amount"], H["safe"])
    same("windows", [(w["start"], w["safe"], w["set_aside"]["total"]) for w in snap["windows"]], [(w["start"].isoformat(), w["safe"], w["set_aside"]) for w in H["windows"]])
    # Saving's per-check table
    saving = saving_plan(session, today)
    same("saving per check", [(r["date"], r["left"], r["keep"]) for r in saving["per_check"]], [(r["date"].isoformat(), r["left"], r["keep"]) for r in hand_saving(R, today, H)])
    # Cover, the deposit account's two windows and its status; the cards' past-due status
    cover = account_cover(session, today, saving=saving)
    hc = hand_cover(R, today, H)
    acct = next(a for a in cover["accounts"] if a["id"] == hc["id"])
    fields = ("has", "inflow", "bills", "everyday", "buffer", "set_aside", "needs", "left")
    for k, (w, h) in enumerate(zip(acct["windows"], hc["windows"], strict=True)):
        same(f"cover window {k + 1}", {f: w[f] for f in fields}, h)
    same("cover status", acct["status"], hc["status"])
    # Cover's second window leaves what Saving's first window row says (one account holds all the cash pace here)
    first = next(r for r in saving["per_check"] if r["date"] == snap["windows"][0]["start"])
    if H["pace"]["by_account"].get(hc["id"], 0.0) == H["pace"]["per_day_cash"]:
        same("cover's second window against saving's row for the same check", acct["windows"][1]["left"], first["left"])
    for card_id, overdue in hand_card_overdue(R, today).items():
        row = next(a for a in cover["accounts"] if a["id"] == card_id)
        same(f"card {card_id} past due", (row["status"] == "crit" and row["headline"].startswith("Past due")) if overdue else row["headline"].startswith("Past due"), bool(overdue))
    # Borrowing: each register plan's monthly figure, the one Debts and the stairs' loans give it, and the total
    page = borrowing_overview(session, today)
    plans = {p.id: plan_month(p.installment, p.cadence_days, p.remaining, p.balance) for p in R.plans if p.remaining > 0}
    same("borrowing per-plan monthly", {i["plan_id"]: i["per_month"] for i in page["installments"] if i["plan_id"]}, plans)
    same("borrowing installments_month", page["cost"]["installments_month"], r2(sum(i["per_month"] for i in page["installments"] if i["active"])))
    same("borrowing installments_month from the plans alone", page["cost"]["installments_month"], r2(sum(plans.values())) if not any(not i["plan_id"] and i["active"] for i in page["installments"]) else page["cost"]["installments_month"])
    same("borrowing plan loans' monthly", {l["id"]: l["per_month"] for l in borrowing_plan(session, today)["loans"]}, plans)
    same("debts' plan minimums", sorted(i["minimum"] for i in debt_mountain(session, today)["items"] if i["kind"] == "bnpl"), sorted(plans.values()))
    # The brief's past-due line
    lines = [i["text"] for i in snap["brief"] if "past due with no payment" in i["text"]]
    want = brief_past_due(hand_past_due(R, today))
    same("brief past-due line", lines, [want] if want else [])
    # The purchase check's by_paycheck, pay-in-four from today and a single charge on the next payday
    paydays = H["paydays"]
    same("by_paycheck pay in four", purchase_check(session, today, 400.0, installments=4)["by_paycheck"], by_paycheck(400.0, today, 4, paydays))
    same("by_paycheck one charge on payday", purchase_check(session, today, 120.0, on=paydays[0])["by_paycheck"], by_paycheck(120.0, paydays[0], 1, paydays))
    return out


# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("today", DATES, ids=[d.isoformat() for d in DATES])
def test_the_new_figures_at_sweep_tens_three_dates(today: date):
    with Session(engine) as session:
        seed(session)
        match_payments(session, today)
        assert compare(session, today) == []


def feed_quarter(session: Session, day: date) -> None:
    """The household seeded on its own today, then the quarter's rows through `day` in the ledger as a bank link
    lands them (a deposit is income, a card payment is one), the balances moved by them, and the register matched."""
    from tests.test_sweep8_bugs_quarter import future_rows

    seed(session)
    ids = {a.external_id: a for a in session.exec(select(Account)).all()}
    k = 0
    for d, rows in sorted(future_rows(QUARTER_START, day).items()):
        if not QUARTER_START < d <= day:
            continue
        for t in rows:
            k += 1
            acct = ids[t["account"]]
            income = t["category"] == "Income" and t["amount"] < 0
            card_pay = t["category"] == "Credit Card Payment"
            session.add(Transaction(account_id=acct.id, date=d, merchant=t["merchant"], raw_name=t["merchant"], amount=t["amount"], category=t["category"], is_income=income, is_card_payment=card_pay,
                                    is_transfer=bool(t.get("is_transfer")), row_hash=f"q23-{k}"))
            acct.balance = round((acct.balance or 0.0) + (-t["amount"] if acct.kind in ("checking", "savings") else t["amount"]), 2)
            session.add(acct)
    session.commit()
    match_payments(session, day)


@pytest.mark.parametrize("day", QUARTER_DAYS, ids=[d.isoformat() for d in QUARTER_DAYS])
def test_the_new_figures_on_random_days_of_the_simulated_quarter(day: date):
    with Session(engine) as session:
        feed_quarter(session, day)
        assert compare(session, day) == []


@pytest.mark.parametrize("seed_value", HOUSEHOLD_SEEDS)
def test_the_new_figures_on_sweep_sevens_random_households(seed_value: int):
    """Where the hand model's events do not reach (other cadences, bills on cards, two incomes), each new figure is
    recomputed from the rows it reads and the windows the engine states."""
    from app.engine.borrowing import borrowing_overview
    from app.engine.cover import account_cover
    from app.engine.decisions import purchase_check
    from app.engine.needsyou import PAST_DUE_DAYS
    from app.engine.saving import saving_plan
    from app.engine.snapshot import finance_snapshot
    from app.models import InstallmentPlan
    from tests.test_sweep7_bugs_db import _fresh, _household, _today

    rng = random.Random(7100 + seed_value)
    today = _today(rng)
    _fresh()
    out: list[str] = []
    with Session(engine) as session:
        _household(session, rng, today)
        snap = finance_snapshot(session, today)
        windows = snap["windows"]
        pace = snap["usual_pace"]["per_day_cash"]
        # Saving's per check, from the windows the engine states
        saving = saving_plan(session, today, snap=snap)
        rows = [r for r in saving["per_check"] if r["date"] != today.isoformat() or windows[0]["start"] == today.isoformat()]
        for k, (r, w) in enumerate(zip(rows, windows, strict=True)):
            s, e = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
            days = (e - s).days + 1 + (max(0, (s - today).days - 1) if k == 0 else 0)
            left = None if w["safe"] is None else w["safe"] - pace * days
            if (r["left"], r["keep"]) != (None if left is None else r2(left), 0.0 if left is None else r2(max(0.0, left) * SHARE / 100)):
                out.append(f"saving row {k}: {r} against {left}")
        # Cover: each spending account's windows add up; the deposit account's second window keeps the check's set-aside
        cover = account_cover(session, today, saving=saving)
        asides = {w["start"]: w["set_aside"] for w in saving["windows"]}
        for a in (a for a in cover["accounts"] if a["role"] == "spend" and not a.get("no_balance")):
            w1, w2 = a["windows"]
            want_aside = asides.get(w2["start"], 0.0) if a["deposit"] else 0.0
            if w2["set_aside"] != want_aside or w1["set_aside"] != 0.0:
                out.append(f"cover {a['name']} set-aside {w2['set_aside']} against {want_aside}")
            for w in (w1, w2):
                if w["needs"] != r2(w["bills"] + w["everyday"] + w["buffer"] + w["set_aside"]) or w["left"] != r2(w["has"] + w["inflow"] - w["needs"]):
                    out.append(f"cover {a['name']} {w['start']}: {w}")
            if w2["has"] != r2(w1["left"] + w1["buffer"]):
                out.append(f"cover {a['name']} second window has {w2['has']} against {r2(w1['left'] + w1['buffer'])}")
            status = "crit" if w1["left"] < 0 else "warn" if w2["left"] < 0 else "good"
            if a["status"] != status:
                out.append(f"cover {a['name']} status {a['status']} against {status}")
        # Cards: one whose own bill has a period past due is crit
        waiting = [p for p in session.exec(select(Payment).where(Payment.status == "past_due")).all() if p.due_date >= today - timedelta(days=PAST_DUE_DAYS) and session.get(Bill, p.bill_id).active]
        for a in (a for a in cover["accounts"] if a["role"] == "card"):
            mine = [p for p in waiting if (b := session.get(Bill, p.bill_id)) and b.category == "Credit Card Payment" and (key(a["name"]) in key(b.name))]
            if mine and a["status"] != "crit":
                out.append(f"card {a['name']} with {len(mine)} past due periods is {a['status']}")
        # Borrowing: every active register plan with its monthly figure, and the month's total
        page = borrowing_overview(session, today)
        plans = {p.id: plan_month(p.installment, p.cadence_days, p.remaining, p.balance) for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all() if p.remaining > 0}  # noqa: E712
        got = {i["plan_id"]: i["per_month"] for i in page["installments"] if i["plan_id"]}
        if got != plans:
            out.append(f"borrowing plans {got} against {plans}")
        if page["cost"]["installments_month"] != r2(sum(i["per_month"] for i in page["installments"] if i["active"])):
            out.append(f"installments_month {page['cost']['installments_month']}")
        # The brief's count is Needs you's: the active bills' past-due periods of the last ninety days
        lines = [i["text"] for i in snap["brief"] if "past due with no payment" in i["text"]]
        n = int(re.match(r"\d+", lines[0]).group()) if lines else 0
        if n != len(waiting):
            out.append(f"brief says {n} past due, the register holds {len(waiting)}: {lines}")
        # by_paycheck from the engine's own windows
        check = purchase_check(session, today, 400.0, installments=4)
        want = [{"payday": w["start"], "through": w["end"], "adds": r2(sum(c["amount"] for c in check["charges"] if w["start"] <= c["date"] <= w["end"]))} for w in windows[:3]]
        if check["by_paycheck"] != want:
            out.append(f"by_paycheck {check['by_paycheck']} against {want}")
    assert not out, (seed_value, today, out)
