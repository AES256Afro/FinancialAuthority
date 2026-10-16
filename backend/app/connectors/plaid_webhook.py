"""Plaid's webhook: a signed notice that an item has new transactions, so they arrive minutes after Plaid has
them instead of at the next scheduled sync. The notice carries no money data; it only names the item, and the app
then asks Plaid itself, with its own keys, as a sync always does.

Every notice is verified before anything happens: Plaid signs a JSON Web Token (ES256) over the SHA-256 of the
body, with a key the app fetches from Plaid by its id. A token that is not ES256, is more than five minutes old,
names an unknown key, or does not match the body is refused. Nothing in a notice is trusted beyond the item id it
names, and that item must already be one of the owner's links."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

log = logging.getLogger(__name__)
MAX_AGE = 300  # seconds
SYNC_CODES = {"SYNC_UPDATES_AVAILABLE", "DEFAULT_UPDATE", "INITIAL_UPDATE", "HISTORICAL_UPDATE", "TRANSACTIONS_REMOVED"}
LOGIN_CODES = {"ERROR", "PENDING_EXPIRATION", "PENDING_DISCONNECT", "USER_PERMISSION_REVOKED"}
DEBOUNCE = 60  # a link is synced at most once a minute however many notices arrive

_keys: dict[str, tuple[float, dict[str, Any]]] = {}
_last_sync: dict[int, float] = {}
_lock = threading.Lock()


class Refused(Exception):
    """The notice did not verify; the reason is for the log, never for the caller."""


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify(token: str, body: bytes, fetch_key: Callable[[str], dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """The verified claims of a Plaid-Verification token over `body`, or Refused."""
    now = time.time() if now is None else now
    try:
        head_b64, claims_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(claims_b64))
        signature = _b64(sig_b64)
    except (ValueError, json.JSONDecodeError) as error:
        raise Refused(f"not a token: {error}") from error
    if header.get("alg") != "ES256" or not header.get("kid"):
        raise Refused("not ES256 or no key id")
    kid = str(header["kid"])
    jwk = _key(kid, fetch_key, now)
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise Refused("not a P-256 key")
    if jwk.get("expired_at"):
        raise Refused("the key has expired")
    public = ec.EllipticCurvePublicNumbers(int.from_bytes(_b64(jwk["x"]), "big"), int.from_bytes(_b64(jwk["y"]), "big"), ec.SECP256R1()).public_key()
    if len(signature) != 64:
        raise Refused("signature length")
    der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
    try:
        public.verify(der, f"{head_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as error:
        raise Refused("bad signature") from error
    issued = claims.get("iat")
    if not isinstance(issued, (int, float)) or abs(now - issued) > MAX_AGE:
        raise Refused("too old or from the future")
    expected = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(str(claims.get("request_body_sha256", "")), expected):
        raise Refused("the body does not match")
    return claims


def _key(kid: str, fetch_key: Callable[[str], dict[str, Any]], now: float) -> dict[str, Any]:
    with _lock:
        cached = _keys.get(kid)
    if cached and now - cached[0] < 24 * 3600:
        return cached[1]
    try:
        jwk = fetch_key(kid)
    except Exception as error:  # noqa: BLE001 - an unknown key is a refusal, whatever went wrong fetching it
        raise Refused(f"key {kid[:12]} not found: {type(error).__name__}") from error
    with _lock:
        _keys[kid] = (now, jwk)
    return jwk


def handle(notice: dict[str, Any], start_sync: Callable[[int], None], session_factory) -> str:  # noqa: ANN001
    """Act on a verified notice: start a sync of the named link, or mark it as needing a new sign-in."""
    from sqlmodel import select

    from ..models import BankLink

    kind, code, item_id = str(notice.get("webhook_type", "")), str(notice.get("webhook_code", "")), str(notice.get("item_id", ""))
    if not item_id:
        return "no item"
    with session_factory() as session:
        link = session.exec(select(BankLink).where(BankLink.provider == "plaid", BankLink.external_id == item_id, BankLink.active == True)).first()  # noqa: E712
        if link is None:
            return "not one of the owner's links"
        if kind == "ITEM" and code in LOGIN_CODES:
            link.needs_login = True
            link.last_error = f"Plaid says this bank needs you to sign in again ({code.lower().replace('_', ' ')})."
            session.add(link)
            session.commit()
            return "needs a new sign-in"
        link_id = link.id
    if kind == "TRANSACTIONS" and code in SYNC_CODES:
        now = time.monotonic()
        with _lock:
            if now - _last_sync.get(link_id, -DEBOUNCE) < DEBOUNCE:
                return "synced a moment ago"
            _last_sync[link_id] = now
        start_sync(link_id)
        return "sync started"
    return "nothing to do"


def webhook_url(public_url: str) -> str | None:
    """Where Plaid sends its notices: only over https, at the published address."""
    return public_url.rstrip("/") + "/api/plaid/webhook" if public_url.startswith("https://") else None
