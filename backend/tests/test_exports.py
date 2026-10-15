"""Exports: every layout renders the household in every report format; the data formats parse and the
plain-text accounting balances; no export ever holds a secret; the routes are for the owner's session only;
templates are checked; the calendar feed answers only to its token and stops when revoked."""

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crypto
from app.db import engine
from app.engine.household import seed
from app.exports import data as data_formats
from app.exports import render
from app.exports.model import SECTIONS, build_report, registers
from app.exports.templates import LAYOUTS, MASCOTS
from app.models import MailAccount
from app.store import set_setting

TODAY = date(2026, 10, 14)
ALL_SECTIONS = [k for k, _ in SECTIONS]


@pytest.fixture
def household():
    with Session(engine) as session:
        seed(session)
        yield session


def test_every_layout_renders_every_report_format(household: Session):
    report = build_report(household, TODAY, ALL_SECTIONS, notes="Rent goes first.")
    assert [s["key"] for s in report["sections"]] == ALL_SECTIONS
    regs = registers(household)
    for layout in LAYOUTS:
        for mascot in (None, "kitties"):
            md = render.markdown(report, layout, "layout", mascot)
            assert report["title"] in md and report["not_advice"] in md
            page = render.html(report, layout, "app", mascot)
            assert page.startswith("<!doctype html>") and "Not financial advice" in page and "<script" not in page
            pdf = render.pdf(report, layout, "ink", mascot)
            assert pdf[:5] == b"%PDF-" and len(pdf) > 2000
        docx = render.docx(report, layout, "layout", MASCOTS and LAYOUTS[layout]["mascot"])
        assert zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").count(b"<w:tbl>") >= 3
        book = render.xlsx(report, regs, layout, "layout")
        names = zipfile.ZipFile(io.BytesIO(book)).namelist()
        assert "xl/workbook.xml" in names and any(n.startswith("xl/charts/") for n in names)
    # the kitties draw as shapes in the HTML; no people anywhere, no outside fetches
    page = render.html(report, "kitties", "layout", "kitties")
    assert page.count("<svg") >= 2 and "http://" not in page.replace("http://www.w3.org/2000/svg", "") and "https://" not in page


def test_the_data_formats_parse_and_the_books_balance(household: Session):
    z = zipfile.ZipFile(io.BytesIO(data_formats.csv_zip(household, TODAY)))
    assert {"transactions.csv", "bills.csv", "debts.csv", "accounts.csv", "README.txt"} <= set(z.namelist())
    assert z.read("transactions.csv").count(b"\n") > 100
    everything = json.loads(data_formats.everything_json(household, TODAY))
    assert everything["as_of"] == TODAY.isoformat() and everything["snapshot"]["safe_to_spend"]["amount"] == 55.0 and everything["registers"]["bills"]
    ics = data_formats.calendar(household, TODAY, amounts=False).decode()
    assert ics.startswith("BEGIN:VCALENDAR") and "SUMMARY:Payday" in ics and "$" not in ics and ics.count("BEGIN:VEVENT") > 10
    assert "$" in data_formats.calendar(household, TODAY, amounts=True).decode()
    bean = data_formats.beancount(household, TODAY).decode()
    blocks = [b for b in bean.split("\n\n") if re.match(r"\d{4}-\d{2}-\d{2} \*", b.strip())]
    assert len(blocks) > 100
    for b in blocks:
        amounts = [Decimal(m) for m in re.findall(r"  (-?\d+\.\d{2}) USD", b)]
        assert len(amounts) == 2 and sum(amounts) == 0, b
    opened = set(re.findall(r"open (\S+) USD", bean))
    used = set(re.findall(r"^  (\S+)  ", bean, re.MULTILINE))
    assert used <= opened and all(re.match(r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+$", a) for a in opened)
    led = data_formats.ledger(household, TODAY).decode()
    assert led.count("Equity:Transfers") >= 1 and "$" in led
    qif = data_formats.qif(household, TODAY).decode()
    assert qif.count("^") > 100 and "!Type:Bank" in qif
    ofx = data_formats.ofx(household, TODAY).decode()
    root = ET.fromstring(ofx.split("?>", 2)[-1].strip())
    assert root.tag == "OFX" and len(root.findall(".//STMTTRN")) > 100


SECRETS = ["correct horse battery", "test-session-secret", "test-data-key", "mailbox-pass-7731", "plaid-secret-9a8b"]


def _texts(blob: bytes) -> list[str]:
    if blob[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(blob))
        return [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
    return [blob.decode("latin-1", "replace")]


def test_no_export_ever_holds_a_secret(owner: TestClient):
    with Session(engine) as session:
        seed(session)
        session.add(MailAccount(label="Proton", kind="proton", host="bridge", port=143, username="owner@example.test", password_enc=crypto.encrypt("mailbox-pass-7731"), use_ssl=False, starttls=True, folders="INBOX"))
        set_setting(session, "banks.secrets", {"plaid": {"client_id": "cid", "secret": crypto.encrypt("plaid-secret-9a8b")}})
        session.commit()
    for fmt in data_formats.FORMATS:
        r = owner.get("/api/v1/exports/file", params={"format": fmt, "layout": "dossier", "sections": ",".join(ALL_SECTIONS)})
        assert r.status_code == 200, (fmt, r.text[:200])
        assert r.headers["cache-control"] == "no-store" and "attachment" in r.headers["content-disposition"]
        for text in _texts(r.content):
            for secret in SECRETS:
                assert secret not in text, (fmt, secret)
            assert "fa1:" not in text, fmt  # no encrypted blob either


def test_exports_are_the_owners_alone_and_templates_are_checked(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    cat = owner.get("/api/v1/exports").json()
    assert len(cat["layouts"]) == 10 and len(cat["formats"]) == 12 and {m["id"] for m in cat["mascots"]} >= {"kitties", "forest", "sprites", "acorns"}
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"]}).json()
    reader = TestClient(client.app)
    assert reader.get("/api/v1/exports", headers={"Authorization": f"Bearer {key['token']}"}).status_code == 403
    assert reader.get("/api/v1/exports/file", params={"format": "json"}, headers={"Authorization": f"Bearer {key['token']}"}).status_code == 403
    assert TestClient(client.app).get("/api/v1/exports/file", params={"format": "json"}).status_code == 401
    saved = owner.post("/api/v1/exports/templates", json={"name": "Kitty report", "layout": "kitties", "palette": "layout", "mascot": "kitties", "sections": ["summary", "this_check"], "css": "h2{letter-spacing:.1em}"}).json()
    tid = saved["template"]["id"]
    r = owner.get("/api/v1/exports/file", params={"format": "html", "template": tid})
    assert r.status_code == 200 and b"letter-spacing:.1em" in r.content and b"Bills by check" not in r.content
    assert owner.post("/api/v1/exports/templates", json={"name": "x", "css": "body{background:url(https://evil.test/x)}"}).status_code == 400
    assert owner.post("/api/v1/exports/templates", json={"name": "x", "css": "</style><script>alert(1)</script>"}).status_code == 400
    assert owner.post("/api/v1/exports/templates", json={"name": "x", "layout": "nope"}).status_code == 400
    preview = owner.post("/api/v1/exports/preview", json={"layout": "horizon", "sections": ["summary"]}).json()
    assert "<h1>" in preview["html"] and preview["css_refused"] is False
    assert owner.delete(f"/api/v1/exports/templates/{tid}").json()["templates"] == []
    audit = owner.get("/api/v1/audit").json()
    assert any(e["tool"] == "export:html" for e in audit["entries"])


def test_the_calendar_feed_answers_only_to_its_token(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    anon = TestClient(client.app)
    made = owner.post("/api/v1/calendar/feed", json={"amounts": False}).json()
    path = "/" + made["url"].split("/", 3)[3]
    r = anon.get(path)
    assert r.status_code == 200 and r.text.startswith("BEGIN:VCALENDAR") and "$" not in r.text
    assert anon.get(path.replace(".ics", "x.ics")).status_code == 404
    assert anon.get("/api/calendar/short.ics").status_code == 404
    again = owner.post("/api/v1/calendar/feed", json={"amounts": True}).json()
    assert anon.get(path).status_code == 404  # the old address stops when a new one is made
    new_path = "/" + again["url"].split("/", 3)[3]
    assert "$" in anon.get(new_path).text
    assert owner.get("/api/v1/calendar/feed").json() == {"active": True, "amounts": True, "created": owner.get("/api/v1/calendar/feed").json()["created"]}
    owner.delete("/api/v1/calendar/feed")
    assert anon.get(new_path).status_code == 404
