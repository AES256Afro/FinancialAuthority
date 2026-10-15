"""finance.snapshot, version 0: balances, the next paycheck, what is due before it, and a first safe-to-spend.

Version 0 exists so the screens and the tools have something true to show from M0. M5 replaces the
arithmetic with the full projection, set-asides and goals; the shape of the result stays.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from ..models import Account, Advance, Approval, Bill, Debt, ExtractedFact, Income, InstallmentPlan, MessageRef, RecurringSeries, Transaction
from ..store import get_setting
from .cadence import next_paydays, payday_position
from .projection import build_events, card_events, cash_of, line_of, next_due, pending_installment_events, project, projection_caveats, set_aside_of, split_windows, wdate  # noqa: F401

PACE_DAYS = 56
OBLIGATION_CATEGORIES = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities", "Borrowing"}


def usual_pace(session: Session, today: date, bill_names: set[str]) -> dict:
    """Everyday spending per day over the last eight weeks: what is spent that is not a bill."""
    since = today - timedelta(days=PACE_DAYS)
    rows = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.date < today)).all()
    names = {n.lower() for n in bill_names}
    total = 0.0
    counted = 0
    for t in rows:
        if not t.is_spending or t.amount <= 0 or t.category in OBLIGATION_CATEGORIES or t.merchant.lower() in names:
            continue
        total += t.amount
        counted += 1
    per_day = total / PACE_DAYS if counted else 0.0
    return {"per_day": _money(per_day), "per_window": _money(per_day * 14), "basis_days": PACE_DAYS, "transactions": counted}


def _money(v: float) -> float:
    return round(float(v), 2)


def _mail_facts(session: Session, today: date) -> dict:
    """What the mailboxes yielded: facts waiting for the owner, and the last three days of bank alerts."""
    import json

    pending_rows = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending").order_by(ExtractedFact.created_at.desc()).limit(30)).all()
    since = datetime.combine(today - timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc)
    alert_rows = session.exec(select(ExtractedFact).where(ExtractedFact.kind == "bank_alert", ExtractedFact.created_at >= since).order_by(ExtractedFact.created_at.desc()).limit(20)).all()
    ids = [f.message_ref_id for f in [*pending_rows, *alert_rows] if f.message_ref_id]
    refs = {r.id: r for r in session.exec(select(MessageRef).where(MessageRef.id.in_(ids))).all()} if ids else {}

    def out(f: ExtractedFact) -> dict:
        r = refs.get(f.message_ref_id)
        fields = json.loads(f.fields or "{}")
        return {"id": f.id, "kind": f.kind, "fields": fields, "confidence": f.confidence, "status": f.status, "sender_name": r.sender_name if r else fields.get("sender_name", ""),
                "subject": r.subject if r else "", "date": r.date.date().isoformat() if r and r.date else fields.get("message_date"), "source": f"fact:{f.id}"}

    return {"pending": [out(f) for f in pending_rows], "alerts": [out(f) for f in alert_rows]}


def finance_snapshot(session: Session, today: date) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    cash, cash_all = cash_of(accounts)  # the plan spends checking and cash; savings is kept and shown apart
    buffer = float(get_setting(session, "buffer", 200.0))

    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    upcoming = sorted(((i.next_date, i) for i in incomes if i.next_date >= today), key=lambda p: p[0])
    next_pay = upcoming[0] if upcoming else None
    next_payday = next_pay[0] if next_pay else today + timedelta(days=14)
    cadence = (next_pay[1].cadence if next_pay else "biweekly") or "biweekly"
    paydays = next_paydays(next_payday, cadence, 6)
    following_payday = paydays[1]

    def net_for(payday: date) -> float:
        """The usual net of the check that lands on `payday`; a semi-monthly paycheck may differ by position."""
        if next_pay is None:
            return 0.0
        if cadence == "semimonthly" and payday_position(payday) == "end" and next_pay[1].expected_net_end is not None:
            return float(next_pay[1].expected_net_end)
        return float(next_pay[1].expected_net)

    expected_net = net_for(next_payday)

    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    unconfirmed_rows = session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False, RecurringSeries.active == True)).all()  # noqa: E712
    unconfirmed = len(unconfirmed_rows)
    pending_streams = [r for r in unconfirmed_rows if r.suggested_kind == "installment"]
    caveats = projection_caveats(has_income=next_pay is not None, bills=len(bills), unnamed=unconfirmed - len(pending_streams), pending_installments=len(pending_streams))
    facts = _mail_facts(session, today)
    approvals_waiting = len(session.exec(select(Approval.id).where(Approval.status == "pending")).all())

    events = build_events(bills, plans, advances, today)
    card_mode = (get_setting(session, "cards", {}) or {}).get("pay_from", "auto")
    events = sorted(events + card_events([a for a in accounts if a.kind == "card"], paydays[:4], card_mode, today, debts) + pending_installment_events(pending_streams, today), key=lambda e: e["date"])
    windows_split = split_windows(events, today, next_payday, following_payday, paydays[2])
    before, window, next_window, advances_due = windows_split["before"], windows_split["window"], windows_split["next_window"], windows_split["advances_due"]

    due_before = sum(e["amount"] for e in before)
    window_total = sum(e["amount"] for e in window)
    set_aside = windows_split["set_aside"]
    advances_total = sum(e["amount"] for e in advances_due)
    # Safe to spend, until payday: what is in checking, less what is due before payday, the advances, the buffer,
    # and whatever the next paycheck will not cover of its own window. The paycheck itself is not spent early.
    advances_before = sum(e["amount"] for e in advances_due if e["date"] < next_payday)  # pulled before the check lands
    advances_on_payday = advances_total - advances_before  # pulled from the check itself
    next_window_shortfall = max(0.0, window_total + set_aside + advances_on_payday - expected_net)
    safe = cash - due_before - advances_before - buffer - next_window_shortfall

    # A daily path for sixty days: `available` is cash minus the buffer minus what is reserved, if nothing
    # else is spent; `at_pace` also spends the usual everyday amount each day, which is the honest line.
    pace = usual_pace(session, today, {b.name for b in bills})
    pay_dates = set(paydays[:4])
    pay_amounts = {d: net_for(d) for d in paydays[:4]}
    projection = project(today, cash=cash, buffer=buffer, expected_net=expected_net, pay_dates=pay_dates, set_aside=set_aside, events=events, per_day=pace["per_day"], pay_amounts=pay_amounts)

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
        running = carried + expected_net - adv_total
        funded_items = []
        for e in in_window:
            ok = running >= e["amount"]
            running -= e["amount"]
            funded_items.append({**e, "date": e["date"].isoformat(), "funded": ok})
        aside_items = [{**e, "date": e["date"].isoformat(), "funded": running >= aside, "share": 0.5 if e["amount"] >= 500 else 1.0} for e in following]
        windows.append({
            "start": start.isoformat(), "end": end.isoformat(), "paycheck": _money(expected_net), "carried_in": _money(carried),
            "advances": {"total": _money(adv_total), "items": [{**e, "date": e["date"].isoformat(), "funded": True} for e in adv]},
            "bills": {"total": _money(bills_total), "items": funded_items},
            "set_aside": {"total": _money(aside), "items": aside_items},
            "buffer": _money(buffer) if k == 0 else 0.0,
            "safe": _money(left),
        })
        carried = aside

    # The calendar: every event for sixty days, paydays included, and each week's low point at the usual pace.
    calendar_events = [{**e, "date": e["date"].isoformat()} for e in events if today <= e["date"] <= today + timedelta(days=60)]
    for pd in sorted(pay_dates):
        if today <= pd <= today + timedelta(days=60) and expected_net:
            calendar_events.append({"date": pd.isoformat(), "name": "Payday", "amount": _money(-expected_net), "kind": "paycheck", "autopay": True, "source": f"income:{next_pay[1].id}" if next_pay else "income"})
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

    goals = goals_plan(session, today, next_payday)

    payments = payments_overview(session, today)
    paystubs = paystubs_overview(session)
    from .borrowing import borrowing_summary

    borrowing = borrowing_summary(session, today)
    snap = {
        "version": "snapshot.v0",
        "as_of": today.isoformat(),
        "windows": windows,
        "calendar": {"events": calendar_events, "weeks": weeks},
        "payments": payments,
        "paystubs": paystubs,
        "borrowing": borrowing,
        "accounts": [
            {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector}
            for a in accounts
        ],
        "cash": _money(cash),
        "cash_all": _money(cash_all),
        "savings": _money(cash_all - cash),
        "buffer": _money(buffer),
        "next_paycheck": {"date": next_payday.isoformat(), "days": (next_payday - today).days, "amount": _money(expected_net), "source": f"income:{next_pay[1].id}" if next_pay else None, "known": next_pay is not None, "cadence": cadence},
        "paydays": [{"date": d.isoformat(), "amount": _money(net_for(d))} for d in paydays[:4]],
        "caveats": caveats,
        "due_before_payday": {"total": _money(due_before), "items": [{**e, "date": e["date"].isoformat()} for e in before]},
        "advances": {"total": _money(advances_total), "items": [{**e, "date": e["date"].isoformat()} for e in advances_due]},
        "window": {"start": next_payday.isoformat(), "end": (following_payday - timedelta(days=1)).isoformat(), "total": _money(window_total), "items": [{**e, "date": e["date"].isoformat()} for e in window]},
        "set_aside": {"total": _money(set_aside), "items": [{**e, "date": e["date"].isoformat()} for e in next_window]},
        "safe_to_spend": {
            "amount": _money(safe), "after_goals": _money(safe - goals["per_paycheck"]), "through": (next_payday - timedelta(days=1)).isoformat(), "version": "v1",
            "basis": "cash in checking, less what is due before payday, advances, the buffer, and any shortfall the next paycheck leaves in its own window",
            "parts": {"cash": _money(cash), "due_before_payday": _money(due_before), "advances": _money(advances_before), "advances_on_payday": _money(advances_on_payday), "buffer": _money(buffer), "next_window_shortfall": _money(next_window_shortfall)},
        },
        "goals": goals,
        "usual_pace": pace,
        "debts": [{"id": d.id, "name": d.name, "kind": d.kind, "balance": d.balance, "apr": d.apr, "minimum": d.minimum, "due_day": d.due_day} for d in debts],
        "pending": {"confirmations": unconfirmed + len(facts["pending"]), "series": unconfirmed, "facts": len(facts["pending"]), "approvals": approvals_waiting, "past_due": len(payments["past_due"])},
        "mail": facts,
        "projection": projection,
        "not_advice": "Computed by the engine from the registers above. Not financial advice.",
    }
    snap["brief"] = build_brief(snap)
    return snap
