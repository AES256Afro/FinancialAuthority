"""Reports the engine computes from the ledger and the registers: spending by month, category and merchant;
net worth; the debt mountain. Models explain these; they never compute their own."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Debt, InstallmentPlan, Transaction

NOT_ADVICE = "Computed by the engine from the ledger and the registers. Not financial advice."
ASSET_KINDS = {"checking", "savings", "cash", "investment"}
DEBT_KINDS = {"card", "loan", "bnpl", "advance"}


def _money(v: float) -> float:
    return round(float(v), 2)


def month_start(d: date) -> date:
    return d.replace(day=1)


def months_back(today: date, n: int) -> list[date]:
    """The first day of each of the last n months, oldest first, the current month last."""
    out = []
    cursor = month_start(today)
    for _ in range(n):
        out.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return list(reversed(out))


def spending_summary(session: Session, today: date, *, months: int = 6, by: str = "category", redaction: str = "full") -> dict:
    months = max(1, min(int(months), 24))
    starts = months_back(today, months)
    start = starts[0]
    rows = session.exec(select(Transaction).where(Transaction.date >= start, Transaction.date <= today)).all()
    per_month: dict[str, dict] = {s.strftime("%Y-%m"): {"month": s.strftime("%Y-%m"), "spent": 0.0, "income": 0.0, "count": 0, "partial": s == starts[-1]} for s in starts}
    groups: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0, "this_month": 0.0, "months": defaultdict(float)})
    merchants: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0})
    this_month = starts[-1].strftime("%Y-%m")
    for t in rows:
        key = t.date.strftime("%Y-%m")
        bucket = per_month.get(key)
        if bucket is None:
            continue
        if t.is_income:
            bucket["income"] += -t.amount
            continue
        if not t.is_spending:
            continue
        bucket["spent"] += t.amount
        bucket["count"] += 1
        name = t.category if by == "category" else (t.merchant or t.raw_name or "(unknown)")
        g = groups[name]
        g["total"] += t.amount
        g["count"] += 1
        g["months"][key] += t.amount
        if key == this_month:
            g["this_month"] += t.amount
        m = merchants[t.merchant or t.raw_name or "(unknown)"]
        m["total"] += t.amount
        m["count"] += 1
    full_months = [k for k, v in per_month.items() if not v["partial"]]
    spent_total = sum(v["spent"] for v in per_month.values())
    income_total = sum(v["income"] for v in per_month.values())
    out_groups = []
    for name, g in groups.items():
        average = (sum(g["months"][k] for k in full_months) / len(full_months)) if full_months else g["total"]
        out_groups.append({"name": "(redacted)" if redaction == "aggregates" and by == "merchant" else name, "total": _money(g["total"]), "count": g["count"], "this_month": _money(g["this_month"]), "average": _money(average), "share": round(g["total"] / spent_total, 4) if spent_total else 0.0})
    out_groups.sort(key=lambda g: -g["total"])
    top_merchants = sorted(({"name": "(redacted)" if redaction == "aggregates" else n, "total": _money(m["total"]), "count": m["count"]} for n, m in merchants.items()), key=lambda m: -m["total"])[:10]
    return {
        "as_of": today.isoformat(),
        "from": start.isoformat(),
        "by": by,
        "months": [{**v, "spent": _money(v["spent"]), "income": _money(v["income"])} for v in per_month.values()],
        "groups": out_groups,
        "top_merchants": top_merchants,
        "totals": {"spent": _money(spent_total), "income": _money(income_total), "savings_rate": round((income_total - spent_total) / income_total, 4) if income_total > 0 else None, "months": months, "full_months": len(full_months)},
        "not_advice": NOT_ADVICE,
    }


def net_worth(session: Session, today: date) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    linked = {d.account_id for d in debts if d.account_id}

    def account_out(a: Account) -> dict:
        return {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None, "connector": a.connector, "source": f"account:{a.id}"}

    assets = [account_out(a) for a in accounts if a.kind in ASSET_KINDS]
    by_id = {a.id: a for a in accounts}

    def debt_out(d: Debt) -> dict:
        limit = d.credit_limit or (by_id[d.account_id].credit_limit if d.account_id in by_id else None)
        return {
            "id": d.id, "name": d.name, "kind": d.kind, "balance": d.balance, "apr": d.apr, "minimum": d.minimum, "source": f"debt:{d.id}", "account_id": d.account_id,
            "statement_day": d.statement_day, "due_day": d.due_day, "pay_in_full": d.pay_in_full, "credit_limit": limit,
            "utilisation": round(d.balance / limit, 4) if limit else None, "interest_month_if_carried": _money(d.balance * d.apr / 100.0 / 12.0) if d.apr else 0.0,
        }

    owed = [debt_out(d) for d in debts]
    owed += [{**account_out(a), "apr": None, "minimum": None, "credit_limit": a.credit_limit, "utilisation": round((a.balance or 0.0) / a.credit_limit, 4) if a.credit_limit else None} for a in accounts if a.kind in DEBT_KINDS and a.id not in linked and a.balance]
    owed += [{"id": p.id, "name": (f"{p.provider} · {p.merchant}" if p.merchant and p.merchant.lower() != p.provider.lower() else p.provider) + (f" · {p.remaining} of {p.count} left" if not p.balance else ""), "kind": "bnpl",
              "balance": _money(p.balance) if p.balance else _money(p.installment * p.remaining), "apr": p.apr if p.apr is not None else 0.0, "minimum": p.installment, "source": f"plan:{p.id}", "finance_charge": p.finance_charge, "interest_paid": p.interest_paid,
              "ends": (p.next_date + timedelta(days=p.cadence_days * max(0, p.remaining - 1))).isoformat(), "balance_at": p.balance_at.isoformat() if p.balance_at else None, "loan_id": p.loan_id} for p in plans if p.remaining > 0 or (p.balance or 0) > 0]
    owed += [{"id": a.id, "name": f"{a.provider} advance", "kind": "advance", "balance": _money(a.amount + a.fee), "apr": None, "minimum": None, "source": f"advance:{a.id}"} for a in advances]
    assets_total = sum((a["balance"] or 0.0) for a in assets)
    debts_total = sum((d["balance"] or 0.0) for d in owed)
    stale = [a["name"] for a in assets if a["balance_at"] and date.fromisoformat(a["balance_at"][:10]) < today - timedelta(days=3)]
    return {
        "as_of": today.isoformat(),
        "assets": {"total": _money(assets_total), "items": assets},
        "debts": {"total": _money(debts_total), "items": owed},
        "net": _money(assets_total - debts_total),
        "stale": stale,
        "not_advice": NOT_ADVICE,
    }


def debt_mountain(session: Session, today: date) -> dict:
    """Every debt with its share of the whole, the minimums due each month, and the two classic orders."""
    nw = net_worth(session, today)
    items = [d for d in nw["debts"]["items"]]
    total = nw["debts"]["total"]
    for d in items:
        d["share"] = round((d["balance"] or 0.0) / total, 4) if total else 0.0
    minimums = sum((d["minimum"] or 0.0) for d in items)
    rated = [d for d in items if d.get("apr") is not None and d["kind"] in ("card", "loan", "medical")]
    avalanche = [d["name"] for d in sorted(rated, key=lambda d: (-(d["apr"] or 0.0), d["balance"] or 0.0))]
    snowball = [d["name"] for d in sorted(rated, key=lambda d: (d["balance"] or 0.0))]
    interest_month = sum(((d["balance"] or 0.0) * (d["apr"] or 0.0) / 100.0 / 12.0) for d in rated)
    return {
        "as_of": today.isoformat(),
        "total": total,
        "items": sorted(items, key=lambda d: -(d["balance"] or 0.0)),
        "minimums_month": _money(minimums),
        "interest_month": _money(interest_month),
        "order": {"avalanche": avalanche, "snowball": snowball},
        "not_advice": NOT_ADVICE,
    }
