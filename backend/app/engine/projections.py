"""The outlook: savings and investment balances carried forward from today's balances, the contributions the
paystubs and goals show, and rates the owner sets as assumptions. Three scenarios, nominal and in today's
dollars, a retirement line when the owner names the year. Arithmetic only; the rates are assumptions, and
the page says so beside every figure."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, Income
from ..store import get_setting
from .goals import goals_plan
from .investments import contributions_from_paystubs

NOT_ADVICE = "Computed by the engine from today's balances, the contributions on record and the rates you set as assumptions. A rate is a guess, not a promise. Not financial advice."
DEFAULT_ASSUMPTIONS: dict = {
    "savings_apy": 0.04,
    "returns": {"low": 0.04, "mid": 0.07, "high": 0.10},
    "inflation": 0.025,
    "years": [1, 5, 10, 20, 30],
    "years_to_retirement": None,
    "extra_savings_per_paycheck": 0.0,
    "investment_contribution_per_paycheck": None,
    "employer_match_per_paycheck": 0.0,
    "withdrawal_rate": 0.04,
    "marginal_tax_rate": 0.22,
    "capital_gains_rate": 0.15,
    "employer_match_rate": 0.0,
    "employer_match_cap_per_paycheck": 0.0,
    "roth_limit_per_year": 7500.0,
}
PAYCHECKS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12, "quarterly": 4, "yearly": 1}
SCENARIO_WORDS = {"low": "cautious", "mid": "middle", "high": "strong"}


def _money(v: float) -> float:
    return round(float(v), 2)


def assumptions(session: Session) -> dict:
    stored = get_setting(session, "projections", {}) or {}
    out = {**DEFAULT_ASSUMPTIONS, **stored}
    out["returns"] = {**DEFAULT_ASSUMPTIONS["returns"], **(stored.get("returns") or {})}
    out["years"] = sorted({int(y) for y in out["years"] if 1 <= int(y) <= 60}) or [1, 5, 10, 20, 30]
    return out


def paychecks_per_year(session: Session) -> int:
    income = session.exec(select(Income).where(Income.active == True).order_by(Income.id)).first()  # noqa: E712
    return PAYCHECKS_PER_YEAR.get(income.cadence if income else "biweekly", 26)


def grow(start: float, annual_rate: float, monthly_contribution: float, months: int) -> list[float]:
    """Balance at the end of each month: the balance grows at the monthly share of the annual rate, then the
    month's contribution lands. Month 0 is today."""
    monthly = (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0
    out = [start]
    balance = start
    for _ in range(months):
        balance = balance * (1.0 + monthly) + monthly_contribution
        out.append(balance)
    return out


def month_label(today: date, offset: int) -> str:
    month = (today.month - 1 + offset) % 12 + 1
    year = today.year + (today.month - 1 + offset) // 12
    return f"{year:04d}-{month:02d}"


def savings_outlook(session: Session, today: date, a: dict, per_year: int) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "savings")).all()  # noqa: E712
    now = sum((x.balance or 0.0) for x in accounts)
    cadence = {52: "weekly", 26: "biweekly", 24: "semimonthly", 12: "monthly"}.get(int(per_year), "biweekly")
    snap_next_payday = today + timedelta(days=14)
    goals = goals_plan(session, today, snap_next_payday, cadence)
    goal_pace = sum(g["per_paycheck"] for g in goals["items"] if g["kind"] == "save")
    per_paycheck = goal_pace + float(a.get("extra_savings_per_paycheck") or 0.0)
    per_month = per_paycheck * per_year / 12.0
    series = grow(now, float(a["savings_apy"]), per_month, 36)
    months = [{"month": month_label(today, i), "balance": _money(v)} for i, v in enumerate(series)]
    milestones = [{"months": m, "balance": _money(series[m]), "contributed": _money(now + per_month * m), "interest": _money(series[m] - now - per_month * m)} for m in (6, 12, 24, 36)]
    return {
        "now": _money(now), "apy": float(a["savings_apy"]), "per_paycheck": _money(per_paycheck), "goals_per_paycheck": _money(goal_pace), "extra_per_paycheck": _money(float(a.get("extra_savings_per_paycheck") or 0.0)),
        "per_month": _money(per_month), "months": months, "milestones": milestones,
        "accounts": [{"id": x.id, "name": x.name, "institution": x.institution, "balance": x.balance, "source": f"account:{x.id}"} for x in accounts],
        "sources": [f"account:{x.id}" for x in accounts] + [g["source"] for g in goals["items"] if g["kind"] == "save"] + ["setting:projections"],
    }


def investment_outlook(session: Session, today: date, a: dict, per_year: int) -> dict:
    accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()  # noqa: E712
    now = sum((x.balance or 0.0) for x in accounts)
    stubs = contributions_from_paystubs(session, today)
    override = a.get("investment_contribution_per_paycheck")
    own = float(override) if override is not None else stubs["per_paycheck"]
    match = float(a.get("employer_match_per_paycheck") or 0.0)
    per_paycheck = own + match
    per_month = per_paycheck * per_year / 12.0
    years = a["years"]
    horizon = max(years)
    retire_in = a.get("years_to_retirement")
    retire_in = int(retire_in) if retire_in not in (None, "", 0) else None
    if retire_in:
        horizon = max(horizon, retire_in)
    inflation = float(a.get("inflation") or 0.0)
    scenarios = []
    for key in ("low", "mid", "high"):
        rate = float(a["returns"][key])
        series = grow(now, rate, per_month, horizon * 12)
        by_year = []
        for y in range(1, horizon + 1):
            balance = series[y * 12]
            contributed = now + per_month * 12 * y
            by_year.append({"year": y, "balance": _money(balance), "contributed": _money(contributed), "growth": _money(balance - contributed), "real": _money(balance / ((1.0 + inflation) ** y))})
        milestones = [row for row in by_year if row["year"] in years]
        at_retirement = None
        if retire_in:
            row = by_year[retire_in - 1]
            monthly = row["balance"] * float(a.get("withdrawal_rate") or 0.04) / 12.0
            at_retirement = {**row, "monthly_at_withdrawal_rate": _money(monthly), "monthly_real": _money(row["real"] * float(a.get("withdrawal_rate") or 0.04) / 12.0)}
        scenarios.append({"id": key, "word": SCENARIO_WORDS[key], "rate": rate, "years": by_year, "milestones": milestones, "at_retirement": at_retirement})
    return {
        "now": _money(now), "per_paycheck": _money(per_paycheck), "own_per_paycheck": _money(own), "employer_match_per_paycheck": _money(match),
        "contribution_source": "assumption" if override is not None else ("paystubs" if stubs["items"] else "none"), "paystub_lines": stubs["items"],
        "per_month": _money(per_month), "horizon_years": horizon, "milestone_years": years, "years_to_retirement": retire_in, "withdrawal_rate": float(a.get("withdrawal_rate") or 0.04),
        "scenarios": scenarios,
        "accounts": [{"id": x.id, "name": x.name, "institution": x.institution, "subtype": x.subtype, "balance": x.balance, "source": f"account:{x.id}"} for x in accounts],
        "sources": [f"account:{x.id}" for x in accounts] + (["paystubs"] if stubs["items"] and override is None else []) + ["setting:projections"],
    }


def outlook(session: Session, today: date) -> dict:
    a = assumptions(session)
    per_year = paychecks_per_year(session)
    return {
        "as_of": today.isoformat(),
        "assumptions": a,
        "paychecks_per_year": per_year,
        "savings": savings_outlook(session, today, a, per_year),
        "investments": investment_outlook(session, today, a, per_year),
        "not_advice": NOT_ADVICE,
    }
