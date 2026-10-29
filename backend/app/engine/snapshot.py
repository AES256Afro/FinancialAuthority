"""finance.snapshot, version 0: balances, the next paycheck, what is due before it, and a first safe-to-spend.

Version 0 exists so the screens and the tools have something true to show from M0. M5 replaces the
arithmetic with the full projection, set-asides and goals; the shape of the result stays.
"""

from __future__ import annotations

import json

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from ..models import Account, Advance, Approval, Bill, Debt, ExtractedFact, Income, InstallmentPlan, MessageRef, RecurringSeries, Transaction
from ..store import get_setting
from .cadence import income_net, next_paydays, next_unpaid_payday, pay_schedule, payday_position, paydays_through, scheduled_payday  # noqa: F401
from .projection import HORIZON_DAYS, assemble, build_events, card_events, cash_of, line_of, next_due, pending_installment_events, project, projection_caveats, set_aside_of, split_windows, wdate  # noqa: F401

PACE_DAYS = 56
OBLIGATION_CATEGORIES = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities", "Borrowing"}


def usual_pace(session: Session, today: date, bill_names: set[str]) -> dict:
    """Everyday spending per day over the last eight weeks: what is spent that is not a bill. Split by what
    pays it: spending from checking leaves checking that day; spending on a card accrues on the card until
    the card is paid."""
    since = today - timedelta(days=PACE_DAYS)
    rows = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.date < today)).all()
    kinds = {a.id: a.kind for a in session.exec(select(Account)).all()}
    names = {n.lower() for n in bill_names}
    total = cash_total = card_total = 0.0
    by_account: dict[int, float] = {}
    counted = 0
    for t in rows:
        if not t.is_spending or t.amount <= 0 or t.category in OBLIGATION_CATEGORIES or t.merchant.lower() in names:
            continue
        total += t.amount
        by_account[t.account_id] = by_account.get(t.account_id, 0.0) + t.amount
        if kinds.get(t.account_id) == "card":
            card_total += t.amount
        else:
            cash_total += t.amount
        counted += 1
    per_day = total / PACE_DAYS if counted else 0.0
    return {
        "per_day": _money(per_day), "per_day_cash": _money(cash_total / PACE_DAYS if counted else 0.0), "per_day_card": _money(card_total / PACE_DAYS if counted else 0.0),
        "per_day_by_account": {k: _money(v / PACE_DAYS) for k, v in by_account.items()}, "per_window": _money(per_day * 14), "basis_days": PACE_DAYS, "transactions": counted,
    }


def _money(v: float) -> float:
    return round(float(v), 2) + 0.0  # + 0.0 turns a negated zero (a buffer of 0 taken off) into plain 0.0, so no "-0.00" reaches a screen


def _mail_facts(session: Session, today: date) -> dict:
    """What the mailboxes yielded: facts waiting for the owner, and the last three days of bank alerts."""
    import json

    from sqlalchemy import func

    pending_rows = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending").order_by(ExtractedFact.created_at.desc()).limit(30)).all()
    # The count is every fact waiting, not the thirty listed for the screen.
    pending_count = int(session.exec(select(func.count()).select_from(ExtractedFact).where(ExtractedFact.status == "pending")).one())
    since = datetime.combine(today - timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc)
    alert_rows = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "bank_alert", ExtractedFact.created_at >= since).order_by(ExtractedFact.created_at.desc()).limit(20)).all()
    ids = [f.message_ref_id for f in [*pending_rows, *alert_rows] if f.message_ref_id]
    refs = {r.id: r for r in session.exec(select(MessageRef).where(MessageRef.id.in_(ids))).all()} if ids else {}
    # An alert from a sender the trust check found forged (mail/trust.py) never reaches the brief: a message is data, and a
    # forged "you are overdrawn" is the bait, not a fact about the account.
    alert_rows = [f for f in alert_rows if not (f.message_ref_id in refs and refs[f.message_ref_id].trust == "crit")]

    def out(f: ExtractedFact) -> dict:
        r = refs.get(f.message_ref_id)
        fields = json.loads(f.fields or "{}")
        return {"id": f.id, "kind": f.kind, "fields": fields, "confidence": f.confidence, "status": f.status, "sender_name": _from_mail(r.sender_name if r else fields.get("sender_name", ""), 60),
                "subject": _from_mail(r.subject if r else "", 100), "date": r.date.date().isoformat() if r and r.date else fields.get("message_date"), "source": f"fact:{f.id}", "from_mail": True}

    return {"pending": [out(f) for f in pending_rows], "pending_count": pending_count, "alerts": [out(f) for f in alert_rows]}


_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 120  # a computed plan is reused until the data changes, and never for more than two minutes


def finance_snapshot(session: Session, today: date) -> dict:
    """The plan, worked out once per change of the data: the home page, the brief, Cover and the tools all ask
    for it, often several times a page. Each caller gets its own copy."""
    import copy
    import time

    from ..db import data_version

    key = (today, data_version())
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return copy.deepcopy(hit[1])
    snap = _finance_snapshot(session, today)
    if data_version() == key[1]:  # nothing was written while it was worked out
        _cache.clear()
        _cache[key] = (now, snap)
    return copy.deepcopy(snap)


def _finance_snapshot(session: Session, today: date) -> dict:
    from .projection import household_accounts

    accounts = household_accounts(session)  # the owner's and the shared accounts; a second person's own is theirs, not the plan's
    cash, cash_all = cash_of(accounts)  # the plan spends checking and cash; savings is kept and shown apart
    buffer = float(get_setting(session, "buffer", 200.0))

    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    # Every paycheck on one schedule: each payday of each, what lands on it, and the earliest as the next.
    schedule = pay_schedule(session, incomes, today, today + timedelta(days=HORIZON_DAYS + 31), at_least=6)
    next_pay = (schedule["next"], schedule["income"]) if schedule["known"] else None
    next_payday = schedule["next"]
    cadence = schedule["cadence"]
    paydays = schedule["paydays"]
    following_payday = paydays[1]

    def net_for(payday: date) -> float:
        """What lands on `payday`: the usual net of every paycheck paid that day (a semi-monthly one by its
        position in the month), added together."""
        return schedule["amounts"].get(payday, 0.0)

    expected_net = net_for(next_payday)

    from .billdates import unanchored as _unanchored
    from .borrowing import plan_covers_series
    from .payments import paid_periods, unposted_periods

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    unconfirmed_rows = session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all()  # noqa: E712
    unconfirmed = len(unconfirmed_rows)
    streams = [r for r in unconfirmed_rows if r.suggested_kind == "installment"]
    pending_streams = [r for r in streams if not any(plan_covers_series(p, r) for p in plans)]  # a plan's own payments are the plan, counted once
    caveats = projection_caveats(has_income=next_pay is not None, bills=len(bills), unnamed=unconfirmed - len(streams), pending_installments=len(pending_streams), unanchored=sum(1 for b in bills if _unanchored(b)))
    paid = paid_periods(session, today)
    late = unposted_periods(session, today)  # due in the last few days, not posted yet: still in checking, so counted today
    facts = _mail_facts(session, today)
    approvals_waiting = len(session.exec(select(Approval.id).where(Approval.status == "pending", Approval.for_person_id == None)).all())  # noqa: E711 - the owner's own; a second person's wait for them
    from ..models import TransactionPair

    pairs_waiting = len(session.exec(select(TransactionPair.id).where(TransactionPair.status == "pending")).all())
    from .needsyou import past_due_waiting as _past_due_waiting

    past_due_waiting = len(_past_due_waiting(session, today))  # the page's own list (90 days), not the register's shorter span, so badge and page agree

    from .planned import open_planned, planned_events, planned_out, planned_to_cards

    card_mode = (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")
    planned_rows = planned_events(session, today)
    pace = usual_pace(session, today, {b.name for b in bills})
    by_id = {a.id: a for a in accounts}
    # The events reach the day before the sixth payday: the fourth window's set-aside is the fifth window's bills. The fixed
    # seventy-five days stopped short of it when the next payday was more than a few days out, and for a monthly check left
    # the third and fourth windows with no bills at all (sweep 10).
    horizon_days = max(HORIZON_DAYS, (paydays[min(5, len(paydays) - 1)] - today).days)
    assembled = assemble(bills=bills, plans=plans, advances=advances, today=today, accounts=by_id, paydays=paydays, card_mode=card_mode, debts=debts, planned=planned_to_cards(session, today),
                         extra=pending_installment_events(pending_streams, today, horizon_days=horizon_days, plans=plans) + planned_rows, card_pace_by_account=pace["per_day_by_account"], paid=paid, pay_days=schedule["pay_days"],
                         late=late, checks_by_day=schedule["semimonthly"], horizon_days=horizon_days)
    events, display_events, card_bills, payoffs = assembled["math"], assembled["display"], assembled["card_bills"], assembled["payoffs"]
    windows_split = split_windows(events, today, next_payday, following_payday, paydays[2])
    shown = split_windows(display_events, today, next_payday, following_payday, paydays[2])
    before, window, next_window, advances_due = shown["before"], shown["window"], shown["next_window"], windows_split["advances_due"]
    first_payoff = payoffs[0] if payoffs else None
    on_card = [e for e in card_bills if today <= e["date"] < (first_payoff["date"] if first_payoff else today + timedelta(days=horizon_days))]

    due_before = sum(e["amount"] for e in windows_split["before"])  # what leaves checking; a bill riding the card is listed but not summed here
    window_total = sum(e["amount"] for e in windows_split["window"])
    # Due before payday, in its parts: what is already sent, the pay-later plans, the bills and subscriptions,
    # and what the next check carries instead, so one figure never looks like it holds the rent.
    group_words = {"planned": "Already sent", "card_payment": "Card payment", "installment": "Pay-later plans", "advance": "Advances", "subscription": "Subscriptions", "bill": "Bills"}
    grouped: dict[str, dict] = {}
    for e in windows_split["before"]:
        key = "planned" if e.get("planned") else (e["kind"] if e["kind"] in group_words else "bill")
        g = grouped.setdefault(key, {"key": key, "label": group_words[key], "total": 0.0, "count": 0, "names": []})
        g["total"] += e["amount"]
        g["count"] += 1
        g["names"].append(e["name"])
    due_groups = sorted(({**g, "total": _money(g["total"]), "names": g["names"][:4]} for g in grouped.values()), key=lambda g: -g["total"])
    next_check = {
        "start": next_payday.isoformat(), "total": _money(window_total), "position": payday_position(next_payday) if cadence == "semimonthly" else "",
        "top": [{"name": e["name"], "amount": e["amount"], "date": e["date"].isoformat()} for e in sorted(windows_split["window"], key=lambda e: -e["amount"])[:3]],
    }
    set_aside = windows_split["set_aside"]
    advances_total = sum(e["amount"] for e in advances_due)
    # Safe to spend, until payday: what is in checking, less what is due before payday, the advances, the buffer,
    # and whatever the next paycheck will not cover of its own window. The paycheck itself is not spent early.
    advances_before = sum(e["amount"] for e in advances_due if e["date"] < next_payday)  # pulled before the check lands
    advances_on_payday = advances_total - advances_before  # pulled from the check itself
    next_window_shortfall = max(0.0, window_total + set_aside + advances_on_payday - expected_net)
    safe = cash - due_before - advances_before - buffer - next_window_shortfall
    explain = _explain_safe(session, accounts, windows_split["before"], advances_due, next_payday, buffer=buffer, advances_before=advances_before,
                            advances_on_payday=advances_on_payday, shortfall=next_window_shortfall)

    # A daily path for sixty days: `available` is cash minus the buffer minus what is reserved, if nothing
    # else is spent; `at_pace` also spends the usual everyday amount each day, which is the honest line. Every
    # payday goes in, past the sixty days too: the line only counts the checks it reaches, but it releases a
    # reserve on the check after next, which a monthly paycheck may not reach inside them.
    pay_dates = set(paydays)
    pay_amounts = {d: net_for(d) for d in paydays}
    projection = project(today, cash=cash, buffer=buffer, expected_net=expected_net, pay_dates=pay_dates, set_aside=set_aside, events=events, per_day=pace["per_day_cash"], pay_amounts=pay_amounts, reserved_for={(e.get("source"), e["date"]) for e in windows_split["next_window"]})

    # Four paycheck windows, each filled the same way: what the paycheck carries in, what leaves it, what is left.
    windows = []
    carried = cash - due_before - buffer
    for k in range(4):
        start = paydays[k]
        end = paydays[k + 1] - timedelta(days=1)
        after_end = paydays[k + 2] - timedelta(days=1)
        in_window = [e for e in events if start <= wdate(e) <= end and e["kind"] != "advance"]
        adv = [e for e in events if e["kind"] == "advance" and (start <= e["date"] <= end or (k == 0 and e["date"] <= start))]
        following = [e for e in events if end < wdate(e) <= after_end and e["kind"] != "advance"]
        aside = set_aside_of(following)
        bills_total = sum(e["amount"] for e in in_window)
        adv_total = sum(e["amount"] for e in adv)
        left = carried + net_for(start) - adv_total - bills_total - aside
        # Funded, in date order: the money runs out on the last bills when a window is short.
        running = carried + net_for(start) - adv_total
        funded_items = []
        for e in in_window:
            ok = running >= e["amount"]
            running -= e["amount"]
            funded_items.append({**e, "date": e["date"].isoformat(), "funded": ok})
        funded_items += [{**e, "date": e["date"].isoformat(), "funded": True} for e in card_bills if start <= wdate(e) <= end]
        funded_items.sort(key=lambda e: e["date"])
        aside_items = [{**e, "date": e["date"].isoformat(), "funded": running >= aside, "share": 0.5 if e["amount"] >= 500 else 1.0,
                        "kept": _money(e["amount"] * (0.5 if e["amount"] >= 500 else 1.0))} for e in following if e.get("kind") != "card_payment"]
        windows.append({
            "start": start.isoformat(), "end": end.isoformat(), "paycheck": _money(net_for(start)), "carried_in": _money(carried),
            "inflow": _money(net_for(start) + carried),
            "advances": {"total": _money(adv_total), "items": [{**e, "date": e["date"].isoformat(), "funded": True} for e in adv]},
            "bills": {"total": _money(bills_total), "items": funded_items, **_bill_groups(funded_items)},
            "set_aside": {"total": _money(aside), "items": aside_items},
            "buffer": _money(buffer) if k == 0 else 0.0,  # shown, and already kept out of what the first window carries in
            "out": _money(bills_total + adv_total),
            "spoken_for": _money(adv_total + bills_total + aside),
            "safe": _money(left),
        })
        carried = aside

    # The calendar: every event for sixty days, paydays included, and each week's low point at the usual pace.
    calendar_events = [{**e, "date": e["date"].isoformat()} for e in display_events if today <= e["date"] <= today + timedelta(days=60)]
    for pd in sorted(pay_dates):
        if not today <= pd <= today + timedelta(days=60):
            continue
        for income in schedule["incomes_on"].get(pd, []):  # each check as it lands that day, an end-of-month one at its own net
            amount = income_net(income, pd)
            if amount:
                calendar_events.append({"date": pd.isoformat(), "name": "Payday" if len(incomes) == 1 else f"Payday: {income.name}", "amount": _money(-amount), "kind": "paycheck", "autopay": True, "source": f"income:{income.id}"})
    calendar_events.sort(key=lambda e: e["date"])
    weeks = []
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # the Sunday on or before today
    while week_start <= today + timedelta(days=60):
        week_end = week_start + timedelta(days=6)
        in_week = [p for p in projection if week_start.isoformat() <= p["date"] <= week_end.isoformat()]
        low = min(in_week, key=lambda p: p["at_pace"]) if in_week else None
        weeks.append({"start": week_start.isoformat(), "end": week_end.isoformat(), "low": low["at_pace"] if low else None, "low_date": low["date"] if low else None})
        week_start += timedelta(days=7)

    from .brief import build_brief
    from .goals import goals_plan
    from .payments import payments_overview
    from .paystubs import paystubs_overview

    goals = goals_plan(session, today, next_payday, cadence, days=schedule["days"])

    # Payday is the day a check is scheduled, whether or not it has posted: a deposit that landed in the last
    # two days is today's check. Once it has landed the windows start at the next check, so what today's check
    # keeps for that window and what it leaves after it are worked out here, for the payday moves.
    paid_today = [i for i in incomes if scheduled_payday(i, today) == today]
    landed = bool(paid_today) and next_payday > today
    payday = {"today": bool(paid_today), "date": (today if paid_today else next_payday).isoformat(), "amount": _money(sum(income_net(i, today) for i in paid_today) if paid_today else expected_net),
              "landed": landed, "sources": [f"income:{i.id}" for i in paid_today]}
    if landed:
        keep = set_aside_of(windows_split["window"])
        payday.update(set_aside=_money(keep), left=_money(cash - due_before - advances_before - buffer - keep), end=(next_payday - timedelta(days=1)).isoformat())

    payments = payments_overview(session, today)
    paystubs = paystubs_overview(session)
    from .borrowing import borrowing_summary

    borrowing = borrowing_summary(session, today)
    from .projection import PATH_WORDS
    from .reports import owed

    everything_owed = owed(session, today, accounts)  # the Debts page's and Accounts' own list and total
    snap = {
        "version": "snapshot.v0",
        "as_of": today.isoformat(),
        "windows": windows,
        "calendar": {"events": calendar_events, "weeks": weeks},
        "payments": payments,
        "paystubs": paystubs,
        "borrowing": borrowing,
        "planned": [planned_out(p) for p in open_planned(session, today)],
        "on_card": {"total": _money(sum(e["amount"] for e in on_card)), "items": [{**e, "date": e["date"].isoformat()} for e in on_card], "paid_on": first_payoff["date"].isoformat() if first_payoff else None},
        "card_payments": [{**e, "date": e["date"].isoformat()} for e in payoffs],
        "accounts": [
            {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector}
            for a in accounts
        ],
        "cash": _money(cash),
        "cash_all": _money(cash_all),
        "savings": _money(cash_all - cash),
        "buffer": _money(buffer),
        "next_paycheck": {"date": next_payday.isoformat(), "days": (next_payday - today).days, "amount": _money(expected_net), "source": f"income:{next_pay[1].id}" if next_pay else None, "known": next_pay is not None, "cadence": cadence,
                          "pay_days": schedule["days"]["pay_days"], "pay_day": schedule["days"]["pay_day"]},
        "payday": payday,
        "paydays": [{"date": d.isoformat(), "amount": _money(net_for(d))} for d in paydays[:4]],
        "caveats": caveats,
        "due_before_payday": {"total": _money(due_before), "items": [{**e, "date": e["date"].isoformat()} for e in before], "groups": due_groups, "next_check": next_check},
        "advances": {"total": _money(advances_total), "items": [{**e, "date": e["date"].isoformat()} for e in advances_due]},
        "window": {"start": next_payday.isoformat(), "end": (following_payday - timedelta(days=1)).isoformat(), "total": _money(window_total), "items": [{**e, "date": e["date"].isoformat()} for e in window]},
        "set_aside": {"total": _money(set_aside), "items": [{**e, "date": e["date"].isoformat()} for e in next_window]},
        "safe_to_spend": {
            "amount": _money(safe), "after_goals": _money(safe - goals["per_paycheck"]), "share_of_cash": round(max(0.0, min(1.0, safe / cash)), 4) if cash > 0 else 0.0, "through": max(today, next_payday - timedelta(days=1)).isoformat(), "version": "v1",
            "basis": "cash in checking, less what is due before payday, advances, the buffer, and any shortfall the next paycheck leaves in its own window",
            "parts": {"cash": _money(cash), "due_before_payday": _money(due_before), "advances": _money(advances_before), "advances_on_payday": _money(advances_on_payday), "buffer": _money(buffer), "next_window_shortfall": _money(next_window_shortfall)},
            **explain,
        },
        "goals": goals,
        "usual_pace": {**pace, "over_safe": _money(max(0.0, pace["per_window"] - safe))},
        "board": _board(display_events, before, windows[0] if windows else None, payments, today),
        "fresh": _freshness(session, accounts),
        "in_flight": _in_flight(session, today),
        # Everything owed, as the Debts page ("Owed in all") and Accounts ("You owe") count it: cards and loans, the
        # pay-later plans and the advances, each once. The cards and loans alone stay apart, named as such.
        "debts": [{"id": d["id"], "name": d["name"], "kind": d["kind"], "balance": d["balance"], "apr": d.get("apr"), "minimum": d.get("minimum"), "due_day": d.get("due_day"), "source": d["source"]} for d in everything_owed["items"]],
        "debts_total": everything_owed["total"],
        "debts_words": everything_owed["words"],
        "debts_cards_loans_total": _money(sum((d.balance or 0.0) for d in debts)),
        # "confirmations" is what the Needs you page lists (the series to name, the facts, the pairs, the bills with no
        # payment seen), so its badge and the page's own count agree.
        "pending": {"total": unconfirmed + facts["pending_count"] + pairs_waiting + past_due_waiting + approvals_waiting, "confirmations": unconfirmed + facts["pending_count"] + pairs_waiting + past_due_waiting, "series": unconfirmed, "facts": facts["pending_count"], "pairs": pairs_waiting, "approvals": approvals_waiting, "past_due": past_due_waiting},
        "mail": facts,
        "projection": projection,
        "projection_words": PATH_WORDS,
        "not_advice": "Computed by the engine from the registers above. Not financial advice.",
    }
    snap["brief"] = build_brief(snap)
    from .explain import why_all

    snap["why"] = why_all(snap)
    return snap


def _explain_safe(session: Session, accounts: list, before: list[dict], advances_due: list[dict], next_payday: date, *, buffer: float, advances_before: float,
                  advances_on_payday: float, shortfall: float) -> dict:
    """Safe to spend taken apart: each spending account's share, the steps from the bank's balance to the figure,
    and what is left out on purpose (savings, the cards, what the next paycheck repays). The accounts add up to
    the figure. The buffer, the advances and any shortfall belong to the account the paycheck lands in."""
    from ..connectors.banks import told_balances
    from .cover import _deposit_account, _payer_id
    from .projection import is_spendable

    by_id = {a.id: a for a in accounts}
    told = told_balances(session)
    spendables = [a for a in accounts if is_spendable(a) and a.kind != "card"]
    deposit = _deposit_account(session, by_id, spendables)
    deposit_id = deposit.id if deposit else None
    rows = {a.id: {"id": a.id, "name": a.name, "kind": a.kind, "balance": float(a.balance or 0.0), "as_of": a.balance_at.isoformat() if a.balance_at else None,
                   "from_bank": a.connector in ("plaid", "teller"), "told": a.id in told and abs(told[a.id]["amount"] - float(a.balance or 0.0)) < 0.005, "paycheck_lands": a.id == deposit_id, "due": 0.0, "items": [], "buffer": 0.0, "advances": 0.0, "shortfall": 0.0}
            for a in spendables}
    for e in before:
        row = rows.get(_payer_id(e, by_id, deposit_id)) or rows.get(deposit_id)
        if row is None:
            continue
        row["due"] += e["amount"]
        row["items"].append({"name": e["name"], "amount": _money(e["amount"]), "date": e["date"].isoformat(), "source": e.get("source"), **({"note": e["note"], "due": e["due"]} if e.get("late") else {})})
    if deposit_id in rows:
        rows[deposit_id].update(buffer=buffer, advances=advances_before, shortfall=shortfall)
    by_account = []
    for r in rows.values():
        r["goes_out"] = _money(r["due"] + r["buffer"] + r["advances"] + r["shortfall"])
        r["safe"] = _money(r["balance"] - r["goes_out"])
        for k in ("balance", "due", "buffer", "advances", "shortfall"):
            r[k] = _money(r[k])
        r["items"] = sorted(r["items"], key=lambda i: i["date"])
        by_account.append(r)
    steps: list[dict] = [{"label": f"In {r['name']}", "amount": r["balance"], "note": "as you read it off your bank" if r.get("told") else "as your bank reported it" if r["from_bank"] else "as entered", "as_of": r["as_of"]} for r in by_account]
    due_items = sorted((i for r in by_account for i in r["items"]), key=lambda i: i["date"])
    if due_items:
        steps.append({"label": f"Comes out before payday ({next_payday:%b} {next_payday.day})", "amount": _money(-sum(i["amount"] for i in due_items)), "items": due_items})
    if advances_before:
        steps.append({"label": "Advances taken back before payday", "amount": _money(-advances_before)})
    steps.append({"label": "Your buffer, kept in case", "amount": _money(-buffer), "note": "change it on Settings › Models › Your rules and goals"})  # a buffer of 0 is 0.0, not -0.0
    if shortfall:
        steps.append({"label": "What the next paycheck cannot cover of its own bills", "amount": _money(-shortfall), "note": "kept now, so that check is not short"})
    left_out: list[dict] = []
    savings = [a for a in accounts if a.kind == "savings" and (a.balance or 0)]
    for a in savings:
        left_out.append({"name": a.name, "verb": "has", "amount": _money(a.balance or 0.0), "why": "Savings is kept apart and never counted as money to spend."})
    paid_before = {str(e.get("source")) for e in before}
    from ..models import PlannedPayment

    card_of = {f"debt:{d.id}": d.account_id for d in session.exec(select(Debt)).all() if d.account_id}
    card_of.update({f"planned:{p.id}": p.to_account_id for p in session.exec(select(PlannedPayment)).all() if p.to_account_id})
    for a in accounts:
        if a.kind != "card" or not (a.balance or 0):
            continue
        owes = float(a.balance or 0.0)
        if owes < 0:
            left_out.append({"name": a.name, "verb": "holds a credit of", "amount": _money(-owes), "why": "A credit on a card pays its next charges; it is not cash to spend."})
            continue
        counted = any(src == f"account:{a.id}" or card_of.get(src) == a.id for src in paid_before)
        why = ("A card is not money to spend. Its payment before payday is counted above." if counted else
               "A card is not money to spend. Its next payment falls on or after payday, on the paycheck that carries it.")
        left_out.append({"name": a.name, "verb": "owes", "amount": _money(owes), "why": why, "owes": True})
    if advances_on_payday:
        names = sorted({e["name"].removesuffix(" repays") for e in advances_due if e["date"] >= next_payday})
        left_out.append({"name": " and ".join(names) or "Advances", "verb": "take back" if len(names) > 1 else "takes back", "amount": _money(advances_on_payday),
                         "why": f"On payday ({next_payday:%b} {next_payday.day}), from that paycheck, not from what is here now."})
    return {"by_account": by_account, "steps": steps, "left_out": left_out}


def _from_mail(text: str | None, limit: int) -> str:
    """A name or subject from an email, on one line and short: it is data, shown as written, never instructions."""
    import re as _re

    clean = _re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", text or "")
    clean = _re.sub(r"\s+", " ", clean).strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


SMALL_BILL = 25.0  # bills under this are grouped as "small stuff" in the pictures


def _bill_groups(items: list[dict]) -> dict:
    """The groups the pictures draw a window's bills in: card payments split into their parts, other bills, the
    small ones together, and what is left after the eight largest."""
    own = [e for e in items if not e.get("via_card")]  # a bill riding a card is in the card payment's bills part already
    card = [e for e in own if e.get("kind") == "card_payment"]
    other = [e for e in own if e.get("kind") != "card_payment"]
    small = [e for e in own if e["amount"] < SMALL_BILL]
    by_size = sorted(own, key=lambda e: -e["amount"])
    rest = by_size[8:]
    return {
        "card_parts": {k: _money(sum((e.get("parts") or {}).get(k, 0.0) for e in card)) for k in ("balance", "bills", "spend")},
        "other_total": _money(sum(e["amount"] for e in other)),
        "small": {"total": _money(sum(e["amount"] for e in small)), "count": len(small), "names": [e["name"] for e in small][:4], "all_funded": all(e.get("funded") for e in small), "under": SMALL_BILL},
        "after_eight": {"total": _money(sum(e["amount"] for e in rest)), "count": len(rest)},
    }


def _board(display_events: list[dict], before: list[dict], window: dict | None, payments: dict, today: date) -> dict:
    """The bill board's four columns, with their totals: past due or unmatched, due within a week, later this
    paycheck, paid recently."""
    problems = payments["past_due"] + payments["late"]
    # A bill due a day or two ago that has not posted is counted today; when the late column already shows that
    # period, it is not listed again as due this week.
    shown_late = {(p["bill_id"], p["due_date"]) for p in problems}
    upcoming = [{**e, "date": e["date"].isoformat() if hasattr(e["date"], "isoformat") else e["date"]} for e in before if not (e.get("late") and (e.get("bill_id"), e.get("due")) in shown_late)]
    upcoming += list((window or {}).get("bills", {}).get("items", []))
    soon = (today + timedelta(days=7)).isoformat()
    due7 = [e for e in upcoming if e["date"] <= soon]
    later = [e for e in upcoming if e["date"] > soon]
    paid = list(reversed(payments["paid"][-10:]))

    def col(rows: list[dict]) -> dict:
        return {"total": _money(sum(r["amount"] for r in rows)), "items": rows}

    return {"problems": col(problems), "due_7": col(due7), "later": col(later), "paid": col(paid)}


def _freshness(session: Session, accounts: list) -> dict:
    """How fresh the figures are: when each spending account's balance was last reported, the oldest of them,
    and the newest transaction."""
    from sqlalchemy import func

    from ..connectors.banks import told_balances

    spending = [a for a in accounts if a.kind in ("checking", "cash", "savings", "card")]
    stamps = [a.balance_at for a in spending if a.balance_at]
    newest = session.exec(select(func.max(Transaction.date))).one()
    told = told_balances(session)
    return {
        "balances_as_of": min(stamps).isoformat() if stamps else None,
        "accounts": [{"id": a.id, "name": a.name, "kind": a.kind, "as_of": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector, "balance": a.balance,
                      **({"told": told[a.id]} if a.id in told and abs(told[a.id]["amount"] - float(a.balance or 0.0)) < 0.005 else {})} for a in spending],
        "newest_transaction": newest.isoformat() if newest else None,
    }


def _in_flight(session: Session, today: date) -> dict:
    """What has happened but is not posted: charges the bank holds as pending (already off the checking balance the
    bank reports), and purchases a bank's alert email told of that no bank row matches yet. The mail ones are
    shown, never taken off: they are what the email said, and the bank's own row replaces them."""
    from ..models import ExtractedFact

    pending = session.exec(select(Transaction).where(Transaction.pending == True, Transaction.date >= today - timedelta(days=14)).order_by(Transaction.date.desc())).all()  # noqa: E712
    names = {a.id: a.name for a in session.exec(select(Account)).all()}
    since = datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)  # the injected day, never the wall clock
    alerts = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "bank_alert", ExtractedFact.created_at >= since)).all()
    forged = set(session.exec(select(MessageRef.id).where(MessageRef.id.in_([f.message_ref_id for f in alerts if f.message_ref_id]), MessageRef.trust == "crit")).all()) if alerts else set()  # type: ignore[union-attr]
    seen = []
    for f in alerts:
        try:
            fields = json.loads(f.fields or "{}")
        except ValueError:
            continue
        if fields.get("alert") != "purchase" or not isinstance(fields.get("amount"), (int, float)) or f.message_ref_id in forged:
            continue  # a purchase a forged alert told of is no purchase
        when = date.fromisoformat(str(fields.get("message_date") or f.created_at.date().isoformat())[:10])
        amount = float(fields["amount"])
        posted = session.exec(select(Transaction.id).where(Transaction.amount >= amount - 0.01, Transaction.amount <= amount + 0.01, Transaction.date >= when - timedelta(days=1), Transaction.date <= when + timedelta(days=6)).limit(1)).first()
        if posted is not None:
            continue
        seen.append({"amount": _money(amount), "merchant": _from_mail(str(fields.get("merchant") or ""), 60), "last4": str(fields.get("last4") or "")[-4:], "date": when.isoformat(), "source": f"fact:{f.id}"})
    return {
        "pending": {"total": _money(sum(t.amount for t in pending)), "items": [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": _money(t.amount), "account": names.get(t.account_id, ""), "source": f"transaction:{t.id}"} for t in pending]},
        "mail": {"total": _money(sum(s["amount"] for s in seen)), "items": seen, "counted": False},
    }
