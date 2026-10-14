"""Paystubs: the text of a stub read into gross, net, pay date, period and every deduction line.

Payroll providers lay stubs out differently; the labels are what they share. The reader looks for those labels
and the amount that follows each. A stub it cannot read fully still yields what it found, at lower confidence,
and the owner fills the rest on the confirm card.
"""

from __future__ import annotations

import io
import re
from datetime import date

from ..mail.classify import AMOUNT, DATE_NUM, DATE_WORDS, parse_date_text

NET_LABELS = r"net pay|net amount|net check|take[- ]home( pay)?|net earnings|total net|net:"
GROSS_LABELS = r"gross pay|gross earnings|total gross|gross wages|gross amount|total earnings|gross:"
DATE_LABELS = r"pay date|check date|payment date|date paid|paid on|pay day"
PERIOD_LABELS = r"pay period|period (?:beginning|begin|start|from)|period:|pay period ending|period ending"
DEDUCTION_LABELS = [
    ("Federal income tax", r"federal (?:income )?(?:tax|withholding)|fed(?:eral)? w/h|fit\b|fed tax"),
    ("Social Security", r"social security|oasdi|fica(?:[- ]ss)?\b(?!.*med)"),
    ("Medicare", r"medicare|fica[- ]?med"),
    ("State income tax", r"state (?:income )?(?:tax|withholding)|sit\b|state w/h"),
    ("Local tax", r"local (?:income )?tax|city tax|sdi\b"),
    ("Health insurance", r"medical|health(?: insurance| plan| premium)?\b"),
    ("Dental", r"dental"),
    ("Vision", r"vision"),
    ("401(k)", r"401\(?k\)?|retirement|403\(?b\)?|pension"),
    ("HSA", r"\bhsa\b|health savings"),
    ("Life insurance", r"life (?:insurance|ins)"),
    ("Garnishment", r"garnish"),
]


def pdf_text(data: bytes) -> str:
    """Text from a PDF, page by page. A scanned stub gives nothing here; OCR is a later milestone."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001  (a bad file is still just data)
        return ""


def looks_like_paystub(text: str) -> bool:
    low = (text or "").lower()
    return bool(re.search(NET_LABELS, low)) and bool(re.search(GROSS_LABELS, low) or re.search(PERIOD_LABELS, low) or "earnings" in low)


def _amount_after(text: str, label_pattern: str) -> float | None:
    low = text.lower()
    best: float | None = None
    for m in re.finditer(label_pattern, low):
        window = text[m.end(): m.end() + 80]
        a = AMOUNT.search(window) or re.search(r"(\d{1,3}(?:,\d{3})*\.\d{2})", window)
        if a:
            value = float(a.group(1).replace(",", ""))
            # The largest figure near a net or gross label is the period figure; a YTD column comes after and is bigger,
            # so take the first amount, not the largest.
            return round(value, 2)
    return best


def _date_after(text: str, label_pattern: str, ref: date | None) -> date | None:
    low = text.lower()
    for m in re.finditer(label_pattern, low):
        window = text[m.end(): m.end() + 60]
        for pattern in (DATE_WORDS, DATE_NUM):
            d = pattern.search(window)
            if d:
                parsed = parse_date_text(d.group(1), ref)
                if parsed:
                    return parsed
    return None


def _period(text: str, ref: date | None) -> tuple[date | None, date | None]:
    low = text.lower()
    for m in re.finditer(PERIOD_LABELS, low):
        window = text[m.end(): m.end() + 90]
        found = [d.group(1) for d in DATE_WORDS.finditer(window)] + [d.group(1) for d in DATE_NUM.finditer(window)]
        parsed = [parse_date_text(f, ref) for f in found]
        parsed = [p for p in parsed if p]
        if len(parsed) >= 2:
            return min(parsed[:2]), max(parsed[:2])
        if len(parsed) == 1:
            return None, parsed[0]
    return None, None


def parse_paystub_text(text: str, ref: date | None = None) -> dict | None:
    if not looks_like_paystub(text):
        return None
    gross = _amount_after(text, GROSS_LABELS)
    net = _amount_after(text, NET_LABELS)
    pay_date = _date_after(text, DATE_LABELS, ref)
    start, end = _period(text, ref)
    deductions: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        low = line.lower()
        if re.search(GROSS_LABELS, low) or re.search(NET_LABELS, low):
            continue
        for name, pattern in DEDUCTION_LABELS:
            if name in seen or not re.search(pattern, low):
                continue
            a = AMOUNT.search(line) or re.search(r"(\d{1,3}(?:,\d{3})*\.\d{2})", line)
            if a:
                deductions.append({"name": name, "amount": round(float(a.group(1).replace(",", "")), 2)})
                seen.add(name)
                break
    employer = None
    for line in text.splitlines()[:8]:
        clean = line.strip()
        if 3 < len(clean) < 60 and not re.search(r"\d{2,}|pay|stub|statement|earnings|period", clean.lower()):
            employer = clean
            break
    hours = None
    # "Regular Hours: 80", or an earnings row "Regular  37.26  80.00  2,980.77" where the rate comes before the hours.
    h = re.search(r"(?:regular|reg)\s*(?:hours|hrs)\s*[:\s]\s*(\d{1,3}(?:\.\d{1,2})?)", text, re.IGNORECASE) or re.search(
        r"(?:regular|reg)\s+(?:\$?\d{1,3}\.\d{2,4}\s+)?(\d{1,3}(?:\.\d{1,2})?)\s+\$?\d{1,3}(?:,\d{3})*\.\d{2}", text, re.IGNORECASE
    )
    if h:
        hours = float(h.group(1))
    fields: dict = {k: v for k, v in {
        "gross": gross, "net": net, "pay_date": pay_date.isoformat() if pay_date else None, "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None, "employer": employer, "hours": hours, "deductions": deductions or None,
    }.items() if v is not None}
    confidence = 0.3 + (0.3 if net is not None else 0) + (0.2 if pay_date is not None else 0) + (0.1 if gross is not None else 0) + (0.1 if deductions else 0)
    fields["confidence"] = round(min(confidence, 0.95), 2)
    return fields
