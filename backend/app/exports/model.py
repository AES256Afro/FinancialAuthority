"""The report model: one structure every report format renders. Each section has a title, a plain lead sentence,
key figures, bars (each with its part's role, for colour), a table and notes, all taken from the engine's results.
The registers, for the data formats, are the rows as stored, minus anything secret."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Advance, BalanceSnapshot, Bill, Debt, Goal, InstallmentPlan, Payment, Paystub, Transaction
from ..store import get_setting

NOT_ADVICE = "Computed by the engine from your registers and the ledger. Not financial advice."

SECTIONS = [
    ("summary", "The summary"),
    ("this_check", "This check"),
    ("bills_by_check", "Bills by check"),
    ("cover", "Cover"),
    ("saving", "Saving"),
    ("debts", "Debts"),
    ("borrowing", "Borrowing"),
    ("accounts", "Accounts"),
    ("transactions", "Transactions, last 30 days"),
    ("notes", "Your notes"),
]
DEFAULT_SECTIONS = ["summary", "this_check", "bills_by_check", "cover", "saving", "debts", "borrowing"]


def usd(v: float | None, cents: bool = False) -> str:
    if v is None:
        return "-"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.{2 if cents else 0}f}"


def day(iso: str | date | None) -> str:
    if not iso:
        return "-"
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return f"{d:%a %b} {d.day}"


def _section(key: str, title: str, lead: str = "", kpis: list | None = None, bars: list | None = None, table: dict | None = None, notes: list | None = None) -> dict:
    return {"key": key, "title": title, "lead": lead, "kpis": kpis or [], "bars": bars or [], "table": table, "notes": notes or []}


def build_report(session: Session, today: date, sections: list[str] | None = None, notes: str = "") -> dict[str, Any]:
    from ..engine.cover import account_cover
    from ..engine.paychecks import paycheck_views
    from ..engine.saving import saving_plan
    from ..engine.snapshot import finance_snapshot

    wanted = [s for s in (sections or DEFAULT_SECTIONS) if s in dict(SECTIONS)]
    snap = finance_snapshot(session, today)
    sv = saving_plan(session, today, snap=snap)
    cov = account_cover(session, today, saving=sv) if "cover" in wanted else None
    pv = paycheck_views(session, today, snap=snap, saving=sv) if {"this_check", "bills_by_check"} & set(wanted) else None
    brand = ((get_setting(session, "appearance", {}) or {}).get("brand") or {}).get("name") or "FinancialAuthority"
    out: list[dict] = []
    for key in wanted:
        title = dict(SECTIONS)[key]
        if key == "summary":
            out.append(_section(key, title, snap["why"]["safe_to_spend"], kpis=[
                {"label": "Safe to spend", "value": usd(snap["safe_to_spend"]["amount"]), "note": f"through {day(snap['safe_to_spend']['through'])}"},
                {"label": "Next paycheck", "value": usd(snap["next_paycheck"]["amount"]), "note": day(snap["next_paycheck"]["date"])},
                {"label": "Due before payday", "value": usd(snap["due_before_payday"]["total"]), "note": f"{len(snap['due_before_payday']['items'])} payments"},
                {"label": "Saved", "value": usd(sv["balance"]), "note": f"rung {sv['next_rung']['n']} next" if sv["next_rung"] else "every rung reached"},
            ]))
        elif key == "this_check" and pv and pv["windows"]:
            w = pv["windows"][0]
            parts = [p for p in w["parts"] if p["amount"] > 0.5]
            rows = [[i["name"], day(i["date"]), p["label"], usd(i["amount"], True)] for p in parts for i in p["items"][:8] if p["key"] not in ("yours", "saving")]
            out.append(_section(key, f"The {day(w['start'])} check", snap["why"].get(f"window:{w['start']}", ""),
                                kpis=[{"label": "Brings in", "value": usd(w["inflow"]), "note": f"{usd(w['paycheck'])} check" + (f" + {usd(w['carried_in'])} carried" if w["carried_in"] > 0 else "")},
                                      {"label": "Yours", "value": usd(next(p["amount"] for p in w["parts"] if p["key"] == "yours")), "note": "after the parts below"}],
                                bars=[{"label": p["label"], "value": p["amount"], "role": p["key"]} for p in parts],
                                table={"columns": ["Line", "Date", "Part", "Amount"], "align": ["l", "l", "l", "r"], "rows": rows}))
        elif key == "bills_by_check" and pv:
            rows = []
            for w in pv["windows"]:
                for p in w["parts"]:
                    if p["key"] in ("yours", "saving", "aside"):
                        continue
                    for i in p["items"]:
                        rows.append([day(w["start"]), i["name"], day(i["date"]), usd(i["amount"], True)])
            out.append(_section(key, title, "Each bill in the check that pays it, for the next four checks.", table={"columns": ["Check", "Bill", "Due", "Amount"], "align": ["l", "l", "l", "r"], "rows": rows[:80]}))
        elif key == "cover" and cov:
            rows = []
            for a in cov["accounts"]:
                if a["role"] == "spend":
                    w1, w2 = a["windows"]
                    rows.append([a["name"], usd(w1["needs"]), usd(w2["needs"]), usd(a["balance"]), {"good": "on track", "warn": "watch", "crit": "act"}[a["status"]]])
                elif a["role"] == "card":
                    rows.append([a["name"], usd(sum(p["amount"] for p in a.get("payments", []) if p["date"] < cov["paydays"][0])), usd(sum(p["amount"] for p in a.get("payments", []) if cov["paydays"][0] <= p["date"] < cov["paydays"][1])), usd(a["balance"]) + " owed", {"good": "on track", "warn": "watch", "crit": "act"}[a["status"]]])
            sheet = [f"{day(r['date'])}: {r['action']}, {usd(r['amount'], True)}, so that {r['so_that']}." for r in cov["sheet"]]
            out.append(_section(key, title, f"What each account needs before {day(cov['paydays'][0])} and before {day(cov['paydays'][1])}, against what it holds.",
                                table={"columns": ["Account", f"Needs before {day(cov['paydays'][0])}", f"Needs before {day(cov['paydays'][1])}", "Has", "Status"], "align": ["l", "r", "r", "r", "l"], "rows": rows},
                                notes=["The payday sheet:"] + sheet if sheet else []))
        elif key == "saving":
            rows = [[f"{r['n']}. {r['name']}", usd(r["target"]), "reached" if r["reached"] else usd(r["short"]) + " to go", day(r["eta"]) if r["eta"] else "not within six years"] for r in sv["rungs"]]
            out.append(_section(key, title, f"{usd(sv['balance'])} saved. Each check keeps {sv['share_used']}% of what it leaves" + ("" if sv["chosen"] else " (the suggested share)") + ".",
                                kpis=[{"label": "Saved", "value": usd(sv["balance"]), "note": ""}, {"label": "A month of costs", "value": usd(sv["month"]), "note": "bills and everyday spending"}],
                                bars=[{"label": f"Rung {r['n']}", "value": r["target"], "role": "saving"} for r in sv["rungs"][:3]],
                                table={"columns": ["Rung", "Target", "Left", "Reached on"], "align": ["l", "r", "r", "l"], "rows": rows}))
        elif key == "debts":
            debts = snap["debts"]
            rows = [[d["name"], d["kind"], usd(d["balance"]), f"{d['apr']:.2f}%" if d["apr"] else "-", usd(d["minimum"]) if d["minimum"] else "-"] for d in sorted(debts, key=lambda d: -(d["balance"] or 0))]
            total = sum(d["balance"] or 0 for d in debts)
            out.append(_section(key, title, f"{usd(total)} owed across {len(debts)} debts.", kpis=[{"label": "Owed", "value": usd(total), "note": f"{len(debts)} debts"}],
                                bars=[{"label": d["name"], "value": d["balance"], "role": "card" if d["kind"] == "card" else "later"} for d in sorted(debts, key=lambda d: -(d["balance"] or 0))[:6]],
                                table={"columns": ["Debt", "Kind", "Owed", "Rate", "Minimum"], "align": ["l", "l", "r", "r", "r"], "rows": rows}))
        elif key == "borrowing":
            b = snap.get("borrowing") or {}
            plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
            rows = [[f"{p.provider} · {p.merchant}" if p.merchant and p.merchant != p.provider else p.provider, usd(p.installment, True), str(p.remaining), day(p.next_date)] for p in plans]
            out.append(_section(key, title, f"Advances and pay-later cost {usd(b.get('fees_month', 0))} a month at the recent pace; {b.get('outstanding', 0)} advances are out.",
                                kpis=[{"label": "Lost a month", "value": usd(b.get("fees_month", 0)), "note": "fees and interest"}, {"label": "Advances out", "value": str(b.get("outstanding", 0)), "note": usd(b.get("outstanding_total", 0))}],
                                table={"columns": ["Plan", "Each", "Left", "Next"], "align": ["l", "r", "r", "l"], "rows": rows} if rows else None))
        elif key == "accounts":
            rows = [[a["name"], a["institution"] or "-", a["kind"], usd(a["balance"], True) if a["balance"] is not None else "-"] for a in snap["accounts"]]
            out.append(_section(key, title, f"{len(rows)} accounts.", table={"columns": ["Account", "Institution", "Kind", "Balance"], "align": ["l", "l", "l", "r"], "rows": rows}))
        elif key == "transactions":
            since = today - timedelta(days=30)
            names = {a.id: a.name for a in session.exec(select(Account)).all()}
            txns = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.is_ignored == False).order_by(Transaction.date.desc()).limit(200)).all()  # noqa: E712
            rows = [[day(t.date), t.merchant or t.description, t.category, names.get(t.account_id, "?"), usd(-t.amount, True)] for t in txns]
            out.append(_section(key, title, "Money in is positive, money out negative.", table={"columns": ["Date", "Merchant", "Category", "Account", "Amount"], "align": ["l", "l", "l", "l", "r"], "rows": rows}))
        elif key == "notes" and notes.strip():
            out.append(_section(key, title, notes.strip()[:4000]))
    return {"title": f"{brand}: the paycheck plan", "brand": brand, "as_of": today.isoformat(), "as_of_words": day(today), "sections": out,
            "sources": "finance.snapshot, paycheck.views, accounts.cover and saving.plan, as of " + today.isoformat(), "not_advice": NOT_ADVICE}


def registers(session: Session) -> dict[str, list[dict]]:
    """Every register as rows, for CSV, JSON and the workbook. Nothing secret: no keys, no tokens, no mail."""
    accounts = session.exec(select(Account)).all()
    names = {a.id: a.name for a in accounts}

    def rows(model, fields):
        return [{f: (v.isoformat() if hasattr(v, "isoformat") else v) for f in fields for v in [getattr(r, f, None)]} for r in session.exec(select(model)).all()]

    txns = [{**r, "account": names.get(r["account_id"], "")} for r in rows(Transaction, ["id", "date", "account_id", "merchant", "description", "amount", "category", "note", "is_transfer", "is_card_payment", "is_income", "is_ignored", "source"])]
    return {
        "accounts": rows(Account, ["id", "name", "institution", "kind", "subtype", "balance", "balance_at", "credit_limit", "connector", "active"]),
        "transactions": txns,
        "bills": rows(Bill, ["id", "name", "kind", "amount", "amount_mode", "cadence", "due_day", "autopay", "pay_from_account_id", "paid_from", "movable", "category", "active"]),
        "payments": rows(Payment, ["id", "bill_id", "due_date", "amount", "status", "paid_date", "transaction_id"]),
        "debts": rows(Debt, ["id", "name", "kind", "balance", "apr", "minimum", "statement_day", "due_day", "account_id", "pay_in_full", "credit_limit", "active"]),
        "plans": rows(InstallmentPlan, ["id", "provider", "merchant", "total", "installment", "count", "remaining", "next_date", "cadence_days", "active"]),
        "advances": rows(Advance, ["id", "provider", "amount", "fee", "taken_on", "repay_date", "repaid"]),
        "paystubs": rows(Paystub, ["id", "employer", "pay_date", "period_start", "period_end", "gross", "net", "status"]),
        "goals": rows(Goal, ["id", "name", "kind", "target", "target_date", "pace", "priority", "funded", "active"]),
        "balances": rows(BalanceSnapshot, ["id", "account_id", "date", "balance"]),
    }
