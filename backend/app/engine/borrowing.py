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
from .cadence import next_paydays
from .projections import NOT_ADVICE

BORROWING_CATEGORY = "Borrowing"
RULE_SOURCE = "engine:borrowing"

ADVANCE_PROVIDERS = {"earnin": "Earnin", "dave": "Dave", "brigit": "Brigit", "moneylion": "MoneyLion", "cleo": "Cleo", "empower": "Empower", "klover": "Klover", "floatme": "FloatMe", "chime spotme": "Chime SpotMe", "albert instant": "Albert"}
INSTALLMENT_PROVIDERS = {"affirm": "Affirm", "klarna": "Klarna", "afterpay": "Afterpay", "sezzle": "Sezzle", "zip pay": "Zip", "quadpay": "Zip", "paypal pay in 4": "PayPal Pay in 4", "uplift": "Uplift"}
INSTALLMENT_DEFAULT_DAYS = {"Affirm": 30, "Klarna": 14, "Afterpay": 14, "Sezzle": 14, "Zip": 14, "PayPal Pay in 4": 14, "Uplift": 30}
LOOKBACK_DAYS = 180
FEE_CEILING = 0.30  # a repayment more than 30% over the advance is not its repayment
TIP_CEILING = 15.0  # a small payment to an advance app with no advance behind it is a tip or a membership fee
WINDOW_DAYS = 90


def _money(v: float) -> float:
    return round(float(v), 2)


def provider_of(text: str, table: dict[str, str]) -> str | None:
    low = (text or "").lower()
    for key, name in table.items():
        if re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", low):
            return name
    return None


def _text(t: Transaction) -> str:
    return f"{t.merchant} {t.raw_name} {t.description}"


def pair_advances(rows: list[dict], next_payday: date | None = None) -> dict:
    """rows: dicts with id, date, amount (positive = money out), provider. Money in from an advance app is an
    advance; money out to it is a repayment. Each advance takes the first later repayment that covers it with a
    fee of up to thirty percent, or a run of partial repayments that adds up within ten days; an advance with
    no repayment yet is outstanding and due on the next payday. Small payments with no advance behind them are
    tips and membership fees."""
    ins = sorted((r for r in rows if r["amount"] < 0), key=lambda r: (r["date"], r["id"]))
    outs = sorted((r for r in rows if r["amount"] > 0), key=lambda r: (r["date"], r["id"]))
    used: set = set()
    pairs: list[dict] = []
    for adv in ins:
        amount = -adv["amount"]
        taken = adv["date"]
        whole = None
        for out in outs:
            if out["id"] in used or out["provider"] != adv["provider"] or out["date"] < taken or (out["date"] - taken).days > 45:
                continue
            if amount * 0.98 <= out["amount"] <= amount * (1.0 + FEE_CEILING):
                whole = [out]
                break
        partial = None
        run: list[dict] = []
        total = 0.0
        for out in outs:
            if out["id"] in used or out["provider"] != adv["provider"] or out["date"] < taken or (out["date"] - taken).days > 45 or out["amount"] >= amount * 0.98:
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
            due = next_payday if next_payday and next_payday > taken else taken + timedelta(days=14)
            pairs.append({"provider": adv["provider"], "taken_on": taken, "amount": _money(amount), "fee": 0.0, "repaid": False, "repay_date": due, "days": None, "annual_rate": None, "sources": [f"transaction:{adv['id']}"]})
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
    for t in session.exec(select(Transaction).where(Transaction.date >= since, Transaction.is_ignored == False)).all():  # noqa: E712
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
    upcoming = sorted(i.next_date for i in incomes if i.next_date >= today)
    return upcoming[0] if upcoming else None


def _cadence(session: Session) -> str:
    from ..models import Income

    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    return (income.cadence if income else "biweekly") or "biweekly"


def previous_payday(next_payday: date, cadence: str, before: date) -> date | None:
    """The last payday strictly before `before`, walked back from the next one by cadence."""
    walk = next_paydays(next_payday - timedelta(days=45), cadence, 8)[1:] + [next_payday]
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
    paired = pair_advances(advance_rows, next_payday)
    since = today - timedelta(days=WINDOW_DAYS)
    fees = sum(p["fee"] for p in paired["pairs"] if p["taken_on"] >= since) + sum(t["amount"] for t in paired["tips"] if t["date"] >= since)
    outstanding = [p for p in paired["pairs"] if not p["repaid"]]
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
    advance_rows, installment_rows = _rows(session, today)
    paired = pair_advances(advance_rows, _next_payday(session, today))
    existing = {(a.provider, a.taken_on, round(a.amount, 2)): a for a in session.exec(select(Advance)).all()}
    created = updated = 0
    for p in paired["pairs"]:
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
    paired = pair_advances(advance_rows, next_payday)
    since = today - timedelta(days=WINDOW_DAYS)
    pairs = [p for p in paired["pairs"] if p["taken_on"] >= since]
    tips = [t for t in paired["tips"] if t["date"] >= since]
    fees = sum(p["fee"] for p in pairs) + sum(t["amount"] for t in tips)
    advanced = sum(p["amount"] for p in pairs)
    outstanding = [p for p in paired["pairs"] if not p["repaid"]]
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

    cards = [a for a in session.exec(select(Account).where(Account.active == True, Account.kind == "card")).all()]  # noqa: E712
    card_balance = _money(sum((a.balance or 0.0) for a in cards))

    fees_month = _money(fees * 30.0 / WINDOW_DAYS)
    fees_year = _money(fees_month * 12)
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
    if card_balance > 0:
        steps.append({"n": len(steps) + 1, "text": f"The card carries ${card_balance:,.0f}. Paid in full from the next checks, it costs nothing; carried, its APR applies. Enter the card's APR and limit on the Debts page and the cost and the utilisation show here.", "action": "Debts", "link": "/debts"})
    steps.append({"n": len(steps) + 1, "text": "Credit: advance apps and pay-in-four plans do not build it, and a missed Affirm payment can hurt it. What moves a score is every card and loan payment on time, and the card balance kept well under its limit at statement time.", "action": "Accounts", "link": "/accounts"})

    sources = sorted({src for p in pairs for src in p["sources"]} | {src for s in streams for src in s["sources"]} | {f"account:{a.id}" for a in cards})
    return {
        "as_of": today.isoformat(), "window_days": WINDOW_DAYS,
        "cost": {"fees_90d": _money(fees), "fees_month": fees_month, "fees_year": fees_year, "advanced_90d": _money(advanced), "advances_90d": len(pairs), "tips_90d": _money(sum(t["amount"] for t in tips)), "installments_month": installments_month, "card_balance": card_balance, "card_interest_month": None},
        "providers": providers, "outstanding": [{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}} for p in outstanding], "outstanding_total": _money(sum(p["amount"] for p in outstanding)),
        "cycle": cycle, "typical_per_period": typical_period, "next_payday": next_payday.isoformat() if next_payday else None,
        "advances": [{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()}} for p in sorted(pairs, key=lambda p: p["taken_on"], reverse=True)],
        "installments": installments, "becomes": becomes, "steps": steps, "sources": sources,
        "not_advice": NOT_ADVICE + " What an advance costs is its fee over the days it is out; what a plan costs needs the lender's own figures, which only you can enter.",
    }
