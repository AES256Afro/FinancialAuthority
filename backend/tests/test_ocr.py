"""M49: photos and scanned PDFs read on this server. The reader runs as its own process under limits; without it a
photo keeps no text, as before. A receipt's total and day are offered from its words, never applied on their own.
The programs are faked here, so the tests run anywhere."""

import subprocess
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import ocr
from app.db import engine
from app.engine import documents as docs

RECEIPT = """EXAMPLE HARDWARE
10/02/26 14:31
Hammer            12.99
Subtotal          12.99
Tax                1.07
TOTAL             14.06
"""


def _fake_reader(monkeypatch, text: str = RECEIPT, *, slow: bool = False) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(args, **kw):  # noqa: ANN001, ANN003
        calls.append(args)
        if slow:
            raise subprocess.TimeoutExpired(args, kw.get("timeout"))
        return subprocess.CompletedProcess(args, 0, stdout=text.encode(), stderr=b"")

    monkeypatch.setattr(ocr.subprocess, "run", run)
    return calls


def test_a_photo_is_read_and_its_receipt_total_and_day_offered(monkeypatch, owner: TestClient):
    calls = _fake_reader(monkeypatch)
    with Session(engine) as session:
        doc = docs.save_document(session, b"\x89PNG\r\n\x1a\nfake", filename="receipt.png", title="Receipt", kind="receipt")
        did = doc.id
    assert calls[0][:3] == ["/usr/bin/tesseract", "stdin", "stdout"] or calls[0][:3] == ["tesseract", "stdin", "stdout"]
    out = next(d for d in owner.get("/api/v1/documents").json()["documents"] if d["id"] == did)
    assert out["read_with"] == "ocr" and out["has_text"] is True
    assert out["suggested"] == {"amount": 14.06, "bought_on": "2026-10-02"}  # the TOTAL line, not the subtotal
    owner.put(f"/api/v1/documents/{did}", json={"amount": 14.06, "bought_on": "2026-10-02"})
    out = next(d for d in owner.get("/api/v1/documents").json()["documents"] if d["id"] == did)
    assert out["suggested"] is None and out["amount"] == 14.06


def test_without_the_reader_or_in_time_a_photo_simply_has_no_text(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    assert docs.read_pages_with(b"\x89PNG\r\n\x1a\nfake", "png") == ([], "")
    _fake_reader(monkeypatch, slow=True)
    assert docs.read_pages_with(b"\x89PNG\r\n\x1a\nfake", "png") == ([], "")
    _fake_reader(monkeypatch)
    assert ocr.image_text(b"x" * (ocr.MAX_IMAGE_BYTES + 1)) == ""  # too big to try


def test_a_future_date_or_a_subtotal_is_never_taken():
    text = "Subtotal 20.00\nTotal Tax 1.50\nTOTAL DUE: $21.50\nReturn by 12/31/2099\nSold 09/30/2026"
    assert ocr.receipt_reading(text, date(2026, 10, 14)) == {"total": 21.5, "bought_on": "2026-09-30"}
    assert ocr.receipt_reading("no figures here", date(2026, 10, 14)) == {}
