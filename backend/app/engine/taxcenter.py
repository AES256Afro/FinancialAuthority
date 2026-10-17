"""The tax center (M59): a tax year prepared through the year, not in April. Its dates; each business's profit and
self-employment tax; the standard deduction beside what could be itemized; personal deductions to check, with the
questions that decide them (the new deduction for car loan interest among them); what never counts; and how far back
an amended return reaches. From the owner's records and the rules in taxrules.py, each with its source. Not advice."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..models import Bill, Business, Debt
from ..store import get_setting, set_setting
from . import taxrules as R
from .business import books
from .custom_reports import tax_totals

ANSWERS = "taxes.answers"
ITEMIZABLE = ("Charitable Donations", "Charity", "Taxes", "Medical", "Health & Wellness")  # what Schedule A can take (medical only past 7.5% of income)
CAR_LOAN = re.compile(r"\b(car|auto|vehicle|truck)\b.*\b(loan|finance|financing|payment)\b|\b(loan|finance)\b.*\b(car|auto|vehicle|truck)\b", re.I)
CAR_QUESTIONS = [
    ("car_new", "Was the car new when you bought it (not used, not leased)?"),
    ("car_after_2024", "Did you take out the loan after December 31, 2024?"),
    ("car_us_assembly", "Was its final assembly in the United States? (The window sticker, or the NHTSA VIN decoder, says.)"),
    ("car_personal", "Is it mostly for personal use, under 14,000 pounds, with the loan secured by the car?"),
]


def save_answers(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    keep = dict(get_setting(session, ANSWERS, {}) or {})
    for key, value in values.items():
        if key not in {k for k, _ in CAR_QUESTIONS} | {"filing_status"}:
            raise ValueError(f"Not a question the app asks: {key}")
        if key == "filing_status" and value not in R.STANDARD_DEDUCTION[2026]:
            raise ValueError("The filing status is single, married_joint or head_of_household")
        keep[key] = value
    set_setting(session, ANSWERS, keep)
    return keep


def tax_year(session: Session, today: date, year: int | None = None) -> dict[str, Any]:
    year = year or today.year
    answers = get_setting(session, ANSWERS, {}) or {}
    status = answers.get("filing_status", "single")
    businesses = session.exec(select(Business).where(Business.active == True)).all()  # noqa: E712
    biz = [books(session, b, year) for b in businesses]
    profit = sum(b["profit"] for b in biz)
    se = sum(b["se_tax"] for b in biz)

    dates = [{"due": f"{year + 1}-01-31", "what": "Anyone who paid you $2,000 or more for work (from 2026) sends you a 1099-NEC" if year >= 2026 else "1099-NEC forms arrive for work paid $600 or more"},
             {"due": f"{year + 1}-04-15", "what": f"Your {year} return is due (or ask for more time to file; the tax is still due)"}]
    if businesses:
        dates += [{"due": d["due"], "what": f"Estimated tax for {d['for']}, once you expect to owe $1,000 or more"} for d in R.estimated_tax_dates(year)]
    dates.sort(key=lambda d: d["due"])
    upcoming = [d for d in dates if d["due"] >= today.isoformat()]

    standard = (R.STANDARD_DEDUCTION.get(year) or R.STANDARD_DEDUCTION[max(R.STANDARD_DEDUCTION)]).get(status)
    totals = tax_totals(session, today, year)
    itemizable = round(sum(c["total"] for c in totals["categories"] if c["name"] in ITEMIZABLE), 2)
    categories = [{**c, "itemizable": c["name"] in ITEMIZABLE} for c in totals["categories"]]

    car_answers = {k: answers.get(k) for k, _ in CAR_QUESTIONS}
    car_loans = [b.name for b in session.exec(select(Bill).where(Bill.active == True)).all() if CAR_LOAN.search(b.name)]  # noqa: E712
    car_loans += [d.name for d in session.exec(select(Debt).where(Debt.active == True)).all() if d.kind == "loan" and re.search(r"\b(car|auto|vehicle|truck)\b", d.name, re.I)]  # noqa: E712
    if all(car_answers.values()):
        car_state, car_word = "likely", f"It looks like it qualifies: interest paid on the loan, up to $10,000 a year (2025 to 2028), without itemizing. Your lender reports the interest; the deduction shrinks at higher incomes."
    elif any(v is False for v in car_answers.values()):
        car_state, car_word = "no", "From your answers, this loan does not qualify."
    else:
        car_state, car_word = "ask", "Answer the four questions to see whether your car loan's interest may qualify."
    checks = [{"key": "car_interest", "title": "Interest on a new car loan", "state": car_state, "words": car_word, "questions": [{"key": k, "text": t, "answer": car_answers[k]} for k, t in CAR_QUESTIONS],
               "loans_seen": car_loans[:5], "source": R.source("irs_car_interest")}]

    said = []
    if businesses:
        said.append(f"Your {'business shows' if len(businesses) == 1 else 'businesses show'} ${profit:,.2f} of profit in {year} so far" + (f", with about ${se:,.2f} of self-employment tax." if se else "."))
    said.append(f"The {year} standard deduction for your filing status is ${standard:,.2f}; the costs that could be itemized come to ${itemizable:,.2f}, so the standard deduction is {'larger' if standard >= itemizable else 'smaller'}." if standard else "")
    if upcoming:
        d = date.fromisoformat(upcoming[0]["due"])
        said.append(f"Next: {d:%A, %B} {d.day}, {d.year}. {upcoming[0]['what']}.")
    return {"year": year, "filing_status": status, "dates": dates, "upcoming": upcoming[:4], "businesses": biz, "profit": round(profit, 2), "se_tax": round(se, 2),
            "standard_deduction": standard, "itemizable": itemizable, "itemizable_categories": categories, "checks": checks,
            "never": [{**n, "source": R.source(n["source"])} for n in R.NEVER],
            "amend": {"years": [year - 3, year - 2, year - 1], "words": "A return can generally be amended within three years of filing it (or two years of paying the tax), with Form 1040-X. Business costs count only for years the business existed.",
                      "source": R.source("irs_1040x")},
            "said": " ".join(s for s in said if s), "sources": [R.source("irs_std_deduction"), R.source("irs_estimated")], "not_advice": R.NOT_ADVICE}
