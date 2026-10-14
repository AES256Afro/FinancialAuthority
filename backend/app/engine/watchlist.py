"""The don't-buy list: what to hold off on and why, from the registers and the ledger. Every item carries
its sources; the models explain, the engine decides what is on the list."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from ..models import Advance, Bill, Debt, InstallmentPlan, RecurringSeries
from .brief import when
from .reports import spending_summary
from .snapshot import finance_snapshot

NOT_ADVICE = "Computed by the engine from the registers and the ledger. Not financial advice."
OVER_USUAL = 1.25  # a category this far over its usual, and at least this many dollars over, is on the list
OVER_USUAL_MIN = 25.0
MANY_PLANS = 3
PRICE_RISE_MIN = 1.0  # a recurring charge up by at least this, and by this share of its usual, is worth a look
PRICE_RISE_SHARE = 0.05
ENTERTAINMENT = {"entertainment", "streaming", "music"}


def watchlist(session: Session, today: date) -> dict:
    snap = finance_snapshot(session, today)
    items: list[dict] = []

    advances = session.exec(select(Advance).where(Advance.repaid == False)).all()  # noqa: E712
    if advances:
        total = round(sum(a.amount + a.fee for a in advances), 2)
        items.append({
            "kind": "advances", "severity": "critical",
            "text": f"No new advances: {len(advances)} already repay {total:,.2f} out of the next paycheck." if len(advances) > 1 else f"No new advances: one already repays {total:,.2f} out of the next paycheck.",
            "amount": total, "sources": [f"advance:{a.id}" for a in advances], "link": "/debts",
        })

    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True, InstallmentPlan.remaining > 0)).all()  # noqa: E712
    if len(plans) >= MANY_PLANS:
        left = round(sum(p.installment * p.remaining for p in plans), 2)
        items.append({
            "kind": "plans", "severity": "warning",
            "text": f"No new pay-later plans: {len(plans)} are running, {left:,.2f} still to pay.",
            "amount": left, "sources": [f"plan:{p.id}" for p in plans], "link": "/debts",
        })

    bad_weeks = [w for w in snap["calendar"]["weeks"][:6] if w["low"] is not None and w["low"] < 0]
    if bad_weeks:
        w = bad_weeks[0]
        items.append({
            "kind": "buffer", "severity": "critical" if w["low"] < -snap["buffer"] else "warning",
            "text": f"Nothing optional until after {when(w['low_date'])}: at the usual pace the week of {when(w['start'])} dips {-w['low']:,.2f} under the buffer.",
            "amount": round(-w["low"], 2), "sources": ["snapshot:projection"], "link": "/?view=month",
        })

    for s in session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).all():  # noqa: E712
        if s.kind in ("income", "ignored", "advance") or s.typical_amount <= 0:
            continue
        rise = s.latest_amount - s.typical_amount
        if rise >= max(PRICE_RISE_MIN, s.typical_amount * PRICE_RISE_SHARE):
            items.append({
                "kind": "price", "severity": "warning",
                "text": f"{s.merchant} went up: {s.latest_amount:,.2f} against the usual {s.typical_amount:,.2f}. Worth a look before it renews.",
                "amount": round(rise, 2), "sources": [f"series:{s.id}"], "link": "/bills",
            })

    subs = [b for b in session.exec(select(Bill).where(Bill.active == True, Bill.kind == "subscription")).all()]  # noqa: E712
    by_theme: dict[str, list[Bill]] = {}
    for b in subs:
        theme = (b.category or "").lower()
        theme = "entertainment" if theme in ENTERTAINMENT or any(w in theme for w in ENTERTAINMENT) else theme
        by_theme.setdefault(theme, []).append(b)
    for theme, group in by_theme.items():
        if theme and len(group) >= 2:
            names = ", ".join(sorted(b.name for b in group))
            items.append({
                "kind": "duplicates", "severity": "info",
                "text": f"{len(group)} {theme} subscriptions ({names}): {sum(b.amount for b in group):,.2f} a month together. One could go.",
                "amount": round(sum(b.amount for b in group), 2), "sources": [f"bill:{b.id}" for b in group], "link": "/bills",
            })

    for d in session.exec(select(Debt).where(Debt.active == True, Debt.pay_in_full == True)).all():  # noqa: E712
        if d.balance > 0:
            items.append({
                "kind": "card", "severity": "warning",
                "text": f"{d.name} is a pay-in-full card carrying {d.balance:,.2f}; nothing new on it until it is clear.",
                "amount": d.balance, "sources": [f"debt:{d.id}"], "link": "/debts",
            })

    report = spending_summary(session, today, months=4, by="category")
    for g in report["groups"]:
        if g["average"] > 0 and g["this_month"] >= g["average"] * OVER_USUAL and g["this_month"] - g["average"] >= OVER_USUAL_MIN:
            items.append({
                "kind": "category", "severity": "info",
                "text": f"{g['name']} is {g['this_month'] - g['average']:,.2f} over its usual this month ({g['this_month']:,.2f} against {g['average']:,.2f}).",
                "amount": round(g["this_month"] - g["average"], 2), "sources": ["report:spending"], "link": "/reports",
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: (order[i["severity"]], -(i["amount"] or 0)))
    return {"as_of": today.isoformat(), "items": items, "safe_to_spend": snap["safe_to_spend"], "not_advice": NOT_ADVICE}
