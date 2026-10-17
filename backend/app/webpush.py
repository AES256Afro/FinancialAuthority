"""Web push from the app itself: no ntfy or Gotify needed. A phone or tablet that installed the app (on iPhone
and iPad, from Safari's Add to Home Screen, iOS 16.4 or later) subscribes once; the brief and the reminders then
arrive as notifications, and an Apple Watch shows whatever the iPhone shows. The signing key pair is made here
once and the private half is encrypted with the data key; each subscription's address is encrypted too. A
subscription the push service says is gone is dropped."""

from __future__ import annotations

import base64
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from . import crypto
from .store import get_setting, set_setting

log = logging.getLogger("fa.webpush")
KEYS = "webpush.vapid"
SUBS = "webpush.subs"
MAX_SUBS = 12


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ensure_keys(session: Session) -> dict[str, str]:
    """The VAPID key pair: made on first use, the private key kept encrypted."""
    stored = get_setting(session, KEYS, {}) or {}
    if stored.get("public") and stored.get("private_enc"):
        return stored
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02

    v = Vapid02()
    v.generate_keys()
    public = _b64url(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    stored = {"public": public, "private_enc": crypto.encrypt(v.private_pem().decode())}
    set_setting(session, KEYS, stored)
    return stored


def public_key(session: Session) -> str:
    return ensure_keys(session)["public"]


def _subs(session: Session) -> list[dict[str, Any]]:
    return get_setting(session, SUBS, []) or []


def subscriptions(session: Session) -> list[dict[str, Any]]:
    """What the screens see: a label and dates, never the address."""
    return [{"id": s["id"], "label": s.get("label", "A device"), "created": s.get("created"), "last_ok": s.get("last_ok"), "last_error": s.get("last_error", "")} for s in _subs(session)]


def drop_person(session: Session, person_id: int) -> int:
    """Every device a second person added, removed with their access."""
    subs = _subs(session)
    kept = [s for s in subs if s.get("person_id") != person_id]
    set_setting(session, SUBS, kept)
    return len(subs) - len(kept)


def subscribe(session: Session, *, endpoint: str, p256dh: str, auth: str, label: str, origin: str, person_id: int | None = None) -> dict[str, Any]:
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        raise ValueError("That is not a push subscription")
    subs = [s for s in _subs(session) if crypto.decrypt(s["endpoint_enc"]) != endpoint]
    if len(subs) >= MAX_SUBS:
        raise ValueError(f"At most {MAX_SUBS} devices; remove one first")
    row = {"id": secrets.token_hex(6), "endpoint_enc": crypto.encrypt(endpoint), "p256dh": p256dh, "auth_enc": crypto.encrypt(auth), "label": (label or "A device")[:60],
           "origin": origin[:200], "created": datetime.now(timezone.utc).isoformat(), "last_ok": None, "person_id": person_id}
    set_setting(session, SUBS, [*subs, row])
    return {k: row[k] for k in ("id", "label", "created")}


def unsubscribe(session: Session, sub_id: str) -> None:
    set_setting(session, SUBS, [s for s in _subs(session) if s["id"] != sub_id])


def webpush(**kwargs: Any) -> Any:  # a seam for tests
    from pywebpush import webpush as _send

    return _send(**kwargs)


def send_all(session: Session, title: str, body: str, *, link: str | None = None, tag: str | None = None) -> dict[str, Any]:
    """Send to every subscribed device. Never raises into a job; returns how many arrived."""
    subs = _subs(session)
    if not subs:
        return {"ok": False, "skipped": True, "sent": 0, "reason": "no device subscribed"}
    from py_vapid import Vapid02

    keys = ensure_keys(session)
    try:
        vapid = Vapid02.from_pem(crypto.decrypt(keys["private_enc"]).encode())
    except Exception as error:  # noqa: BLE001
        log.warning("web push keys unreadable: %s", type(error).__name__)
        return {"ok": False, "sent": 0, "reason": "the push keys could not be read"}
    payload = json.dumps({"title": title[:120], "body": body[:900], "link": link or "/today", "tag": tag or "fa"})
    sent, keep, now = 0, [], datetime.now(timezone.utc).isoformat()
    for s in subs:
        try:
            info = {"endpoint": crypto.decrypt(s["endpoint_enc"]), "keys": {"p256dh": s["p256dh"], "auth": crypto.decrypt(s["auth_enc"])}}
            webpush(subscription_info=info, data=payload, vapid_private_key=vapid, vapid_claims={"sub": s.get("origin") or "mailto:owner@localhost"}, ttl=12 * 3600, timeout=15)
            s["last_ok"], s["last_error"] = now, ""
            sent += 1
            keep.append(s)
        except Exception as error:  # noqa: BLE001
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status in (404, 410):
                log.info("web push: a device unsubscribed; dropped")
                continue  # the browser or the phone dropped it; so do we
            s["last_error"] = f"{type(error).__name__}{f' {status}' if status else ''}"[:80]
            log.warning("web push failed for a device: %s", s["last_error"])
            keep.append(s)
    set_setting(session, SUBS, keep)
    return {"ok": sent > 0, "sent": sent, "devices": len(keep)}
