"""A second step at sign-in, and passkeys.

An authenticator code (TOTP, the six digits an app such as Google Authenticator, 1Password or Aegis shows) can be
turned on; then the password alone no longer signs in, and ten one-time recovery codes are shown once. A code is
accepted once: the same six digits cannot be replayed within their minute.

A passkey signs in without the password: the phone or computer proves it holds the key (with a fingerprint, face
or PIN). Passkeys work at an https address or on this computer (localhost), because browsers allow them nowhere
else; each passkey belongs to the address it was made at.

The secret behind the codes is stored encrypted with the data key; recovery codes are stored hashed; a passkey
is stored as its public key only. Wrong codes count toward the same lockout as wrong passwords.

The owner and a second person each have their own: the owner's second step is kept in the settings, a person's on
their row; each passkey names whose it is."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, select

from .api.inputs import In
from . import auth, crypto
from .config import settings
from .db import get_session
from .models import Passkey, Person, utcnow
from .store import get_setting, set_setting

router = APIRouter(prefix="/api/auth", tags=["auth"])
TOTP_KEY = "auth.totp"
LAST_STEP_KEY = "auth.totp.last_step"
TICKET_SECONDS = 300
CHALLENGE_SECONDS = 300
_challenges: dict[str, tuple[float, bytes, str]] = {}


# ---- the authenticator code -------------------------------------------------------------------------------------


def totp_state(session: Session) -> dict[str, Any]:
    return get_setting(session, TOTP_KEY, {}) or {}


def _state(session: Session, person_id: int | None) -> dict[str, Any]:
    """The second step's state for the owner (person_id None) or a second person."""
    if person_id is None:
        return totp_state(session)
    import json

    p = session.get(Person, person_id)
    if p is None:
        return {}
    return {"enabled": bool(p.totp_enc), "secret_enc": p.totp_enc, "pending_enc": p.totp_pending_enc, "recovery": json.loads(p.totp_recovery or "[]"), "since": p.totp_since}


def _save_state(session: Session, person_id: int | None, state: dict[str, Any]) -> None:
    if person_id is None:
        set_setting(session, TOTP_KEY, state)
        return
    import json

    p = session.get(Person, person_id)
    p.totp_enc = state.get("secret_enc", "") if state.get("enabled") else ""
    p.totp_pending_enc = state.get("pending_enc", "")
    p.totp_recovery = json.dumps(state.get("recovery", []))
    p.totp_since = state.get("since", "") or ""
    session.add(p)
    session.commit()


def _last_step(session: Session, person_id: int | None) -> int:
    if person_id is None:
        return int(get_setting(session, LAST_STEP_KEY, 0) or 0)
    p = session.get(Person, person_id)
    return int(p.totp_last_step or 0) if p else 0


def _set_last_step(session: Session, person_id: int | None, step: int) -> None:
    if person_id is None:
        set_setting(session, LAST_STEP_KEY, step)
        return
    p = session.get(Person, person_id)
    p.totp_last_step = step
    session.add(p)
    session.commit()


def second_step_on(session: Session, person_id: int | None = None) -> bool:
    return bool(_state(session, person_id).get("enabled"))


def _totp(secret: str):  # noqa: ANN202
    import pyotp

    return pyotp.TOTP(secret)


def _normal(code: str) -> str:
    return code.strip().lower().replace(" ", "")


def _hash_code(code: str) -> str:
    """A recovery code as it is kept: its own salted scrypt hash (people.hash_password), so a stolen table is slow
    to try against and nothing about it hangs on FA_SESSION_SECRET, which may be rotated at any time."""
    from .people import hash_password

    return hash_password(_normal(code))


def _legacy_hash(code: str) -> str:
    """How codes were kept before: a digest keyed with the session secret. Still accepted for codes made then,
    while that secret is what it was; a rotated secret ends them, and the owner makes new ones from Settings."""
    return hashlib.sha256((settings.signing_secret() + "\x00" + _normal(code)).encode()).hexdigest()


def _matches(kept: str, code: str) -> bool:
    from .people import check_password

    if kept.startswith("scrypt$"):
        return check_password(kept, _normal(code))
    return hmac.compare_digest(kept, _legacy_hash(code))


def check_code(session: Session, code: str, person_id: int | None = None) -> bool:
    """A current authenticator code (once only) or an unused recovery code. A recovery code is used up."""
    state = _state(session, person_id)
    if not state.get("enabled"):
        return False
    code = (code or "").strip().replace(" ", "")
    if code.isdigit() and len(code) == 6:
        totp = _totp(crypto.decrypt(state["secret_enc"]))
        now = int(time.time())
        last = _last_step(session, person_id)
        for drift in (0, -1, 1):
            step = now // 30 + drift
            if step > last and hmac.compare_digest(totp.at(step * 30), code):
                _set_last_step(session, person_id, step)
                return True
        return False
    left = list(state.get("recovery", []))
    for kept in left:
        if _matches(kept, code):
            left.remove(kept)
            _save_state(session, person_id, {**state, "recovery": left})
            return True
    return False


def _owner(request: Request, session: Session) -> auth.Principal:
    """The person signed in (the owner or the second person), managing their own sign-in."""
    principal = auth.principal_from_request(request, session)
    if principal is None or principal.kind not in ("owner", "person"):
        raise HTTPException(401, "Sign in")
    if request.method != "GET" and request.headers.get(auth.CSRF_HEADER, "") != auth.CSRF_VALUE:
        raise HTTPException(403, "Missing the request header the app sends")
    return principal


@router.get("/factors")
def factors(request: Request, session: Session = Depends(get_session)) -> dict:
    who = _owner(request, session)
    state = _state(session, who.person_id)
    rp = _rp_or_none(request)
    keys = session.exec(select(Passkey).where(Passkey.person_id == who.person_id).order_by(Passkey.created_at)).all()
    return {
        "totp": {"enabled": bool(state.get("enabled")), "recovery_left": len(state.get("recovery", [])), "since": state.get("since")},
        "passkeys": [{"id": k.id, "name": k.name, "address": k.rp_id, "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None} for k in keys],
        "passkeys_here": rp is not None,
        "passkeys_note": None if rp else (f"Passkeys work at the https address, {settings.public_url}. Open the app there to add one." if settings.public_url.startswith("https://") else "Passkeys need an https address. Publish the app first (Settings › Setting up › Reach it from anywhere)."),
    }


@router.post("/totp/start")
def totp_start(request: Request, session: Session = Depends(get_session)) -> dict:
    """A new secret, shown once as a QR code and as text, waiting for the first code to prove it was saved."""
    import pyotp
    import segno

    who = _owner(request, session)
    state = _state(session, who.person_id)
    if state.get("enabled"):
        raise HTTPException(409, "The second step is on already. Turn it off first to make a new one.")
    secret = pyotp.random_base32()
    person = session.get(Person, who.person_id) if who.person_id else None
    uri = pyotp.TOTP(secret).provisioning_uri(name=person.username if person else settings.username, issuer_name="FinancialAuthority")
    _save_state(session, who.person_id, {**state, "pending_enc": crypto.encrypt(secret)})
    qr = segno.make(uri, error="m").svg_inline(scale=5, dark="#141518", light="#ffffff")
    return {"secret": secret, "uri": uri, "qr_svg": qr}


class CodeIn(In):
    code: str


@router.post("/totp/confirm")
def totp_confirm(body: CodeIn, request: Request, session: Session = Depends(get_session)) -> dict:
    who = _owner(request, session)
    state = _state(session, who.person_id)
    if not state.get("pending_enc"):
        raise HTTPException(409, "Start again: no code is waiting to be confirmed.")
    secret = crypto.decrypt(state["pending_enc"])
    if not _totp(secret).verify((body.code or "").strip().replace(" ", ""), valid_window=1):
        raise HTTPException(400, "That code does not match. Check the time on your phone, then type the code it shows now.")
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(10)]
    _save_state(session, who.person_id, {"enabled": True, "secret_enc": crypto.encrypt(secret), "pending_enc": "", "recovery": [_hash_code(c) for c in codes], "since": utcnow().isoformat()})
    return {"ok": True, "recovery_codes": codes, "note": "Each recovery code signs in once, when the phone is not at hand. They are shown only now: keep them somewhere safe, away from this computer."}


@router.post("/totp/disable")
def totp_disable(body: CodeIn, request: Request, session: Session = Depends(get_session)) -> dict:
    who = _owner(request, session)
    if not check_code(session, body.code, who.person_id):
        raise HTTPException(400, "That code does not match. Type the code your app shows, or a recovery code.")
    _save_state(session, who.person_id, {"enabled": False})
    return {"ok": True}


def ticket_for(person: Person | None = None) -> str:
    """Proof that the password was right, good for five minutes, for the second step."""
    if person is not None:
        return _tickets().dumps({"u": person.username, "p": person.id, "n": secrets.token_hex(8)})
    return _tickets().dumps({"u": settings.username, "n": secrets.token_hex(8)})


def _tickets() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.signing_secret(), salt="fa-second-step")


class SecondIn(In):
    ticket: str
    code: str


@router.post("/login/second")
def login_second(body: SecondIn, request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    ip = auth._client_ip(request)
    if auth._locked(ip):
        raise HTTPException(429, "Too many failed sign-ins. Wait a minute, then try again.")
    try:
        data = _tickets().loads(body.ticket, max_age=TICKET_SECONDS)
    except SignatureExpired as error:
        raise HTTPException(401, "That took too long. Sign in with your password again.") from error
    except BadSignature as error:
        raise HTTPException(401, "Sign in with your password again.") from error
    person = None
    if data.get("p") is not None:
        person = session.get(Person, int(data["p"]))
        if person is None or not person.active or data.get("u") != person.username:
            raise HTTPException(401, "Sign in with your password again.")
    elif data.get("u") != settings.username:
        raise HTTPException(401, "Sign in with your password again.")
    if auth._account_wait(data["u"]) > 0:
        raise HTTPException(429, "Too many failed sign-ins. Wait a minute, then try again.")
    if not check_code(session, body.code, person.id if person else None):
        auth._record_failure(ip, username=data["u"])
        raise HTTPException(401, "That code does not match. Type the code your app shows now, or a recovery code.")
    auth.clear_failures(ip, data["u"])
    _set_cookie(response, request, auth.start_session(session, request, person))
    return {"ok": True, "user": person.username if person else settings.username}


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=auth.request_is_https(request), path="/")


# ---- passkeys ---------------------------------------------------------------------------------------------------


def _rp_or_none(request: Request) -> tuple[str, str] | None:
    """The address passkeys are made and used at: the published https address, or this computer."""
    host = (request.url.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return host, f"{request.url.scheme}://{request.url.netloc}"
    public = urlsplit(settings.public_url or "")
    if public.scheme == "https" and public.hostname and public.hostname.lower() == (request.headers.get("host", "").split(":")[0].lower() or host):
        return public.hostname.lower(), f"https://{public.netloc}"
    return None


def _rp(request: Request) -> tuple[str, str]:
    rp = _rp_or_none(request)
    if rp is None:
        raise HTTPException(409, "Passkeys work only at the app's https address or on this computer.")
    return rp


CHALLENGES_MAX = 1000  # open challenges kept at once; past that the oldest go, so a flood at the public address holds no memory


def _remember(purpose: str, challenge: bytes, rp_id: str) -> str:
    now = time.time()
    for k in [k for k, v in _challenges.items() if now - v[0] > CHALLENGE_SECONDS]:
        _challenges.pop(k, None)
    while len(_challenges) >= CHALLENGES_MAX:
        _challenges.pop(min(_challenges, key=lambda k: _challenges[k][0]), None)
    key = f"{purpose}:{secrets.token_urlsafe(16)}"
    _challenges[key] = (now, challenge, rp_id)
    return key


def _recall(key: str, purpose: str) -> tuple[bytes, str]:
    got = _challenges.pop(key or "", None)
    if got is None or not key.startswith(purpose + ":") or time.time() - got[0] > CHALLENGE_SECONDS:
        raise HTTPException(400, "That took too long. Try again.")
    return got[1], got[2]


@router.post("/passkeys/register/options")
def passkey_register_options(request: Request, session: Session = Depends(get_session)) -> dict:
    import json

    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement

    who = _owner(request, session)
    rp_id, _origin = _rp(request)
    person = session.get(Person, who.person_id) if who.person_id else None
    name = person.username if person else settings.username
    user_id = hashlib.sha256((f"fa-person:{person.id}:{person.username}" if person else "fa-owner:" + settings.username).encode()).digest()[:16]
    existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id)) for k in session.exec(select(Passkey).where(Passkey.rp_id == rp_id, Passkey.person_id == who.person_id)).all()]
    options = generate_registration_options(
        rp_id=rp_id, rp_name="FinancialAuthority", user_name=name, user_id=user_id,
        user_display_name=(person.display_name or person.username) if person else settings.username, exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.REQUIRED),
    )
    return {"key": _remember("reg", options.challenge, rp_id), "options": json.loads(options_to_json(options))}


class PasskeyIn(In):
    key: str
    credential: dict
    name: str = ""


@router.post("/passkeys/register")
def passkey_register(body: PasskeyIn, request: Request, session: Session = Depends(get_session)) -> dict:
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    who = _owner(request, session)
    challenge, rp_id = _recall(body.key, "reg")
    _rp_id, origin = _rp(request)
    if _rp_id != rp_id:
        raise HTTPException(400, "Start again at the same address.")
    try:
        verified = verify_registration_response(credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    except Exception as error:  # noqa: BLE001 - any failure to verify is a refusal
        raise HTTPException(400, f"The passkey was not accepted: {type(error).__name__}") from error
    row = Passkey(credential_id=bytes_to_base64url(verified.credential_id), public_key=bytes_to_base64url(verified.credential_public_key), sign_count=verified.sign_count,
                  name=(body.name or "Passkey").strip()[:60], rp_id=rp_id, person_id=who.person_id)
    session.add(row)
    session.commit()
    return {"ok": True, "id": row.id, "name": row.name}


@router.delete("/passkeys/{passkey_id}")
def passkey_remove(passkey_id: int, request: Request, session: Session = Depends(get_session)) -> dict:
    who = _owner(request, session)
    row = session.get(Passkey, passkey_id)
    if row is None or row.person_id != who.person_id:
        raise HTTPException(404, "No such passkey")
    session.delete(row)
    session.commit()
    return {"ok": True}


@router.post("/passkeys/login/options")
def passkey_login_options(request: Request, session: Session = Depends(get_session)) -> dict:
    import json

    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    ip = auth._client_ip(request)
    if auth._locked(ip) or auth._locked("passkey:" + ip):
        # Under the sign-in lockout: an address that failed its passwords or its passkeys is not handed more challenges.
        raise HTTPException(429, "Too many failed passkey sign-ins. Wait, then try again.")
    rp = _rp_or_none(request)
    keys = session.exec(select(Passkey).where(Passkey.rp_id == rp[0])).all() if rp else []
    if not rp or not keys:
        raise HTTPException(409, "No passkey is set up for this address. Sign in with the password.")
    options = generate_authentication_options(rp_id=rp[0], allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id)) for k in keys], user_verification=UserVerificationRequirement.REQUIRED)
    return {"key": _remember("login", options.challenge, rp[0]), "options": json.loads(options_to_json(options))}


@router.post("/passkeys/login")
def passkey_login(body: PasskeyIn, request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    ip = "passkey:" + auth._client_ip(request)
    if auth._locked(auth._client_ip(request)) or auth._locked(ip):
        raise HTTPException(429, "Too many failed passkey sign-ins. Wait, then try again.")
    challenge, rp_id = _recall(body.key, "login")
    _rp_id, origin = _rp(request)
    raw_id = str(body.credential.get("id") or body.credential.get("rawId") or "")
    row = session.exec(select(Passkey).where(Passkey.credential_id == raw_id, Passkey.rp_id == rp_id)).first()
    if row is None or _rp_id != rp_id:
        auth._record_failure(ip)
        raise HTTPException(401, "That passkey is not one of yours here.")
    try:
        verified = verify_authentication_response(credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
                                                  credential_public_key=base64url_to_bytes(row.public_key), credential_current_sign_count=row.sign_count, require_user_verification=True)
    except Exception as error:  # noqa: BLE001 - any failure to verify is a refusal
        auth._record_failure(ip)
        raise HTTPException(401, "The passkey did not verify.") from error
    person = None
    if row.person_id is not None:
        person = session.get(Person, row.person_id)
        if person is None or not person.active or not person.password_hash:
            auth._record_failure(ip)
            raise HTTPException(401, "That passkey's sign-in was turned off.")
    row.sign_count, row.last_used_at = verified.new_sign_count, utcnow()
    session.add(row)
    session.commit()
    auth.clear_failures(ip)  # the passkey count for this address; a password count is never cleared by a passkey
    _set_cookie(response, request, auth.start_session(session, request, person))
    return {"ok": True, "user": person.username if person else settings.username, "kind": "person" if person else "owner"}
