"""Text from outside (a merchant's name, a memo, an imported row) made safe for the file it goes into.

Spreadsheets run a cell that starts with = + - @ or a tab or carriage return as a formula, so such a cell gets a
leading apostrophe, unless it is a plain number. Line-based formats (ledger, beancount, QIF, ICS) cannot carry a
line break inside a field, so breaks become spaces."""

from __future__ import annotations

import re
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)*$")


def cell(value: Any) -> Any:
    """A spreadsheet or CSV cell that cannot run as a formula."""
    if isinstance(value, str) and value.startswith(FORMULA_START) and not NUMBER.match(value.strip()):
        return "'" + value
    return value


def row(values: dict[str, Any]) -> dict[str, Any]:
    return {k: cell(v) for k, v in values.items()}


def line(value: str | None) -> str:
    """A field that must stay on one line."""
    return re.sub(r"[\r\n  ]+", " ", value or "")


def put(sheet: Any, *, row: int, column: int, value: Any) -> Any:  # noqa: A002 - mirrors openpyxl's names
    """Write a spreadsheet cell; text is always stored as text, never as a formula."""
    c = sheet.cell(row=row, column=column, value=cell(value))
    if isinstance(value, str):
        c.data_type = "s"
    return c
