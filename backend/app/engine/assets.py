"""Net worth beyond the accounts: a home, a car, or anything else worth something, at the value the owner gives
it. A car (or anything set to lose value) loses a share a year from the day its value was last set, so its line
falls between updates; each asset asks to be updated when its value is older than the owner chose.

Also here: what the holdings have gained or lost against what was paid for them, where the vendor gives the cost."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Holding, OtherAsset, utcnow

KINDS = {"home": "A home", "car": "A car", "other": "Something else"}
DEFAULT_RATES = {"car": 0.15}  # a share of its value a car loses a year, unless the owner sets another


def _money(v: float) -> float:
    return round(float(v), 2)


def value_now(a: OtherAsset, today: date) -> float:
    """The value today: the owner's figure, less what it loses a year since it was set."""
    rate = a.yearly_change if a.yearly_change is not None else 0.0
    years = max(0.0, (today - a.value_on).days / 365.25)
    return max(0.0, a.value * ((1.0 + rate) ** years))


def assets_overview(session: Session, today: date) -> dict[str, Any]:
    rows = session.exec(select(OtherAsset).where(OtherAsset.active == True).order_by(OtherAsset.name)).all()  # noqa: E712
    items = []
    for a in rows:
        now = value_now(a, today)
        due = a.value_on + timedelta(days=a.update_every_days)
        items.append({
            "id": a.id, "name": a.name, "kind": a.kind, "what": KINDS.get(a.kind, "Something else"), "value_set": _money(a.value), "value_on": a.value_on.isoformat(),
            "value_now": _money(now), "yearly_change": a.yearly_change, "change_since": _money(now - a.value),
            "owed_against": _money(a.owed or 0.0), "equity": _money(now - (a.owed or 0.0)), "update_due": due.isoformat(), "needs_update": today >= due,
            "note": a.note, "source": f"asset:{a.id}",
        })
    return {"items": items, "total": _money(sum(i["value_now"] for i in items)), "owed_total": _money(sum(i["owed_against"] for i in items)), "needs_update": [i["name"] for i in items if i["needs_update"]]}


def save_asset(session: Session, *, asset_id: int | None, name: str, kind: str, value: float, value_on: date, yearly_change: float | None = None, update_every_days: int = 180, owed: float = 0.0, note: str = "") -> OtherAsset:
    if kind not in KINDS:
        raise ValueError("kind is home, car or other")
    if not name.strip():
        raise ValueError("a name is needed")
    if not (0 <= value <= 100_000_000) or not (0 <= owed <= 100_000_000):
        raise ValueError("a value between 0 and 100 million")
    if yearly_change is not None and not (-0.9 <= yearly_change <= 0.5):
        raise ValueError("the yearly change is a share between -0.9 (loses 90% a year) and 0.5")
    if not (7 <= update_every_days <= 1100):
        raise ValueError("ask to update between every week and every three years")
    row = session.get(OtherAsset, asset_id) if asset_id else None
    row = row or OtherAsset(name=name.strip()[:80], kind=kind, value=0.0, value_on=value_on)
    row.name, row.kind, row.value, row.value_on = name.strip()[:80], kind, round(value, 2), value_on
    row.yearly_change = yearly_change if yearly_change is not None else (-DEFAULT_RATES[kind] if kind in DEFAULT_RATES else None)
    row.update_every_days, row.owed, row.note, row.updated_at = update_every_days, round(owed, 2), note.strip()[:300], utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def holdings_returns(session: Session) -> dict[str, Any]:
    """What each investment account's holdings have gained or lost against their cost, where the cost is known."""
    accounts = {a.id: a for a in session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()}  # noqa: E712
    out = []
    for account_id, account in accounts.items():
        rows = session.exec(select(Holding).where(Holding.account_id == account_id)).all()
        known = [h for h in rows if h.cost_basis is not None and h.value is not None]
        if not known:
            out.append({"account_id": account_id, "name": account.name, "known": False, "value": _money(sum((h.value or 0.0) for h in rows)), "cost": None, "gain": None, "gain_share": None, "holdings_without_cost": len(rows)})
            continue
        value = sum(h.value for h in known)
        cost = sum(h.cost_basis for h in known)
        out.append({"account_id": account_id, "name": account.name, "known": True, "value": _money(value), "cost": _money(cost), "gain": _money(value - cost),
                    "gain_share": round((value - cost) / cost, 4) if cost else None, "holdings_without_cost": len(rows) - len(known)})
    known = [r for r in out if r["known"]]
    return {"accounts": out, "gain_total": _money(sum(r["gain"] for r in known)), "cost_total": _money(sum(r["cost"] for r in known)),
            "note": "Gain is the holdings' value now against what the vendor says was paid. Holdings without a cost are left out of it."}
