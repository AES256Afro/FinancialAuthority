"""Security sweep 13, part one: data at rest. A household with every kind of secret the app can hold is seeded
through the app, then the SQLite file, its journal, the files under the data folder, a restore point, the off-box
copy and every export are opened directly, outside the app, and read for each secret in the clear; then the erase
route runs and the same places are read again for anything at all.

No real data: every value here is made up for the test."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto, offbox, restore
from app.config import settings
from app.connectors import CONNECTORS
from app.db import engine
from app.engine.household import seed
from app.main import app
from app.models import Account, BankLink, Document, MessageRef, Passkey, Person, Transaction
from app.store import get_setting

H = {"X-Requested-With": "FinancialAuthority"}
PW = "correct horse battery"
REDIRECT = "http://localhost:9999/callback"

# Every secret the test puts in, by name. None is a real value.
MAILBOX_PASSWORD = "mailbox-pass-7731x-ZQ"
PLAID_SECRET = "plaid-secret-9a8b-ZQ"
PLAID_TOKEN = "access-sandbox-plaidtok55aa-ZQ"
TELLER_TOKEN = "teller-token-77bb-ZQ"
TELLER_CERT = "-----BEGIN CERTIFICATE-----\nCERTBODY-abc123-ZQ\n-----END CERTIFICATE-----"
TELLER_KEY = "-----BEGIN PRIVATE KEY-----\nKEYBODY-xyz789-ZQ\n-----END PRIVATE KEY-----"
ANTHROPIC_KEY = "sk-ant-seeded-key-4455-ZQ"
PUSH_TOKEN = "tk_push_secret_6677-ZQ"
WEBPUSH_AUTH = "webpush-auth-secret-99-ZQ"
WEBPUSH_ENDPOINT = "https://push.example.test/send/endpoint-id-ZQ-0001"
PARTNER_PASSWORD = "plum river lantern seven ZQ"
OFFBOX_PASSPHRASE = "orchard-lantern-river-stone-9-ZQ"
LEASE_MARKER = "LEASE-CLAUSE-MARKER-7731-ZQ"
RECEIPT_MARKER = b"RECEIPT-PHOTO-BYTES-MARKER-ZQ"
MAIL_EARLY = "EARLY-LINE-QQ17-ZQ"  # within the first 200 characters: the snippet the design keeps
MAIL_DEEP = "BODY-DEEP-MARKER-ZX81-ZQ"  # past the snippet: never kept
MAIL_SENDER = "billing@example-utility.test"
MANUAL_LAST4 = "4321"
MANUAL_NUMBER = "998877664321"  # typed in full; only the last four may stay


class ScriptedMailbox:
    """The IMAP client the mail connector asks for, answering from a dict of raw messages."""

    messages: dict[int, bytes] = {}  # noqa: RUF012 - set by the test

    def __init__(self, host, port, username, password, use_ssl=True, starttls=False):  # noqa: ANN001
        self.folder = None

    def connect(self) -> None:
        pass

    def folders(self) -> list[str]:
        return ["INBOX"]

    def select_readonly(self, folder: str) -> tuple[int, int]:
        self.folder = folder
        return (len(self.messages), max(self.messages, default=0) + 1)

    def new_uids(self, since_uid: int) -> list[int]:
        return [u for u in sorted(self.messages) if u > since_uid]

    def fetch_raw(self, uid: int) -> bytes:
        return self.messages[uid]

    def close(self) -> None:
        pass


def _mail(subject: str, text: str, n: int) -> bytes:
    m = EmailMessage()
    m["From"] = f"Example Utility <{MAIL_SENDER}>"
    m["To"] = "owner@example.test"
    m["Subject"] = subject
    m["Date"] = datetime(2026, 10, 1 + n, 8, 0, tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = f"<msg-{n}-ZQ@example.test>"
    m.set_content(text)
    return bytes(m)


def _pdf(lines: list[str]) -> bytes:
    from fpdf import FPDF

    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", size=10)
    for line in lines:
        doc.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


def _pkce() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def seed_everything(owner: TestClient, tmp_path: Path) -> dict[str, str]:
    """A household with one of everything secret, put in the way the app puts it in. Returns the secrets by name,
    with the ones the app minted (tokens, codes, the session id) added."""
    secrets: dict[str, str] = {
        "mailbox password": MAILBOX_PASSWORD, "Plaid secret": PLAID_SECRET, "Plaid access token": PLAID_TOKEN, "Teller access token": TELLER_TOKEN,
        "Teller certificate": "CERTBODY-abc123-ZQ", "Teller private key": "KEYBODY-xyz789-ZQ", "Anthropic key": ANTHROPIC_KEY, "push token": PUSH_TOKEN,
        "web push auth secret": WEBPUSH_AUTH, "web push endpoint": WEBPUSH_ENDPOINT, "second person's password": PARTNER_PASSWORD, "off-box passphrase": OFFBOX_PASSPHRASE,
        "document text": LEASE_MARKER, "mail body past the snippet": MAIL_DEEP, "full account number": MANUAL_NUMBER,
        "FA_PASSWORD": PW, "FA_SESSION_SECRET": settings.session_secret, "FA_DATA_KEY": settings.data_key,
    }
    with Session(engine) as session:
        seed(session)
        session.add(BankLink(provider="plaid", external_id="item-ZQ", institution="Example Bank", environment="sandbox", access_token_enc=crypto.encrypt(PLAID_TOKEN), cursor="c1"))
        session.add(BankLink(provider="teller", external_id="enr-ZQ", institution="Example Credit Union", environment="sandbox", access_token_enc=crypto.encrypt(TELLER_TOKEN)))
        session.add(Passkey(credential_id="cred-ZQ-0001", public_key="pk-ZQ-0001", name="Phone", rp_id="localhost"))
        session.commit()
    assert owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid-ZQ", "secret": PLAID_SECRET, "environment": "sandbox"}).status_code == 200
    assert owner.put("/api/v1/banks/teller/settings", json={"application_id": "app_ZQ", "certificate": TELLER_CERT, "private_key": TELLER_KEY}).status_code == 200
    assert owner.put("/api/v1/models/secrets", json={"name": "anthropic", "value": ANTHROPIC_KEY}).status_code == 200
    assert owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "money", "token": PUSH_TOKEN, "brief_hour": 7}).status_code == 200
    r = owner.post("/api/v1/push/devices", json={"endpoint": WEBPUSH_ENDPOINT, "keys": {"p256dh": "p256dh-ZQ", "auth": WEBPUSH_AUTH}, "label": "Phone"})
    assert r.status_code == 200, r.text
    r = owner.post("/api/v1/mail/accounts", json={"label": "Proton", "kind": "proton", "host": "bridge", "port": 1143, "username": "owner@example.test", "password": MAILBOX_PASSWORD,
                                                 "use_ssl": False, "starttls": True, "folders": "INBOX"})
    assert r.status_code == 200, r.text
    r = owner.post("/api/v1/accounts", json={"name": "Example Manual Card", "kind": "card", "institution": "Example Lender", "last4": MANUAL_NUMBER, "balance": 321.0})
    assert r.status_code == 200, r.text
    key = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read"], "redaction": "full"}).json()["token"]
    secrets["API key"] = key
    # A second person who joined, and one whose invitation is still open.
    invited = owner.post("/api/v1/people", json={"username": "sam", "display_name": "Sam Example", "role": "everything"})
    assert invited.status_code == 200, invited.text
    invite = invited.json()["invite"]["link"].split("#", 1)[1]
    partner = TestClient(app)
    partner.headers.update(H)
    assert partner.post("/api/auth/join", json={"token": invite, "password": PARTNER_PASSWORD}).status_code == 200
    secrets["used invitation"] = invite
    pending = owner.post("/api/v1/people", json={"username": "kit", "display_name": "Kit Example", "role": "shared"}).json()["invite"]["link"].split("#", 1)[1]
    secrets["open invitation"] = pending
    # A connected app with an access and a refresh token.
    anon = TestClient(app)
    reg = anon.post("/register", json={"client_name": "Claude on the phone", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                       "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "read confirm"})
    assert reg.status_code == 201, reg.text
    info = reg.json()
    verifier, challenge = _pkce()
    r = anon.get("/authorize", params={"response_type": "code", "client_id": info["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read confirm", "state": "s-ZQ"}, follow_redirects=False)
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    assert anon.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": PW}).status_code in (200, 303)
    allowed = anon.post("/oauth/consent", data={"req": req, "decision": "allow", "scope": ["read", "confirm"]}, follow_redirects=False)
    code = parse_qs(urlparse(allowed.headers["location"]).query)["code"][0]
    tokens = anon.post("/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": info["client_id"], "redirect_uri": REDIRECT}).json()
    secrets["OAuth access token"] = tokens["access_token"]
    secrets["OAuth refresh token"] = tokens["refresh_token"]
    secrets["OAuth authorization code"] = code
    # Documents: a lease as a PDF, a receipt as a photo.
    lease = _pdf(["RESIDENTIAL LEASE", f"Clause 7: {LEASE_MARKER}", "Rent: $1,650.00 due on the 1st", "Late fee: $75.00 after the 5th"])
    r = owner.post("/api/v1/documents", files={"file": ("lease.pdf", lease, "application/pdf")}, data={"title": "Lease", "kind": "lease"})
    assert r.status_code == 200, r.text
    r = owner.post("/api/v1/documents", files={"file": ("shoes.png", b"\x89PNG\r\n\x1a\n" + RECEIPT_MARKER, "image/png")}, data={"kind": "receipt", "merchant": "Example Shoes", "amount": "89.00", "bought_on": "2026-10-01"})
    assert r.status_code == 200, r.text
    # Mail: three messages with bodies, read through the connector.
    filler = "The usual lines of a utility's notice follow here, which the app reads for a bill. " * 4
    ScriptedMailbox.messages = {n: _mail(f"Your Example Utility bill is ready ({n})", f"Hello Owner. {MAIL_EARLY}. Your bill of $84.{n}0 is due October 2{n}.\n\n{filler}\n{MAIL_DEEP} account ending 7788.\n", n) for n in (1, 2, 3)}
    CONNECTORS["mail"].client_factory = ScriptedMailbox
    try:
        from app.jobs.scheduler import jobs

        jobs.run("mail_sync")
    finally:
        from app.mail.imap import ImapClient

        CONNECTORS["mail"].client_factory = ImapClient
    with Session(engine) as session:
        assert session.exec(select(MessageRef)).first() is not None, "the mail sync kept no message"
    # The owner's second step, with its recovery codes.
    started = owner.post("/api/auth/totp/start", headers=H).json()
    secrets["TOTP secret"] = started["secret"]
    confirmed = owner.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(started["secret"]).now()}, headers=H).json()
    for n, code in enumerate(confirmed["recovery_codes"]):
        secrets[f"recovery code {n}"] = code
    # The calendar feed's token, and the off-box copy's passphrase.
    made = owner.post("/api/v1/calendar/feed", json={"amounts": True}).json()
    secrets["calendar token"] = made["url"].rsplit("/", 1)[1].split(".")[0]
    folder = tmp_path / "offbox"
    folder.mkdir(exist_ok=True)
    assert owner.put("/api/v1/restore-points/offbox", json={"folder": str(folder), "passphrase": OFFBOX_PASSPHRASE, "every_days": 1}).status_code == 200
    secrets["session id (cookie)"] = owner.cookies.get("fa_session") or next(iter(owner.cookies.values()))
    return secrets


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _found(blob: bytes, secrets: dict[str, str]) -> list[str]:
    return [name for name, value in secrets.items() if value and value.encode() in blob]


def _dump(path: Path) -> str:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def _data_files() -> list[Path]:
    return [p for p in Path(settings.data_dir).rglob("*") if p.is_file()]



MONEY_AND_NAMES = {
    "an account name": "Everyday Checking", "another account name": "Rewards Card", "the manual card": "Example Manual Card", "an institution": "Example Credit Union",
    "a merchant": "Example Grocery", "another merchant": "Example Taqueria", "the employer": "Example Employer", "a mail subject": "Example Utility bill is ready",
    "a mail sender": MAIL_SENDER, "the mail snippet": MAIL_EARLY, "a mail amount": "$84.10", "the net pay": "2240", "the lease text": "RESIDENTIAL LEASE",
}


def _journal_files(db: Path) -> list[Path]:
    return [db.with_name(db.name + suffix) for suffix in ("-wal", "-shm", "-journal")]


def test_the_database_its_journal_and_the_data_folder_hold_no_secret_in_the_clear(owner: TestClient, tmp_path: Path):
    secrets = seed_everything(owner, tmp_path)
    db = settings.db_path
    dump = _dump(db).encode()
    assert _found(dump, secrets) == [], "in the SQL dump"
    assert _found(_read(db), secrets) == [], "in the database file's bytes"
    for journal in _journal_files(db):
        assert _found(_read(journal), secrets) == [], f"in {journal.name}"
    for path in _data_files():
        assert _found(_read(path), secrets) == [], f"in {path.relative_to(settings.data_dir)}"
    assert b"fa1:" in dump, "the encrypted fields are there, sealed"
    with Session(engine) as session:
        people = {p.username: p for p in session.exec(select(Person)).all()}
        assert people["sam"].password_hash.startswith("scrypt$") and people["kit"].password_hash == ""
        assert len(people["kit"].invite_hash) == 64 and people["kit"].invite_hash != secrets["open invitation"]
        totp = get_setting(session, "auth.totp")
        assert crypto.is_encrypted(totp["secret_enc"]) and all(c.startswith("scrypt$") for c in totp["recovery"])
        manual = session.exec(select(Account).where(Account.name == "Example Manual Card")).one()
        assert MANUAL_NUMBER not in manual.external_id and manual.external_id.endswith(MANUAL_LAST4) and crypto.is_encrypted(manual.last4_enc)
        for message in session.exec(select(MessageRef)).all():
            assert MAIL_DEEP not in message.snippet and len(message.snippet) <= 201  # 200 characters and an ellipsis
        for doc in session.exec(select(Document)).all():
            assert crypto.is_encrypted(doc.pages_enc)
        for link in session.exec(select(BankLink)).all():
            assert crypto.is_encrypted(link.access_token_enc)


def test_a_restore_point_and_the_off_box_copy_keep_every_secret_sealed(owner: TestClient, tmp_path: Path):
    secrets = seed_everything(owner, tmp_path)
    point = restore.make_point("manual", "sweep 13")
    with Session(engine) as session:
        made = offbox.copy_now(session)
    copy = restore.folder() / point["name"]
    assert _found(_read(copy), secrets) == [] and _found(_dump(copy).encode(), secrets) == []
    meta = json.loads(copy.with_suffix(".json").read_text())
    assert set(meta) <= {"name", "kind", "reason", "created_at", "bytes", "verified", "tables", "transactions", "documents"}
    sealed = _read(tmp_path / "offbox" / made["file"])
    assert sealed.startswith(b"FABK2") and b"SQLite format 3" not in sealed and b"fa1:" not in sealed and b"PK\x03\x04" not in sealed
    assert _found(sealed, secrets) == [] and _found(sealed, MONEY_AND_NAMES) == []
    assert not list(restore.folder().glob("**/copy.zip")), "the plain zip the copy is sealed from is gone"


def test_every_export_carries_no_secret_and_no_encrypted_blob(owner: TestClient, tmp_path: Path):
    from app.exports.data import FORMATS

    secrets = seed_everything(owner, tmp_path)
    assert len(FORMATS) == 12
    for fmt in FORMATS:
        r = owner.get("/api/v1/exports/file", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.text[:200])
        blob = r.content
        if fmt in ("csv", "xlsx", "docx"):
            import zipfile

            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                blob = b"".join(z.read(n) for n in z.namelist())
        elif fmt == "pdf":
            from pypdf import PdfReader

            blob = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(blob)).pages).encode()
        assert _found(blob, secrets) == [], fmt
        assert b"fa1:" not in blob and b"_enc" not in blob, fmt
        assert MANUAL_NUMBER.encode() not in blob, fmt


def test_erase_leaves_nothing_in_the_file_the_journal_or_the_folders(owner: TestClient, tmp_path: Path):
    secrets = seed_everything(owner, tmp_path)
    restore.make_point("manual", "before")
    with Session(engine) as session:
        offbox.copy_now(session)
    kept_by_design = {"second person's password", "FA_PASSWORD", "FA_SESSION_SECRET", "FA_DATA_KEY", "session id (cookie)", "TOTP secret", *(f"recovery code {n}" for n in range(10))}
    code = pyotp.TOTP(secrets["TOTP secret"]).at(int(time.time()) + 30)
    done = owner.post("/api/v1/privacy/erase", json={"phrase": "delete everything", "password": PW, "code": code})
    assert done.status_code == 200, done.text
    db = settings.db_path
    wanted = {k: v for k, v in secrets.items() if k not in kept_by_design}
    everything = {**wanted, **MONEY_AND_NAMES}
    assert _found(_read(db), everything) == [], "in the database file after the erase"
    for journal in _journal_files(db):
        assert _found(_read(journal), everything) == [], f"in {journal.name} after the erase"
    assert _found(_dump(db).encode(), everything) == []
    assert [p.name for p in restore.folder().glob("*.db")] == [], "restore points are gone"
    assert [p.name for p in (Path(settings.data_dir) / "documents").glob("*")] == [], "document files are gone"
    for path in _data_files():
        assert _found(_read(path), everything) == [], f"in {path.relative_to(settings.data_dir)} after the erase"
    # The off-box copies on their own disk stay (the answer says so); they were sealed, so they hold nothing readable.
    for copy in (tmp_path / "offbox").glob("*"):
        assert _found(_read(copy), everything) == [] and b"SQLite format 3" not in _read(copy)
    assert any("Off-box copies" in line for line in done.json()["left"])
    with Session(engine) as session:
        assert session.exec(select(Transaction)).first() is None and session.exec(select(Account)).first() is None
        assert {p.username for p in session.exec(select(Person)).all()} == {"sam", "kit"}  # who may get in stays, as the route says


def test_the_database_and_the_off_box_copy_are_readable_by_the_apps_own_user_alone(owner: TestClient, tmp_path: Path):
    """SQLite makes the database and its journal world-readable by default (0644), and the journal copies the
    file's mode; the start sets the file to the app's user alone, and the sealed off-box copy is written so too.
    (Restore points and the kept document files are made by restore.py and engine/documents.py.)"""
    from app.main import _owner_only

    seed_everything(owner, tmp_path)
    db = settings.db_path
    db.chmod(0o644)
    _owner_only(db)
    for path in (db, *_journal_files(db)):
        if path.exists():
            assert path.stat().st_mode & 0o777 == 0o600, path.name
    with Session(engine) as session:
        made = offbox.copy_now(session)
    assert (tmp_path / "offbox" / made["file"]).stat().st_mode & 0o777 == 0o600
