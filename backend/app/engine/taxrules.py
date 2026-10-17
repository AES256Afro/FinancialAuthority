"""The tax rules the business and tax screens use (M56 to M60), each with where it comes from and the day it was
checked. General rules as the IRS and the state publish them, never advice for one person: the screens say what a
rule is and what the owner's records show, and a tax preparer decides with the owner. Rules change; a rule whose
check is more than a year old says so.

Every figure here is from an official page or the law as summarized by it; each has its source."""

from __future__ import annotations

from datetime import date
from typing import Any

CHECKED = "2026-10-17"
NOT_ADVICE = ("General rules as the IRS and your state publish them, with what your own records show. Not tax advice: "
              "a tax preparer (a CPA or an enrolled agent) decides with you what to claim.")

SOURCES = {
    "irs_ein": ("Get an employer identification number (free, from the IRS)", "https://www.irs.gov/businesses/small-businesses-self-employed/get-an-employer-identification-number"),
    "irs_sole_prop": ("Sole proprietorships", "https://www.irs.gov/businesses/small-businesses-self-employed/sole-proprietorships"),
    "irs_sch_c": ("Schedule C (Form 1040), Profit or Loss From Business", "https://www.irs.gov/forms-pubs/about-schedule-c-form-1040"),
    "irs_pub_334": ("Publication 334, Tax Guide for Small Business", "https://www.irs.gov/publications/p334"),
    "irs_pub_535": ("Guide to business expense resources (in place of the retired Publication 535)", "https://www.irs.gov/forms-pubs/guide-to-business-expense-resources"),
    "irs_pub_946": ("Publication 946, How To Depreciate Property", "https://www.irs.gov/publications/p946"),
    "irs_pub_463": ("Publication 463, Travel, Gift, and Car Expenses", "https://www.irs.gov/publications/p463"),
    "irs_pub_587": ("Publication 587, Business Use of Your Home", "https://www.irs.gov/publications/p587"),
    "irs_se_tax": ("Self-employment tax (Social Security and Medicare)", "https://www.irs.gov/businesses/small-businesses-self-employed/self-employment-tax-social-security-and-medicare-taxes"),
    "irs_estimated": ("Estimated taxes", "https://www.irs.gov/businesses/small-businesses-self-employed/estimated-taxes"),
    "irs_mileage": ("Standard mileage rates", "https://www.irs.gov/tax-professionals/standard-mileage-rates"),
    "irs_1099nec": ("About Form 1099-NEC", "https://www.irs.gov/forms-pubs/about-form-1099-nec"),
    "irs_car_interest": ("Treasury, IRS guidance on the deduction for car loan interest (IR-2025-129)", "https://www.irs.gov/node/153796"),
    "irs_1040x": ("File an amended return", "https://www.irs.gov/filing/file-an-amended-return"),
    "irs_records": ("How long should I keep records?", "https://www.irs.gov/businesses/small-businesses-self-employed/how-long-should-i-keep-records"),
    "irs_hobby": ("Is it a hobby or a business?", "https://www.irs.gov/newsroom/heres-how-to-tell-the-difference-between-a-hobby-and-a-business-for-tax-purposes"),
    "irs_moving": ("Moving expenses (Form 3903 is for the armed forces)", "https://www.irs.gov/forms-pubs/about-form-3903"),
    "irs_std_deduction": ("Inflation-adjusted tax items by tax year (Rev. Proc. 2025-32 for 2026)", "https://www.irs.gov/newsroom/inflation-adjusted-tax-items-by-tax-year"),
    "tx_sos_llc": ("Texas Secretary of State: forming a limited liability company (Form 205)", "https://www.sos.state.tx.us/corp/forms_boc.shtml"),
    "tx_sosdirect": ("SOSDirect, the Texas online filing system", "https://direct.sos.state.tx.us/"),
    "tx_assumed_name": ("Texas Secretary of State: name filings, including assumed names (DBA)", "https://www.sos.state.tx.us/corp/namefilingsfaqs.shtml"),
    "tx_structure": ("Texas Secretary of State: selecting a business structure", "https://www.sos.state.tx.us/corp/businessstructure.shtml"),
    "tx_franchise": ("Texas Comptroller: franchise tax", "https://comptroller.texas.gov/taxes/franchise/"),
}


def source(key: str) -> dict[str, str]:
    title, url = SOURCES[key]
    return {"title": title, "url": url, "checked": CHECKED}


# The standard mileage rate for business use, by the day driven (the rate is the one in effect that day).
MILEAGE = [  # (from, to, cents per mile)
    ("2025-01-01", "2025-12-31", 70.0),
    ("2026-01-01", "2026-06-30", 72.5),
    ("2026-07-01", "2026-12-31", 76.0),
]


def mileage_rate(day: date) -> float | None:
    """Cents per business mile on the day driven, or None for a day this table does not cover yet."""
    d = day.isoformat()
    for start, end, cents in MILEAGE:
        if start <= d <= end:
            return cents
    return None


def form_1099_nec_threshold(year: int) -> float:
    """Payments to one person in a year at or above which the payer files a 1099-NEC: $600 through 2025, $2,000 for
    payments from 2026 (indexed for inflation from 2027 on; this table says $2,000 until the new figure is known)."""
    return 600.0 if year <= 2025 else 2000.0


STANDARD_DEDUCTION = {2025: {"single": 15750.0, "married_joint": 31500.0, "head_of_household": 23625.0},
                      2026: {"single": 16100.0, "married_joint": 32200.0, "head_of_household": 24150.0}}
SE_TAX_RATE = 0.153  # 12.4% Social Security (up to the year's wage base) plus 2.9% Medicare
SE_EARNINGS_SHARE = 0.9235  # self-employment tax applies to 92.35% of net earnings
SE_FLOOR = 400.0  # net earnings from self-employment at which the tax and Schedule SE apply
ESTIMATED_TAX_FLOOR = 1000.0  # expected tax owed at which quarterly estimates are generally due
STARTUP_DEDUCTION = 5000.0  # start-up costs deductible in the year the business begins, reduced past $50,000
STARTUP_PHASEOUT = 50000.0
DE_MINIMIS_PER_ITEM = 2500.0  # de minimis safe harbor per invoice or item, for a business without audited statements
HOME_OFFICE_SIMPLE = (5.0, 300)  # simplified method: $5 a square foot, up to 300 square feet
CAR_INTEREST_CAP = 10000.0  # interest a year on a loan for a new, US-assembled vehicle for personal use (2025 to 2028)
MEALS_SHARE = 0.5


def estimated_tax_dates(year: int) -> list[dict[str, str]]:
    """The four estimated tax payments for a tax year (a date that falls on a weekend moves to the next business day;
    the IRS calendar has the exact day)."""
    return [{"for": "January to March", "due": f"{year}-04-15"}, {"for": "April and May", "due": f"{year}-06-15"},
            {"for": "June to August", "due": f"{year}-09-15"}, {"for": "September to December", "due": f"{year + 1}-01-15"}]


# The lines of Schedule C a business expense goes on, in plain words, with what usually belongs on each.
TAX_LINES: dict[str, dict[str, Any]] = {
    "advertising": {"line": "8", "words": "Advertising", "examples": "ads, a website's promotion, business cards"},
    "car": {"line": "9", "words": "Car and truck", "examples": "business miles at the standard rate, or the business share of actual costs; never the commute", "source": "irs_pub_463"},
    "contract_labor": {"line": "11", "words": "Contract labor", "examples": "people paid for work on the business"},
    "depreciation": {"line": "13", "words": "Depreciation and section 179", "examples": "computers, monitors, equipment that lasts more than a year", "source": "irs_pub_946"},
    "insurance": {"line": "15", "words": "Insurance", "examples": "business insurance (not health insurance, which goes elsewhere)"},
    "interest": {"line": "16", "words": "Interest", "examples": "interest on money borrowed for the business"},
    "legal": {"line": "17", "words": "Legal and professional services", "examples": "a tax preparer for the business, a lawyer, formation fees"},
    "office": {"line": "18", "words": "Office expense", "examples": "postage, printer ink, small office items"},
    "rent": {"line": "20", "words": "Rent or lease", "examples": "equipment or space rented for the business"},
    "repairs": {"line": "21", "words": "Repairs and maintenance", "examples": "fixing business equipment"},
    "supplies": {"line": "22", "words": "Supplies", "examples": "materials used up in the work"},
    "taxes_licenses": {"line": "23", "words": "Taxes and licenses", "examples": "state filing fees, business licenses"},
    "travel": {"line": "24a", "words": "Travel", "examples": "travel away from home overnight for the business"},
    "meals": {"line": "24b", "words": "Meals (half counts)", "examples": "business meals with a client; 50% counts"},
    "utilities": {"line": "25", "words": "Utilities", "examples": "the business share of phone and internet"},
    "software": {"line": "27a", "words": "Other: software and online services", "examples": "subscriptions used for the business (Claude, cloud hosting, design tools)"},
    "education": {"line": "27a", "words": "Other: education for the business", "examples": "courses that keep or improve skills used in the business"},
    "home_office": {"line": "30", "words": "Business use of home", "examples": "a space used only and regularly for the business", "source": "irs_pub_587"},
}

# What never counts, said plainly, so the owner is not misled by hope.
NEVER = [
    {"what": "Commuting", "why": "Driving between home and a regular workplace is personal, even for a business owner.", "source": "irs_pub_463"},
    {"what": "Moving to a new home", "why": "The moving deduction is permanently gone except for the armed forces (and, from 2026, some intelligence community employees).", "source": "irs_moving"},
    {"what": "Personal spending before a business existed", "why": "A cost is a business expense only for a business you are carrying on; start-up costs count only once the business begins.", "source": "irs_pub_535"},
    {"what": "Your own time", "why": "A sole proprietor cannot pay themselves a deductible wage; the profit is the pay.", "source": "irs_sole_prop"},
    {"what": "A hobby's costs", "why": "Activity without a profit motive is a hobby; its costs are not deductible.", "source": "irs_hobby"},
]
