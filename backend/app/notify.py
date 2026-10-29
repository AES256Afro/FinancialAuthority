"""Push to the owner's phone through ntfy or Gotify, both free and both in BoxPilot's catalog.

This is the app talking to its owner, not to anyone else: the morning brief, and later the approvals.
Settings live in the settings table; the token is encrypted. Nothing is sent unless push is configured.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlmodel import Session

from . import crypto
from .clock import zone
from .config import settings
from .store import get_setting, set_setting

log = logging.getLogger("fa.notify")

KINDS = ("off", "ntfy", "gotify")
LAST_ERROR = "push.last_error"  # the last delivery that failed, for the self-check; cleared by the next that arrives
DEFAULT: dict[str, Any] = {"kind": "off", "url": "", "topic": "", "token_enc": "", "brief_hour": 7, "last_brief_day": ""}


def push_settings(session: Session) -> dict[str, Any]:
    s = {**DEFAULT, **(get_setting(session, "push", {}) or {})}
    s["last_brief_day"] = get_setting(session, BRIEF_DAY, None) or s.get("last_brief_day") or ""
    return s


BRIEF_DAY = "push.brief_day"


def push_state(session: Session) -> dict[str, Any]:
    s = push_settings(session)
    return {"kind": s["kind"], "url": s["url"], "topic": s["topic"], "token_set": bool(s["token_enc"]), "brief_hour": int(s["brief_hour"]), "last_brief_day": s["last_brief_day"], "configured": s["kind"] != "off" and bool(s["url"])}


def save_push(session: Session, values: dict[str, Any]) -> None:
    s = push_settings(session)
    was = (s.get("url") or "").strip().rstrip("/")
    for key in ("kind", "url", "topic"):
        if values.get(key) is not None:
            s[key] = str(values[key]).strip()
    if s["kind"] not in KINDS:
        raise ValueError("kind is off, ntfy or gotify")
    if values.get("brief_hour") is not None:
        s["brief_hour"] = max(0, min(23, int(values["brief_hour"])))
    token = values.get("token")
    if token is not None and str(token).strip():
        s["token_enc"] = crypto.encrypt(str(token).strip())
    elif token == "" or (s.get("url") or "").strip().rstrip("/") != was:
        # The token is sent as a bearer to the address on file and nowhere else: a new address needs it typed again.
        s["token_enc"] = ""
    set_setting(session, "push", s)


def _token(s: dict[str, Any]) -> str:
    if s.get("token_enc") and crypto.is_encrypted(s["token_enc"]):
        try:
            return crypto.decrypt(s["token_enc"])
        except (ValueError, crypto.DataKeyMissing):
            return ""
    return ""


def has_devices(session: Session) -> bool:
    return bool(get_setting(session, "webpush.subs", []) or [])


def send(session: Session, title: str, body: str, *, priority: str = "default", link: str | None = None, tags: list[str] | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Deliver one notification to every channel that is set up: ntfy or Gotify, and every device subscribed to
    the app's own web push. Returns what happened; never raises into a job."""
    from .webpush import send_all

    devices = send_all(session, title, body, link=link if (link or "").startswith("/") else None, tag=(tags or ["fa"])[0]) if has_devices(session) else None
    channel = _send_channel(session, title, body, priority=priority, link=link, tags=tags, client=client)
    if channel.get("skipped") and devices is None:
        return {"ok": False, "skipped": True, "reason": "push is off"}
    ok = bool(channel.get("ok")) or bool(devices and devices.get("ok"))
    detail = "" if ok else (channel.get("detail") or channel.get("reason") or (devices or {}).get("reason") or "")
    _note(ok, detail or (f"status {channel.get('status')}" if channel.get("status") else "the push channel refused it"))
    return {"ok": ok, "skipped": False, "status": channel.get("status"), "detail": detail, "channel": channel, "devices": devices}


def _note(ok: bool, detail: str) -> None:
    """Keep the last failed delivery where the self-check reads it, and clear it once one arrives. Written in a
    session of its own, so a caller's unfinished work is never committed early; a write that cannot be made
    (the database busy) is only logged, since the send itself already happened or failed."""
    from .db import session_scope

    try:
        with session_scope() as own:
            had = get_setting(own, LAST_ERROR, None)
            if ok and had:
                set_setting(own, LAST_ERROR, None)
            elif not ok:
                set_setting(own, LAST_ERROR, {"at": datetime.now(UTC).isoformat(), "detail": str(detail)[:200]})
    except Exception as error:  # noqa: BLE001 - a note about a send must never fail the send
        log.warning("could not keep the push result: %s", type(error).__name__)


def _header(value: str, limit: int = 200) -> str:
    """A value fit for an HTTP header: one line, no control characters, and encoded (RFC 2047, as ntfy reads it)
    when it holds a character outside ASCII, since a header cannot carry one as it is."""
    import re
    from email.header import Header

    one = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "").strip()[:limit]
    try:
        one.encode("ascii")
        return one
    except UnicodeEncodeError:
        return Header(one, "utf-8").encode(maxlinelen=10_000).replace("\n", "")


def _send_channel(session: Session, title: str, body: str, *, priority: str = "default", link: str | None = None, tags: list[str] | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    s = push_settings(session)
    if s["kind"] == "off" or not s["url"]:
        return {"ok": False, "skipped": True, "reason": "push is off"}
    if link and link.startswith("/"):
        link = (settings.public_url or "").rstrip("/") + link if settings.public_url else None
    url = s["url"].rstrip("/")
    token = _token(s)
    try:
        with client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as c:
            if s["kind"] == "ntfy":
                headers = {"Title": _header(title), "Priority": _header(priority, 20)}
                if tags:
                    headers["Tags"] = _header(",".join(tags), 100)
                if link:
                    headers["Click"] = link
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                response = c.post(f"{url}/{s['topic'] or 'financialauthority'}", content=body.encode("utf-8"), headers=headers)
            else:
                payload: dict[str, Any] = {"title": title, "message": body, "priority": {"min": 1, "low": 2, "default": 5, "high": 8, "urgent": 10}.get(priority, 5)}
                if link:
                    payload["extras"] = {"client::notification": {"click": {"url": link}}}
                response = c.post(f"{url}/message", json=payload, headers={"X-Gotify-Key": token} if token else {})
        ok = response.status_code < 300
        if not ok:
            log.warning("push failed: %s %s", response.status_code, response.text[:200])
        return {"ok": ok, "status": response.status_code, "detail": "" if ok else response.text[:200]}
    except httpx.HTTPError as error:
        log.warning("push failed: %s", error)
        return {"ok": False, "status": 0, "detail": f"{type(error).__name__}: {error}"[:200]}
    except Exception as error:  # noqa: BLE001 - a push that cannot be built or sent is reported, never raised into a job or a tool call
        log.warning("push failed: %s", type(error).__name__)
        return {"ok": False, "status": 0, "detail": f"{type(error).__name__}"[:200]}


def brief_text(brief: list[dict]) -> str:
    marks = {"info": "·", "warning": "!", "critical": "!!"}
    return "\n".join(f"{marks.get(b['severity'], '·')} {b['text']}" for b in brief)


def morning_brief_due(session: Session, now: datetime) -> bool:
    """True once per local day, from the chosen hour on, when push is configured."""
    s = push_settings(session)
    if (s["kind"] == "off" or not s["url"]) and not has_devices(session):
        return False
    local = now.astimezone(zone())  # a time zone the server does not know falls back on its own, as everywhere else
    if local.hour < int(s["brief_hour"]):
        return False
    return s["last_brief_day"] != local.date().isoformat()


def mark_brief_sent(session: Session, now: datetime) -> None:
    local = now.astimezone(zone())
    set_setting(session, BRIEF_DAY, local.date().isoformat())
