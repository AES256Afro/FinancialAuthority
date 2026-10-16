"""paycheck.views: one paycheck split into the parts every picture draws, in a fixed order: housing, bills, the
card payment, pay-later and advances, what is set aside for the window after, saving, and what is yours. The
next four checks as projected, the last six as the ledger saw them, the next six months, and the bills between
two paydays as beads. The pictures (the ribbon, the strips, the waterfall, the stream, the rings and the beads)
only draw these figures; nothing is computed on the screen."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Bill, Income, Transaction
from .projection import is_spendable

PARTS = [
    ("housing", "Housing"),
    ("bills", "Bills"),
    ("card", "Card payment"),
    ("later", "Pay-later and advances"),
    ("aside", "Set aside"),
    ("saving", "Saving"),
    ("yours", "Yours"),
]
HOUSING_WORDS = ("rent", "mortgage", "housing", "hoa", "home")
OBLIGATION_CATEGORIES = {"Rent", "Loan Payment", "Credit Card Payment", "Fees & Charges", "Insurance", "Bills & Utilities", "Utilities", "Phone", "Internet"}
NOT_ADVICE = "Computed by the engine from your registers and the ledger. Not financial advice."


def _money(v: float) -> float:
    return round(float(v), 2)


def part_of(e: dict) -> str:
    kind = e.get("kind")
    if kind == "card_payment" or (e.get("planned") and e.get("kind") == "card_payment"):
        return "card"
    if kind in ("installment", "advance"):
        return "later"
    category = (e.get("category") or "").lower()
    if any(w in category for w in HOUSING_WORDS):
        return "housing"
    return "bills"


def _item(e: dict) -> dict:
    return {"name": e["name"], "amount": _money(e["amount"]), "date": e["date"] if isinstance(e["date"], str) else e["date"].isoformat(), "source": e.get("source", ""), "kind": e.get("kind", "")}


def _empty_parts() -> dict:
    return {k: {"key": k, "label": label, "amount": 0.0, "items": []} for k, label in PARTS}


def _finish(parts: dict) -> list[dict]:
    return [{**p, "amount": _money(p["amount"]), "items": sorted(p["items"], key=lambda i: -i["amount"])[:12]} for p in parts.values()]


def projected(snap: dict, saving: dict | None) -> list[dict]:
    keeps = {p["date"]: p["keep"] for p in (saving or {}).get("per_check", [])}
    out = []
    for w in snap["windows"]:
        parts = _empty_parts()
        for e in w["bills"]["items"]:
            if e.get("via_card"):
                continue  # paid with the card payment, which is already a part
            k = part_of(e)
            parts[k]["amount"] += e["amount"]
            parts[k]["items"].append(_item(e))
        for e in w["advances"]["items"]:
            parts["later"]["amount"] += e["amount"]
            parts["later"]["items"].append(_item(e))
        parts["aside"]["amount"] = w["set_aside"]["total"]
        parts["aside"]["items"] = [_item(e) for e in w["set_aside"]["items"]]
        safe = w["safe"]
        keep = min(float(keeps.get(w["start"], 0.0)), max(0.0, safe))
        parts["saving"]["amount"] = keep
        parts["yours"]["amount"] = max(0.0, safe - keep)
        riding = [_item(e) for e in w["bills"]["items"] if e.get("via_card")]
        if riding:
            parts["card"]["riding"] = riding
        out.append({
            "start": w["start"], "end": w["end"], "paycheck": _money(w["paycheck"]), "carried_in": _money(w["carried_in"]), "inflow": _money(w["paycheck"] + w["carried_in"]),
            "short": _money(max(0.0, -safe)), "safe": _money(safe), "parts": _finish(parts), "actual": False,
        })
    return out


def _past_paydays(session: Session, today: date, count: int) -> list[date]:
    from .cadence import next_paydays

    income = session.exec(select(Income).where(Income.active == True).order_by(Income.next_date)).first()  # noqa: E712
    if income is None:
        return []
    from .cadence import _add_months

    cadence = income.cadence or "biweekly"
    start = income.next_date
    # Walk forward from a real payday far enough back that `count` paydays before today are on the list.
    if cadence == "monthly":
        seq = [_add_months(start, -k) for k in range(count + 4)]
    elif cadence == "semimonthly":
        seq = next_paydays(start - timedelta(days=16 * (count + 3)), cadence, 2 * count + 12)[1:]  # the first is only where the walk began
    else:
        step = 7 if cadence == "weekly" else 14
        seq = next_paydays(start - timedelta(days=step * (count + 3)), cadence, count + 8)
    past = sorted(d for d in set(seq) if d <= today)
    return past[-count:]


def history(session: Session, today: date, count: int = 6) -> list[dict]:
    """The last checks as the ledger saw them: what came in, and where it went, from the spending accounts."""
    paydays = _past_paydays(session, today, count + 1)
    if len(paydays) < 2:
        return []
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    spend_ids = [a.id for a in accounts.values() if is_spendable(a) and a.kind != "card"]
    if not spend_ids:
        return []
    housing_names = {b.name.lower() for b in session.exec(select(Bill)).all() if any(w in (b.category or "").lower() for w in HOUSING_WORDS)}
    bill_names = {b.name.lower() for b in session.exec(select(Bill)).all()}
    first, last = paydays[0], today
    rows = session.exec(select(Transaction).where(Transaction.account_id.in_(spend_ids), Transaction.date >= first, Transaction.date <= last, Transaction.is_ignored == False)).all()  # noqa: E712
    out = []
    bounds = [*paydays, today + timedelta(days=1)]
    for k in range(len(paydays)):
        start, end = bounds[k], bounds[k + 1] - timedelta(days=1)
        if start > today:
            break
        inside = [t for t in rows if start <= t.date <= end]
        income = -sum(t.amount for t in inside if t.is_income and t.amount < 0)
        borrowed = -sum(t.amount for t in inside if t.amount < 0 and not t.is_income and t.category == "Borrowing")
        parts = _empty_parts()
        everyday = 0.0
        for t in inside:
            if t.amount <= 0:
                continue
            name = (t.merchant or "").lower()
            item = {"name": t.merchant or t.description or "?", "amount": _money(t.amount), "date": t.date.isoformat(), "source": f"txn:{t.id}", "kind": t.category}
            if t.is_card_payment:
                key = "card"
            elif t.category == "Borrowing":
                key = "later"
            elif t.category in ("Rent", "Mortgage", "Housing") or name in housing_names:
                key = "housing"
            elif t.category in OBLIGATION_CATEGORIES or name in bill_names:
                key = "bills"
            elif t.is_transfer:
                key = "saving"
            else:
                everyday += t.amount
                continue
            parts[key]["amount"] += t.amount
            parts[key]["items"].append(item)
        inflow = income + borrowed
        spent = sum(p["amount"] for p in parts.values())
        left = inflow - spent
        parts["yours"]["amount"] = max(0.0, left)
        parts["yours"]["everyday"] = _money(everyday)
        parts["yours"]["kept"] = _money(left - everyday)
        out.append({
            "start": start.isoformat(), "end": end.isoformat(), "paycheck": _money(income), "borrowed": _money(borrowed), "carried_in": 0.0, "inflow": _money(inflow),
            "short": _money(max(0.0, -left)), "safe": _money(left), "parts": _finish(parts), "actual": True, "partial": end >= today,
        })
    return [h for h in out if h["paycheck"] > 0 or h["borrowed"] > 0][-count:]


def months_ahead(session: Session, today: date, saving: dict | None, count: int = 6) -> list[dict]:
    """Month by month, what the checks bring and where it is due to go: the stream's bands."""
    from .projection import assemble
    from .scenario import load_inputs

    inp = load_inputs(session, today, horizon_days=31 * count + 5)
    events = assemble(bills=inp["bills"], plans=inp["plans"], advances=inp["advances"], today=today, accounts=inp["accounts"], paydays=inp["paydays"], card_mode=inp["card_mode"], debts=inp["debts"],
                      planned=inp["planned"], extra=inp["extra"], card_pace_by_account=inp["pace"]["per_day_by_account"], horizon_days=inp["horizon_days"])["math"]
    pace = float(inp["pace"]["per_day_cash"])
    share = float((saving or {}).get("share_used", 0) if (saving or {}).get("chosen") else 0)
    out = []
    y, m = today.year, today.month
    for _ in range(count):
        first = date(y, m, 1)
        last = date(y, m, monthrange(y, m)[1])
        start = max(first, today)
        income = sum(inp["net_for"](d) for d in inp["paydays"] if start <= d <= last) if inp["has_income"] else 0.0
        parts = _empty_parts()
        for e in events:
            if start <= e["date"] <= last:
                k = part_of(e)
                parts[k]["amount"] += e["amount"]
                parts[k]["items"].append(_item(e))
        obligations = sum(p["amount"] for p in parts.values())
        everyday = pace * ((last - start).days + 1)
        free = income - obligations - everyday
        keep = max(0.0, free) * share / 100.0
        parts["saving"]["amount"] = keep
        parts["yours"]["amount"] = max(0.0, income - obligations - keep)
        parts["yours"]["everyday"] = _money(everyday)
        out.append({"month": first.isoformat()[:7], "from": start.isoformat(), "to": last.isoformat(), "income": _money(income), "obligations": _money(obligations),
                    "everyday": _money(everyday), "short": _money(max(0.0, obligations - income)), "parts": _finish(parts), "partial": start > first})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def beads(snap: dict) -> list[dict]:
    """Between each pair of paydays, every bill on its own day, with whether its due day can move."""
    out = []
    first = {"start": snap["as_of"], "end": (date.fromisoformat(snap["windows"][0]["start"]) - timedelta(days=1)).isoformat(), "items": snap["due_before_payday"]["items"], "before": True}
    # On a payday the stretch before it is empty, and starts the same day as the first window: leave it out.
    spans = ([first] if first["start"] < snap["windows"][0]["start"] else []) + [{"start": w["start"], "end": w["end"], "items": w["bills"]["items"] + w["advances"]["items"], "before": False} for w in snap["windows"][:3]]
    for span in spans:
        items = []
        for e in span["items"]:
            items.append({**_item(e), "part": part_of(e), "via_card": bool(e.get("via_card")), "bill_id": e.get("bill_id"), "movable": bool(e.get("movable")), "due_day": e.get("due_day")})
        out.append({"start": span["start"], "end": span["end"], "before": span["before"], "items": sorted(items, key=lambda i: i["date"])})
    return out


def _totals(checks: list[dict]) -> list[dict]:
    """What the pictures draw beside each check: all its parts together, the parts it had to cover (everything but
    what was yours and what was saved), and each part's share of what came in."""
    for c in checks:
        parts = c.get("parts") or []
        c["parts_total"] = _money(sum(p["amount"] for p in parts))
        c["cover_total"] = _money(sum(p["amount"] for p in parts if p.get("key") not in ("yours", "saving")))
        base = c.get("inflow") or c.get("income") or 0.0
        for p in parts:
            p["share_of_check"] = round(p["amount"] / base, 4) if base else None
    return checks


def paycheck_views(session: Session, today: date, *, snap: dict | None = None, saving: dict | None = None) -> dict:
    from .saving import saving_plan
    from .snapshot import finance_snapshot

    if snap is None:
        snap = finance_snapshot(session, today)
    if saving is None:
        saving = saving_plan(session, today, snap=snap)
    return {
        "as_of": today.isoformat(), "parts": [{"key": k, "label": label} for k, label in PARTS],
        "windows": _totals(projected(snap, saving)), "history": _totals(history(session, today)), "months": _totals(months_ahead(session, today, saving)), "beads": beads(snap),
        "share": saving.get("share_used") if saving.get("chosen") else None,
        "basis": "Each check split by what it pays: the projected checks from the plan, the past checks from the ledger's spending accounts, the months from the registers and the usual pace.",
        "not_advice": NOT_ADVICE,
    }
