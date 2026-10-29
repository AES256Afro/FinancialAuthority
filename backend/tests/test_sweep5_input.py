"""Security sweep five, input handling: a mailbox folder name can never carry an IMAP command, an email with a
pile of PDFs is read under a cap, a CSV with an oversized field is refused rather than crashed on, and the
webhook's rate table never grows without end."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crypto
from app.connectors import CONNECTORS
from app.connectors.mail import MailConnector
from app.db import engine
from app.mail.imap import Attachment, Parsed
from app.models import MailAccount, MessageRef
from tests.conftest import TODAY

# A folder name that ends the EXAMINE line and starts commands of its own: imaplib sends arguments as given.
INJECTION = 'INBOX"\r\nA1 UID STORE 1:* +FLAGS (\\Deleted)\r\nA2 EXPUNGE'


class Recorder:
    """An IMAP client that writes down every folder it is asked to open and holds no messages."""

    opened: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def connect(self) -> None:
        pass

    def folders(self) -> list[str]:
        return ["INBOX"]

    def select_readonly(self, folder: str) -> tuple[int, int]:
        Recorder.opened.append(folder)
        return (0, 1)

    def new_uids(self, since_uid: int) -> list[int]:
        return []

    def fetch_raw(self, uid: int) -> bytes:
        raise AssertionError("nothing to fetch")

    def close(self) -> None:
        pass


def test_a_folder_name_cannot_carry_an_imap_command(owner: TestClient):
    body = {"kind": "imap", "host": "mail.example", "username": "u", "password": "p"}
    assert owner.post("/api/v1/mail/accounts", json={**body, "folders": INJECTION}).status_code == 400
    assert owner.post("/api/v1/mail/accounts", json={**body, "folders": "INBOX\x00"}).status_code == 400
    made = owner.post("/api/v1/mail/accounts", json={**body, "folders": "INBOX, Receipts/2026, [Gmail]/All Mail"})
    assert made.status_code == 200, made.text
    account_id = made.json()["id"]
    assert owner.put(f"/api/v1/mail/accounts/{account_id}", json={"folders": INJECTION}).status_code == 400
    assert owner.put(f"/api/v1/mail/accounts/{account_id}", json={"folders": 'Bills "and" more'}).status_code == 400
    assert owner.put(f"/api/v1/mail/accounts/{account_id}", json={"folders": "Bills"}).status_code == 200
    with Session(engine) as session:
        assert session.get(MailAccount, account_id).folders == "Bills"


def test_the_connector_never_opens_a_folder_that_carries_a_command():
    Recorder.opened = []
    with Session(engine) as session:
        account = MailAccount(label="Box", kind="imap", host="mail.example", port=993, username="u", password_enc=crypto.encrypt("p"), folders="INBOX," + INJECTION)
        session.add(account)
        session.commit()
        result = MailConnector(client_factory=Recorder, fetch=lambda url: b"").sync(session, today=TODAY)
        session.refresh(account)
    assert Recorder.opened == []  # the mailbox is refused whole, before anything is sent, and says why
    assert not result.ok and "folder" in account.last_error.lower()


def test_a_stored_reference_with_such_a_folder_is_not_fetched(owner: TestClient, monkeypatch):
    Recorder.opened = []
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", Recorder)
    with Session(engine) as session:
        account = MailAccount(label="Box", kind="imap", host="mail.example", port=993, username="u", password_enc=crypto.encrypt("p"))
        session.add(account)
        session.commit()
        ref = MessageRef(account_id=account.id, folder=INJECTION, uid=7, subject="x")
        session.add(ref)
        session.commit()
        ref_id = ref.id
    assert owner.get(f"/api/v1/mail/messages/{ref_id}").status_code == 400
    assert owner.get(f"/api/v1/mail/messages/{ref_id}/attachments/a.pdf").status_code == 400
    assert Recorder.opened == []


def test_an_email_with_a_pile_of_pdfs_reads_only_a_few(monkeypatch):
    from app.connectors import mail as mail_connector

    calls: list[int] = []
    monkeypatch.setattr("app.parsers.paystub.pdf_text", lambda data: calls.append(1) or "")
    attachments = [Attachment(name=f"stub-{i}.pdf", content_type="application/pdf", size=4, data=b"%PDF") for i in range(40)]
    parsed = Parsed(message_id="<pile@example>", sender="payroll@example.test", sender_name="Payroll", subject="Stubs", date=None, text="", html="", attachments=attachments)
    with Session(engine) as session:
        account = MailAccount(label="Box", kind="imap", host="mail.example", username="u", password_enc="")
        session.add(account)
        session.commit()
        ref = MessageRef(account_id=account.id, folder="INBOX", uid=1)
        session.add(ref)
        session.commit()
        mail_connector.paystub_from_attachments(session, ref, parsed, TODAY, "rule:test")
    assert 0 < len(calls) <= mail_connector.MAX_PDF_ATTACHMENTS < 40


def test_a_csv_with_an_oversized_field_is_refused_not_crashed_on(owner: TestClient):
    big = ("Date,Amount,Description\n2026-01-02,1.00," + "x" * 200_000 + "\n").encode()
    for route in ("preview", "file"):
        response = owner.post(f"/api/v1/imports/{route}", files={"file": ("rows.csv", big, "text/csv")})
        assert response.status_code == 400, (route, response.status_code)
    from app.connectors.rocketmoney import parse_csv

    assert parse_csv("Date,Name,Amount,Category\n2026-01-02," + "y" * 200_000 + ",1.00,Food\n") is None


def test_the_webhook_rate_table_never_grows_without_end():
    from app.api import plaid_webhook as route

    route._hits.clear()
    now = 1_700_000_000.0
    for n in range(route.LIMIT):
        assert route.allow("203.0.113.9", now + n)
    assert not route.allow("203.0.113.9", now + route.LIMIT)  # the limit holds for one address
    for n in range(6000):
        route.allow(f"198.51.{n // 256}.{n % 256}", now + 1)
    assert len(route._hits) <= route.MAX_ADDRESSES
    assert not route.allow("203.0.113.9", now + route.LIMIT + 1)  # a crowd of strangers does not reset a busy one
    route._hits.clear()
