"""The outlook and its assumptions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .inputs import In
from .. import auth
from ..db import get_session
from ..engine.projections import DEFAULT_ASSUMPTIONS, assumptions, outlook
from ..engine.where_to_put import where_to_put
from ..llm.reviewer import mark_pending
from ..store import get_setting, set_setting
from .routes import today

router = APIRouter(prefix="/api/v1/projections", tags=["projections"])


class ReturnsIn(In):
    low: float | None = None
    mid: float | None = None
    high: float | None = None


class AssumptionsIn(In):
    savings_apy: float | None = None
    returns: ReturnsIn | None = None
    inflation: float | None = None
    years: list[int] | None = None
    years_to_retirement: int | None = None
    clear_retirement: bool = False
    extra_savings_per_paycheck: float | None = None
    investment_contribution_per_paycheck: float | None = None
    use_paystubs_for_contributions: bool = False
    employer_match_per_paycheck: float | None = None
    withdrawal_rate: float | None = None
    marginal_tax_rate: float | None = None
    capital_gains_rate: float | None = None
    employer_match_rate: float | None = None
    employer_match_cap_per_paycheck: float | None = None
    roth_limit_per_year: float | None = None


def _rate(value: float, name: str, top: float = 0.5) -> float:
    if not 0.0 <= value <= top:
        raise HTTPException(400, f"{name} must be between 0 and {top:.0%}, as a fraction (0.07 for 7%)")
    return float(value)


@router.get("")
def get_outlook(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return outlook(session, today())


@router.put("/assumptions")
def put_assumptions(body: AssumptionsIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    stored = {**(get_setting(session, "projections", {}) or {})}
    if body.savings_apy is not None:
        stored["savings_apy"] = _rate(body.savings_apy, "savings_apy")
    if body.returns is not None:
        returns = {**DEFAULT_ASSUMPTIONS["returns"], **(stored.get("returns") or {})}
        for key in ("low", "mid", "high"):
            value = getattr(body.returns, key)
            if value is not None:
                returns[key] = _rate(value, key)
        if not returns["low"] <= returns["mid"] <= returns["high"]:
            raise HTTPException(400, "returns must rise from low to mid to high")
        stored["returns"] = returns
    if body.inflation is not None:
        stored["inflation"] = _rate(body.inflation, "inflation", 0.3)
    if body.years is not None:
        years = sorted({int(y) for y in body.years if 1 <= int(y) <= 60})
        if not years:
            raise HTTPException(400, "years must hold at least one year between 1 and 60")
        stored["years"] = years
    if body.clear_retirement:
        stored["years_to_retirement"] = None
    elif body.years_to_retirement is not None:
        if not 1 <= body.years_to_retirement <= 60:
            raise HTTPException(400, "years_to_retirement must be between 1 and 60")
        stored["years_to_retirement"] = int(body.years_to_retirement)
    if body.extra_savings_per_paycheck is not None:
        stored["extra_savings_per_paycheck"] = max(0.0, float(body.extra_savings_per_paycheck))
    if body.use_paystubs_for_contributions:
        stored["investment_contribution_per_paycheck"] = None
    elif body.investment_contribution_per_paycheck is not None:
        stored["investment_contribution_per_paycheck"] = max(0.0, float(body.investment_contribution_per_paycheck))
    if body.employer_match_per_paycheck is not None:
        stored["employer_match_per_paycheck"] = max(0.0, float(body.employer_match_per_paycheck))
    if body.withdrawal_rate is not None:
        stored["withdrawal_rate"] = _rate(body.withdrawal_rate, "withdrawal_rate", 0.2)
    if body.marginal_tax_rate is not None:
        stored["marginal_tax_rate"] = _rate(body.marginal_tax_rate, "marginal_tax_rate", 0.6)
    if body.capital_gains_rate is not None:
        stored["capital_gains_rate"] = _rate(body.capital_gains_rate, "capital_gains_rate", 0.5)
    if body.employer_match_rate is not None:
        stored["employer_match_rate"] = _rate(body.employer_match_rate, "employer_match_rate", 2.0)
    if body.employer_match_cap_per_paycheck is not None:
        stored["employer_match_cap_per_paycheck"] = max(0.0, float(body.employer_match_cap_per_paycheck))
    if body.roth_limit_per_year is not None:
        stored["roth_limit_per_year"] = max(0.0, float(body.roth_limit_per_year))
    set_setting(session, "projections", stored)
    mark_pending(session, "outlook assumptions changed")
    return {"assumptions": assumptions(session), "outlook": outlook(session, today())}


@router.get("/where")
def get_where(amount: float | None = None, percent: float | None = None, basis: str = "net", years: int | None = None, scenario: str = "mid", session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """Dollars a paycheck (amount), or a share of the paycheck on file (percent, of take-home or gross pay)."""
    if amount is None and percent is None:
        raise HTTPException(400, "give amount (dollars a paycheck) or percent (a share of the paycheck)")
    if amount is not None and (amount < 0 or amount > 100_000):
        raise HTTPException(400, "amount must be between 0 and 100,000 a paycheck")
    if percent is not None and not 0 <= percent <= 100:
        raise HTTPException(400, "percent must be between 0 and 100")
    if basis not in ("net", "gross"):
        raise HTTPException(400, "basis is net (take-home) or gross")
    if years is not None and not 1 <= years <= 60:
        raise HTTPException(400, "years must be between 1 and 60")
    if scenario not in ("low", "mid", "high"):
        raise HTTPException(400, "scenario is low, mid or high")
    return where_to_put(session, today(), amount or 0.0, years=years, scenario=scenario, percent=percent, basis=basis)
