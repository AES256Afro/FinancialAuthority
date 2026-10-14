"""Push to the owner's phone through ntfy or Gotify, both free and both in BoxPilot's catalog.

This is the app talking to its owner, not to anyone else: the morning brief, and later the approvals.
Settings live in the settings table; the token is encrypted. Nothing is sent unless push is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session

from . import crypto
from .config import settings
from .store import get_setting, set_setting

log = logging.getLogger("fa.notify")

KINDS = ("off", "ntfy", "gotify")
DEFAULT: dict[str, Any] = {"kind": "off", "url": "", "topic": "", "token_enc": "", "brief_hour": 7, "last_brief_day": ""}


def push_settings(session: Session) -> dict[str, Any]:
    return {**DEFAULT, **(get_setting(session, "push", {}) or {})}


def push_state(session: Session) -> dict[str, Any]:
    s = push_settings(session)
    return {"kind": s["kind"], "url": s["url"], "topic": s["topic"], "token_set": bool(s["token_enc"]), "brief_hour": int(s["brief_hour"]), "last_brief_day": s["last_brief_day"], "configured": s["kind"] != "off" and bool(s["url"])}


def save_push(session: Session, values: dict[str, Any]) -> None:
    s = push_settings(session)
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
    elif token == "":
        s["token_enc"] = ""
    set_setting(session, "push", s)


def _token(s: dict[str, Any]) -> str:
    if s.get("token_enc") and crypto.is_encrypted(s["token_enc"]):
        try:
            return crypto.decrypt(s["token_enc"])
        except (ValueError, crypto.DataKeyMissing):
            return ""
    return ""


def send(session: Session, title: str, body: str, *, priority: str = "default", link: str | None = None, tags: list[str] | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Deliver one notification. Returns what happened; never raises into a job."""
    s = push_settings(session)
    if s["kind"] == "off" or not s["url"]:
        return {"ok": False, "skipped": True, "reason": "push is off"}
    url = s["url"].rstrip("/")
    token = _token(s)
    try:
        with client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as c:
            if s["kind"] == "ntfy":
                headers = {"Title": title, "Priority": priority}
                if tags:
                    headers["Tags"] = ",".join(tags)
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


def brief_text(brief: list[dict]) -> str:
    marks = {"info": "·", "warning": "!", "critical": "!!"}
    return "\n".join(f"{marks.get(b['severity'], '·')} {b['text']}" for b in brief)


def morning_brief_due(session: Session, now: datetime) -> bool:
    """True once per local day, from the chosen hour on, when push is configured."""
    s = push_settings(session)
    if s["kind"] == "off" or not s["url"]:
        return False
    local = now.astimezone(ZoneInfo(settings.tz) if settings.tz else None)
    if local.hour < int(s["brief_hour"]):
        return False
    return s["last_brief_day"] != local.date().isoformat()


def mark_brief_sent(session: Session, now: datetime) -> None:
    s = push_settings(session)
    local = now.astimezone(ZoneInfo(settings.tz) if settings.tz else None)
    s["last_brief_day"] = local.date().isoformat()
    set_setting(session, "push", s)
