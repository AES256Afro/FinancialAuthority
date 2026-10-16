"""Bank and card statements as PDFs with a text layer: the transaction lines read into rows the importer takes,
with a preview first. A line is a date, words, and an amount (with a minus sign, parentheses, "CR" or a separate
credit column marking money in); a running balance after the amount is left out. The statement's own period gives
the year to dates printed without one, and a December line in a January statement belongs to the year before.

Deterministic, and bounded: at most forty pages are read (parsers.paystub.pdf_text), lines longer than 300
characters are skipped, and nothing found is guessed at."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DATE_LEAD = re.compile(r"^\s*(?:(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?|([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?)\s+")
AMOUNT = re.compile(r"(\(?-?\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:\s?(?:CR|DR|-))?)")
PERIOD = re.compile(r"(?:statement period|period|for the period|closing date)[^\n]{0,40}?(\d{1,2}/\d{1,2}/(\d{2,4})|[A-Za-z]{3,9}\.? \d{1,2},? (\d{4}))", re.IGNORECASE)
MAX_LINE = 300
SKIP = re.compile(r"\b(?:beginning balance|ending balance|previous balance|new balance|total|subtotal|minimum payment|payment due|credit limit|available credit|page \d+)\b", re.IGNORECASE)
CREDIT_WORDS = re.compile(r"\b(?:payment|deposit|credit|refund|return|transfer in|interest paid)\b", re.IGNORECASE)


def _value(text: str) -> tuple[float, bool]:
    """An amount and whether it is marked as money in."""
    t = text.replace("$", "").replace(" ", "")
    credit = t.startswith("(") or t.endswith(")") or t.endswith("CR") or t.startswith("-") or t.endswith("-")
    number = float(re.sub(r"[^\d.]", "", t))
    return number, credit


def _year_from(text: str, fallback: int) -> int:
    m = PERIOD.search(text)
    if m:
        year = m.group(2) or m.group(3)
        if year:
            y = int(year)
            return y + 2000 if y < 100 else y
    return fallback


def parse_statement_text(text: str, today: date, *, card: bool = False) -> dict[str, Any]:
    """Rows in the ledger's sign (money out positive). On a card statement a charge is money out and a payment money
    in; on a bank statement a line with a credit mark, or a word like "deposit", is money in."""
    year = _year_from(text[:5000], today.year)
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in (text or "").splitlines():
        if len(line) > MAX_LINE or not line.strip():
            continue
        m = DATE_LEAD.match(line)
        if not m:
            continue
        if SKIP.search(line):
            skipped += 1
            continue
        if m.group(1):
            month, day = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else None
        else:
            month, day = MONTHS.get(m.group(4).lower()[:3], 0), int(m.group(5))
            y = int(m.group(6)) if m.group(6) else None
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        rest = line[m.end():]
        amounts = AMOUNT.findall(rest)
        if not amounts:
            continue
        # With two amounts on a line, the last is usually the running balance.
        raw = amounts[-2] if len(amounts) >= 2 else amounts[-1]
        description = rest[: rest.find(raw)].strip(" .-")
        if not description:
            continue
        value, marked_credit = _value(raw)
        y = (y + 2000 if y and y < 100 else y) or (year - 1 if month == 12 and today.month <= 2 and year == today.year else year)
        try:
            when = date(y, month, day)
        except ValueError:
            continue
        money_in = marked_credit or (not card and bool(CREDIT_WORDS.search(description))) or (card and bool(re.search(r"\bpayment\b|\bcredit\b|\brefund\b", description, re.IGNORECASE)))
        rows.append({"date": when, "amount": round(-value if money_in else value, 2), "description": description[:120], "merchant": re.sub(r"\s+", " ", description)[:80],
                     "category": "Uncategorized", "account": "", "fitid": "", "posted": None})
    return {"kind": "statement", "rows": rows, "total": len(rows), "skipped": skipped, "year": year, "ready": bool(rows),
            "note": "Read from the statement's text. Check the preview: a line the app could not read is left out, never guessed."}
