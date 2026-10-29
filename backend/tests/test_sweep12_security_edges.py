"""Security sweep 12, parts three to six: the push wire (ntfy, Gotify, the app's own web push), Home Assistant's
sensors under each redaction level, the reader of photos and documents against crafted files, and the import drop
folder against entries that are not plain files.

Push: the token rides only in its header; a 301 to another host is not followed with it; a hostile error body is
not logged raw; a web push payload is bounded and the VAPID private key never leaves the server. Home Assistant: a
key at the aggregates level reads figures without names, and no key writes anything. Documents: a 20,000 by 20,000
one-colour PNG, a PDF that is a zip, a TIFF of ten thousand pages, a file named `../../x.pdf` and a 15.1 MB file are
each refused or bounded within the documented limits; the reader runs without a shell; its temp folder goes; the
blob on disk is not the plaintext. The drop folder: a symlink is not followed, a FIFO does not hang the job, one
bad entry does not stop the rest, a run is bounded in files and bytes (fixed in jobs/scheduler.py in the
eighth correctness sweep)."""

from __future__ import annotations

import logging
import os
import struct
import tempfile
import threading
import time
import zlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import notify, ocr, webpush
from app.config import settings
from app.db import engine
from app.engine import documents as docs
from app.engine.household import seed
from app.jobs import scheduler
from app.main import app
from app.models import Transaction
from app.store import get_setting

PUSH_TOKEN = "tk_push_secret_7731_sweep12"


# ---- push --------------------------------------------------------------------------------------------------------


def _push_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ntfy_token_rides_only_in_the_header_a_redirect_is_not_followed_and_a_hostile_body_is_not_logged_raw(owner: TestClient, caplog):
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Request] = []
    hostile = "oops\n\x1b[31mWARNING fa.auth: owner signed in from 203.0.113.9\nERROR injected line\n<script>alert(1)</script>" + "x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "evil.test":
            return httpx.Response(200, text="got it")
        if request.url.path.endswith("/redirect"):
            return httpx.Response(301, headers={"Location": "http://evil.test/fa"})
        if request.url.path.endswith("/hostile"):
            return httpx.Response(500, text=hostile)
        return httpx.Response(200, json={"id": "1"})

    owner.put("/api/v1/push", json={"kind": "ntfy", "url": "http://ntfy.test", "topic": "fa", "token": PUSH_TOKEN})
    with Session(engine) as session:
        result = notify.send(session, "Safe to spend 120", "Rent is due Friday.", client=_push_client(handler))
        assert result["ok"]
        req = seen[-1]
        assert str(req.url) == "http://ntfy.test/fa" and req.headers["Authorization"] == f"Bearer {PUSH_TOKEN}" and req.headers["Title"] == "Safe to spend 120" and req.content == b"Rent is due Friday."
        assert PUSH_TOKEN not in str(req.url) and PUSH_TOKEN not in req.content.decode()

        notify.save_push(session, {"kind": "ntfy", "url": "http://ntfy.test", "topic": "redirect", "token": PUSH_TOKEN})
        result = notify.send(session, "Hello", "body", client=_push_client(handler))
        assert result["ok"] is False
        assert [r.url.host for r in seen] == ["ntfy.test", "ntfy.test"], "the 301 was followed, with the token, to another host"

        notify.save_push(session, {"kind": "ntfy", "url": "http://ntfy.test", "topic": "hostile", "token": PUSH_TOKEN})
        result = notify.send(session, "Hello", "body", client=_push_client(handler))
        assert result["ok"] is False and "\n" not in result["detail"] and "\x1b" not in result["detail"] and "<script>" not in result["detail"] and len(result["detail"]) <= 200
    failures = [r.getMessage() for r in caplog.records if "push failed" in r.getMessage()]
    assert failures
    for message in failures:
        # One bounded line per failure: no line break the body could use to draw a log line of its own, no escape code, no tag.
        assert "\n" not in message and "\x1b" not in message and "<script>" not in message and len(message) < 400, message
    assert not [line for line in caplog.text.split("\n") if line.startswith(("ERROR injected line", "WARNING fa.auth"))]
    with Session(engine) as session:
        kept = get_setting(session, notify.LAST_ERROR, {}) or {}
        assert "\n" not in kept.get("detail", "") and len(kept.get("detail", "")) <= 200
    assert PUSH_TOKEN not in caplog.text and PUSH_TOKEN not in owner.get("/api/v1/push").text and PUSH_TOKEN not in owner.get("/api/v1/apphealth").text


def test_gotify_token_rides_only_in_its_header(owner: TestClient):
    seen: list[httpx.Request] = []
    handler = lambda request: (seen.append(request), httpx.Response(200, json={"id": 1}))[1]
    owner.put("/api/v1/push", json={"kind": "gotify", "url": "http://gotify.test", "token": PUSH_TOKEN})
    with Session(engine) as session:
        assert notify.send(session, "Hi", "there", client=_push_client(handler))["ok"]
    req = seen[-1]
    assert req.headers["X-Gotify-Key"] == PUSH_TOKEN and PUSH_TOKEN not in str(req.url) and PUSH_TOKEN not in req.content.decode()


def test_web_push_payload_is_bounded_and_the_vapid_private_key_never_leaves(owner: TestClient):
    sent: list[dict] = []
    owner.post("/api/v1/push/devices", json={"endpoint": "https://push.example.test/sub/1", "keys": {"p256dh": "p" * 80, "auth": "a" * 20}, "label": "Phone"})
    with Session(engine) as session:
        keys = webpush.ensure_keys(session)
        private_pem = __import__("app.crypto", fromlist=["decrypt"]).decrypt(keys["private_enc"])
        assert "PRIVATE KEY" in private_pem
        import app.webpush as wp

        def fake(**kwargs):
            sent.append(kwargs)

        original = wp.webpush
        wp.webpush = fake
        try:
            result = webpush.send_all(session, "T" * 5000, "B" * 50_000, link="/today")
        finally:
            wp.webpush = original
    assert result["sent"] == 1 and len(sent[0]["data"]) < 1200
    assert "subscription_info" in sent[0] and sent[0]["subscription_info"]["endpoint"] == "https://push.example.test/sub/1"
    for path in ("/api/v1/push", "/api/v1/push/devices", "/api/v1/apphealth", "/api/v1/about", "/api/v1/privacy"):
        got = owner.get(path)
        if got.status_code == 200:
            assert "PRIVATE KEY" not in got.text and keys["private_enc"] not in got.text and private_pem.split("\n")[1] not in got.text, path
    devices = owner.get("/api/v1/push/devices").json()
    assert devices["public_key"] == keys["public"] and "endpoint" not in str(devices["devices"]) and "push.example.test" not in str(devices)


# ---- Home Assistant --------------------------------------------------------------------------------------------


def _key(owner: TestClient, redaction: str, scopes: list[str] | None = None) -> str:
    got = owner.post("/api/v1/keys", json={"name": f"ha-{redaction}", "scopes": scopes or ["read"], "redaction": redaction})
    assert got.status_code == 200, got.text
    return got.json()["token"]


def test_home_assistant_reads_figures_at_each_level_and_writes_nothing(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    tokens = {level: _key(owner, level) for level in ("full", "merchants", "aggregates")}
    outside = TestClient(app)
    names = ("Everyday Checking", "Rewards Card", "Example Bank", "Car insurance", "Water and trash", "Example Employer", "Electricity", "Internet", "Phone")
    for level, token in tokens.items():
        got = outside.get("/api/v1/ha", headers={"Authorization": f"Bearer {token}"})
        assert got.status_code == 200, (level, got.text)
        out = got.json()
        assert set(out) >= {"safe_to_spend", "payday", "days_to_payday", "next_bill_amount", "next_bill_date", "needs_you", "status", "status_word"}
        if level == "aggregates":
            assert out["sentence"] is None and out["next_bill_name"] in (None, "A bill")
            assert not any(n.lower() in got.text.lower() for n in names), got.text
        else:
            assert out["next_bill_name"] not in (None, "A bill") or out["next_bill_amount"] is None
        # Nothing else on /api answers a key that hides details; every write path refuses every key.
        if level != "full":
            for path in ("/api/v1/glance", "/api/v1/accounts", "/api/v1/bills", "/api/v1/watch", "/api/v1/snapshot", "/api/v1/documents"):
                assert owner.get(path).status_code == 200, path  # a route that exists, so the refusal is the key's
                assert outside.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 403, (level, path)
        for method, path in (("POST", "/api/v1/watch/x/expected"), ("PUT", "/api/v1/push"), ("POST", "/api/v1/approvals"), ("DELETE", "/api/v1/banks/1"), ("POST", "/api/v1/facts/1/confirm"),
                             ("PUT", "/api/v1/models/advisor"), ("POST", "/api/v1/keys"), ("POST", "/api/v1/documents"), ("PUT", "/api/v1/rules"), ("POST", "/api/v1/push/test")):
            got = outside.request(method, path, headers={"Authorization": f"Bearer {token}"}, json={})
            assert got.status_code in (401, 403, 404, 405), (level, method, path, got.status_code)
    rows = owner.get("/api/v1/audit").json() if owner.get("/api/v1/audit").status_code == 200 else {}
    assert "ha_sensors" in str(rows)


# ---- the reader and the documents ------------------------------------------------------------------------------


def png_bomb(size: int = 20_000) -> bytes:
    """A valid PNG of `size` by `size` pixels of one colour: a few hundred kilobytes that decode to 400 million pixels."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 1, 0, 0, 0, 0)  # one-bit greyscale
    row = b"\x00" + b"\x00" * ((size + 7) // 8)
    packer = zlib.compressobj(9)
    data = b"".join(packer.compress(row) for _ in range(size)) + packer.flush()
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", data) + chunk(b"IEND", b"")


def _reader(monkeypatch, *, text: str = "TOTAL 14.06", fail: bool = False) -> list[tuple[list, dict]]:
    calls: list[tuple[list, dict]] = []
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(args, **kw):
        calls.append((args, kw))
        if fail:
            raise OSError("reader crashed")
        import subprocess

        return subprocess.CompletedProcess(args, 0, stdout=text.encode(), stderr=b"")

    monkeypatch.setattr(ocr.subprocess, "run", run)
    return calls


def _scan_dirs() -> set[str]:
    return {p for p in os.listdir(tempfile.gettempdir()) if p.startswith("fa-scan-")}


def test_crafted_files_are_refused_or_bounded_the_reader_has_no_shell_and_the_blob_is_sealed(owner: TestClient, monkeypatch):
    calls = _reader(monkeypatch)
    bomb = png_bomb()
    assert len(bomb) < 2_000_000
    before = _scan_dirs()
    with Session(engine) as session:
        doc = docs.save_document(session, bomb, filename="../../x.png", title="Bomb", kind="receipt")
        stored = docs.folder() / f"{doc.sha256}.bin"
        assert doc.filename == "x.png" and stored.is_file()
        on_disk = stored.read_bytes()
        assert on_disk.startswith(b"fa1:") and b"\x89PNG" not in on_disk and bomb[100:200] not in on_disk, "the file on disk is the plaintext"
        assert doc.pages_enc.startswith("fa1:") and "TOTAL" not in doc.pages_enc
        assert docs.file_of(doc) == bomb
    assert len(calls) == 1
    args, kw = calls[0]
    assert isinstance(args, list) and args[0].endswith("tesseract") and args[1:3] == ["stdin", "stdout"]
    assert not kw.get("shell") and set(kw["env"]) <= {"PATH", "OMP_THREAD_LIMIT"} and 0 < kw["timeout"] <= ocr.IMAGE_SECONDS and kw.get("preexec_fn") is not None
    assert not any("x.png" in str(a) or ".." in str(a) for a in args)
    assert not list(Path(settings.data_dir).parent.glob("x.png")) and not list(Path(settings.data_dir).glob("x.png"))

    # A PDF that is a zip: refused by its first bytes. One that only starts like a PDF reads as no text, the scan is
    # tried under the reader's limits, and its working folder goes.
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.txt", "x" * 100_000)
    with Session(engine) as session:
        with pytest.raises(ValueError):
            docs.save_document(session, buf.getvalue(), filename="archive.pdf")
        with pytest.raises(ValueError):
            docs.save_document(session, b"II*\x00" + b"\x00" * 100, filename="pages.tiff")
        with pytest.raises(ValueError):
            docs.save_document(session, b"II*\x00" + b"\x00" * 100, filename="pages.tif")
        calls.clear()
        doc = docs.save_document(session, b"%PDF-1.4\n" + buf.getvalue(), filename="zip.pdf")
        assert doc.pages == 0 or not any(p.strip() for p in docs.pages_of(doc))
    for args, kw in calls:
        assert isinstance(args, list) and not kw.get("shell") and kw["timeout"] <= ocr.SCAN_SECONDS
    assert _scan_dirs() == before, "a scan's working folder was left behind"
    calls = _reader(monkeypatch, fail=True)
    assert ocr.scan_pages(b"%PDF-1.4\nbroken") == [] and _scan_dirs() == before
    assert ocr.image_text(bomb) == ""

    # Size: a 15.1 MB photo is kept but never handed to the reader; past 25 MB the route refuses it.
    calls = _reader(monkeypatch)
    big = b"\x89PNG\r\n\x1a\n" + os.urandom(1024) * (15 * 1024 + 100)
    got = owner.post("/api/v1/documents", files={"file": ("../../big.png", big, "image/png")}, data={"kind": "receipt"})
    assert got.status_code == 200, got.text
    assert got.json()["filename"] == "big.png" and got.json()["has_text"] is False and calls == []
    too_big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (docs.MAX_BYTES + 10)
    got = owner.post("/api/v1/documents", files={"file": ("huge.png", too_big, "image/png")}, data={"kind": "receipt"})
    assert got.status_code == 400 and "25 MB" in got.text
    got = owner.post("/api/v1/documents", files={"file": ("pages.tiff", b"II*\x00" + b"\x00" * 100, "image/tiff")}, data={"kind": "receipt"})
    assert got.status_code == 400
    assert ocr.image_text(big) == "" and calls == []


# ---- the import drop folder ----------------------------------------------------------------------------------


@pytest.fixture
def drop() -> Path:
    settings.ensure_dirs()
    root = settings.import_root
    for p in root.rglob("*"):
        if p.is_symlink() or p.is_file() or p.is_fifo():
            p.unlink()
    yield root
    for p in root.rglob("*"):
        if p.is_symlink() or p.is_file() or p.is_fifo():
            p.unlink()


def _watch(seconds: float = 8.0) -> tuple[bool, str]:
    """Run the import job in a thread with a budget; (finished, what it said or raised)."""
    out: dict[str, str] = {}

    def go() -> None:
        try:
            out["said"] = scheduler.import_watch()
        except Exception as error:  # noqa: BLE001
            out["said"] = f"{type(error).__name__}: {error}"

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return not t.is_alive(), out.get("said", "")


CSV = "Date,Description,Amount\n2026-10-01,EXAMPLE GROCER 12,-12.34\n2026-10-03,EXAMPLE FUEL,-40.00\n"


def test_a_symlink_in_the_drop_folder_is_not_followed(drop: Path, tmp_path: Path):
    with Session(engine) as session:
        seed(session)
    outside = tmp_path / "elsewhere.csv"
    outside.write_text(CSV)
    (drop / "Everyday Checking export.csv").symlink_to(outside)
    (drop / "passwd.csv").symlink_to("/etc/passwd")
    finished, _said = _watch()
    assert finished
    with Session(engine) as session:
        assert not [t for t in session.exec(select(Transaction)).all() if "EXAMPLE GROCER 12" in (t.raw_name or "") + (t.merchant or "")], "a symlink's target was imported"


def test_a_fifo_in_the_drop_folder_does_not_hang_the_job(drop: Path):
    fifo = drop / "pipe.csv"
    os.mkfifo(fifo)
    try:
        finished, _said = _watch(3.0)
    finally:
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)  # let a reader blocked on it go
            os.close(fd)
        except OSError:
            pass
        time.sleep(0.2)
        fifo.unlink()
    assert finished, "the job sat on the FIFO"


def test_one_bad_entry_does_not_stop_the_good_files(drop: Path):
    with Session(engine) as session:
        seed(session)
    (drop / "dangling.pdf").symlink_to(drop / "never-there.pdf")
    (drop / "Everyday Checking export.csv").write_text(CSV)
    finished, said = _watch()
    assert finished and "EXAMPLE" not in said
    with Session(engine) as session:
        assert [t for t in session.exec(select(Transaction)).all() if "EXAMPLE GROCER 12" in (t.raw_name or "") + (t.merchant or "")], said


def test_a_file_past_the_upload_ceiling_is_refused_unread(drop: Path):
    big = drop / "huge.csv"
    with big.open("wb") as f:
        f.write(b"Date,Description,Amount\n")
        f.truncate(60 * 1024 * 1024)
    started = time.monotonic()
    finished, said = _watch(20.0)
    assert finished and time.monotonic() - started < 2 and ("too big" in said.lower() or "larger" in said.lower() or "MB" in said), said


def test_a_folder_full_of_files_is_taken_in_bounded_runs(drop: Path, monkeypatch):
    for i in range(300):
        (drop / f"empty-{i}.csv").write_text("")
    writes: list[int] = []
    original = scheduler.set_setting
    monkeypatch.setattr(scheduler, "set_setting", lambda *a, **k: (writes.append(1), original(*a, **k))[1])
    finished, _said = _watch(60.0)
    assert finished
    assert len(writes) <= 5, f"the record was rewritten {len(writes)} times in one run"


def test_a_symlink_loop_in_the_drop_folder_ends(drop: Path):
    (drop / "loop").symlink_to(drop)
    (drop / "paystubs" / "up").symlink_to(drop)
    finished, _said = _watch(10.0)
    assert finished, "the folder walk did not end"
