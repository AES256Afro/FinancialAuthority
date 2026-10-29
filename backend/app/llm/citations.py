"""The citation check: every figure a model states must be one the engine returned.

The engine's output is walked for numbers; the model's text is scanned for amounts; any amount that is not
in the engine's set, allowing for dropped cents and rounding to the dollar, is reported as unverified. The
chat and the reviewer attach that list to what they show, and a test fails when it is not empty for a
scripted reply that invents a figure.
"""

from __future__ import annotations

import re
from typing import Any

# $1,234.56 · $1,234 · 1,234.56 · 1234.56 · 12.5% · plain integers of three digits or more. A figure may carry a
# scale letter ($1.2k, $3M) or run straight into a word (1200USD, 450dollars): the letters do not hide the number.
AMOUNT = re.compile(r"(?<![\w.])(?:\$\s?)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s?(?P<scale>[kKmM])(?![a-zA-Z]))?%?(?!\.\d)")  # a period that ends a sentence ends the figure
DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
SCALE = {"k": 1_000.0, "m": 1_000_000.0}

# Figures written in words: "four thousand", "twelve hundred", "1.5 thousand", "3 grand", "a couple hundred" (no).
_SMALL = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_BIG = {"hundred": 100.0, "thousand": 1_000.0, "grand": 1_000.0, "k": 1_000.0, "million": 1_000_000.0}
_WORD = "|".join([*_SMALL, *_TENS])
WORDS = re.compile(rf"\b(?P<n>(?:(?:{_WORD})(?:[\s-]+(?:{_WORD}))?)|\d+(?:\.\d+)?)[\s-]+(?P<big>hundred|thousand|grand|million)(?:[\s-]+(?P<rest>(?:{_WORD})(?:[\s-]+(?:{_WORD}))?))?\b", re.I)


def _small(words: str) -> float:
    total = 0.0
    for w in re.split(r"[\s-]+", words.strip().lower()):
        total += _SMALL.get(w, _TENS.get(w, 0))
    return total


def _value(token: str) -> float | None:
    """The number a token states, scaled by its letter when it has one."""
    bare = token.replace("$", "").replace(",", "").replace("%", "").strip()
    scale = 1.0
    if bare and bare[-1] in "kKmM":
        scale = SCALE[bare[-1].lower()]
        bare = bare[:-1].strip()
    try:
        return abs(round(float(bare) * scale, 2))
    except ValueError:
        return None


def figures_in_words(text: str) -> list[tuple[str, float]]:
    """Amounts written out, each with the value it states."""
    found: list[tuple[str, float]] = []
    for m in WORDS.finditer(text):
        n = m.group("n")
        try:
            lead = float(n)
        except ValueError:
            lead = _small(n)
        value = lead * _BIG[m.group("big").lower()] + (_small(m.group("rest")) if m.group("rest") else 0.0)
        if value > 0:
            found.append((m.group(0).strip(), abs(round(value, 2))))
    return found


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
                value = _value(token)
                if value is not None:
                    out.add(value)
    return out


def figures_in(text: str) -> list[tuple[str, float]]:
    """The amounts a model stated: anything with a dollar sign, a thousands separator, cents, a percent or a scale
    letter, any bare number of three or more digits, and amounts written in words. Dates, years and small counts
    are left alone."""
    cleaned = DATE_LIKE.sub(" ", text)
    found: list[tuple[str, float]] = []
    for m in AMOUNT.finditer(cleaned):
        token = m.group(0)
        bare = token.replace("$", "").replace(",", "").replace("%", "").strip()
        marked = "$" in token or "," in token or "." in bare or token.endswith("%") or bool(m.group("scale"))
        if not marked and len(bare) < 3:
            continue
        if not marked and 1900 <= int(bare) <= 2100:
            continue  # a year
        value = _value(token)
        if value is not None:
            found.append((token.strip(), value))
    found.extend(figures_in_words(cleaned))
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
