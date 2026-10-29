"""The app as its own OAuth 2.1 authorization server, for one owner.

claude.ai, Claude Code, Open WebUI and any MCP client register themselves, send the owner to the consent page,
and come back with a code they exchange for tokens. The MCP SDK handles the protocol endpoints; this module
holds the records and the consent page. Tokens are stored hashed; revoking a client revokes everything it holds.
API keys (fa_…) are accepted by the same verifier, so one bearer check serves both.
"""

from __future__ import annotations

import logging
import hashlib
import hmac
import html
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, OAuthAuthorizationServerProvider, RefreshToken, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlmodel import select

from . import auth, crypto
from .config import settings

log = logging.getLogger("fa.oauth")
from .db import session_scope
from .models import OAuthClient, OAuthCode, OAuthToken as TokenRow, utcnow

REUSE_GRACE = 60  # seconds a spent refresh token may be retried before it counts as stolen
ACCESS_SECONDS = 24 * 3600
REFRESH_SECONDS = 90 * 24 * 3600
CODE_SECONDS = 600
VALID_SCOPES = ["read", "confirm"]
BAD_REDIRECT_SCHEMES = {"", "javascript", "data", "vbscript", "file", "blob", "about"}


def issuer() -> str:
    """The OAuth issuer: the published https address, or the loopback. An http address that is not the
    loopback cannot be an issuer (RFC 8414 wants https, and the MCP SDK refuses to start with one), so the
    app keeps running on the loopback issuer and says so once; the REST, the screens and API keys are unaffected."""
    public = (settings.public_url or "").rstrip("/")
    if public:
        host = urlparse(public).hostname or ""
        if public.startswith("https://") or host in ("localhost", "127.0.0.1", "::1"):
            return public
        if not _warned_issuer:
            _warned_issuer.append(public)
            log.warning("FA_PUBLIC_URL %s is not https, so it cannot be the OAuth issuer; connected apps sign in only once an https address is set. API keys work regardless.", public)
    return f"http://localhost:{settings.port}"


_warned_issuer: list[str] = []


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.signing_secret(), salt="fa-oauth-consent")


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
        from datetime import timedelta as _td

        from mcp.server.auth.provider import RegistrationError

        from sqlalchemy import func

        uris = [str(u) for u in (client_info.redirect_uris or [])]
        if len(uris) > 5 or any(len(u) > 512 for u in uris) or len(client_info.client_name or "") > 120:
            raise RegistrationError(error="invalid_client_metadata", error_description="At most five redirect addresses of 512 characters, and a name of 120")
        # The browser is sent to the callback after Allow: a web address, a local http one, or an app's own
        # scheme. A script, a data address or a file is never a place to send anyone.
        if any((urlparse(u).scheme or "").lower() in BAD_REDIRECT_SCHEMES for u in uris):
            raise RegistrationError(error="invalid_redirect_uri", error_description="A redirect address is https, http on this computer, or the app's own scheme")
        with session_scope() as session:
            # An app that registered and was never allowed within the hour is forgotten; it holds nothing.
            unused = session.exec(select(OAuthClient).where(OAuthClient.created_at < utcnow() - _td(hours=1), OAuthClient.last_used_at == None, OAuthClient.revoked_at == None)).all()  # noqa: E711
            held = {t.client_id for t in session.exec(select(OAuthToken).where(OAuthToken.client_id.in_([c.client_id for c in unused]))).all()} if unused else set()
            for c in unused:
                if c.client_id not in held:
                    session.delete(c)
            session.commit()
            recent = session.exec(select(func.count()).select_from(OAuthClient).where(OAuthClient.created_at >= utcnow() - _td(days=1))).one()
            live = session.exec(select(func.count()).select_from(OAuthClient).where(OAuthClient.revoked_at == None)).one()  # noqa: E711
        if recent >= 20 or live >= 100:
            raise RegistrationError(error="invalid_client_metadata", error_description="Too many apps registered; revoke unused ones on Settings › Apps and keys, or try tomorrow")
        keep = {"client_id": client_info.client_id, "client_name": (client_info.client_name or "")[:120], "redirect_uris": uris, "grant_types": list(client_info.grant_types or [])[:4],
                "response_types": list(client_info.response_types or [])[:2], "token_endpoint_auth_method": client_info.token_endpoint_auth_method, "scope": (client_info.scope or "read")[:60],
                "client_id_issued_at": client_info.client_id_issued_at, "client_secret": client_info.client_secret}
        raw = json.dumps({k: v for k, v in keep.items() if v is not None})
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
        from mcp.server.auth.provider import TokenError
        from sqlalchemy import update

        with session_scope() as session:
            # The code is claimed in one statement: two exchanges in the same instant both passed the SDK's unused
            # check, and only the one that claims it here gets tokens.
            claimed = session.exec(update(OAuthCode).where(OAuthCode.code_hash == _hash(authorization_code.code), OAuthCode.used == False, OAuthCode.client_id == client.client_id).values(used=True))  # noqa: E712
            if claimed.rowcount == 1:
                tokens = _issue(session, client.client_id, authorization_code.scopes, authorization_code.resource)
                session.commit()
                return tokens
            session.rollback()
        raise TokenError("invalid_grant", "the authorization code was used already")  # raised outside the session: the SDK's error is a frozen dataclass

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with session_scope() as session:
            row = session.exec(select(TokenRow).where(TokenRow.token_hash == _hash(refresh_token), TokenRow.kind == "refresh")).first()
            if row is not None and row.revoked_at is not None and row.client_id == client.client_id:
                # A spent refresh token presented again: someone holds a copy. A retry within a minute of the
                # exchange is a lost reply, not a thief; after that, every token the client holds is ended.
                if (datetime.now(timezone.utc) - row.revoked_at.replace(tzinfo=timezone.utc)).total_seconds() > REUSE_GRACE:
                    for live in session.exec(select(TokenRow).where(TokenRow.client_id == client.client_id, TokenRow.revoked_at == None)).all():  # noqa: E711
                        live.revoked_at = utcnow()
                        session.add(live)
                    session.commit()
                    log.warning("a spent refresh token was used again; the client's tokens are revoked")
                return None
            if row is None or row.revoked_at is not None or row.client_id != client.client_id or (row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)):
                return None
            return RefreshToken(token=refresh_token, client_id=row.client_id, scopes=row.scopes.split(), expires_at=int(row.expires_at.replace(tzinfo=timezone.utc).timestamp()) if row.expires_at else None, resource=row.resource, subject="owner")

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        from mcp.server.auth.provider import TokenError
        from sqlalchemy import update

        with session_scope() as session:
            # The refresh token is spent in one statement, as the authorization code is: two refreshes in the same
            # instant both passed the load (the row was live for both), and only the one that spends it here gets a
            # new pair. The other is refused, and is the retry the reuse rule above forgives within its minute.
            spent = session.exec(update(TokenRow).where(TokenRow.token_hash == _hash(refresh_token.token), TokenRow.kind == "refresh", TokenRow.client_id == client.client_id, TokenRow.revoked_at == None).values(revoked_at=utcnow()))  # noqa: E711
            if spent.rowcount == 1:
                granted = [s for s in (scopes or refresh_token.scopes) if s in refresh_token.scopes] or refresh_token.scopes
                tokens = _issue(session, client.client_id, granted, refresh_token.resource)
                session.commit()
                return tokens
            session.rollback()
        raise TokenError("invalid_grant", "the refresh token was used already")

    async def load_access_token(self, token: str) -> AccessToken | None:
        with session_scope() as session:
            if not token.startswith("fa_oa_"):
                key = auth.lookup_key(session, token)
                if key is None:
                    return None
                # person_id names the second person who made the key, so a send it queues through a tool is theirs.
                # The subject names the key itself: the MCP transport ties a session to (client, issuer, subject), and key
                # names are not unique, so two keys called the same would otherwise share each other's sessions. The
                # prefix keeps a new key that reuses a deleted key's id and name out of the old key's sessions too.
                return AccessToken(token=token, client_id=key.name, scopes=sorted(key.scope_set & auth.KEY_SCOPES), expires_at=None, resource=issuer(), subject=f"key:{key.id}:{key.prefix}",
                                   claims={"redaction": key.redaction, "kind": "key", "name": key.name, "person_id": key.person_id})
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
            return AccessToken(token=token, client_id=row.client_id, scopes=row.scopes.split(), expires_at=int(row.expires_at.replace(tzinfo=timezone.utc).timestamp()) if row.expires_at else None, resource=row.resource or issuer(), subject="owner", claims={"redaction": client.redaction, "kind": "oauth", "name": client.name or row.client_id})

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
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f4ef;color:#141518;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:15px;line-height:1.45}}
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


KNOWN_HOSTS = ("claude.ai", "claude.com", "anthropic.com", "localhost", "127.0.0.1", "[::1]")


def _destination(data: dict) -> tuple[str, bool]:
    from urllib.parse import urlsplit

    host = (urlsplit(str(data.get("redirect_uri") or "")).hostname or "").lower()
    known = any(host == k or host.endswith("." + k) for k in KNOWN_HOSTS)
    return host or "an unknown address", known


def _consent_body(data: dict, req: str, client_name: str, error: str = "") -> str:
    scopes = data.get("scopes") or ["read"]
    host, known = _destination(data)
    where = (f'<div class="muted">After you allow it, you go back to <strong>{html.escape(host)}</strong>.</div>' if known else
             f'<div class="err">After you allow it, you go back to <strong>{html.escape(host)}</strong>, which is not Claude. Allow it only if you started this connection yourself, from an app you trust; the name above is chosen by whoever asked.</div>')
    boxes = "".join(
        f'<label class="scope"><input type="checkbox" name="scope" value="{s}" {"checked" if s == "read" else ""} {"disabled" if s == "read" else ""}>'
        f"{'Read the money: balances, bills, the plan, the mail' if s == 'read' else 'Confirm facts on your behalf (bills from mail, recurring charges)'}</label>"
        for s in scopes if s in VALID_SCOPES
    )
    return (
        f"<h1>Let {html.escape(client_name)} in?</h1>{where}"
        f'<div class="muted">It will read through the app as you, with what you tick below. It can never approve a send or move money. You can revoke it any time on Settings › Clients.</div>'
        f'<input type="hidden" name="req" value="{html.escape(req)}"><input type="hidden" name="scope" value="read">{boxes}'
        f'{f"<div class=err>{html.escape(error)}</div>" if error else ""}'
        f'<div class="row"><button class="dark" type="submit" name="decision" value="allow">Allow</button><button type="submit" name="decision" value="deny">Deny</button></div>'
    )


def _signin_body(req: str, error: str = "", code: bool = False) -> str:
    return (
        "<h1>Sign in to continue</h1><div class=\"muted\">An app is asking to connect. Sign in with the owner's password first.</div>"
        f'<input type="hidden" name="req" value="{html.escape(req)}">'
        '<label>Username<input type="text" name="username" value="owner" autocomplete="username"></label>'
        '<label>Password<input type="password" name="password" autocomplete="current-password"></label>'
        + ('<label>Code from your authenticator app (or a recovery code)<input type="text" name="code" inputmode="numeric" autocomplete="one-time-code"></label>' if code else "") +
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
        from .factors import second_step_on

        with session_scope() as session:
            needs_code = second_step_on(session)
        return _page("Sign in", _signin_body(req, code=needs_code))
    return _page("Allow access", _consent_body(data, req, await _client_name(data["client_id"])))


def _same_origin(request: Request) -> bool:
    """The form came from the app's own consent page: the browser says so, by Origin or Sec-Fetch-Site."""
    site = request.headers.get("sec-fetch-site", "")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin", "")
    if origin and origin != "null":
        from urllib.parse import urlsplit as _split

        here = _split(issuer())
        mine = {f"{here.scheme}://{here.netloc}", f"{request.url.scheme}://{request.url.netloc}"}
        forwarded = request.headers.get("x-forwarded-host")
        if forwarded:
            mine.add(f"https://{forwarded}")
        return origin in mine
    return True


@router.post("/oauth/consent")
async def consent_post(request: Request, req: str = Form(""), decision: str = Form(""), username: str = Form(""), password: str = Form(""), code: str = Form(""), scope: list[str] | None = Form(None)):  # noqa: ANN201
    if not _same_origin(request):
        return _page("Refused", "<h1>That form did not come from this app</h1><div class=\"muted\">Start the connection again from the app that asked.</div>")
    data = _load_request(req)
    if data is None:
        return _page("Expired", "<h1>That request has expired</h1><div class=\"muted\">Start the connection again from the app that asked.</div>")
    with session_scope() as session:
        principal = auth.principal_from_request(request, session)
    if decision == "signin":
        ip = auth._client_ip(request)
        if auth._locked(ip) or auth._account_wait(settings.username) > 0:
            return _page("Sign in", _signin_body(req, "Too many failed sign-ins. Wait a minute, then try again."))
        from .factors import check_code, second_step_on

        ok = auth.auth_enabled() and hmac.compare_digest(username.encode(), settings.username.encode()) and hmac.compare_digest(password.encode(), settings.password.encode())
        if not ok and auth.auth_enabled():
            auth._record_failure(ip, username=settings.username)
            with session_scope() as session:
                needs_code = second_step_on(session)
            return _page("Sign in", _signin_body(req, "Wrong username or password.", code=needs_code))
        with session_scope() as session:
            if auth.auth_enabled() and second_step_on(session) and not check_code(session, code):
                auth._record_failure(ip, username=settings.username)
                return _page("Sign in", _signin_body(req, "Type the code your authenticator app shows now, or a recovery code.", code=True))
        response = _page("Allow access", _consent_body(data, req, await _client_name(data["client_id"])))
        with session_scope() as session:
            token = auth.start_session(session, request)
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=auth.request_is_https(request), path="/")
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
