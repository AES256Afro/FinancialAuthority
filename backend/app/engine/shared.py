"""The shared accounts, as a second person with the "shared" role sees them (M33): the accounts marked shared and
their own, each balance, the bills paid from them in the next month, what was spent from them by category, and
the latest transactions. The owner sees the same page for any person, to know exactly what they see."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Bill, Person, Transaction
from .billdates import bill_dates

CASH_KINDS = ("checking", "savings", "cash")


def _money(v: float) -> float:
    return round(float(v), 2)


def visible_accounts(session: Session, person_id: int | None) -> list[Account]:
    rows = session.exec(select(Account).where(Account.active == True).order_by(Account.name)).all()  # noqa: E712
    return [a for a in rows if a.shared or (person_id is not None and a.person_id == person_id)]


def shared_view(session: Session, today: date, person_id: int | None = None) -> dict[str, Any]:
    person = session.get(Person, person_id) if person_id else None
    accounts = visible_accounts(session, person_id)
    ids = {a.id for a in accounts}
    names = {a.id: a.name for a in accounts}
    out_accounts = [{"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "balance_at": a.balance_at.isoformat() if a.balance_at else None,
                     "shared": a.shared, "whose": "shared" if a.shared else "theirs", "source": f"account:{a.id}"} for a in accounts]
    cash = _money(sum(a.balance or 0.0 for a in accounts if a.kind in CASH_KINDS))
    cards = _money(sum(abs(a.balance or 0.0) for a in accounts if a.kind == "card"))

    end = today + timedelta(days=31)
    bills = []
    for b in session.exec(select(Bill).where(Bill.active == True)).all():  # noqa: E712
        if b.pay_from_account_id not in ids:
            continue
        for d in bill_dates(b, today, end):
            bills.append({"bill_id": b.id, "name": b.name, "amount": _money(b.amount), "date": d.isoformat(), "autopay": b.autopay, "account": names.get(b.pay_from_account_id, ""),
                          "kind": b.kind, "source": f"bill:{b.id}"})
    bills.sort(key=lambda x: (x["date"], x["name"]))

    since = today - timedelta(days=45)
    txs = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.date <= today, Transaction.is_ignored == False).order_by(Transaction.date.desc(), Transaction.id.desc())).all() if ids else []  # noqa: E712
    txs = [t for t in txs if t.account_id in ids]
    recent = [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant or t.description, "amount": _money(t.amount), "category": t.category, "account": names.get(t.account_id, ""),
               "pending": t.pending, "source": f"transaction:{t.id}"} for t in txs[:100]]
    month_ago = today - timedelta(days=30)
    by_cat: dict[str, float] = {}
    for t in txs:
        if t.date < month_ago or t.amount <= 0 or t.is_transfer or t.is_card_payment or t.is_income:
            continue
        by_cat[t.category or "Uncategorized"] = by_cat.get(t.category or "Uncategorized", 0.0) + t.amount
    spending = [{"category": c, "spent": _money(v)} for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]
    return {
        "as_of": today.isoformat(), "person": {"id": person.id, "name": person.display_name or person.username, "role": person.role} if person else None,
        "accounts": out_accounts, "cash": cash, "cards_owed": cards,
        "bills": bills, "bills_total": _money(sum(b["amount"] for b in bills)), "bills_until": end.isoformat(),
        "spending": spending[:10], "spent_30d": _money(sum(s["spent"] for s in spending)),
        "recent": recent,
        "note": None if accounts else "No account is marked shared yet. The owner marks accounts shared on Settings › People or on Accounts.",
        "not_advice": "The shared accounts as the bank last reported them, and the bills paid from them. Not financial advice.",
    }
