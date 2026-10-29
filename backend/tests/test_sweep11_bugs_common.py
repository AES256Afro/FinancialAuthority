"""Sweep 11 helpers: every GET route the app serves, filled in and read as the owner, each one timed.

No tests here; the drill (test_sweep11_bugs_upgrade.py) and the live box's shapes (test_sweep11_bugs_shapes.py)
share these.
"""

from __future__ import annotations

import time
from datetime import date

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

SLOW_SECONDS = 1.5  # a route over this is flagged

# What fills a route's placeholders and its required query parameters; anything not named here is read with ids of 1.
QUERY_FILL = {"day": "5", "amount": "50", "q": "rent", "format": "json", "bought_on": "{today}", "merchant": "Example"}
PATH_FILL = {"token": "not-a-token", "name": "none.pdf", "page": "1", "year": "{year}"}
SKIP = ("/api/calendar/",)  # a public feed by token: read by test_sweep5_api_routes


def get_routes() -> list[tuple[str, list[str], list[str]]]:
    """Every GET route: its path template, its required query parameters and its path parameters."""
    seen: dict[str, tuple[str, list[str], list[str]]] = {}

    def walk(routes, prefix: str = "") -> None:
        for r in routes:
            if isinstance(r, APIRoute):
                path = prefix + r.path
                if "GET" in r.methods and path not in seen:
                    required = [q.name for q in r.dependant.query_params if getattr(q, "required", False) is True or getattr(getattr(q, "field_info", None), "is_required", lambda: False)()]
                    seen[path] = (path, required, [p.name for p in r.dependant.path_params])
            elif hasattr(r, "original_router"):
                walk(r.original_router.routes, prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or ""))
            elif hasattr(r, "routes"):
                walk(r.routes, prefix)

    walk(app.routes)
    return sorted(seen.values())


def fill(path: str, required: list[str], params: list[str], today: date, ids: dict[str, int | str]) -> str:
    year = str(today.year)
    for p in params:
        value = ids.get(p, PATH_FILL.get(p, "1"))
        path = path.replace("{" + p + "}", str(value).format(year=year))
    query = "&".join(f"{q}={QUERY_FILL.get(q, '1').format(today=today.isoformat())}" for q in required)
    return path + ("?" + query if query else "")


def read_every_route(owner: TestClient, today: date, ids: dict[str, int | str] | None = None) -> dict[str, tuple[int, float, object]]:
    """Each GET route as the owner: its status, its seconds and its body (JSON when it is JSON). Nothing is asserted here."""
    out: dict[str, tuple[int, float, object]] = {}
    for path, required, params in get_routes():
        if path.startswith(SKIP):
            continue
        url = fill(path, required, params, today, ids or {})
        started = time.perf_counter()
        response = owner.get(url)
        seconds = time.perf_counter() - started
        body: object = None
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                body = response.json()
            except ValueError:
                body = None
        out[path] = (response.status_code, seconds, body)
    return out


UNREACHABLE = ("/api/v1/mail/messages/{message_id}",)  # the body is fetched from the mailbox itself; 502 is its deliberate "could not fetch"


def failures(got: dict[str, tuple[int, float, object]]) -> list[str]:
    return [f"{path} answered {status}" for path, (status, _s, _b) in got.items() if status >= 500 and not (status == 502 and path.startswith(UNREACHABLE))]


def slow(got: dict[str, tuple[int, float, object]], limit: float = SLOW_SECONDS) -> list[str]:
    return [f"{path} took {seconds:.2f}s" for path, (_st, seconds, _b) in got.items() if seconds > limit]
