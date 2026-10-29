"""Teller on the developer tier: the bank's own login in Teller Connect, then accounts, balances and
transactions, free for a hundred enrollments. The owner pastes the application id and the application
certificate on the Connectors page; the app never sees a bank password.

Teller's API wants the application certificate as a client certificate and the enrollment's access token as
the user of HTTP basic auth, with no password. The certificate and key are stored encrypted and written to
the data directory, readable by the app alone, only when a call is made.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..config import settings
from ..jobs.writing import one_writer
from ..models import BankLink, Transaction
from .banks import (
    MAX_NAME_CHARS,
    MAX_RESPONSE_BYTES,
    SAVINGS_SUBTYPES,
    SEEN,
    TELLER_CATEGORIES,
    VendorAnswer,
    bank_settings,
    clean_id,
    clean_text,
    duplicate_of,
    finish_link,
    finite_amount,
    hide_secrets,
    iso_day,
    link_token,
    read_bounded,
    remove_transaction,
    upsert_account,
    upsert_transaction,
)
from .base import Connector, SyncResult

log = logging.getLogger("fa.teller")

BASE = "https://api.teller.io"
REQUEST_SECONDS = 120.0  # one call, from the request to the last byte of the answer, whatever the socket allows
LINK_NOTES = "fa_link_notes"  # session.info: what a link sync has to say beyond its counts
# Teller reports a purchase as a negative amount; the ledger keeps money out positive.
DEBITS_NEGATIVE = True
ClientFactory = Callable[[str], httpx.Client]


class TellerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


_FOLDER: Path | None = None


def cert_files(certificate: str, private_key: str) -> tuple[str, str] | None:
    """The PEMs on disk for the HTTP client, rewritten whenever their content changes. They are written to a
    private folder outside the data directory (so no backup or restore point carries the key in the clear); the
    encrypted copy in the database is the one that is kept."""
    if not (certificate.strip() and private_key.strip()):
        return None
    old = Path(settings.data_dir) / "teller"
    for leftover in (old / "private_key.pem", old / "certificate.pem"):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
    import tempfile

    global _FOLDER
    if _FOLDER is None or not _FOLDER.is_dir():
        _FOLDER = Path(tempfile.mkdtemp(prefix="fa-teller-"))  # random name, made by this process, 0700
    folder = _FOLDER
    paths = (folder / "certificate.pem", folder / "private_key.pem")
    for path, content in zip(paths, (certificate, private_key), strict=True):
        text = content.strip() + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    return str(paths[0]), str(paths[1])


def forget_cert_files() -> None:
    """Remove the PEMs written for the HTTP client and their folder (an erase, or the certificate replaced)."""
    global _FOLDER
    folder = _FOLDER
    _FOLDER = None
    if folder is None:
        return
    import shutil

    shutil.rmtree(folder, ignore_errors=True)


class TellerClient:
    def __init__(self, certificate: str = "", private_key: str = "", client_factory: ClientFactory | None = None) -> None:
        self.cert = cert_files(certificate, private_key)
        self.client_factory = client_factory

    def client(self, access_token: str) -> httpx.Client:
        if self.client_factory is not None:
            return self.client_factory(access_token)
        kwargs: dict[str, Any] = {"auth": (access_token, ""), "timeout": httpx.Timeout(60.0, connect=10.0), "base_url": BASE}
        if self.cert:
            kwargs["cert"] = self.cert
        return httpx.Client(**kwargs)

    def get(self, access_token: str, path: str, params: dict[str, Any] | None = None) -> Any:
        from ..usage import note

        note("teller", path.split("?")[0])
        hidden = [access_token]  # what Teller might echo back and must never land in a record
        try:
            with self.client(access_token) as client, client.stream("GET", path, params=params) as response:
                status = response.status_code
                raw = read_bounded(response, max_bytes=MAX_RESPONSE_BYTES, deadline=time.monotonic() + REQUEST_SECONDS)
        except VendorAnswer as error:
            raise TellerError("BAD_RESPONSE", str(error)) from error
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if status >= 400:
            err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
            message = clean_text(err.get("message") or "Teller refused the request", 300)  # the body itself (HTML, a stack trace) stays out
            raise TellerError(clean_text(err.get("code") or status, 80), hide_secrets(message, hidden))
        if data is None:
            raise TellerError("BAD_RESPONSE", f"{status}: not JSON")
        return data

    def accounts(self, access_token: str) -> list[dict[str, Any]]:
        return _objects(self.get(access_token, "/accounts"))

    def balances(self, access_token: str, account_id: str) -> dict[str, Any]:
        got = self.get(access_token, f"/accounts/{account_id}/balances")
        return got if isinstance(got, dict) else {}

    def transactions(self, access_token: str, account_id: str, count: int = 500) -> list[dict[str, Any]]:
        return _objects(self.get(access_token, f"/accounts/{account_id}/transactions", {"count": count}))


def _objects(data: Any) -> list[dict[str, Any]]:
    """The objects of a list answer; an answer of another shape is an empty list, not a crash."""
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def account_kind(teller_type: str, subtype: str) -> str:
    if teller_type == "credit":
        return "card"
    if subtype in SAVINGS_SUBTYPES:
        return "savings"
    return "checking"


def category_for(tx: dict[str, Any]) -> str:
    details = tx.get("details") or {}
    cat = str(details.get("category") or "").lower()
    if cat in TELLER_CATEGORIES:
        return TELLER_CATEGORIES[cat]
    if str(tx.get("type") or "") == "transfer":
        return "Transfer"
    return "Uncategorized"


class TellerConnector(Connector):
    id = "teller"
    title = "Teller, developer tier"
    yields = "Live transactions and balances from the banks Teller supports, through the bank's own login page. Free for a hundred logins, rate limited."
    settings_schema = [
        {"name": "application_id", "label": "Application id", "help": "Teller dashboard › Application"},
        {"name": "certificate", "label": "Application certificate (PEM)", "secret": True},
        {"name": "private_key", "label": "Certificate private key (PEM)", "secret": True},
        {"name": "environment", "label": "Environment", "help": "sandbox, development (real banks, free) or production"},
    ]

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self.client_factory = client_factory

    def api(self, session: Session) -> TellerClient | None:
        s = bank_settings(session, "teller")
        if not s["application_id"]:
            return None
        return TellerClient(s["certificate"], s["private_key"], self.client_factory)

    def sync(self, session: Session, today: date | None = None, link_id: int | None = None, **_: object) -> SyncResult:
        api = self.api(session)
        if api is None:
            return SyncResult(ok=False, summary="Teller is not configured")
        query = select(BankLink).where(BankLink.provider == "teller", BankLink.active == True)  # noqa: E712
        if link_id is not None:
            query = query.where(BankLink.id == link_id)
        links = session.exec(query).all()
        if not links:
            return SyncResult(ok=True, summary="no Teller links")
        inserted = updated = 0
        notes: list[str] = []
        ok = True
        from ..usage import flush

        for link in links:
            with one_writer():  # one sync of a link at a time, whoever asks
                session.info.pop(SEEN, None)  # the accounts this link takes; every other link's are on its list
                try:
                    session.refresh(link)
                    if not link.active:
                        continue  # removed while this sync waited its turn
                    n_ins, n_upd, account_ids = self.sync_link(session, api, link)
                    inserted, updated = inserted + n_ins, updated + n_upd
                    flush(session)
                    finish_link(session, link, ok=True, accounts=account_ids)
                    notes.append(f"{link.institution}: {n_ins} new, {n_upd} changed" + "".join(f"; {n}" for n in session.info.pop(LINK_NOTES, [])))
                except Exception as error:  # noqa: BLE001 - one link's failure is recorded on it and never stops the others
                    session.rollback()
                    session.info.pop(LINK_NOTES, None)
                    session.info.pop(SEEN, None)
                    ok = False
                    if isinstance(error, TellerError) and error.code in ("enrollment.disconnected", "401", "unauthorized"):
                        link.needs_login = True
                    if isinstance(error, TellerError):
                        message = str(error)
                    elif isinstance(error, httpx.HTTPError):
                        # The network, not the bank: a timeout, a refused connection, a dropped reply. The next sync tries again.
                        message = f"Teller could not be reached ({type(error).__name__.replace('Error', '').lower() or 'network'}); the next sync tries again"
                    else:
                        message = f"{type(error).__name__}: {error}"
                    finish_link(session, link, ok=False, error=message[:300])
                    notes.append(f"{link.institution}: {message[:120]}")
        flush(session)
        session.commit()
        return SyncResult(ok=ok, summary="; ".join(notes), inserted=inserted, details={"updated": updated})

    def sync_link(self, session: Session, api: TellerClient, link: BankLink) -> tuple[int, int, list[str]]:
        token = link_token(link)
        inserted = updated = 0
        ids: list[str] = []
        # Fetch everything first, then write in one short transaction: the database is never locked while the app
        # waits on the network.
        fetched = []
        unreadable = 0  # accounts and rows the answer carried that are not ones: no id, no amount, no date
        for a in api.accounts(token):
            account_id = clean_id(a.get("id"))
            if account_id is None:
                unreadable += 1
                continue
            if link.external_id and a.get("enrollment_id") and a["enrollment_id"] != link.external_id:
                continue
            fetched.append(({**a, "id": account_id}, api.balances(token, account_id), api.transactions(token, account_id)))
        left_out_names: list[str] = []  # said once per sync, not once per account
        for a, balances, txs in fetched:
            institution = clean_text((a.get("institution") or {}).get("name") if isinstance(a.get("institution"), dict) else "", MAX_NAME_CHARS) or link.institution
            kind = account_kind(str(a.get("type") or ""), str(a.get("subtype") or ""))
            # What can be spent is the available balance (pending charges already out); a card owes its ledger.
            available, ledger = finite_amount(balances.get("available")), finite_amount(balances.get("ledger"))
            balance = available if (kind in ("checking", "savings", "cash") and available is not None) else ledger
            if kind == "card" and balance is not None:
                balance = -balance  # Teller gives what a card owes as negative; a card in credit stays a credit
            ids.append(a["id"])
            name = clean_text(a.get("name") or "Account", MAX_NAME_CHARS) or "Account"
            last4 = clean_text(a.get("last_four") or "", 8)
            if duplicate_of(session, link=link, external_id=a["id"], institution=institution, last4=last4) is not None:
                # The same account is read through another link to this bank: it stays with that one.
                left_out_names.append(name)
                continue
            account = upsert_account(session, provider="teller", external_id=a["id"], name=name, institution=institution, kind=kind, balance=balance, last4=last4)
            if not account.active:
                continue  # an account the owner removed gets no balance and no rows
            for tx in txs:
                tx_id, raw, when = clean_id(tx.get("id")), finite_amount(tx.get("amount")), iso_day(tx.get("date"))
                if tx_id is None or raw is None or when is None:
                    unreadable += 1  # a row without an id, an amount or a day is not a transaction; it is left out and said so
                    continue
                pending = str(tx.get("status") or "posted") == "pending"  # the same id posts later; the row is updated in place
                amount = -raw if DEBITS_NEGATIVE else raw
                details = tx.get("details") if isinstance(tx.get("details"), dict) else {}
                counterparty = (details.get("counterparty") or {}).get("name") if isinstance(details.get("counterparty"), dict) else None
                new = upsert_transaction(
                    session, provider="teller", tx_id=tx_id, account=account, when=when, amount=amount,
                    merchant=str(counterparty or tx.get("description") or ""), raw_name=str(tx.get("description") or ""), category=category_for(tx), pending=pending,
                )
                inserted, updated = inserted + int(new), updated + int(not new)
            # A pending charge the bank has dropped (a hold released, an authorisation that never posted) is gone
            # from what it sends; it leaves the ledger too, or it would stay off the balance for good.
            sent = {str(tx.get("id")) for tx in txs}
            for row in session.exec(select(Transaction).where(Transaction.account_id == account.id, Transaction.source == "teller", Transaction.pending == True)).all():  # noqa: E712
                if row.source_ref not in sent:
                    remove_transaction(session, "teller", row.source_ref)
        if left_out_names:
            from .plaid import left_out_note

            session.info.setdefault(LINK_NOTES, []).append(f"{left_out_note(left_out_names)} already read through your other {institution or 'bank'} link, so this link leaves {'it' if len(left_out_names) == 1 else 'them'} out; remove one of the two links")
        if unreadable:
            session.info.setdefault(LINK_NOTES, []).append(f"{unreadable} {'row' if unreadable == 1 else 'rows'} Teller sent could not be read (no id, amount or day) and were left out")
        link.needs_login = False
        session.commit()
        return inserted, updated, ids

    def connect(self, session: Session, access_token: str, enrollment_id: str, institution: str) -> BankLink:
        if clean_id(enrollment_id) is None or clean_id(access_token, 500) is None:
            raise ValueError("That is not a Teller enrollment")
        institution = clean_text(institution, MAX_NAME_CHARS)
        link = session.exec(select(BankLink).where(BankLink.provider == "teller", BankLink.external_id == enrollment_id)).first()
        if link is None:
            link = BankLink(provider="teller", external_id=enrollment_id, institution=institution)
        link.institution = institution or link.institution
        link.access_token_enc = crypto.encrypt(access_token)
        link.active = True
        link.needs_login = False
        session.add(link)
        session.commit()
        session.refresh(link)
        return link

    def disconnect(self, session: Session, link: BankLink) -> None:
        link.active = False
        link.access_token_enc = ""
        session.add(link)
        session.commit()
