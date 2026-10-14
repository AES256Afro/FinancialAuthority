"""The app as its own OAuth 2.1 authorization server, for one owner.

claude.ai, Claude Code, Open WebUI and any MCP client register themselves, send the owner to the consent page,
and come back with a code they exchange for tokens. The MCP SDK handles the protocol endpoints; this module
holds the records and the consent page. Tokens are stored hashed; revoking a client revokes everything it holds.
API keys (fa_…) are accepted by the same verifier, so one bearer check serves both.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, OAuthAuthorizationServerProvider, RefreshToken, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlmodel import select

from . import auth, crypto
from .config import settings
from .db import session_scope
from .models import OAuthClient, OAuthCode, OAuthToken as TokenRow, utcnow

ACCESS_SECONDS = 24 * 3600
REFRESH_SECONDS = 90 * 24 * 3600
CODE_SECONDS = 600
VALID_SCOPES = ["read", "confirm"]


def issuer() -> str:
    return (settings.public_url or f"http://localhost:{settings.port}").rstrip("/")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret or "insecure-development-secret", salt="fa-oauth-consent")


def _client_info(row: OAuthClient) -> OAuthClientInformationFull:
    raw = row.registration
    if crypto.is_encrypted(raw):
        raw = crypto.decrypt(raw)
    return OAuthClientInformationFull.model_validate_json(raw)


class AuthorityProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with session_scope() as session:
            row = session.exec(select(OAuthClient).where(OAuthClient.client_id == client_id)).first()
            if row is None or row.revoked_at is not None:
                return None
            return _client_info(row)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raw = client_info.model_dump_json(exclude_none=True)
        stored = crypto.encrypt(raw) if settings.data_key else raw
        with session_scope() as session:
            session.add(OAuthClient(
                client_id=client_info.client_id, client_secret_hash=_hash(client_info.client_secret) if client_info.client_secret else "",
                name=(client_info.client_name or "an app")[:120], redirect_uris=json.dumps([str(u) for u in (client_info.redirect_uris or [])]),
                scopes=client_info.scope or "read", token_endpoint_auth_method=client_info.token_endpoint_auth_method or "none", registration=stored,
            ))
            session.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        req = _signer().dumps({
            "client_id": client.client_id, "redirect_uri": str(params.redirect_uri), "explicit": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge, "scopes": params.scopes or ["read"], "state": params.state, "resource": params.resource, "iat": int(time.time()),
        })
        return f"{issuer()}/oauth/consent?{urlencode({'req': req})}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with session_scope() as session:
            row = session.exec(select(OAuthCode).where(OAuthCode.code_hash == _hash(authorization_code))).first()
            if row is None or row.used or row.client_id != client.client_id or row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                return None
            return AuthorizationCode(
                code=authorization_code, scopes=row.scopes.split(), expires_at=row.expires_at.replace(tzinfo=timezone.utc).timestamp(), client_id=row.client_id,
                code_challenge=row.code_challenge, redirect_uri=AnyUrl(row.redirect_uri), redirect_uri_provided_explicitly=row.redirect_uri_explicit, resource=row.resource, subject="owner",
            )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        with session_scope() as session:
            row = session.exec(select(OAuthCode).where(OAuthCode.code_hash == _hash(authorization_code.code))).first()
            if row is not None:
                row.used = True
                session.add(row)
            tokens = _issue(session, client.client_id, authorization_code.scopes, authorization_code.resource)
            session.commit()
            return tokens

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with session_scope() as session:
            row = session.exec(select(TokenRow).where(TokenRow.token_hash == _hash(refresh_token), TokenRow.kind == "refresh")).first()
            if row is None or row.revoked_at is not None or row.client_id != client.client_id or (row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)):
                return None
            return RefreshToken(token=refresh_token, client_id=row.client_id, scopes=row.scopes.split(), expires_at=int(row.expires_at.replace(tzinfo=timezone.utc).timestamp()) if row.expires_at else None, resource=row.resource, subject="owner")

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        with session_scope() as session:
            old = session.exec(select(TokenRow).where(TokenRow.token_hash == _hash(refresh_token.token))).first()
            if old is not None:
                old.revoked_at = utcnow()
                session.add(old)
            granted = [s for s in (scopes or refresh_token.scopes) if s in refresh_token.scopes] or refresh_token.scopes
            tokens = _issue(session, client.client_id, granted, refresh_token.resource)
            session.commit()
            return tokens

    async def load_access_token(self, token: str) -> AccessToken | None:
        with session_scope() as session:
            if not token.startswith("fa_oa_"):
                key = auth.lookup_key(session, token)
                if key is None:
                    return None
                return AccessToken(token=token, client_id=key.name, scopes=sorted(key.scope_set), expires_at=None, resource=issuer(), subject="owner", claims={"redaction": key.redaction, "kind": "key"})
            row = session.exec(select(TokenRow).where(TokenRow.token_hash == _hash(token), TokenRow.kind == "access")).first()
            if row is None or row.revoked_at is not None or (row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)):
                return None
            client = session.exec(select(OAuthClient).where(OAuthClient.client_id == row.client_id)).first()
            if client is None or client.revoked_at is not None:
                return None
            row.last_used_at = utcnow()
            client.last_used_at = utcnow()
            session.add_all([row, client])
            session.commit()
            return AccessToken(token=token, client_id=client.name or row.client_id, scopes=row.scopes.split(), expires_at=int(row.expires_at.replace(tzinfo=timezone.utc).timestamp()) if row.expires_at else None, resource=row.resource or issuer(), subject="owner", claims={"redaction": client.redaction, "kind": "oauth"})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with session_scope() as session:
            row = session.exec(select(TokenRow).where(TokenRow.token_hash == _hash(token.token))).first()
            if row is not None:
                row.revoked_at = utcnow()
                session.add(row)
                session.commit()


def _issue(session, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:  # noqa: ANN001
    access, refresh = "fa_oa_" + secrets.token_urlsafe(32), "fa_or_" + secrets.token_urlsafe(32)
    now = utcnow()
    resource = resource or issuer()  # a token is for this app; the SDK refuses one bound to nothing
    session.add(TokenRow(token_hash=_hash(access), kind="access", client_id=client_id, scopes=" ".join(scopes), resource=resource, expires_at=now + timedelta(seconds=ACCESS_SECONDS)))
    session.add(TokenRow(token_hash=_hash(refresh), kind="refresh", client_id=client_id, scopes=" ".join(scopes), resource=resource, expires_at=now + timedelta(seconds=REFRESH_SECONDS)))
    return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_SECONDS, scope=" ".join(scopes), refresh_token=refresh)


def revoke_client(session, client_id: str) -> int:  # noqa: ANN001
    """Revoke a client and every token it holds. Returns the number of tokens revoked."""
    row = session.exec(select(OAuthClient).where(OAuthClient.client_id == client_id)).first()
    if row is None:
        return 0
    row.revoked_at = utcnow()
    session.add(row)
    count = 0
    for token in session.exec(select(TokenRow).where(TokenRow.client_id == client_id, TokenRow.revoked_at == None)).all():  # noqa: E711
        token.revoked_at = utcnow()
        session.add(token)
        count += 1
    session.commit()
    return count


# ---- the consent page ----------------------------------------------------------------------

router = APIRouter(tags=["oauth"])

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="icon" href="/favicon.svg">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f4ef;color:#141518;font-family:Manrope,system-ui,sans-serif;font-size:15px;line-height:1.45}}
.card{{background:#fff;border:1px solid #e4e1da;border-radius:16px;padding:24px;width:min(100% - 32px,420px);display:flex;flex-direction:column;gap:12px;box-sizing:border-box}}
h1{{margin:0;font-size:20px;font-weight:800}}label{{display:flex;flex-direction:column;gap:4px;font-size:11px;font-weight:800;color:#85878f;text-transform:uppercase;letter-spacing:.06em}}
input[type=text],input[type=password]{{font:inherit;font-size:15px;padding:10px 12px;border-radius:10px;border:1px solid #d9d6ce;min-height:44px;box-sizing:border-box}}
.row{{display:flex;gap:8px;flex-wrap:wrap}}button{{font:inherit;font-weight:800;font-size:14px;border-radius:12px;padding:11px 16px;border:1px solid #d9d6ce;background:#fff;color:#141518;cursor:pointer;min-height:44px}}
button.dark{{background:#141518;color:#fff;border-color:#141518}}.muted{{font-size:12px;color:#4e5058}}.scope{{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:600;text-transform:none;letter-spacing:0;color:#141518;min-height:40px}}
.err{{color:#d03b3b;font-size:13px}}</style></head><body><form class="card" method="post" action="/oauth/consent">
<div style="display:flex;align-items:center;gap:10px"><img src="/favicon.svg" width="28" height="28" alt=""><strong style="font-size:17px">FinancialAuthority</strong></div>
{body}</form></body></html>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body))


def _load_request(req: str) -> dict | None:
    try:
        return _signer().loads(req, max_age=CODE_SECONDS)
    except BadSignature:
        return None


def _consent_body(data: dict, req: str, client_name: str, error: str = "") -> str:
    scopes = data.get("scopes") or ["read"]
    boxes = "".join(
        f'<label class="scope"><input type="checkbox" name="scope" value="{s}" {"checked" if s == "read" else ""} {"disabled" if s == "read" else ""}>'
        f"{'Read the money: balances, bills, the plan, the mail' if s == 'read' else 'Confirm facts on your behalf (bills from mail, recurring charges)'}</label>"
        for s in scopes if s in VALID_SCOPES
    )
    return (
        f"<h1>Let {html.escape(client_name)} in?</h1>"
        f'<div class="muted">It will read through the app as you, with what you tick below. It can never approve a send or move money. You can revoke it any time on Settings › Clients.</div>'
        f'<input type="hidden" name="req" value="{html.escape(req)}"><input type="hidden" name="scope" value="read">{boxes}'
        f'{f"<div class=err>{html.escape(error)}</div>" if error else ""}'
        f'<div class="row"><button class="dark" type="submit" name="decision" value="allow">Allow</button><button type="submit" name="decision" value="deny">Deny</button></div>'
    )


def _signin_body(req: str, error: str = "") -> str:
    return (
        "<h1>Sign in to continue</h1><div class=\"muted\">An app is asking to connect. Sign in with the owner's password first.</div>"
        f'<input type="hidden" name="req" value="{html.escape(req)}">'
        '<label>Username<input type="text" name="username" value="owner" autocomplete="username"></label>'
        '<label>Password<input type="password" name="password" autocomplete="current-password"></label>'
        f'{f"<div class=err>{html.escape(error)}</div>" if error else ""}'
        '<div class="row"><button class="dark" type="submit" name="decision" value="signin">Sign in</button></div>'
    )


async def _client_name(client_id: str) -> str:
    info = await provider.get_client(client_id)
    return (info.client_name if info and info.client_name else "an app")


@router.get("/oauth/consent", response_class=HTMLResponse)
async def consent_get(request: Request, req: str = "") -> HTMLResponse:
    data = _load_request(req)
    if data is None:
        return _page("Expired", "<h1>That request has expired</h1><div class=\"muted\">Start the connection again from the app that asked.</div>")
    with session_scope() as session:
        principal = auth.principal_from_request(request, session)
    if principal is None or principal.kind != "owner":
        return _page("Sign in", _signin_body(req))
    return _page("Allow access", _consent_body(data, req, await _client_name(data["client_id"])))


@router.post("/oauth/consent")
async def consent_post(request: Request, req: str = Form(""), decision: str = Form(""), username: str = Form(""), password: str = Form(""), scope: list[str] | None = Form(None)):  # noqa: ANN201
    data = _load_request(req)
    if data is None:
        return _page("Expired", "<h1>That request has expired</h1><div class=\"muted\">Start the connection again from the app that asked.</div>")
    with session_scope() as session:
        principal = auth.principal_from_request(request, session)
    if decision == "signin":
        ip = auth._client_ip(request)
        if auth._locked(ip):
            return _page("Sign in", _signin_body(req, "Too many failed sign-ins. Try again in fifteen minutes."))
        ok = auth.auth_enabled() and hmac.compare_digest(username.encode(), settings.username.encode()) and hmac.compare_digest(password.encode(), settings.password.encode())
        if not ok and auth.auth_enabled():
            auth._record_failure(ip)
            return _page("Sign in", _signin_body(req, "Wrong username or password."))
        response = _page("Allow access", _consent_body(data, req, await _client_name(data["client_id"])))
        token = auth._serializer().dumps({"u": settings.username, "iat": int(time.time())})
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=settings.public_url.startswith("https://"), path="/")
        return response
    if principal is None or principal.kind != "owner":
        return _page("Sign in", _signin_body(req))
    redirect_uri = data["redirect_uri"]
    if decision != "allow":
        return RedirectResponse(construct_redirect_uri(redirect_uri, error="access_denied", state=data.get("state")), status_code=302)
    granted = [s for s in (scope or []) if s in VALID_SCOPES and s in (data.get("scopes") or ["read"])] or ["read"]
    if "read" not in granted:
        granted.insert(0, "read")
    code = "fa_oc_" + secrets.token_urlsafe(32)
    with session_scope() as session:
        session.add(OAuthCode(
            code_hash=_hash(code), client_id=data["client_id"], redirect_uri=redirect_uri, redirect_uri_explicit=bool(data.get("explicit", True)), code_challenge=data["code_challenge"],
            scopes=" ".join(dict.fromkeys(granted)), resource=data.get("resource"), expires_at=utcnow() + timedelta(seconds=CODE_SECONDS),
        ))
        session.commit()
    return RedirectResponse(construct_redirect_uri(redirect_uri, code=code, state=data.get("state")), status_code=302)


provider = AuthorityProvider()
