"""The mail connector: every active mailbox, every quarter hour, read-only.

New messages become MessageRefs; what the classifier recognises becomes an ExtractedFact that waits for the
owner; a Rocket Money export email has its download link followed and the file imported; bank alerts land in
the brief the same hour. Nothing here replies, marks, moves or deletes.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..mail.box import clean
from ..mail.classify import classify, excerpt_for, extract
from ..mail.imap import ImapClient, Parsed, parse_message
from ..models import ExtractedFact, MailAccount, MessageRef, utcnow
from ..store import get_setting, set_setting
from .base import Connector, SyncResult
from .rocketmoney import import_rows, parse_csv

log = logging.getLogger("fa.mail")

ClientFactory = Callable[..., ImapClient]
Fetcher = Callable[[str], bytes]

INFORMATIONAL = {"bank_alert", "payday", "payment_confirmation", "upcoming_bills"}  # kept as auto facts; no confirmation needed


def default_fetch(url: str) -> bytes:
    """A link from an email, fetched only over https, never to an address inside this network, every redirect
    checked again (app/netguard.py)."""
    from ..netguard import safe_get

    return safe_get(url)[0]


def export_link(parsed: Parsed) -> str | None:
    links = [l for l in parsed.links if re.search(r"download|export|transactions|csv", l, re.IGNORECASE)] or parsed.links
    return links[0] if links else None


def explain_error(host: str, error: Exception) -> str:
    """A mailbox failure in plain words where the cause is known; the raw error otherwise."""
    import imaplib
    import socket

    if host == "bridge":
        if isinstance(error, socket.gaierror):
            return "The Bridge is not running on the server yet: it needs your one sign-in there (the two commands are under Mailboxes on Connectors), then Sync now."
        if isinstance(error, (ConnectionRefusedError, TimeoutError)):
            return "The Bridge is not answering. It may still be starting; if this stays, start it again on the server (the command is under Mailboxes on Connectors)."
    if host in ("127.0.0.1", "localhost", "::1") and isinstance(error, ConnectionRefusedError):
        return "Nothing answers at 127.0.0.1 from inside the app. For Proton, the host is bridge: the Bridge's own screen says 127.0.0.1 because it speaks from inside its container."
    if isinstance(error, imaplib.IMAP4.error) and re.search(r"auth|login|credential|password|invalid", str(error), re.IGNORECASE):
        if host == "bridge":
            return "The Bridge refused the sign-in. Use the username and password the Bridge printed with info, not the Proton account password."
        return "The mail server refused the sign-in. Check the username and the app password."
    return f"{type(error).__name__}: {error}"[:300]


def uid_key(account_id: int, folder: str) -> str:
    return f"mail.{account_id}.{folder}.last_uid"


def account_password(account: MailAccount) -> str:
    return crypto.decrypt(account.password_enc) if crypto.is_encrypted(account.password_enc) else account.password_enc


def handle_message(session: Session, account: MailAccount, folder: str, uid: int, parsed: Parsed, today: date, fetch: Fetcher = default_fetch) -> dict:
    """Record one message and whatever it yields. Returns what happened, for the sync summary."""
    existing = session.exec(select(MessageRef).where(MessageRef.account_id == account.id, MessageRef.folder == folder, MessageRef.uid == uid)).first()
    if existing is not None:
        return {"seen": True}
    if parsed.message_id:
        # The same message under a new number (the server renumbered the folder) is one already read: its reference
        # follows the new number, and nothing it yielded is made twice.
        same = session.exec(select(MessageRef).where(MessageRef.account_id == account.id, MessageRef.message_id == parsed.message_id[:255])).first()
        if same is not None:
            if same.folder == folder:
                same.uid = uid
                session.add(same)
            return {"seen": True}
    kind, confidence, produced_by = classify(parsed.sender, parsed.subject, parsed.text)
    ref = MessageRef(
        account_id=account.id, folder=folder, uid=uid, message_id=parsed.message_id[:255], sender=parsed.sender[:255], sender_name=parsed.sender_name[:120],
        subject=parsed.subject[:300], date=parsed.date, has_attachments=bool(parsed.attachments), attachment_names=", ".join(a.name for a in parsed.attachments)[:400],
        snippet=clean(parsed.text, 200), kind=kind,
    )
    session.add(ref)
    session.flush()
    outcome: dict = {"kind": kind}
    if kind == "none":
        return outcome
    if kind == "export":
        # Nothing from a message is acted on by itself: the export waits on Needs you, with where it would come from.
        url = export_link(parsed)
        if not url:
            session.add(ExtractedFact(kind="export", message_ref_id=ref.id, fields=json.dumps({"sender": parsed.sender}), confidence=1.0, status="rejected", excerpt="no download link in the message", produced_by=produced_by, resolved_at=utcnow()))
            return {**outcome, "note": "no download link in the message"}
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        session.add(ExtractedFact(kind="export", message_ref_id=ref.id, fields=json.dumps({"url": url, "host": host, "sender": parsed.sender, "subject": parsed.subject[:200]}),
                                  confidence=1.0, status="pending", excerpt=f"A Rocket Money export is ready. Importing downloads it from {host} and adds its transactions; an import can be undone on Backups.",
                                  produced_by=produced_by))
        outcome["fact"] = "pending"
        return outcome
    if kind == "upcoming_bills":
        outcome.update(upcoming_bills_facts(session, ref, parsed, today, produced_by))
        return outcome
    # A PDF attachment that reads like a paystub becomes a paystub fact whatever the subject said.
    stub_fact = paystub_from_attachments(session, ref, parsed, today, produced_by)
    if stub_fact and kind in ("none", "paystub", "statement"):
        outcome["fact"] = "pending"
        outcome["kind"] = "paystub"
        return outcome
    fields = extract(kind, parsed.subject, parsed.text, today)
    if kind == "installment":
        from ..parsers.affirm import parse_loan_confirmation

        loan = parse_loan_confirmation(parsed.text, parsed.date.date() if parsed.date else today)
        if loan:
            confidence = max(confidence, loan.pop("confidence"))
            fields.update(loan)
            fields["amount"] = loan["installment"]
            fields["due_date"] = loan["first_date"]
    if kind == "paystub":
        from ..parsers.paystub import parse_paystub_text

        parsed_stub = parse_paystub_text(parsed.text, today)
        if parsed_stub:
            fields.update({k: v for k, v in parsed_stub.items() if k != "confidence"})
            confidence = max(confidence, parsed_stub["confidence"])
    fields["sender"] = parsed.sender
    fields["sender_name"] = parsed.sender_name
    fields["subject"] = parsed.subject[:200]
    if parsed.date:
        fields["message_date"] = parsed.date.date().isoformat()
    status = "auto" if kind in INFORMATIONAL else "pending"
    if kind in ("bill_due", "past_due") and "amount" not in fields:
        confidence = min(confidence, 0.5)
    session.add(ExtractedFact(kind=kind, message_ref_id=ref.id, fields=json.dumps(fields), confidence=confidence, status=status, excerpt=excerpt_for(parsed.text, fields), produced_by=produced_by, resolved_at=utcnow() if status == "auto" else None))
    outcome["fact"] = status
    return outcome


def upcoming_bills_facts(session: Session, ref: MessageRef, parsed: Parsed, today: date, produced_by: str) -> dict:
    """Rocket Money's weekly list: one pending bill fact per charge it names, or an auto fact when a bill on the
    register already says the same. With nothing parsed, the message stays an informational fact."""
    from ..engine.recurring import merchant_key
    from ..models import Bill
    from ..parsers.rocketmoney_bills import parse_upcoming_bills

    ref_date = parsed.date.date() if parsed.date else today
    charges = parse_upcoming_bills(parsed.text, ref_date)
    base = {"sender": parsed.sender, "sender_name": parsed.sender_name, "subject": parsed.subject[:200], "source": "rocketmoney-upcoming"}
    if parsed.date:
        base["message_date"] = parsed.date.date().isoformat()
    if not charges:
        session.add(ExtractedFact(kind="upcoming_bills", message_ref_id=ref.id, fields=json.dumps(base), confidence=0.5, status="auto", excerpt=clean(parsed.text, 200), produced_by=produced_by, resolved_at=utcnow()))
        return {"fact": "auto", "charges": 0}
    bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
    pending = known = 0
    for charge in charges:
        key = merchant_key(charge["name"])
        match = next((b for b in bills if merchant_key(b.name) == key or merchant_key(b.name).startswith(key) or key.startswith(merchant_key(b.name))), None)
        same = match is not None and abs(match.amount - charge["amount"]) < 0.01 and match.due_day == charge["due_date"].day
        fields = {**base, "name": charge["name"], "amount": charge["amount"], "due_date": charge["due_date"].isoformat()}
        excerpt = f"{charge['name']} · {charge['due_date'].strftime('%A, %B %d')} · ${charge['amount']:,.2f}"
        session.add(ExtractedFact(kind="bill_due", message_ref_id=ref.id, fields=json.dumps(fields), confidence=0.85, status="auto" if same else "pending", excerpt=excerpt,
                                  produced_by=f"{produced_by}+parser:rocketmoney_bills", applied_to=f"bill:{match.id}" if same else "", resolved_at=utcnow() if same else None))
        if same:
            known += 1
        else:
            pending += 1
    return {"fact": "pending" if pending else "auto", "charges": len(charges), "pending": pending, "already_known": known}


def paystub_from_attachments(session: Session, ref: MessageRef, parsed: Parsed, today: date, produced_by: str) -> bool:
    """Read each PDF attachment; one that reads like a paystub becomes a pending paystub fact."""
    from ..parsers.paystub import parse_paystub_text, pdf_text

    found = False
    for attachment in parsed.attachments:
        is_pdf = attachment.content_type == "application/pdf" or attachment.name.lower().endswith(".pdf")
        if not is_pdf or not attachment.data:
            continue
        text = pdf_text(attachment.data)
        stub = parse_paystub_text(text, today)
        if not stub:
            continue
        confidence = stub.pop("confidence")
        stub.update({"sender": parsed.sender, "sender_name": parsed.sender_name, "subject": parsed.subject[:200], "attachment": attachment.name})
        if parsed.date:
            stub["message_date"] = parsed.date.date().isoformat()
        session.add(ExtractedFact(kind="paystub", message_ref_id=ref.id, fields=json.dumps(stub), confidence=confidence, status="pending", excerpt=excerpt_for(text, {"amount": stub.get("net"), "due_date": stub.get("pay_date")}), produced_by=f"{produced_by or 'rule:attachment'}+parser:paystub"))
        found = True
    return found


def capture_export(session: Session, url: str, fetch: Fetcher) -> dict:
    """Follow an export email's download link and import the file, once the owner has confirmed it on Needs you.
    The link lives seven days."""
    try:
        raw = fetch(url)
    except Exception as error:  # noqa: BLE001
        return {"imported": 0, "note": f"could not download the export: {error if str(error) else type(error).__name__}"}
    text = raw.decode("utf-8-sig", errors="replace")
    rows = parse_csv(text, "rocketmoney-export-from-mail.csv")
    if rows is None:
        return {"imported": 0, "note": "the link did not give a Rocket Money transactions export"}
    result = import_rows(session, rows, "rocketmoney-export-from-mail.csv", via="mail")
    return {"imported": result.inserted, "duplicates": result.duplicates, "note": f"export imported: {result.summary}"}


class MailConnector(Connector):
    id = "mail"
    title = "Mailboxes"
    yields = "Bills, statements, paystubs, installment schedules, payment confirmations, past-due and bank alerts, and the Rocket Money export, from Proton Mail, Gmail or any IMAP mailbox."
    settings_schema = [
        {"name": "label", "label": "Name", "help": "Proton, Gmail, Work"},
        {"name": "kind", "label": "Kind", "help": "proton (through the Bridge), gmail (app password), or imap"},
        {"name": "host", "label": "IMAP host"},
        {"name": "port", "label": "Port"},
        {"name": "username", "label": "Username"},
        {"name": "password", "label": "Password or app password", "secret": True},
        {"name": "folders", "label": "Folders to watch", "help": "comma separated; INBOX by default"},
    ]

    def __init__(self, client_factory: ClientFactory = ImapClient, fetch: Fetcher = default_fetch) -> None:
        self.client_factory = client_factory
        self.fetch = fetch

    def sync(self, session: Session, today: date | None = None, account_id: int | None = None, **_: object) -> SyncResult:
        from ..clock import local_today

        today = today or local_today()
        query = select(MailAccount).where(MailAccount.active == True)  # noqa: E712
        if account_id is not None:
            query = query.where(MailAccount.id == account_id)
        accounts = session.exec(query).all()
        if not accounts:
            return SyncResult(ok=True, summary="no mailboxes connected")
        notes: list[str] = []
        total_new = 0
        ok = True
        for account in accounts:
            try:
                new, facts = self._sync_account(session, account, today)
                total_new += new
                account.last_error = ""
                account.last_sync_at = utcnow()
                account.messages_seen += new
                notes.append(f"{account.label}: {new} new, {facts} facts")
            except Exception as error:  # noqa: BLE001
                session.rollback()
                ok = False
                account.last_error = explain_error(account.host, error)[:300]
                notes.append(f"{account.label}: {account.last_error}")
                log.warning("mail sync failed for %s: %s", account.label, error)
            session.add(account)
            session.commit()
        return SyncResult(ok=ok, summary="; ".join(notes), inserted=total_new, at=utcnow())

    def _sync_account(self, session: Session, account: MailAccount, today: date) -> tuple[int, int]:
        client = self.client_factory(account.host, account.port, account.username, account_password(account), account.use_ssl, account.starttls)
        client.connect()
        new = facts = 0
        try:
            for folder in [f.strip() for f in account.folders.split(",") if f.strip()]:
                client.select_readonly(folder)
                last = int(get_setting(session, uid_key(account.id, folder), 0) or 0)
                validity = getattr(client, "uidvalidity", lambda: 0)()
                known = int(get_setting(session, uid_key(account.id, folder) + ".validity", 0) or 0)
                if validity and known and validity != known:
                    # The server numbered the folder afresh (a rebuilt Bridge cache): start again from the recent end.
                    # The old numbers mean nothing now, so they are set aside (negative) and never match a new one;
                    # a message already read is known again by its Message-ID.
                    last = 0
                    from sqlmodel import delete, update

                    session.exec(delete(MessageRef).where(MessageRef.account_id == account.id, MessageRef.folder == folder, MessageRef.kind == "none"))
                    session.exec(update(MessageRef).where(MessageRef.account_id == account.id, MessageRef.folder == folder, MessageRef.uid > 0).values(uid=-MessageRef.uid))
                uids = client.new_uids(last)
                # A new mailbox starts from the most recent messages; after that, the oldest unread first.
                uids = uids[-BATCH_FIRST:] if last == 0 else uids[:BATCH_FIRST]
                for uid in uids:
                    try:
                        parsed = parse_message(client.fetch_raw(uid))
                        with session.begin_nested():
                            outcome = handle_message(session, account, folder, uid, parsed, today, self.fetch)
                    except Exception as error:  # noqa: BLE001 - one unreadable message never blocks the mailbox
                        log.warning("message %s in %s could not be read: %s", uid, folder, error)
                        outcome = {}
                    if outcome and not outcome.get("seen"):
                        new += 1
                    if outcome.get("fact") or outcome.get("imported"):
                        facts += 1
                    last = max(last, uid)
                    done = uids.index(uid) + 1
                    if done % 25 == 0:
                        set_setting(session, uid_key(account.id, folder), last)
                set_setting(session, uid_key(account.id, folder), last)
                if validity:
                    # Written with the last UID it goes with, once the folder is read: a sync that fails before this
                    # finds the numbering still new next time and reads the folder again from the start.
                    set_setting(session, uid_key(account.id, folder) + ".validity", validity)
                session.commit()
        finally:
            client.close()
        return new, facts


BATCH_FIRST = 150


def test_account(host: str, port: int, username: str, password: str, use_ssl: bool = True, starttls: bool = False, client_factory: ClientFactory = ImapClient) -> dict:
    client = client_factory(host, port, username, password, use_ssl, starttls)
    client.connect()
    try:
        folders = client.folders()
        exists, _ = client.select_readonly("INBOX")
        return {"ok": True, "folders": folders, "inbox_messages": exists}
    finally:
        client.close()
