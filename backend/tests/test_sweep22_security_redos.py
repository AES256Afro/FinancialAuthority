"""Security sweep 22 (the seventeenth sweep), part two: crafted inputs that make the app work hard. Every regular
expression run over text an outsider writes (a message, a file, a vendor's field, a model's reply, a search string),
and the parsers and the mail pipeline at the largest input each accepts, timed; what took seconds to hours on a crafted
input of an allowed size is bounded now.

The ceilings are generous (a second or a few where the crafted input took from 13 s to longer than anyone waited), so
a slow runner passes; the inputs are the shapes that took that long. Three findings are in the parsers, which another
change owns: their tests run the parser in a child process with a timeout, are marked xfail with the exact change, and
pass once it lands."""

from __future__ import annotations

import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import auth
from app.db import engine
from app.engine.household import seed
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]


def _cpu(fn, *args):  # noqa: ANN001, ANN202
    started = time.process_time()
    out = fn(*args)
    return time.process_time() - started, out


def _key() -> TestClient:
    with Session(engine) as session:
        _, token = auth.create_key(session, "sweep22-redos", {"read"}, "full")
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ---- regular expressions in the app's own modules ----------------------------------------------------------------------


def test_a_receipts_total_is_read_in_linear_time():
    """ocr.TOTAL had five blank runs side by side with only optional words between: "total" and a run of spaces with no
    figure after it was tried every way the run splits, the run's length to the fourth power (10 KB did not finish in
    20 s; a receipt kept without its total runs it on every listing of the documents). The runs are possessive now."""
    from datetime import date

    from app.ocr import TOTAL, receipt_reading

    spent, _ = _cpu(TOTAL.findall, "total" + " " * 1_000_000 + "x")
    assert spent < 1.0
    spent, _ = _cpu(TOTAL.findall, ("total" + " " * 40 + "x\n") * 20_000)
    assert spent < 1.0
    assert receipt_reading("Subtotal 10.00\nTax 1.00\n  Grand  Total : USD $ 1,011.00\n", date(2026, 10, 14))["total"] == 1011.00
    assert TOTAL.findall("TOTAL DUE 5.00\ntotal paid - 7.50\nTotal:12.00\ntotal dues 9.00") == ["5.00", "7.50", "12.00"]


def test_a_receipt_with_a_blank_run_lists_quickly(owner: TestClient):
    """The same, end to end: a kept receipt whose words are "Total" and a long run of blanks (a store's email, a scan's
    text) made every GET /documents read it again."""
    body = ("Thanks for shopping.\nTotal" + " " * 20_000 + "see you soon\n").encode()
    assert owner.post("/api/v1/documents", files={"file": ("receipt.txt", body, "text/plain")}, data={"title": "Receipt", "kind": "receipt"}).status_code == 200
    started = time.perf_counter()
    for path in ("/api/v1/documents", "/api/v1/documents/months", "/api/v1/receipts"):
        assert owner.get(path).status_code == 200
    assert time.perf_counter() - started < 3.0


def test_the_logo_check_is_linear():
    """SVG_REFUSED: "href=" and a run of blanks was split every way between the two blank runs around the quote
    (300 KB, the largest mark, did not finish in 20 s)."""
    from app.api.appearance import SVG_REFUSED

    for text in ("<svg><a href=" + " " * 300_000 + "!", "<svg on" + "x" * 300_000 + " " * 1000):
        spent, _ = _cpu(SVG_REFUSED.search, text)
        assert spent < 1.0
    for bad in ('<svg onload="x">', '<svg><a href = " javascript:x">', "<svg><a xlink:href = ' http:x'>", "<svg ONCLICK =1>"):
        assert SVG_REFUSED.search(bad), bad
    assert not SVG_REFUSED.search('<svg><a href="#part">')


def test_the_citation_check_on_a_long_reply():
    """unverified() compared each figure with cents against every engine number: a 1 MB reply of cents against a
    snapshot's three thousand numbers took 13 s of the chat's worker. Bisection now, with the same answers."""
    from app.llm.citations import figures_in, unverified

    rng = random.Random(22)
    allowed = {round(rng.random() * 10_000, 2) for _ in range(3000)} | {12.34}
    spent, bad = _cpu(unverified, "$1.23 " * 170_000, allowed)
    assert spent < 2.0 and len(bad) == 170_000
    text = " ".join(f"${rng.random() * 10_000:.2f}" for _ in range(2000)) + " " + " ".join(f"${a:.2f}" for a in list(allowed)[:500]) + " $12.3449 $12.35"
    brute = [tok for tok, v in figures_in(text) if not (v in allowed if v == int(v) else any(abs(v - a) < 0.005 for a in allowed))]
    assert unverified(text, allowed) == brute and "$12.35" in brute and "$12.3449" not in brute


# ---- files: OFX and CSV -----------------------------------------------------------------------------------------------


def _ofx(statements: int) -> str:
    return "<OFX>" + "<STMTRS><ACCTID>1</STMTRS>" * statements + "<STMTTRN><DTPOSTED>20260101<TRNAMT>1.00<NAME>x</STMTTRN>" * statements


def test_an_ofx_of_many_statements_is_read_in_linear_time():
    """Each transaction looked through every statement for the one that holds it: a 3 MB file of empty statements took
    26 s, a 50 MB one would have taken hours. By bisection now, and an OFX date is read directly rather than after eight
    formats fail (that alone was 20 of a 50 MB file's 24 s)."""
    from app.connectors.files import parse_date, parse_ofx

    spent, got = _cpu(parse_ofx, _ofx(100_000))
    assert spent < 3.0 and got["total"] == 100_000
    assert parse_date("20261014").isoformat() == "2026-10-14" and parse_date("20261314") is None
    two = "<OFX><STMTRS><ACCTID>111</STMTRS><CCSTMTRS><ACCTID>222<STMTTRN><DTPOSTED>20260102<TRNAMT>-5<NAME>card</STMTTRN></CCSTMTRS>" \
          "<STMTRS><ACCTID>333<STMTTRN><DTPOSTED>20260103<TRNAMT>7<NAME>bank</STMTTRN></STMTRS><STMTTRN><DTPOSTED>20260104<TRNAMT>1<NAME>loose</STMTTRN>"
    held = {r["merchant"]: (r["account"], r["account_kind"]) for r in parse_ofx(two)["rows"]}
    assert held == {"card": ("222", "card"), "bank": ("333", ""), "loose": ("", "")}


def test_an_ofx_at_the_upload_ceiling():
    """The heaviest: 49 MB of the smallest statements and transactions the reader accepts."""
    from app.connectors.files import parse_ofx

    spent, got = _cpu(parse_ofx, _ofx(600_000))
    assert spent < 10.0 and got["total"] == 600_000


def test_a_csv_header_of_a_million_columns_is_refused_at_once(owner: TestClient):
    """Each column lookup normalised and scanned every column name: a 10 MB header of a million columns took 11 s
    before a row was read. A file with more columns than any export has is refused."""
    from app.connectors.files import MAX_COLUMNS, detect

    header = ",".join(f"c{i}" for i in range(1_000_000)) + "\n"
    started = time.process_time()
    with pytest.raises(ValueError, match="columns"):
        detect(header)
    assert time.process_time() - started < 1.0
    r = owner.post("/api/v1/imports/preview", files={"file": ("wide.csv", header.encode(), "text/csv")}, data={"kind": "checking"})
    assert r.status_code == 400 and "columns" in r.json()["detail"]
    ok = ",".join(["Date", "Description", "Amount"] + [f"extra {i}" for i in range(MAX_COLUMNS - 3)]) + "\n2026-01-01,Example,-1.00" + "," * (MAX_COLUMNS - 3) + "\n"
    assert detect(ok)["mapping"]["amount"] == "Amount"


# ---- the mail pipeline -------------------------------------------------------------------------------------------------


def _message(headers: list[tuple[str, str]], body: bytes, ctype: str = "text/plain; charset=utf-8") -> bytes:
    head = "".join(f"{k}: {v}\r\n" for k, v in headers)
    return (head + f"MIME-Version: 1.0\r\nContent-Type: {ctype}\r\n\r\n").encode() + body


def test_a_25_mb_message_built_to_be_slow_is_read_quickly():
    """The standard library's header parser is quadratic or worse on some shapes and the reader spends its time per
    line and per part: a From of four million addresses, a Subject of encoded words, a Subject of one long line, an
    Authentication-Results of repeats and 600,000 empty parts each held the sync past 40 s; 25 MB of empty lines took
    2.3 s. Headers are cut to 8,000 characters as they are read, and the lines and parts read are bounded."""
    from app.mail.imap import parse_message

    size = 25_000_000
    plain = [("From", "Payroll <pay@example.test>"), ("Subject", "Your pay stub"), ("Date", "Fri, 09 Oct 2026 11:12:00 -0500")]
    crafted = {
        "many addresses": _message([("From", "a@b, " * (size // 5)), ("Subject", "hi")], b"hello"),
        "encoded words": _message([("From", "x@example.test"), ("Subject", "=?utf-8?q?a?= " * (size // 14))], b"hello"),
        "one long subject": _message([("From", "x@example.test"), ("Subject", "your " * (size // 5))], b"hello"),
        "angle brackets": _message([("From", "<" * size + "x@example.test"), ("Subject", "hi")], b"hello"),
        "auth results": _message(plain + [("Authentication-Results", "dkim=" * (size // 5))], b"hi"),
        "empty parts": _message(plain, b"--B\r\nContent-Type: text/plain\r\n\r\nx\r\n" * (size // 40) + b"--B--", 'multipart/mixed; boundary="B"'),
        "empty lines": _message(plain, b"\n" * size),
        "bare returns": _message(plain, b"\r" * size),
    }
    for name, raw in crafted.items():
        spent, parsed = _cpu(parse_message, raw)
        assert spent < 2.0, (name, spent)
        assert len(parsed.subject) <= 1000 and len(parsed.text) <= 200_000
    nested = _message([("From", "(" * 10_000 + ")" * 10_000 + " x@example.test"), ("Subject", "hi")], b"hello")
    started = time.process_time()
    try:
        parse_message(nested)  # the sync reads past a message that raises; it must not take long to raise
    except RecursionError:
        pass
    assert time.process_time() - started < 1.0


def test_a_bounded_header_still_reads_as_before():
    from app.mail.imap import parse_message
    from tests.test_mail import MESSAGES

    p = parse_message(MESSAGES[1])
    assert (p.sender, p.sender_name, p.subject, p.date.isoformat()) == ("billing@electric.example", "Electric Co", "Your statement is ready", "2026-10-09T11:12:00-05:00")
    raw = b"From: =?utf-8?q?Caf=C3=A9?= <a@b.test>\r\nSubject: =?utf-8?q?h=C3=A9llo?=\r\nAuthentication-Results: mx; dkim=pass\r\nAuthentication-Results: other; dkim=fail\r\n\r\nbody"
    p = parse_message(raw)
    assert (p.sender_name, p.subject, p.auth_results) == ("Café", "héllo", "mx; dkim=pass")
    signed = b"From: a@b.test\r\nSubject: s\r\n\r\n" + b"line\n-- \nsig\n" * 3000  # a body's own "--" lines are read
    assert parse_message(signed).text.count("sig") > 2900


# ---- search strings ----------------------------------------------------------------------------------------------------


def test_a_search_matches_percent_and_underscore_as_typed(owner: TestClient):
    """A search string went into LIKE as it was: "%" or "_" matched every row. They match themselves now (the scan is
    a full one either way; at 200,000 rows a search that finds nothing took 0.2 to 0.3 s)."""
    from app.services import contains

    with Session(engine) as session:
        seed(session)
    every = owner.get("/api/v1/transactions/search", params={"text": "", "limit": 500}).json()["returned"]
    assert every > 0
    for wild in ("%", "_", "%_%", "\\"):
        assert owner.get("/api/v1/transactions/search", params={"text": wild, "limit": 500}).json()["returned"] < every, wild
        assert owner.get("/api/v1/transactions/search", params={"account": wild}).json()["returned"] == 0, wild
        assert owner.get("/api/v1/mail/messages", params={"q": wild}).status_code == 200
    assert owner.get("/api/v1/transactions/search", params={"tag": "%"}).json()["returned"] == 0
    assert contains("50%_a\\b") == "%50\\%\\_a\\\\b%" and len(contains("x" * 5000)) == 202
    some = owner.get("/api/v1/transactions/search", params={"limit": 1}).json()["transactions"][0]["merchant"]
    assert owner.get("/api/v1/transactions/search", params={"text": some[:6]}).json()["returned"] >= 1


def test_a_key_cannot_run_a_regular_expression_of_its_own(owner: TestClient):
    """POST /ledger/rules/preview, open to a read key and a connected app, ran a "re:" pattern over a year of rows: a
    twenty-character pattern written to backtrack held the worker 50 s per request, and a slightly longer one would
    not end. A key or an app previews plain words; the owner previews a regular expression in the app."""
    with Session(engine) as session:
        seed(session)
    key = _key()
    started = time.perf_counter()
    r = key.post("/api/v1/ledger/rules/preview", json={"kind": "hide", "pattern": r"re:(.|.){0,40}\x00", "match_field": "all"})
    assert r.status_code == 403 and time.perf_counter() - started < 2.0, r.text
    assert key.post("/api/v1/ledger/rules/preview", json={"kind": "categorise", "pattern": "cafe", "value": "Coffee"}).status_code == 200
    assert owner.post("/api/v1/ledger/rules/preview", json={"kind": "categorise", "pattern": "re:caf[eé]", "value": "Coffee"}).status_code == 200


# ---- the parsers (another change owns them: the exact fix is in each reason) -------------------------------------------


def _child_seconds(code: str, timeout: float = 8.0) -> float:
    """CPU seconds a snippet takes in a child process; the timeout when it does not finish (a regular expression
    cannot be interrupted from inside the process)."""
    prog = f"import sys, time\nsys.path.insert(0, {str(BACKEND)!r})\n{code}\nstarted = time.process_time()\nrun()\nprint(time.process_time() - started)"
    try:
        out = subprocess.run([sys.executable, "-c", prog], check=False, capture_output=True, text=True, timeout=timeout, cwd=BACKEND)
    except subprocess.TimeoutExpired:
        return timeout
    assert out.returncode == 0, out.stderr[-2000:]
    return float(out.stdout.strip().splitlines()[-1])


def test_a_paystub_with_a_blank_run_after_its_hours_label_is_read_quickly():
    """A pay-stub email (any sender, subject "Your pay stub") whose text is "reg hours" and 100 KB of spaces: the two
    blank runs around [:\\s] were split every way, quadratic, about 27 s at 100 KB and 110 s at the mail reader's
    200 KB, inside the mail sync and its writer lock."""
    assert _child_seconds("from app.parsers.paystub import parse_paystub_text\nrun = lambda: parse_paystub_text('net pay 1.00 gross pay 2.00 reg hours' + ' ' * 100_000 + 'x')") < 1.0


def test_a_paystub_of_repeated_layout_words_is_read_quickly():
    """COLUMNS_LAYOUT (earnings statement.*pay date:.*(?:...), DOTALL) retried from every "earnings statement" and ran
    to the end each time: 10 KB of "earnings statement pay date: " took 1.6 s, the mail reader's 200 KB some ten
    minutes, a statement PDF's 2 MB some hours."""
    assert _child_seconds("from app.parsers.paystub import parse_paystub_text\nrun = lambda: parse_paystub_text('earnings statement pay date: ' * 3500)") < 1.0


def test_an_installment_email_of_blank_lines_is_read_quickly():
    """An email whose sender holds "affirm" anywhere (affirm@any.example), a subject naming an installment, the words
    "loan confirmation" and "payment schedule", a date, then 100 KB of empty lines: \\s*\\n\\s* splits the run every
    way, 4.8 s at 100 KB and 19 s at the mail reader's 200 KB, inside the mail sync."""
    assert _child_seconds("from app.parsers.affirm import parse_loan_confirmation\nrun = lambda: parse_loan_confirmation('loan confirmation payment schedule Jan 1, 2026' + '\\n' * 100_000 + 'x')") < 1.0


def test_a_saved_rule_that_backtracks_is_refused_and_a_plain_one_still_matches():
    """A saved `re:` rule runs on every row in every detection; the shape that backtracks without end is refused."""
    from types import SimpleNamespace

    from app.engine.rules import matches

    row = SimpleNamespace(merchant="NORTHWIND POWER AUTOPAY " + "a" * 5000, raw_name="", description="")
    bad = SimpleNamespace(match_field="merchant", pattern="re:(a|aa)+$")
    good = SimpleNamespace(match_field="merchant", pattern="re:northwind\\s+power")
    assert matches(bad, row) is False and matches(good, row) is True
