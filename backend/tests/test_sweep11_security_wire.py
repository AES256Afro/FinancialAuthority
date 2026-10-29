"""Security sweep 11, part one: the IMAP and SMTP wire. A scripted IMAP server and a scripted SMTP server on the
loopback log every command the app sends, and every mail path the app has (the sync, a search, a read, an
attachment, the data check's header fetch, the export link, a receipt confirmed on Needs you, an approved send)
is run against them. What is asserted is the wire itself: read-only folders, PEEK fetches, one sign-in per
connection, a sign-out at the end, nothing typed by anyone outside a quoted or numeric argument, and a hostile
answer (a 50 MB literal, a dripped one, another UID than asked, a thousand folders, BAD on everything) that
neither crashes the job nor holds it past its budget.

Found: the mailbox username went into the LOGIN line as typed (imaplib quotes only the password), so a username
holding a line break could end the sign-in and start a command of its own; a password holding one broke its
quotes the same way; and a server that dripped a literal a byte at a time held a sync, and the writer lock with
it, for as long as it liked, because the socket timeout starts over at every byte.

The servers bind a loopback port; where the sandbox refuses that, the tests are skipped with the reason."""

from __future__ import annotations

import base64
import json
import re
import socket
import socketserver
import threading
import time
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import approvals, crypto
from app.connectors import CONNECTORS
from app.db import engine
from app.mail.imap import ImapClient
from app.models import Approval, ExtractedFact, MailAccount, MessageRef

from test_mail import CSV, MESSAGES, raw

PASSWORD = "app-pass"
USER = "owner@example.test"


def _bindable() -> bool:
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.close()
        return True
    except PermissionError:
        return False


needs_loopback = pytest.mark.skipif(not _bindable(), reason="binding a loopback port is refused here (the sandbox); run outside it")


def receipt_with_pdf() -> bytes:
    m = EmailMessage()
    m["From"] = "Example Hardware <orders@example-hardware.test>"
    m["To"] = USER
    m["Subject"] = "Your receipt from Example Hardware"
    m["Date"] = "Fri, 09 Oct 2026 10:00:00 -0500"
    m["Message-ID"] = "<EX-48213-wire@example.test>"
    m["Authentication-Results"] = "mx.example.test; dkim=pass header.d=example-hardware.test; spf=pass; dmarc=pass"
    m.set_content("Thanks for your order EX-48213.\nOrder total: $84.17\nReturns within 30 days.\nComes with a 2-year limited warranty.\n")
    m.add_attachment(b"%PDF-1.4 receipt", maintype="application", subtype="pdf", filename="receipt.pdf")
    return bytes(m)


WIRE_MESSAGES = {**MESSAGES, 6: receipt_with_pdf()}


# ---- the scripted IMAP server ----------------------------------------------------------------------------------


class ImapScript(socketserver.ThreadingTCPServer):
    """A minimal IMAP4rev1 server over a dict of messages. `mode` makes it hostile: huge (a 50 MB literal), drip
    (a literal a byte at a time), wrong_uid (a FETCH naming another UID), folders (a thousand of them), bad (BAD on
    every command). Every command line received is kept in `log` as (connection, line)."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, messages: dict[int, bytes], mode: str = "normal", password: str = PASSWORD) -> None:
        super().__init__(("127.0.0.1", 0), _ImapHandler)
        self.messages, self.mode, self.password = messages, mode, password
        self.log: list[tuple[int, str]] = []
        self.connections = 0
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    def lines(self) -> list[str]:
        return [line for _, line in self.log]


HUGE = 50 * 1024 * 1024


class _ImapHandler(socketserver.StreamRequestHandler):
    server: ImapScript

    def _w(self, *lines: bytes) -> None:
        self.wfile.write(b"".join(l + b"\r\n" for l in lines))
        self.wfile.flush()

    def handle(self) -> None:  # noqa: C901 - one scripted state machine
        srv = self.server
        with srv.lock:
            srv.connections += 1
            me = srv.connections
        self._w(b"* OK scripted IMAP ready")
        folders = [f"Folder {i}" for i in range(1000)] if srv.mode == "folders" else ["INBOX", "Sent"]
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.rstrip(b"\r\n").decode("utf-8", "replace")
            with srv.lock:
                srv.log.append((me, text))
            parts = text.split(" ", 2)
            if len(parts) < 2:
                self._w(b"* BAD no tag")
                continue
            tag, command = parts[0].encode(), parts[1].upper()
            rest = parts[2] if len(parts) > 2 else ""
            if srv.mode == "bad":
                self._w(tag + b" BAD go away")
                if command == "LOGOUT":
                    return
                continue
            if command == "CAPABILITY":
                self._w(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN", tag + b" OK done")
            elif command == "LOGIN":
                if rest.endswith(f'"{srv.password}"'):
                    self._w(tag + b" OK signed in")
                else:
                    self._w(tag + b" NO [AUTHENTICATIONFAILED] Invalid credentials")
            elif command == "LIST":
                self._w(*[f'* LIST (\\HasNoChildren) "/" "{f}"'.encode() for f in folders], tag + b" OK done")
            elif command in ("EXAMINE", "SELECT"):
                uids = sorted(srv.messages)
                self._w(f"* {len(uids)} EXISTS".encode(), b"* 0 RECENT", b"* OK [UIDVALIDITY 7] ok", f"* OK [UIDNEXT {max(uids, default=0) + 1}] ok".encode(), b"* FLAGS (\\Seen)",
                        tag + (b" OK [READ-ONLY] done" if command == "EXAMINE" else b" OK [READ-WRITE] done"))
            elif command == "UID" and rest.upper().startswith("SEARCH"):
                m = re.search(r"UID (\d+):\*", rest)
                start = int(m.group(1)) if m else 1
                found = [u for u in sorted(srv.messages) if u >= start]
                self._w(("* SEARCH " + " ".join(str(u) for u in found)).encode() if found else b"* SEARCH", tag + b" OK done")
            elif command == "UID" and rest.upper().startswith("FETCH"):
                m = re.match(r"FETCH (\d+) \((BODY\.PEEK\[(HEADER)?\])\)", rest)
                if not m or int(m.group(1)) not in srv.messages:
                    self._w(tag + b" NO no such message")
                    continue
                uid = int(m.group(1))
                body = srv.messages[uid]
                if m.group(3):
                    body = body.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                if srv.mode == "huge":
                    body = b"From: big@example.test\r\nSubject: big\r\n\r\n" + b"a" * HUGE
                if srv.mode == "wrong_uid":
                    uid, body = uid + 1000, srv.messages[min(srv.messages)]
                self.wfile.write(f"* {uid} FETCH (UID {uid} BODY[] {{{len(body)}}}\r\n".encode())
                if srv.mode == "drip":
                    for b in body[:40]:  # a byte every tenth of a second, for as long as the client stays
                        self.wfile.write(bytes([b]))
                        self.wfile.flush()
                        time.sleep(0.1)
                    time.sleep(5)
                    return
                try:
                    self.wfile.write(body)
                    self._w(b")", tag + b" OK done")
                except BrokenPipeError:
                    return  # the client cut the connection (a literal past its ceiling)
            elif command == "LOGOUT":
                self._w(b"* BYE", tag + b" OK bye")
                return
            elif command == "NOOP":
                self._w(tag + b" OK")
            else:
                self._w(tag + b" BAD not scripted")


# ---- the scripted SMTP server ----------------------------------------------------------------------------------


class SmtpScript(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, advertise_starttls: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.advertise_starttls = advertise_starttls
        self.log: list[str] = []
        self.data: list[bytes] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _SmtpHandler(socketserver.StreamRequestHandler):
    server: SmtpScript

    def _w(self, *lines: bytes) -> None:
        self.wfile.write(b"".join(l + b"\r\n" for l in lines))
        self.wfile.flush()

    def handle(self) -> None:
        srv = self.server
        self._w(b"220 scripted ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.rstrip(b"\r\n").decode("utf-8", "replace")
            srv.log.append(text)
            verb = text.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                extra = [b"250-STARTTLS"] if srv.advertise_starttls else []
                self._w(b"250-scripted", b"250-8BITMIME", b"250-SIZE 36700160", *extra, b"250 AUTH PLAIN LOGIN")
            elif verb == "STARTTLS":
                self._w(b"454 TLS not available right now")
            elif verb == "AUTH":
                self._w(b"235 ok")
            elif verb in ("MAIL", "RCPT"):
                self._w(b"250 ok")
            elif verb == "DATA":
                self._w(b"354 go")
                chunks = []
                while True:
                    row = self.rfile.readline()
                    if not row or row == b".\r\n":
                        break
                    chunks.append(row[1:] if row.startswith(b"..") else row)
                srv.data.append(b"".join(chunks))
                self._w(b"250 queued")
            elif verb == "QUIT":
                self._w(b"221 bye")
                return
            else:
                self._w(b"500 what")


# ---- helpers ---------------------------------------------------------------------------------------------------


def add_mailbox(owner: TestClient, server: ImapScript, kind: str, smtp: SmtpScript | None = None, *, starttls: bool = False, username: str = USER, password: str = PASSWORD) -> int:
    body = {"label": kind.title(), "kind": kind, "host": "127.0.0.1", "port": server.port, "username": username, "password": password, "use_ssl": False, "starttls": False, "folders": "INBOX"}
    if smtp is not None:
        body.update({"smtp_host": "127.0.0.1", "smtp_port": smtp.port, "smtp_ssl": False, "smtp_starttls": starttls})
    else:
        body.update({"smtp_host": "", "smtp_port": 0})
    r = owner.post("/api/v1/mail/accounts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# Every line the app may send. A folder name and the sign-in are quoted strings without quotes, backslashes or
# control characters inside; a UID is digits; nothing else is anyone's to type.
ALLOWED = re.compile(
    r'^[A-Z]+\d+ (CAPABILITY|LOGIN "[^"\\\x00-\x1f]*" "[^"\\\x00-\x1f]*"|LIST "" \*|EXAMINE "[^"\\\x00-\x1f]+"|UID SEARCH UID \d+:\*|UID FETCH \d+ \(BODY\.PEEK\[(HEADER)?\]\)|LOGOUT)$'
)
NEVER = re.compile(r"\b(STORE|COPY|MOVE|EXPUNGE|APPEND|DELETE|CREATE|RENAME|SUBSCRIBE|SETACL|SELECT|CLOSE)\b", re.IGNORECASE)


def assert_wire(server: ImapScript) -> None:
    lines = server.lines()
    assert lines, "nothing reached the server"
    for line in lines:
        assert ALLOWED.match(line), f"a command the app should never send: {line!r}"
        assert not NEVER.search(line.split(" ", 1)[1]), line
    assert not any(re.search(r"BODY\[", l) and "PEEK" not in l for l in lines), "a FETCH without PEEK sets \\Seen"
    by_connection: dict[int, list[str]] = {}
    for n, line in server.log:
        by_connection.setdefault(n, []).append(line.split(" ", 1)[1])
    for n, cmds in by_connection.items():
        assert sum(c.startswith("LOGIN") for c in cmds) == 1, f"connection {n} signed in {cmds.count('LOGIN')} times: {cmds}"
        assert cmds[-1] == "LOGOUT", f"connection {n} did not sign out: {cmds[-3:]}"


@pytest.fixture
def imap():
    server = ImapScript(WIRE_MESSAGES)
    yield server
    server.stop()


# ---- IMAP ------------------------------------------------------------------------------------------------------


@needs_loopback
def test_every_mail_path_is_read_only_at_the_wire(owner: TestClient, imap: ImapScript, monkeypatch):
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", ImapClient)
    monkeypatch.setattr(CONNECTORS["mail"], "fetch", lambda url: CSV.encode())
    gmail = add_mailbox(owner, imap, "gmail")
    proton = add_mailbox(owner, imap, "proton")
    for account_id in (gmail, proton):
        r = owner.post(f"/api/v1/mail/accounts/{account_id}/sync")
        assert r.status_code == 200 and r.json()["ok"], r.text
    assert owner.post("/api/v1/mail/accounts/test", json={"id": gmail}).json()["ok"] is True
    # search, read, attachment (the MCP tools call the same functions)
    found = owner.get("/api/v1/mail/messages", params={"q": "receipt"}).json()["messages"]
    assert found and all(m["subject"] == "Your receipt from Example Hardware" for m in found)
    read = owner.get(f"/api/v1/mail/messages/{found[0]['id']}")
    assert read.status_code == 200 and read.json()["attachments"][0]["name"] == "receipt.pdf"
    att = owner.get(f"/api/v1/mail/messages/{found[0]['id']}/attachments/receipt.pdf")
    assert att.status_code == 200 and att.json()["readable"] is True
    # the data check's header fetch: a waiting item whose sender was never judged
    with Session(engine) as session:
        ref = session.exec(select(MessageRef).where(MessageRef.id == found[0]["id"])).one()
        ref.trust = ""
        session.add(ref)
        session.commit()
        from app.engine.datacheck import recheck_senders

        assert recheck_senders(session) >= 1
        session.commit()
    # the export link (an https fetch, stubbed) and a receipt confirmed on Needs you (fetches the attachment)
    facts = owner.get("/api/v1/facts").json()["facts"]
    export = next(f for f in facts if f["kind"] == "export")
    assert owner.post(f"/api/v1/facts/{export['id']}/confirm", json={"apply": True}).status_code == 200
    receipt = next(f for f in facts if f["kind"] == "receipt")
    r = owner.post(f"/api/v1/facts/{receipt['id']}/confirm", json={"apply": True})
    assert r.status_code == 200, r.text
    assert_wire(imap)
    lines = imap.lines()
    assert any(l.endswith("(BODY.PEEK[HEADER])") for l in lines), "the data check read whole messages, not headers"
    assert sum(1 for l in lines if "EXAMINE" in l) >= 6 and not any(" SELECT " in l for l in lines)


@needs_loopback
def test_a_hostile_server_neither_crashes_the_sync_nor_holds_it(owner: TestClient, monkeypatch):
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", ImapClient)
    for mode in ("huge", "wrong_uid", "folders", "bad"):
        server = ImapScript(WIRE_MESSAGES, mode=mode)
        try:
            account_id = add_mailbox(owner, server, "gmail")
            started = time.monotonic()
            r = owner.post(f"/api/v1/mail/accounts/{account_id}/sync")
            took = time.monotonic() - started
            assert r.status_code == 200, (mode, r.text)
            assert took < 60, (mode, took)
            test = owner.post("/api/v1/mail/accounts/test", json={"id": account_id}).json()
            if mode == "folders":
                assert test["ok"] and len(test["folders"]) == 1000
            if mode == "bad":
                assert r.json()["ok"] is False and test["ok"] is False
            assert_wire(server) if mode != "bad" else None
            owner.delete(f"/api/v1/mail/accounts/{account_id}")
        finally:
            server.stop()


@needs_loopback
def test_a_dripped_literal_ends_at_the_budget_not_at_the_servers_leisure():
    """The socket timeout starts over at every byte, so a server that drips a literal held a fetch, the sync and
    the writer lock for as long as it liked. One command now has a budget of its own."""
    from app.mail import imap as imap_module

    server = ImapScript(WIRE_MESSAGES, mode="drip")
    try:
        client = ImapClient("127.0.0.1", server.port, USER, PASSWORD, use_ssl=False, timeout=1)
        client.connect()
        client.select_readonly("INBOX")
        monkeypatch_budget = getattr(imap_module, "COMMAND_SECONDS", None)
        assert monkeypatch_budget is not None, "no per-command budget in the IMAP client"
        imap_module.COMMAND_SECONDS = 2
        try:
            started = time.monotonic()
            with pytest.raises(Exception):
                client.fetch_raw(1)
            assert time.monotonic() - started < 4
        finally:
            imap_module.COMMAND_SECONDS = monkeypatch_budget
            client.close()
    finally:
        server.stop()


@needs_loopback
def test_a_literal_past_the_ceiling_is_refused_before_it_is_read():
    from app.mail import imap as imap_module

    server = ImapScript(WIRE_MESSAGES, mode="huge")
    try:
        client = ImapClient("127.0.0.1", server.port, USER, PASSWORD, use_ssl=False, timeout=5)
        client.connect()
        client.select_readonly("INBOX")
        kept = imap_module.MAX_LITERAL
        imap_module.MAX_LITERAL = 1024 * 1024
        try:
            with pytest.raises(Exception, match="literal"):
                client.fetch_raw(1)
        finally:
            imap_module.MAX_LITERAL = kept
            client.close()
    finally:
        server.stop()


@needs_loopback
def test_the_sign_in_never_carries_a_line_break_or_an_unquoted_name(owner: TestClient, monkeypatch):
    """A username or a password holding a line break could end the LOGIN and start a command of its own on a
    read-only connection (imaplib quotes only the password, and a quoted string cannot hold a line break either)."""
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", ImapClient)
    server = ImapScript(WIRE_MESSAGES)
    try:
        injected = f'{USER}\r\nA9 UID STORE 1 +FLAGS (\\Deleted)\r\nA10 EXPUNGE'
        # at the API: refused, or at the client: never sent
        r = owner.post("/api/v1/mail/accounts", json={"label": "x", "kind": "gmail", "host": "127.0.0.1", "port": server.port, "username": injected, "password": PASSWORD, "use_ssl": False, "starttls": False})
        if r.status_code == 200:
            owner.post(f"/api/v1/mail/accounts/{r.json()['id']}/sync")
        r = owner.post("/api/v1/mail/accounts/test", json={"kind": "gmail", "host": "127.0.0.1", "port": server.port, "username": USER, "password": f"{PASSWORD}\"\r\nA9 UID STORE 1 +FLAGS (\\Deleted)\r\nA10 EXPUNGE", "use_ssl": False, "starttls": False})
        assert r.status_code in (200, 400)
        assert not any(NEVER.search(l) for l in server.lines()), server.lines()
        for line in server.lines():
            assert ALLOWED.match(line), line
        # a username with a space (some providers) still signs in, quoted
        r = owner.post("/api/v1/mail/accounts/test", json={"kind": "imap", "host": "127.0.0.1", "port": server.port, "username": "owner example", "password": PASSWORD, "use_ssl": False, "starttls": False})
        assert r.json()["ok"] is True, r.text
        assert any(l.endswith(f'LOGIN "owner example" "{PASSWORD}"') for l in server.lines()), server.lines()
    finally:
        server.stop()


# ---- SMTP ------------------------------------------------------------------------------------------------------


@needs_loopback
def test_an_approved_send_reaches_the_wire_as_the_app_meant_it(owner: TestClient, imap: ImapScript, monkeypatch):
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", ImapClient)
    monkeypatch.setattr(approvals, "SENDER", approvals.smtp.send_message)
    smtp = SmtpScript()
    try:
        account_id = add_mailbox(owner, imap, "gmail", smtp)
        text_start = "Line one\r\nLine two\n.\n.hidden dot line\nAccents: café, naïve, 日本語, emoji 🙂\n"
        subject = "Receipt for October\r\nBcc: stranger@example.test\r\nX-Injected: yes"
        # a 2 MB draft never reaches the app: the JSON ceiling refuses it at the door
        huge = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "big", "body": text_start + ("x" * 3000 + "\n") * 700, "account_id": account_id})
        assert huge.status_code == 413, huge.status_code
        body = text_start + ("x" * 3000 + "\n") * 300  # about 900 KB, under the ceiling, far over the draft's 20,000 characters
        r = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": subject, "body": body, "reason": "wire test", "account_id": account_id})
        assert r.status_code in (200, 400), r.text
        if r.status_code == 400:
            # a subject with a line break is refused at the door: queue a clean one and check the rest
            r = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "Receipt for October", "body": body, "reason": "wire test", "account_id": account_id})
            assert r.status_code == 200, r.text
        approval_id = r.json()["approval"]["id"] if "approval" in r.json() else r.json()["id"]
        r = owner.post(f"/api/v1/approvals/{approval_id}/approve", json={})
        assert r.status_code == 200, r.text
        with Session(engine) as session:
            row = session.get(Approval, approval_id)
            assert row.status == "sent", row.error
        rcpts = [l for l in smtp.log if l.upper().startswith("RCPT TO")]
        assert len(rcpts) == 1 and "landlord@example.test" in rcpts[0]
        assert len([l for l in smtp.log if l.upper().startswith("MAIL FROM")]) == 1
        assert not any(l.upper().startswith("STARTTLS") for l in smtp.log)
        assert len(smtp.data) == 1
        sent = smtp.data[0]
        head, _, sent_body = sent.partition(b"\r\n\r\n")
        headers = {k.lower(): v for k, v in (h.split(": ", 1) for h in head.decode().split("\r\n") if ": " in h)}
        assert headers["to"] == "landlord@example.test" and headers["from"] == USER
        assert "bcc" not in headers and "cc" not in headers and "x-injected" not in headers, headers
        assert headers["subject"].startswith("Receipt for October")
        from email import message_from_bytes, policy

        parsed = message_from_bytes(sent, policy=policy.default)
        text = parsed.get_content()
        assert "Line one" in text and ".hidden dot line" in text and "café" in text and "日本語" in text and "🙂" in text
        assert "\n.\n" in text.replace("\r\n", "\n")
        assert len(text.replace("\r\n", "\n")) <= 20_000, "the 20,000-character cap on a draft did not hold at the wire"
        assert parsed.get_all("To") == ["landlord@example.test"]
    finally:
        smtp.stop()


@needs_loopback
def test_starttls_is_attempted_where_configured_and_a_refusal_ends_the_send(owner: TestClient, imap: ImapScript, monkeypatch):
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", ImapClient)
    monkeypatch.setattr(approvals, "SENDER", approvals.smtp.send_message)
    smtp = SmtpScript(advertise_starttls=True)
    try:
        account_id = add_mailbox(owner, imap, "proton", smtp, starttls=True)
        r = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "Hello", "body": "A plain note.", "account_id": account_id})
        assert r.status_code == 200, r.text
        approval_id = r.json()["approval"]["id"] if "approval" in r.json() else r.json()["id"]
        owner.post(f"/api/v1/approvals/{approval_id}/approve", json={})
        with Session(engine) as session:
            row = session.get(Approval, approval_id)
            assert row.status == "failed", "the send went on in the clear after STARTTLS was refused"
        assert any(l.upper() == "STARTTLS" for l in smtp.log), smtp.log
        assert not any(l.upper().startswith(("AUTH", "MAIL FROM")) for l in smtp.log), "credentials or mail went over the wire in the clear after STARTTLS was refused"
        assert not smtp.data
    finally:
        smtp.stop()


def test_a_draft_queued_approved_or_denied_is_in_the_activity_log(owner: TestClient, monkeypatch):
    """Every tool call and key read leaves an audit row; a send the owner queued or decided did not, so Settings ›
    Activity showed nothing for it. Now each leaves one, with the approval's id hashed and no text from the draft."""
    monkeypatch.setattr(approvals, "SENDER", lambda *a, **k: "<sent@example.test>")
    with Session(engine) as session:
        session.add(MailAccount(label="Box", kind="imap", host="mail.example.test", port=993, username=USER, password_enc=crypto.encrypt(PASSWORD), smtp_host="mail.example.test", smtp_port=465))
        session.commit()
    queued = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "Rent receipt SECRETWORD", "body": "Body SECRETBODY"}).json()
    denied = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "Second", "body": "Body"}).json()
    assert owner.post(f"/api/v1/approvals/{queued['id']}/approve", json={}).status_code == 200
    assert owner.post(f"/api/v1/approvals/{denied['id']}/deny").status_code == 200
    entries = owner.get("/api/v1/audit").json()["entries"]
    tools = [e["tool"] for e in entries]
    assert tools.count("approval_queued") == 2 and "approval_approved" in tools and "approval_denied" in tools, tools
    for e in entries:
        if e["tool"].startswith("approval_"):
            assert e["client_kind"] == "owner" and e["rows"] == 1
    dump = json.dumps(entries)
    assert "SECRETWORD" not in dump and "SECRETBODY" not in dump and "landlord@example.test" not in dump
    # a subject with a line break is refused when queued, not discovered when it fails to send
    r = owner.post("/api/v1/approvals", json={"to": "landlord@example.test", "subject": "One\r\nBcc: x@example.test", "body": "Body"})
    assert r.status_code == 400 and "one line" in r.text
