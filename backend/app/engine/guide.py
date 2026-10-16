"""Guidance computed from the registers: the one thing today, the streaks that mean something, and the weekly
check-in. The one thing is the single action with the largest effect on the plan, said once, with why. Streaks
are counted from the ledger, so tapping cannot earn them. Nothing here moves money or sends anything."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, Payment, Transaction
from ..store import get_setting, set_setting
from .projection import is_spendable
NOT_ADVICE = "Computed by the engine from your registers and the ledger. Not financial advice."


def _usd(v: float) -> str:
    return f"${abs(float(v)):,.2f}"


def _short(d: date | str) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d:%b} {d.day}"


def streaks(session: Session, today: date, snap: dict) -> list[dict]:
    out: list[dict] = []
    last_adv = session.exec(select(Advance).order_by(Advance.taken_on.desc())).first()
    first_txn = session.exec(select(Transaction).order_by(Transaction.date)).first()
    if last_adv:
        days = (today - last_adv.taken_on).days
        out.append({"key": "no_advance", "label": f"{days} day{'s' if days != 1 else ''} without an advance", "value": days, "unit": "days", "since": last_adv.taken_on.isoformat(), "good": days >= 14, "source": f"advance:{last_adv.id}"})
    elif first_txn:
        days = (today - first_txn.date).days
        out.append({"key": "no_advance", "label": f"No advance in {days} days of history", "value": days, "unit": "days", "since": first_txn.date.isoformat(), "good": True, "source": "ledger"})
    rows = session.exec(select(Payment).where(Payment.status.in_(["paid", "late", "past_due", "missed"]), Payment.due_date <= today).order_by(Payment.due_date.desc())).all()
    run = 0
    for p in rows:
        if p.status != "paid":
            break
        run += 1
    if rows:
        out.append({"key": "on_time", "label": f"{run} bill{'s' if run != 1 else ''} paid on time in a row", "value": run, "unit": "bills", "good": run >= 3, "source": "payments"})
    # Checks in a row with money moved into savings: an inflow to a kept account inside the check's window.
    saves = [a.id for a in session.exec(select(Account).where(Account.active == True)).all() if a.kind in ("savings", "cash") and not is_spendable(a)]  # noqa: E712
    if saves:
        inflows = session.exec(select(Transaction).where(Transaction.account_id.in_(saves), Transaction.amount < 0, Transaction.date <= today, Transaction.date >= today - timedelta(days=200))).all()
        days_with = sorted({t.date for t in inflows}, reverse=True)
        cadence_days = 14 if (snap["next_paycheck"].get("cadence") or "biweekly") != "weekly" else 7
        count, edge = 0, today
        while True:
            window_start = edge - timedelta(days=cadence_days + 2)
            if any(window_start <= d <= edge for d in days_with):
                count += 1
                edge = window_start
            else:
                break
        out.append({"key": "saving", "label": f"{count} check{'s' if count != 1 else ''} in a row with money saved" if count else "No saving move yet this check", "value": count, "unit": "checks", "good": count >= 2, "source": "ledger:savings"})
    ci = get_setting(session, "checkin.log", {}) or {}
    weeks = sorted(ci.keys(), reverse=True)
    wk, probe = 0, today
    if week_key(probe) not in ci:  # this week not done yet: the run counts from last week
        probe -= timedelta(days=7)
    while week_key(probe) in ci and wk < 104:
        wk += 1
        probe -= timedelta(days=7)
    if weeks:
        out.append({"key": "checkin", "label": f"{wk} weekly check-in{'s' if wk != 1 else ''} in a row", "value": wk, "unit": "weeks", "good": wk >= 2, "source": "checkin"})
    return out


def one_thing(session: Session, today: date, *, snap: dict, cover: dict | None = None) -> dict:
    """The single most useful action today, and the next two, each with why and where."""
    cands: list[tuple[int, dict]] = []
    for p in snap["payments"]["past_due"]:
        age = (today - date.fromisoformat(p["due_date"])).days
        if age <= 14:
            cands.append((0, {"text": f"Pay {p['name']} {_usd(p['amount'])}; it was due {_short(p['due_date'])}.", "why": "A past-due bill costs a late fee and can reach the credit report at thirty days.", "link": "/bills", "source": p["source"], "amount": p["amount"], "date": p["due_date"]}))
        else:
            # An old miss is more often a payment the ledger did not match (another card, another name) than an unpaid bill.
            cands.append((4, {"text": f"Check {p['name']}: no payment of {_usd(p['amount'])} seen since it was due {_short(p['due_date'])}.", "why": "If it was paid from an account the app does not see, or under another name, mark it paid on Bills; if not, it is past due.", "link": "/bills", "source": p["source"], "amount": p["amount"], "date": p["due_date"]}))
    if cover:
        for row in cover["sheet"]:
            if row["done"] or row["date"] > (today + timedelta(days=3)).isoformat():
                continue
            if row["kind"] == "short":
                cands.append((1, {"text": f"{row['action']}: {_usd(row['amount'])} by {_short(row['date'])}.", "why": row["so_that"].capitalize() + ".", "link": "/cover", "source": row["source"], "amount": row["amount"], "date": row["date"]}))
            elif row["kind"] == "pay":
                cands.append((2, {"text": f"{row['action']}: {_usd(row['amount'])} by {_short(row['date'])}.", "why": f"So that {row['so_that']}.", "link": "/cover", "source": row["source"], "amount": row["amount"], "date": row["date"]}))
            elif row["kind"] == "move" and row["date"] <= today.isoformat():
                cands.append((5, {"text": f"{row['action']}: {_usd(row['amount'])} today.", "why": f"So that {row['so_that']}.", "link": "/saving", "source": row["source"], "amount": row["amount"], "date": row["date"]}))
    from .moves import active_moves, move_plan

    for mv in active_moves(session):
        for al in move_plan(session, mv, today)["alerts"][:4]:
            rank = {"crit": 1, "warn": 3, "info": 5}[al["severity"]]
            cands.append((rank, {"text": al["text"], "why": f"Part of {mv.name}: switched one at a time so nothing is charged to an account with no money in it.", "link": "/switch", "source": f"move:{mv.id}:{al['ref']}", "amount": None, "date": today.isoformat()}))
    for a in snap["advances"]["items"]:
        if a["date"] <= (today + timedelta(days=3)).isoformat():
            cands.append((3, {"text": f"Keep {_usd(a['amount'])} in checking for {a['name']} on {_short(a['date'])}.", "why": "The app takes it back on its own; a short account turns it into a fee.", "link": "/borrowing", "source": a["source"], "amount": a["amount"], "date": a["date"]}))
    safe = snap["safe_to_spend"]["amount"]
    if safe < 0:
        cands.append((1, {"text": f"Spend nothing extra until {_short(snap['next_paycheck']['date'])}: the plan is short {_usd(safe)}.", "why": "Safe to spend is below zero, so any extra purchase comes out of a bill.", "link": "/", "source": "snapshot:safe_to_spend", "amount": safe, "date": snap["safe_to_spend"]["through"]}))
    pend = snap["pending"]
    waiting = pend["confirmations"] + pend["approvals"]
    if waiting:
        cands.append((4, {"text": f"{waiting} thing{'s' if waiting != 1 else ''} need{'s' if waiting == 1 else ''} a look.", "why": "Bills and plans the mail or the ledger found are not counted until named.", "link": "/confirm", "source": "pending", "amount": None, "date": today.isoformat()}))
    days = snap["next_paycheck"]["days"]
    if snap["next_paycheck"].get("known") and days <= 1:
        cands.append((6, {"text": f"Payday {'today' if days == 0 else 'tomorrow'}: the sheet on Cover lists each move.", "why": "Doing the payday moves on the day keeps the windows the way the plan counts them.", "link": "/cover", "source": snap["next_paycheck"].get("source") or "income", "amount": snap["next_paycheck"]["amount"], "date": snap["next_paycheck"]["date"]}))
    cands.sort(key=lambda c: (c[0], -(abs(c[1]["amount"] or 0))))
    if not cands:
        thing = {"text": f"Nothing needs you today. Payday is in {days} day{'s' if days != 1 else ''}; safe to spend is {_usd(safe)}.", "why": "Every bill before payday is covered and nothing waits for you.", "link": "/", "source": "snapshot:safe_to_spend", "amount": safe, "date": today.isoformat()}
        status = "good"
    else:
        thing = cands[0][1]
        status = "crit" if cands[0][0] <= 1 else "warn" if cands[0][0] <= 3 else "good"
    return {"as_of": today.isoformat(), "thing": thing, "status": status, "others": [c[1] for c in cands[1:3]], "streaks": streaks(session, today, snap), "not_advice": NOT_ADVICE}


def glance(session: Session, today: date, *, snap: dict, cover: dict) -> dict:
    """The few figures a Siri Shortcut, a Home Screen widget or a watch shows, and one sentence to speak."""
    thing = one_thing(session, today, snap=snap, cover=cover)
    upcoming = [e for e in snap["calendar"]["events"] if e["date"] > today.isoformat() and e.get("kind") != "paycheck" and not e.get("via_card")]
    nxt = upcoming[0] if upcoming else None
    p = snap["next_paycheck"]
    days = p["days"]
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    status = "crit" if cover["counts"].get("crit") else "warn" if cover["counts"].get("warn") else "good"
    safe = snap["safe_to_spend"]
    text = f"Safe to spend {_usd(safe['amount'])}{' below zero' if safe['amount'] < 0 else ''} through {_short(safe['through'])}. "
    text += f"Payday {when}, {_usd(p['amount'])}. " if p.get("known") else "No payday on file. "
    text += thing["thing"]["text"]
    return {
        "as_of": today.isoformat(), "safe_to_spend": safe["amount"], "through": safe["through"], "payday": {"date": p["date"], "days": days, "amount": p["amount"]},
        "next_bill": {"name": nxt["name"], "amount": nxt["amount"], "date": nxt["date"]} if nxt else None, "one_thing": thing["thing"]["text"], "status": status,
        "cover": {"good": cover["counts"].get("good", 0), "watch": cover["counts"].get("warn", 0), "act": cover["counts"].get("crit", 0)},
        "text": text, "not_advice": NOT_ADVICE,
    }


def week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def weekly_checkin(session: Session, today: date, *, snap: dict, cover: dict | None = None) -> dict:
    """Five short questions, from the ledger: anything unexpected, one subscription to keep or drop, the card
    against its statement, whether savings moved, and next week's one thing; with one line against last week."""
    week = week_key(today)
    bill_names = {b.name.lower() for b in session.exec(select(Bill).where(Bill.active == True)).all()}  # noqa: E712
    recent = session.exec(select(Transaction).where(Transaction.date > today - timedelta(days=7), Transaction.date <= today, Transaction.amount > 0)).all()
    unexpected = sorted((t for t in recent if t.is_spending and t.merchant.lower() not in bill_names and t.category != "Borrowing"), key=lambda t: -t.amount)[:3]
    subs = sorted(session.exec(select(Bill).where(Bill.active == True, Bill.kind == "subscription")).all(), key=lambda b: b.name.lower())  # noqa: E712
    sub = subs[today.isocalendar().week % len(subs)] if subs else None
    cards = []
    for a in (cover or {}).get("accounts", []):
        if a["role"] != "card" or not a.get("statement_on"):
            continue
        st = date.fromisoformat(a["statement_on"])
        if (st - today).days <= 10:
            cards.append({"id": a["id"], "name": a["name"], "statement_on": a["statement_on"], "statement_balance": a.get("statement_balance"), "statement_utilisation": a.get("statement_utilisation"), "pay_by": a.get("pay_by"), "to_target": a.get("to_target"), "status": a["status"]})
    saves = [a.id for a in session.exec(select(Account).where(Account.active == True)).all() if a.kind in ("savings", "cash") and not is_spendable(a)]  # noqa: E712
    moved = 0.0
    if saves:
        moved = -sum(t.amount for t in session.exec(select(Transaction).where(Transaction.account_id.in_(saves), Transaction.amount < 0, Transaction.date > today - timedelta(days=14), Transaction.date <= today)).all())
    coming = sorted((e for e in snap["calendar"]["events"] if today.isoformat() < e["date"] <= (today + timedelta(days=7)).isoformat() and e.get("kind") != "paycheck" and not e.get("via_card")), key=lambda e: -e["amount"])[:3]
    log = get_setting(session, "checkin.log", {}) or {}
    last = log.get(week_key(today - timedelta(days=7)))
    cash_now = snap["cash"]
    line = f"Checking holds {_usd(cash_now)}."
    if last and last.get("cash") is not None:
        diff = cash_now - float(last["cash"])
        line = f"Checking holds {_usd(cash_now)}, {'up' if diff >= 0 else 'down'} {_usd(diff)} from your last check-in."
    return {
        "as_of": today.isoformat(), "week": week, "done": week in log, "answers": log.get(week, {}).get("answers", {}),
        "unexpected": [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": t.amount, "category": t.category, "source": f"txn:{t.id}"} for t in unexpected],
        "subscription": {"id": sub.id, "name": sub.name, "amount": sub.amount, "yearly": round(sub.amount * 12, 2), "source": f"bill:{sub.id}"} if sub else None,
        "cards": cards, "saved_14d": round(moved, 2), "coming": [{"name": e["name"], "amount": e["amount"], "date": e["date"], "source": e.get("source", "")} for e in coming],
        "line": line, "not_advice": NOT_ADVICE,
    }


def record_checkin(session: Session, today: date, answers: dict, snap: dict) -> dict:
    """Keep the week's answers. A flagged charge gets a note on its row so the ledger shows it was questioned;
    a subscription marked to drop is listed on the watchlist note. Nothing is cancelled or sent."""
    log = get_setting(session, "checkin.log", {}) or {}
    week = week_key(today)
    flagged = [int(i) for i in answers.get("flagged", []) if str(i).isdigit()]
    for t in session.exec(select(Transaction).where(Transaction.id.in_(flagged))).all() if flagged else []:
        if "questioned in a weekly check-in" not in (t.note or ""):
            t.note = ((t.note + " · ") if t.note else "") + "questioned in a weekly check-in"
            session.add(t)
    log[week] = {"at": today.isoformat(), "answers": {k: answers.get(k) for k in ("flagged", "subscription", "card_paid", "saved", "note") if k in answers}, "cash": snap["cash"]}
    keep = sorted(log.keys())[-60:]
    set_setting(session, "checkin.log", {k: log[k] for k in keep})
    session.commit()
    return {"week": week, "done": True}
