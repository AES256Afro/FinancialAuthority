"""The owner's sign-in (a signed cookie), lockout, CSRF, and scoped API keys for models and scripts.

Scopes: read (every read tool), confirm (facts.confirm), approve (sends), admin (keys, settings, jobs).
A model client gets read by default and never approve. The owner's session carries every scope.
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
LOCK_AFTER = 10
LOCK_SECONDS = 15 * 60

_failures: dict[str, list[float]] = {}


def auth_enabled() -> bool:
    return bool(settings.password)


def _serializer() -> URLSafeTimedSerializer:
    secret = settings.session_secret or "insecure-development-secret"
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
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _locked(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCK_SECONDS]
    _failures[ip] = recent
    return len(recent) >= LOCK_AFTER


def _record_failure(ip: str, now: float | None = None) -> None:
    _failures.setdefault(ip, []).append(now or time.time())


def reset_lockouts() -> None:
    _failures.clear()


# ---- keys ----------------------------------------------------------------------------------


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_key(session: Session, name: str, scopes: set[str], redaction: str = "full") -> tuple[ApiKey, str]:
    scopes = {s for s in scopes if s in ALL_SCOPES}
    if "approve" in scopes:
        raise ValueError("no key may hold the approve scope; approvals are the owner's")
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


def principal_from_request(request: Request, session: Session) -> Principal | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        key = lookup_key(session, header[7:].strip())
        if key is None:
            return None
        return Principal(kind="key", name=key.name, scopes=key.scope_set, redaction=key.redaction)
    if not auth_enabled():
        return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    if data.get("u") != settings.username:
        return None
    return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)


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
def login(body: LoginIn, request: Request, response: Response) -> dict:
    if not auth_enabled():
        return {"ok": True, "user": settings.username, "note": "sign-in is off: FA_PASSWORD is not set"}
    ip = _client_ip(request)
    if _locked(ip):
        raise HTTPException(429, "Too many failed sign-ins. Try again in fifteen minutes.")
    user_ok = hmac.compare_digest(body.username.encode(), settings.username.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), settings.password.encode())
    if not (user_ok and pass_ok):
        _record_failure(ip)
        raise HTTPException(401, "Wrong username or password")
    _failures.pop(ip, None)
    token = _serializer().dumps({"u": settings.username, "iat": int(time.time())})
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.public_url.startswith("https://"),
        path="/",
    )
    return {"ok": True, "user": settings.username}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)) -> dict:
    principal = principal_from_request(request, session)
    if principal is None:
        raise HTTPException(401, "Not signed in")
    return {"kind": principal.kind, "name": principal.name, "scopes": sorted(principal.scopes), "auth_enabled": auth_enabled()}
