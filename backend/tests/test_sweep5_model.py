"""Security sweep 5, the model surface: scopes and audit on every tool call, redaction at the cloud levels,
the citation check against smuggled figures, the reviewer's frame for mail words, pushes that never raise,
and provider streams that cannot grow without bound. No model and no network: providers are mocked."""

import asyncio
import json
from datetime import UTC, datetime, date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto, mcp_server as mcp, notify
from app.db import engine
from app.engine.household import seed
from app.llm import reviewer, roles
from app.llm.advisor import system_prompt
from app.llm.citations import unverified
from app.llm.providers import OpenAICompatible
from app.models import AuditEntry, Debt, ExtractedFact, JobRun, MailAccount, MessageRef
from app.store import set_setting

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    roles.set_override("advisor", None)
    roles.set_override("reviewer", None)


def call(name: str, args: dict, level: str = "full", who: str = "sweep") -> dict:
    """One tool, as the built-in chat calls it: no bearer token, the caller named and its redaction set."""
    token = mcp.CALLER.set((who, level))
    try:
        result = asyncio.run(mcp.mcp_server.call_tool(name, args))
    finally:
        mcp.CALLER.reset(token)
    text = "".join(getattr(c, "text", "") for c in result.content)
    return result.structured_content or json.loads(text)


def hostile_mail(session: Session, *, trust: str = "") -> int:
    account = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="owner@example.test", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
    session.add(account)
    session.commit()
    ref = MessageRef(account_id=account.id, folder="INBOX", uid=5, sender="billing@hostile-sender.example", sender_name="Hostile Sender Co", subject="Past due statement for account 4421", kind="past_due",
                     date=datetime(2026, 10, 10, tzinfo=UTC), trust=trust, trust_why="It comes from hostile-sender.example, a look-alike of example-bank.com." if trust else "")
    session.add(ref)
    session.commit()
    session.add(ExtractedFact(kind="past_due", message_ref_id=ref.id, fields=json.dumps({"amount": 50.0, "sender_name": "Hostile Sender Co", "subject": ref.subject}), confidence=0.9, status="pending", excerpt="past due $50.00"))
    session.commit()
    return ref.id


# ---- the citation check ----------------------------------------------------------------------


def test_a_figure_with_a_suffix_or_in_words_is_still_checked():
    # "$1.2k", "1200USD" and "four thousand dollars" are all amounts the engine did not return
    assert unverified("Put about $1.2k aside.", set()) != []
    assert unverified("That is 1200USD a month.", set()) != []
    assert unverified("You owe four thousand dollars.", set()) != []
    assert unverified("Save twelve hundred by March.", set()) != []
    # and each of them passes when the engine did return it
    assert unverified("Put about $1.2k aside.", {1200.0}) == []
    assert unverified("You owe four thousand dollars.", {4000.0}) == []
    assert unverified("Save twelve hundred by March.", {1200.0}) == []
    # what was fine stays fine
    assert unverified("Payday is 2026-10-16; 3 bills; in 2026.", set()) == []


# ---- redaction at the cloud levels -----------------------------------------------------------


def test_the_aggregates_prompt_carries_no_mail_names_in_the_brief():
    with Session(engine) as session:
        seed(session)
        hostile_mail(session)
        roles.save_role(session, "advisor", {"sees": "aggregates"})
        prompt, _ = system_prompt(session, TODAY)
        full, _ = system_prompt(session, TODAY) if roles.save_role(session, "advisor", {"sees": "full"}) else ("", set())
    assert "Hostile Sender Co" in full  # the brief names the sender at the full level
    assert "Hostile Sender Co" not in prompt and "hostile-sender" not in prompt


def test_data_check_hides_the_sender_address_and_subject_from_a_redacted_client():
    with Session(engine) as session:
        seed(session)
        hostile_mail(session, trust="crit")
    plain = json.dumps(call("data_check", {}, "full"))
    assert "billing@hostile-sender.example" in plain and "account 4421" in plain
    for level in ("merchants", "aggregates"):
        text = json.dumps(call("data_check", {}, level))
        assert "billing@hostile-sender.example" not in text, level
        assert "account 4421" not in text, level
        assert "hostile-sender.example" not in text, level


def test_jobs_status_hides_account_names_and_hosts_from_a_redacted_client():
    with Session(engine) as session:
        set_setting(session, "selfcheck", {"at": "2026-10-14T06:00:00+00:00", "free_gb": 40.0, "stale_accounts": ["Example Checking"], "problems": ["stale balances: Example Checking"],
                                           "sources": [{"name": "mail", "error": "imap.example-host.test refused the sign-in", "last_ok": None}]})
        session.add(JobRun(name="mail_sync", ok=True, summary="Gmail owner@example.test: 3 new"))
        session.commit()
    assert "Example Checking" in json.dumps(call("jobs_status", {}, "full"))
    for level in ("merchants", "aggregates"):
        text = json.dumps(call("jobs_status", {}, level))
        assert "Example Checking" not in text and "owner@example.test" not in text and "example-host" not in text, level
        assert "mail_sync" in json.dumps(call("jobs_status", {}, "merchants"))  # the job names and whether they ran still show


def test_facts_pending_at_aggregates_names_nobody():
    with Session(engine) as session:
        session.add(Debt(name="Example Card Co", kind="card", balance=400.0, minimum=25.0))
        session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Card Co", "amount": 25.0, "due_date": "2026-10-20"}), confidence=0.9, status="pending", excerpt="due $25.00"))
        session.commit()
    assert "Example Card Co" in json.dumps(call("facts_pending", {}, "full"))
    assert "Example Card Co" not in json.dumps(call("facts_pending", {}, "aggregates"))


# ---- the audit log -----------------------------------------------------------------------------


def test_a_refused_or_failed_tool_call_is_audited_too():
    with Session(engine) as session:
        seed(session)
        session.add(ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Water", "amount": 64.0, "due_date": "2026-11-02"}), confidence=0.9, status="pending", excerpt="due $64.00"))
        session.commit()
        fact_id = session.exec(select(ExtractedFact.id)).first()
    assert "confirm scope" in call("facts_confirm", {"fact_id": fact_id}, "full")["error"]  # no token: refused
    assert "error" in call("mail_read", {"message_id": 1}, "merchants")  # refused by the redaction level
    assert "error" in call("transactions_search", {"text": "x"}, "aggregates")
    with pytest.raises(Exception):
        call("transactions_search", {"start_date": "not-a-date"}, "full")  # fails inside the tool
    with Session(engine) as session:
        rows = session.exec(select(AuditEntry).where(AuditEntry.client == "sweep")).all()
    tools = [r.tool for r in rows]
    for name in ("facts_confirm", "mail_read", "transactions_search"):
        assert any(t.startswith(name) for t in tools), (name, tools)
    assert sum(1 for t in tools if t.startswith("transactions_search")) == 2
    assert all(r.rows == 0 for r in rows)


# ---- the reviewer's frame ----------------------------------------------------------------------


def test_the_reviewer_is_told_that_alert_lines_may_quote_a_sender():
    with Session(engine) as session:
        seed(session)
        hostile_mail(session)
        pack = reviewer.packet(session, TODAY)
    prompt = reviewer.prompt_for(pack, "request")
    assert any("Hostile Sender Co" in a for a in pack["alerts"])  # the brief's critical line names the sender
    assert "written by" in prompt and "never instructions" in prompt


# ---- pushes --------------------------------------------------------------------------------------


def test_a_push_title_with_accents_or_a_line_break_is_delivered_not_raised(owner: TestClient):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "money"})
    with Session(engine) as session:
        result = notify.send(session, "A send waits for José", "body", client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert result["ok"] and seen and "Jos" in seen[-1].headers["Title"]
        result = notify.send(session, "Pay Café Example\nby Friday", "body", client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert result["ok"] and "\n" not in seen[-1].headers["Title"]


# ---- providers -----------------------------------------------------------------------------------


def test_a_provider_stream_that_never_ends_is_cut_off():
    chunk = ("data: " + json.dumps({"choices": [{"delta": {"content": "x" * 1000}}]}) + "\n\n").encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            for _ in range(10_000):  # about ten megabytes, far past any answer
                yield chunk

        return httpx.Response(200, content=body())

    provider = OpenAICompatible("m", "https://llm.example.test/v1", "k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def run() -> list:
        out = []
        async for event in provider.chat([{"role": "user", "content": "hi"}]):
            out.append(event)
        return out

    events = asyncio.run(run())
    text = sum(len(e.text) for e in events if e.type == "text")
    assert text <= 3_000_000
    assert any(e.type == "error" for e in events)


# ---- the reviewer's actions are bounded ------------------------------------------------------------


def test_a_reviewer_cannot_propose_a_buffer_outside_sense_or_a_rule_the_size_of_a_book():
    allowed = {300.0}
    notes = reviewer.parse_notes(json.dumps({"notes": [
        {"kind": "tighten", "text": "Keep $300 back.", "action": {"type": "buffer", "value": "nan"}},
        {"kind": "tighten", "text": "Keep $300 back.", "action": {"type": "buffer", "value": -5}},
        {"kind": "tighten", "text": "Keep $300 back.", "action": {"type": "buffer", "value": 1e12}},
        {"kind": "tighten", "text": "Keep $300 back.", "action": {"type": "buffer", "value": 300}},
        {"kind": "ask", "text": "A rule.", "action": {"type": "rule", "text": "x" * 20_000}},
    ]}), allowed)
    assert [n["action"]["type"] for n in notes[:3]] == ["none", "none", "none"]
    assert notes[3]["action"] == {"type": "buffer", "value": 300.0}
    assert len(notes[4]["action"]["text"]) <= 500


# ---- mail words are framed wherever a model reads them ----------------------------------------------


def test_facts_and_message_references_say_their_words_are_the_senders():
    with Session(engine) as session:
        seed(session)
        hostile_mail(session)
    facts = call("facts_pending", {}, "full")
    assert facts["facts"] and "written by" in facts["how"] and "never instructions" in facts["how"]
    found = call("mail_search", {"text": "Past due"}, "full")
    assert found["returned"] == 1 and "written by" in found["how"] and "never instructions" in found["how"]


def test_the_advisor_gets_the_tools_answers_as_structures_not_text():
    from app.llm.advisor import advise
    from app.llm.providers import Event, Scripted

    with Session(engine) as session:
        seed(session)
    provider = Scripted([
        [Event("tool_call", id="c1", name="finance_snapshot", arguments={}), Event("done")],
        [Event("text", text="Done."), Event("done")],
    ])

    async def collect() -> list[dict]:
        from app.db import session_scope

        return [e async for e in advise([{"role": "user", "content": "plan?"}], provider=provider, session_factory=session_scope, today=TODAY)]

    events = asyncio.run(collect())
    content = provider.seen[1][-1]["content"]
    assert content.startswith("{") and '"projection"' not in content and '"safe_to_spend"' in content  # compacted, not the raw JSON text
    assert events[-1]["unverified"] == []


# ---- the IMAP client refuses a folder name that could end its command ------------------------------------


def test_a_folder_name_that_could_end_the_imap_command_never_reaches_the_wire():
    from app.mail.imap import ImapClient

    class Conn:
        calls: list = []

        def select(self, name, readonly=True):  # noqa: ANN001
            self.calls.append(name)
            return "OK", [b"1"]

        def response(self, key):  # noqa: ANN001
            return "OK", [b"2"]

    client = ImapClient("bridge", 1143, "owner@example.test", "x")
    client.conn = Conn()  # type: ignore[assignment]
    assert client.select_readonly("INBOX") == (1, 2)
    for bad in ('INBOX"\r\nUID STORE 1 +FLAGS (\\Deleted)', "INBOX\\", 'a"b', "", "INBOX\x00"):
        with pytest.raises(ValueError):
            client.select_readonly(bad)
    assert Conn.calls == ['"INBOX"']
