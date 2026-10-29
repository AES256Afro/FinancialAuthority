"""Where should $X a paycheck go, or a share of it? The same money sent to each place the owner could send
it, carried to the horizon under the owner's assumptions: what each comes to at every milestone year, what is
kept after tax, whether it is guaranteed and reachable, and the step that starts it. A percentage is taken
from the paycheck on file: the usual take-home (a semi-monthly paycheck's two checks averaged) or the gross
from the last stubs, in which case a pre-tax account takes the whole share before tax. Arithmetic with named
assumptions, ranked by what is kept; the page and the models say it is not a recommendation."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from ..models import Account, Debt, Income, Paystub
from .cadence import payday_position
from .projections import NOT_ADVICE, SCENARIO_WORDS, assumptions, grow, month_label, paychecks_per_year

MILESTONE_YEARS = (1, 3, 5, 10, 15, 20, 25, 30)
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


def paycheck_basis(session: Session, basis: str = "net") -> dict:
    """What one paycheck is, for a percentage of it: the usual take-home on file (a semi-monthly paycheck's two
    checks averaged), or the gross from the last stubs. Says where the figure came from."""
    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    stubs = session.exec(select(Paystub).order_by(Paystub.pay_date.desc()).limit(6)).all()
    semimonthly = income is not None and income.cadence == "semimonthly"

    def by_position(values: list[tuple[date, float]]) -> float | None:
        if not values:
            return None
        if semimonthly:
            mids = [v for d, v in values if payday_position(d) == "mid"]
            ends = [v for d, v in values if payday_position(d) == "end"]
            if mids and ends:
                return (sum(mids) / len(mids) + sum(ends) / len(ends)) / 2
        return sum(v for _, v in values) / len(values)

    prefix = ""
    if basis == "gross":
        grosses = [(p.pay_date, float(p.gross)) for p in stubs if p.gross]
        value = by_position(grosses)
        if value is not None:
            return {"kind": "gross", "per_paycheck": _money(value), "source": "paystubs", "sources": [f"paystub:{p.id}" for p in stubs if p.gross], "note": f"gross pay from the last {len(grosses)} stub{'s' if len(grosses) != 1 else ''}"}
        prefix = "no gross on a stub yet, so take-home stands in; "
    if income is not None:
        nets = [float(income.expected_net)] + ([float(income.expected_net_end)] if semimonthly and income.expected_net_end is not None else [])
        return {"kind": "net", "per_paycheck": _money(sum(nets) / len(nets)), "source": f"income:{income.id}", "sources": [f"income:{income.id}"], "note": prefix + ("the two checks' usual nets, averaged" if len(nets) == 2 else "the usual net on file")}
    nets = [(p.pay_date, float(p.net)) for p in stubs]
    value = by_position(nets)
    if value is not None:
        return {"kind": "net", "per_paycheck": _money(value), "source": "paystubs", "sources": [f"paystub:{p.id}" for p in stubs], "note": prefix + f"net pay from the last {len(nets)} stub{'s' if len(nets) != 1 else ''}"}
    return {"kind": "net", "per_paycheck": None, "source": None, "sources": [], "note": "no paycheck on file; name it on Needs you or confirm a paystub"}


def where_to_put(session: Session, today: date, amount: float, *, years: int | None = None, scenario: str = "mid", percent: float | None = None, basis: str = "net") -> dict:
    a = assumptions(session)
    per_year = paychecks_per_year(session)
    basis_info = None
    if percent is not None:
        percent = max(0.0, min(float(percent), 100.0))
        basis_info = paycheck_basis(session, basis if basis in ("net", "gross") else "net")
        amount = round(percent / 100.0 * basis_info["per_paycheck"], 2) if basis_info["per_paycheck"] else 0.0
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
    gross_basis = basis_info is not None and basis_info["kind"] == "gross"
    # A share of gross goes into a pre-tax account whole; after tax it is smaller. Dollars of take-home, or a
    # share of take-home, buy more of a pre-tax account than they cost.
    take_home = round(amount * (1.0 - tax), 2) if gross_basis else amount
    pretax_in = amount if gross_basis else (amount / (1.0 - tax) if tax < 1 else amount)
    per_month = take_home * per_year / 12.0
    contributed = take_home * per_year * horizon
    accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()  # noqa: E712
    has_401k = any(x.subtype in ("401k", "403b", "457b", "tsp") for x in accounts)
    has_roth = any(x.subtype in ("roth_ira", "roth") for x in accounts)
    has_hsa = any(x.subtype == "hsa" for x in accounts)
    has_brokerage = any(x.subtype in ("brokerage", "other") for x in accounts)
    marks = sorted({y * 12 for y in MILESTONE_YEARS if y <= horizon} | {months})

    def milestones(value_at) -> list[dict]:  # noqa: ANN001
        return [{"year": m // 12, "after_tax": _money(value_at(m))} for m in marks]

    options: list[dict] = []

    apy = float(a["savings_apy"])

    def savings_at(m: int) -> float:
        v = grow(0.0, apy, per_month, m)[-1]
        return v - (v - per_month * m) * tax

    savings_value = grow(0.0, apy, per_month, months)[-1]
    savings_months = grow(0.0, apy, per_month, 36)
    options.append({
        "id": "savings", "name": DESTINATION_WORDS["savings"], "in_per_paycheck": take_home, "match_per_paycheck": 0.0, "rate": apy,
        "value": _money(savings_value), "contributed": _money(contributed), "after_tax": _money(savings_at(months)),
        "guaranteed": True, "reachable": "any time", "tax": "interest taxed each year at your rate",
        "months": [{"month": month_label(today, i), "balance": _money(v)} for i, v in enumerate(savings_months)],
        "action": "Set a savings goal at this pace", "link": "/today#goals", "exists": True, "note": "", "milestones": milestones(savings_at),
    })

    match = match_rate * (min(pretax_in, match_cap) if match_cap > 0 else pretax_in)
    k_month = (pretax_in + match) * per_year / 12.0

    def k_at(m: int) -> float:
        return grow(0.0, rate, k_month, m)[-1] * (1.0 - tax)

    k_value = grow(0.0, rate, k_month, months)[-1]
    k_note = (f"the share of gross goes in whole, before tax; the take-home cost is {money_word(take_home)}" if gross_basis else f"the same take-home cost buys {money_word(pretax_in)} of contribution before tax") + (f", and the employer adds {money_word(match)}" if match > 0 else "")
    options.append({
        "id": "k401_pretax", "name": DESTINATION_WORDS["k401_pretax"], "in_per_paycheck": _money(pretax_in), "match_per_paycheck": _money(match), "rate": rate,
        "value": _money(k_value), "contributed": _money((pretax_in + match) * per_year * horizon), "after_tax": _money(k_at(months)),
        "guaranteed": False, "reachable": "at retirement (a penalty before 59 and a half)", "tax": "untaxed going in, taxed coming out at your rate",
        "action": "Change the election with payroll; the paystub shows it" if has_401k else "Add your 401(k)", "link": "/paystubs" if has_401k else "/investments", "exists": has_401k,
        "note": k_note, "milestones": milestones(k_at),
    })

    def roth_at(m: int) -> float:
        return grow(0.0, rate, per_month, m)[-1]

    roth_value = roth_at(months)
    over = roth_limit > 0 and take_home * per_year > roth_limit
    options.append({
        "id": "roth_ira", "name": DESTINATION_WORDS["roth_ira"], "in_per_paycheck": take_home, "match_per_paycheck": 0.0, "rate": rate,
        "value": _money(roth_value), "contributed": _money(contributed), "after_tax": _money(roth_value),
        "guaranteed": False, "reachable": "contributions any time, growth at retirement", "tax": "taxed going in, untaxed coming out",
        "action": "Raise the transfer at the vendor" if has_roth else "Sign in at the vendor, or add it by hand", "link": "/investments", "exists": has_roth,
        "note": f"over the yearly limit of {money_word(roth_limit)}" if over else "", "milestones": milestones(roth_at),
    })

    def brokerage_at(m: int) -> float:
        v = grow(0.0, rate, per_month, m)[-1]
        return v - (v - per_month * m) * gains

    options.append({
        "id": "brokerage", "name": DESTINATION_WORDS["brokerage"], "in_per_paycheck": take_home, "match_per_paycheck": 0.0, "rate": rate,
        "value": _money(roth_value), "contributed": _money(contributed), "after_tax": _money(brokerage_at(months)),
        "guaranteed": False, "reachable": "any time, by selling", "tax": "gains taxed when sold",
        "action": "Raise the transfer at the vendor" if has_brokerage else "Sign in at the vendor, or add it by hand", "link": "/investments", "exists": has_brokerage, "note": "",
        "milestones": milestones(brokerage_at),
    })

    if has_hsa:
        hsa_month = pretax_in * per_year / 12.0

        def hsa_at(m: int) -> float:
            return grow(0.0, rate, hsa_month, m)[-1]

        options.append({
            "id": "hsa", "name": DESTINATION_WORDS["hsa"], "in_per_paycheck": _money(pretax_in), "match_per_paycheck": 0.0, "rate": rate,
            "value": _money(hsa_at(months)), "contributed": _money(pretax_in * per_year * horizon), "after_tax": _money(hsa_at(months)),
            "guaranteed": False, "reachable": "any time for medical costs", "tax": "untaxed going in and, for medical costs, coming out",
            "action": "Change the election with payroll", "link": "/paystubs", "exists": True, "note": "the yearly limit applies", "milestones": milestones(hsa_at),
        })

    for d in session.exec(select(Debt).where(Debt.active == True)).all():  # noqa: E712
        if d.apr <= 0 or d.balance <= 0:
            continue
        apr = d.apr / 100.0

        def debt_at(m: int, balance: float = float(d.balance), apr: float = apr) -> float:
            payoff = months_to_retire(balance, apr, per_month, m)
            saved = grow(0.0, apr, per_month, payoff)[-1]
            return grow(saved, rate, per_month, m - payoff)[-1] if m > payoff else saved

        payoff = months_to_retire(d.balance, apr, per_month, months)
        after = debt_at(months)
        word = SCENARIO_WORDS.get(scenario, "middle")
        options.append({
            "id": f"debt:{d.id}", "name": f"Pay down {d.name}", "in_per_paycheck": take_home, "match_per_paycheck": 0.0, "rate": apr,
            "value": _money(after), "contributed": _money(contributed), "after_tax": _money(after),
            "guaranteed": True, "reachable": "once it is gone, from the payment it frees", "tax": "avoided interest is never taxed",
            "action": "See the debts", "link": "/debts", "exists": True, "source": f"debt:{d.id}",
            "note": f"earns its {d.apr:g}% APR, guaranteed, for about {payoff} months until it is gone; the same money then goes at the {word} return" if payoff < months else f"earns its {d.apr:g}% APR, guaranteed, the whole way",
            "milestones": milestones(debt_at),
        })

    options.sort(key=lambda o: -o["after_tax"])
    for i, o in enumerate(options, start=1):
        o["rank"] = i
    best = options[0] if options else None
    runner = options[1] if len(options) > 1 else None
    summary = ""
    if percent is not None and basis_info and basis_info["per_paycheck"] is None:
        summary = "No paycheck is on file yet, so a share of it is nothing; name the paycheck on Needs you or confirm a paystub."
    elif best:
        lead = f"{percent:g}% of your {'gross' if gross_basis else 'take-home'} pay is {money_word(amount)} a paycheck; for {horizon} years" if percent is not None else f"{money_word(amount)} a paycheck for {horizon} years"
        summary = f"{lead}, at the {SCENARIO_WORDS.get(scenario, 'middle')} return of {rate:.0%}: {best['name']} comes to {money_word(best['after_tax'])} kept"
        if runner:
            summary += f", {money_word(best['after_tax'] - runner['after_tax'])} more than {runner['name']}"
        summary += ". " + ("Guaranteed." if best["guaranteed"] else "Not guaranteed.")
    return {
        "as_of": today.isoformat(), "amount_per_paycheck": amount, "take_home_per_paycheck": take_home, "percent": percent, "basis": basis_info, "years": horizon, "scenario": scenario, "rate": rate, "paychecks_per_year": per_year,
        "milestone_years": [m // 12 for m in marks],
        "assumptions": {"marginal_tax_rate": tax, "capital_gains_rate": gains, "employer_match_rate": match_rate, "employer_match_cap_per_paycheck": match_cap, "roth_limit_per_year": roth_limit, "savings_apy": apy},
        "options": options, "best": best["id"] if best else None, "summary": summary,
        "sources": ["setting:projections", *(basis_info["sources"] if basis_info else []), *[o["source"] for o in options if o.get("source")], *[f"account:{x.id}" for x in accounts]],
        "not_advice": NOT_ADVICE + " Where the money is reachable and what it is taxed at matter as much as the total; this is the arithmetic, not a recommendation.",
    }
