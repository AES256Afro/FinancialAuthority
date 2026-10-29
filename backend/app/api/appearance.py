"""Appearance, kept on the server so the phone and the desktop agree: the look, the home arrangements (sections
in order, each with its picture, any hidden), which picture each section shows, the owner's colour roles, the
app's name and mark, the type size, the density and quiet numbers. Validated here; the colour roles are also
checked against the palette rules in the browser before they can be saved."""

from __future__ import annotations

import base64
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .inputs import In
from .. import auth
from ..db import get_session
from ..models import Setting
from ..store import get_setting, set_setting

router = APIRouter(prefix="/api/v1", tags=["appearance"])

LOOKS = {"light", "dark", "console", "contrast"}
FONTS = {"regular", "readable"}
SECTION_KEYS = {"one_thing", "safe", "paycheck", "cover", "saving", "borrowing", "brief", "by_check", "calendar", "view", "next_steps", "purchase", "goals", "sixty", "debts"}
COLOUR_ROLES = {"housing", "bills", "card", "later", "aside", "saving", "yours"}
TYPE_SIZES = {"regular", "larger", "largest"}
DENSITIES = {"comfortable", "compact"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME = re.compile(r"^[\w\- .]{1,40}$")
MARK_MAX = 300_000
MARK_TYPES = ("image/png", "image/jpeg", "image/webp", "image/svg+xml")
SVG_REFUSED = re.compile(r"<script|<foreignobject|<iframe|<embed|<object|javascript:|\bon[a-z]+\s*=|href\s*=\s*[\"']?\s*(?!#)[a-z]+:", re.IGNORECASE)

DEFAULT = {"look": "light", "active": "river", "arrangements": [], "show_as": {}, "colours": None, "brand": {"name": "", "mark": ""}, "type_size": "regular", "density": "comfortable", "quiet": False, "calm": False, "font": "regular"}


class SectionIn(In):
    key: str
    show_as: str | None = None
    hidden: bool = False


class ArrangementIn(In):
    id: str
    name: str
    sections: list[SectionIn]


class BrandIn(In):
    name: str = ""
    mark: str = ""


class AppearanceIn(In):
    look: str = "light"
    active: str = "river"
    arrangements: list[ArrangementIn] = []
    show_as: dict[str, str] = {}
    colours: dict[str, dict[str, str]] | None = None
    brand: BrandIn = BrandIn()
    type_size: str = "regular"
    density: str = "comfortable"
    quiet: bool = False
    calm: bool = False  # one card at a time, no motion, more room
    font: str = "regular"  # regular | readable


def _check_mark(mark: str) -> str:
    if not mark:
        return ""
    if len(mark) > MARK_MAX * 1.4:
        raise HTTPException(400, "The mark is larger than 300 KB; use a smaller image")
    m = re.match(r"^data:(image/[a-z+]+);base64,([A-Za-z0-9+/=]+)$", mark)
    if not m or m.group(1) not in MARK_TYPES:
        raise HTTPException(400, "The mark must be a PNG, JPEG, WebP or SVG image")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError as error:
        raise HTTPException(400, "The mark is not valid base64") from error
    if len(raw) > MARK_MAX:
        raise HTTPException(400, "The mark is larger than 300 KB; use a smaller image")
    if m.group(1) == "image/svg+xml":
        text = raw.decode("utf-8", errors="replace")
        if "<svg" not in text.lower() or SVG_REFUSED.search(text):
            raise HTTPException(400, "That SVG carries scripts or outside links; export it as a plain drawing or a PNG")
    elif m.group(1) == "image/png" and not raw.startswith(b"\x89PNG"):
        raise HTTPException(400, "That file is not a PNG")
    elif m.group(1) == "image/jpeg" and not raw.startswith(b"\xff\xd8"):
        raise HTTPException(400, "That file is not a JPEG")
    elif m.group(1) == "image/webp" and raw[8:12] != b"WEBP":
        raise HTTPException(400, "That file is not a WebP")
    return mark


def validate(body: AppearanceIn) -> dict[str, Any]:
    if body.look not in LOOKS:
        raise HTTPException(400, "look is light, dark or console")
    if body.type_size not in TYPE_SIZES or body.density not in DENSITIES:
        raise HTTPException(400, "type size is regular, larger or largest; density is comfortable or compact")
    if body.font not in FONTS:
        raise HTTPException(400, "the font is regular or readable")
    if len(body.arrangements) > 20:
        raise HTTPException(400, "at most twenty arrangements")
    arrangements = []
    for a in body.arrangements:
        if not NAME.match(a.id) or not a.name.strip() or len(a.name) > 40:
            raise HTTPException(400, "an arrangement needs a short id and a name of up to 40 characters")
        if len(a.sections) > 24:
            raise HTTPException(400, "at most twenty-four sections in an arrangement")
        sections = []
        for sec in a.sections:
            if sec.key not in SECTION_KEYS:
                raise HTTPException(400, f"unknown section {sec.key[:30]}")
            if sec.show_as is not None and not NAME.match(sec.show_as):
                raise HTTPException(400, "show_as is a short name")
            sections.append({"key": sec.key, "show_as": sec.show_as, "hidden": sec.hidden})
        arrangements.append({"id": a.id, "name": a.name.strip()[:40], "sections": sections})
    if not NAME.match(body.active):
        raise HTTPException(400, "active is a short id")
    if len(body.show_as) > 40 or any(not NAME.match(k) or not NAME.match(v) for k, v in body.show_as.items()):
        raise HTTPException(400, "show_as maps short section names to short picture names")
    colours = None
    if body.colours:
        colours = {}
        for mode, roles in body.colours.items():
            if mode not in ("light", "dark"):
                raise HTTPException(400, "colours are given per mode: light and dark")
            for role, hex_ in roles.items():
                if role not in COLOUR_ROLES or not HEX.match(hex_):
                    raise HTTPException(400, f"colour {role[:20]} must be one of the part roles, as #rrggbb")
            colours[mode] = {r: h.lower() for r, h in roles.items()}
    name = body.brand.name.strip()
    if len(name) > 40 or any(ch in name for ch in "<>"):
        raise HTTPException(400, "the name is up to 40 characters, without angle brackets")
    return {"look": body.look, "active": body.active, "arrangements": arrangements, "show_as": dict(body.show_as), "colours": colours,
            "brand": {"name": name, "mark": _check_mark(body.brand.mark)}, "type_size": body.type_size, "density": body.density, "quiet": body.quiet, "calm": body.calm, "font": body.font}


@router.get("/appearance")
def get_appearance(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    saved = session.get(Setting, "appearance") is not None
    stored = get_setting(session, "appearance", {}) or {}
    return {**DEFAULT, **stored, "saved": saved}


@router.put("/appearance")
def put_appearance(body: AppearanceIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    clean = validate(body)
    set_setting(session, "appearance", clean)
    return {**clean, "saved": True}
