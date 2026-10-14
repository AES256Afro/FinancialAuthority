"""Sends happen only with an approval. A model, a script or the owner's own screen queues a draft; the owner
approves it in the app (or a standing allowance for that recipient does); only then does the mailbox send.
Every step is recorded on the approval row."""

from __future__ import annotations

import logging
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


def allowances(session: Session) -> list[str]:
    return [a.lower() for a in (get_setting(session, "send.allowances", []) or [])]


def allow(session: Session, recipient: str) -> list[str]:
    current = allowances(session)
    if recipient.lower() not in current:
        current.append(recipient.lower())
    set_setting(session, "send.allowances", current)
    return current


def disallow(session: Session, recipient: str) -> list[str]:
    current = [a for a in allowances(session) if a != recipient.lower()]
    set_setting(session, "send.allowances", current)
    return current


def sending_account(session: Session, account_id: int | None) -> MailAccount | None:
    if account_id:
        account = session.get(MailAccount, account_id)
        return account if account and account.active else None
    return session.exec(select(MailAccount).where(MailAccount.active == True, MailAccount.smtp_host != "").order_by(MailAccount.id)).first()  # noqa: E712


def create(session: Session, *, to: str, subject: str, body: str, reason: str = "", source: str = "", created_by: str = "owner", created_kind: str = "owner", account_id: int | None = None) -> Approval:
    to = to.strip()
    if "@" not in to or " " in to:
        raise ValueError("the recipient must be one email address")
    if not subject.strip() or not body.strip():
        raise ValueError("a subject and a body are needed")
    row = Approval(kind="send", to=to, subject=subject.strip()[:200], body=body.strip()[:20000], reason=reason[:300], source=source[:100], created_by=created_by, created_kind=created_kind, account_id=account_id)
    session.add(row)
    session.commit()
    session.refresh(row)
    if to.lower() in allowances(session):
        row.decided_by = "allowance"
        row.decided_at = datetime.now(timezone.utc)
        row.status = "approved"
        session.add(row)
        session.commit()
        send(session, row)
    else:
        notify.send(session, "A send waits for you", f"To {to}: {row.subject}\n{reason or body[:140]}", priority="high", tags=["envelope"], link=None)
    session.refresh(row)
    return row


def approve(session: Session, row: Approval, *, decided_by: str = "owner", allow_always: bool = False) -> Approval:
    if row.status != "pending":
        raise ValueError(f"the approval is {row.status}, not pending")
    row.status = "approved"
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    if allow_always:
        allow(session, row.to)
    send(session, row)
    session.refresh(row)
    return row


def deny(session: Session, row: Approval, *, decided_by: str = "owner") -> Approval:
    if row.status != "pending":
        raise ValueError(f"the approval is {row.status}, not pending")
    row.status = "denied"
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def send(session: Session, row: Approval) -> Approval:
    """Send an approved draft through the chosen mailbox; record the message id or the failure."""
    account = sending_account(session, row.account_id)
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
    }
