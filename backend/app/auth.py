"""The owner's sign-in (a signed cookie naming a session the owner can end), lockout, CSRF, and scoped API keys
for models and scripts.

Scopes: read (every read tool), confirm (facts.confirm), approve (sends), admin (keys, settings, jobs).
Keys and connected apps hold read and confirm at most: never approve, never admin, so only a person, signed
in, changes settings. The owner's session carries every scope. A second person (people.py) signs in the same
way: with the role "everything" they hold every scope and approve only their own sends; with "shared" they
reach one page, the shared accounts.
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


def open_install_problem() -> str | None:
    """Why this copy must not start: FA_PASSWORD empty turns sign-in off, which is for a developer's machine only. An
    installed copy (FA_INSTALL says how: boxpilot, compose, docker) would answer anyone who reaches its port as the
    owner, so it refuses to start that way. The demo holds nothing real and may."""
    if auth_enabled() or settings.demo or not settings.install:
        return None
    return (f"FA_PASSWORD is empty, so sign-in would be off on this {settings.install} install, and anyone who reaches the port would be the owner. "
            "Set FA_PASSWORD (compose: in .env; BoxPilot generates it), or FA_DEMO=true to look around with the example household.")


def _serializer() -> URLSafeTimedSerializer:
    secret = settings.signing_secret()
    return URLSafeTimedSerializer(secret, salt="fa-session-v1")


@dataclass
class Principal:
    kind: str  # owner | person | key
    name: str
    scopes: set[str] = field(default_factory=set)
    redaction: str = "full"
    via_cookie: bool = False
    person_id: int | None = None  # the second person, when kind is person
    role: str = ""  # a person's role: everything | shared

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes and scope != "approve"

    @property
    def full(self) -> bool:
        """A person signed in who may do what the owner does (the owner, or a second person with everything)."""
        return self.kind == "owner" or (self.kind == "person" and self.role == "everything")


# What a "shared accounts only" sign-in may reach through require_scope; its own sign-in settings live under
# /api/auth and check for themselves.
SHARED_PATHS = {"/api/v1/shared", "/api/v1/about", "/api/v1/appearance"}


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


def _bucket(username: str) -> str:
    """The failures counted against one username, from any address."""
    return "u:" + (username or "").strip().lower()


def _record_failure(ip: str, now: float | None = None, username: str | None = None) -> None:
    """A wrong try: counted against the address, and against the username it named (none for a link or a passkey)."""
    t = now or time.time()
    _failures.setdefault(ip, []).append(t)
    if username is not None:
        _failures.setdefault(_bucket(username), []).append(t)


def clear_failures(ip: str, username: str | None = None) -> None:
    """A success clears its own address and its own username's count, never another username's: one person signing
    in must not reset the wait on guesses at someone else's password."""
    _failures.pop(ip, None)
    if username is not None:
        _failures.pop(_bucket(username), None)


def _account_wait(username: str | None = None, now: float | None = None) -> float:
    """Seconds before the next sign-in to this username may be tried, whatever address it comes from: none for the
    first few failures, then doubling, at most a minute. Guessing stays hopeless; the person waits a minute at worst."""
    now = now or time.time()
    key = _bucket(username if username is not None else settings.username)
    recent = [t for t in _failures.get(key, []) if now - t < LOCK_SECONDS]
    _failures[key] = recent
    if len(recent) < 5:
        return 0.0
    wait = min(60.0, 2.0 ** (len(recent) - 5))
    return max(0.0, recent[-1] + wait - now)


def reset_lockouts() -> None:
    _failures.clear()


# ---- keys ----------------------------------------------------------------------------------


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_key(session: Session, name: str, scopes: set[str], redaction: str = "full", person_id: int | None = None) -> tuple[ApiKey, str]:
    if "approve" in scopes:
        raise ValueError("no key may hold the approve scope; approvals are the owner's")
    if "admin" in scopes:
        raise ValueError("no key may hold the admin scope; settings are changed by the owner, signed in")
    scopes = {s for s in scopes if s in KEY_SCOPES} or {"read"}
    token = "fa_" + secrets.token_urlsafe(32)
    key = ApiKey(name=name, prefix=token[:10], key_hash=hash_key(token), scopes=",".join(sorted(scopes)), redaction=redaction, person_id=person_id)
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
        # A key made by a second person is that person's credential: what it queues is theirs to approve, and
        # the owner's standing allowances never cover it.
        return Principal(kind="key", name=key.name, scopes=key.scope_set & KEY_SCOPES, redaction=key.redaction, person_id=key.person_id)
    if not auth_enabled():
        return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)
    row = session_row(request, session)
    if row is None:
        return None
    if row.person_id is not None:
        from .models import Person

        person = session.get(Person, row.person_id)
        if person is None or not person.active:
            return None
        scopes = set(ALL_SCOPES) if person.role == "everything" else {"read"}
        return Principal(kind="person", name=person.display_name or person.username, scopes=scopes, via_cookie=True, person_id=person.id, role=person.role)
    return Principal(kind="owner", name=settings.username, scopes=set(ALL_SCOPES), via_cookie=True)


def start_session(session: Session, request: Request, person=None) -> str:  # noqa: ANN001 - a Person, for the second person
    """Record a new signed-in browser and return the cookie that names it."""
    from .models import WebSession

    sid = secrets.token_urlsafe(32)
    session.add(WebSession(sid_hash=hash_key(sid), user_agent=(request.headers.get("user-agent") or "")[:200], ip=_client_ip(request)[:64], last_seen_at=utcnow(),
                           person_id=person.id if person is not None else None))
    if person is not None:
        from . import people

        person.last_sign_in_at = utcnow()
        session.add(person)
        session.commit()
        return _serializer().dumps({"u": person.username, "p": person.id, "sid": sid, "pw": people.password_mark(person), "iat": int(time.time())})
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
    if not data.get("sid"):
        return None
    if data.get("p") is not None:
        from . import people
        from .models import Person

        person = session.get(Person, int(data["p"]))
        if person is None or not person.active or data.get("u") != person.username or not person.password_hash or data.get("pw") != people.password_mark(person):
            return None
    elif data.get("u") != settings.username or data.get("pw") != _password_mark():
        return None
    row = session.exec(select(WebSession).where(WebSession.sid_hash == hash_key(data["sid"]))).first()
    if row is None or row.revoked_at is not None or row.person_id != data.get("p"):
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
        if principal.kind == "key" and request.url.path.startswith("/api/"):
            # Every REST call a key makes leaves a row, a refused one too (as a refused tool call does), so the log
            # shows a key asking for more than it holds.
            from . import services

            refusal = _key_refusal(principal, scope, request.url.path)
            services.audit(session, client=principal.name, client_kind="key", tool=f"rest {request.method} {request.url.path}"[:110] + ("_refused" if refusal else ""), args=dict(request.query_params), rows=0, redaction=principal.redaction)
            if refusal:
                raise HTTPException(403, refusal)
            return principal
        if scope == "approve" and principal.kind not in ("owner", "person"):
            raise HTTPException(403, "Only a person signed in can approve")
        if principal.role == "shared" and request.url.path.rstrip("/") not in SHARED_PATHS:
            raise HTTPException(403, "This sign-in shows the shared accounts only.")
        if not principal.has(scope):
            raise HTTPException(403, f"This needs the {scope} scope")
        if principal.via_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get(CSRF_HEADER, "") != CSRF_VALUE:
                raise HTTPException(403, "Missing the request header the app sends")
            if principal.kind == "person":
                from . import services

                services.audit(session, client=principal.name, client_kind="person", tool=f"rest {request.method} {request.url.path}"[:120], args=dict(request.query_params), rows=0)
        return principal

    return dependency


def _key_refusal(principal: Principal, scope: str, path: str) -> str | None:
    """Why a key may not make this REST call, or None: the same checks a sign-in gets, in the same order, plus the
    rule that a key which hides details reads through the tools, where the details are held back."""
    if scope == "approve":
        return "Only a person signed in can approve"
    if not principal.has(scope):
        return f"This needs the {scope} scope"
    if principal.redaction != "full" and path.rstrip("/") != "/api/v1/ha":
        return "This key hides some details, so it reads through the tools at /mcp, which hold them back; the REST routes show everything"
    return None


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
    wait = _account_wait(body.username)
    if wait > 0:
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {int(wait) + 1} seconds.")
    user_ok = hmac.compare_digest(body.username.encode(), settings.username.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), settings.password.encode())
    from .factors import second_step_on, ticket_for

    person = None
    if user_ok:
        from . import people

        people.check_nobody(body.password)  # the same work as a second person's check, so timing does not name the owner
    else:
        from . import people, services

        person = people.by_username(session, body.username)
        pass_ok = person is not None and people.check_password(person.password_hash, body.password)
        if person is None:
            people.check_nobody(body.password)
        if person is not None:
            services.audit(session, client=person.display_name or person.username, client_kind="person", tool="sign_in" if pass_ok else "sign_in_refused", rows=0)
        if not pass_ok:
            person = None
    if not ((user_ok or person is not None) and pass_ok):
        _record_failure(ip, username=body.username)
        raise HTTPException(401, "Wrong username or password")
    if second_step_on(session, person.id if person else None):
        # The password was right; the session waits for the code.
        return {"ok": False, "second_step": "code", "ticket": ticket_for(person)}
    clear_failures(ip, body.username)
    if person is not None:
        token = start_session(session, request, person)
        response.set_cookie(COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=request_is_https(request), path="/")
        return {"ok": True, "user": person.username, "kind": "person", "role": person.role}
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

    from .models import Person

    principal = principal_from_request(request, session)
    if principal is None or principal.kind not in ("owner", "person"):
        raise HTTPException(401, "Sign in")
    here = session_row(request, session)
    query = select(WebSession).where(WebSession.revoked_at == None)  # noqa: E711
    if principal.kind == "person":
        query = query.where(WebSession.person_id == principal.person_id)
    rows = session.exec(query.order_by(WebSession.id.desc())).all()
    names = {p.id: p.display_name or p.username for p in session.exec(select(Person)).all()}
    return {"sessions": [{"id": r.id, "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                          "user_agent": r.user_agent, "ip": r.ip, "this": bool(here and here.id == r.id),
                          "who": "you" if r.person_id == principal.person_id else names.get(r.person_id, "someone") if r.person_id else settings.username, "person_id": r.person_id} for r in rows]}


class EndIn(BaseModel):
    session_id: int | None = None
    all_others: bool = False


@router.post("/sessions/end")
def end_sessions(body: EndIn, request: Request, session: Session = Depends(get_session)) -> dict:
    """End one signed-in browser, or every one but this."""
    from .models import WebSession

    principal = principal_from_request(request, session)
    if principal is None or principal.kind not in ("owner", "person"):
        raise HTTPException(401, "Sign in")
    if principal.via_cookie and request.headers.get(CSRF_HEADER, "") != CSRF_VALUE:
        raise HTTPException(403, "Missing the request header the app sends")
    here = session_row(request, session)
    ended = 0
    query = select(WebSession).where(WebSession.revoked_at == None)  # noqa: E711
    if principal.kind == "person":
        query = query.where(WebSession.person_id == principal.person_id)  # a person ends only their own
    for r in session.exec(query).all():
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
    return {"kind": principal.kind, "name": principal.name, "scopes": sorted(principal.scopes), "auth_enabled": auth_enabled(), "person_id": principal.person_id,
            "role": principal.role or ("owner" if principal.kind == "owner" else ""), "owner": settings.username}


# ---- joining: the second person sets their own password from the owner's invitation ------------------------------


class JoinCheckIn(BaseModel):
    token: str


@router.post("/join/check")
def join_check(body: JoinCheckIn, request: Request, session: Session = Depends(get_session)) -> dict:
    """Who an invitation is for, so the page can greet them. Nothing secret."""
    from . import people

    ip = _client_ip(request)
    if _locked(ip):
        raise HTTPException(429, "Too many tries. Wait fifteen minutes.")
    person = people.by_invite(session, body.token)
    if person is None:
        _record_failure(ip)
        raise HTTPException(400, "This invitation has expired or was used already. Ask the person who sent it for a new one.")
    return {"username": person.username, "display_name": person.display_name or person.username, "role": person.role, "role_words": people.ROLES_FOR_THEM.get(person.role, "").format(owner=settings.username),
            "expires_at": person.invite_expires_at.isoformat() if person.invite_expires_at else None, "min_length": people.MIN_PASSWORD}


class JoinIn(BaseModel):
    token: str
    password: str


@router.post("/join")
def join(body: JoinIn, request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    """The invitation used once: the person's own password set, and the person signed in."""
    from . import people, services

    ip = _client_ip(request)
    if _locked(ip):
        raise HTTPException(429, "Too many tries. Wait fifteen minutes.")
    person = people.by_invite(session, body.token)
    if person is None:
        _record_failure(ip)
        raise HTTPException(400, "This invitation has expired or was used already. Ask the person who sent it for a new one.")
    problem = people.password_problem(person.username, body.password)
    if problem:
        raise HTTPException(400, problem)
    person = people.claim_invite(session, body.token, people.hash_password(body.password))  # used once: a second redemption at the same moment finds it gone
    if person is None:
        _record_failure(ip)
        raise HTTPException(400, "This invitation has expired or was used already. Ask the person who sent it for a new one.")
    services.audit(session, client=person.display_name or person.username, client_kind="person", tool="joined", rows=0)
    clear_failures(ip)
    token = start_session(session, request, person)
    response.set_cookie(COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=request_is_https(request), path="/")
    return {"ok": True, "user": person.username, "role": person.role}
