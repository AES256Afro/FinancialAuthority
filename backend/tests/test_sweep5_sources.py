"""M61: a mailbox whose sign-in is refused is tried again later, not every quarter hour."""

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session

from app import crypto
from app.connectors import mail as mail_connector
from app.connectors.mail import MailConnector, backoff_key, explain_error, signin_refused
from app.db import engine
from app.models import MailAccount
from app.store import get_setting

TODAY = date(2026, 10, 14)


class CountingClient:
    """Refuses every sign-in the way the Bridge does, and counts the tries."""

    tries = 0

    def __init__(self, host, port, username, password, use_ssl=True, starttls=False):  # noqa: ANN001
        self.password = password

    def connect(self) -> None:
        CountingClient.tries += 1
        if self.password != "app-pass":
            import imaplib

            raise imaplib.IMAP4.error(b"no such user")

    def select_readonly(self, folder: str) -> tuple[int, int]:
        return (0, 1)

    def new_uids(self, since_uid: int) -> list[int]:
        return []

    def close(self) -> None:
        pass


def add_account(session: Session, password: str) -> MailAccount:
    account = MailAccount(label="Proton", kind="proton", host="bridge", port=143, username="owner@example.test", password_enc=crypto.encrypt(password), use_ssl=False, starttls=True, folders="INBOX")
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_a_refused_sign_in_waits_an_hour_then_four_then_a_day(monkeypatch):
    clock = {"now": datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(mail_connector, "now_utc", lambda: clock["now"])
    CountingClient.tries = 0
    connector = MailConnector(client_factory=CountingClient, fetch=lambda url: b"")
    with Session(engine) as session:
        account = add_account(session, password="wrong")
        first = connector.sync(session, today=TODAY)
        assert not first.ok and "no account signed in" in first.summary and CountingClient.tries == 1
        session.refresh(account)
        assert "Trying again at" in account.last_error
        # Every quarter hour for the next hour: nothing is tried, and the run is not a failure.
        for minutes in (15, 30, 45):
            clock["now"] += timedelta(minutes=15)
            again = connector.sync(session, today=TODAY)
            assert again.ok and "trying again at" in again.summary and CountingClient.tries == 1, minutes
        # Sync now from Connectors always tries.
        pressed = connector.sync(session, today=TODAY, account_id=account.id)
        assert not pressed.ok and CountingClient.tries == 2
        state = get_setting(session, backoff_key(account.id))
        assert state["fails"] == 2 and datetime.fromisoformat(state["until"]) == clock["now"] + timedelta(hours=4)
        clock["now"] += timedelta(hours=4, minutes=1)
        connector.sync(session, today=TODAY)
        assert CountingClient.tries == 3 and get_setting(session, backoff_key(account.id))["fails"] == 3
        clock["now"] += timedelta(hours=23)
        assert connector.sync(session, today=TODAY).ok and CountingClient.tries == 3  # a day's wait after the third
        clock["now"] += timedelta(hours=2)
        connector.sync(session, today=TODAY)
        assert CountingClient.tries == 4
        # The right password clears the wait.
        account.password_enc = crypto.encrypt("app-pass")
        session.add(account)
        session.commit()
        fixed = connector.sync(session, today=TODAY, account_id=account.id)
        assert fixed.ok and get_setting(session, backoff_key(account.id)) is None
        session.refresh(account)
        assert account.last_error == ""


def test_a_network_error_is_not_a_refused_sign_in():
    assert not signin_refused(ConnectionRefusedError(111, "Connection refused"))
    assert not signin_refused(TimeoutError("timed out"))
    import imaplib

    assert signin_refused(imaplib.IMAP4.error(b"no such user"))
    assert signin_refused(imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials"))
    assert "no account signed in" in explain_error("bridge", imaplib.IMAP4.error(b"no such user"))
    assert "refused the sign-in" in explain_error("bridge", imaplib.IMAP4.error(b"too many login attempts"))
