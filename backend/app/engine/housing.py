"""Housing: each home lived in, its lease or mortgage, and what it costs, in one place.

A home's charges paid with the rent (the rent itself, pet rent, the property's fees, escrow) make one bill, due on
the home's due day from its first regular payment to the end of the lease. A charge paid to someone else (renter's
insurance) makes its own bill. A utility billed on its own makes its own bill. The one-time costs of getting in
(the move-in check, fees, the truck) become one-time bills on their dates. So housing is entered once, here, and
every figure counts it the way it counts any bill: safe to spend, Cover, budgets, the outlook, net worth. The bank's
payments are matched to those bills like any other, so nothing is counted twice.

Moving is a home with a start date: the costs to get in are its own, the old home ends on the move date, and the
notice the old lease needs is a dated step until it is given."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Bill, Document, Home, HomeCharge, HomeCost, Utility
from .billdates import bill_dates

KINDS = ("rent", "mortgage", "other")
CHARGE_KINDS = ("rent", "pet", "fee", "utility", "insurance", "principal_interest", "escrow", "pmi", "hoa", "parking", "other")
SERVICES = ("electric", "water", "gas", "internet", "trash", "sewer", "phone", "other")
BILLED = ("direct", "landlord", "submetered", "included")
SERVICE_WORDS = {"electric": "Electricity", "water": "Water", "gas": "Gas", "internet": "Internet", "trash": "Trash", "sewer": "Sewer", "phone": "Phone", "other": "Service"}
BILLED_WORDS = {"direct": "billed to you by the provider", "landlord": "paid with the rent", "submetered": "measured at your unit and billed by a billing company", "included": "included in the rent"}


def _money(v: float) -> float:
    return round(float(v), 2)


def charges_of(session: Session, home_id: int) -> list[HomeCharge]:
    return list(session.exec(select(HomeCharge).where(HomeCharge.home_id == home_id, HomeCharge.active == True).order_by(HomeCharge.id)).all())  # noqa: E712


def costs_of(session: Session, home_id: int) -> list[HomeCost]:
    return list(session.exec(select(HomeCost).where(HomeCost.home_id == home_id, HomeCost.active == True).order_by(HomeCost.due_on, HomeCost.id)).all())  # noqa: E712


def _bill(session: Session, bill_id: int | None) -> Bill | None:
    return session.get(Bill, bill_id) if bill_id else None


def _end_due(home: Home) -> date | None:
    """The last day a regular payment can come due: the day before moving out."""
    return home.ends_on - timedelta(days=1) if home.ends_on else None


def sync_bills(session: Session, home: Home) -> None:
    """Make the home's bills match its charges, costs and dates. Bills it no longer needs are turned off."""
    charges = charges_of(session, home.id)
    with_rent = [c for c in charges if c.pays_to == "home"]
    known = [c for c in with_rent if c.amount is not None]
    word = {"rent": "Rent", "mortgage": "Mortgage"}.get(home.kind, "Housing")
    bill = _bill(session, home.bill_id)
    if known and home.active:
        bill = bill or Bill(name="", amount=0.0, created_on=date.today())
        bill.name = f"{word} · {home.name}"[:80]
        bill.amount = _money(sum(c.amount for c in known))
        bill.amount_mode = "estimate" if any(c.estimate for c in known) or len(known) < len(with_rent) else "fixed"
        bill.cadence, bill.due_day = "monthly", max(1, min(31, home.due_day or 1))
        bill.starts_on = home.first_due or home.starts_on
        bill.ends_on = _end_due(home)
        bill.autopay, bill.grace_days, bill.late_fee = home.autopay, home.grace_days, home.late_fee
        bill.pay_from_account_id = home.pay_from_account_id or bill.pay_from_account_id
        bill.category, bill.kind, bill.home_id, bill.active = word if word != "Housing" else "Rent", "bill", home.id, True
        bill.pays_to = home.payee or bill.pays_to
        session.add(bill)
        session.flush()
        home.bill_id = bill.id
    elif bill is not None:
        bill.active = False
        session.add(bill)
    for c in charges:
        if c.pays_to == "home":
            if c.bill_id and (b := _bill(session, c.bill_id)):
                b.active = False
                session.add(b)
            continue
        b = _bill(session, c.bill_id)
        if c.amount is not None and home.active:
            b = b or Bill(name="", amount=0.0, created_on=date.today())
            b.name, b.amount = f"{c.name}"[:80], _money(c.amount)
            b.amount_mode = "estimate" if c.estimate else "fixed"
            b.cadence, b.due_day = "monthly", max(1, min(31, c.due_day or home.due_day or 1))
            b.starts_on, b.ends_on = c.starts_on or home.first_due or home.starts_on, c.ends_on or _end_due(home)
            b.category = "Insurance" if c.kind == "insurance" else "Housing"
            b.pays_to = c.payee or b.pays_to
            b.kind, b.home_id, b.active = "bill", home.id, True
            session.add(b)
            session.flush()
            c.bill_id = b.id
            session.add(c)
        elif b is not None:
            b.active = False
            session.add(b)
    for cost in costs_of(session, home.id):
        b = _bill(session, cost.bill_id)
        if cost.amount is not None and cost.amount > 0 and cost.due_on and not cost.paid and home.active:
            b = b or Bill(name="", amount=0.0, created_on=date.today())
            b.name, b.amount = f"{cost.name}"[:80], _money(cost.amount)
            b.cadence, b.due_day, b.starts_on, b.ends_on = "monthly", cost.due_on.day, cost.due_on, cost.due_on  # one date, once
            b.category, b.kind, b.home_id, b.active, b.amount_mode = "Moving", "bill", home.id, True, "fixed"
            b.pays_to = home.payee or b.pays_to
            session.add(b)
            session.flush()
            cost.bill_id = b.id
            session.add(cost)
        elif b is not None:
            b.active = False
            session.add(b)
    session.add(home)
    session.commit()


def sync_utility(session: Session, u: Utility) -> None:
    """A utility billed on its own (directly, or by a billing company) with a known amount makes its own bill."""
    b = _bill(session, u.bill_id)
    if u.active and u.billed in ("direct", "submetered") and u.amount is not None:
        b = b or Bill(name="", amount=0.0, created_on=date.today())
        who = u.billing_company if u.billed == "submetered" and u.billing_company else u.provider
        b.name = f"{SERVICE_WORDS.get(u.service, 'Service')} · {who}".strip(" ·")[:80]
        b.amount, b.amount_mode = _money(u.amount), "estimate" if u.estimate else "fixed"
        b.cadence, b.due_day = "monthly", max(1, min(31, u.due_day or 1))
        b.starts_on, b.ends_on = u.starts_on, u.ends_on
        b.category, b.kind, b.utility_id, b.active = b.category or "Utilities", "bill", u.id, True
        b.pays_to = who or b.pays_to
        session.add(b)
        session.flush()
        u.bill_id = b.id
    elif b is not None:
        b.active = False
        session.add(b)
    session.add(u)
    session.commit()


def adopt_bill(session: Session, bill: Bill, *, name: str, kind: str = "rent") -> Home:
    """A rent bill already on the list becomes a home: its amount the rent, its due day the home's."""
    home = Home(name=name.strip()[:80] or bill.name, kind=kind if kind in KINDS else "rent", payee=bill.pays_to or bill.name, due_day=bill.due_day, autopay=bill.autopay, grace_days=bill.grace_days,
                late_fee=bill.late_fee, pay_from_account_id=bill.pay_from_account_id, bill_id=bill.id)
    session.add(home)
    session.flush()
    session.add(HomeCharge(home_id=home.id, name="Rent" if home.kind == "rent" else "Payment", kind="rent" if home.kind == "rent" else "principal_interest", amount=_money(bill.amount)))
    bill.home_id = home.id
    session.add(bill)
    session.commit()
    sync_bills(session, home)
    return home


def notice(home: Home, today: date) -> dict[str, Any] | None:
    """The written notice the lease needs before moving out: by when, and whether it is given."""
    if not home.ends_on or not home.notice_days:
        return None
    # A day early: leases count the notice day differently, and a day early holds under either count.
    by = home.ends_on - timedelta(days=home.notice_days + 1)
    if home.notice_given_on:
        return {"by": by.isoformat(), "given_on": home.notice_given_on.isoformat(), "status": "good", "word": f"Notice given on {home.notice_given_on:%a %b} {home.notice_given_on.day}"}
    left = (by - today).days
    if left < 0:
        return {"by": by.isoformat(), "given_on": None, "status": "crit", "word": f"Notice was due by {by:%a %b} {by.day}. Give it in writing now, and ask the office to confirm the move-out date."}
    status = "crit" if left <= 7 else "warn"
    return {"by": by.isoformat(), "given_on": None, "status": status, "days_left": left, "word": f"Give written notice by {by:%a %b} {by.day} ({left} day{'s' if left != 1 else ''} left)"}


def _charge_out(c: HomeCharge) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "kind": c.kind, "amount": c.amount, "estimate": c.estimate, "pays_to": c.pays_to, "payee": c.payee, "due_day": c.due_day,
            "starts_on": c.starts_on.isoformat() if c.starts_on else None, "ends_on": c.ends_on.isoformat() if c.ends_on else None, "note": c.note, "known": c.amount is not None, "bill_id": c.bill_id}


def _cost_out(c: HomeCost) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "amount": c.amount, "due_on": c.due_on.isoformat() if c.due_on else None, "refundable": c.refundable, "parts": json.loads(c.parts or "[]"),
            "paid": c.paid, "known": c.amount is not None, "note": c.note, "bill_id": c.bill_id, "counted": bool(c.bill_id) and c.amount is not None and not c.paid}


def utility_out(u: Utility) -> dict[str, Any]:
    return {"id": u.id, "home_id": u.home_id, "service": u.service, "what": SERVICE_WORDS.get(u.service, "Service"), "provider": u.provider, "billed": u.billed, "billed_word": BILLED_WORDS.get(u.billed, u.billed),
            "billing_company": u.billing_company, "account_last4": "set" if u.account_last4_enc else "", "amount": u.amount, "estimate": u.estimate, "due_day": u.due_day,
            "starts_on": u.starts_on.isoformat() if u.starts_on else None, "ends_on": u.ends_on.isoformat() if u.ends_on else None, "moves_with_you": u.moves_with_you,
            "bill_id": u.bill_id, "note": u.note, "known": u.amount is not None or u.billed in ("landlord", "included") or u.moves_with_you}


def home_out(session: Session, home: Home, today: date) -> dict[str, Any]:
    charges = charges_of(session, home.id)
    with_rent = [c for c in charges if c.pays_to == "home"]
    elsewhere = [c for c in charges if c.pays_to != "home"]
    costs = costs_of(session, home.id)
    utilities = session.exec(select(Utility).where(Utility.home_id == home.id, Utility.active == True)).all()  # noqa: E712
    docs = session.exec(select(Document).where(Document.attach_to == f"home:{home.id}")).all()
    bill = _bill(session, home.bill_id)
    next_due = None
    if bill is not None and bill.active:
        upcoming = bill_dates(bill, today, today + timedelta(days=400))
        next_due = upcoming[0].isoformat() if upcoming else None
    to_landlord = sum(c.amount for c in with_rent if c.amount is not None)
    own_bills = sum(c.amount for c in elsewhere if c.amount is not None) + sum(u.amount for u in utilities if u.amount is not None and u.billed in ("direct", "submetered"))
    state = "upcoming" if home.starts_on and home.starts_on > today else "ended" if home.ends_on and home.ends_on <= today else "current"
    missing = [c.name for c in charges if c.amount is None] + [SERVICE_WORDS.get(u.service, "Service") for u in utilities if u.amount is None and u.billed in ("direct", "submetered") and not u.moves_with_you]
    costs_known = [c for c in costs if c.amount is not None and not c.paid]
    return {
        "id": home.id, "name": home.name, "kind": home.kind, "payee": home.payee, "state": state,
        "starts_on": home.starts_on.isoformat() if home.starts_on else None, "ends_on": home.ends_on.isoformat() if home.ends_on else None,
        "first_due": (home.first_due or home.starts_on).isoformat() if (home.first_due or home.starts_on) else None, "next_due": next_due,
        "due_day": home.due_day, "grace_days": home.grace_days, "late_fee": home.late_fee, "autopay": home.autopay, "notice_days": home.notice_days,
        "notice": notice(home, today), "deposit": home.deposit, "deposit_back_on": home.deposit_back_on.isoformat() if home.deposit_back_on else None,
        "pay_from_account_id": home.pay_from_account_id, "note": home.note,
        "charges": [_charge_out(c) for c in charges],
        "to_landlord_monthly": _money(to_landlord), "own_bills_monthly": _money(own_bills), "monthly_total": _money(to_landlord + own_bills), "missing": missing,
        "costs": [_cost_out(c) for c in costs], "costs_total": _money(sum(c.amount for c in costs_known)), "costs_missing": [c.name for c in costs if c.amount is None],
        "costs_paid_before": _money(sum(c.amount for c in costs if c.paid and c.amount)),
        "costs_undated": [c.name for c in costs_known if c.due_on is None],
        "costs_counted": _money(sum(c.amount for c in costs_known if c.due_on is not None)),
        "refundable_in_costs": _money(sum(c.amount for c in costs_known if c.refundable) + sum(float(p.get("amount") or 0) for c in costs_known for p in json.loads(c.parts or "[]") if p.get("refundable"))),
        "utilities": [utility_out(u) for u in utilities],
        "documents": [{"id": d.id, "title": d.title, "kind": d.kind, "pages": d.pages, "uploaded_at": d.uploaded_at.isoformat()} for d in docs],
        "bill_id": home.bill_id, "source": f"home:{home.id}",
    }


def overview(session: Session, today: date) -> dict[str, Any]:
    homes = session.exec(select(Home).where(Home.active == True).order_by(Home.starts_on)).all()  # noqa: E712
    out = [home_out(session, h, today) for h in homes]
    current = next((h for h in out if h["state"] == "current"), None)
    upcoming = next((h for h in out if h["state"] == "upcoming"), None)
    loose = session.exec(select(Utility).where(Utility.home_id == None, Utility.active == True)).all()  # noqa: E711, E712
    rent_bills = [b for b in session.exec(select(Bill).where(Bill.active == True, Bill.home_id == None)).all() if (b.category or "").lower() in ("rent", "mortgage")]  # noqa: E711, E712
    move = None
    if current and upcoming:
        move = {"from": current["name"], "to": upcoming["name"], "on": upcoming["starts_on"], "monthly_change": _money(upcoming["monthly_total"] - current["monthly_total"]),
                "costs_total": upcoming["costs_total"], "costs_missing": upcoming["costs_missing"], "old_ends_on": current["ends_on"], "old_notice": current["notice"]}
    return {"as_of": today.isoformat(), "homes": out, "current_id": current["id"] if current else None, "upcoming_id": upcoming["id"] if upcoming else None, "move": move,
            "utilities_unassigned": [utility_out(u) for u in loose], "rent_bills_to_adopt": [{"id": b.id, "name": b.name, "amount": b.amount, "due_day": b.due_day} for b in rent_bills],
            "not_advice": "Your housing as you entered it, counted in every figure. Not financial or legal advice; your lease is what holds."}


def move_check(session: Session, today: date, home_id: int) -> dict[str, Any]:
    """Whether the paychecks before a move cover the costs of getting in: the lowest balance from today to a month
    after the move, at the usual pace, with every cost and the new home's bills already counted."""
    from .scenario import run_scenario

    home = session.get(Home, home_id)
    if home is None or not home.starts_on:
        raise KeyError(home_id)
    run = run_scenario(session, today, {"name": home.name, "move_date": home.starts_on.isoformat(), "one_offs": [], "recurring": [], "stop_bills": []})
    until = (home.starts_on + timedelta(days=31)).isoformat()
    path = [p for p in run["baseline"]["path"] if p["date"] <= until]
    low = min(path, key=lambda p: p["at_pace"]) if path else None
    out = home_out(session, home, today)
    holds = low is None or low["at_pace"] >= 0
    return {
        "home": out, "move_on": home.starts_on.isoformat(), "low": low, "holds": holds,
        "status": "good" if holds else "crit",
        "word": (f"Covered: at your usual spending the lowest balance before a month after the move is ${low['at_pace']:,.2f} on {low['date']}." if holds and low else
                 f"Short: at your usual spending the balance falls ${-low['at_pace']:,.2f} below the cushion on {low['date']}." if low else "No paycheck on file to check against."),
        "path": path, "caveats": run["caveats"],
    }
