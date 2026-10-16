"""Privacy you control: every place a model is used, with a switch; every service that receives anything, with
what it receives; and, once, deleting everything.

Deleting everything removes every account, transaction, bill, message record, fact, plan, setting and restore
point, disconnects each bank at its vendor, and leaves only what lets the owner keep signing in (this browser's
session, passkeys, the second step). It needs the password typed again, the second-step code when that is on,
and a typed phrase. Nothing comes back from it; the off-box copies on their own disk are not touched, and the
answer says where they are."""

from __future__ import annotations

import hmac
import logging
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from .. import auth, services
from ..config import settings
from ..db import bump_version, engine, get_session
from ..store import get_setting

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])
PHRASE = "delete everything"
KEEP_TABLES = {"web_sessions", "passkeys"}
KEEP_SETTINGS = {"auth.totp", "auth.totp.last_step"}

# What the chat's model sees at each level the owner can choose on Models.
ADVISOR_SEES = {
    "full": "Your questions, the plan's figures and names, and, when you ask, your mail and your documents (boxed as data, never as instructions). Never account numbers.",
    "merchants": "Your questions, the plan's figures and who was paid. Never descriptions or notes, mail, documents or account numbers.",
    "aggregates": "Your questions and the plan's amounts, dates and categories only. Never a name, mail, documents or account numbers.",
}
ROLE_WORDS = {
    "advisor": ("The chat", ADVISOR_SEES["full"]),
    "reviewer": ("Notes on your plan after payday", "The plan's figures, your rules and goals, and alert summaries. Never mail, attachments or account numbers."),
}


@router.get("")
def privacy(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..connectors.banks import bank_state
    from ..llm.roles import role_settings
    from ..models import ApiKey, BankLink, OAuthClient
    from ..notify import has_devices, push_state

    roles = role_settings(session)
    models = []
    for role, (what, sees) in ROLE_WORDS.items():
        provider = roles[role]["provider"]
        if role == "advisor":
            sees = ADVISOR_SEES.get(str(roles[role].get("sees") or "full"), sees)
        models.append({"role": role, "what": what, "on": provider != "none", "provider": provider, "model": roles[role]["model"] if provider != "none" else None,
                       "local": provider == "ollama", "sees": sees,
                       "where": "on your own computer (Ollama)" if provider == "ollama" else "off" if provider == "none" else "Anthropic, by API" if provider == "anthropic" else "an OpenAI-compatible server you set"})
    links = session.exec(select(BankLink).where(BankLink.active == True)).all()  # noqa: E712
    push = push_state(session)
    leaves = [
        {"service": "Plaid", "on": any(l.provider == "plaid" for l in links), "gets": "Its own read-only access to the banks you linked through it; the app asks it for transactions and balances.", "why": "bank data"},
        {"service": "Teller", "on": any(l.provider == "teller" for l in links), "gets": "The same, for banks linked through Teller.", "why": "bank data"},
        {"service": "Anthropic", "on": any(m["provider"] == "anthropic" for m in models), "gets": "What the models above say they see, when that role uses Claude.", "why": "a model"},
        {"service": "An OpenAI-compatible server", "on": any(m["provider"] == "openai" for m in models), "gets": "What the models above say they see, when that role uses it.", "why": "a model"},
        {"service": "ntfy or Gotify", "on": bool(push.get("configured")), "gets": "The text of each notification: the brief, reminders.", "why": "notifications"},
        {"service": "Your browser's push service", "on": has_devices(session), "gets": "Each notification's text, end-to-end encrypted to your device.", "why": "notifications"},
    ]
    apps = session.exec(select(OAuthClient).where(OAuthClient.revoked_at == None)).all()  # noqa: E711
    keys = session.exec(select(ApiKey).where(ApiKey.revoked_at == None)).all()  # noqa: E711
    return {
        "models": models,
        "leaves": leaves,
        "readers": {"apps": len(apps), "keys": len(keys)},
        "quiet": bool((get_setting(session, "appearance", {}) or {}).get("quiet")),
        "plaid_environment": bank_state(session, "plaid").get("environment"),
        "export": {"format": "json", "note": "Everything as one file: every register and the plan, in JSON."},
        "offbox": settings.offbox_dir or None,
    }


class SwitchIn(BaseModel):
    on: bool


@router.post("/models/{role}")
def switch_model(role: str, body: SwitchIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    """One tap to turn a model off; turning one back on starts the chat on the local model, and the reviewer needs
    a model chosen on Models."""
    from ..llm.roles import save_role

    if role not in ROLE_WORDS:
        raise HTTPException(404, "No such feature")
    if body.on and role == "reviewer":
        raise HTTPException(409, "Choose the reviewer's model on Settings › Models to turn it on.")
    save_role(session, role, {"provider": "ollama" if body.on else "none"})
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="privacy_switch", args={"role": role, "on": body.on}, rows=0)
    return privacy(session)


class EraseIn(BaseModel):
    phrase: str
    password: str
    code: str = ""


@router.post("/erase")
def erase(body: EraseIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    from ..connectors.plaid import PlaidConnector
    from ..factors import check_code, second_step_on
    from ..models import BankLink

    if body.phrase.strip().lower() != PHRASE:
        raise HTTPException(400, f'Type the words "{PHRASE}" to confirm.')
    if auth.auth_enabled() and not hmac.compare_digest(body.password.encode(), settings.password.encode()):
        raise HTTPException(401, "That is not the password.")
    if second_step_on(session) and not check_code(session, body.code):
        raise HTTPException(401, "Type the code your authenticator app shows now.")
    # Each bank is disconnected at its vendor first, while the app still holds the tokens.
    plaid = PlaidConnector()
    api = plaid.api(session)
    for link in session.exec(select(BankLink).where(BankLink.active == True)).all():  # noqa: E712
        if link.provider == "plaid":
            plaid.disconnect(session, api, link)
    session.commit()
    session.close()
    erased: dict[str, int] = {}
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        tables = [r[0] for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")).all()]
        for table in tables:
            if table in KEEP_TABLES:
                continue
            if table == "settings":
                keep = ", ".join(f"'{k}'" for k in sorted(KEEP_SETTINGS))
                erased[table] = connection.execute(text(f"DELETE FROM settings WHERE key NOT IN ({keep})")).rowcount or 0
            else:
                erased[table] = connection.execute(text(f'DELETE FROM "{table}"')).rowcount or 0
        connection.execute(text("PRAGMA foreign_keys=ON"))
    with engine.connect() as connection:
        connection.execute(text("VACUUM"))  # freed pages hold the old rows until the file is rebuilt
    from ..restore import folder

    points = 0
    for path in folder().glob("*"):
        try:
            path.unlink() if path.is_file() else shutil.rmtree(path)
            points += 1
        except OSError as error:
            log.warning("could not remove %s: %s", path.name, error)
    from ..engine.documents import folder as documents_folder

    for path in documents_folder().glob("*.bin"):
        path.unlink(missing_ok=True)
    bump_version()
    left = ["Your sign-in: this browser's session, your passkeys and the second step."]
    if settings.offbox_dir:
        left.append(f"Off-box copies on their own disk, at {settings.offbox_dir}: delete them there if you want them gone too.")
    left.append(f"Files you put in the import folder ({settings.import_root}).")
    return {"ok": True, "rows": sum(erased.values()), "restore_points": points, "left": left}
