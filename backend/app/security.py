"""Security headers and a body-size ceiling, as plain ASGI so streamed MCP responses pass through untouched.

Every response: no sniffing, no referrer, no framing by other sites, no camera or microphone, HSTS over https.
The app's pages also get a content-security policy: scripts only from the app (and Plaid's Link, for connecting
a bank), styles from the app (inline style attributes allowed; React sets them), no plugins, no outside forms.
The OAuth consent page gets its own: it posts to itself and is then redirected to the client's own address, so
its form rule allows https and a local callback; everything else is closed. A request body over the ceiling is
refused before it is read."""

from __future__ import annotations

from typing import Any

MAX_BODY = 60 * 1024 * 1024  # uploads are refused above 50 MB by their routes; this stops anything larger early
MAX_JSON = 1024 * 1024  # every other body: a form or a JSON object is never near a megabyte
UPLOAD_PREFIXES = ("/api/v1/imports", "/api/v1/borrowing/statement", "/api/v1/imports/rocketmoney", "/api/v1/appearance", "/api/v1/exports/templates")

APP_CSP = (
    "default-src 'self'; script-src 'self' https://cdn.plaid.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self' https://*.plaid.com; frame-src 'self' https://cdn.plaid.com https://*.plaid.com; worker-src 'self'; manifest-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
OAUTH_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self' https: http://localhost:* http://127.0.0.1:* financialauthority:; frame-ancestors 'none'; base-uri 'none'"

COMMON = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    (b"cross-origin-opener-policy", b"same-origin-allow-popups"),
]


def _is_https(scope: dict) -> bool:
    if scope.get("scheme") == "https":
        return True
    for k, v in scope.get("headers", []):
        if k == b"x-forwarded-proto" and v.split(b",")[0].strip() == b"https":
            return True
    return False


class SecurityMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path_now: str = scope.get("path", "")
        ceiling = MAX_BODY if path_now.startswith(UPLOAD_PREFIXES) else MAX_JSON
        for k, v in scope.get("headers", []):
            if k == b"content-length" and v.isdigit() and int(v) > ceiling:
                await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), *COMMON]})
                await send({"type": "http.response.body", "body": b'{"detail":"That request is too large"}'})
                return
        path: str = scope.get("path", "")
        https = _is_https(scope)
        state = {"received": 0, "refused": False, "started": False}

        async def counted_receive() -> dict:
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > ceiling and not state["refused"]:
                    state["refused"] = True
                    if not state["started"]:
                        state["started"] = True
                        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), *COMMON]})
                        await send({"type": "http.response.body", "body": b'{"detail":"That request is too large"}'})
                    return {"type": "http.disconnect"}
            return message

        async def wrapped(message: dict) -> None:
            if state["refused"]:
                return
            if message["type"] == "http.response.start":
                state["started"] = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in {h for h, _ in COMMON} and k.lower() != b"content-security-policy"]
                headers += COMMON
                if https:
                    headers.append((b"strict-transport-security", b"max-age=15552000; includeSubDomains"))
                if path.startswith("/oauth/"):
                    headers.append((b"content-security-policy", OAUTH_CSP.encode()))
                elif not path.startswith(("/api/", "/mcp", "/.well-known/")):
                    headers.append((b"content-security-policy", APP_CSP.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, counted_receive, wrapped)
