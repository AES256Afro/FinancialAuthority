"""Model providers behind one small interface: Ollama, any OpenAI-compatible server, Anthropic.

Every provider takes the same conversation (OpenAI-style messages) and the same tool list, and yields the
same events: text deltas, tool calls, usage, done. The advisor and the reviewer never see which one they are
talking to. Secrets arrive as plain strings from the encrypted store and go only into request headers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

ANTHROPIC_MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-fable-5-1"]
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class Event:
    type: str  # text | tool_call | usage | done | error
    text: str = ""
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    message: str = ""


class ProviderError(RuntimeError):
    pass


def _parse_arguments(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": value}


class Provider:
    """The interface. `chat` streams events; `complete` returns the whole text, for the reviewer."""

    id = "none"

    def __init__(self, model: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self._client = client

    def client(self) -> httpx.AsyncClient:
        return self._client or httpx.AsyncClient(timeout=TIMEOUT)

    async def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None, *, max_tokens: int = 1024) -> AsyncIterator[Event]:  # pragma: no cover - interface
        raise NotImplementedError
        yield Event("done")  # noqa: RET503

    async def complete(self, messages: list[dict], *, max_tokens: int = 1024) -> tuple[str, int, int]:
        text, inp, out = [], 0, 0
        async for event in self.chat(messages, None, max_tokens=max_tokens):
            if event.type == "text":
                text.append(event.text)
            elif event.type == "usage":
                inp, out = event.input_tokens, event.output_tokens
            elif event.type == "error":
                raise ProviderError(event.message)
        return "".join(text), inp, out

    async def list_models(self) -> list[str]:  # pragma: no cover - interface
        return []


class OpenAICompatible(Provider):
    """Any server that speaks `/chat/completions`: OpenAI, Ollama's `/v1`, LM Studio, vLLM, llama.cpp, OpenRouter."""

    id = "openai"

    def __init__(self, model: str, base_url: str, api_key: str = "", *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(model, client=client)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None, *, max_tokens: int = 1024) -> AsyncIterator[Event]:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        calls: dict[int, dict] = {}
        usage: tuple[int, int] | None = None
        async with self.client() as client:
            try:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers(), json=body) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        yield Event("error", message=f"{self.base_url} answered {response.status_code}: {detail}")
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = (int(chunk["usage"].get("prompt_tokens", 0)), int(chunk["usage"].get("completion_tokens", 0)))
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield Event("text", text=delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
            except httpx.HTTPError as error:
                yield Event("error", message=f"{self.base_url}: {type(error).__name__}: {error}")
                return
        for index in sorted(calls):
            slot = calls[index]
            yield Event("tool_call", id=slot["id"] or f"call_{index}", name=slot["name"], arguments=_parse_arguments(slot["arguments"]))
        if usage:
            yield Event("usage", input_tokens=usage[0], output_tokens=usage[1])
        yield Event("done")

    async def list_models(self) -> list[str]:
        async with self.client() as client:
            response = await client.get(f"{self.base_url}/models", headers=self.headers())
            response.raise_for_status()
            data = response.json().get("data", [])
            return sorted(m.get("id", "") for m in data if m.get("id"))


class Ollama(OpenAICompatible):
    """Ollama on the host, through its OpenAI-compatible endpoint; the model list from its own API."""

    id = "ollama"

    def __init__(self, model: str, url: str, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(model, url.rstrip("/") + "/v1", "", client=client)
        self.url = url.rstrip("/")

    async def list_models(self) -> list[str]:
        async with self.client() as client:
            response = await client.get(f"{self.url}/api/tags")
            response.raise_for_status()
            return sorted(m.get("name", "") for m in response.json().get("models", []) if m.get("name"))


def _to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style messages to Anthropic's: the system prompt apart, tool calls and results as content blocks."""
    system: list[str] = []
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "system":
            system.append(content)
        elif role == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": content}]})
        elif role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                blocks.append({"type": "tool_use", "id": tc.get("id", ""), "name": fn.get("name", ""), "input": _parse_arguments(fn.get("arguments", "{}"))})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""), "content": content}
            if out and out[-1]["role"] == "user" and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system), out


class Anthropic(Provider):
    id = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.anthropic.com", *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(model, client=client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION, "Content-Type": "application/json"}

    async def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None, *, max_tokens: int = 1024) -> AsyncIterator[Event]:
        system, converted = _to_anthropic(messages)
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": converted, "stream": True}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        blocks: dict[int, dict] = {}
        input_tokens = output_tokens = 0
        async with self.client() as client:
            try:
                async with client.stream("POST", f"{self.base_url}/v1/messages", headers=self.headers(), json=body) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        yield Event("error", message=f"Anthropic answered {response.status_code}: {detail}")
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        kind = event.get("type")
                        if kind == "message_start":
                            input_tokens = int(((event.get("message") or {}).get("usage") or {}).get("input_tokens", 0))
                        elif kind == "content_block_start":
                            block = event.get("content_block") or {}
                            blocks[int(event.get("index", 0))] = {"type": block.get("type"), "id": block.get("id", ""), "name": block.get("name", ""), "json": ""}
                        elif kind == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                yield Event("text", text=delta.get("text", ""))
                            elif delta.get("type") == "input_json_delta":
                                blocks.setdefault(int(event.get("index", 0)), {"type": "tool_use", "id": "", "name": "", "json": ""})["json"] += delta.get("partial_json", "")
                        elif kind == "message_delta":
                            output_tokens = int((event.get("usage") or {}).get("output_tokens", output_tokens))
                        elif kind == "error":
                            yield Event("error", message=str((event.get("error") or {}).get("message", "error")))
                            return
            except httpx.HTTPError as error:
                yield Event("error", message=f"Anthropic: {type(error).__name__}: {error}")
                return
        for index in sorted(blocks):
            block = blocks[index]
            if block["type"] == "tool_use":
                yield Event("tool_call", id=block["id"] or f"toolu_{index}", name=block["name"], arguments=_parse_arguments(block["json"] or "{}"))
        yield Event("usage", input_tokens=input_tokens, output_tokens=output_tokens)
        yield Event("done")

    async def list_models(self) -> list[str]:
        return list(ANTHROPIC_MODELS)


class Scripted(Provider):
    """A provider for tests and the demo: it plays a script of turns instead of calling anything."""

    id = "scripted"

    def __init__(self, turns: list[list[Event]], model: str = "scripted") -> None:
        super().__init__(model)
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None, *, max_tokens: int = 1024) -> AsyncIterator[Event]:
        self.seen.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else [Event("text", text="(the script ran out)"), Event("done")]
        for event in turn:
            yield event
        if not turn or turn[-1].type != "done":
            yield Event("done")

    async def list_models(self) -> list[str]:
        return ["scripted"]
