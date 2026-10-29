"""Security headers and a body-size ceiling, as plain ASGI so streamed MCP responses pass through untouched.

Every response: no sniffing, no referrer, no framing by other sites, no camera or microphone, HSTS over https.
The app's pages also get a content-security policy: scripts only from the app (and Plaid's Link, for connecting
a bank), styles from the app (inline style attributes allowed; React sets them), no plugins, no outside forms.
The OAuth consent page gets its own: it posts to itself and is then redirected to the client's own address, so
its form rule allows https and a local callback; everything else is closed. A request body over the ceiling is
refused before it is read.

Every /api answer also says Cross-Origin-Resource-Policy: same-origin, and a request for /api that another site's
page made other than by CORS or a top-level page load (a <script>, <img>, <link>, <object> or frame, or a no-cors
fetch) is refused before any route runs. SameSite=Lax keeps the cookie off those from another site, but a sibling
subdomain (the demo's) is the same site, so its tags went out with the owner's cookie and were answered in full: the
browser then refused to run them, but the work was done, its timing told signed-in from not, and the bytes reached
the sibling's process. The browser says who asked in Sec-Fetch-Site; callers that send no such header (a key, a
calendar server, Home Assistant, Plaid's webhook) are not affected."""

from __future__ import annotations

from typing import Any

MAX_BODY = 60 * 1024 * 1024  # uploads are refused above 50 MB by their routes; this stops anything larger early
MAX_JSON = 1024 * 1024  # every other body: a form or a JSON object is never near a megabyte
UPLOAD_PREFIXES = ("/api/v1/imports", "/api/v1/borrowing/statement", "/api/v1/imports/rocketmoney", "/api/v1/documents")  # multipart files only; JSON keeps MAX_JSON

APP_CSP = (
    # Scripts: the app's own, Plaid Link and Teller Connect (the two bank sign-in widgets); nothing else.
    "default-src 'self'; script-src 'self' https://cdn.plaid.com https://cdn.teller.io; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self' https://*.plaid.com https://*.teller.io; frame-src 'self' https://cdn.plaid.com https://*.plaid.com https://teller.io https://*.teller.io; worker-src 'self'; manifest-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
OAUTH_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self' https: http://localhost:* http://127.0.0.1:* financialauthority:; frame-ancestors 'none'; base-uri 'none'"

API_ONLY = [(b"cross-origin-resource-policy", b"same-origin")]
OTHER_SITE = (b"same-site", b"cross-site")


def other_site_subresource(scope: dict) -> bool:
    """A request another site's page made as a subresource: anything but CORS (which decides for itself who reads)
    and a top-level page load (a link followed, which the browser shows to the owner, never to the page)."""
    fetch = {k: v for k, v in scope.get("headers", []) if k in (b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest")}
    if fetch.get(b"sec-fetch-site") not in OTHER_SITE:
        return False
    mode, dest = fetch.get(b"sec-fetch-mode", b""), fetch.get(b"sec-fetch-dest", b"")
    if mode == b"cors":
        return False
    if mode == b"navigate" and dest in (b"document", b""):
        return False
    return True


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
        api = path.startswith("/api/")
        if api and other_site_subresource(scope):
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), *COMMON, *API_ONLY]})
            await send({"type": "http.response.body", "body": b'{"detail":"Another site asked for this; it answers the app only"}'})
            return
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
                # A route that sets its own policy (the printable invoice: no script at all) keeps it; the page policy
                # is added only where no route spoke.
                own_csp = [(k, v) for k, v in message.get("headers", []) if k.lower() == b"content-security-policy"][:1]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in {h for h, _ in COMMON} and k.lower() != b"content-security-policy"]
                headers += COMMON
                if api:
                    headers = [(k, v) for k, v in headers if k.lower() != b"cross-origin-resource-policy"] + API_ONLY
                if https:
                    headers.append((b"strict-transport-security", b"max-age=15552000; includeSubDomains"))
                if own_csp:
                    headers += own_csp
                elif path.startswith("/oauth/"):
                    headers.append((b"content-security-policy", OAUTH_CSP.encode()))
                elif not path.startswith(("/api/", "/mcp", "/.well-known/")):
                    headers.append((b"content-security-policy", APP_CSP.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, counted_receive, wrapped)
