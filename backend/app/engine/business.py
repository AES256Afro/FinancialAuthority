"""The business (M56 to M58, M60): the bare-minimum setup walked through, the books by the lines a tax return uses,
invoices, the write-off finder and the mileage log. Worked out here, with no model, from the owner's own records
and the rules in taxrules.py, each with its source. General rules, never advice: a tax preparer decides with the
owner what to claim."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Business, Document, Invoice, Transaction, Trip
from ..store import get_setting, set_setting
from . import taxrules as R

TICKS = "business.setup.ticks"  # {step id: day ticked}
PERSONAL = "business.finder.personal"  # transaction ids the owner said were personal: never suggested again


def _money(v: float) -> float:
    return round(float(v), 2)


# ---- the setup guide (M56) -----------------------------------------------------------------------------------

def compare(state: str = "TX") -> list[dict[str, str]]:
    """Sole proprietor beside an LLC, row by row, in plain words."""
    texas = state.upper() == "TX"
    return [
        {"what": "What it is", "sole_prop": "You, doing business. Nothing to form.", "llc": "A company you own, formed with the state."},
        {"what": "To start", "sole_prop": "Free. An EIN from the IRS is free and takes minutes.", "llc": "$300 to file the Certificate of Formation in Texas, plus a registered agent." if texas else "Your state's filing fee, plus a registered agent."},
        {"what": "Each year", "sole_prop": "Nothing to the state.", "llc": "A yearly report to the Texas Comptroller (usually no tax at a small size, but the report is due)." if texas else "Your state's yearly report or fee."},
        {"what": "Federal taxes", "sole_prop": "Schedule C with your own return, plus self-employment tax.", "llc": "The same: a one-owner LLC is taxed like a sole proprietor unless you choose otherwise."},
        {"what": "If something goes wrong", "sole_prop": "Your own savings and things can answer for the business's debts.", "llc": "The company's debts stay the company's, if you keep its money separate."},
        {"what": "Best when", "sole_prop": "You are starting small: a friend paying you for services, side work.", "llc": "There is real risk (clients who could sue, a lease, employees) or the business grows."},
    ]


def guide_steps(business: Business | None, ticks: dict[str, str]) -> list[dict[str, Any]]:
    texas = (business.state if business else "TX").upper() == "TX"
    llc = bool(business and business.kind == "llc")
    steps: list[dict[str, Any]] = [
        {"id": "record", "title": "Name the business and what it does", "for": "both", "set_by": "details",
         "how": "Add it here: a name, what you do, the day it began. Costs before that day count only as start-up costs.",
         "done": business is not None, "source": R.source("irs_sole_prop")},
        {"id": "ein", "title": "Get an EIN from the IRS (free, about ten minutes)", "for": "both", "set_by": "details",
         "how": "Apply on irs.gov. You get the number at once and give it instead of your Social Security number. Never pay a website for one: the IRS gives it free. The app only notes that you have one, never the number.",
         "done": bool(business and business.ein_on_file), "source": R.source("irs_ein")},
    ]
    if llc:
        steps.append({"id": "formation", "title": "File the Certificate of Formation (Form 205)" if texas else "Form the LLC with your state", "for": "llc",
                      "how": ("On SOSDirect, $300, with a registered agent (a person or company in Texas that accepts legal papers for the LLC; it cannot be the LLC itself). "
                              "Then a yearly report to the Texas Comptroller.") if texas else "File with your state's Secretary of State and name a registered agent.",
                      "done": "formation" in ticks, "source": R.source("tx_sos_llc" if texas else "irs_sole_prop")})
    steps += [
        {"id": "assumed_name", "title": "If you use a name other than your own, file an assumed name (DBA)", "for": "both",
         "how": ("As a sole proprietor: with the county clerk where you do business, notarized, for the county's fee. An LLC files Form 503 with the Secretary of State ($25). "
                 "Not needed if you work under your own name.") if texas else "Rules differ by state; often the county clerk. Not needed under your own name.",
         "done": bool(business and business.assumed_name) or "assumed_name" in ticks, "source": R.source("tx_assumed_name" if texas else "irs_sole_prop")},
        {"id": "account", "title": "Keep the business's money apart", "for": "both",
         "how": "A checking account used only for the business, even one in your own name, keeps every cost and payment easy to show. Mark it as the business's account here.",
         "done": bool(business and business.account_id) or "account" in ticks, "source": R.source("irs_pub_334")},
        {"id": "w9", "title": "Give a W-9 to whoever pays you", "for": "both",
         "how": "They need your name and EIN. Whoever pays you $2,000 or more in a year (from 2026; $600 before) sends you a 1099-NEC by January 31. The income counts either way.",
         "done": "w9" in ticks, "source": R.source("irs_1099nec")},
        {"id": "invoices", "title": "Send invoices from here", "for": "both",
         "how": "Each with a number, the date, what you did and the amount. When the money arrives, the app matches it to the deposit.",
         "done": "invoices" in ticks, "source": R.source("irs_pub_334")},
        {"id": "receipts", "title": "Keep every receipt with its purchase", "for": "both",
         "how": "The app keeps receipts and documents with their transactions. Keep records at least three years after you file (longer in some cases).",
         "done": "receipts" in ticks, "source": R.source("irs_records")},
        {"id": "set_aside", "title": "Set money aside for taxes", "for": "both",
         "how": ("Profit is taxed twice over: self-employment tax (15.3% of 92.35% of it, once it reaches $400) and income tax at your own rate. "
                 "Once you expect to owe $1,000 or more for the year, pay it in four parts (April, June, September, January)."),
         "done": "set_aside" in ticks, "source": R.source("irs_estimated")},
    ]
    return steps


def guide(session: Session, business: Business | None) -> dict[str, Any]:
    ticks = get_setting(session, TICKS, {}) or {}
    steps = guide_steps(business, ticks)
    done = sum(1 for s in steps if s["done"])
    return {"compare": compare(business.state if business else "TX"), "steps": steps, "done": done, "total": len(steps),
            "said": (f"{done} of {len(steps)} steps done." if business else "Start with the first step: name the business and what it does."),
            "bare_minimum": "To be paid for services by a friend, the bare minimum is to work as a sole proprietor under your own name, with a free EIN, a W-9 for whoever pays you, and the money kept apart. An LLC adds protection, and a fee.",
            "not_advice": R.NOT_ADVICE}


SET_BY_DETAILS = {"record", "ein"}  # done by the business's own details, never by a tick


def tick(session: Session, step: str, done: bool, today: date) -> dict[str, str]:
    if step in SET_BY_DETAILS:
        raise ValueError("That step is done by the business's own details (its record, and the box for an EIN on file)")
    ticks = dict(get_setting(session, TICKS, {}) or {})
    if done:
        ticks[step] = today.isoformat()
    else:
        ticks.pop(step, None)
    set_setting(session, TICKS, ticks)
    return ticks


# ---- the books (M57) -----------------------------------------------------------------------------------------

def books(session: Session, business: Business, year: int) -> dict[str, Any]:
    """The year by the lines of Schedule C: income (invoices paid, deposits marked as the business's), costs by line
    (purchases marked as the business's, at their business share; invoices received and paid), car miles at the
    rate for each day, and the home office by the simple method. Then the profit, and self-employment tax on it."""
    first, last = date(year, 1, 1), date(year, 12, 31)
    rows = session.exec(select(Transaction).where(Transaction.business_id == business.id, Transaction.date >= first, Transaction.date <= last, Transaction.is_ignored == False)).all()  # noqa: E712
    income_rows = [t for t in rows if t.amount < 0]
    cost_rows = [t for t in rows if t.amount > 0]
    invoices = session.exec(select(Invoice).where(Invoice.business_id == business.id, Invoice.status == "paid")).all()
    out_paid = [i for i in invoices if i.direction == "out" and i.paid_on and first <= i.paid_on <= last]
    in_paid = [i for i in invoices if i.direction == "in" and i.paid_on and first <= i.paid_on <= last]
    matched = {i.transaction_id for i in invoices if i.transaction_id}

    income = _money(sum(i.amount for i in out_paid) + sum(-t.amount for t in income_rows if t.id not in matched))
    lines: dict[str, dict[str, Any]] = {}

    def add(key: str, amount: float, source: str) -> None:
        meta = R.TAX_LINES.get(key) or {"line": "27a", "words": "Other expenses"}
        row = lines.setdefault(key or "other", {"key": key or "other", "line": meta["line"], "words": meta["words"], "amount": 0.0, "count": 0, "sources": [],
                                               "rule": R.source(meta.get("source", "irs_sch_c"))})
        row["amount"] += amount
        row["count"] += 1
        row["sources"].append(source)

    for t in cost_rows:
        if t.id in matched:
            continue
        share = max(0.0, min(1.0, t.business_share))
        amount = t.amount * share * (R.MEALS_SHARE if t.tax_line == "meals" else 1.0)
        add(t.tax_line, amount, f"transaction:{t.id}")
    for i in in_paid:
        add(i.tax_line, i.amount, f"invoice:{i.id}")
    trips = session.exec(select(Trip).where(Trip.business_id == business.id, Trip.day >= first, Trip.day <= last)).all()
    miles = sum(tr.miles for tr in trips)
    car = sum(tr.miles * (R.mileage_rate(tr.day) or 0.0) / 100 for tr in trips)
    if car:
        add("car", car, f"trips:{year}")
        lines["car"]["count"] = len(trips)
    if business.home_office_sqft:
        rate, cap = R.HOME_OFFICE_SIMPLE
        add("home_office", rate * min(cap, business.home_office_sqft), f"business:{business.id}")
    for row in lines.values():
        row["amount"] = _money(row["amount"])
    costs = _money(sum(r["amount"] for r in lines.values()))
    profit = _money(income - costs)
    se_base = max(0.0, profit) * R.SE_EARNINGS_SHARE
    se_tax = _money(se_base * R.SE_TAX_RATE) if profit >= R.SE_FLOOR else 0.0

    payers: dict[str, float] = defaultdict(float)
    for i in out_paid:
        payers[i.counterparty or "(no name)"] += i.amount
    threshold = R.form_1099_nec_threshold(year)
    expect = [{"payer": k, "paid": _money(v), "expect_1099": v >= threshold} for k, v in sorted(payers.items(), key=lambda kv: -kv[1])]
    return {"year": year, "business": business.name, "income": income, "costs": costs, "profit": profit,
            "lines": sorted(lines.values(), key=lambda r: -r["amount"]), "miles": _money(miles), "trips": len(trips),
            "se_tax": se_tax, "se_note": f"Self-employment tax: 15.3% of 92.35% of the profit, once it reaches ${R.SE_FLOOR:,.0f}. Income tax on the profit comes on top, at your own rate.",
            "payers": expect, "form_1099_threshold": threshold,
            "sources": [R.source("irs_sch_c"), R.source("irs_se_tax"), R.source("irs_1099nec"), R.source("irs_mileage")], "not_advice": R.NOT_ADVICE}


def mark(session: Session, ids: list[int], business_id: int | None, share: float = 1.0, tax_line: str = "") -> int:
    """Mark purchases (or deposits) as a business's, at a share, on a line; or back to personal with no business."""
    if tax_line and tax_line not in R.TAX_LINES:
        raise ValueError("Not a line the app knows")
    if not 0 < share <= 1:
        raise ValueError("The business share is more than 0 and at most 1")
    n = 0
    for tid in ids:
        t = session.get(Transaction, tid)
        if t is None:
            continue
        t.business_id, t.business_share, t.tax_line = business_id, (share if business_id else 1.0), (tax_line if business_id else "")
        session.add(t)
        n += 1
    session.commit()
    return n


# ---- the write-off finder (M58) ------------------------------------------------------------------------------

FINDER: list[tuple[str, str, re.Pattern]] = [
    ("software", "Software and online services", re.compile(r"\b(anthropic|claude|openai|chatgpt|github|cloudflare|digitalocean|google ?\*?cloud|firebase|aws|amazon web services|microsoft ?365|office ?365|adobe|jetbrains|figma|notion|canva|dropbox|zoom\.us|zoom|slack|godaddy|namecheap|squarespace|wix|vercel|netlify|heroku|linode|vultr|hetzner|backblaze|1password|bitwarden|tailscale|cursor|midjourney|elevenlabs|replit)\b", re.I)),
    ("depreciation", "Computers and equipment", re.compile(r"\b(micro ?center|best ?buy|newegg|b ?& ?h photo|bhphoto|apple\.com|apple store|dell|lenovo|logitech|corsair|evga|nvidia|asus|msi|samsung|elgato|rode|shure|wacom|keychron)\b", re.I)),
    ("utilities", "Phone and internet (the business share)", re.compile(r"\b(verizon|at&t|att\b|t-mobile|xfinity|comcast|spectrum (?:internet|mobile|charter)|charter comm\w*|cox comm\w*|google fi|mint mobile|starlink|visible)\b", re.I)),
    ("education", "Courses and books for the work", re.compile(r"\b(udemy|coursera|pluralsight|linkedin learning|o'?reilly|edx|skillshare|frontend masters|egghead)\b", re.I)),
    ("office", "Office supplies and shipping", re.compile(r"\b(staples|office ?depot|officemax|usps|ups store|fedex office)\b", re.I)),
    ("advertising", "Advertising", re.compile(r"\b(facebook ads|meta ads|google ads|linkedin ads|vistaprint)\b", re.I)),
]
EQUIPMENT_FLOOR = 100.0  # a purchase from an equipment store under this is likely a cable, not equipment


def finder(session: Session, today: date, years: int = 3) -> dict[str, Any]:
    """Purchases that could be business costs if they were for your business: software and online services,
    computers and equipment, the business share of phone and internet, courses, supplies. Each kind with the rule and
    its source, the purchases, whether a receipt is kept, and what it would mean. Purchases already marked, or that
    the owner said were personal, are left out. Also: equipment bought earlier that a business could put to use at
    today's value, and start-up costs before a business began."""
    since = date(today.year - years + 1, 1, 1)
    personal = set(get_setting(session, PERSONAL, []) or [])
    rows = session.exec(select(Transaction).where(Transaction.date >= since, Transaction.amount > 0, Transaction.is_ignored == False, Transaction.business_id == None)).all()  # noqa: E711, E712
    receipts = {d.attach_to for d in session.exec(select(Document).where(Document.kind == "receipt")).all() if d.attach_to.startswith("transaction:")}
    groups: list[dict[str, Any]] = []
    for key, words, pattern in FINDER:
        hits = []
        for t in rows:
            if t.id in personal or t.is_transfer or t.is_card_payment:
                continue
            text = f"{t.merchant} {t.raw_name} {t.description}"
            if not pattern.search(text):
                continue
            if key == "depreciation" and t.amount < EQUIPMENT_FLOOR:
                continue
            hits.append({"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": _money(t.amount), "receipt": f"transaction:{t.id}" in receipts,
                         "source": f"transaction:{t.id}"})
        if not hits:
            continue
        hits.sort(key=lambda h: h["date"], reverse=True)
        total = _money(sum(h["amount"] for h in hits))
        by_year: dict[int, float] = defaultdict(float)
        for h in hits:
            by_year[int(h["date"][:4])] += h["amount"]
        meta = R.TAX_LINES[key]
        groups.append({"key": key, "words": words, "line": meta["line"], "examples": meta["examples"], "count": len(hits), "total": total,
                       "by_year": [{"year": y, "total": _money(v)} for y, v in sorted(by_year.items())], "items": hits[:200],
                       "receipts": sum(1 for h in hits if h["receipt"]), "question": "Was this for your business? Only the business share counts.",
                       "rule": _rule_for(key), "source": R.source(meta.get("source", "irs_pub_334"))})
    groups.sort(key=lambda g: -g["total"])
    eq = next((g for g in groups if g["key"] == "depreciation"), None)
    equipment = None
    if eq:
        older = [h for h in eq["items"] if h["date"] < date(today.year, 1, 1).isoformat()]
        if older:
            equipment = {"count": len(older), "cost": _money(sum(h["amount"] for h in older)),
                         "words": ("Equipment you already own can be put to use in a business. It is then depreciated from the lower of what you paid "
                                   "and what it is worth on the day the business starts using it, not what you paid years ago."),
                         "source": R.source("irs_pub_946")}
    total = _money(sum(g["total"] for g in groups))
    year_now = sum(next((y["total"] for y in g["by_year"] if y["year"] == today.year), 0.0) for g in groups)
    se = _money(year_now * R.SE_EARNINGS_SHARE * R.SE_TAX_RATE)
    said = (f"{sum(g['count'] for g in groups)} purchases since {since.year} could be business costs, ${total:,.2f} in all, if they were for a business. "
            f"This year's ${year_now:,.2f} of them would lower a business's profit by that much, and its self-employment tax by about ${se:,.2f}, with income tax on top at your own rate."
            if groups else "Nothing in your purchases looks like a usual business cost yet.")
    return {"since": since.isoformat(), "groups": groups, "total": total, "this_year": _money(year_now), "se_saving_this_year": se, "equipment": equipment,
            "startup": {"limit": R.STARTUP_DEDUCTION, "phaseout": R.STARTUP_PHASEOUT, "words": "Costs to start a business before it begins (research, a website, formation) are deductible up to $5,000 in the year it begins; more is spread over 15 years.",
                        "source": R.source("irs_pub_535")},
            "never": [{**n, "source": R.source(n["source"])} for n in R.NEVER], "said": said,
            "past_years": "A business cost counts only for a business you were carrying on. Purchases from years with no business are shown to see the pattern, not to claim; a return can generally be amended for about three years after filing.",
            "not_advice": R.NOT_ADVICE}


def _rule_for(key: str) -> str:
    return {
        "software": "Software and online services used to run the business are ordinary and necessary costs; one used for both business and personal life counts only for its business share.",
        "depreciation": "Equipment that lasts more than a year (a computer, a monitor) is depreciated, or written off at once under section 179 or the de minimis rule (up to $2,500 an item). Only the share used for the business counts.",
        "utilities": "A phone or internet plan used for both counts only for the business share; a separate business line counts in full.",
        "education": "Courses that keep up or improve skills used in the business you have count; courses toward a new trade do not.",
        "office": "Supplies used up in the business, postage and shipping count.",
        "advertising": "Advertising the business counts.",
    }.get(key, "Ordinary and necessary costs of the business count.")


def answer_finder(session: Session, ids: list[int], answer: str, business_id: int | None = None, tax_line: str = "", share: float = 1.0) -> str:
    if answer == "personal":
        kept = list(get_setting(session, PERSONAL, []) or [])
        kept.extend(i for i in ids if i not in kept)
        set_setting(session, PERSONAL, kept[-20000:])
        return f"Kept {len(ids)} as personal. They are not suggested again."
    if answer == "business":
        if business_id is None or session.get(Business, business_id) is None:
            raise ValueError("Add the business first, on Business")
        n = mark(session, ids, business_id, share, tax_line)
        return f"Marked {n} as the business's" + (f", at {share:.0%}" if share < 1 else "") + "."
    raise ValueError("The answer is business or personal")


# ---- invoices (M57) and the mileage log (M60) ----------------------------------------------------------------

def invoice_total(lines: list[dict[str, Any]]) -> float:
    total = 0.0
    for line in lines:
        q, p = float(line.get("quantity") or 1), float(line.get("price") or 0)
        if q < 0 or p < 0 or q > 100000 or p > 10_000_000:
            raise ValueError("Each line has a quantity and a price that are not negative")
        total += q * p
    return _money(total)


def next_invoice_number(session: Session, business_id: int, year: int) -> str:
    n = sum(1 for i in session.exec(select(Invoice).where(Invoice.business_id == business_id, Invoice.direction == "out")).all() if i.issued_on.year == year)
    return f"{year}-{n + 1:03d}"


def invoice_candidates(session: Session, inv: Invoice) -> list[dict[str, Any]]:
    """Deposits (for an invoice sent) or payments (received) of the same amount from its date on."""
    sign = -1 if inv.direction == "out" else 1
    rows = session.exec(select(Transaction).where(Transaction.date >= inv.issued_on - timedelta(days=3), Transaction.date <= inv.issued_on + timedelta(days=120))).all()
    hits = [t for t in rows if abs(t.amount - sign * inv.amount) <= 0.01]
    return [{"id": t.id, "date": t.date.isoformat(), "merchant": t.merchant, "amount": _money(abs(t.amount))} for t in sorted(hits, key=lambda t: t.date)[:10]]


def trips_year(session: Session, business_id: int, year: int) -> dict[str, Any]:
    rows = session.exec(select(Trip).where(Trip.business_id == business_id, Trip.day >= date(year, 1, 1), Trip.day <= date(year, 12, 31)).order_by(Trip.day)).all()
    out = []
    for t in rows:
        cents = R.mileage_rate(t.day)
        out.append({"id": t.id, "day": t.day.isoformat(), "miles": t.miles, "purpose": t.purpose, "start": t.start, "end": t.end, "cents": cents,
                    "amount": _money(t.miles * (cents or 0) / 100), "source": f"trip:{t.id}"})
    return {"year": year, "trips": out, "miles": _money(sum(t["miles"] for t in out)), "amount": _money(sum(t["amount"] for t in out)),
            "rates": [{"from": a, "to": b, "cents": c} for a, b, c in R.MILEAGE if a[:4] == str(year)], "never": "Commuting between home and a regular workplace never counts.",
            "source": R.source("irs_mileage"), "not_advice": R.NOT_ADVICE}


def json_lines(text: str) -> list[dict[str, Any]]:
    try:
        lines = json.loads(text or "[]")
    except ValueError:
        return []
    return lines if isinstance(lines, list) else []
