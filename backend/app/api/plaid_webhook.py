"""POST /api/plaid/webhook: the one route Plaid calls. Public by necessity (PLAN section 12), verified before
anything is read from it, and limited per address. It answers the same short reply whether or not the notice
verified, so it tells a caller nothing."""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import auth
from ..connectors import plaid_webhook
from ..db import session_scope

log = logging.getLogger(__name__)
router = APIRouter(tags=["plaid"])
LIMIT = 120  # notices an hour per address
MAX_BODY = 64 * 1024
_hits: dict[str, list[float]] = {}


def _fetch_key(kid: str) -> dict:
    from ..connectors.plaid import PlaidConnector

    with session_scope() as session:
        api = PlaidConnector().api(session)
    if api is None:
        raise RuntimeError("Plaid is not configured")
    return api.webhook_key(kid)


def sync_link(link_id: int) -> str:
    """One link's sync, then, when rows arrived, the chain the scheduled bank sync runs: detection, borrowing
    (advances and pay-later streams) and payment matching, so a webhook leaves the plan as current as the hour does."""
    from ..connectors.plaid import PlaidConnector
    from ..jobs import scheduler

    with session_scope() as session:
        result = PlaidConnector().sync(session, link_id=link_id)
    if result.inserted:
        return f"{result.summary} · {scheduler.detect_recurring()}"
    return result.summary


def _sync(link_id: int) -> None:
    """A sync of one link in the background, recorded like the scheduled one."""

    def work() -> None:
        try:
            log.info("plaid webhook sync: %s", sync_link(link_id))
        except Exception:  # noqa: BLE001 - the scheduled sync is still there if this one fails
            log.exception("plaid webhook sync failed")

    threading.Thread(target=work, name=f"plaid-webhook-{link_id}", daemon=True).start()


@router.post("/api/plaid/webhook", include_in_schema=False)
async def plaid_webhook_route(request: Request) -> JSONResponse:
    ip = auth._client_ip(request)
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < 3600]
    if len(recent) >= LIMIT:
        return JSONResponse({"ok": False}, status_code=429)
    _hits[ip] = [*recent, now]
    token = request.headers.get("plaid-verification", "")
    if not token:
        return JSONResponse({"ok": False}, status_code=401)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            return JSONResponse({"ok": False}, status_code=401)
    import json

    from starlette.concurrency import run_in_threadpool

    try:
        await run_in_threadpool(plaid_webhook.verify, token, body, _fetch_key)
        notice = json.loads(body)
    except (plaid_webhook.Refused, ValueError) as error:
        log.warning("a Plaid notice was refused: %s", error)
        return JSONResponse({"ok": False}, status_code=401)
    outcome = await run_in_threadpool(plaid_webhook.handle, notice, _sync, session_scope)
    log.info("plaid notice %s/%s: %s", notice.get("webhook_type"), notice.get("webhook_code"), outcome)
    return JSONResponse({"ok": True})
