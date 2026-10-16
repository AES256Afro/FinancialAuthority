"""Teller on the developer tier: the bank's own login in Teller Connect, then accounts, balances and
transactions, free for a hundred enrollments. The owner pastes the application id and the application
certificate on the Connectors page; the app never sees a bank password.

Teller's API wants the application certificate as a client certificate and the enrollment's access token as
the user of HTTP basic auth, with no password. The certificate and key are stored encrypted and written to
the data directory, readable by the app alone, only when a call is made.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..config import settings
from ..models import BankLink, Transaction
from .banks import SAVINGS_SUBTYPES, TELLER_CATEGORIES, bank_settings, finish_link, link_token, remove_transaction, upsert_account, upsert_transaction
from .base import Connector, SyncResult

log = logging.getLogger("fa.teller")

BASE = "https://api.teller.io"
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
        with self.client(access_token) as client:
            response = client.get(path, params=params)
        if response.status_code >= 400:
            try:
                err = response.json().get("error") or {}
            except ValueError:
                err = {}
            raise TellerError(str(err.get("code") or response.status_code), str(err.get("message") or response.text[:200] or "Teller refused the request"))
        return response.json()

    def accounts(self, access_token: str) -> list[dict[str, Any]]:
        return list(self.get(access_token, "/accounts"))

    def balances(self, access_token: str, account_id: str) -> dict[str, Any]:
        return dict(self.get(access_token, f"/accounts/{account_id}/balances"))

    def transactions(self, access_token: str, account_id: str, count: int = 500) -> list[dict[str, Any]]:
        return list(self.get(access_token, f"/accounts/{account_id}/transactions", {"count": count}))


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
            try:
                n_ins, n_upd, account_ids = self.sync_link(session, api, link)
                inserted, updated = inserted + n_ins, updated + n_upd
                flush(session)
                finish_link(session, link, ok=True, accounts=account_ids)
                notes.append(f"{link.institution}: {n_ins} new, {n_upd} changed")
            except Exception as error:  # noqa: BLE001 - one link's failure is recorded on it and never stops the others
                session.rollback()
                ok = False
                if isinstance(error, TellerError) and error.code in ("enrollment.disconnected", "401", "unauthorized"):
                    link.needs_login = True
                message = str(error) if isinstance(error, TellerError) else f"{type(error).__name__}: {error}"
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
        for a in api.accounts(token):
            if link.external_id and a.get("enrollment_id") and a["enrollment_id"] != link.external_id:
                continue
            fetched.append((a, api.balances(token, a["id"]), api.transactions(token, a["id"])))
        for a, balances, txs in fetched:
            institution = str((a.get("institution") or {}).get("name") or link.institution)
            kind = account_kind(str(a.get("type") or ""), str(a.get("subtype") or ""))
            # What can be spent is the available balance (pending charges already out); a card owes its ledger.
            available, ledger = balances.get("available"), balances.get("ledger")
            chosen = available if (kind in ("checking", "savings", "cash") and available not in (None, "")) else ledger
            balance = float(chosen) if chosen not in (None, "") else None
            if kind == "card" and balance is not None:
                balance = -balance  # Teller gives what a card owes as negative; a card in credit stays a credit
            account = upsert_account(session, provider="teller", external_id=a["id"], name=str(a.get("name") or "Account"), institution=institution, kind=kind, balance=balance, last4=str(a.get("last_four") or ""))
            ids.append(a["id"])
            for tx in txs:
                pending = str(tx.get("status") or "posted") == "pending"  # the same id posts later; the row is updated in place
                raw = float(tx["amount"])
                amount = -raw if DEBITS_NEGATIVE else raw
                details = tx.get("details") or {}
                counterparty = (details.get("counterparty") or {}).get("name")
                new = upsert_transaction(
                    session, provider="teller", tx_id=tx["id"], account=account, when=date.fromisoformat(tx["date"]), amount=amount,
                    merchant=str(counterparty or tx.get("description") or ""), raw_name=str(tx.get("description") or ""), category=category_for(tx), pending=pending,
                )
                inserted, updated = inserted + int(new), updated + int(not new)
            # A pending charge the bank has dropped (a hold released, an authorisation that never posted) is gone
            # from what it sends; it leaves the ledger too, or it would stay off the balance for good.
            sent = {str(tx["id"]) for tx in txs}
            for row in session.exec(select(Transaction).where(Transaction.account_id == account.id, Transaction.source == "teller", Transaction.pending == True)).all():  # noqa: E712
                if row.source_ref not in sent:
                    remove_transaction(session, "teller", row.source_ref)
        link.needs_login = False
        session.commit()
        return inserted, updated, ids

    def connect(self, session: Session, access_token: str, enrollment_id: str, institution: str) -> BankLink:
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
