"""Sends happen only with an approval. A model, a script or the owner's own screen queues a draft; the owner
approves it in the app (or a standing allowance for that recipient does); only then does the mailbox send.
Every step is recorded on the approval row."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from sqlmodel import Session, select

from . import notify
from .connectors.mail import account_password
from .mail import smtp
from .models import Approval, MailAccount
from .store import get_setting, set_setting

log = logging.getLogger("fa.approvals")

Sender = Callable[..., str]
SENDER: Sender = smtp.send_message  # tests replace this; nothing else does
# One decision at a time: two requests that both read an approval as pending must not both send it. The row is read
# again under this lock, so the second finds it decided, whatever it saw before.
_decide = threading.Lock()


def allowances(session: Session, person_id: int | None = None) -> list[str]:
    """The recipients a person's sends go to without asking: the owner's in the settings, a second person's on
    their own row. One person's allowance never lets the other's sends through."""
    if person_id is not None:
        from .models import Person
        from .people import allowances as person_allowances

        person = session.get(Person, person_id)
        return person_allowances(person) if person else []
    return [a.lower() for a in (get_setting(session, "send.allowances", []) or [])]


def _store(session: Session, person_id: int | None, values: list[str]) -> None:
    if person_id is None:
        set_setting(session, "send.allowances", values)
        return
    import json

    from .models import Person

    person = session.get(Person, person_id)
    person.allowances = json.dumps(values)
    session.add(person)
    session.commit()


def allow(session: Session, recipient: str, person_id: int | None = None) -> list[str]:
    current = allowances(session, person_id)
    if recipient.lower() not in current:
        current.append(recipient.lower())
    _store(session, person_id, current)
    return current


def disallow(session: Session, recipient: str, person_id: int | None = None) -> list[str]:
    current = [a for a in allowances(session, person_id) if a != recipient.lower()]
    _store(session, person_id, current)
    return current


def sending_account(session: Session, account_id: int | None) -> MailAccount | None:
    if account_id:
        account = session.get(MailAccount, account_id)
        return account if account and account.active else None
    return session.exec(select(MailAccount).where(MailAccount.active == True, MailAccount.smtp_host != "").order_by(MailAccount.id)).first()  # noqa: E712


def check_draft(to: str, subject: str, body: str) -> None:
    """One recipient, as the mail library reads it, and a subject and a body. Checked when a send is queued, when it
    is edited, and again just before it goes."""
    from email.utils import getaddresses

    to = (to or "").strip()
    found = [addr for _, addr in getaddresses([to]) if addr]
    if len(found) != 1 or found[0] != to or "@" not in to or any(c in to for c in " ,;<>\r\n"):
        raise ValueError("the recipient must be one email address")
    if not (subject or "").strip() or not (body or "").strip():
        raise ValueError("a subject and a body are needed")
    if any(ord(c) < 32 or c == "\x7f" for c in subject):
        # the mail library refuses a header with a line break, but only at send time; said at the door instead
        raise ValueError("the subject is one line")


def create(session: Session, *, to: str, subject: str, body: str, reason: str = "", source: str = "", created_by: str = "owner", created_kind: str = "owner", account_id: int | None = None,
           for_person_id: int | None = None) -> Approval:
    to = to.strip()
    check_draft(to, subject, body)
    row = Approval(kind="send", to=to, subject=subject.strip()[:200], body=body.strip()[:20000], reason=reason[:300], source=source[:100], created_by=created_by, created_kind=created_kind, account_id=account_id,
                   for_person_id=for_person_id)
    session.add(row)
    session.commit()
    session.refresh(row)
    # A standing allowance covers what the owner or a second person queues themselves (the app's own pages). A draft
    # a model or a script queued (an API key, a connected app) always waits for a tap: a hostile email read by that
    # model could otherwise send, in the owner's name, anything to any allowed address.
    by_a_person = created_kind in ("owner", "person")
    if not by_a_person and to.lower() in allowances(session, for_person_id):
        row.reason = (row.reason + " " if row.reason else "") + "(queued by a connected app or key, so the standing allowance for this address was not used)"
        row.reason = row.reason[:300]
    if by_a_person and to.lower() in allowances(session, for_person_id):
        row.decided_by = "allowance"
        row.decided_at = datetime.now(timezone.utc)
        row.status = "approved"
        session.add(row)
        session.commit()
        send(session, row)
    else:
        # Said in the push itself, not only on the screen: a draft a model queued may be a message's instructions.
        title = "A send waits for you" if by_a_person else f"{(created_by or 'A connected app')[:60]} (a connected app or key) asks to send; it waits for you"
        if for_person_id is not None:
            from .models import Person

            person = session.get(Person, for_person_id)
            title = f"A send waits for {(person.display_name or person.username) if person else 'the second person'}" + ("" if by_a_person else f", asked for by {(created_by or 'a connected app')[:60]} (a connected app or key)")
        notify.send(session, title, f"To {to}: {row.subject}\n{reason or body[:140]}", priority="high", tags=["envelope"], link=None)
    session.refresh(row)
    return row


def _claim(session: Session, row: Approval, status: str, decided_by: str) -> None:
    """Move a pending approval to its decision, once: a second decision finds it decided."""
    with _decide:
        session.refresh(row)
        if row.status != "pending":
            raise ValueError(f"the approval is {row.status}, not pending")
        row.status = status
        row.decided_by = decided_by
        row.decided_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()


def edit_draft(session: Session, row: Approval, changed: dict[str, str]) -> Approval:
    """Change a pending draft's recipient, subject or body, under the decision lock: an approve in the same instant
    either sent what was there before the edit, or finds the edited draft; never a draft half of each. A draft that
    was decided meanwhile is left as it is (ValueError)."""
    check_draft(changed.get("to", row.to), changed.get("subject", row.subject), changed.get("body", row.body))
    with _decide:
        session.refresh(row)
        if row.status != "pending":
            raise ValueError(f"the approval is {row.status}, not pending")
        for field, value in changed.items():
            setattr(row, field, value)
        session.add(row)
        session.commit()
    session.refresh(row)
    return row


def approve(session: Session, row: Approval, *, decided_by: str = "owner", allow_always: bool = False) -> Approval:
    _claim(session, row, "approved", decided_by)
    if allow_always:
        allow(session, row.to, row.for_person_id)
    send(session, row)
    session.refresh(row)
    return row


def deny(session: Session, row: Approval, *, decided_by: str = "owner") -> Approval:
    _claim(session, row, "denied", decided_by)
    session.refresh(row)
    return row


def send(session: Session, row: Approval) -> Approval:
    """Send an approved draft through the chosen mailbox; record the message id or the failure."""
    account = sending_account(session, row.account_id)
    try:
        check_draft(row.to, row.subject, row.body)
    except ValueError as error:
        row.status, row.error = "failed", f"not sent: {error}"
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    if account is None:
        row.status, row.error = "failed", "no mailbox can send: set the SMTP server on Settings › Connectors"
    else:
        try:
            row.message_id = SENDER(account, account_password(account), to=row.to, subject=row.subject, body=row.body)
            row.status, row.error, row.sent_at, row.account_id = "sent", "", datetime.now(timezone.utc), account.id
        except Exception as error:  # noqa: BLE001 - the failure is recorded on the row, never raised into a request
            log.warning("send failed: %s", error)
            row.status, row.error = "failed", f"{type(error).__name__}: {error}"[:500]
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def out(row: Approval) -> dict:
    return {
        "id": row.id, "kind": row.kind, "status": row.status, "to": row.to, "subject": row.subject, "body": row.body, "reason": row.reason, "source": row.source,
        "created_by": row.created_by, "created_kind": row.created_kind, "created_at": row.created_at.isoformat(), "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None, "sent_at": row.sent_at.isoformat() if row.sent_at else None, "error": row.error, "account_id": row.account_id,
        "for_person_id": row.for_person_id,
    }
