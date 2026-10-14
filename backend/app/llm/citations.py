"""The citation check: every figure a model states must be one the engine returned.

The engine's output is walked for numbers; the model's text is scanned for amounts; any amount that is not
in the engine's set, allowing for dropped cents and rounding to the dollar, is reported as unverified. The
chat and the reviewer attach that list to what they show, and a test fails when it is not empty for a
scripted reply that invents a figure.
"""

from __future__ import annotations

import re
from typing import Any

# $1,234.56 · $1,234 · 1,234.56 · 1234.56 · 12.5% · plain integers of three digits or more
AMOUNT = re.compile(r"(?<![\w.])(?:\$\s?)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![\w.])")
DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")


def engine_numbers(payload: Any, into: set[float] | None = None) -> set[float]:
    """Every number in a structure the engine returned, as rounded floats, both signs."""
    out: set[float] = set() if into is None else into
    if isinstance(payload, bool):
        return out
    if isinstance(payload, int | float):
        v = round(float(payload), 2)
        out.add(abs(v))
        out.add(round(abs(v)))
        out.add(float(int(abs(v))))
    elif isinstance(payload, dict):
        for v in payload.values():
            engine_numbers(v, out)
    elif isinstance(payload, list | tuple | set):
        for v in payload:
            engine_numbers(v, out)
    elif isinstance(payload, str):
        for m in AMOUNT.finditer(payload):
            token = m.group(0)
            if token.endswith("%") or "$" in token or "," in token or "." in token:
                try:
                    out.add(abs(round(float(token.replace("$", "").replace(",", "").replace("%", "").strip()), 2)))
                except ValueError:
                    pass
    return out


def figures_in(text: str) -> list[tuple[str, float]]:
    """The amounts a model stated: anything with a dollar sign, a thousands separator, cents or a percent,
    and any bare number of three or more digits. Dates, years and small counts are left alone."""
    cleaned = DATE_LIKE.sub(" ", text)
    found: list[tuple[str, float]] = []
    for m in AMOUNT.finditer(cleaned):
        token = m.group(0)
        bare = token.replace("$", "").replace(",", "").replace("%", "").strip()
        marked = "$" in token or "," in token or "." in bare or token.endswith("%")
        if not marked and len(bare) < 3:
            continue
        if not marked and 1900 <= int(bare) <= 2100:
            continue  # a year
        try:
            found.append((token.strip(), abs(round(float(bare), 2))))
        except ValueError:
            continue
    return found


def unverified(text: str, allowed: set[float]) -> list[str]:
    """The figures in `text` that the engine did not return."""
    bad: list[str] = []
    for token, value in figures_in(text):
        if value == int(value):
            # A whole figure may be an engine number with its cents dropped or rounded; `allowed` holds both.
            if value in allowed:
                continue
        elif any(abs(value - a) < 0.005 for a in allowed):
            continue
        bad.append(token)
    return bad
