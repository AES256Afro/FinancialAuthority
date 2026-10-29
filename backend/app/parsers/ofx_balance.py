"""The balance an OFX or QFX statement states: LEDGERBAL (the posted balance) and AVAILBAL (what can be spent now),
each with the day it is as of, per statement in the file. A file of transactions alone is otherwise all an owner who
links no bank has, and without a balance the plan cannot say what is safe to spend.

Deterministic, bounded, and data only: an amount outside the bounds, or a date that does not parse, is dropped."""

from __future__ import annotations

import re
from datetime import date

MAX_ABS = 10_000_000.0
STATEMENT = re.compile(r"<(STMTRS|CCSTMTRS)>(.*?)(?:</\1>|(?=<(?:STMTRS|CCSTMTRS)>)|\Z)", re.IGNORECASE | re.DOTALL)
FIELD = r"<{tag}>\s*([^<\r\n]*)"


def _field(body: str, tag: str) -> str:
    m = re.search(FIELD.format(tag=tag), body, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _amount(text: str) -> float | None:
    clean = text.replace(",", "").strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", clean):
        return None
    value = float(clean)
    return round(value, 2) if abs(value) <= MAX_ABS else None


def _day(text: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _balance(body: str, tag: str) -> tuple[float | None, date | None]:
    m = re.search(rf"<{tag}>(.*?)(?:</{tag}>|(?=<(?:LEDGERBAL|AVAILBAL|/STMTRS|/CCSTMTRS)>)|\Z)", body, re.IGNORECASE | re.DOTALL)
    if not m:
        return None, None
    return _amount(_field(m.group(1), "BALAMT")), _day(_field(m.group(1), "DTASOF"))


def statement_balances(text: str) -> list[dict]:
    """[{account, card, ledger, available, as_of}] for each statement that states a balance. For a card the amounts
    are as the statement writes them (a balance owed is negative in OFX); `owed` turns it into what is owed."""
    out = []
    for m in STATEMENT.finditer(text or ""):
        body = m.group(2)
        ledger, ledger_on = _balance(body, "LEDGERBAL")
        available, available_on = _balance(body, "AVAILBAL")
        if ledger is None and available is None:
            continue
        card = m.group(1).upper() == "CCSTMTRS"
        as_of = (available_on or ledger_on) if available is not None else (ledger_on or available_on)
        out.append({"account": _field(body, "ACCTID"), "card": card, "ledger": ledger, "available": available, "as_of": as_of,
                    "owed": round(-ledger, 2) + 0.0 if card and ledger is not None else None})
    return out


def balance_to_count(found: dict) -> tuple[float, date | None] | None:
    """The one figure the plan counts from a statement: for checking and savings the available balance when stated
    (holds already out), else the posted one; for a card what is owed."""
    if found["card"]:
        return (found["owed"], found["as_of"]) if found["owed"] is not None else None
    amount = found["available"] if found["available"] is not None else found["ledger"]
    return (amount, found["as_of"]) if amount is not None else None
