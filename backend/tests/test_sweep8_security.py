"""Security sweep 8: the second code paths of what rounds one to three closed (a fact confirmed at once through the
MCP tool, or through Needs you's bulk answer beside the facts route), a sender's display name dressed in look-alike
letters, and an installed copy that would start with sign-in off."""

from __future__ import annotations

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth
from app import mcp_server as mcp
from app.api import mail as mail_api
from app.config import settings
from app.db import engine
from app.mail.trust import brand_of, judge
from app.main import app
from app.models import Bill, ExtractedFact

H = {"X-Requested-With": "FinancialAuthority"}


def _owner_client() -> TestClient:
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).status_code == 200
    c.headers.update(H)
    return c


def _run_together(workers: list) -> list:
    """Run each callable in its own thread, released together; return what each returned or raised."""
    barrier = threading.Barrier(len(workers))
    out: list = [None] * len(workers)

    def go(i: int, fn) -> None:  # noqa: ANN001
        barrier.wait()
        try:
            out[i] = fn()
        except Exception as error:  # noqa: BLE001 - the test reads it
            out[i] = error

    threads = [threading.Thread(target=go, args=(i, fn)) for i, fn in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return out


def _pending_fact(name: str) -> int:
    with Session(engine) as session:
        fact = ExtractedFact(kind="bill_due", fields=json.dumps({"name": name, "amount": 41.0, "due_date": "2026-10-28"}), status="pending")
        session.add(fact)
        session.commit()
        return fact.id


def _slow_apply(monkeypatch, delay: float = 0.15) -> None:
    """apply_fact as it is, after a pause: both callers have read the fact as pending before either writes."""
    real = mail_api.apply_fact

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(delay)
        return real(*args, **kwargs)

    monkeypatch.setattr(mail_api, "apply_fact", slow)


def _call(name: str, args: dict) -> dict:
    token = mcp.CALLER.set(("sweep8", "full"))
    try:
        result = asyncio.run(mcp.mcp_server.call_tool(name, args))
    finally:
        mcp.CALLER.reset(token)
    return result.structured_content or json.loads("".join(getattr(c, "text", "") for c in result.content))


# ---- the second paths of a double confirm ----------------------------------------------------------


def test_a_fact_confirmed_twice_at_once_through_the_tool_makes_one_bill(owner: TestClient, monkeypatch):
    """The facts route claims a fact under the writer lock; the MCP tool must claim it the same way, or two confirm-
    scoped calls in the same instant (or one beside the route) make two bills."""
    from mcp.server.auth.provider import AccessToken

    monkeypatch.setattr(mcp, "get_access_token", lambda: AccessToken(token="t", client_id="sweep8", scopes=["read", "confirm"], expires_at=None, claims={"name": "sweep8", "redaction": "full", "kind": "key"}))
    _slow_apply(monkeypatch)
    fact_id = _pending_fact("Example Water")
    results = _run_together([lambda: _call("facts_confirm", {"fact_id": fact_id}), lambda: _call("facts_confirm", {"fact_id": fact_id})])
    assert sum(1 for r in results if isinstance(r, dict) and r.get("ok")) == 1, results
    assert sum(1 for r in results if isinstance(r, dict) and r.get("error")) == 1, results
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.name == "Example Water")).all()) == 1
        assert session.get(ExtractedFact, fact_id).status == "applied"


def test_a_fact_confirmed_on_needs_you_and_on_its_card_at_once_makes_one_bill(owner: TestClient, monkeypatch):
    """Needs you's bulk answer and the fact's own card both apply the same fact; released together they must agree on
    one bill, with the loser told it was answered already."""
    _slow_apply(monkeypatch)
    fact_id = _pending_fact("Example Gas")
    other = _owner_client()
    results = _run_together([
        lambda: owner.post("/api/v1/needs-you/facts", json={"ids": [fact_id], "action": "confirm"}),
        lambda: other.post(f"/api/v1/facts/{fact_id}/confirm", json={"apply": True}),
    ])
    bulk, card = results
    assert bulk.status_code == 200, bulk.text
    with Session(engine) as session:
        assert len(session.exec(select(Bill).where(Bill.name == "Example Gas")).all()) == 1, [r.text for r in results]
        assert session.get(ExtractedFact, fact_id).status == "applied"
    done = bulk.json()["done"]
    assert (card.status_code == 200) != bool(done), (card.text, bulk.text)  # exactly one of the two applied it
    if not done:
        assert bulk.json()["failed"][0]["error"] == "already answered"


# ---- a sender's name in look-alike letters ----------------------------------------------------------


def test_a_brand_name_in_lookalike_letters_is_still_the_brand():
    """A forger writes "Capital One" with a Cyrillic а, a zero-width space inside the word, or full-width letters, so
    the name reads the same to the owner and nothing to the brand check. The name is folded first."""
    assert brand_of("Capitаl One Alerts") == "capital one"  # Cyrillic а
    assert brand_of("Capi​tal One") == "capital one"  # zero-width space
    assert brand_of("Ｃhase") == "chase"  # full-width C
    assert brand_of("PаyPаl") == "paypal"
    assert brand_of("Square Enix") is None
    assert judge("billing@example-evil.test", "Capitаl One", auth_results="dmarc=pass")[0] == "crit"
    assert judge("alerts@capitalone.com", "Capitаl One", auth_results="dmarc=pass")[0] == "ok"


# ---- an installed copy with sign-in off ----------------------------------------------------------


def test_an_installed_copy_refuses_to_start_with_sign_in_off(monkeypatch):
    """FA_PASSWORD empty turns sign-in off, which is for a developer's machine only. An installed copy (FA_INSTALL
    names how it was installed) refuses to start that way, unless it is the demo, which holds nothing real."""
    monkeypatch.setattr(settings, "password", "")
    monkeypatch.setattr(settings, "install", "compose")
    monkeypatch.setattr(settings, "demo", False)
    problem = auth.open_install_problem()
    assert problem and "FA_PASSWORD" in problem
    monkeypatch.setattr(settings, "demo", True)
    assert auth.open_install_problem() is None
    monkeypatch.setattr(settings, "demo", False)
    monkeypatch.setattr(settings, "install", "")
    assert auth.open_install_problem() is None  # a developer's machine
    monkeypatch.setattr(settings, "install", "boxpilot")
    monkeypatch.setattr(settings, "password", "correct horse battery")
    assert auth.open_install_problem() is None


def test_the_app_does_not_start_as_an_open_install(monkeypatch):
    import pytest

    monkeypatch.setattr(settings, "password", "")
    monkeypatch.setattr(settings, "install", "docker")
    monkeypatch.setattr(settings, "demo", False)
    with pytest.raises(RuntimeError, match="FA_PASSWORD"), TestClient(app):
        pass
