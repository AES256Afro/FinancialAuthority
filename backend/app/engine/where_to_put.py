"""Where should $X a paycheck go? The same take-home dollars sent to each place the owner could send them,
carried to the horizon under the owner's assumptions: what each comes to, what is kept after tax, whether
it is guaranteed and reachable, and the step that starts it. Arithmetic with named assumptions, ranked by
what is kept; the page and the models say it is not a recommendation."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from ..models import Account, Debt
from .projections import NOT_ADVICE, SCENARIO_WORDS, assumptions, grow, month_label, paychecks_per_year

DESTINATION_WORDS = {"savings": "Savings account", "k401_pretax": "401(k), pre-tax", "roth_ira": "Roth IRA", "brokerage": "Brokerage account", "hsa": "HSA"}


def _money(v: float) -> float:
    return round(float(v), 2)


def money_word(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


def months_to_retire(balance: float, apr: float, per_month: float, cap: int) -> int:
    """How many months of the extra payments, each earning the APR as avoided interest, until they equal the balance."""
    if per_month <= 0 or balance <= 0:
        return cap
    running = 0.0
    monthly = (1.0 + apr) ** (1.0 / 12.0) - 1.0
    for m in range(1, cap + 1):
        running = running * (1.0 + monthly) + per_month
        if running >= balance:
            return m
    return cap


def where_to_put(session: Session, today: date, amount: float, *, years: int | None = None, scenario: str = "mid") -> dict:
    a = assumptions(session)
    per_year = paychecks_per_year(session)
    amount = max(0.0, round(float(amount), 2))
    horizon = int(years) if years else (int(a["years_to_retirement"]) if a.get("years_to_retirement") else 10)
    horizon = max(1, min(horizon, 60))
    months = horizon * 12
    scenario = scenario if scenario in a["returns"] else "mid"
    rate = float(a["returns"][scenario])
    tax = float(a.get("marginal_tax_rate") or 0.0)
    gains = float(a.get("capital_gains_rate") or 0.0)
    match_rate = float(a.get("employer_match_rate") or 0.0)
    match_cap = float(a.get("employer_match_cap_per_paycheck") or 0.0)
    roth_limit = float(a.get("roth_limit_per_year") or 0.0)
    per_month = amount * per_year / 12.0
    contributed = amount * per_year * horizon
    accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()  # noqa: E712
    has_401k = any(x.subtype in ("401k", "403b", "457b", "tsp") for x in accounts)
    has_roth = any(x.subtype in ("roth_ira", "roth") for x in accounts)
    has_hsa = any(x.subtype == "hsa" for x in accounts)
    has_brokerage = any(x.subtype in ("brokerage", "other") for x in accounts)
    options: list[dict] = []

    savings_value = grow(0.0, float(a["savings_apy"]), per_month, months)[-1]
    savings_months = grow(0.0, float(a["savings_apy"]), per_month, 36)
    options.append({
        "id": "savings", "name": DESTINATION_WORDS["savings"], "in_per_paycheck": amount, "match_per_paycheck": 0.0, "rate": float(a["savings_apy"]),
        "value": _money(savings_value), "contributed": _money(contributed), "after_tax": _money(savings_value - (savings_value - contributed) * tax),
        "guaranteed": True, "reachable": "any time", "tax": "interest taxed each year at your rate",
        "months": [{"month": month_label(today, i), "balance": _money(v)} for i, v in enumerate(savings_months)],
        "action": "Set a savings goal at this pace", "link": "/today#goals", "exists": True, "note": "",
    })

    pretax_in = amount / (1.0 - tax) if tax < 1 else amount
    match = match_rate * (min(pretax_in, match_cap) if match_cap > 0 else pretax_in)
    k_value = grow(0.0, rate, (pretax_in + match) * per_year / 12.0, months)[-1]
    options.append({
        "id": "k401_pretax", "name": DESTINATION_WORDS["k401_pretax"], "in_per_paycheck": _money(pretax_in), "match_per_paycheck": _money(match), "rate": rate,
        "value": _money(k_value), "contributed": _money((pretax_in + match) * per_year * horizon), "after_tax": _money(k_value * (1.0 - tax)),
        "guaranteed": False, "reachable": "at retirement (a penalty before 59 and a half)", "tax": "untaxed going in, taxed coming out at your rate",
        "action": "Change the election with payroll; the paystub shows it" if has_401k else "Add your 401(k)", "link": "/paystubs" if has_401k else "/investments", "exists": has_401k,
        "note": f"the same take-home cost buys {money_word(pretax_in)} of contribution before tax" + (f", and the employer adds {money_word(match)}" if match > 0 else ""),
    })

    roth_value = grow(0.0, rate, per_month, months)[-1]
    over = roth_limit > 0 and amount * per_year > roth_limit
    options.append({
        "id": "roth_ira", "name": DESTINATION_WORDS["roth_ira"], "in_per_paycheck": amount, "match_per_paycheck": 0.0, "rate": rate,
        "value": _money(roth_value), "contributed": _money(contributed), "after_tax": _money(roth_value),
        "guaranteed": False, "reachable": "contributions any time, growth at retirement", "tax": "taxed going in, untaxed coming out",
        "action": "Raise the transfer at the vendor" if has_roth else "Sign in at the vendor, or add it by hand", "link": "/investments", "exists": has_roth,
        "note": f"over the yearly limit of {money_word(roth_limit)}" if over else "",
    })

    options.append({
        "id": "brokerage", "name": DESTINATION_WORDS["brokerage"], "in_per_paycheck": amount, "match_per_paycheck": 0.0, "rate": rate,
        "value": _money(roth_value), "contributed": _money(contributed), "after_tax": _money(roth_value - (roth_value - contributed) * gains),
        "guaranteed": False, "reachable": "any time, by selling", "tax": "gains taxed when sold",
        "action": "Raise the transfer at the vendor" if has_brokerage else "Sign in at the vendor, or add it by hand", "link": "/investments", "exists": has_brokerage, "note": "",
    })

    if has_hsa:
        hsa_value = grow(0.0, rate, pretax_in * per_year / 12.0, months)[-1]
        options.append({
            "id": "hsa", "name": DESTINATION_WORDS["hsa"], "in_per_paycheck": _money(pretax_in), "match_per_paycheck": 0.0, "rate": rate,
            "value": _money(hsa_value), "contributed": _money(pretax_in * per_year * horizon), "after_tax": _money(hsa_value),
            "guaranteed": False, "reachable": "any time for medical costs", "tax": "untaxed going in and, for medical costs, coming out",
            "action": "Change the election with payroll", "link": "/paystubs", "exists": True, "note": "the yearly limit applies",
        })

    for d in session.exec(select(Debt).where(Debt.active == True)).all():  # noqa: E712
        if d.apr <= 0 or d.balance <= 0:
            continue
        apr = d.apr / 100.0
        payoff = months_to_retire(d.balance, apr, per_month, months)
        saved = grow(0.0, apr, per_month, payoff)[-1]
        after = grow(saved, rate, per_month, months - payoff)[-1] if months > payoff else saved
        word = SCENARIO_WORDS.get(scenario, "middle")
        options.append({
            "id": f"debt:{d.id}", "name": f"Pay down {d.name}", "in_per_paycheck": amount, "match_per_paycheck": 0.0, "rate": apr,
            "value": _money(after), "contributed": _money(contributed), "after_tax": _money(after),
            "guaranteed": True, "reachable": "the freed payment, once it is gone", "tax": "avoided interest is never taxed",
            "action": "See the debts", "link": "/debts", "exists": True, "source": f"debt:{d.id}",
            "note": f"earns its {d.apr:g}% APR, guaranteed, for about {payoff} months until it is gone; the same money then goes at the {word} return" if payoff < months else f"earns its {d.apr:g}% APR, guaranteed, the whole way",
        })

    options.sort(key=lambda o: -o["after_tax"])
    for i, o in enumerate(options, start=1):
        o["rank"] = i
    best = options[0] if options else None
    runner = options[1] if len(options) > 1 else None
    summary = ""
    if best:
        summary = f"{money_word(amount)} a paycheck for {horizon} years, at the {SCENARIO_WORDS.get(scenario, 'middle')} return of {rate:.0%}: {best['name']} comes to {money_word(best['after_tax'])} kept"
        if runner:
            summary += f", {money_word(best['after_tax'] - runner['after_tax'])} more than {runner['name']}"
        summary += ". " + ("Guaranteed." if best["guaranteed"] else "Not guaranteed.")
    return {
        "as_of": today.isoformat(), "amount_per_paycheck": amount, "years": horizon, "scenario": scenario, "rate": rate, "paychecks_per_year": per_year,
        "assumptions": {"marginal_tax_rate": tax, "capital_gains_rate": gains, "employer_match_rate": match_rate, "employer_match_cap_per_paycheck": match_cap, "roth_limit_per_year": roth_limit, "savings_apy": float(a["savings_apy"])},
        "options": options, "best": best["id"] if best else None, "summary": summary,
        "sources": ["setting:projections", *[o["source"] for o in options if o.get("source")], *[f"account:{x.id}" for x in accounts]],
        "not_advice": NOT_ADVICE + " Where the money is reachable and what it is taxed at matter as much as the total; this is the arithmetic, not a recommendation.",
    }
