"""The owner's sign-in (a signed cookie naming a session the owner can end), lockout, CSRF, and scoped API keys
for models and scripts.

Scopes: read (every read tool), confirm (facts.confirm), approve (sends), admin (keys, settings, jobs).
Keys and connected apps hold read and confirm at most: never approve, never admin, so only the owner, signed
in, changes settings. The owner's session carries every scope.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .models import ApiKey, utcnow

log = logging.getLogger("fa.auth")

COOKIE = "fa_session"
SESSION_MAX_AGE = 30 * 24 * 3600
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "FinancialAuthority"
ALL_SCOPES = {"read", "confirm", "approve", "admin"}
KEY_SCOPES = {"read", "confirm"}
LOCK_AFTER = 10
LOCK_SECONDS = 15 * 60
SEEN_EVERY = 300  # a session's last-seen time is written at most this often

_failures: dict[str, list[float]] = {}


def auth_enabled() -> bool:
    return bool(settings.password)


def _serializer() -> URLSafeTimedSerializer:
    secret = settings.signing_secret()
    return URLSafeTimedSerializer(secret, salt="fa-session-v1")


@dataclass
class Principal:
    kind: str  # owner | key
    name: str
    scopes: set[str] = field(default_factory=set)
    redaction: str = "full"
    via_cookie: bool = False

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes and scope != "approve"


# ---- lockout -------------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """The caller's address. A forwarding header is believed only from a proxy the owner named in
    FA_TRUSTED_PROXIES (the tunnel's address), because anyone can send one."""
    import ipaddress

    peer = request.client.host if request.client else "unknown"
    trusted = [p.strip() for p in settings.trusted_proxies.split(",") if p.strip()]
    if trusted:
        try:
            address = ipaddress.ip_address(peer)
            nets = [ipaddress.ip_network(p, strict=False) for p in trusted]
            if any(address in n for n in nets):
                # Each proxy appends who it heard from; the caller is the last address no trusted proxy owns.
                chain = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
                for hop in reversed(chain):
                    try:
                        if not any(ipaddress.ip_address(hop) in n for n in nets):
                            return hop
                    except ValueError:
                        return hop
                cf = request.headers.get("cf-connecting-ip", "").strip()
                if cf:
                    return cf
        except ValueError:
            pass
    return peer


def _locked(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCK_SECONDS]
    _failures[ip] = recent
    return len(recent) >= LOCK_AFTER


def _record_failure(ip: str, now: float | None = None) -> None:
    _failures.setdefault(ip, []).append(now or time.time())
    _failures.setdefault("*", []).append(now or time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)
    _failures.pop("*", None)


def _account_wait(now: float | None = None) -> float:
    """Seconds before the next sign-in may be tried, whatever address it comes from: none for the first few
    failures, then doubling, at most a minute. Guessing stays hopeless; the owner waits a minute at worst."""
    now = now or time.time()
    recent = [t for t in _failures.get("*", []) if now - t < LOCK_SECONDS]
    _failures["*"] = recent
    if len(recent) < 5:
        return 0.0
    wait = min(60.0, 2.0 ** (len(recent) - 5))
    return max(0.0, recent[-1] + wait - now)


def reset_lockouts() -> None:
    _failures.clear()


# ---- keys ----------------------------------------------------------------------------------


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_key(session: Session, name: str, scopes: set[str], redaction: str = "full") -> tuple[ApiKey, str]:
    if "approve" in scopes:
        raise ValueError("no key may hold the approve scope; approvals are the owner's")
    if "admin" in scopes:
        raise ValueError("no key may hold the admin scope; settings are changed by the owner, signed in")
    scopes = {s for s in scopes if s in KEY_SCOPES} or {"read"}
    token = "fa_" + secrets.token_urlsafe(32)
    key = ApiKey(name=name, prefix=token[:10], key_hash=hash_key(token), scopes=",".join(sorted(scopes)), redaction=redaction)
    session.add(key)
    session.commit()
    session.refresh(key)
    return key, token


def lookup_key(session: Session, token: str) -> ApiKey | None:
    if not token.startswith("fa_"):
        return None
    key = session.exec(select(ApiKey).where(ApiKey.key_hash == hash_key(token))).first()
    if key is None or key.revoked_at is not None:
        return None
    key.last_used_at = utcnow()
    session.add(key)
    session.commit()
    return key


# ---- principals ----------------------------------------------------------------------------


def principal_from_oauth(session: Session, token: str) -> Principal | None:
    """An OAuth access token issued by the app's own authorization server (oauth.py)."""
    from datetime import timezone

    from .models import OAuthClient, OAuthToken

    row = session.exec(select(OAuthToken).where(OAuthToken.token_hash == hash_key(token), OAuthToken.kind == "access")).first()
    if row is None or row.revoked_at is not None or (row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < utcnow()):
        return None
    client = session.exec(select(OAuthClient).where(OAuthClient.client_id == row.client_id)).first()
    if client is None or client.revoked_at is not None:
        return None
    row.last_used_at = utcnow()
    session.add(row)
    session.commit()
    return Principal(kind="key", name=client.name or row.client_id, scopes=set(row.scopes.split()) & KEY_SCOPES, redaction=client.redaction)


def principal_from_request(request: Request, session: Session) -> Principal | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token.startswith("fa_oa_"):
            return principal_from_oauth(session, token)
        key = lookup_key(session, token)
        if key is None:
            return None
        return Principal(kind="key", name=key.name, scopes=key.scope_set & KEY_SCOPES, redaction=key.redaction)
    if not auth_enabled():
        return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)
    row = session_row(request, session)
    if row is None:
        return None
    return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)


def start_session(session: Session, request: Request) -> str:
    """Record a new signed-in browser and return the cookie that names it."""
    from .models import WebSession

    sid = secrets.token_urlsafe(32)
    session.add(WebSession(sid_hash=hash_key(sid), user_agent=(request.headers.get("user-agent") or "")[:200], ip=_client_ip(request)[:64], last_seen_at=utcnow()))
    session.commit()
    return _serializer().dumps({"u": settings.username, "sid": sid, "pw": _password_mark(), "iat": int(time.time())})


def _password_mark() -> str:
    """Changes when FA_PASSWORD changes, so a new password ends every session."""
    return hashlib.sha256((settings.signing_secret() + "\x00" + settings.password).encode()).hexdigest()[:16]


def session_row(request: Request, session: Session):  # noqa: ANN201
    """The signed-in session this request's cookie names, if it is still open."""
    from .models import WebSession

    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    if data.get("u") != settings.username or data.get("pw") != _password_mark() or not data.get("sid"):
        return None
    row = session.exec(select(WebSession).where(WebSession.sid_hash == hash_key(data["sid"]))).first()
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    seen = row.last_seen_at.replace(tzinfo=now.tzinfo) if row.last_seen_at and row.last_seen_at.tzinfo is None else row.last_seen_at
    if seen is None or (now - seen).total_seconds() > SEEN_EVERY:
        row.last_seen_at = now
        session.add(row)
        session.commit()
    return row


def require_scope(scope: str):
    def dependency(request: Request, session: Session = Depends(get_session)) -> Principal:
        principal = principal_from_request(request, session)
        if principal is None:
            raise HTTPException(401, "Sign in, or send an API key")
        if scope == "approve" and principal.kind != "owner":
            raise HTTPException(403, "Only the owner can approve")
        if not principal.has(scope):
            raise HTTPException(403, f"This needs the {scope} scope")
        if principal.via_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get(CSRF_HEADER, "") != CSRF_VALUE:
                raise HTTPException(403, "Missing the request header the app sends")
        if principal.kind == "key" and request.url.path.startswith("/api/"):
            if principal.redaction != "full" and request.url.path.rstrip("/") != "/api/v1/ha":
                raise HTTPException(403, "This key hides some details, so it reads through the tools at /mcp, which hold them back; the REST routes show everything")
            from . import services

            services.audit(session, client=principal.name, client_kind="key", tool=f"rest {request.method} {request.url.path}"[:120], args=dict(request.query_params), rows=0, redaction=principal.redaction)
        return principal

    return dependency


require_read = require_scope("read")
require_admin = require_scope("admin")
require_owner = require_scope("approve")


# ---- routes --------------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    from .models import WebSession

    if not auth_enabled():
        return {"ok": True, "user": settings.username, "note": "sign-in is off: FA_PASSWORD is not set"}
    ip = _client_ip(request)
    if _locked(ip):
        raise HTTPException(429, "Too many failed sign-ins. Try again in fifteen minutes.")
    wait = _account_wait()
    if wait > 0:
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {int(wait) + 1} seconds.")
    user_ok = hmac.compare_digest(body.username.encode(), settings.username.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), settings.password.encode())
    if not (user_ok and pass_ok):
        _record_failure(ip)
        raise HTTPException(401, "Wrong username or password")
    from .factors import second_step_on, ticket_for

    if second_step_on(session):
        # The password was right; the session waits for the code.
        return {"ok": False, "second_step": "code", "ticket": ticket_for()}
    clear_failures(ip)
    token = start_session(session, request)
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        path="/",
    )
    return {"ok": True, "user": settings.username}


def request_is_https(request: Request) -> bool:
    """Whether this request arrived over https, directly or through a proxy that says so. The session cookie
    is marked Secure only then, so the same app signs in over plain http inside the tailnet and over https
    through a tunnel, whatever the published address says."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


@router.post("/logout")
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    row = session_row(request, session)
    if row is not None:
        row.revoked_at = utcnow()
        session.add(row)
        session.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/sessions")
def list_sessions(request: Request, session: Session = Depends(get_session)) -> dict:
    """Every browser signed in as the owner, the one asking marked."""
    from .models import WebSession

    principal = principal_from_request(request, session)
    if principal is None or principal.kind != "owner":
        raise HTTPException(401, "Sign in")
    here = session_row(request, session)
    rows = session.exec(select(WebSession).where(WebSession.revoked_at == None).order_by(WebSession.id.desc())).all()  # noqa: E711
    return {"sessions": [{"id": r.id, "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                          "user_agent": r.user_agent, "ip": r.ip, "this": bool(here and here.id == r.id)} for r in rows]}


class EndIn(BaseModel):
    session_id: int | None = None
    all_others: bool = False


@router.post("/sessions/end")
def end_sessions(body: EndIn, request: Request, session: Session = Depends(get_session)) -> dict:
    """End one signed-in browser, or every one but this."""
    from .models import WebSession

    principal = principal_from_request(request, session)
    if principal is None or principal.kind != "owner":
        raise HTTPException(401, "Sign in")
    if principal.via_cookie and request.headers.get(CSRF_HEADER, "") != CSRF_VALUE:
        raise HTTPException(403, "Missing the request header the app sends")
    here = session_row(request, session)
    ended = 0
    for r in session.exec(select(WebSession).where(WebSession.revoked_at == None)).all():  # noqa: E711
        if (body.all_others and (here is None or r.id != here.id)) or (body.session_id is not None and r.id == body.session_id):
            r.revoked_at = utcnow()
            session.add(r)
            ended += 1
    session.commit()
    return {"ended": ended}


def note_public_sighting(request: Request, session: Session) -> None:
    """A signed-in page load that arrived at the published address is proof from outside that the address
    answers, which the self-check cannot get from inside the container (a tailnet only answers its peers)."""
    if not settings.public_url:
        return
    from datetime import datetime
    from urllib.parse import urlparse

    from .store import get_setting, set_setting

    wanted = (urlparse(settings.public_url).hostname or "").lower()
    seen_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
    if not wanted or seen_host.rsplit(":", 1)[0].strip("[]") != wanted:
        return
    sighting = get_setting(session, "public.last_seen", {}) or {}
    now = utcnow()
    try:
        if sighting.get("at") and (now - datetime.fromisoformat(sighting["at"])).total_seconds() < 600:
            return
    except ValueError:
        pass
    set_setting(session, "public.last_seen", {"at": now.isoformat(), "host": seen_host})


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)) -> dict:
    principal = principal_from_request(request, session)
    if principal is None:
        raise HTTPException(401, "Not signed in")
    note_public_sighting(request, session)
    return {"kind": principal.kind, "name": principal.name, "scopes": sorted(principal.scopes), "auth_enabled": auth_enabled()}
