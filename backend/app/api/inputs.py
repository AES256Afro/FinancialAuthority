"""The bounds every input to the app keeps, in one place: the request bodies (every model here derives from `In`),
the path and query parameters (`bounded_params`, a dependency on every route), the fields of an upload's form
(`bounded_form`, the same), the 422 answer (`validation_error`), and the MCP tools' arguments (`check_args`, called
by the tool wrapper).

What it holds: a number is finite and within a trillion; a whole number fits a 32-bit id; a date falls between 1900
and 2199; a string is at most a thousand characters unless its field is one of the free-text ones below, and carries
no control character, bidi override or invisible space (a line break is kept only in the multi-line fields). Python's
JSON reader accepts NaN, Infinity and integers of any length, and Pydantic keeps them unless told otherwise; SQLite
stores NaN as NULL and refuses an integer past 2^63, and a stored Infinity or 1e308 breaks every later read that sums
it, so these are refused at the door with a sentence instead."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

MAX_NUMBER = 1e12
MAX_INT = 2_147_483_647
FIRST_YEAR, LAST_YEAR = 1900, 2199
MAX_LINE = 1000

# The free-text fields, by name, with their own ceilings; a line break is kept in these and nowhere else.
TEXT = {
    "body": 1_000_000,  # a draft (approvals keep their own 1 MB ceiling) or a letter
    "content": 100_000,  # a chat message
    "css": 100_000, "notes": 20_000, "note": 2_000, "rules": 20_000, "goals": 20_000,
    "certificate": 20_000, "private_key": 20_000,  # PEM blocks
    "mark": 1_000_000,  # an appearance mark as a data URL (its own 300 KB check follows)
}
# Fields that carry a date as text and are read with date.fromisoformat further in.
DATE_TEXT = {"date", "next_date", "target_date", "as_of", "due_date", "move_date", "on", "bought_on", "anchor_date", "start_date", "end_date", "paid_on", "until", "since",
             "return_by", "warranty_until"}
# Form fields that carry a number as text (an upload's "$1,234.50") and are read with float() further in.
NUMBER_TEXT = {"amount"}
# Form fields with a longer ceiling of their own: the OAuth consent page's signed request, which carries the app's
# redirect address and state.
FORM_TEXT = {"req": 16_000}

_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b  ‪-‮⁦-⁩﻿]")
_BREAK = re.compile("[\r\n\t]")


def problem(name: str, value: Any, depth: int = 0) -> str | None:
    """Why one value is refused, or None. Walks lists and dicts with the field's own name, twenty levels at most."""
    if depth > 20:
        return f"{name} is nested too deeply"
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return f"{name} must be a finite number"
        if abs(value) > MAX_NUMBER:
            return f"{name} is out of range"
        return None
    if isinstance(value, int):
        if name == "year" and not FIRST_YEAR <= value <= LAST_YEAR:
            return f"year must fall between {FIRST_YEAR} and {LAST_YEAR}"
        return f"{name} is out of range" if abs(value) > MAX_INT else None
    if isinstance(value, (date, datetime)):
        return None if FIRST_YEAR <= value.year <= LAST_YEAR else f"{name} must fall between {FIRST_YEAR} and {LAST_YEAR}"
    if isinstance(value, str):
        ceiling = TEXT.get(name, MAX_LINE)
        if len(value) > ceiling:
            return f"{name} is longer than {ceiling} characters"
        if name in TEXT and _CONTROL.search(value):
            return f"{name} holds a control character or a direction override"
        if name not in TEXT and (_CONTROL.search(value) or _BREAK.search(value)):
            return f"{name} must be one line of plain text, with no control characters"
        if name in DATE_TEXT and value:
            try:
                parsed = date.fromisoformat(value[:10])
            except ValueError:
                return f"{name} must be a date like 2026-10-30"
            if not FIRST_YEAR <= parsed.year <= LAST_YEAR:
                return f"{name} must fall between {FIRST_YEAR} and {LAST_YEAR}"
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            why = problem(name, item, depth + 1)
            if why:
                return why
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            why = problem(name, key) if isinstance(key, str) else None
            why = why or problem(name if not isinstance(key, str) or key not in TEXT else key, item, depth + 1)
            if why:
                return why
    return None


class In(BaseModel):
    """The base of every request body."""

    model_config = ConfigDict(allow_inf_nan=False)

    @model_validator(mode="after")
    def _bounded(self) -> "In":
        for name in type(self).model_fields:
            value = getattr(self, name, None)
            if isinstance(value, BaseModel):
                continue  # a nested model checks itself
            why = problem(name, value)
            if why:
                raise ValueError(why)
        return self


def check_args(args: dict) -> str | None:
    """The same bounds over a tool call's arguments; the sentence to answer with, or None."""
    for name, value in args.items():
        why = problem(name, value)
        if why:
            return why
    return None


def bounded_params(request: Request) -> None:
    """Every route's path and query parameters that are numbers: finite and in range, before the route reads them
    (a path id past 2^63 raised out of SQLite, a year past a C long out of the calendar)."""
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return
    for where, raw in (("path", request.path_params), ("query", request.query_params)):
        for param in getattr(dependant, f"{where}_params", []):
            kind = getattr(param.field_info, "annotation", None) or getattr(param, "type_", None)
            text = raw.get(param.alias) if hasattr(raw, "get") else None
            if text is None or kind not in (int, float, int | None, float | None):
                continue
            try:
                number = float(text)
            except ValueError:
                continue  # the route's own validation answers 422
            if not math.isfinite(number) or abs(number) > (MAX_INT if kind in (int, int | None) else MAX_NUMBER) or (param.alias == "year" and not FIRST_YEAR <= number <= LAST_YEAR):
                if where == "path":
                    raise HTTPException(404, "Not found")
                raise HTTPException(400, f"{param.alias} is out of range" if param.alias != "year" else f"year must fall between {FIRST_YEAR} and {LAST_YEAR}")


def _number_problem(name: str, text: str, whole: bool) -> str | None:
    """A form field read as a number: finite and within range, as a JSON number is. Text that is not a number at all
    is left to the route, which says what it expected."""
    try:
        number = float(text.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    if not math.isfinite(number):
        return f"{name} must be a finite number"
    if abs(number) > (MAX_INT if whole else MAX_NUMBER):
        return f"{name} is out of range"
    return None


async def bounded_form(request: Request) -> None:
    """Every route that reads a multipart or url-encoded form (an upload, the OAuth consent page): each field keeps
    the bounds a JSON body keeps, before the route reads it. FastAPI has parsed the form already (Starlette keeps it
    on the request), so this reads the same fields, never the body twice. A file's name is a one-line string too.
    Before this, `amount=inf` on an upload was stored as Infinity and `bought_on=0001-01-01` passed."""
    from fastapi import params as fastapi_params
    from starlette.datastructures import UploadFile

    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    fields = {p.alias: p for p in getattr(dependant, "body_params", []) if isinstance(p.field_info, (fastapi_params.Form, fastapi_params.File))}
    if not fields:
        return
    form = await request.form()
    for name, value in form.multi_items():
        if isinstance(value, UploadFile):
            why = problem("filename", value.filename or "")
        elif name not in fields:
            continue  # the route never reads it
        elif name in FORM_TEXT:
            why = f"{name} is longer than {FORM_TEXT[name]} characters" if len(value) > FORM_TEXT[name] else (
                f"{name} must be one line of plain text, with no control characters" if _CONTROL.search(value) or _BREAK.search(value) else None)
        else:
            why = problem(name, value)
            kind = getattr(fields[name].field_info, "annotation", None)
            if why is None and value.strip() and (name in NUMBER_TEXT or kind in (int, float, int | None, float | None)):
                why = _number_problem(name, value, kind in (int, int | None))
        if why:
            raise HTTPException(400, why)


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422, without echoing the input back: the default answer carries each refused value, so a NaN or
    an Infinity made the refusal itself fail to render (a 500), a body nested a thousand deep overflowed the
    encoder, and a 100,000-character field came back whole."""
    raw = exc.errors()
    if raw and all(e.get("type") == "value_error" and str(e.get("msg", "")).startswith("Value error, ") for e in raw):
        # the bounds above (and a model's own check): one sentence, as the routes' own refusals are
        return JSONResponse({"detail": str(raw[0]["msg"]).removeprefix("Value error, ")[:300]}, status_code=400)
    errors = []
    for error in raw[:20]:
        loc = [part if isinstance(part, (str, int)) else str(part) for part in error.get("loc", ())][:8]
        errors.append({"loc": loc, "msg": str(error.get("msg", "invalid"))[:300], "type": str(error.get("type", "value_error"))[:60]})
    return JSONResponse({"detail": errors or [{"loc": [], "msg": "The request could not be read.", "type": "value_error"}]}, status_code=422)
