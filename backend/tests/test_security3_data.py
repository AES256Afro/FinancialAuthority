"""Security round three, untrusted data: a crafted PDF or spreadsheet is read under a budget, redacted callers see
no links, senders or names, a resolved fact cannot be confirmed again, figures from third-party text never verify a
reply, and lease terms are read in linear time."""

import io
import json
import time
import zipfile
import zlib

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.documents import read_pages, terms_of
from app.llm.citations import engine_numbers, unverified
from app.mcp_server import redact
from app.models import Bill, ExtractedFact
from app.parsers.paystub import pdf_pages


def shared_stream_pdf(content_mb: float, pages: int) -> bytes:
    """Many pages pointing at one compressed content stream: the shape that made reading slow."""
    op = b"BT /F1 9 Tf 10 10 Td (Net pay 1.00) Tj ET\n"
    stream = zlib.compress(op * int(content_mb * 1_000_000 / len(op)), 9)
    kids = " ".join(f"{4 + i} 0 R" for i in range(pages))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
            b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream"]
    objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"] * pages
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.7\n")
    for n, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets))
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_a_pdf_whose_pages_share_one_stream_is_read_once_and_quickly():
    raw = shared_stream_pdf(1, 40)
    started = time.monotonic()
    pages = pdf_pages(raw)
    assert time.monotonic() - started < 5
    assert len(pages) == 40 and "Net pay" in pages[0] and all(p == "" for p in pages[1:])


def test_a_stream_that_inflates_past_the_limit_gives_no_text_rather_than_hours():
    started = time.monotonic()
    assert pdf_pages(shared_stream_pdf(6, 1)) == [""]
    assert time.monotonic() - started < 5


def test_a_spreadsheet_that_expands_too_far_is_kept_without_text():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", "<sst>" + "<si><t>a</t></si>" * 2_000_000 + "</sst>")
    started = time.monotonic()
    assert read_pages(raw.getvalue(), "xlsx") == [] and time.monotonic() - started < 2


def test_lease_terms_read_in_linear_time_and_read_amounts_and_pet_rent_right():
    started = time.monotonic()
    terms_of(["30" + " " * 20_000] * 60)
    terms_of(["rent" + " " * 20_000] * 60)
    assert time.monotonic() - started < 2
    got = terms_of(["Pet Rent: $25.00 per month.\nMonthly rent of $1450.00 is due on or before the 1st."])
    assert got["rent"]["value"] == 1450.0 and got["pet_rent"]["value"] == 25.0


def test_redacted_callers_never_see_links_senders_or_names_in_lists():
    fact = {"fields": {"url": "https://exports.example.test/dl?sig=abc", "host": "exports.example.test", "sender": "noreply@example.test", "amount": 12.5},
            "missing": ["Example Water Co"], "billing_company": "Example Billing Co", "word": "Last day to return Example Store: Sun Oct 25"}
    merchants = redact(fact, "merchants")
    assert merchants["fields"]["url"] == "(redacted)" and merchants["fields"]["sender"] == "(redacted)" and merchants["fields"]["amount"] == 12.5
    aggregates = redact(fact, "aggregates")
    assert aggregates["missing"] == ["(redacted)"] and aggregates["billing_company"] == "(redacted)" and aggregates["word"] == "(redacted)"


def test_a_fact_already_resolved_cannot_be_confirmed_or_rejected_again(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Example Rent", amount=1295.0, due_day=1))
        f = ExtractedFact(kind="bill_due", fields=json.dumps({"name": "Example Rent", "amount": 1.0, "due_date": "2026-11-01"}), status="pending")
        session.add(f)
        session.commit()
        fid = f.id
    assert owner.post(f"/api/v1/facts/{fid}/reject").status_code == 200
    assert owner.post(f"/api/v1/facts/{fid}/confirm", json={"apply": True}).status_code == 409
    assert owner.post(f"/api/v1/facts/{fid}/reject").status_code == 409


def test_figures_from_third_party_text_never_verify_and_a_figure_ending_a_sentence_is_read_whole():
    assert unverified("You can spend $4,321.99.", engine_numbers({"amount": 4.0})) == ["$4,321.99"]
    assert unverified("It comes to 4321.99.", set()) == ["4321.99"]
    assert unverified("Safe to spend is $220.50.", engine_numbers({"safe": 220.5})) == []
