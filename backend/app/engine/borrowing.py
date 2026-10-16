"""Borrowing from pay: advance apps (Earnin, Dave, Brigit, MoneyLion and the like) and installment lenders
(Affirm, Klarna, Afterpay, Sezzle, Zip). From the ledger alone: each advance paired with its repayment and the
fee between them, each installment stream and where it stands, what it all costs a month and a year, what the
same money would become, and the steps that end the cycle. Figures with their sources; the page and the
models say it is not a recommendation."""

from __future__ import annotations

import re
import statistics
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Advance, Bill, InstallmentPlan, RecurringSeries, Rule, Transaction
from ..store import get_setting, set_setting
from .cadence import next_paydays
from .projections import NOT_ADVICE

BORROWING_CATEGORY = "Borrowing"
RULE_SOURCE = "engine:borrowing"

ADVANCE_PROVIDERS = {"earnin": "Earnin", "dave": "Dave", "brigit": "Brigit", "moneylion": "MoneyLion", "cleo": "Cleo", "empower": "Empower", "klover": "Klover", "floatme": "FloatMe", "chime spotme": "Chime SpotMe", "albert instant": "Albert"}
INSTALLMENT_PROVIDERS = {"affirm": "Affirm", "klarna": "Klarna", "afterpay": "Afterpay", "sezzle": "Sezzle", "zip pay": "Zip", "quadpay": "Zip", "paypal pay in 4": "PayPal Pay in 4", "uplift": "Uplift"}
INSTALLMENT_DEFAULT_DAYS = {"Affirm": 30, "Klarna": 14, "Afterpay": 14, "Sezzle": 14, "Zip": 14, "PayPal Pay in 4": 14, "Uplift": 30}
LOOKBACK_DAYS = 180
FEE_CEILING = 0.30  # a repayment more than 30% over the advance is not its repayment
MATCH_DAYS = 21  # an advance app takes its money back within a pay period; later repayments belong to later advances
MIN_ADVANCE = 5.0  # a cent or a dollar in is a verification deposit, not an advance
TIP_CEILING = 15.0  # a small payment to an advance app with no advance behind it is a tip or a membership fee
WINDOW_DAYS = 90


def _money(v: float) -> float:
    return round(float(v), 2)


# A provider whose name is also a first name is known only by how its own charges read.
PROVIDER_PATTERNS = {"dave": r"(?<![a-z])dave(?:\.com|,?\s+inc\b|\s+extra\s*cash|\s+app\b|\s+advance|\s+membership|\s+express)"}


_COMPILED: dict[int, list[tuple[str, re.Pattern, str]]] = {}


def _patterns(table: dict[str, str]) -> list[tuple[str, re.Pattern, str]]:
    got = _COMPILED.get(id(table))
    if got is None:
        got = [(key.split()[0], re.compile(PROVIDER_PATTERNS.get(key) or (r"(?<![a-z])" + re.escape(key) + r"(?![a-z])")), name) for key, name in table.items()]
        _COMPILED[id(table)] = got
    return got


def provider_of(text: str, table: dict[str, str]) -> str | None:
    low = (text or "").lower()
    for word, pattern, name in _patterns(table):
        if word in low and pattern.search(low):
            return name
    return None


def _text(t: Transaction) -> str:
    return f"{t.merchant} {t.raw_name} {t.description}"


def pair_advances(rows: list[dict], next_payday: date | None = None, today: date | None = None) -> dict:
    """rows: dicts with id, date, amount (positive = money out), provider. Money in from an advance app is an
    advance; money out to it is a repayment. Each advance takes the first later repayment that covers it with a
    fee of up to thirty percent, or a run of partial repayments that adds up within ten days; an advance with
    no repayment yet is outstanding and due on the next payday. Small payments with no advance behind them are
    tips and membership fees."""
    ins = sorted((r for r in rows if r["amount"] <= -MIN_ADVANCE), key=lambda r: (r["date"], r["id"]))
    today = today or (max((r["date"] for r in rows), default=date.today()))
    outs = sorted((r for r in rows if r["amount"] > 0), key=lambda r: (r["date"], r["id"]))
    used: set = set()
    pairs: list[dict] = []
    for adv in ins:
        amount = -adv["amount"]
        taken = adv["date"]
        whole = None
        for out in outs:
            if out["id"] in used or out["provider"] != adv["provider"] or out["date"] < taken or (out["date"] - taken).days > MATCH_DAYS:
                continue
            if amount * 0.98 <= out["amount"] <= amount * (1.0 + FEE_CEILING):
                whole = [out]
                break
        partial = None
        run: list[dict] = []
        total = 0.0
        for out in outs:
            if out["id"] in used or out["provider"] != adv["provider"] or out["date"] < taken or (out["date"] - taken).days > MATCH_DAYS or out["amount"] >= amount * 0.98:
                continue
            if run and (out["date"] - run[0]["date"]).days > 10:
                run, total = [], 0.0
            run.append(out)
            total += out["amount"]
            if total >= amount * 0.98:
                partial = run
                break
        # Whichever repayment finishes first is this advance's; a later whole payment belongs to a later advance.
        match = whole
        if partial and (whole is None or max(o["date"] for o in partial) < whole[0]["date"]):
            match = partial
        if match:
            for out in match:
                used.add(out["id"])
            repaid_on = max(o["date"] for o in match)
            repaid = sum(o["amount"] for o in match)
            days = max(1, (repaid_on - taken).days)
            pairs.append({"provider": adv["provider"], "taken_on": taken, "amount": _money(amount), "fee": _money(repaid - amount), "repaid": True, "repay_date": repaid_on, "days": days,
                          "annual_rate": _money((repaid - amount) / amount * 365.0 / days) if amount else 0.0, "sources": [f"transaction:{adv['id']}", *[f"transaction:{o['id']}" for o in match]]})
        else:
            stale = (today - taken).days > MATCH_DAYS  # no repayment within a pay period: unresolved history, not money due
            due = next_payday if next_payday and next_payday > taken else taken + timedelta(days=14)
            pairs.append({"provider": adv["provider"], "taken_on": taken, "amount": _money(amount), "fee": 0.0, "repaid": False, "stale": stale, "repay_date": due, "days": None, "annual_rate": None, "sources": [f"transaction:{adv['id']}"]})
    tips = [o for o in outs if o["id"] not in used and o["amount"] <= TIP_CEILING]
    unmatched = [o for o in outs if o["id"] not in used and o["amount"] > TIP_CEILING]
    return {"pairs": pairs, "tips": tips, "unmatched_repayments": unmatched}


def installment_streams(rows: list[dict], today: date) -> list[dict]:
    """rows: dicts with id, date, amount (money out), provider. One stream per provider and installment amount:
    its cadence from the gaps, or the provider's usual one when a single payment is all there is."""
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["amount"] <= 0:
            continue
        by_key.setdefault((r["provider"], round(r["amount"])), []).append(r)
    out: list[dict] = []
    for (provider, _rounded), payments in by_key.items():
        payments.sort(key=lambda r: r["date"])
        dates = [p["date"] for p in payments]
        amount = statistics.median(p["amount"] for p in payments)
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        assumed = not gaps
        if gaps:
            med = statistics.median(gaps)
            cadence_days = 7 if med <= 9 else 14 if med <= 20 else 30
        else:
            cadence_days = INSTALLMENT_DEFAULT_DAYS.get(provider, 14)
        last = dates[-1]
        next_date = last + timedelta(days=cadence_days)
        while next_date < today:
            next_date += timedelta(days=cadence_days)
        active = (today - last).days <= cadence_days * 1.6
        out.append({"provider": provider, "amount": _money(amount), "cadence_days": cadence_days, "cadence_assumed": assumed, "payments_seen": len(payments), "first_date": dates[0], "last_date": last,
                    "next_date": next_date, "active": active, "paid_so_far": _money(sum(p["amount"] for p in payments)), "sources": [f"transaction:{p['id']}" for p in payments]})
    out.sort(key=lambda s: (-s["active"], -s["amount"]))
    return out


def _rows(session: Session, today: date) -> tuple[list[dict], list[dict]]:
    since = today - timedelta(days=LOOKBACK_DAYS)
    advances: list[dict] = []
    installments: list[dict] = []
    cols = select(Transaction.id, Transaction.date, Transaction.amount, Transaction.merchant, Transaction.raw_name, Transaction.description)
    for t in session.exec(cols.where(Transaction.date >= since, Transaction.is_ignored == False)).all():  # noqa: E712
        text = _text(t)
        provider = provider_of(text, ADVANCE_PROVIDERS)
        if provider:
            advances.append({"id": t.id, "date": t.date, "amount": float(t.amount), "provider": provider})
            continue
        provider = provider_of(text, INSTALLMENT_PROVIDERS)
        if provider and t.amount > 0:
            installments.append({"id": t.id, "date": t.date, "amount": float(t.amount), "provider": provider})
    return advances, installments


def _next_payday(session: Session, today: date) -> date | None:
    from ..models import Income

    incomes = session.exec(select(Income).where(Income.active == True)).all()  # noqa: E712
    from .cadence import next_unpaid_payday

    upcoming = sorted(next_unpaid_payday(session, i, today) for i in incomes)
    return upcoming[0] if upcoming else None


def _cadence(session: Session) -> str:
    from ..models import Income

    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    return (income.cadence if income else "biweekly") or "biweekly"


def previous_payday(next_payday: date, cadence: str, before: date) -> date | None:
    """The last payday strictly before `before`, walked back from the next one by cadence."""
    from .cadence import previous_paydays

    walk = [next_payday, *previous_paydays(next_payday, cadence, 8)]
    earlier = [d for d in walk if d < before]
    return max(earlier) if earlier else None


def ensure_borrowing_rules(session: Session) -> int:
    """Every advance app and installment lender gets a ledger rule that files its rows under Borrowing, so the
    category, the reports and the pace all see them as borrowing, never as spending. The rules show on the
    Ledger rules page and the owner can change them. Returns how many rows the rules changed."""
    from .rules import apply_to_ledger, forget, load_rules

    have = {(r.pattern.lower()) for r in session.exec(select(Rule).where(Rule.kind == "categorise", Rule.source == RULE_SOURCE)).all()}
    added = 0
    for key in list(ADVANCE_PROVIDERS) + list(INSTALLMENT_PROVIDERS):
        if key.lower() in have:
            continue
        session.add(Rule(kind="categorise", match_field="", pattern=key, action="categorise", value=BORROWING_CATEGORY, source=RULE_SOURCE))
        added += 1
    if added:
        session.commit()
        forget(session)
    changed = apply_to_ledger(session, load_rules(session)) if added else 0
    return changed


def borrowing_summary(session: Session, today: date) -> dict:
    """The light figures the snapshot, the brief and the watchlist carry: fees in the last ninety days, what
    stands outstanding, and what was taken since the last payday."""
    advance_rows, _installments = _rows(session, today)
    next_payday = _next_payday(session, today)
    paired = pair_advances(advance_rows, next_payday, today)
    since = today - timedelta(days=WINDOW_DAYS)
    fees = sum(p["fee"] for p in paired["pairs"] if p["taken_on"] >= since) + sum(t["amount"] for t in paired["tips"] if t["date"] >= since)
    outstanding = [p for p in paired["pairs"] if not p["repaid"] and not p.get("stale")]
    last_payday = previous_payday(next_payday, _cadence(session), today) if next_payday is not None else None
    since_payday = [p for p in paired["pairs"] if last_payday and p["taken_on"] >= last_payday and p["taken_on"] <= today]
    return {
        "fees_90d": _money(fees), "fees_month": _money(fees * 30.0 / WINDOW_DAYS), "advances_90d": len([p for p in paired["pairs"] if p["taken_on"] >= since]),
        "outstanding_total": _money(sum(p["amount"] for p in outstanding)), "outstanding": len(outstanding),
        "since_payday": {"advances": len(since_payday), "advanced": _money(sum(p["amount"] for p in since_payday)), "providers": sorted({p["provider"] for p in since_payday})},
    }


def sync_borrowing(session: Session, today: date) -> dict:
    """Keep the advances register current from the ledger, file every borrowing row under its own category,
    retire any 'bill' that is really an advance app's repayment, and queue each installment stream the register
    does not know as a series to name."""
    recategorised = ensure_borrowing_rules(session)
    refresh_plan_progress(session, today)
    advance_rows, installment_rows = _rows(session, today)
    paired = pair_advances(advance_rows, _next_payday(session, today), today)
    existing = {(a.provider, a.taken_on, round(a.amount, 2)): a for a in session.exec(select(Advance)).all()}
    created = updated = 0
    for key, row in list(existing.items()):
        if not row.repaid and (today - row.taken_on).days > MATCH_DAYS:  # it never showed a repayment; it is not due against the next check
            session.delete(row)
            existing.pop(key)
    for p in paired["pairs"]:
        if p.get("stale"):
            continue
        key = (p["provider"], p["taken_on"], round(p["amount"], 2))
        row = existing.get(key)
        if row is None:
            session.add(Advance(provider=p["provider"], amount=p["amount"], fee=p["fee"], taken_on=p["taken_on"], repay_date=p["repay_date"], repaid=p["repaid"]))
            created += 1
        elif (row.repaid, round(row.fee, 2), row.repay_date) != (p["repaid"], round(p["fee"], 2), p["repay_date"]):
            row.repaid, row.fee, row.repay_date = p["repaid"], p["fee"], p["repay_date"]
            session.add(row)
            updated += 1
    retired = 0
    for b in session.exec(select(Bill).where(Bill.active == True)).all():  # noqa: E712
        if provider_of(b.name, ADVANCE_PROVIDERS):
            b.active = False
            session.add(b)
            retired += 1
            if b.series_id:
                series = session.get(RecurringSeries, b.series_id)
                if series is not None:
                    series.kind, series.confirmed = "advance", True
                    session.add(series)
    for s in session.exec(select(RecurringSeries)).all():
        if provider_of(s.merchant, ADVANCE_PROVIDERS) and s.kind != "advance":
            s.kind, s.confirmed = "advance", True
            session.add(s)
    queued = 0
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    series_rows = session.exec(select(RecurringSeries)).all()
    for stream in installment_streams(installment_rows, today):
        if not stream["active"]:
            continue
        known = any(p.provider.lower() == stream["provider"].lower() and abs(p.installment - stream["amount"]) < 1.0 for p in plans)
        known = known or any(provider_of(b.name, INSTALLMENT_PROVIDERS) == stream["provider"] and abs(b.amount - stream["amount"]) < 1.0 for b in bills)
        known = known or any(provider_of(s.merchant, INSTALLMENT_PROVIDERS) == stream["provider"] and abs(s.typical_amount - stream["amount"]) < 1.0 for s in series_rows)
        if known:
            continue
        cadence = {7: "weekly", 14: "biweekly", 30: "monthly"}[stream["cadence_days"]]
        session.add(RecurringSeries(merchant_key=stream["provider"].lower(), merchant=stream["provider"], category="Loan Payment", cadence=cadence, typical_amount=stream["amount"], latest_amount=stream["amount"],
                                    charges_seen=stream["payments_seen"], first_seen=stream["first_date"], last_date=stream["last_date"], next_expected=stream["next_date"], active=True, kind="unknown", confirmed=False, suggested_kind="installment"))
        queued += 1
    session.commit()
    return {"advances_created": created, "advances_updated": updated, "bills_retired": retired, "streams_queued": queued, "rows_recategorised": recategorised}


def borrowing_overview(session: Session, today: date) -> dict:
    """The picture: what advances and installments cost, the cycle they run in, what stands outstanding, and
    the steps that end it. Everything here is read from the ledger and the registers."""
    advance_rows, installment_rows = _rows(session, today)
    next_payday = _next_payday(session, today)
    paired = pair_advances(advance_rows, next_payday, today)
    since = today - timedelta(days=WINDOW_DAYS)
    pairs = [p for p in paired["pairs"] if p["taken_on"] >= since]
    tips = [t for t in paired["tips"] if t["date"] >= since]
    fees = sum(p["fee"] for p in pairs) + sum(t["amount"] for t in tips)
    advanced = sum(p["amount"] for p in pairs)
    outstanding = [p for p in paired["pairs"] if not p["repaid"] and not p.get("stale")]
    unresolved = [p for p in pairs if p.get("stale")]
    by_provider: dict[str, dict] = {}
    for p in pairs:
        row = by_provider.setdefault(p["provider"], {"provider": p["provider"], "advances": 0, "advanced": 0.0, "fees": 0.0, "outstanding": 0.0, "typical_advance": [], "days": []})
        row["advances"] += 1
        row["advanced"] += p["amount"]
        row["fees"] += p["fee"]
        row["typical_advance"].append(p["amount"])
        if p["days"]:
            row["days"].append(p["days"])
        if not p["repaid"]:
            row["outstanding"] += p["amount"]
    for t in tips:
        row = by_provider.setdefault(t["provider"], {"provider": t["provider"], "advances": 0, "advanced": 0.0, "fees": 0.0, "outstanding": 0.0, "typical_advance": [], "days": []})
        row["fees"] += t["amount"]
    providers = []
    for row in by_provider.values():
        rate = (row["fees"] / row["advanced"]) if row["advanced"] else 0.0
        days = statistics.median(row["days"]) if row["days"] else None
        providers.append({
            "provider": row["provider"], "advances": row["advances"], "advanced": _money(row["advanced"]), "fees": _money(row["fees"]), "outstanding": _money(row["outstanding"]),
            "typical_advance": _money(statistics.median(row["typical_advance"])) if row["typical_advance"] else 0.0, "fee_share": _money(rate), "typical_days": days,
            "annual_rate": _money(rate * 365.0 / days) if days else None,
        })
    providers.sort(key=lambda r: -r["fees"])

    # The cycle: for each payday in the window, what was advanced since the one before and what was repaid on it.
    cycle: list[dict] = []
    paydays: list[date] = []
    if next_payday is not None:
        cadence = _cadence(session)
        back: date | None = next_payday
        guard = 0
        while back is not None and back > since and guard < 12:
            paydays.insert(0, back)
            back = previous_payday(back, cadence, back)
            guard += 1
        for i, pd in enumerate(paydays):
            start = paydays[i - 1] if i > 0 else since
            taken = [p for p in paired["pairs"] if start <= p["taken_on"] < pd]
            repaid_on = [p for p in paired["pairs"] if p["repaid"] and p["repay_date"] == pd]
            cycle.append({"payday": pd.isoformat(), "advances": len(taken), "advanced": _money(sum(p["amount"] for p in taken)), "repaid_on_payday": _money(sum(p["amount"] + p["fee"] for p in repaid_on)), "fees": _money(sum(p["fee"] for p in repaid_on))})
    advances_per_period = [c["advanced"] for c in cycle if c["payday"] <= today.isoformat()] or [c["advanced"] for c in cycle]
    typical_period = _money(statistics.median(advances_per_period)) if advances_per_period else 0.0

    streams = installment_streams(installment_rows, today)
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    series_rows = session.exec(select(RecurringSeries).where(RecurringSeries.confirmed == False)).all()  # noqa: E712
    installments = []
    for s in streams:
        plan = next((p for p in plans if p.provider.lower() == s["provider"].lower() and abs(p.installment - s["amount"]) < 1.0), None)
        bill = next((b for b in bills if provider_of(b.name, INSTALLMENT_PROVIDERS) == s["provider"] and abs(b.amount - s["amount"]) < 1.0), None)
        series = next((r for r in series_rows if provider_of(r.merchant, INSTALLMENT_PROVIDERS) == s["provider"] and abs(r.typical_amount - s["amount"]) < 1.0), None)
        status = "plan" if plan else "bill" if bill else "pending" if series else "unknown"
        installments.append({
            **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in s.items()},
            "status": status, "plan_id": plan.id if plan else None, "bill_id": bill.id if bill else None, "series_id": series.id if series else None,
            "remaining": plan.remaining if plan else None, "ends": (plan.next_date + timedelta(days=plan.cadence_days * max(0, plan.remaining - 1))).isoformat() if plan else None,
            "per_month": _money(s["amount"] * 30.0 / s["cadence_days"]),
        })
    installments_month = _money(sum(i["per_month"] for i in installments if i["active"]))
    import json as _json

    loans = []
    for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all():  # noqa: E712
        sched = sorted(_json.loads(p.schedule or "[]"), key=lambda r: r["date"])
        upcoming = sched[len(sched) - p.remaining:] if sched and p.remaining else []
        loans.append({
            "id": p.id, "provider": p.provider, "merchant": p.merchant, "loan_id": p.loan_id, "loan_amount": p.loan_amount, "total": p.total, "finance_charge": p.finance_charge, "installment": p.installment,
            "balance": p.balance, "balance_at": p.balance_at.isoformat() if p.balance_at else None, "apr": p.apr, "interest_paid": p.interest_paid, "pay_from_account_id": p.pay_from_account_id,
            "count": p.count, "remaining": p.remaining, "next_date": p.next_date.isoformat(), "ends": (upcoming[-1]["date"] if upcoming else (p.next_date + timedelta(days=p.cadence_days * max(0, p.remaining - 1))).isoformat()),
            "left_to_pay": _money(sum(r["amount"] for r in upcoming) if upcoming else p.installment * p.remaining), "interest_left": _money(sum(r.get("interest", 0.0) for r in upcoming)) if upcoming else None, "from_lender": bool(sched),
        })
    loans.sort(key=lambda l: l["next_date"])
    installment_interest = _money(sum(l["finance_charge"] for l in loans))
    left_to_pay = _money(sum((l.get("balance") or l["left_to_pay"]) for l in loans))
    statement = get_setting(session, "affirm", {}) or {}

    cards = [a for a in session.exec(select(Account).where(Account.active == True, Account.kind == "card")).all()]  # noqa: E712
    card_balance = _money(sum((a.balance or 0.0) for a in cards))
    from ..models import Debt
    from .projection import next_due

    debts = session.exec(select(Debt).where(Debt.active == True, Debt.kind == "card")).all()  # noqa: E712
    credit: list[dict] = []
    for a in cards:
        debt = next((d for d in debts if d.account_id == a.id), None)
        limit = (debt.credit_limit if debt and debt.credit_limit else None) or a.credit_limit
        balance = float(a.balance or 0.0)
        statement = next_due(int(debt.statement_day), today) if debt and debt.statement_day else None
        due = next_due(int(debt.due_day), today) if debt and debt.due_day else None
        credit.append({
            "account_id": a.id, "debt_id": debt.id if debt else None, "name": a.name, "balance": _money(balance), "credit_limit": limit, "utilisation": round(balance / limit, 4) if limit else None, "utilisation_status": _limit_status(balance / limit if limit else None),
            "apr": debt.apr if debt else None, "pay_in_full": debt.pay_in_full if debt else None, "statement_day": debt.statement_day if debt else None, "next_statement": statement.isoformat() if statement else None,
            "due_day": debt.due_day if debt else None, "next_due": due.isoformat() if due else None,
            "interest_month_if_carried": _money(balance * (debt.apr if debt else 0.0) / 100.0 / 12.0),
        })
    card_interest_month = _money(sum(c["interest_month_if_carried"] for c in credit if not c["pay_in_full"]))

    fees_month = _money(fees * 30.0 / WINDOW_DAYS)
    fees_year = _money(fees_month * 12)
    affirm_stmt = get_setting(session, "affirm", {}) or {}
    interest_month = _money(float(affirm_stmt.get("interest_period") or 0.0))
    if interest_month:
        fees_month = _money(fees_month + interest_month)
        fees_year = _money(fees_year + interest_month * 12)
    becomes = None
    if fees_month > 0:
        from .projections import paychecks_per_year
        from .where_to_put import where_to_put

        per_year = paychecks_per_year(session)
        grown = where_to_put(session, today, _money(fees_month * 12 / per_year), years=10, scenario="mid")
        if grown["best"]:
            best = grown["options"][0]
            becomes = {"per_paycheck": grown["amount_per_paycheck"], "years": grown["years"], "name": best["name"], "after_tax": best["after_tax"], "guaranteed": best["guaranteed"]}

    steps: list[dict] = []
    if outstanding:
        total_out = _money(sum(p["amount"] for p in outstanding))
        steps.append({"n": 1, "text": f"{len(outstanding)} advance{'s' if len(outstanding) != 1 else ''} ({', '.join(sorted({p['provider'] for p in outstanding}))}) comes out of the next paycheck: ${total_out:,.2f}. The plan already holds it back.", "action": "Today", "link": "/today"})
    if typical_period > 0:
        steps.append({"n": len(steps) + 1, "text": f"The cycle runs on about ${typical_period:,.0f} advanced each pay period and ${fees_month:,.0f} a month in fees. Keeping ${typical_period:,.0f} of one paycheck unspent ends it: the next period is paid from that instead of from an advance.", "action": "Set it as a goal", "link": "/today#goals"})
    unknown = [i for i in installments if i["active"] and i["status"] != "plan"]
    if unknown:
        steps.append({"n": len(steps) + 1, "text": f"{len(unknown)} installment stream{'s' if len(unknown) != 1 else ''} ({', '.join(sorted({i['provider'] for i in unknown}))}) {'are' if len(unknown) != 1 else 'is'} not on the register as plans with an end date, so the windows cannot see when they stop. Enter the payments left on each; the lender's app shows it.", "action": "Below", "link": "/borrowing#installments"})
    if installments_month > 0:
        steps.append({"n": len(steps) + 1, "text": f"Installments take about ${installments_month:,.0f} a month. As each plan ends, that payment is free; the engine moves it to the next plan or the goal when you say so.", "action": "Plans", "link": "/borrowing#installments"})
    for c in credit:
        if c["balance"] <= 0:
            continue
        if c["credit_limit"] and c["apr"] is not None:
            use = f"{c['utilisation'] * 100:.0f}% of its ${c['credit_limit']:,.0f} line" if c["utilisation"] is not None else "its line"
            closes = f" The statement closes on {date.fromisoformat(c['next_statement']).strftime('%b %d')}; what is owed that day is what gets reported, so a balance paid before it reports low use." if c["next_statement"] else ""
            carried = f" Carried a month at {c['apr']:g}% it costs ${c['interest_month_if_carried']:,.0f}." if not c["pay_in_full"] else ""
            steps.append({"n": len(steps) + 1, "text": f"{c['name']} is at {use}.{closes}{carried}", "action": "Debts", "link": "/debts"})
        else:
            steps.append({"n": len(steps) + 1, "text": f"{c['name']} carries ${c['balance']:,.0f}. Paid in full from the next checks, it costs nothing; carried, its APR applies. Enter the card's APR, line and statement day on the Debts page and the cost and the use show here.", "action": "Debts", "link": "/debts"})
    steps.append({"n": len(steps) + 1, "text": "Credit: advance apps and pay-in-four plans do not build it, and a missed Affirm payment can hurt it. What moves a score is every card and loan payment on time, and the card balance kept well under its limit at statement time.", "action": "Accounts", "link": "/accounts"})

    sources = sorted({src for p in pairs for src in p["sources"]} | {src for s in streams for src in s["sources"]} | {f"account:{a.id}" for a in cards})
    return {
        "as_of": today.isoformat(), "window_days": WINDOW_DAYS,
        "cost": {"fees_90d": _money(fees), "fees_month": fees_month, "fees_year": fees_year, "advanced_90d": _money(advanced), "advances_90d": len(pairs), "tips_90d": _money(sum(t["amount"] for t in tips)), "installments_month": installments_month, "card_balance": card_balance, "card_interest_month": card_interest_month,
                 "affirm_interest_month": interest_month, "affirm_interest_ytd": float(affirm_stmt.get("interest_ytd") or 0.0)},
        "credit": credit,
        "providers": providers, "outstanding": [{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}} for p in outstanding], "outstanding_total": _money(sum(p["amount"] for p in outstanding)),
        "cycle": cycle, "typical_per_period": typical_period, "next_payday": next_payday.isoformat() if next_payday else None,
        "unresolved": [{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}} for p in unresolved], "unresolved_total": _money(sum(p["amount"] for p in unresolved)),
        "advances": [{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}} for p in sorted(pairs, key=lambda p: p["taken_on"], reverse=True)],
        "installments": installments, "loans": loans, "loans_left_to_pay": left_to_pay, "loans_finance_charge": installment_interest, "statement": statement, "becomes": becomes, "steps": steps, "sources": sources,
        "not_advice": NOT_ADVICE + " What an advance costs is its fee over the days it is out; what a plan costs needs the lender's own figures, which only you can enter.",
    }


def plan_from_fields(session: Session, fields: dict, amount: float | None, remaining: int | None, today: date) -> InstallmentPlan | None:
    """A plan from a lender's confirmation (its schedule) or from what the owner typed (an amount and the
    payments left). The lender's loan ID keeps one email from making two plans."""
    import json

    schedule = fields.get("schedule") or []
    loan_id = str(fields.get("loan_id") or "")
    provider = str(fields.get("provider") or provider_of(str(fields.get("sender", "")) + " " + str(fields.get("sender_name", "")), INSTALLMENT_PROVIDERS) or fields.get("sender_name") or "Lender")
    merchant = str(fields.get("merchant") or "")
    existing = session.exec(select(InstallmentPlan).where(InstallmentPlan.loan_id == loan_id)).first() if loan_id else None
    if schedule:
        upcoming = [s for s in schedule if s["date"] >= today.isoformat()]
        gaps = [(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days for a, b in zip(schedule, schedule[1:])]
        cadence_days = 30 if not gaps or sum(gaps) / len(gaps) > 20 else 14
        plan = existing or InstallmentPlan(provider=provider, merchant=merchant, total=0.0, installment=0.0, count=0, remaining=0, next_date=today)
        plan.provider, plan.merchant = provider, merchant or plan.merchant
        plan.total = float(fields.get("total") or sum(s["amount"] for s in schedule))
        plan.installment = float(fields.get("installment") or schedule[0]["amount"])
        plan.count, plan.remaining = len(schedule), len(upcoming)
        plan.next_date = date.fromisoformat(upcoming[0]["date"]) if upcoming else date.fromisoformat(schedule[-1]["date"])
        plan.cadence_days, plan.loan_id, plan.schedule = cadence_days, loan_id, json.dumps(schedule)
        plan.finance_charge = float(fields.get("finance_charge") or 0.0)
        plan.loan_amount = float(fields["loan_amount"]) if fields.get("loan_amount") else None
        plan.active = plan.remaining > 0
    else:
        each = amount if amount is not None else fields.get("amount")
        if each is None or not remaining:
            return None
        plan = existing or InstallmentPlan(provider=provider, merchant=merchant, total=round(float(each) * remaining, 2), installment=round(float(each), 2), count=remaining, remaining=remaining, next_date=today, cadence_days=30, loan_id=loan_id)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    refresh_plan_progress(session, today)
    return plan


def refresh_plan_progress(session: Session, today: date) -> int:
    """A plan with the lender's schedule follows the ledger: each payment to the lender of a scheduled amount
    on or after the loan's first date ticks one off. Returns how many plans changed."""
    import json

    changed = 0
    active = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    plans = [p for p in active if p.schedule and p.schedule != "[]"]
    unscheduled = [p for p in active if not (p.schedule and p.schedule != "[]") and p.remaining > 0 and p.installment > 0]
    if not plans and not unscheduled:
        return 0
    rows = session.exec(select(Transaction).where(Transaction.amount > 0)).all()
    # A plan known only by its installment and next date: each payment of that amount to the lender from five days
    # before the next date on ticks one off and moves the next date on by the plan's cadence. A payment counts once.
    used_ids: set[int] = set()
    for p in unscheduled:
        pays = sorted((t for t in rows if abs(float(t.amount) - p.installment) <= 0.05 and provider_of(_text(t), INSTALLMENT_PROVIDERS) == p.provider), key=lambda t: t.date)
        moved = False
        for t in pays:
            if p.remaining <= 0:
                break
            if t.id in used_ids or t.date < p.next_date - timedelta(days=5) or t.date > today:
                continue
            used_ids.add(t.id)
            p.remaining -= 1
            p.next_date = p.next_date + timedelta(days=p.cadence_days or 14)
            moved = True
        if moved:
            p.active = p.remaining > 0
            session.add(p)
            changed += 1
    paid_to = {}
    for t in rows:
        provider = provider_of(_text(t), INSTALLMENT_PROVIDERS)
        if provider:
            paid_to.setdefault(provider, []).append((t.date, float(t.amount)))
    for p in plans:
        schedule = sorted(json.loads(p.schedule), key=lambda s: s["date"])
        first = date.fromisoformat(schedule[0]["date"])
        amounts = [s["amount"] for s in schedule]
        matched = 0
        pool = sorted((row for row in paid_to.get(p.provider, []) if row[0] >= first - timedelta(days=5)), key=lambda r: r[0])
        used: set = set()
        for i, target in enumerate(amounts):
            hit = next((j for j, (d, a) in enumerate(pool) if j not in used and abs(a - target) <= 0.05), None)
            if hit is None:
                break
            used.add(hit)
            matched += 1
        remaining = max(0, len(schedule) - matched)
        upcoming = schedule[len(schedule) - remaining:] if remaining else []
        next_date = date.fromisoformat(upcoming[0]["date"]) if upcoming else date.fromisoformat(schedule[-1]["date"])
        if (p.remaining, p.next_date, p.active) != (remaining, next_date, remaining > 0):
            p.remaining, p.next_date, p.active = remaining, next_date, remaining > 0
            session.add(p)
            changed += 1
    if changed:
        session.commit()
    return changed


def apply_affirm_statement(session: Session, parsed: dict, today: date, *, source: str = "statement", pay_from_account_id: int | None = None) -> dict:
    """Affirm's monthly statement into the register: each loan by its ID with its merchant, amount, APR, closing
    balance, next payment and status; the period's interest added to each loan's interest paid; a closed loan
    retired. A plan the ledger built from payments is matched by its installment amount and takes the ID."""
    import math

    period_end = date.fromisoformat(parsed["period_end"]) if parsed.get("period_end") else today
    plans = session.exec(select(InstallmentPlan)).all()
    by_id = {p.loan_id: p for p in plans if p.loan_id}
    created = updated = retired = 0
    for loan in parsed["loans"]:
        plan = by_id.get(loan["loan_id"])
        if plan is None and loan.get("payment_amount"):
            plan = next((p for p in plans if not p.loan_id and p.provider.lower() == "affirm" and abs(p.installment - loan["payment_amount"]) < 0.05), None)
        if plan is None:
            if loan["status"] != "active":
                continue
            plan = InstallmentPlan(provider="Affirm", merchant=loan["merchant"], total=loan["amount"], installment=loan.get("payment_amount") or 0.0, count=0, remaining=0, next_date=today, cadence_days=30, loan_id=loan["loan_id"])
            created += 1
        else:
            updated += 1
        plan.loan_id = loan["loan_id"]
        plan.merchant = loan["merchant"] or plan.merchant
        plan.loan_amount = loan["amount"]
        plan.apr = loan.get("apr")
        installment = loan.get("payment_amount") or plan.installment
        plan.installment = installment or plan.installment
        plan.balance, plan.balance_at = loan["closing_balance"], period_end
        plan.active = loan["status"] == "active" and loan["closing_balance"] > 0
        if not plan.active:
            retired += 1
            plan.remaining = 0
        else:
            if loan.get("payment_due"):
                plan.next_date = date.fromisoformat(loan["payment_due"])
            if plan.installment and (plan.schedule in ("", "[]")):
                plan.remaining = max(1, math.ceil(loan["closing_balance"] / plan.installment))  # at least this many at the current payment; interest can add one
                plan.count = max(plan.count, plan.remaining + sum(1 for t in parsed.get("transactions", []) if t["loan_id"] == loan["loan_id"] and t["description"].lower() == "payment"))
        if loan["loan_id"] in parsed.get("interest", {}):
            seen = set_setting_interest(session, loan["loan_id"], period_end, parsed["interest"][loan["loan_id"]])
            if seen:
                plan.interest_paid = round(plan.interest_paid + parsed["interest"][loan["loan_id"]], 2)
        if pay_from_account_id and not plan.pay_from_account_id:
            plan.pay_from_account_id = pay_from_account_id
        session.add(plan)
        by_id[loan["loan_id"]] = plan
    # The statement lists every active loan, so an Affirm plan typed from payments that matches none of them is
    # a guess the statement has overtaken: it is retired, and the statement's loans stand in its place.
    unlisted = 0
    for p in plans:
        if p.provider.lower() == "affirm" and not p.loan_id and p.active:
            p.active, p.remaining = False, 0
            session.add(p)
            unlisted += 1
    stored = get_setting(session, "affirm", {}) or {}
    if parsed.get("period_end") and parsed.get("period_end") >= str(stored.get("period_end", "")):
        stored.update({"period_start": parsed.get("period_start"), "period_end": parsed.get("period_end"), "interest_period": parsed.get("interest_period"), "interest_ytd": parsed.get("interest_ytd"), "total_closing": parsed.get("total_closing"), "source": source, "at": today.isoformat()})
        set_setting(session, "affirm", stored)
    session.commit()
    return {"created": created, "updated": updated, "retired": retired, "typed_retired": unlisted, "loans": len(parsed["loans"]), "period_end": parsed.get("period_end"), "interest_period": parsed.get("interest_period"), "interest_ytd": parsed.get("interest_ytd"), "total_closing": parsed.get("total_closing")}


def set_setting_interest(session: Session, loan_id: str, period_end: date, amount: float) -> bool:
    """Interest for a period is added once per loan and period, however many times the statement is read."""
    seen = get_setting(session, "affirm.interest_seen", {}) or {}
    key = f"{loan_id}:{period_end.isoformat()}"
    if key in seen:
        return False
    seen[key] = amount
    set_setting(session, "affirm.interest_seen", seen)
    return True


def statement_fact_from_file(session: Session, data: bytes, filename: str, today: date, source: str) -> dict | None:
    """An Affirm statement PDF handed over as a file: read and applied at once. The statement is the lender's own
    register, not a claim about the owner, so it lands without a confirmation and says what it changed."""
    from ..parsers.affirm_statement import parse_affirm_statement
    from ..parsers.paystub import pdf_text

    text = pdf_text(data) if filename.lower().endswith(".pdf") else data.decode("utf-8", errors="replace")
    parsed = parse_affirm_statement(text)
    if not parsed:
        return None
    return apply_affirm_statement(session, parsed, today, source=source)


def _limit_status(share: float | None) -> dict | None:
    from .reports import limit_status

    return limit_status(share)
