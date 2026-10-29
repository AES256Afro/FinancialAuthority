"""Security sweep 14: schema-driven fuzzing of every write.

Every operation the app publishes with a body, a query or a path parameter is read from its own OpenAPI document; a
valid body is built from the schema, then each field is replaced, one at a time, by what a hostile or careless
caller sends (missing, null, the wrong type, empty, 100,000 characters, bidi overrides, CR/LF and NUL, 0, -1, -0.0,
1e308, NaN, Infinity, integers past 2^63, dates at the ends of the calendar and free text where a date belongs,
nesting a thousand deep, arrays of a hundred thousand, unknown keys) and each path id by one that does not exist, is
negative, is text or belongs to the other person. The same generator runs against every MCP tool's input schema.

Held for every request: never a 500 or a traceback; a refusal leaves no row behind; a write never stores a
non-finite or extreme number, an over-long string or a control character; and after each write that succeeded the
snapshot, Today, the bills and Needs you still answer with finite numbers.

The run is sampled with a fixed seed to stay under a minute; FZ_FULL=1 sends every case (several minutes)."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import socket
import time
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app import auth
from app import mcp_server as mcp
from app.db import engine
from app.api.inputs import TEXT
from app.engine.household import seed
from app.main import app

PW = "correct horse battery"
PARTNER_PW = "maple harbour winter nine"
H = {"X-Requested-With": "FinancialAuthority"}
FULL = os.environ.get("FZ_FULL") == "1"
SEED = 14
LONG = 100_000
MARK = "ZQ"  # every fuzzed string carries it, so a stored one is found and traced back to its request

# The finite-number reads: after each write that succeeded these must still answer 200 with finite numbers.
READS = ("/api/v1/snapshot", "/api/v1/today", "/api/v1/bills", "/api/v1/needs-you")
# Tables a refusal may still write: the activity log, job records, the version counter.
QUIET = {"audit_entries", "job_runs", "health_samples"}
# Routes that end the caller's session or reach outside are sent too, but with their schema's body only.
NO_RECORDED_BASELINE = re.compile(r"^/api/auth/|/privacy/erase|/api/v1/people|/restore-points/.+/restore")
SKIP_BODY = ("multipart/form-data", "application/x-www-form-urlencoded")  # the upload routes: sweep M14 and round six


# ---- the generator, per JSON-schema type ------------------------------------------------------------


def _spec() -> dict:
    return app.openapi()


def _resolve(schema: dict, spec: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[1]
        schema = spec["components"]["schemas"][name]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"]
            if options:
                return _resolve(options[0], spec)
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve(schema["allOf"][0], spec)
    return schema


def _is_date(name: str, schema: dict) -> bool:
    return schema.get("format") in ("date", "date-time") or bool(re.search(r"(^|_)(date|on|start|end|until|since|when|day_of)$|_date|date_", name))


def _valid(schema: dict, spec: dict, name: str = "", depth: int = 0) -> Any:
    schema = _resolve(schema, spec)
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: _valid(v, spec, k, depth + 1) for k, v in props.items()} if depth < 4 else {}
    if kind == "array":
        return [_valid(schema.get("items", {}), spec, name, depth + 1)] if depth < 4 else []
    if kind == "integer":
        return max(int(schema.get("minimum", 1)), 1)
    if kind == "number":
        return 10.0
    if kind == "boolean":
        return False
    if _is_date(name, schema):
        return "2026-11-01T09:00:00" if schema.get("format") == "date-time" else "2026-11-01"
    if "email" in name or name in ("to", "recipient", "username"):
        return "office@example-flats.test" if name != "username" else "fuzz.user"
    if name == "cadence":
        return "monthly"
    if name in ("url", "server", "host", "link"):
        return "https://example.test/x"
    return "Example"


def _strings(n: int, date_like: bool) -> list[tuple[str, Any]]:
    out = [("empty", ""), ("long", f"{MARK}{n}" + "a" * LONG), ("bidi", f"{MARK}{n}‮evil​⁦x⁩"),
           ("crlf_nul", f"{MARK}{n}\r\nX-Injected: 1\x00tail"), ("wrongtype_num", 12345), ("wrongtype_obj", {"a": 1})]
    if date_like:
        out += [("date_0000", "0000-01-01"), ("date_9999", "9999-12-31"), ("date_feb30", "2026-02-30"), ("date_text", "next tuesday"), ("date_0001", "0001-01-01")]
    return out


NUMBERS = [("zero", 0), ("minus_one", -1), ("neg_zero", -0.0), ("huge", 1e308), ("nan", math.nan), ("inf", math.inf), ("neg_inf", -math.inf),
           ("int_2_63", 2**63), ("int_neg_2_63", -(2**63) - 1), ("int_10_30", 10**30), ("wrongtype_str", "ten")]


def _deep(n: int = 1000) -> Any:
    value: Any = {"leaf": 1}
    for _ in range(n):
        value = {"a": value}
    return value


def _mutations(schema: dict, spec: dict, name: str, n: int) -> list[tuple[str, Any]]:
    """What to send in place of one field, by the field's declared type."""
    schema = _resolve(schema, spec)
    kind = schema.get("type")
    common: list[tuple[str, Any]] = [("null", None)]
    if kind in ("integer", "number"):
        return common + NUMBERS
    if kind == "boolean":
        return common + [("wrongtype_str", "yes"), ("wrongtype_num", 2)]
    if kind == "array":
        item = _valid(schema.get("items", {}), spec, name)
        return common + [("wrongtype_str", "x"), ("empty_list", []), ("array_100k", [item] * LONG), ("deep", [_deep()])] + \
            [(f"item_{label}", [value]) for label, value in _mutations(schema.get("items", {}), spec, name, n)[1:5]]
    if kind == "object" or "properties" in schema:
        return common + [("wrongtype_list", [1]), ("deep", _deep()), ("extra_key", {**(_valid(schema, spec, name) or {}), "zz_unknown": 1})]
    return common + _strings(n, _is_date(name, schema))


def body_cases(op: dict, spec: dict, baseline: Any | None) -> Iterator[tuple[str, Any]]:
    """The body as the schema builds it, then each field missing or replaced; and the whole body malformed."""
    content = op.get("requestBody", {}).get("content", {})
    if not content or any(c in content for c in SKIP_BODY):
        return
    schema = _resolve(content["application/json"]["schema"], spec)
    valid = baseline if baseline is not None else _valid(schema, spec)
    yield "valid", valid
    for label, whole in (("body_missing", ...), ("body_null", None), ("body_list", []), ("body_text", "x"), ("body_deep", _deep()), ("body_extra_key", {**valid, "zz_unknown": 1} if isinstance(valid, dict) else None)):
        yield label, whole
    if not isinstance(valid, dict):
        return
    n = 0
    for field, fschema in schema.get("properties", {}).items():
        yield f"{field}:missing", {k: v for k, v in valid.items() if k != field}
        for label, value in _mutations(fschema, spec, field, n):
            n += 1
            yield f"{field}:{label}", {**valid, field: value}
        # one level down: the first item of a list of objects, or a nested object
        inner = _resolve(fschema, spec)
        sub = _resolve(inner.get("items", {}), spec) if inner.get("type") == "array" else inner
        if sub.get("properties") and isinstance(valid.get(field), (list, dict)):
            base_item = valid[field][0] if isinstance(valid[field], list) and valid[field] else valid[field]
            if isinstance(base_item, dict):
                for sfield, sschema in sub["properties"].items():
                    for label, value in _mutations(sschema, spec, sfield, n)[:8]:
                        n += 1
                        item = {**base_item, sfield: value}
                        yield f"{field}.{sfield}:{label}", {**valid, field: [item] if isinstance(valid[field], list) else item}


def query_cases(op: dict, spec: dict) -> Iterator[tuple[str, dict]]:
    params = [p for p in op.get("parameters", []) if p["in"] == "query"]
    if not params:
        return
    for p in params:
        schema = _resolve(p.get("schema", {}), spec)
        kind = schema.get("type")
        values = ["0", "-1", "-0.0", "1e308", "nan", "inf", "-inf", str(2**63), str(10**30), "ten"] if kind in ("integer", "number") else \
            ["", "a" * 5000, "‮evil", "x\r\ny", "0000-01-01", "9999-12-31", "2026-02-30", "next tuesday", "-1", "nan"]
        for v in values:
            yield f"{p['name']}={v[:20]!r}", {p["name"]: v}


PATH_IDS = ["999999", "-1", "0", "abc", str(2**63), "1e3", "%00", "..%2f..%2fetc"]


def path_cases(path: str) -> Iterator[tuple[str, str]]:
    names = re.findall(r"{(\w+)}", path)
    base = {n: "1" for n in names}
    if not names:
        yield "path", path
        return
    yield "path:base", _fill(path, base)
    for n in names:
        for v in PATH_IDS:
            yield f"path:{n}={v}", _fill(path, {**base, n: v})


def _fill(path: str, values: dict) -> str:
    for k, v in values.items():
        path = path.replace("{" + k + "}", v)
    return path


# ---- the checks --------------------------------------------------------------------------------------


def _tables() -> dict[str, list[tuple[str, str]]]:
    with engine.connect() as c:
        names = [r[0] for r in c.execute(text("select name from sqlite_master where type='table' and name not like 'sqlite_%'"))]
        return {t: [(r[1], (r[2] or "").upper()) for r in c.execute(text(f'pragma table_info("{t}")'))] for t in names}


def _counts(tables: dict) -> dict[str, int]:
    with engine.connect() as c:
        return {t: c.execute(text(f'select count(*) from "{t}"')).scalar() for t in tables if t not in QUIET}


BAD_CHARS = re.compile("[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩​﻿]|[\r\n]")


def stored_poison(tables: dict, seen: set) -> list[str]:
    """Rows that hold what a write must not keep: a non-finite or extreme number, or a fuzzed string that is over-long
    or carries a control character. Each row is reported once."""
    found = []
    with engine.connect() as c:
        for t, cols in tables.items():
            if t in QUIET:
                continue
            for col, kind in cols:
                if kind in ("FLOAT", "REAL", "NUMERIC") or kind.startswith("DECIMAL"):
                    rows = c.execute(text(f'select rowid from "{t}" where "{col}" > 1e300 or "{col}" < -1e300')).all()
                    for (rid,) in rows:
                        if (t, col, rid) not in seen:
                            seen.add((t, col, rid))
                            found.append(f"{t}.{col}: extreme or non-finite number")
                elif kind in ("VARCHAR", "TEXT", "") or kind.startswith("VARCHAR"):
                    rows = c.execute(text(f'select rowid, "{col}" from "{t}" where instr("{col}", :m) > 0'), {"m": MARK}).all()
                    for rid, value in rows:
                        if (t, col, rid) in seen or not isinstance(value, str):
                            continue
                        why = []
                        if len(value) >= LONG and col not in TEXT:  # a draft's body keeps its own 1 MB ceiling
                            why.append(f"{len(value)} characters")
                        bad = BAD_CHARS.search(value) if not col.endswith(("_json", "fields", "body", "rules", "notes", "text", "data", "blob", "payload", "args")) else None
                        if bad or "\x00" in value:
                            why.append(f"control character {bad.group() if bad else chr(0)!r}")
                        if why:
                            seen.add((t, col, rid))
                            found.append(f"{t}.{col}: {', '.join(why)}")
    return found


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def reads_broken(client: TestClient) -> list[str]:
    broken = []
    for path in READS:
        try:
            r = client.get(path)
        except Exception as error:  # noqa: BLE001 - the read raised: a 500 in the running app
            broken.append(f"{path} raised {type(error).__name__}: {str(error)[:120]}")
            continue
        if r.status_code != 200:
            broken.append(f"{path} answered {r.status_code}")
            continue
        try:
            if not _finite(json.loads(r.text)):
                broken.append(f"{path} carried a non-finite number")
        except ValueError:
            broken.append(f"{path} was not JSON")
    return broken


# ---- the household and the two principals -----------------------------------------------------------


def _no_network(monkeypatch) -> None:
    """Nothing leaves the process: a fuzzed host, a model provider or a push server fails at once."""
    real = socket.getaddrinfo

    def local_only(host, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        if host in ("localhost", "127.0.0.1", "::1", "testserver"):
            return real(host, *a, **k)
        raise socket.gaierror("no network in the fuzz run")

    monkeypatch.setattr(socket, "getaddrinfo", local_only)
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (_ for _ in ()).throw(OSError("no network in the fuzz run")))


class World:
    def __init__(self) -> None:
        self.owner = self.person = None  # type: ignore[assignment]
        self.setup()

    def setup(self) -> None:
        from sqlmodel import SQLModel

        from app.db import init_db

        SQLModel.metadata.drop_all(engine)
        init_db()
        auth.reset_lockouts()
        with Session(engine) as session:
            seed(session)
        self.owner = self._sign_in("owner", PW)
        made = self.owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"}).json()
        token = made["invite"]["link"].split("#", 1)[1]
        self.person = TestClient(app)
        self.person.headers.update(H)
        assert self.person.post("/api/auth/join", json={"token": token, "password": PARTNER_PW}).status_code == 200
        # rows that are the owner's own, for the person to reach for by id
        self.owner_key = self.owner.post("/api/v1/keys", json={"name": "owner key", "scopes": ["read"], "redaction": "full"}).json().get("id")
        self.owner_draft = self.owner.post("/api/v1/approvals", json={"to": "office@example-flats.test", "subject": "Notice", "body": "Moving out."}).json().get("id")

    @staticmethod
    def _sign_in(username: str, password: str) -> TestClient:
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
        c.headers.update(H)
        return c

    def heal(self) -> None:
        """A fuzzed request may end a session, change a password or lock the address out; put the world back."""
        auth.reset_lockouts()
        if self.owner.get("/api/v1/about").status_code == 200 and self.person.get("/api/v1/about").status_code == 200:
            return
        try:
            self.owner = self._sign_in("owner", PW)
            self.person = self._sign_in("sam", PARTNER_PW)
        except AssertionError:
            self.setup()


def _send(client: TestClient, method: str, url: str, body: Any = ..., params: dict | None = None):
    kw: dict = {"params": params} if params else {}
    if body is not ...:
        kw["content"] = json.dumps(body, allow_nan=True)
        kw["headers"] = {"content-type": "application/json"}
    return client.request(method, url, **kw)


def _cases(spec: dict, baselines: dict) -> list[tuple[str, str, str, Any, dict | None]]:
    cases = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            method = method.upper()
            has_body = bool(op.get("requestBody")) and not any(c in op["requestBody"].get("content", {}) for c in SKIP_BODY)
            has_query = any(p["in"] == "query" for p in op.get("parameters", []))
            has_path = "{" in path
            if not (has_body or has_query or has_path) or (method == "GET" and not (has_query or has_path)):
                continue
            base = None if NO_RECORDED_BASELINE.search(path) else baselines.get((method, path))
            valid = next((v for label, v in body_cases(op, spec, base) if label == "valid"), ...) if has_body else ...
            first = _fill(path, {n: "1" for n in re.findall(r"{(\w+)}", path)})
            for label, body in body_cases(op, spec, base):
                cases.append((method, path, f"body {label}", (first, body), None))
            for label, params in query_cases(op, spec):
                cases.append((method, path, f"query {label}", (first, valid), params))
            for label, url in path_cases(path):
                cases.append((method, path, label, (url, valid), None))
    return cases


def _baselines() -> dict:
    """Bodies the rest of the suite sent and the app accepted (FZ_BASELINES, a JSON-lines file of method, url and
    body), so the fuzz starts from a write that lands; without it, from the schema."""
    out: dict = {}
    source = os.environ.get("FZ_BASELINES")
    if not source:
        return out
    paths = [(re.compile("^" + re.sub(r"{\w+}", r"[^/]+", p) + "$"), p) for p in _spec()["paths"]]
    for line in open(source):
        row = json.loads(line)
        for rx, p in paths:
            if rx.match(row["u"]) and isinstance(row["j"], dict):
                key = (row["m"], p)
                if len(json.dumps(row["j"])) < 20_000 and len(row["j"]) >= len(out.get(key, {})):
                    out[key] = row["j"]
                break
    return out


def run_rest(world: World, rng: random.Random, sample: float) -> tuple[int, list[tuple[str, str, str, str]]]:
    spec = _spec()
    tables = _tables()
    seen: set = set()
    failures: list[tuple[str, str, str, str]] = []
    cases = _cases(spec, _baselines())
    sent = 0
    for method, path, label, (url, body), params in cases:
        if not FULL and label not in ("body valid", "path:base") and rng.random() > sample:
            continue
        for who in ("owner", "person"):
            if who == "person" and not FULL and rng.random() > 0.3:
                continue
            client = getattr(world, who)
            if who == "person" and path in ("/api/v1/keys/{key_id}", "/api/v1/approvals/{approval_id}", "/api/v1/approvals/{approval_id}/approve", "/api/v1/approvals/{approval_id}/deny") and label == "path:base":
                url = _fill(path, {"key_id": str(world.owner_key), "approval_id": str(world.owner_draft)})
            before = _counts(tables)
            sent += 1
            try:
                r = _send(client, method, url, body, params)
                status, words = r.status_code, r.text
            except Exception as error:  # noqa: BLE001 - what the TestClient raises is a 500 in the running app
                r, status, words = None, 500, f"raised {type(error).__name__}: {error}"
            if status == 500 or (status > 500 and not _sentence(r)) or "Traceback" in words:
                failures.append(("500", method, path, f"{who} {label}: {status} {words[:160]}"))
            elif status in (400, 422) and not _sentence(r):
                failures.append(("no_sentence", method, path, f"{who} {label}: {words[:160]}"))
            if method == "GET":
                if status == 401:
                    world.heal()
                continue
            if status >= 400:
                after = _counts(tables)
                grew = {t: after[t] - before.get(t, 0) for t in after if after[t] != before.get(t, 0)}
                if grew:
                    failures.append(("half_written", method, path, f"{who} {label}: refused {status} but rows changed {grew}"))
            for what in stored_poison(tables, seen):
                failures.append(("stored" if status < 300 else "stored_on_refusal", method, path, f"{who} {label}: {what}"))
            world.heal()  # the write may have ended a session (sessions/end, a password); that is not a poisoned read
            broken = reads_broken(world.owner)
            if broken:
                failures.append(("poisoned", method, path, f"{who} {label} ({status}): {'; '.join(broken)}"))
                world.setup()  # start clean, so one poisoned row is not counted against every later route
                tables = _tables()
    return sent, failures


def _sentence(r) -> bool:  # noqa: ANN001
    try:
        detail = r.json().get("detail")
    except (ValueError, AttributeError):
        return False
    if isinstance(detail, str):
        return bool(detail.strip())
    if isinstance(detail, list):
        return all(isinstance(d, dict) and d.get("msg") for d in detail)
    return isinstance(detail, dict)


# ---- the MCP tools -----------------------------------------------------------------------------------


def run_tools(world: World, rng: random.Random, sample: float, monkeypatch) -> tuple[int, int, list[tuple[str, str, str, str]]]:
    from mcp.server.auth.provider import AccessToken

    monkeypatch.setattr(mcp, "get_access_token", lambda: AccessToken(token="t", client_id="fuzz", scopes=["read", "confirm"], expires_at=None, claims={"name": "fuzz", "redaction": "full", "kind": "key"}))
    tools = asyncio.run(mcp.mcp_server.list_tools())
    tables = _tables()
    seen: set = set()
    failures: list[tuple[str, str, str, str]] = []
    sent = 0
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
        spec = {"components": {"schemas": schema.get("$defs", {})}}
        schema = {**schema, **{"$defs": {}}}
        fixed = json.loads(json.dumps(schema).replace("#/$defs/", "#/components/schemas/"))
        spec = json.loads(json.dumps(spec).replace("#/$defs/", "#/components/schemas/"))
        op = {"requestBody": {"content": {"application/json": {"schema": fixed}}}}
        for label, args in body_cases(op, spec, None):
            if label.startswith("body_") or (not FULL and label != "valid" and rng.random() > sample):
                continue
            if not isinstance(args, dict):
                continue
            before = _counts(tables)
            sent += 1
            try:
                result = asyncio.run(mcp.mcp_server.call_tool(tool.name, args))
                out_text = "".join(getattr(c, "text", "") for c in getattr(result, "content", []) or [])
            except Exception as error:  # noqa: BLE001 - the SDK's own refusal of bad arguments
                out_text = f"{type(error).__name__}: {error}"
                if not _is_validation(error):
                    failures.append(("tool_raised", "TOOL", tool.name, f"{label}: {out_text[:200]}"))
            if "Traceback" in out_text or re.search(r"\[SQL: |sqlite3\.|OverflowError|ValueError: |TypeError: |KeyError", out_text):
                failures.append(("tool_leak", "TOOL", tool.name, f"{label}: {out_text[:200]}"))
            with engine.connect() as c:
                failed = c.execute(text("select count(*) from audit_entries where tool like :t"), {"t": f"{tool.name}_failed"}).scalar()
            if failed:
                failures.append(("tool_failed", "TOOL", tool.name, f"{label}: the tool raised ({out_text[:160]})"))
                with engine.begin() as c:
                    c.execute(text("delete from audit_entries where tool like :t"), {"t": f"{tool.name}_failed"})
            after = _counts(tables)
            for what in stored_poison(tables, seen):
                failures.append(("stored", "TOOL", tool.name, f"{label}: {what}"))
            if after != before:
                broken = reads_broken(world.owner)
                if broken:
                    failures.append(("poisoned", "TOOL", tool.name, f"{label}: {'; '.join(broken)}"))
                    world.setup()
                    tables = _tables()
    return len(tools), sent, failures


def _is_validation(error: Exception) -> bool:
    words = str(error)
    return "validation error" in words.lower() or "Input should be" in words or "Field required" in words


# ---- the test ----------------------------------------------------------------------------------------


def _report(failures: list) -> str:
    by: dict = {}
    for kind, method, path, what in failures:
        by.setdefault((kind, method, path), []).append(what)
    return "\n".join(f"[{k}] {m} {p} ({len(v)}): {v[0]}" for (k, m, p), v in sorted(by.items()))


def test_every_write_and_every_tool_holds_under_schema_fuzzing(monkeypatch, client: TestClient):
    started = time.monotonic()
    _no_network(monkeypatch)
    rng = random.Random(SEED)
    world = World()
    sent, failures = run_rest(world, rng, 1.0 if FULL else 0.1)
    tool_count, tool_sent, tool_failures = run_tools(world, rng, 1.0 if FULL else 0.25, monkeypatch)
    failures += tool_failures
    world.heal()
    assert world.owner.get("/api/health").status_code == 200
    assert reads_broken(world.owner) == []
    out = os.environ.get("FZ_OUT")
    if out:
        with open(out, "w") as fh:
            fh.write(f"rest requests {sent}, tools {tool_count}, tool calls {tool_sent}, seconds {time.monotonic() - started:.0f}\n{_report(failures)}\n")
    assert not failures, f"{len(failures)} failures:\n{_report(failures)}"
