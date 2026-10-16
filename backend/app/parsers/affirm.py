"""Affirm's "Your loan confirmation" email: the loan amount, the total of payments, the finance charge, every
scheduled payment with its date, principal and interest, the merchant and the loan ID. Deterministic over the
plain text; bounds-checked; the text is data and nothing in it is followed."""

from __future__ import annotations

import re
import statistics
from datetime import date

from ..mail.classify import DATE_WORDS, parse_date_text

MONEY = r"\$\s?([\d,]+\.\d{2})"
LOAN_AMOUNT = re.compile(MONEY + r"\s*\n\s*Loan amount", re.IGNORECASE)
TOTAL = re.compile(MONEY + r"\s*\n\s*Total of payments", re.IGNORECASE)
FINANCE = re.compile(MONEY + r"\s*\n\s*Finance charge", re.IGNORECASE)
MERCHANT = re.compile(r"[–\-—]\s*([^\n]{1,120}?)\s+has processed your order", re.IGNORECASE)
LOAN_ID = re.compile(r"Loan ID:\s*([A-Z0-9][A-Z0-9-]{3,})", re.IGNORECASE)
ENTRY = re.compile(DATE_WORDS.pattern + r"\s*\n\s*" + MONEY + r"\s*\n\s*Principal:\s*" + MONEY + r"\s*\n\s*Interest:\s*" + MONEY, re.IGNORECASE)
MAX_AMOUNT = 100_000.0


def _money(m: str) -> float:
    return round(float(m.replace(",", "")), 2)


def looks_like_loan_confirmation(text: str) -> bool:
    low = (text or "").lower()
    return "loan confirmation" in low and "payment schedule" in low


def parse_loan_confirmation(text: str, ref: date | None = None) -> dict | None:
    """The loan as a plan: schedule, totals, merchant, loan ID; None when the layout is not there."""
    if not looks_like_loan_confirmation(text):
        return None
    schedule: list[dict] = []
    for m in ENTRY.finditer(text):
        when = parse_date_text(m.group(1), ref)
        if when is None:
            continue
        amount, principal, interest = _money(m.group(2)), _money(m.group(3)), _money(m.group(4))
        if not 0 < amount <= MAX_AMOUNT:
            continue
        schedule.append({"date": when.isoformat(), "amount": amount, "principal": principal, "interest": interest})
    if not schedule or len(schedule) > 72:
        return None
    schedule.sort(key=lambda s: s["date"])
    fields: dict = {"provider": "Affirm", "schedule": schedule, "count": len(schedule), "installment": round(statistics.median(s["amount"] for s in schedule), 2),
                    "first_date": schedule[0]["date"], "last_date": schedule[-1]["date"]}
    for key, pattern in (("loan_amount", LOAN_AMOUNT), ("total", TOTAL), ("finance_charge", FINANCE)):
        m = pattern.search(text)
        if m:
            fields[key] = _money(m.group(1))
    merchant = MERCHANT.search(text)
    if merchant:
        fields["merchant"] = merchant.group(1).strip()[:80]
    loan_id = LOAN_ID.search(text)
    if loan_id:
        fields["loan_id"] = loan_id.group(1).upper()
    paid = round(sum(s["amount"] for s in schedule), 2)
    fields.setdefault("total", paid)
    fields["schedule_total"] = paid
    sane = abs(paid - fields["total"]) <= 1.0 and (fields.get("finance_charge", 0.0) <= fields["total"])
    fields["confidence"] = 0.95 if sane and "loan_id" in fields else 0.8 if sane else 0.5
    return fields
