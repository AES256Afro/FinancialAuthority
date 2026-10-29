"""Security sweep 13, part two: the operator's view. BoxPilot runs `python -m app.restore` in the container and
reads its JSON; Docker reads `/api/health`; the container's environment holds the FA_* secrets. None of them may
carry a secret, a row, a name or a figure, and no route, error page or export may show an environment value."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import restore
from app.config import settings
from app.db import engine
from app.engine.household import seed
from app.main import app
from tests.test_sweep13_security_rest import MONEY_AND_NAMES, PARTNER_PASSWORD, PW, H, _found, seed_everything

ENV_SECRETS = {"FA_ANTHROPIC_API_KEY": "sk-ant-env-only-ZQ-2026", "FA_OPENAI_API_KEY": "sk-openai-env-only-ZQ-2026", "FA_PASSWORD": PW,
               "FA_SESSION_SECRET": "test-session-secret", "FA_DATA_KEY": "test-data-key"}


def _env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "anthropic_api_key", ENV_SECRETS["FA_ANTHROPIC_API_KEY"])
    monkeypatch.setattr(settings, "openai_api_key", ENV_SECRETS["FA_OPENAI_API_KEY"])


def test_the_backup_managers_command_prints_names_sizes_and_verdicts_only(owner: TestClient, tmp_path: Path, capsys, monkeypatch):
    _env(monkeypatch)
    secrets = seed_everything(owner, tmp_path)
    outputs: dict[str, object] = {}
    for argv in (["make", "nightly", "by", "the", "manager"], ["status"], ["list"], ["check", "the backup manager"]):
        assert restore.main(argv) == 0
        text = capsys.readouterr().out
        assert _found(text.encode(), {**secrets, **ENV_SECRETS, **MONEY_AND_NAMES}) == [], argv[0]
        outputs[argv[0]] = json.loads(text)
    point_keys = {"name", "kind", "reason", "created_at", "bytes", "verified", "transactions"}
    assert all(set(p) <= point_keys for p in outputs["list"]) and outputs["list"]
    assert set(outputs["make"]) <= point_keys | {"tables", "documents"}
    status = outputs["status"]
    assert set(status) == {"folder", "count", "bytes", "newest", "latest_age_hours", "database", "keep", "last_check", "copy_due", "state", "state_words"}
    assert set(status["database"]) == {"bytes", "ok", "result", "tables", "transactions"}
    assert set(outputs["check"]) == {"database", "points"} and all(v == "ok" for v in outputs["check"]["points"].values())
    # The unknown command and a failure print a sentence, never a traceback with values in it.
    assert restore.main(["nope"]) == 2 and "unknown command" in capsys.readouterr().out


def test_the_healthcheck_says_up_and_nothing_else(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "demo": False}


def _fill(path: str) -> str:
    out = ""
    for piece in path.split("/"):
        if piece.startswith("{") and piece.endswith("}"):
            out += "/" + ("csv" if "format" in piece else "1")
        elif piece:
            out += "/" + piece
    return out or "/"


def test_no_route_error_page_or_export_shows_an_environment_secret(owner: TestClient, tmp_path: Path, monkeypatch):
    _env(monkeypatch)
    secrets = seed_everything(owner, tmp_path)
    watched = {**ENV_SECRETS, **{k: v for k, v in secrets.items() if k not in ("session id (cookie)", "API key", "document text")}}  # the owner may read their own lease
    paths = sorted(path for path, methods in app.openapi()["paths"].items() if "get" in methods and path.startswith("/api"))
    assert len(paths) > 100, len(paths)
    for path in paths:
        if "calendar/{token}" in path:
            continue
        r = owner.get(_fill(path))
        hits = _found(r.content, watched)
        assert hits == [], (path, hits)
    # The settings and privacy pages say only whether a secret is set.
    body = owner.get("/api/v1/settings").text + owner.get("/api/v1/privacy").text + owner.get("/api/v1/connectors").text + owner.get("/api/v1/models").text
    assert _found(body.encode(), watched) == []
    # Error pages: not found, wrong method, a bad body, and a route that raises.
    loud = TestClient(app, raise_server_exceptions=False)
    loud.cookies = owner.cookies
    loud.headers.update(H)
    assert _found(loud.get("/api/v1/nothing-here").content, watched) == []
    assert _found(loud.delete("/api/v1/about").content, watched) == []
    assert _found(loud.post("/api/v1/accounts", json={"name": 5}).content, watched) == []

    def boom():
        raise RuntimeError(f"state: {settings.data_key} {settings.anthropic_api_key}")

    monkeypatch.setattr(restore, "status", boom)
    crashed = loud.get("/api/v1/restore-points")
    assert crashed.status_code == 500 and _found(crashed.content, watched) == [] and b"Traceback" not in crashed.content


def test_about_tells_each_principal_the_same_small_things(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    invited = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"}).json()["invite"]["link"].split("#", 1)[1]
    sam = TestClient(app)
    sam.headers.update(H)
    assert sam.post("/api/auth/join", json={"token": invited, "password": PARTNER_PASSWORD}).status_code == 200
    shared_invite = owner.post("/api/v1/people", json={"username": "kit", "display_name": "Kit Example", "role": "shared"}).json()["invite"]["link"].split("#", 1)[1]
    kit = TestClient(app)
    kit.headers.update(H)
    assert kit.post("/api/auth/join", json={"token": shared_invite, "password": "maple harbour winter nine"}).status_code == 200
    reader = TestClient(app)
    answers = {
        "owner": owner.get("/api/v1/about"), "everything": sam.get("/api/v1/about"), "shared": kit.get("/api/v1/about"),
        "key": reader.get("/api/v1/about", headers={"Authorization": f"Bearer {key}"}), "stranger": TestClient(app).get("/api/v1/about"),
    }
    assert answers.pop("stranger").status_code == 401
    for who, r in answers.items():
        assert r.status_code == 200, (who, r.text)
        assert set(r.json()) == {"version", "demo", "transactions", "today", "tz", "public_url", "started"}, who
        assert _found(r.content, {"the password": PW, "the session secret": settings.session_secret, "the data key": settings.data_key}) == []
    # A person who sees only the shared accounts is not told how many rows the whole household has.
    assert answers["shared"].json()["transactions"] == 0
    assert answers["owner"].json()["transactions"] > 0 and answers["everything"].json()["transactions"] == answers["owner"].json()["transactions"]
