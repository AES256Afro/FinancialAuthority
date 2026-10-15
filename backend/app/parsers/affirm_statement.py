"""Affirm's monthly "Loans Statement" PDF, the one export Affirm offers: every loan with its ID, merchant, loan
date, amount, APR, opening and closing balance, next payment and status; the interest charged per loan this
period and for the year; the period's payments. Deterministic over the PDF's text; bounds-checked; the text
is data and nothing in it is followed."""

from __future__ import annotations

import re
from datetime import date, datetime

PERIOD = re.compile(r"Loans Statement for\s+([A-Za-z]+ \d{1,2})\s*-\s*([A-Za-z]+ \d{1,2}),\s*(\d{4})", re.IGNORECASE)
LOAN_ID = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
MONEY = re.compile(r"^-?\$?([\d,]+\.\d{2})$")
DATE = re.compile(r"^(\d{2}/\d{2}/\d{4})$")
PCT = re.compile(r"^(\d{1,2}\.\d{2})%$")
STATUS = {"active", "closed", "in dispute", "paid off", "refunded"}
MAX_AMOUNT = 100_000.0


def looks_like_affirm_statement(text: str) -> bool:
    low = (text or "").lower()
    return "affirm loans statement" in low and "loans summary" in low


def _money(v: str) -> float:
    return round(float(v.replace("$", "").replace(",", "")), 2)


def _date(v: str) -> str:
    return datetime.strptime(v, "%m/%d/%Y").date().isoformat()


def parse_affirm_statement(text: str) -> dict | None:
    """{"period_start", "period_end", "loans": [...], "interest": {loan_id: amount}, "interest_period",
    "interest_ytd", "transactions": [...]} or None when the layout is not there."""
    if not looks_like_affirm_statement(text):
        return None
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out: dict = {"loans": [], "interest": {}, "transactions": []}
    m = PERIOD.search(text)
    if m:
        year = int(m.group(3))
        try:
            start = datetime.strptime(f"{m.group(1)} {year}", "%B %d %Y").date()
            end = datetime.strptime(f"{m.group(2)} {year}", "%B %d %Y").date()
            if end < start:
                start = start.replace(year=year - 1)
            out["period_start"], out["period_end"] = start.isoformat(), end.isoformat()
        except ValueError:
            pass

    def section(name: str, stop: tuple[str, ...]) -> list[str]:
        try:
            i = next(k for k, ln in enumerate(lines) if ln.lower() == name)
        except StopIteration:
            return []
        rows = []
        for ln in lines[i + 1:]:
            if ln.lower() in stop or ln.lower().startswith("loans identified as"):
                break
            rows.append(ln)
        return rows

    # Loans Summary: each loan starts at its ID; the merchant may take one or two lines (a "Virtual Card" or
    # "Affirm Card" line first); then the loan date, amount, APR, opening, closing, due date, payment, status.
    rows = section("loans summary", ("interest charged", "transactions summary"))
    k = 0
    while k < len(rows):
        if not LOAN_ID.match(rows[k]):
            k += 1
            continue
        loan: dict = {"loan_id": rows[k]}
        j = k + 1
        words: list[str] = []
        while j < len(rows) and not DATE.match(rows[j]) and not LOAN_ID.match(rows[j]):
            words.append(rows[j])
            j += 1
        channel = next((w for w in words if w.lower() in ("virtual card", "affirm card")), "")
        rest = [w for w in words if w.lower() not in ("virtual card", "affirm card", "online order", "in-store")]
        loan["channel"] = channel
        loan["merchant"] = rest[0][:80] if rest else "Affirm"
        loan["description"] = " ".join(rest[1:])[:120]
        fields = rows[j: j + 8]
        try:
            loan["loan_date"] = _date(fields[0])
            loan["amount"] = _money(fields[1])
            loan["apr"] = float(PCT.match(fields[2]).group(1)) if PCT.match(fields[2]) else None
            loan["opening_balance"] = _money(fields[3])
            loan["closing_balance"] = _money(fields[4])
            due, pay, status = fields[5], fields[6], fields[7]
            loan["payment_due"] = _date(due) if DATE.match(due) else None
            loan["payment_amount"] = _money(pay) if MONEY.match(pay) else None
            loan["status"] = status.lower() if status.lower() in STATUS else status.lower()
        except (IndexError, ValueError, AttributeError):
            k = j
            continue
        if 0 <= loan["amount"] <= MAX_AMOUNT and 0 <= loan["closing_balance"] <= MAX_AMOUNT:
            out["loans"].append(loan)
        k = j + 8

    rows = section("interest charged", ("transactions summary", "what to do if you think"))
    k = 0
    while k < len(rows):
        if LOAN_ID.match(rows[k]):
            amount = next((rows[t] for t in range(k + 1, min(k + 4, len(rows))) if MONEY.match(rows[t])), None)
            if amount:
                out["interest"][rows[k]] = _money(amount)
        low = rows[k].lower()
        if low.startswith("total interest for this period"):
            nxt = rows[k + 1] if k + 1 < len(rows) else ""
            if MONEY.match(nxt):
                out["interest_period"] = _money(nxt)
        if low.startswith("total interest for calendar ytd") and not low.startswith("total interest for calendar ytd includes"):
            nxt = rows[k + 1] if k + 1 < len(rows) else ""
            if MONEY.match(nxt):
                out["interest_ytd"] = _money(nxt)
        k += 1

    rows = section("transactions summary", ("what to do if you think",))
    k = 0
    while k < len(rows):
        if DATE.match(rows[k]) and k + 4 < len(rows) and LOAN_ID.match(rows[k + 1]):
            amount = rows[k + 4] if MONEY.match(rows[k + 4]) else None
            if amount:
                out["transactions"].append({"date": _date(rows[k]), "loan_id": rows[k + 1], "merchant": rows[k + 2][:80], "description": rows[k + 3][:40], "amount": _money(amount)})
            k += 5
            continue
        k += 1
    if not out["loans"]:
        return None
    out["total_closing"] = round(sum(l["closing_balance"] for l in out["loans"] if l["status"] == "active"), 2)
    out["confidence"] = 0.95 if out.get("period_end") and out["interest"] else 0.8
    return out
