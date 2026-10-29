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
from .recurring import bill_keys, merchant_key

KINDS = ("rent", "mortgage", "other")
CHARGE_KINDS = ("rent", "pet", "fee", "utility", "insurance", "principal_interest", "escrow", "pmi", "hoa", "parking", "other")
SERVICES = ("electric", "water", "gas", "internet", "trash", "sewer", "phone", "other")
BILLED = ("direct", "landlord", "submetered", "included")
SERVICE_WORDS = {"electric": "Electricity", "water": "Water", "gas": "Gas", "internet": "Internet", "trash": "Trash", "sewer": "Sewer", "phone": "Phone", "other": "Service"}
BILLED_WORDS = {"direct": "billed to you by the provider", "landlord": "paid with the rent", "submetered": "measured at your unit and billed by a billing company", "included": "included in the rent"}


def _money(v: float) -> float:
    return round(float(v), 2)


def _what(u: Utility) -> str:
    """What a utility is, in a word: the service, or for anything else, who provides it."""
    return SERVICE_WORDS.get(u.service, "Service") if u.service != "other" else (u.provider or u.billing_company or "Service")


def charges_of(session: Session, home_id: int) -> list[HomeCharge]:
    return list(session.exec(select(HomeCharge).where(HomeCharge.home_id == home_id, HomeCharge.active == True).order_by(HomeCharge.id)).all())  # noqa: E712


def costs_of(session: Session, home_id: int) -> list[HomeCost]:
    return list(session.exec(select(HomeCost).where(HomeCost.home_id == home_id, HomeCost.active == True).order_by(HomeCost.due_on, HomeCost.id)).all())  # noqa: E712


def _bill(session: Session, bill_id: int | None) -> Bill | None:
    return session.get(Bill, bill_id) if bill_id else None


def _end_due(home: Home) -> date | None:
    """The last day a regular payment can come due: the day before moving out."""
    return home.ends_on - timedelta(days=1) if home.ends_on else None


def _first_due(home: Home) -> date | None:
    """The first regular payment: the home's due day on or after the day it is first due (as entered, else the
    move-in), which is the first date its rent bill comes due. Moving in on the 17th with rent due on the 1st, it
    is the 1st of the next month, not the 17th."""
    from .projection import next_due

    start = home.first_due or home.starts_on
    return next_due(max(1, min(31, home.due_day or 1)), start) if start else None


def _today(today: date | None) -> date:
    """The owner's day (the routes pass theirs), never the server's: a bill added on a US evening is today's."""
    if today is not None:
        return today
    from ..clock import local_today

    return local_today()


def _remember(b: Bill, *names: str) -> None:
    """Keep the names a bill carried before Housing renamed it, so its payments, which the bank still names the old
    way, keep matching (see recurring.bill_keys)."""
    if b.id is None:
        return
    try:
        kept = [str(n) for n in json.loads(b.match_names or "[]") if n]
    except (ValueError, TypeError):
        kept = []
    for n in names:
        n = (n or "").strip()
        if n and n not in kept and n != b.name and n != (b.pays_to or ""):
            kept.append(n)
    b.match_names = json.dumps(kept[-8:])


def _near(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 0.05 * max(abs(float(b)), 1.0)


def rent_bill_for(session: Session, home: Home, amount: float) -> Bill | None:
    """A rent or mortgage bill already on the list that is this home's rent: tied to no home and no utility, on the
    home's due day, within 5% of what the home's rent comes to."""
    for b in session.exec(select(Bill).where(Bill.active == True, Bill.home_id == None, Bill.utility_id == None)).all():  # noqa: E711, E712
        if (b.category or "").lower() in ("rent", "mortgage") and int(b.due_day or 0) == int(home.due_day or 1) and _near(b.amount, amount):
            return b
    return None


def same_bill_for(session: Session, *, names: list[str], amount: float, due_day: int, category: str) -> Bill | None:
    """A bill already on the list that a home's own charge (renter's insurance, parking paid elsewhere) duplicates:
    tied to no home and no utility, on the same due day, within 5% of the amount, and of the same kind or under the
    same name."""
    keys = [k for k in (merchant_key(n) for n in names) if k]
    for b in session.exec(select(Bill).where(Bill.active == True, Bill.home_id == None, Bill.utility_id == None)).all():  # noqa: E711, E712
        if int(b.due_day or 0) != int(due_day) or not _near(b.amount, amount):
            continue
        same_kind = bool(category) and (b.category or "").lower() == category.lower() and category.lower() not in ("housing", "")
        named = any(k == bk or k.startswith(bk) or bk.startswith(k) for k in keys for bk in bill_keys(b))
        if same_kind or named:
            return b
    return None


def sync_bills(session: Session, home: Home, today: date | None = None) -> None:
    """Make the home's bills match its charges, costs and dates. Bills it no longer needs are turned off. A rent bill
    already on the list for the same rent (same due day, within 5%) becomes the home's rent bill instead of a second
    one, and so does a bill an "other" charge duplicates. The home's utilities follow its dates."""
    charges = charges_of(session, home.id)
    with_rent = [c for c in charges if c.pays_to == "home"]
    known = [c for c in with_rent if c.amount is not None]
    word = {"rent": "Rent", "mortgage": "Mortgage"}.get(home.kind, "Housing")
    bill = _bill(session, home.bill_id)
    if known and home.active:
        if bill is None and not (home.starts_on and home.starts_on > _today(today)):
            # The home lived in now, with the rent already on the list: the same rent, counted once. A home moved
            # into later never takes it: the rent on the list is the one paid until then.
            bill = rent_bill_for(session, home, sum(c.amount for c in known))
        bill = bill or Bill(name="", amount=0.0, created_on=_today(today))
        old_name, old_pays = bill.name, bill.pays_to
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
        _remember(bill, old_name, old_pays)
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
            category = "Insurance" if c.kind == "insurance" else "Housing"
            due_day = max(1, min(31, c.due_day or home.due_day or 1))
            if b is None and not (home.starts_on and home.starts_on > _today(today)):
                b = same_bill_for(session, names=[c.name, c.payee], amount=c.amount, due_day=due_day, category=category)  # already on the list: counted once
            b = b or Bill(name="", amount=0.0, created_on=_today(today))
            old_name, old_pays = b.name, b.pays_to
            b.name, b.amount = f"{c.name}"[:80], _money(c.amount)
            b.amount_mode = "estimate" if c.estimate else "fixed"
            b.cadence, b.due_day = "monthly", due_day
            b.starts_on, b.ends_on = c.starts_on or home.first_due or home.starts_on, c.ends_on or _end_due(home)
            b.category = category
            b.pays_to = c.payee or b.pays_to
            _remember(b, old_name, old_pays)
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
            b = b or Bill(name="", amount=0.0, created_on=_today(today))
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
    # The home's utilities start and stop with it: a change of its dates, or its removal, reaches their bills.
    for u in session.exec(select(Utility).where(Utility.home_id == home.id)).all():
        sync_utility(session, u, today)


def utility_dates(u: Utility, home: Home | None) -> tuple[date | None, date | None]:
    """When a utility's bill runs: its own dates, else its home's (from moving in to the day before moving out). One
    that moves with you is not stopped by the move."""
    starts = u.starts_on or (home.starts_on if home is not None else None)
    ends = u.ends_on or (_end_due(home) if home is not None and not u.moves_with_you else None)
    return starts, ends


def sync_utility(session: Session, u: Utility, today: date | None = None) -> None:
    """A utility billed on its own (directly, or by a billing company) with a known amount makes its own bill, from
    its home's move-in to its move-out unless it has dates of its own. A utility of a removed home has no bill."""
    b = _bill(session, u.bill_id)
    home = session.get(Home, u.home_id) if u.home_id else None
    if u.active and (home is None or home.active) and u.billed in ("direct", "submetered") and u.amount is not None:
        b = b or Bill(name="", amount=0.0, created_on=_today(today))
        old_name, old_pays = b.name, b.pays_to
        who = u.billing_company if u.billed == "submetered" and u.billing_company else u.provider
        b.name = (f"{SERVICE_WORDS[u.service]} · {who}" if u.service in SERVICE_WORDS and u.service != "other" else who or "Service").strip(" ·")[:80]
        b.amount, b.amount_mode = _money(u.amount), "estimate" if u.estimate else "fixed"
        b.cadence, b.due_day = "monthly", max(1, min(31, u.due_day or 1))
        b.starts_on, b.ends_on = utility_dates(u, home)
        b.category, b.kind, b.utility_id, b.active = b.category or "Utilities", "bill", u.id, True
        b.pays_to = who or b.pays_to
        _remember(b, old_name, old_pays)  # the bank's own name for its payments (a bill linked here) keeps matching
        session.add(b)
        session.flush()
        u.bill_id = b.id
    elif b is not None:
        b.active = False
        session.add(b)
    session.add(u)
    session.commit()


def adopt_into(session: Session, bill: Bill, home: Home, today: date | None = None) -> Home:
    """A rent bill already on the list is an existing home's rent: it becomes the home's rent bill (keeping its own
    name for matching), and the bill the home made for itself is turned off, so the rent is counted once."""
    own = _bill(session, home.bill_id)
    if own is not None and own.id != bill.id:
        own.active = False
        session.add(own)
    if not any(c.pays_to == "home" and c.amount is not None for c in charges_of(session, home.id)):
        session.add(HomeCharge(home_id=home.id, name="Rent" if home.kind == "rent" else "Payment", kind="rent" if home.kind == "rent" else "principal_interest", amount=_money(bill.amount)))
    home.bill_id = bill.id
    home.payee = home.payee or bill.pays_to or bill.name
    bill.home_id = home.id
    session.add_all([home, bill])
    session.commit()
    sync_bills(session, home, today)
    return home


def adopt_bill(session: Session, bill: Bill, *, name: str, kind: str = "rent", today: date | None = None) -> Home:
    """A rent bill already on the list becomes a home: its amount the rent, its due day the home's."""
    home = Home(name=name.strip()[:80] or bill.name, kind=kind if kind in KINDS else "rent", payee=bill.pays_to or bill.name, due_day=bill.due_day, autopay=bill.autopay, grace_days=bill.grace_days,
                late_fee=bill.late_fee, pay_from_account_id=bill.pay_from_account_id, bill_id=bill.id)
    session.add(home)
    session.flush()
    session.add(HomeCharge(home_id=home.id, name="Rent" if home.kind == "rent" else "Payment", kind="rent" if home.kind == "rent" else "principal_interest", amount=_money(bill.amount)))
    bill.home_id = home.id
    session.add(bill)
    session.commit()
    sync_bills(session, home, today)
    return home


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}, {d.year}"


def start_move(session: Session, home: Home, was_start: date | None, today: date) -> list[str]:
    """Moving is a home with a start date, and the home lived in until then ends on that date: its rent and its
    utilities stop there (M37). A home that already has an end keeps it, unless that end was this home's old move-in
    date (the move changed date, so the end follows it). Returns what was done, in words, for the page to say."""
    said: list[str] = []
    if not home.active or not home.starts_on or home.starts_on <= today:
        return said
    for other in session.exec(select(Home).where(Home.active == True, Home.id != home.id)).all():  # noqa: E712
        if other.starts_on is not None and other.starts_on > today:
            continue  # another home not moved into yet: not the one being left
        if other.ends_on is None or (was_start is not None and other.ends_on == was_start and other.ends_on != home.starts_on):
            other.ends_on = home.starts_on
            session.add(other)
            session.commit()
            sync_bills(session, other, today)
            said.append(f"{other.name} now ends on {_day(home.starts_on)}, the day you move into {home.name}: its rent and utilities stop there. Change its end date on Housing if you keep it.")
    return said


def call_off_move(session: Session, home: Home, today: date) -> list[str]:
    """A home moved into later is removed: the home it would have ended goes on, as before the move was added,
    unless notice to leave it was given (it ends either way)."""
    said: list[str] = []
    if not home.starts_on or home.starts_on <= today:
        return said
    for other in session.exec(select(Home).where(Home.active == True, Home.ends_on == home.starts_on)).all():  # noqa: E712
        if other.notice_given_on:
            continue
        other.ends_on = None
        session.add(other)
        session.commit()
        sync_bills(session, other, today)
        said.append(f"{other.name} no longer ends on {_day(home.starts_on)}: with {home.name} removed, its rent and utilities go on.")
    return said


def notice(home: Home, today: date) -> dict[str, Any] | None:
    """The written notice the lease needs before moving out: by when, and whether it is given. With no move-out
    date, the lease term's end is the day to leave by (the same day lease_word names), so the form to mark the
    notice given is there whenever a give-by day can be named."""
    end = home.ends_on or (home.lease_ends_on if home.lease_ends_on and home.lease_ends_on >= today else None)
    if not end or not home.notice_days:
        return None
    # A day early: leases count the notice day differently, and a day early holds under either count.
    by = end - timedelta(days=home.notice_days + 1)
    if home.notice_given_on:
        return {"by": by.isoformat(), "given_on": home.notice_given_on.isoformat(), "status": "good", "word": f"Notice given on {home.notice_given_on:%a %b} {home.notice_given_on.day}"}
    left = (by - today).days
    if left < 0:
        return {"by": by.isoformat(), "given_on": None, "status": "crit", "word": f"Notice was due by {by:%a %b} {by.day}. Give it in writing now, and ask the office to confirm the move-out date."}
    status = "crit" if left <= 7 else "warn"
    return {"by": by.isoformat(), "given_on": None, "status": status, "days_left": left, "word": f"Give written notice by {by:%a %b} {by.day} ({left} day{'s' if left != 1 else ''} left)"}


def lease_word(home: Home, today: date) -> str | None:
    """The lease term's end when no move-out is planned: what it means, and the notice to leave then."""
    if not home.lease_ends_on or home.ends_on:
        return None
    end = home.lease_ends_on
    said = f"Your lease runs to {end:%a %b} {end.day}, {end.year}" + (" and has ended: it goes on month to month unless you signed a new one." if end < today else ".")
    if home.notice_days and end >= today:
        by = end - timedelta(days=home.notice_days + 1)
        said += f" To leave then, give written notice by {by:%a %b} {by.day}, {by.year}; to stay, ask the office about renewing."
    return said


def _charge_out(c: HomeCharge) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "kind": c.kind, "amount": c.amount, "estimate": c.estimate, "pays_to": c.pays_to, "payee": c.payee, "due_day": c.due_day,
            "starts_on": c.starts_on.isoformat() if c.starts_on else None, "ends_on": c.ends_on.isoformat() if c.ends_on else None, "note": c.note, "known": c.amount is not None, "bill_id": c.bill_id}


def _comes_back(c: HomeCost) -> float:
    """What a cost gives back: its refundable parts when it is itemised (the page draws it that way), else the whole
    cost when it is marked refundable; never both, which would count a deposit twice."""
    parts = json.loads(c.parts or "[]")
    if parts:
        return sum(float(p.get("amount") or 0) for p in parts if p.get("refundable"))
    return float(c.amount or 0.0) if c.refundable else 0.0


def _cost_out(c: HomeCost) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "amount": c.amount, "due_on": c.due_on.isoformat() if c.due_on else None, "refundable": c.refundable, "parts": json.loads(c.parts or "[]"),
            "paid": c.paid, "known": c.amount is not None, "note": c.note, "bill_id": c.bill_id, "counted": bool(c.bill_id) and c.amount is not None and not c.paid}


def utility_out(u: Utility) -> dict[str, Any]:
    return {"id": u.id, "home_id": u.home_id, "service": u.service, "what": _what(u), "provider": u.provider, "billed": u.billed, "billed_word": BILLED_WORDS.get(u.billed, u.billed),
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
    missing = [c.name for c in charges if c.amount is None] + [_what(u) for u in utilities if u.amount is None and u.billed in ("direct", "submetered") and not u.moves_with_you]
    costs_known = [c for c in costs if c.amount is not None and not c.paid]
    first_due = _first_due(home)
    return {
        "id": home.id, "name": home.name, "kind": home.kind, "payee": home.payee, "state": state,
        "starts_on": home.starts_on.isoformat() if home.starts_on else None, "ends_on": home.ends_on.isoformat() if home.ends_on else None,
        "first_due": first_due.isoformat() if first_due else None, "next_due": next_due,
        "first_due_entered": home.first_due.isoformat() if home.first_due else None,  # as typed: None when the first payment is the move-in day
        "lease_ends_on": home.lease_ends_on.isoformat() if home.lease_ends_on else None, "lease_word": lease_word(home, today),
        "notice_given_on": home.notice_given_on.isoformat() if home.notice_given_on else None,
        "due_day": home.due_day, "grace_days": home.grace_days, "late_fee": home.late_fee, "autopay": home.autopay, "notice_days": home.notice_days,
        "notice": notice(home, today), "deposit": home.deposit, "deposit_back_on": home.deposit_back_on.isoformat() if home.deposit_back_on else None,
        "pay_from_account_id": home.pay_from_account_id, "note": home.note,
        "charges": [_charge_out(c) for c in charges],
        "to_landlord_monthly": _money(to_landlord), "own_bills_monthly": _money(own_bills), "monthly_total": _money(to_landlord + own_bills), "missing": missing,
        "costs": [_cost_out(c) for c in costs], "costs_total": _money(sum(c.amount for c in costs_known)), "costs_missing": [c.name for c in costs if c.amount is None],
        "costs_paid_before": _money(sum(c.amount for c in costs if c.paid and c.amount)),
        "costs_undated": [c.name for c in costs_known if c.due_on is None],
        "costs_counted": _money(sum(c.amount for c in costs_known if c.due_on is not None)),
        "refundable_in_costs": _money(sum(_comes_back(c) for c in costs_known)),
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
    warnings: list[str] = []
    for b in rent_bills:
        # A rent bill on the list beside a home with its own rent may be the same rent counted twice; beside only a
        # home moved into later, it is the rent paid now, and it does not stop at the move until it is a home's.
        living = [h for h in out if h["bill_id"] and h["state"] == "current"]
        if living:
            h = next((h for h in living if h["due_day"] == b.due_day), living[0])
            warnings.append(f"{b.name} (${b.amount:,.2f} on day {b.due_day} of each month) is on your bills list, and {h['name']} has its own rent. If it is the same rent it is counted twice: make {b.name} the rent of {h['name']}. If it is a different rent, leave both.")
        elif upcoming and not current:
            warnings.append(f"{b.name} (${b.amount:,.2f} on day {b.due_day} of each month) is not tied to a home, so it does not stop when you move to {upcoming['name']}. Make it the home you live in now, and it ends on the move date.")
    move = None
    if current and upcoming:
        dated = sorted((c for c in upcoming["costs"] if c["due_on"] and not c["paid"]), key=lambda c: c["due_on"])
        check = {"name": dated[0]["name"], "on": dated[0]["due_on"], "amount": dated[0]["amount"], "source": f"home:{upcoming['id']}"} if dated else None
        move = {"from": current["name"], "to": upcoming["name"], "on": upcoming["starts_on"], "monthly_change": _money(upcoming["monthly_total"] - current["monthly_total"]),
                "costs_total": upcoming["costs_total"], "costs_missing": upcoming["costs_missing"], "old_ends_on": current["ends_on"], "old_notice": current["notice"],
                "first_cost": check}
    return {"as_of": today.isoformat(), "homes": out, "current_id": current["id"] if current else None, "upcoming_id": upcoming["id"] if upcoming else None, "move": move,
            "utilities_unassigned": [utility_out(u) for u in loose], "rent_bills_to_adopt": [{"id": b.id, "name": b.name, "amount": b.amount, "due_day": b.due_day} for b in rent_bills], "warnings": warnings,
            "not_advice": "Your housing as you entered it, counted in every figure. Not financial or legal advice; your lease is what holds."}


def move_check(session: Session, today: date, home_id: int) -> dict[str, Any]:
    """Whether the paychecks before a move cover the costs of getting in: the lowest left to spend (after the safety
    cushion, the bills kept and the usual spending) from today to a month after the move, with every cost and the new
    home's bills already counted. The plan is run far enough to reach a month after the move; if it still does not,
    the verdict is "unknown", never "Covered"."""
    from .scenario import HORIZON, run_scenario

    home = session.get(Home, home_id)
    if home is None or not home.starts_on:
        raise KeyError(home_id)
    until_day = home.starts_on + timedelta(days=31)
    run = run_scenario(session, today, {"name": home.name, "move_date": home.starts_on.isoformat(), "one_offs": [], "recurring": [], "stop_bills": []},
                       horizon_days=max(HORIZON, (until_day - today).days + 1))
    until = until_day.isoformat()
    whole = run["baseline"]["path"]
    path = [p for p in whole if p["date"] <= until]
    out = home_out(session, home, today)
    if not whole or whole[-1]["date"] < until:
        seen = date.fromisoformat(whole[-1]["date"]) if whole else today
        return {"home": out, "move_on": home.starts_on.isoformat(), "low": None, "holds": None, "status": "unknown",
                "word": f"The plan sees through {seen:%a %b} {seen.day}, {seen.year}; the move is after it, so it cannot say yet whether the move is covered.", "path": path, "caveats": run["caveats"]}
    low = min(path, key=lambda p: p["at_pace"]) if path else None
    holds = low is None or low["at_pace"] >= 0
    on = f"{date.fromisoformat(low['date']):%a %b} {date.fromisoformat(low['date']).day}" if low else ""
    return {
        "home": out, "move_on": home.starts_on.isoformat(), "low": low, "holds": holds,
        "status": "good" if holds else "crit",
        "word": (f"Covered: at your usual spending, the lowest left to spend before a month after the move is ${low['at_pace']:,.2f}, on {on}, after keeping the safety cushion and the money for bills." if holds and low else
                 f"Short: at your usual spending, you would be ${-low['at_pace']:,.2f} short on {on}, after keeping the safety cushion and the money for bills." if low else "No paycheck on file to check against."),
        "path": path, "caveats": run["caveats"],
    }
