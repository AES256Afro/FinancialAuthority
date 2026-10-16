"""The two model roles, their settings and the provider each resolves to.

The advisor talks in the chat and explains; the reviewer reads the plan and the rules and pushes back.
Each is a provider plus a model, kept in the settings table. API keys are encrypted with the data key and
never leave the server; the environment can supply them too (FA_ANTHROPIC_API_KEY, FA_OPENAI_API_KEY).
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .. import crypto
from ..config import settings
from ..store import get_setting, set_setting
from .providers import Anthropic, Ollama, OpenAICompatible, Provider

ROLES = ("advisor", "reviewer")
PROVIDER_IDS = ("none", "ollama", "openai", "anthropic")
SECRET_NAMES = ("anthropic", "openai")

DEFAULT_ROLES: dict[str, dict[str, Any]] = {
    "advisor": {"provider": "ollama", "model": "llama3.2", "base_url": "", "price_in": None, "price_out": None, "sees": "full"},
    "reviewer": {"provider": "none", "model": "claude-sonnet-5-5", "base_url": "", "price_in": None, "price_out": None},
}

# What each provider uses when a role has no model of its own: a small model explains, a strong one reviews.
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "ollama": {"advisor": "llama3.2", "reviewer": "llama3.2"},
    "openai": {"advisor": "", "reviewer": ""},
    "anthropic": {"advisor": "claude-haiku-5-5", "reviewer": "claude-sonnet-5-5"},
    "none": {"advisor": "", "reviewer": ""},
}


def model_fits(provider: str, model: str) -> bool:
    """Whether a model name can belong to a provider: Anthropic's are all claude-…, and a bare claude-… name
    belongs to no other server (OpenRouter and the like prefix it)."""
    m = (model or "").strip().lower()
    if not m:
        return False
    return m.startswith("claude") if provider == "anthropic" else not m.startswith("claude-")


_overrides: dict[str, Provider] = {}


def set_override(role: str, provider: Provider | None) -> None:
    """Tests and the demo put a scripted provider behind a role."""
    if provider is None:
        _overrides.pop(role, None)
    else:
        _overrides[role] = provider


def role_settings(session: Session) -> dict[str, dict[str, Any]]:
    stored = get_setting(session, "models", {}) or {}
    out = {}
    for role in ROLES:
        merged = {**DEFAULT_ROLES[role], **(stored.get(role) or {})}
        if merged["provider"] not in PROVIDER_IDS:
            merged["provider"] = "none"
        out[role] = merged
    return out


def save_role(session: Session, role: str, values: dict[str, Any]) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError("unknown role")
    current = get_setting(session, "models", {}) or {}
    merged = {**DEFAULT_ROLES[role], **(current.get(role) or {})}
    for key in ("provider", "model", "base_url"):
        if key in values and values[key] is not None:
            merged[key] = str(values[key]).strip()
    for key in ("price_in", "price_out"):
        if key in values:
            merged[key] = None if values[key] in (None, "") else float(values[key])
    if values.get("sees") is not None:
        if values["sees"] not in ("full", "merchants", "aggregates"):
            raise ValueError("sees is full, merchants or aggregates")
        merged["sees"] = values["sees"]
    if merged["provider"] not in PROVIDER_IDS:
        raise ValueError("unknown provider")
    fallback = DEFAULT_MODELS[merged["provider"]][role]
    if fallback and not model_fits(merged["provider"], merged["model"]):
        # A model left over from another provider is replaced by this provider's own, never sent where it cannot work.
        merged["model"] = fallback
    current[role] = merged
    set_setting(session, "models", current)
    return merged


def secrets_state(session: Session) -> dict[str, bool]:
    stored = get_setting(session, "models.secrets", {}) or {}
    env = {"anthropic": bool(settings.anthropic_api_key), "openai": bool(settings.openai_api_key)}
    return {name: bool(stored.get(name)) or env[name] for name in SECRET_NAMES}


def save_secret(session: Session, name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError("unknown secret")
    stored = get_setting(session, "models.secrets", {}) or {}
    if value.strip():
        stored[name] = crypto.encrypt(value.strip())
    else:
        stored.pop(name, None)
    set_setting(session, "models.secrets", stored)


def secret(session: Session, name: str) -> str:
    stored = get_setting(session, "models.secrets", {}) or {}
    if stored.get(name):
        try:
            return crypto.decrypt(stored[name])
        except (ValueError, crypto.DataKeyMissing):
            return ""
    return {"anthropic": settings.anthropic_api_key, "openai": settings.openai_api_key}.get(name, "")


def provider_for(session: Session, role: str) -> Provider | None:
    """The provider behind a role, or None when the role is off."""
    if role in _overrides:
        return _overrides[role]
    conf = role_settings(session)[role]
    kind = conf["provider"]
    model = conf["model"] or DEFAULT_MODELS.get(kind, {}).get(role) or DEFAULT_ROLES[role]["model"]
    if kind == "ollama":
        return Ollama(model, conf["base_url"] or settings.ollama_url)
    if kind == "openai":
        return OpenAICompatible(model, conf["base_url"] or settings.openai_base_url or "https://api.openai.com/v1", secret(session, "openai"))
    if kind == "anthropic":
        key = secret(session, "anthropic")
        return Anthropic(model, key) if key else None
    return None


def cost_of(conf: dict[str, Any], input_tokens: int, output_tokens: int) -> float | None:
    """Dollars, when the owner entered prices per million tokens for the role; otherwise unknown."""
    if conf.get("price_in") is None or conf.get("price_out") is None:
        return None
    return round(input_tokens * float(conf["price_in"]) / 1e6 + output_tokens * float(conf["price_out"]) / 1e6, 4)
