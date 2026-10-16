"""A small IMAP client and a message parser. Folders are opened read-only; nothing is ever marked, moved or deleted.

The reader shape comes from the owner's Proton Mail MCP server. Gmail takes an app password on imap.gmail.com;
Proton takes the Bridge's local password on the Bridge's host and port.
"""

from __future__ import annotations

import email
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

BATCH = 150


@dataclass
class Attachment:
    name: str
    content_type: str
    size: int
    data: bytes | None = None


@dataclass
class Parsed:
    message_id: str
    sender: str
    sender_name: str
    subject: str
    date: datetime | None
    text: str
    html: str
    attachments: list[Attachment] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "head", "title"}
    BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BREAK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, list[str]]:
    parser = _HtmlText()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001  (bad markup is still data)
        pass
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text, parser.links


def _part_text(part) -> str:  # noqa: ANN001 - an email.message part
    """A text part's content; a part in a charset Python does not know is read as UTF-8, never refused."""
    try:
        return part.get_content()
    except (LookupError, UnicodeError, ValueError):
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


MAX_TEXT = 200_000  # characters of a message's text or html that are read; a bill or an alert is far shorter


def parse_message(raw: bytes) -> Parsed:
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    sender_pairs = getaddresses([msg.get("From", "")])
    sender_name, sender = (sender_pairs[0] if sender_pairs else ("", ""))
    try:
        date = parsedate_to_datetime(msg.get("Date", "")) if msg.get("Date") else None
        if date and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        date = None
    text, html, links = "", "", []
    attachments: list[Attachment] = []
    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition == "attachment" or (part.get_filename() and part.get_content_maintype() != "text"):
            payload = part.get_payload(decode=True) or b""
            attachments.append(Attachment(name=part.get_filename() or "attachment", content_type=part.get_content_type(), size=len(payload), data=payload if len(payload) <= 15_000_000 else None))
            continue
        if part.get_content_type() == "text/plain" and not text:
            text = _part_text(part)
        elif part.get_content_type() == "text/html" and not html:
            html = _part_text(part)
    text, html = (text or "")[:MAX_TEXT], (html or "")[:MAX_TEXT]
    if html:
        html_text, links = html_to_text(html)
        if not text or len(text) < 40:
            text = html_text[:MAX_TEXT]
    return Parsed(message_id=(msg.get("Message-ID", "") or "")[:500], sender=sender.lower()[:320], sender_name=sender_name[:200], subject=(msg.get("Subject", "") or "")[:1000], date=date, text=text, html=html, attachments=attachments, links=links[:200])


# The Bridge sidecar forwards the Bridge's loopback ports to the standard ones, so from the app's container
# it answers on 143 (IMAP) and 25 (SMTP). The Bridge's own info screen still prints 1143 and 1025, and that is
# what an owner types; both mean the sidecar.
SIDECAR_PORTS = {1143: 143, 1025: 25}


def sidecar_port(host: str, port: int) -> int:
    return SIDECAR_PORTS.get(port, port) if host == "bridge" else port


class ImapClient:
    """imaplib with the few calls the connector needs, every folder opened read-only."""

    def __init__(self, host: str, port: int, username: str, password: str, use_ssl: bool = True, starttls: bool = False, timeout: int = 30) -> None:
        port = sidecar_port(host, port)
        self.host, self.port, self.username, self.password, self.use_ssl, self.starttls, self.timeout = host, port, username, password, use_ssl, starttls, timeout
        self.conn: imaplib.IMAP4 | None = None

    LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "bridge", "host.docker.internal")

    def _context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if self.host in self.LOCAL_HOSTS:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # the Bridge uses a self-signed certificate on the loopback
        return context

    def connect(self) -> None:
        if self.use_ssl:
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self._context(), timeout=self.timeout)
        else:
            self.conn = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
            if self.starttls:
                self.conn.starttls(self._context())
        self.conn.login(self.username, self.password)

    def folders(self) -> list[str]:
        assert self.conn
        status, rows = self.conn.list()
        out = []
        for row in rows or []:
            if not row:
                continue
            line = row.decode("utf-8", "replace") if isinstance(row, bytes) else str(row)
            match = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', line)
            if match:
                out.append((match.group(1) or match.group(2)).strip('"'))
        return out

    def select_readonly(self, folder: str) -> tuple[int, int]:
        assert self.conn
        status, data = self.conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise RuntimeError(f"cannot open {folder}")
        exists = int(data[0] or 0) if data and data[0] else 0
        uidnext = 0
        ok, resp = self.conn.response("UIDNEXT")
        if resp and resp[0]:
            try:
                uidnext = int(resp[0])
            except (TypeError, ValueError):
                uidnext = 0
        return exists, uidnext

    def new_uids(self, since_uid: int) -> list[int]:
        """Every message after `since_uid`, oldest first; the caller picks the batch it reads."""
        assert self.conn
        status, data = self.conn.uid("search", None, f"UID {since_uid + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []
        return [u for u in (int(x) for x in data[0].split()) if u > since_uid]

    def uidvalidity(self) -> int:
        """The folder's UIDVALIDITY after select: when it changes, its message numbers start again."""
        assert self.conn
        ok, resp = self.conn.response("UIDVALIDITY")
        try:
            return int(resp[0]) if resp and resp[0] else 0
        except (TypeError, ValueError):
            return 0

    def fetch_raw(self, uid: int) -> bytes:
        assert self.conn
        status, data = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not data:
            raise RuntimeError(f"cannot fetch uid {uid}")
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                return item[1]
        raise RuntimeError(f"no body for uid {uid}")

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None
