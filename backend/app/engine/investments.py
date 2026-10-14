"""Investments and retirement accounts: every account of that kind with its vendor, what it is (a 401(k), an
IRA, a brokerage account, an HSA), its balance and how fresh it is, its holdings, the allocation, the balance
over time, and what the paychecks put in. The engine's arithmetic; the vendors' and the owner's figures."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, BalanceSnapshot, Holding, Paystub

NOT_ADVICE = "Computed by the engine from the accounts, holdings and paystubs on record. Not financial advice."
RETIREMENT = {"401k", "403b", "457b", "ira", "roth_ira", "roth", "sep_ira", "simple_ira", "pension", "tsp", "retirement", "profit_sharing", "keogh", "sarsep"}
SUBTYPE_WORDS = {
    "401k": "401(k)", "403b": "403(b)", "457b": "457(b)", "ira": "IRA", "roth_ira": "Roth IRA", "roth": "Roth IRA", "sep_ira": "SEP IRA", "simple_ira": "SIMPLE IRA",
    "pension": "pension", "tsp": "TSP", "brokerage": "brokerage", "hsa": "HSA", "529": "529 plan", "crypto": "crypto", "cash_management": "cash management",
    "trust": "trust", "ugma": "UGMA", "utma": "UTMA", "stock_plan": "stock plan", "retirement": "retirement", "other": "investment", "": "investment",
}
CONTRIBUTION_WORDS = ("401k", "401(k)", "403b", "403(b)", "457", "retirement", "roth", "ira", "tsp", "pension", "hsa", "espp", "stock purchase")


def _money(v: float) -> float:
    return round(float(v), 2)


def subtype_word(subtype: str) -> str:
    key = (subtype or "").lower().replace(" ", "_").replace("(", "").replace(")", "")
    return SUBTYPE_WORDS.get(key, subtype or "investment")


def is_retirement(subtype: str) -> bool:
    key = (subtype or "").lower().replace(" ", "_").replace("(", "").replace(")", "")
    return key in RETIREMENT


def contributions_from_paystubs(session: Session, today: date) -> dict:
    """What the last paystubs took out for retirement and savings plans, by line name."""
    stubs = session.exec(select(Paystub).where(Paystub.pay_date <= today).order_by(Paystub.pay_date.desc()).limit(6)).all()
    by_line: dict[str, list[float]] = defaultdict(list)
    for stub in stubs:
        try:
            lines = json.loads(stub.deductions or "[]")
        except json.JSONDecodeError:
            lines = []
        for line in lines:
            name = str(line.get("name", ""))
            amount = line.get("amount")
            if amount is None or not any(w in name.lower() for w in CONTRIBUTION_WORDS):
                continue
            by_line[name].append(abs(float(amount)))
    items = [{"name": name, "last": _money(values[0]), "average": _money(sum(values) / len(values)), "paystubs": len(values)} for name, values in by_line.items()]
    items.sort(key=lambda i: -i["last"])
    return {"per_paycheck": _money(sum(i["last"] for i in items)), "items": items, "source": "paystubs"}


def investments_overview(session: Session, today: date) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "investment").order_by(Account.institution, Account.name)).all()  # noqa: E712
    ids = [a.id for a in accounts]
    holdings = session.exec(select(Holding).where(Holding.account_id.in_(ids))).all() if ids else []  # type: ignore[attr-defined]
    by_account: dict[int, list[Holding]] = defaultdict(list)
    for h in holdings:
        by_account[h.account_id].append(h)
    since = today - timedelta(days=400)
    snapshots = session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(ids), BalanceSnapshot.date >= since).order_by(BalanceSnapshot.date)).all() if ids else []  # type: ignore[attr-defined]
    history_by_account: dict[int, list[BalanceSnapshot]] = defaultdict(list)
    for s in snapshots:
        history_by_account[s.account_id].append(s)

    items = []
    allocation: dict[str, float] = defaultdict(float)
    for a in accounts:
        rows = sorted(by_account.get(a.id, []), key=lambda h: -(h.value or 0.0))
        total_holdings = sum((h.value or 0.0) for h in rows)
        for h in rows:
            allocation[h.kind or "other"] += h.value or 0.0
        history = [{"date": s.date.isoformat(), "balance": s.balance} for s in history_by_account.get(a.id, [])]
        stale_days = (today - a.balance_at.date()).days if a.balance_at else None
        items.append({
            "id": a.id, "name": a.name, "institution": a.institution, "subtype": a.subtype, "what": subtype_word(a.subtype), "retirement": is_retirement(a.subtype),
            "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None, "stale_days": stale_days, "connector": a.connector,
            "holdings": [{"id": h.id, "name": h.name, "ticker": h.ticker, "kind": h.kind, "quantity": h.quantity, "price": h.price, "value": h.value, "cost_basis": h.cost_basis, "as_of": h.as_of.isoformat() if h.as_of else None, "share": round((h.value or 0.0) / total_holdings, 4) if total_holdings else 0.0} for h in rows],
            "holdings_total": _money(total_holdings),
            "history": history,
            "source": f"account:{a.id}",
        })
    total = sum((a.balance or 0.0) for a in accounts)
    retirement = sum((a.balance or 0.0) for a in accounts if is_retirement(a.subtype))
    alloc_total = sum(allocation.values())
    by_day: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for s in snapshots:
        by_day[s.date.isoformat()] += s.balance
        counts[s.date.isoformat()] += 1
    complete_days = [d for d in sorted(by_day) if counts[d] == len(ids)]
    return {
        "as_of": today.isoformat(),
        "total": _money(total),
        "retirement": _money(retirement),
        "taxable": _money(total - retirement),
        "items": items,
        "allocation": sorted(({"kind": k, "value": _money(v), "share": round(v / alloc_total, 4) if alloc_total else 0.0} for k, v in allocation.items()), key=lambda x: -x["value"]),
        "history": [{"date": d, "balance": _money(by_day[d])} for d in complete_days],
        "contributions": contributions_from_paystubs(session, today),
        "stale": [a["name"] for a in items if a["stale_days"] is not None and a["stale_days"] > 35],
        "not_advice": NOT_ADVICE,
    }
