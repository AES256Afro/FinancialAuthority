"""Exports: the catalog of formats, layouts, palettes, mascots and sections; a file in any of them; a preview of a
report template; saved templates; and the calendar feed a phone subscribes to. A file is made for the signed-in
owner only, never cached, and written to the audit. The feed is the one route that answers without a session: it
needs a long random token the owner creates and can revoke, and carries amounts only if the owner says so."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session

from .. import auth, services
from ..config import settings
from ..db import get_session
from ..exports import data as data_formats
from ..exports import render
from ..exports.model import DEFAULT_SECTIONS, SECTIONS, build_report, registers
from ..exports.templates import LAYOUTS, MASCOTS, PALETTES, catalog
from ..store import get_setting, set_setting
from .routes import today

router = APIRouter(tags=["exports"])
TEMPLATES_KEY = "export.templates"
FEED_KEY = "calendar.feed"
NAME = re.compile(r"^[\w\- .]{1,40}$")


class TemplateIn(BaseModel):
    id: str | None = None
    name: str
    layout: str = "board"
    palette: str = "layout"
    mascot: str | None = None
    sections: list[str] = DEFAULT_SECTIONS
    notes: str = ""
    css: str = ""


def _check(t: TemplateIn) -> dict:
    if not t.name.strip() or len(t.name) > 40 or any(c in t.name for c in "<>"):
        raise HTTPException(400, "A template needs a name of up to 40 characters")
    if t.layout not in LAYOUTS or t.palette not in PALETTES or (t.mascot is not None and t.mascot not in MASCOTS):
        raise HTTPException(400, "Unknown layout, palette or mascot")
    if not t.sections or any(s not in dict(SECTIONS) for s in t.sections) or len(t.sections) > len(SECTIONS):
        raise HTTPException(400, "Sections must come from the list")
    if len(t.notes) > 4000 or len(t.css) > 20000:
        raise HTTPException(400, "Notes are up to 4,000 characters and CSS up to 20,000")
    if t.css and render.clean_css(t.css) == "":
        raise HTTPException(400, "The CSS may only style the page: no imports, no urls, no scripts")
    tid = t.id if t.id and NAME.match(t.id) else f"t-{secrets.token_hex(4)}"
    return {"id": tid, "name": t.name.strip(), "layout": t.layout, "palette": t.palette, "mascot": t.mascot, "sections": t.sections, "notes": t.notes, "css": t.css}


def _templates(session: Session) -> list[dict]:
    return get_setting(session, TEMPLATES_KEY, []) or []


@router.get("/api/v1/exports")
def exports_catalog(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    feed = get_setting(session, FEED_KEY, {}) or {}
    return {
        **catalog(), "formats": [{"id": k, **v} for k, v in data_formats.FORMATS.items()],
        "sections": [{"id": k, "name": v, "default": k in DEFAULT_SECTIONS} for k, v in SECTIONS], "templates": _templates(session),
        "feed": {"active": bool(feed.get("hash")), "amounts": bool(feed.get("amounts")), "created": feed.get("created")},
    }


def _filename(session: Session, ext: str) -> str:
    brand = ((get_setting(session, "appearance", {}) or {}).get("brand") or {}).get("name") or "financialauthority"
    slug = re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-") or "financialauthority"
    return f"{slug}-{today().isoformat()}.{ext}"


@router.get("/api/v1/exports/file")
def export_file(format: str, template: str | None = None, layout: str = "board", palette: str = "layout", mascot: str | None = None, sections: str | None = None, notes: str = "",  # noqa: A002
                session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> Response:
    if format not in data_formats.FORMATS:
        raise HTTPException(400, "Unknown format")
    spec = {"layout": layout, "palette": palette, "mascot": mascot, "sections": (sections or ",".join(DEFAULT_SECTIONS)).split(","), "notes": notes, "css": ""}
    if template:
        t = next((x for x in _templates(session) if x["id"] == template), None)
        if t is None:
            raise HTTPException(404, "No such template")
        spec = {**spec, **{k: t[k] for k in ("layout", "palette", "mascot", "sections", "notes", "css")}}
    if spec["layout"] not in LAYOUTS or spec["palette"] not in PALETTES or (spec["mascot"] is not None and spec["mascot"] not in MASCOTS):
        raise HTTPException(400, "Unknown layout, palette or mascot")
    info = data_formats.FORMATS[format]
    if format in data_formats.DATA_WRITERS:
        if format == "ics":
            body = data_formats.calendar(session, today(), amounts=True)
        else:
            body = data_formats.DATA_WRITERS[format](session, today())
    else:
        report = build_report(session, today(), [s for s in spec["sections"] if s], spec["notes"])
        if format == "xlsx":
            body = render.xlsx(report, registers(session), spec["layout"], spec["palette"], spec["mascot"])
        elif format == "html":
            body = render.html(report, spec["layout"], spec["palette"], spec["mascot"], spec["css"]).encode()
        elif format == "md":
            body = render.markdown(report, spec["layout"], spec["palette"], spec["mascot"]).encode()
        else:
            body = render.RENDERERS[format](report, spec["layout"], spec["palette"], spec["mascot"])
    services.audit(session, client=principal.name, client_kind="owner", tool=f"export:{format}", args={"layout": spec["layout"], "template": template}, rows=len(body), redaction=principal.redaction)
    return Response(content=body, media_type=info["mime"], headers={
        "Content-Disposition": f'attachment; filename="{_filename(session, info["ext"])}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


class PreviewIn(BaseModel):
    layout: str = "board"
    palette: str = "layout"
    mascot: str | None = None
    sections: list[str] = DEFAULT_SECTIONS
    notes: str = ""
    css: str = ""


@router.post("/api/v1/exports/preview")
def export_preview(body: PreviewIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    if body.layout not in LAYOUTS or body.palette not in PALETTES or (body.mascot is not None and body.mascot not in MASCOTS):
        raise HTTPException(400, "Unknown layout, palette or mascot")
    report = build_report(session, today(), [s for s in body.sections if s in dict(SECTIONS)] or DEFAULT_SECTIONS, body.notes[:4000])
    return {"html": render.html(report, body.layout, body.palette, body.mascot, body.css[:20000]), "css_refused": bool(body.css) and render.clean_css(body.css) == ""}


@router.post("/api/v1/exports/templates")
def save_template(body: TemplateIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    t = _check(body)
    items = [x for x in _templates(session) if x["id"] != t["id"]]
    if len(items) >= 30:
        raise HTTPException(400, "At most thirty saved templates")
    set_setting(session, TEMPLATES_KEY, [*items, t])
    return {"template": t, "templates": _templates(session)}


@router.delete("/api/v1/exports/templates/{template_id}")
def delete_template(template_id: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    set_setting(session, TEMPLATES_KEY, [x for x in _templates(session) if x["id"] != template_id])
    return {"templates": _templates(session)}


# ---- the calendar feed ---------------------------------------------------------------------------------------

class FeedIn(BaseModel):
    amounts: bool = False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/api/v1/calendar/feed")
def feed_status(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> dict:
    feed = get_setting(session, FEED_KEY, {}) or {}
    return {"active": bool(feed.get("hash")), "amounts": bool(feed.get("amounts")), "created": feed.get("created")}


@router.post("/api/v1/calendar/feed")
def feed_create(body: FeedIn, request: Request, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    """A new feed address; the old one stops working. The address is shown once."""
    token = secrets.token_urlsafe(32)
    set_setting(session, FEED_KEY, {"hash": _hash(token), "amounts": body.amounts, "created": datetime.now(timezone.utc).isoformat()})
    services.audit(session, client=principal.name, client_kind="owner", tool="calendar_feed_create", args={"amounts": body.amounts}, rows=1)
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    return {"url": f"{base}/api/calendar/{token}.ics", "amounts": body.amounts, "note": "Shown once. Add it in the phone's calendar as a subscription; make a new one and this stops working."}


@router.delete("/api/v1/calendar/feed")
def feed_revoke(session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    set_setting(session, FEED_KEY, {})
    services.audit(session, client=principal.name, client_kind="owner", tool="calendar_feed_revoke", rows=0)
    return {"active": False}


_hits: dict[str, list[float]] = {}
FEED_LIMIT = 60  # requests an hour, per address


@router.get("/api/calendar/{token}.ics", include_in_schema=False)
def feed(token: str, request: Request, session: Session = Depends(get_session)) -> Response:
    ip = auth._client_ip(request)
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < 3600]
    if len(recent) >= FEED_LIMIT:
        raise HTTPException(429, "Too many requests")
    _hits[ip] = [*recent, now]
    feed_cfg = get_setting(session, FEED_KEY, {}) or {}
    if not feed_cfg.get("hash") or len(token) < 30 or not secrets.compare_digest(_hash(token), feed_cfg["hash"]):
        raise HTTPException(404, "Not found")
    brand = ((get_setting(session, "appearance", {}) or {}).get("brand") or {}).get("name") or "FinancialAuthority"
    body = data_formats.calendar(session, today(), amounts=bool(feed_cfg.get("amounts")), name=brand)
    return Response(content=body, media_type="text/calendar", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
