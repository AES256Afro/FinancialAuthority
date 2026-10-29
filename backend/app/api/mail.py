"""Mail: accounts, a test and a sync, message search, a boxed read, and the facts that wait for the owner."""

from __future__ import annotations

from pathlib import Path

import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from .inputs import In
from .. import auth, crypto, services
from ..connectors import CONNECTORS
from ..connectors.mail import MailConnector, account_password, folder_list, safe_folder, test_account
from ..db import get_session
from ..engine import facts
from ..engine.recurring import merchant_key
from ..mail.box import box_untrusted
from ..mail.imap import check_credential, parse_message
from ..models import Account, Bill, ExtractedFact, InstallmentPlan, MailAccount, MessageRef
from .routes import today

router = APIRouter(prefix="/api/v1", tags=["mail"])

PRESETS = {
    "proton": {"host": "bridge", "port": 143, "use_ssl": False, "starttls": True, "smtp_host": "bridge", "smtp_port": 25, "smtp_ssl": False, "smtp_starttls": True, "help": "Proton Mail through the Proton Mail Bridge. The steps below say where each command runs and which password goes where."},
    "gmail": {"host": "imap.gmail.com", "port": 993, "use_ssl": True, "starttls": False, "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_ssl": True, "smtp_starttls": False, "help": "Gmail with an app password: a 16-letter password Google makes for one app, not your Google password. It needs 2-Step Verification on."},
    "imap": {"host": "", "port": 993, "use_ssl": True, "starttls": False, "smtp_host": "", "smtp_port": 0, "smtp_ssl": False, "smtp_starttls": True, "help": "Any mailbox over IMAP: your provider's help pages list its IMAP server (often imap.<provider>.com, port 993). Many providers want an app password rather than your sign-in password."},
}


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def in_container() -> bool:
    return Path("/.dockerenv").exists()


def mailbox_host(kind: str, host: str) -> str:
    """The Bridge's info screen prints 127.0.0.1, which is right from inside the Bridge's own container. From the
    app's container the Bridge is the sidecar named bridge, so a Proton mailbox given a loopback host means it."""
    if kind == "proton" and host in LOOPBACK and in_container():
        return "bridge"
    return host


def mail_connector() -> MailConnector:
    return CONNECTORS["mail"]  # type: ignore[return-value]


def _credential(value: str, what: str) -> str:
    """A username or a password goes into the IMAP sign-in line: one with a line break is refused at the door (the
    client refuses it again before anything is sent)."""
    try:
        return check_credential(value, what)
    except ValueError as error:
        raise HTTPException(400, str(error).capitalize()) from error


def _folders(value: str | None) -> str:
    """The folders setting as it is kept: each name checked, since it goes into an IMAP command line as it is."""
    try:
        names = folder_list(value or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return ",".join(names) or "INBOX"


def account_out(a: MailAccount) -> dict:
    return {
        "id": a.id, "label": a.label, "kind": a.kind, "host": a.host, "port": a.port, "username": a.username, "use_ssl": a.use_ssl, "starttls": a.starttls,
        "folders": a.folders, "active": a.active, "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None, "last_error": a.last_error, "messages_seen": a.messages_seen,
        "has_password": bool(a.password_enc), "smtp_host": a.smtp_host, "smtp_port": a.smtp_port, "smtp_ssl": a.smtp_ssl, "smtp_starttls": a.smtp_starttls,
    }


class MailAccountIn(In):
    label: str | None = None
    kind: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    use_ssl: bool | None = None
    starttls: bool | None = None
    folders: str | None = None
    active: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_ssl: bool | None = None
    smtp_starttls: bool | None = None


@router.get("/mail/presets")
def presets(_: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..config import settings

    return {"presets": PRESETS, "bridge": {"container": settings.bridge_container, "install": settings.install, "in_container": in_container()}}


@router.get("/mail/accounts")
def list_accounts(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"accounts": [account_out(a) for a in session.exec(select(MailAccount).order_by(MailAccount.id)).all()]}


@router.post("/mail/accounts")
def create_account(body: MailAccountIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    kind = body.kind or "imap"
    if kind not in PRESETS:
        raise HTTPException(400, "kind must be proton, gmail or imap")
    preset = PRESETS[kind]
    if not body.username or not body.password:
        raise HTTPException(400, "A mailbox needs a username and a password")
    _credential(body.username, "username")
    _credential(body.password, "password")
    host = mailbox_host(kind, body.host or preset["host"])
    if not host:
        raise HTTPException(400, "An IMAP host is needed")
    try:
        enc = crypto.encrypt(body.password)
    except crypto.DataKeyMissing as error:
        raise HTTPException(400, str(error)) from error
    account = MailAccount(
        label=body.label or kind.title(), kind=kind, host=host, port=body.port or preset["port"], username=body.username, password_enc=enc,
        use_ssl=body.use_ssl if body.use_ssl is not None else preset["use_ssl"], starttls=body.starttls if body.starttls is not None else preset["starttls"],
        folders=_folders(body.folders), active=body.active if body.active is not None else True,
        smtp_host=body.smtp_host if body.smtp_host is not None else preset["smtp_host"], smtp_port=body.smtp_port if body.smtp_port is not None else preset["smtp_port"],
        smtp_ssl=body.smtp_ssl if body.smtp_ssl is not None else preset["smtp_ssl"], smtp_starttls=body.smtp_starttls if body.smtp_starttls is not None else preset["smtp_starttls"],
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account_out(account)


@router.put("/mail/accounts/{account_id}")
def update_account(account_id: int, body: MailAccountIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(MailAccount, account_id)
    if account is None:
        raise HTTPException(404, "No such mailbox")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    # The stored password is sent only to the servers it was saved for: moving the mailbox to another server, port
    # or encryption (for reading or for sending) needs the password typed again.
    where = ("host", "port", "use_ssl", "starttls", "username", "smtp_host", "smtp_port", "smtp_ssl", "smtp_starttls")
    moved = [k for k in where if k in changes and (mailbox_host(body.kind or account.kind, changes[k]) if k == "host" else changes[k]) != getattr(account, k)]
    if moved and not changes.get("password"):
        raise HTTPException(400, "Changing where this mailbox connects needs its password again: type it with the change.")
    for field, value in changes.items():
        if field in ("username", "password"):
            _credential(value, field)
        if field == "password":
            account.password_enc = crypto.encrypt(value)
        elif field == "kind" and value not in PRESETS:
            raise HTTPException(400, "kind must be proton, gmail or imap")
        elif field == "host":
            account.host = mailbox_host(body.kind or account.kind, value)
        elif field == "folders":
            account.folders = _folders(value)
        else:
            setattr(account, field, value)
    session.add(account)
    session.commit()
    session.refresh(account)
    return account_out(account)


@router.delete("/mail/accounts/{account_id}")
def remove_account(account_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    account = session.get(MailAccount, account_id)
    if account is None:
        raise HTTPException(404, "No such mailbox")
    account.active = False
    account.password_enc = ""
    session.add(account)
    session.commit()
    return {"ok": True}


class TestIn(In):
    id: int | None = None
    kind: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    use_ssl: bool | None = None
    starttls: bool | None = None


@router.post("/mail/accounts/test")
def test_mailbox(body: TestIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.id is not None:
        account = session.get(MailAccount, body.id)
        if account is None:
            raise HTTPException(404, "No such mailbox")
        host, port, username, password, use_ssl, starttls = account.host, account.port, account.username, account_password(account), account.use_ssl, account.starttls
    else:
        preset = PRESETS.get(body.kind or "imap", PRESETS["imap"])
        host, port = mailbox_host(body.kind or "imap", body.host or preset["host"]), body.port or preset["port"]
        username, password = _credential(body.username or "", "username"), _credential(body.password or "", "password")
        use_ssl = body.use_ssl if body.use_ssl is not None else preset["use_ssl"]
        starttls = body.starttls if body.starttls is not None else preset["starttls"]
    try:
        return test_account(host, port, username, password, use_ssl, starttls, client_factory=mail_connector().client_factory)
    except Exception as error:  # noqa: BLE001
        from ..connectors.mail import explain_error

        return {"ok": False, "error": explain_error(host, error)}


@router.post("/mail/accounts/{account_id}/sync")
def sync_mailbox(account_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    result = mail_connector().sync(session, today=today(), account_id=account_id)
    return {"ok": result.ok, "summary": result.summary, "new": result.inserted}


# ---- messages ------------------------------------------------------------------------------


def message_out(m: MessageRef, labels: dict[int, str]) -> dict:
    return {"id": m.id, "account": labels.get(m.account_id, "?"), "folder": m.folder, "uid": m.uid, "sender": m.sender, "sender_name": m.sender_name, "subject": m.subject,
            "date": m.date.isoformat() if m.date else None, "has_attachments": m.has_attachments, "attachment_names": m.attachment_names, "kind": m.kind, "snippet": m.snippet}


@router.get("/mail/messages")
def search_messages(q: str | None = None, sender: str | None = None, kind: str | None = None, limit: int = Query(30, ge=1, le=200), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    query = select(MessageRef)
    if q:
        like = f"%{q}%"
        query = query.where((MessageRef.subject.ilike(like)) | (MessageRef.sender.ilike(like)) | (MessageRef.sender_name.ilike(like)) | (MessageRef.snippet.ilike(like)))
    if sender:
        query = query.where(MessageRef.sender.ilike(f"%{sender}%"))
    if kind:
        query = query.where(MessageRef.kind == kind)
    rows = session.exec(query.order_by(MessageRef.date.desc(), MessageRef.id.desc()).limit(limit)).all()
    labels = {a.id: a.label for a in session.exec(select(MailAccount)).all()}
    return {"returned": len(rows), "messages": [message_out(m, labels) for m in rows]}


def fetch_parsed(session: Session, message_id: int):  # noqa: ANN201
    """Fetch one message live, read-only, and parse it. Raises HTTPException with the reason."""
    ref = session.get(MessageRef, message_id)
    if ref is None:
        raise HTTPException(404, "No such message")
    account = session.get(MailAccount, ref.account_id)
    if account is None or not account.active:
        raise HTTPException(409, "That mailbox is no longer connected")
    if not safe_folder(ref.folder):
        raise HTTPException(400, "This message's folder name cannot be opened safely; set the mailbox's folders again")
    client = mail_connector().client_factory(account.host, account.port, account.username, account_password(account), account.use_ssl, account.starttls)
    try:
        client.connect()
        client.select_readonly(ref.folder)
        parsed = parse_message(client.fetch_raw(ref.uid))
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, f"Could not fetch the message: {type(error).__name__}") from error
    finally:
        client.close()
    return ref, account, parsed


TEXT_TYPES = ("text/plain", "text/csv", "text/html", "text/markdown", "application/json")
MAX_ATTACHMENT_CHARS = 60_000


def attachment_out(session: Session, message_id: int, name: str) -> dict:
    """One attachment's text, boxed as untrusted data: PDFs through the PDF reader, text as it is, anything
    else by name, type and size only. Never the bytes."""
    from ..parsers.paystub import pdf_text

    ref, account, parsed = fetch_parsed(session, message_id)
    attachment = next((a for a in parsed.attachments if a.name == name), None)
    if attachment is None:
        raise HTTPException(404, f"No attachment called {name!r}; the message lists {[a.name for a in parsed.attachments]}")
    is_pdf = attachment.content_type == "application/pdf" or attachment.name.lower().endswith(".pdf")
    text = None
    if attachment.data is not None:
        if is_pdf:
            text = pdf_text(attachment.data)
        elif attachment.content_type in TEXT_TYPES or attachment.name.lower().endswith((".txt", ".csv", ".md", ".json")):
            text = attachment.data.decode("utf-8", errors="replace")
    truncated = text is not None and len(text) > MAX_ATTACHMENT_CHARS
    return {
        "message_id": ref.id, "name": attachment.name, "content_type": attachment.content_type, "size": attachment.size,
        "readable": text is not None,
        "boxed": box_untrusted(text[:MAX_ATTACHMENT_CHARS] if text is not None else "", f"attachment {attachment.name} from {parsed.sender_name or parsed.sender} ({account.label})") if text is not None else None,
        "truncated": truncated,
        "note": None if text is not None else "Only PDFs and text attachments are read; this one is listed by name, type and size.",
    }


@router.get("/mail/messages/{message_id}/attachments/{name}")
def read_attachment(message_id: int, name: str, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return attachment_out(session, message_id, name)


@router.get("/mail/messages/{message_id}")
def read_message(message_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    ref, account, parsed = fetch_parsed(session, message_id)
    facts = session.exec(select(ExtractedFact).where(ExtractedFact.message_ref_id == ref.id)).all()
    labels = {account.id: account.label}
    return {
        **message_out(ref, labels),
        "boxed": box_untrusted(parsed.text, f"{parsed.sender_name or parsed.sender} ({account.label})"),
        "attachments": [{"name": a.name, "content_type": a.content_type, "size": a.size} for a in parsed.attachments],
        "facts": [fact_out(f, ref) for f in facts],
    }


# ---- facts ---------------------------------------------------------------------------------


def fact_out(f: ExtractedFact, ref: MessageRef | None) -> dict:
    return {
        "id": f.id, "kind": f.kind, "fields": json.loads(f.fields or "{}"), "confidence": f.confidence, "status": f.status, "excerpt": f.excerpt, "produced_by": f.produced_by,
        "applied_to": f.applied_to, "created_at": f.created_at.isoformat(),
        "message": {"id": ref.id, "sender": ref.sender, "sender_name": ref.sender_name, "subject": ref.subject, "date": ref.date.isoformat() if ref.date else None,
                    "trust": ref.trust, "trust_why": ref.trust_why} if ref else None,
    }


def list_facts_rows(session: Session, status: str | None, limit: int) -> list[dict]:
    query = select(ExtractedFact)
    if status:
        query = query.where(ExtractedFact.status == status)
    newest = ExtractedFact.resolved_at.desc() if status in ("applied", "confirmed") else ExtractedFact.created_at.desc()  # answered: the latest answer first
    rows = session.exec(query.order_by(newest, ExtractedFact.id.desc()).limit(limit)).all()
    refs = {r.id: r for r in session.exec(select(MessageRef).where(MessageRef.id.in_([f.message_ref_id for f in rows if f.message_ref_id]))).all()} if rows else {}
    out = [fact_out(f, refs.get(f.message_ref_id)) for f in rows]
    notices = [(item, f) for item, f in zip(out, rows) if f.kind in ("bill_due", "past_due", "price_change") and f.status == "pending"]
    if notices:
        # A notice from someone counted another way says where, so confirming it does not count it twice.
        from ..engine.needsyou import counted_elsewhere, elsewhere_index, notice_name
        from ..engine.recurring import bill_named

        index = elsewhere_index(session)
        bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
        for item, f in notices:
            name = notice_name(json.loads(f.fields or "{}"), refs.get(f.message_ref_id))
            bill = bill_named(bills, merchant_key(name), loose=True)
            if bill is not None and bill.home_id:
                item["counted_elsewhere"] = f"{bill.name} is made from Housing, where the rent is changed"
            elif bill is None:
                item["counted_elsewhere"] = counted_elsewhere(index, name)
    for item, f in zip(out, rows):
        if f.kind == "receipt" and f.status == "pending":
            from ..engine.receipts import candidates

            got = json.loads(f.fields or "{}")
            item["candidates"] = candidates(session, got.get("total"), date.fromisoformat(got["bought_on"]) if got.get("bought_on") else None, got.get("merchant", ""))
    return out


@router.get("/facts")
def list_facts(status: str | None = "pending", limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = list_facts_rows(session, status, limit)
    if status in ("applied", "confirmed"):
        for item in rows:  # who answered, worded for the screen, and whether Undo is worth offering
            fact = session.get(ExtractedFact, item["id"])
            item["resolved_by"] = facts.worded(session, fact.resolved_by, principal)
            item["resolved_at"] = fact.resolved_at.isoformat() if fact.resolved_at else None
            item["undoable"] = facts.undoable(fact)
    pending = len(session.exec(select(ExtractedFact.id).where(ExtractedFact.status == "pending")).all())
    return {"facts": rows, "pending": pending}


class ConfirmIn(In):
    apply: bool = True
    name: str | None = None
    amount: float | None = None
    due_date: date | None = None
    autopay: bool | None = None
    remaining: int | None = None
    transaction_id: int | None = None  # a receipt: the purchase it belongs to (none: the best match, or kept unmatched)
    no_transaction: bool = False  # a receipt kept without a purchase, whatever matches
    terms: dict | None = None  # a document's terms: {key: corrected value, or null to leave it out}
    return_by: date | None = None
    warranty_until: date | None = None


def refuse_forged(ref: MessageRef | None, *, owner: bool) -> None:
    """A message judged forged (mail/trust.py) waits for the owner: a key or a connected app never confirms what it
    says, since the model behind it may be reading that very message's instructions."""
    if not owner and ref is not None and ref.trust == "crit":
        raise HTTPException(403, "Only the owner confirms what a message that looks forged says; it waits on Needs you with the reason")


def apply_fact(session: Session, fact: ExtractedFact, ref: MessageRef | None, body: ConfirmIn, *, owner: bool = False) -> str:
    """What a confirmed fact changes in the registers. Returns the applied_to tag.

    Anyone but the owner (a key or a connected app with the confirm scope) confirms what the message said and no
    more: no export is fetched, no name is overridden, an amount may move a quarter at most and a date six weeks
    at most, and only a bill of exactly the same name is updated."""
    fields = json.loads(fact.fields or "{}")
    refuse_forged(ref, owner=owner)
    if not owner:
        if fact.kind == "export":
            raise HTTPException(403, "Only the owner, signed in, imports an export from mail")
        if body.name:
            raise HTTPException(403, "Only the owner renames what a message says")
        said = fields.get("amount") if fields.get("amount") is not None else fields.get("net")
        if body.amount is not None and (not isinstance(said, (int, float)) or said <= 0 or not (0.75 * said <= body.amount <= 1.25 * said)):
            raise HTTPException(403, "Only the owner changes an amount by more than a quarter of what the message said")
        said_due = fields.get("due_date") or fields.get("pay_date")
        if body.due_date is not None and (not said_due or abs((body.due_date - date.fromisoformat(str(said_due)[:10])).days) > 45):
            raise HTTPException(403, "Only the owner moves a date by more than six weeks from what the message said")
    if fact.kind == "document_terms":
        from ..engine.documents import apply_terms

        if not owner:
            raise HTTPException(403, "Only the owner, signed in, applies the terms a document states")
        applied = apply_terms(session, fields, body.terms)
        if not applied:
            raise HTTPException(400, "The home this document belongs to is gone")
        return applied
    if fact.kind == "receipt":
        return _apply_receipt(session, fact, ref, fields, body)
    if fact.kind == "paystub":
        from ..engine.paystubs import record_paystub

        net = body.amount if body.amount is not None else fields.get("net")
        pay_date = body.due_date or (date.fromisoformat(fields["pay_date"]) if fields.get("pay_date") else None)
        if net is None or pay_date is None:
            raise HTTPException(400, "A paystub needs a net amount and a pay date; add them to the confirmation")
        stub = record_paystub(session, fields={**fields, "net": net, "pay_date": pay_date.isoformat()}, source=f"mail:{ref.id}" if ref else "fact", fact_id=fact.id, today=today())
        _keep_paystub_file(session, ref, fields, stub.id, pay_date)
        return f"paystub:{stub.id}"
    if fact.kind == "investment_balance":
        from ..connectors.banks import record_balance
        from ..engine.investments import SUBTYPE_WORDS

        balance = body.amount if body.amount is not None else fields.get("balance")
        if balance is None:
            raise HTTPException(400, "A statement needs the balance; add it to the confirmation")
        vendor = (ref.sender_name if ref else "") or fields.get("sender_name") or "Vendor"
        name = (body.name or "").strip() or f"{vendor} {fields.get('what') or 'account'}".strip()
        as_of = body.due_date or (date.fromisoformat(fields["as_of"]) if fields.get("as_of") else today())
        key = merchant_key(name)
        accounts = session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()  # noqa: E712
        account = next((a for a in accounts if merchant_key(a.name) == key or (a.institution and merchant_key(a.institution) == merchant_key(vendor) and (fields.get("what") or "") in (SUBTYPE_WORDS.get(a.subtype, ""), ""))), None)
        if account is None:
            subtype = next((k for k, w in SUBTYPE_WORDS.items() if k and w == fields.get("what")), "other")
            account = Account(name=name, institution=vendor, kind="investment", subtype=subtype, connector="manual", external_id=f"statement:{key}")
            session.add(account)
            session.flush()
        when = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
        if account.balance_at is None or when >= account.balance_at:
            account.balance, account.balance_at = round(float(balance), 2), when
        session.add(account)
        session.flush()
        record_balance(session, account, float(balance), as_of, commit=False)
        return f"account:{account.id}"
    if fact.kind == "installment" and fields.get("notice") in ("autopay", "approved"):
        from ..engine.borrowing import apply_lender_notice

        try:
            plan = apply_lender_notice(session, fields, today())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return f"plan:{plan.id}"
    if fact.kind == "installment":
        from ..engine.borrowing import plan_from_fields

        plan = plan_from_fields(session, fields, body.amount, body.remaining, today())
        if plan is None:
            raise HTTPException(400, "A plan needs the lender's schedule, or an amount and the payments left; add them to the confirmation")
        return f"plan:{plan.id}"
    if fact.kind == "export":
        from ..connectors.mail import capture_export

        url = fields.get("url")
        if not url:
            raise HTTPException(400, "This message has no download link")
        result = capture_export(session, url, mail_connector().fetch)
        if not result.get("imported") and not result.get("duplicates"):
            raise HTTPException(400, result.get("note") or "The export could not be imported")
        return f"import:{result.get('note', '')}"[:120]
    if fact.kind in ("bill_due", "past_due", "price_change"):
        from ..engine.needsyou import notice_name

        name = notice_name(fields, ref, body.name)
        amount = body.amount if body.amount is not None else fields.get("amount")
        due = body.due_date or (date.fromisoformat(fields["due_date"]) if fields.get("due_date") else None)
        key = merchant_key(name)
        bills = session.exec(select(Bill).where(Bill.active == True)).all()  # noqa: E712
        from ..engine.recurring import bill_named

        bill = bill_named(bills, key, loose=owner)  # a key changes only the bill of exactly that name
        if bill is not None and bill.home_id:
            raise HTTPException(400, f"{bill.name} is made from Housing; change the rent there, or discard this notice")
        if bill is None:
            if amount is None or due is None:
                raise HTTPException(400, "A new bill needs an amount and a due date; add them to the confirmation")
            bill = Bill(name=name, amount=round(float(amount), 2), amount_mode="estimate", due_day=due.day, anchor_date=due, autopay=bool(body.autopay), movable=True, move_window="1-28", category="", created_on=today())
            session.add(bill)
            session.flush()
        else:
            if amount is not None:
                bill.amount = round(float(amount), 2)
            if due is not None:
                bill.due_day = due.day
            if body.autopay is not None:
                bill.autopay = body.autopay
            session.add(bill)
        return f"bill:{bill.id}"
    return ""


def _keep_paystub_file(session: Session, ref: MessageRef | None, fields: dict, stub_id: int, pay_date: date) -> None:
    """The paystub's PDF kept with its month: the one handed over is already kept; one from mail is fetched now."""
    from ..engine import documents as docs
    from ..models import Document

    if fields.get("document_id"):
        doc = session.get(Document, int(fields["document_id"]))
        if doc is not None:
            docs.update_document(session, doc, {"attach_to": f"paystub:{stub_id}", "period": pay_date.isoformat()[:7]})
        return
    if ref is None or not fields.get("attachment"):
        return
    try:
        _ref, _account, parsed = fetch_parsed(session, ref.id)
    except HTTPException:
        return  # the mailbox is away; the stub counts all the same, and the file can be added by hand
    attachment = next((a for a in parsed.attachments if a.name == fields["attachment"] and a.data), None)
    if attachment is None:
        return
    try:
        docs.save_document(session, attachment.data, filename=attachment.name, title=f"Paystub · {pay_date:%b} {pay_date.day}, {pay_date.year}", kind="paystub",
                           attach_to=f"paystub:{stub_id}", period=pay_date.isoformat()[:7], source=f"mail:{ref.id}")
    except ValueError:
        return


def _apply_receipt(session: Session, fact: ExtractedFact, ref: MessageRef | None, fields: dict, body: ConfirmIn) -> str:
    """A receipt kept with its purchase: the message's PDF, or its words, fetched live once, read-only."""
    from ..engine import documents as docs
    from ..engine.receipts import candidates
    from ..models import Transaction

    bought = date.fromisoformat(fields["bought_on"]) if fields.get("bought_on") else today()
    total = body.amount if body.amount is not None else fields.get("total")
    tid = None if body.no_transaction else body.transaction_id
    if tid is None and not body.no_transaction:
        found = candidates(session, total, bought, fields.get("merchant", ""), limit=1)
        tid = found[0]["id"] if found and not found[0]["has_receipt"] else None
    if tid is not None and session.get(Transaction, tid) is None:
        raise HTTPException(400, "No such transaction")
    facts = {"merchant": (body.name or fields.get("merchant") or "").strip(), "amount": total, "bought_on": bought,
             "return_by": body.return_by or (date.fromisoformat(fields["return_by"]) if fields.get("return_by") else None),
             "warranty_until": body.warranty_until or (date.fromisoformat(fields["warranty_until"]) if fields.get("warranty_until") else None),
             "source": f"mail:{ref.id}" if ref else "fact"}
    title = f"Receipt · {facts['merchant'] or 'store'}" + (f" · order {fields['order_no']}" if fields.get("order_no") else "")
    attach = f"transaction:{tid}" if tid else ""
    doc = None
    if ref is not None:
        try:
            _ref, _account, parsed = fetch_parsed(session, ref.id)
            pdf = next((a for a in parsed.attachments if a.data and (a.content_type == "application/pdf" or a.name.lower().endswith(".pdf"))), None)
            if pdf is not None:
                doc = docs.save_document(session, pdf.data, filename=pdf.name, title=title, kind="receipt", attach_to=attach, **facts)
            else:
                doc = docs.save_text(session, f"{parsed.subject}\nFrom: {parsed.sender_name or parsed.sender}\n\n{parsed.text}", filename=f"receipt-{fact.id}", title=title, kind="receipt", attach_to=attach, **facts)
        except (HTTPException, ValueError):
            doc = None
    if doc is None:
        note = "The message could not be fetched just now; these are the words the app kept from it. Open it in Mail to see it whole."
        doc = docs.save_text(session, fact.excerpt or title, filename=f"receipt-{fact.id}", title=title, kind="receipt", attach_to=attach, note=note, **facts)
    return f"document:{doc.id}"


@router.post("/facts/{fact_id}/confirm")
def confirm_fact(fact_id: int, body: ConfirmIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    from ..jobs.writing import one_writer

    with one_writer():  # one confirmation at a time, and never beside a detection or a sync writing the same registers
        fact = session.get(ExtractedFact, fact_id)
        if fact is None:
            raise HTTPException(404, "No such fact")
        session.refresh(fact)
        if fact.status != "pending":
            raise HTTPException(409, f"This one was {fact.status} already; nothing changed.")
        ref = session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None
        refuse_forged(ref, owner=principal.full)
        b = facts.watermark(session)
        applied = apply_fact(session, fact, ref, body, owner=principal.full) if body.apply else ""
        facts.resolve_fact(session, fact, "applied" if applied else "confirmed", applied_to=applied, by=facts.who(principal), before=b)
        session.commit()
        session.refresh(fact)
    if applied:
        from ..llm.reviewer import mark_pending

        mark_pending(session, f"{fact.kind} confirmed")
    out = fact_out(fact, ref)
    if applied.startswith("paystub:"):
        from ..models import Income

        if session.exec(select(Income).where(Income.active == True)).first() is None:  # noqa: E712
            out["no_paycheck"], out["paystub_id"] = True, int(applied.split(":")[1])  # the screen offers it as the paycheck
    return out


@router.post("/facts/{fact_id}/unconfirm")
def unconfirm(fact_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> dict:
    """Take a confirmation back: a person signed in only, never a key or a connected app."""
    from ..jobs.writing import one_writer

    with one_writer():  # claimed as a confirmation claims it, so nothing confirms or syncs beside it
        fact = session.get(ExtractedFact, fact_id)
        if fact is None:
            raise HTTPException(404, "No such fact")
        session.refresh(fact)
        tag = fact.applied_to
        try:
            out = facts.unconfirm_fact(session, fact_id, by=facts.who(principal))
        except ValueError as error:
            session.rollback()
            services.audit(session, client=principal.name, client_kind=principal.kind, tool="facts_unconfirm_refused", args={"fact_id": fact_id}, rows=0)
            raise HTTPException(409, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="facts_unconfirm", args={"fact_id": fact_id, "undone": tag}, rows=1)
    if tag:
        from ..llm.reviewer import mark_pending

        mark_pending(session, f"{fact.kind} confirmation taken back")
    return {"ok": True, "said": out["said"], "undone": out["undone"]}


@router.post("/facts/{fact_id}/reject")
def reject_fact(fact_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_scope("confirm"))) -> dict:
    from ..jobs.writing import one_writer

    with one_writer():
        fact = session.get(ExtractedFact, fact_id)
        if fact is None:
            raise HTTPException(404, "No such fact")
        session.refresh(fact)
        if fact.status != "pending":
            raise HTTPException(409, f"This one was {fact.status} already; nothing changed.")
        facts.resolve_fact(session, fact, "rejected", by=facts.who(principal))
        session.commit()
    return {"ok": True}
