"""The model layer: the citation check, the advisor's tool loop, the reviewer's notes and actions, the
provider request shapes, and the REST around them. No model is called; providers are scripted or mocked."""

import asyncio
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine, session_scope
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot
from app.llm import reviewer, roles
from app.llm.advisor import advise, compact_plan
from app.llm.citations import engine_numbers, figures_in, unverified
from app.llm.providers import Anthropic, Event, OpenAICompatible, Scripted, ToolSpec
from app.models import ReviewRun
from app.store import get_setting, set_setting

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    roles.set_override("advisor", None)
    roles.set_override("reviewer", None)


def seeded() -> dict:
    with Session(engine) as session:
        seed(session)
        return finance_snapshot(session, TODAY)


# ---- citations -----------------------------------------------------------------------------


def test_figures_are_found_and_dates_and_years_are_not():
    found = [t for t, _ in figures_in("Rent is $1,250.00 on 2026-11-01; 3 bills; in 2026 the pace was 42.5 a day and 1500 total, 12.5% APR")]
    assert found == ["$1,250.00", "42.5", "1500", "12.5%"]


def test_unverified_allows_dropped_cents_and_flags_invented_figures():
    allowed = engine_numbers({"a": 1250.0, "b": {"c": [42.57, -231.0]}, "d": "fee $4.99"})
    assert unverified("Rent $1,250 and the pace 42.57, advances 231, fee 4.99", allowed) == []
    assert unverified("Rent $1,250 and 42.6 a day, plus $999.99 I made up", allowed) == ["42.6", "$999.99"]


def test_every_number_in_the_snapshot_is_allowed_for_the_advisor():
    snap = seeded()
    plan = compact_plan(snap)
    allowed = engine_numbers(plan)
    text = f"Safe to spend ${snap['safe_to_spend']['amount']:,.2f}; advances take ${snap['advances']['total']:,.2f} first."
    assert unverified(text, allowed) == []


# ---- the advisor's loop --------------------------------------------------------------------


def run_advise(provider: Scripted, question: str, tools: bool = True) -> list[dict]:
    async def collect():
        out = []
        async for event in advise([{"role": "user", "content": question}], provider=provider, session_factory=session_scope, today=TODAY, tools=tools):
            out.append(event)
        return out

    return asyncio.run(collect())


def test_advisor_calls_a_tool_then_answers_with_the_engine_numbers():
    snap = seeded()
    safe = snap["safe_to_spend"]["amount"]
    provider = Scripted([
        [Event("tool_call", id="c1", name="brief_today", arguments={}), Event("usage", input_tokens=10, output_tokens=2), Event("done")],
        [Event("text", text=f"You can spend ${safe:,.2f} through the window. "), Event("text", text="Advances go first."), Event("usage", input_tokens=20, output_tokens=12), Event("done")],
    ])
    events = run_advise(provider, "What can I spend?")
    kinds = [e["type"] for e in events]
    assert kinds == ["tool", "text", "text", "done"]
    assert events[0]["name"] == "brief_today" and events[0]["rows"] > 0
    done = events[-1]
    assert done["unverified"] == [] and done["usage"] == {"input": 30, "output": 14} and done["used"][0]["name"] == "brief_today"
    # The system prompt carried the plan, the tool result went back as a tool message, the tools were offered.
    first, second = provider.seen
    assert first[0]["role"] == "system" and "safe_to_spend" in first[0]["content"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1" and "brief" in second[-1]["content"]
    with Session(engine) as session:
        from app.models import AuditEntry

        entry = session.exec(select(AuditEntry).where(AuditEntry.tool == "brief_today")).first()
        assert entry is not None and entry.client == "advisor"


def test_advisor_reply_with_an_invented_figure_is_flagged():
    seeded()
    provider = Scripted([[Event("text", text="Just put $4,321.99 aside and you are fine."), Event("done")]])
    events = run_advise(provider, "Am I fine?")
    assert events[-1]["unverified"] == ["$4,321.99"]


def test_advisor_refuses_tools_outside_the_chat_set():
    seeded()
    provider = Scripted([
        [Event("tool_call", id="c1", name="facts_confirm", arguments={"fact_id": 1}), Event("done")],
        [Event("text", text="I cannot confirm that here."), Event("done")],
    ])
    events = run_advise(provider, "Confirm fact 1")
    assert events[0]["type"] == "tool" and events[0]["name"] == "facts_confirm"
    assert "not available" in provider.seen[1][-1]["content"]


def test_chat_route_streams_events_and_needs_an_advisor(owner: TestClient):
    seeded()
    off = owner.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert off.status_code == 200
    off_events = [json.loads(l[5:]) for l in off.text.splitlines() if l.startswith("data:")]
    # Ollama is not reachable in tests: the error is reported, then the engine answers in its own words.
    assert off_events[0]["type"] == "error" and "did not answer" in off_events[0]["message"]
    assert off_events[1]["type"] == "text" and "Safe to spend" in off_events[1]["text"] and off_events[-1]["model"] == "engine"
    roles.set_override("advisor", Scripted([[Event("text", text="Payday is in 2 days."), Event("done")]]))
    streamed = owner.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "When is payday?"}]})
    assert streamed.status_code == 200 and streamed.headers["content-type"].startswith("text/event-stream")
    lines = [json.loads(l[5:]) for l in streamed.text.splitlines() if l.startswith("data:")]
    assert lines[0] == {"type": "text", "text": "Payday is in 2 days."} and lines[-1]["type"] == "done"
    assert owner.post("/api/v1/chat", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 400


# ---- the reviewer --------------------------------------------------------------------------


def test_reviewer_parses_notes_applies_actions_and_records_cost(owner: TestClient):
    snap = seeded()
    buffer = snap["buffer"]
    reply = json.dumps({"notes": [
        {"kind": "tighten", "text": f"The buffer of ${buffer:.0f} is thin for a household with advances; I would hold more.", "action": {"type": "buffer", "value": 300}},
        {"kind": "disagree", "text": "Set-asides for bills under $500 should not be halved.", "action": {"type": "rule", "text": "Set aside the whole of any bill due in the next window."}},
        {"kind": "ask", "text": "Is the credit union loan on autopay?", "action": {"type": "ask", "text": "Is the credit union loan on autopay?"}},
        {"kind": "ok", "text": "Paying advances first is right; they take the money anyway.", "action": {"type": "none"}},
    ]})
    provider = Scripted([[Event("text", text="Here you go:\n" + reply), Event("usage", input_tokens=4000, output_tokens=300), Event("done")]])
    with session_scope() as session:
        roles.save_role(session, "reviewer", {"provider": "anthropic", "model": "claude-sonnet-5-5", "price_in": 3.0, "price_out": 15.0})
    run = asyncio.run(reviewer.review(session_scope, TODAY, "request", provider=provider))
    assert run.ok, run.error
    notes = json.loads(run.notes)
    assert [n["kind"] for n in notes] == ["tighten", "disagree", "ask", "ok"]
    assert notes[0]["unverified"] == [] and notes[1]["unverified"] == []
    assert run.cost == round(4000 * 3.0 / 1e6 + 300 * 15.0 / 1e6, 4)
    assert "safe_to_spend" in provider.seen[0][0]["content"] and "Rent" not in provider.seen[0][0]["content"].split("accounts")[0]

    listed = owner.get("/api/v1/reviews").json()
    assert listed["reviews"][0]["id"] == run.id and listed["pending"] is None
    applied = owner.post(f"/api/v1/reviews/{run.id}/notes/0/apply").json()
    assert applied["note"]["status"] == "applied"
    rule = owner.post(f"/api/v1/reviews/{run.id}/notes/1/apply").json()
    assert rule["note"]["status"] == "applied"
    assert owner.post(f"/api/v1/reviews/{run.id}/notes/2/apply", json={}).status_code == 400
    answered = owner.post(f"/api/v1/reviews/{run.id}/notes/2/apply", json={"answer": "Yes, on the 5th."}).json()
    assert answered["note"]["status"] == "applied"
    dismissed = owner.post(f"/api/v1/reviews/{run.id}/notes/3/dismiss").json()
    assert dismissed["note"]["status"] == "dismissed"
    with Session(engine) as session:
        assert float(get_setting(session, "buffer")) == 300.0
        assert "Set aside the whole" in get_setting(session, "rules", "")
        assert "Answered: Yes, on the 5th." in get_setting(session, "goals", "")
        snap2 = finance_snapshot(session, TODAY)
    assert snap2["buffer"] == 300.0  # the engine took the owner's new buffer; the reviewer changed no number itself


def test_reviewer_without_a_model_records_why_and_a_bad_reply_fails_cleanly():
    seeded()
    run = asyncio.run(reviewer.review(session_scope, TODAY, "request"))
    assert run.ok is False and "no reviewer" in run.error
    provider = Scripted([[Event("text", text="I would rather chat about it."), Event("done")]])
    run = asyncio.run(reviewer.review(session_scope, TODAY, "payday", provider=provider))
    assert run.ok is False and "ValueError" in run.error and run.raw.startswith("I would")


def test_review_job_runs_after_a_change_and_once_on_payday():
    seeded()
    assert reviewer.run_if_due(session_scope, TODAY) == "reviewer off"
    roles.set_override("reviewer", Scripted([
        [Event("text", text=json.dumps({"notes": [{"kind": "ok", "text": "Fine.", "action": {"type": "none"}}]})), Event("done")],
        [Event("text", text=json.dumps({"notes": [{"kind": "ok", "text": "Payday looks fine.", "action": {"type": "none"}}]})), Event("done")],
    ]))
    assert reviewer.run_if_due(session_scope, TODAY) == "nothing due"
    with session_scope() as session:
        reviewer.mark_pending(session, "bill changed")
    assert reviewer.run_if_due(session_scope, TODAY).startswith("reviewed after a change: ok")
    with session_scope() as session:
        payday = date.fromisoformat(finance_snapshot(session, TODAY)["next_paycheck"]["date"])
    assert reviewer.run_if_due(session_scope, payday).startswith("payday review: ok")
    assert reviewer.run_if_due(session_scope, payday) == "nothing due"
    with Session(engine) as session:
        assert [r.trigger for r in session.exec(select(ReviewRun).order_by(ReviewRun.id)).all()] == ["change", "payday"]


def test_confirming_a_bill_or_changing_one_asks_for_a_review(owner: TestClient):
    seeded()
    made = owner.post("/api/v1/bills", json={"name": "Water", "amount": 40.0, "due_day": 12, "kind": "bill"})
    assert made.status_code == 200, made.text
    with Session(engine) as session:
        assert get_setting(session, "review.pending")["reason"] == "bill added: Water"


# ---- models settings -----------------------------------------------------------------------


def test_models_settings_store_roles_and_secrets_without_showing_them(owner: TestClient):
    got = owner.get("/api/v1/models").json()
    assert got["roles"]["advisor"]["provider"] == "ollama" and got["roles"]["reviewer"]["provider"] == "none"
    assert got["secrets"] == {"anthropic": False, "openai": False} and got["can_store_secrets"] is True
    put = owner.put("/api/v1/models/reviewer", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "price_in": 3, "price_out": 15}).json()
    assert put["roles"]["reviewer"]["provider"] == "anthropic" and put["roles"]["reviewer"]["price_out"] == 15.0
    assert owner.put("/api/v1/models/reviewer", json={"provider": "carrier-pigeon"}).status_code == 400
    stored = owner.put("/api/v1/models/secrets", json={"name": "anthropic", "value": "sk-ant-test-value"}).json()
    assert stored["secrets"]["anthropic"] is True
    with Session(engine) as session:
        raw = get_setting(session, "models.secrets")["anthropic"]
        assert raw.startswith("fa1:") and "sk-ant" not in raw
        assert roles.secret(session, "anthropic") == "sk-ant-test-value"
        p = roles.provider_for(session, "reviewer")
    assert isinstance(p, Anthropic) and p.model == "claude-sonnet-5-5"
    assert "sk-ant" not in json.dumps(owner.get("/api/v1/models").json())
    rules = owner.put("/api/v1/rules", json={"rules": "- Never let checking go under $100.", "goals": "Clear the advances by spring."}).json()
    assert rules["rules"].startswith("- Never") and rules["goals"].startswith("Clear")
    with Session(engine) as session:
        set_setting(session, "models.secrets", {})


# ---- provider request shapes ---------------------------------------------------------------


def test_openai_compatible_streams_text_tool_calls_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        chunks = [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "bills_list", "arguments": "{\"li"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "mit\": 5}"}}]}}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatible("gpt-test", "http://llm.test/v1", "sk-test", client=client)

    async def run():
        return [e async for e in provider.chat([{"role": "user", "content": "hi"}], [ToolSpec("bills_list", "Bills", {"type": "object", "properties": {}})])]

    events = asyncio.run(run())
    assert [e.type for e in events] == ["text", "text", "tool_call", "usage", "done"]
    assert events[2].name == "bills_list" and events[2].arguments == {"limit": 5}
    assert events[3].input_tokens == 12 and events[3].output_tokens == 7
    assert seen["url"] == "http://llm.test/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["tools"][0]["function"]["name"] == "bills_list" and seen["body"]["stream"] is True


def test_anthropic_converts_messages_and_streams_tool_use():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 50}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Looking."}},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "brief_today"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "message_delta", "usage": {"output_tokens": 9}},
            {"type": "message_stop"},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = Anthropic("claude-sonnet-5-5", "sk-ant-x", client=client)
    history = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "What is due?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "t0", "type": "function", "function": {"name": "bills_list", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t0", "content": "{\"bills\": []}"},
    ]

    async def run():
        return [e async for e in provider.chat(history, [ToolSpec("brief_today", "Brief", {"type": "object", "properties": {}})])]

    events = asyncio.run(run())
    assert [e.type for e in events] == ["text", "tool_call", "usage", "done"]
    assert events[1].id == "toolu_1" and events[1].name == "brief_today"
    assert events[2].input_tokens == 50 and events[2].output_tokens == 9
    body = seen["body"]
    assert seen["key"] == "sk-ant-x" and body["system"] == "Be brief." and body["tools"][0]["input_schema"]["type"] == "object"
    assert body["messages"][1]["content"][0]["type"] == "tool_use" and body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "t0", "content": "{\"bills\": []}"}


def test_ollama_falls_back_to_a_model_the_server_has():
    from app.llm.providers import Ollama

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "hermes3:8b"}, {"name": "qwen3:30b-a3b"}]})
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "llama3.2":
            return httpx.Response(404, json={"error": {"message": "model \"llama3.2\" not found, try pulling it first"}})
        chunks = [{"choices": [{"delta": {"content": "ready"}}]}, {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}]
        return httpx.Response(200, text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    provider = Ollama("llama3.2", "http://ollama.test:11434", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def run():
        return [e async for e in provider.chat([{"role": "user", "content": "hi"}])]

    events = asyncio.run(run())
    assert [e.type for e in events] == ["text", "usage", "done"] and events[0].text == "ready"
    assert calls == ["llama3.2", "hermes3:8b"] and provider.model == "hermes3:8b"


def test_a_provider_error_reads_as_what_to_do():
    from app.llm.providers import plain_error

    body = json.dumps({"type": "error", "error": {"type": "not_found_error", "message": "model: llama3.2"}})
    assert plain_error("Anthropic", 404, body, "llama3.2") == 'Anthropic has no model called "llama3.2". Tap Find models and pick one from the list.'
    assert plain_error("Anthropic", 401, json.dumps({"error": {"type": "authentication_error", "message": "invalid x-api-key"}}), "m").startswith("Anthropic did not accept the key")
    assert "out of credit" in plain_error("Anthropic", 400, json.dumps({"error": {"type": "invalid_request_error", "message": "Your credit balance is too low"}}), "m")
    assert plain_error("Example", 500, "upstream broke", "m") == "Example answered 500: upstream broke"


def test_a_model_left_over_from_another_provider_is_replaced(owner: TestClient):
    got = owner.put("/api/v1/models/advisor", json={"provider": "anthropic", "model": "llama3.2"}).json()
    assert got["roles"]["advisor"]["model"] == got["defaults"]["anthropic"]["advisor"] == "claude-haiku-5-5"
    kept = owner.put("/api/v1/models/advisor", json={"provider": "anthropic", "model": "claude-sonnet-5-5"}).json()
    assert kept["roles"]["advisor"]["model"] == "claude-sonnet-5-5"
    back = owner.put("/api/v1/models/advisor", json={"provider": "ollama", "model": "claude-sonnet-5-5"}).json()
    assert back["roles"]["advisor"]["model"] == "llama3.2"
    local = owner.put("/api/v1/models/advisor", json={"provider": "openai", "model": "llama3.2"}).json()
    assert local["roles"]["advisor"]["model"] == "llama3.2"  # a local OpenAI-compatible server may well have it
    owner.put("/api/v1/models/advisor", json={"provider": "ollama", "model": "llama3.2"})


def test_anthropic_lists_the_models_its_key_can_use():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models" and request.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"data": [{"id": "claude-new-1"}, {"id": "claude-new-2"}], "has_more": False})

    live = Anthropic("", "k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(live.list_models()) == ["claude-new-1", "claude-new-2"]
    down = Anthropic("", "k", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    from app.llm.providers import ANTHROPIC_MODELS

    assert asyncio.run(down.list_models()) == ANTHROPIC_MODELS and asyncio.run(Anthropic("", "").list_models()) == ANTHROPIC_MODELS
