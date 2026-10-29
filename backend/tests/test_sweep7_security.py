"""Security sweep 7: the grep-driven pass over the request layer (auth, OAuth, uploads, exports, outbound calls).

Each test pins a property the sweep checked: a send a second person's key queues from any route is that person's;
a session ends when its row goes or the signing secret changes, and sign-in always mints a new one; a bill name
cannot break out of its calendar line; a report stylesheet cannot fetch; an SVG mark cannot carry a script."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, auth, notify
from app.db import engine
from app.main import app
from app.models import Approval, MailAccount, WebSession

H = {"X-Requested-With": "FinancialAuthority"}
GOOD = "a long enough passphrase"


def _join_as(owner: TestClient, username: str, role: str) -> TestClient:
    made = owner.post("/api/v1/people", json={"username": username, "display_name": "Alex Example", "role": role})
    assert made.status_code == 200, made.text
    token = made.json()["invite"]["link"].split("#", 1)[1]
    person = TestClient(app)
    person.headers.update(H)
    assert person.post("/api/auth/join", json={"token": token, "password": GOOD}).status_code == 200
    return person


def test_a_persons_key_queues_the_notice_letter_as_their_own_send(owner: TestClient, monkeypatch):
    """The moving-out letter is queued like any other send: a key a second person made carries that person, so the
    draft waits for them, never lands on the owner's Needs you as the owner's own, and the owner's allowance
    cannot send it."""
    from app.api import moving

    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(approvals, "SENDER", lambda account, password, **kw: sent.append(kw["to"]) or "<id@example.test>")
    monkeypatch.setattr(moving, "moving_plan", lambda session, today: {
        "move": {"from": "Example Flats"}, "steps": [{"id": "notice", "date": "2026-11-01"}], "letter": "Dear office, this letter is my written notice.", "counts": {}, "next": None,
    })
    with Session(engine) as session:
        session.add(MailAccount(label="Example", kind="gmail", host="imap.example.test", username="owner@example.test", smtp_host="smtp.example.test"))
        session.commit()
        approvals.allow(session, "office@example-flats.test")  # the owner's own standing allowance
    person = _join_as(owner, "alex", "everything")
    pid = person.get("/api/auth/me").json()["person_id"]
    token = person.post("/api/v1/keys", json={"name": "theirs", "scopes": ["read", "confirm"]}).json()["token"]
    reader = TestClient(app)
    queued = reader.post("/api/v1/move/plan/letter", headers={"Authorization": f"Bearer {token}"}, json={"to": "office@example-flats.test", "body": "Dear office, this letter is my written notice."})
    assert queued.status_code == 200, queued.text
    assert queued.json()["approval"]["for_person_id"] == pid
    assert queued.json()["approval"]["status"] == "pending" and sent == []
    with Session(engine) as session:
        row = session.get(Approval, queued.json()["approval"]["id"])
        assert row.for_person_id == pid and row.status == "pending"
    # The owner cannot approve the person's send; it is theirs alone.
    refused = owner.post(f"/api/v1/approvals/{row.id}/approve", json={})
    assert refused.status_code == 403, refused.text


def test_a_session_ends_when_its_row_is_gone_or_the_secret_changes(owner: TestClient, monkeypatch):
    """The cookie names a row on the server: delete the row and the cookie is nothing; change FA_SESSION_SECRET and
    every cookie signed before is nothing."""
    assert owner.get("/api/auth/me").status_code == 200
    from app.config import settings

    monkeypatch.setattr(settings, "session_secret", "a different secret after a rotation")
    assert owner.get("/api/auth/me").status_code == 401
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    assert owner.get("/api/auth/me").status_code == 200
    with Session(engine) as session:
        for row in session.exec(select(WebSession)).all():
            session.delete(row)
        session.commit()
    assert owner.get("/api/auth/me").status_code == 401


def test_sign_in_always_mints_a_new_session(client: TestClient):
    """A browser that held a session before signing in (or a cookie someone planted) never keeps it: each sign-in
    records a new row and sets a new cookie, so a session id fixed before sign-in is worth nothing after."""
    first = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert first.status_code == 200
    cookie_before = client.cookies.get(auth.COOKIE)
    second = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert second.status_code == 200
    cookie_after = client.cookies.get(auth.COOKIE)
    assert cookie_before and cookie_after and cookie_before != cookie_after
    with Session(engine) as session:
        rows = session.exec(select(WebSession)).all()
        assert len(rows) == 2 and all(r.revoked_at is None for r in rows)


def test_a_bill_name_cannot_break_out_of_its_calendar_line():
    """A name with a line break, a semicolon or a comma stays inside its SUMMARY: it can never start a property of
    its own in the feed a phone subscribes to."""
    from app.exports.data import _fold, _ics_text

    hostile = "Rent\r\nATTENDEE;ROLE=CHAIR:mailto:x@example.test\nEND:VEVENT, again; -dash"
    text = _ics_text(hostile)
    assert "\r" not in text and "\n" not in text
    assert text.count("\\;") == 2 and text.count("\\,") == 1
    folded = _fold(f"SUMMARY:{text}")
    for line in folded.split("\r\n")[1:]:
        assert line.startswith(" ")  # every continuation line is a fold, never a new property
    assert not any(line.startswith(("ATTENDEE", "END:")) for line in folded.split("\r\n"))


def test_a_report_stylesheet_cannot_fetch_or_run():
    """The owner's extra CSS styles the page and nothing else: no import, no url, no expression, no binding, no
    tag, and no backslash escape that could spell one of those."""
    from app.exports.render import clean_css

    for hostile in ("@import url(https://evil.example/x.css);", "body{background:url(https://evil.example/p.png)}", "div{width:expression(alert(1))}",
                    "a{-moz-binding:url(x)}", "b{behavior:url(x.htc)}", "</style><link rel=stylesheet href=https://evil.example/x.css>",
                    "p{background:u\\72l(https://evil.example)}", "p{background:image-set('https://evil.example/a.png' 1x)}", "p{content:image(x)}"):
        assert clean_css(hostile) == "", hostile
    assert clean_css("body{color:#123456;font-size:14px} h1{margin:0}") != ""


def test_an_svg_mark_cannot_carry_a_script_or_an_outside_link():
    """The appearance mark is an image the app shows: a plain drawing, never a script, a handler, an embedded
    document or a link out."""
    import base64

    from fastapi import HTTPException
    import pytest

    from app.api.appearance import _check_mark

    def mark(svg: str) -> str:
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()

    for hostile in ('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                    '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body onload="x()"/></foreignObject></svg>',
                    '<svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="https://evil.example/t.png"/></svg>',
                    '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><text>x</text></a></svg>',
                    '<svg xmlns="http://www.w3.org/2000/svg"><use href="data:image/svg+xml;base64,PHN2Zz4="/></svg>',
                    '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>'):
        with pytest.raises(HTTPException):
            _check_mark(mark(hostile))
    assert _check_mark(mark('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#123456"/><use href="#a"/></svg>'))


def test_the_mcp_mail_read_refuses_a_folder_name_that_could_end_the_command():
    """The IMAP client itself, not only the REST route, refuses a folder name that could end the EXAMINE line."""
    import pytest

    from app.mail.imap import ImapClient

    client = ImapClient("imap.example.test", 993, "u", "p", True, False)
    client.conn = object()  # never reached
    for bad in ('INBOX" EXPUNGE', "INBOX\r\nSTORE 1 +FLAGS \\Deleted", "INBOX\\x", ""):
        with pytest.raises(ValueError):
            client.select_readonly(bad)


def test_the_invoice_page_shows_a_name_and_never_runs_it(owner: TestClient):
    """The printable invoice escapes every value and carries a policy that allows no script at all."""
    made = owner.post("/api/v1/business", json={"name": "Example Studio <img src=x onerror=alert(1)>", "kind": "sole_prop"})
    assert made.status_code == 200, made.text
    biz = made.json()["businesses"][0]["id"]
    inv = owner.post(f"/api/v1/business/{biz}/invoices", json={"counterparty": "<script>alert(1)</script>", "issued_on": date(2026, 10, 1).isoformat(), "lines": [{"what": "</td><script>x</script>", "quantity": 1, "price": 10}],
                                                           "status": "sent", "direction": "out"})
    assert inv.status_code == 200, inv.text
    page = owner.get(f"/api/v1/business/invoices/{inv.json()['id']}/print")
    assert page.status_code == 200
    assert "<script>" not in page.text and "<img" not in page.text and "</td><script" not in page.text
    assert "&lt;script&gt;" in page.text and "&lt;img src=x" in page.text
    # The route's own policy survives the security middleware (which otherwise sets none under /api/), once, not doubled.
    policies = page.headers.get_list("content-security-policy")
    assert len(policies) == 1 and policies[0].startswith("default-src 'none'")
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["x-frame-options"] == "DENY"
