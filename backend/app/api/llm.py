"""Models, chat, rules and reviews: the REST behind Settings › Models and the Chat page."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, crypto
from ..config import settings
from ..db import get_session, session_scope
from ..engine.snapshot import finance_snapshot
from ..llm import reviewer
from ..llm.advisor import advise
from ..llm.providers import Anthropic, Ollama, OpenAICompatible, ProviderError
from ..llm.roles import DEFAULT_MODELS, PROVIDER_IDS, ROLES, SECRET_NAMES, provider_for, role_settings, save_role, save_secret, secret, secrets_state
from ..models import ReviewRun
from ..store import get_setting, set_setting
from .routes import today

router = APIRouter(prefix="/api/v1", tags=["models"])


class RoleIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    price_in: float | None = None
    price_out: float | None = None


class SecretIn(BaseModel):
    name: str
    value: str


class RulesIn(BaseModel):
    rules: str | None = None
    goals: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage]
    tools: bool = True


class NoteIn(BaseModel):
    answer: str | None = None


def models_out(session: Session) -> dict:
    return {
        "roles": role_settings(session),
        "secrets": secrets_state(session),
        "providers": [
            {"id": "none", "title": "Off"},
            {"id": "ollama", "title": "Ollama (local, free)", "url": settings.ollama_url},
            {"id": "openai", "title": "OpenAI-compatible server", "url": settings.openai_base_url or "https://api.openai.com/v1"},
            {"id": "anthropic", "title": "Anthropic (Claude by API)"},
        ],
        "can_store_secrets": bool(settings.data_key),
        "defaults": DEFAULT_MODELS,
    }


@router.get("/models")
def get_models(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    return models_out(session)


@router.put("/models/secrets")
def put_secret(body: SecretIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.name not in SECRET_NAMES:
        raise HTTPException(404, "No such secret")
    try:
        save_secret(session, body.name, body.value)
    except crypto.DataKeyMissing as error:
        raise HTTPException(409, str(error)) from error
    return {"secrets": secrets_state(session)}


@router.put("/models/{role}")
def put_role(role: str, body: RoleIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if role not in ROLES:
        raise HTTPException(404, "No such role")
    try:
        save_role(session, role, body.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return models_out(session)


@router.get("/models/catalog")
async def catalog(provider: str, base_url: str = "", session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if provider not in PROVIDER_IDS or provider == "none":
        raise HTTPException(400, "Choose a provider")
    if provider == "ollama":
        p = Ollama("", base_url or settings.ollama_url)
    elif provider == "openai":
        p = OpenAICompatible("", base_url or settings.openai_base_url or "https://api.openai.com/v1", secret(session, "openai"))
    else:
        p = Anthropic("", secret(session, "anthropic"))
    try:
        return {"models": await p.list_models()}
    except Exception as error:  # noqa: BLE001 - a listing failure is an answer, not a crash
        return {"models": [], "error": f"{type(error).__name__}: {error}"[:300]}


@router.post("/models/{role}/test")
async def test_role(role: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if role not in ROLES:
        raise HTTPException(404, "No such role")
    p = provider_for(session, role)
    if p is None:
        return {"ok": False, "error": "The role is off, or its key is missing."}
    started = time.perf_counter()
    try:
        text, inp, out = await p.complete([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=20)
    except ProviderError as error:
        return {"ok": False, "error": str(error)[:300]}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
    return {"ok": True, "reply": text.strip()[:200], "ms": int((time.perf_counter() - started) * 1000), "input_tokens": inp, "output_tokens": out, "model": p.model}


@router.get("/rules")
def get_rules(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"rules": get_setting(session, "rules", "") or "", "goals": get_setting(session, "goals", "") or "", "buffer": float(get_setting(session, "buffer", 200.0))}


@router.put("/rules")
def put_rules(body: RulesIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.rules is not None:
        set_setting(session, "rules", body.rules.strip())
    if body.goals is not None:
        set_setting(session, "goals", body.goals.strip())
    reviewer.mark_pending(session, "rules changed")
    return get_rules(session)


@router.post("/chat")
async def chat(body: ChatIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> StreamingResponse:
    if not body.messages or len(body.messages) > 60:
        raise HTTPException(400, "Between one and sixty messages")
    history = []
    for m in body.messages:
        if m.role not in ("user", "assistant"):
            raise HTTPException(400, "Messages are from the user or the assistant")
        history.append({"role": m.role, "content": m.content[:8000]})
    if history[-1]["role"] != "user":
        raise HTTPException(400, "The last message must be the owner's")
    provider = provider_for(session, "advisor")
    day = today()

    def engine_voice(reason: str) -> list[dict]:
        """With no model to talk to, the engine answers in its own sentences: the brief, every figure its own."""
        with session_scope() as s:
            brief = finance_snapshot(s, day)["brief"]
        text = "\n".join(f"• {b['text']}" for b in brief)
        return [
            {"type": "error", "message": reason},
            {"type": "text", "text": "Here is the engine's brief, in its own words:\n" + text},
            {"type": "done", "unverified": [], "used": [{"name": "brief_today", "arguments": {}, "rows": len(brief)}], "usage": {"input": 0, "output": 0}, "model": "engine", "provider": "engine"},
        ]

    async def stream():
        if provider is None:
            for event in engine_voice("No advisor is configured. Choose one on Settings › Models."):
                yield "data: " + json.dumps(event) + "\n\n"
            return
        first = True
        try:
            async for event in advise(history, provider=provider, session_factory=session_scope, today=day, tools=body.tools):
                if first and event["type"] == "error":
                    for fallback in engine_voice(f"The advisor did not answer ({event['message'][:160]})."):
                        yield "data: " + json.dumps(fallback) + "\n\n"
                    return
                first = False
                yield "data: " + json.dumps(event, default=str) + "\n\n"
        except Exception as error:  # noqa: BLE001 - the screen gets the failure as an event
            yield "data: " + json.dumps({"type": "error", "message": f"{type(error).__name__}: {error}"[:300]}) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/reviews")
def list_reviews(limit: int = 10, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    runs = session.exec(select(ReviewRun).order_by(ReviewRun.created_at.desc()).limit(max(1, min(limit, 50)))).all()
    pending = get_setting(session, "review.pending", None)
    return {"reviews": [reviewer.run_out(r) for r in runs], "pending": pending, "configured": provider_for(session, "reviewer") is not None}


@router.post("/reviews")
async def run_review(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    run = await reviewer.review(session_scope, today(), "request")
    return reviewer.run_out(run)


@router.post("/reviews/{run_id}/notes/{n}/apply")
def apply_note(run_id: int, n: int, body: NoteIn | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    run = session.get(ReviewRun, run_id)
    if run is None:
        raise HTTPException(404, "No such review")
    try:
        note = reviewer.apply_note(session, run, n, answer=body.answer if body else None)
    except KeyError as error:
        raise HTTPException(404, "No such note") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"note": note, "review": reviewer.run_out(run)}


@router.post("/reviews/{run_id}/notes/{n}/dismiss")
def dismiss_note(run_id: int, n: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    run = session.get(ReviewRun, run_id)
    if run is None:
        raise HTTPException(404, "No such review")
    try:
        note = reviewer.apply_note(session, run, n, dismiss=True)
    except KeyError as error:
        raise HTTPException(404, "No such note") from error
    return {"note": note, "review": reviewer.run_out(run)}

