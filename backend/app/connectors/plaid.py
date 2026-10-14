"""Plaid on the Trial plan: the bank's own login page in Plaid Link, then transactions and balances, free for
the first ten logins. The owner pastes the client id and secret on the Connectors page; the app never sees a
bank password.

Link: the app makes a link token, Plaid Link runs in the browser, the public token comes back and is
exchanged for an access token, which is stored encrypted. Sync: `/transactions/sync` with a cursor, so each
run takes only what changed; `/accounts/get` for balances.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..models import BankLink
from .banks import PLAID_CATEGORIES, PLAID_DETAILED, PLAID_KINDS, SAVINGS_SUBTYPES, bank_settings, finish_link, link_token, remove_transaction, upsert_account, upsert_transaction
from .base import Connector, SyncResult

log = logging.getLogger("fa.plaid")

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
PLAID_VERSION = "2020-09-14"
ClientFactory = Callable[[], httpx.Client]


class PlaidError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class PlaidClient:
    def __init__(self, client_id: str, secret: str, environment: str, client_factory: ClientFactory | None = None) -> None:
        self.client_id, self.secret = client_id, secret
        self.host = HOSTS.get(environment, HOSTS["sandbox"])
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)))

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = {"client_id": self.client_id, "secret": self.secret, **body}
        with self.client_factory() as client:
            response = client.post(f"{self.host}{path}", json=payload, headers={"Content-Type": "application/json", "Plaid-Version": PLAID_VERSION})
        try:
            data = response.json()
        except ValueError as error:
            raise PlaidError("BAD_RESPONSE", f"{response.status_code}: {response.text[:200]}") from error
        if response.status_code >= 400 or data.get("error_code"):
            raise PlaidError(str(data.get("error_code") or response.status_code), str(data.get("display_message") or data.get("error_message") or "Plaid refused the request"))
        return data

    def link_token(self, *, public_url: str, access_token: str | None = None) -> str:
        body: dict[str, Any] = {
            "client_name": "FinancialAuthority", "language": "en", "country_codes": ["US"], "user": {"client_user_id": "owner"},
        }
        if access_token:
            body["access_token"] = access_token  # update mode: the owner signs in again on the same item
        else:
            body["products"] = ["transactions"]
            body["transactions"] = {"days_requested": 730}
        if public_url.startswith("https://"):
            body["redirect_uri"] = public_url.rstrip("/") + "/settings/connectors"
        return str(self.post("/link/token/create", body)["link_token"])

    def exchange(self, public_token: str) -> tuple[str, str]:
        data = self.post("/item/public_token/exchange", {"public_token": public_token})
        return str(data["access_token"]), str(data["item_id"])

    def accounts(self, access_token: str) -> dict[str, Any]:
        return self.post("/accounts/get", {"access_token": access_token})

    def sync_page(self, access_token: str, cursor: str) -> dict[str, Any]:
        body: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor:
            body["cursor"] = cursor
        return self.post("/transactions/sync", body)

    def remove(self, access_token: str) -> None:
        self.post("/item/remove", {"access_token": access_token})


def account_kind(plaid_type: str, subtype: str | None) -> str:
    if plaid_type == "depository" and (subtype or "").lower() in SAVINGS_SUBTYPES:
        return "savings"
    return PLAID_KINDS.get(plaid_type, "checking")


def category_for(tx: dict[str, Any]) -> str:
    pfc = tx.get("personal_finance_category") or {}
    detailed, primary = str(pfc.get("detailed") or ""), str(pfc.get("primary") or "")
    if detailed in PLAID_DETAILED:
        return PLAID_DETAILED[detailed]
    if primary in PLAID_CATEGORIES:
        return PLAID_CATEGORIES[primary]
    legacy = tx.get("category") or []
    return str(legacy[-1]) if legacy else "Uncategorized"


class PlaidConnector(Connector):
    id = "plaid"
    title = "Plaid, Trial plan"
    yields = "Live transactions and balances from any bank or credit union Plaid supports, through the bank's own login page. Free for the first ten logins."
    settings_schema = [
        {"name": "client_id", "label": "Client id", "help": "Plaid dashboard › Keys"},
        {"name": "secret", "label": "Secret", "secret": True, "help": "The sandbox or production secret for the Trial plan"},
        {"name": "environment", "label": "Environment", "help": "sandbox (test banks) or production (real banks)"},
    ]

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self.client_factory = client_factory

    def api(self, session: Session) -> PlaidClient | None:
        s = bank_settings(session, "plaid")
        if not (s["client_id"] and s["secret"]):
            return None
        return PlaidClient(s["client_id"], s["secret"], s["environment"] or "sandbox", self.client_factory)

    def sync(self, session: Session, today: date | None = None, link_id: int | None = None, **_: object) -> SyncResult:
        api = self.api(session)
        if api is None:
            return SyncResult(ok=False, summary="Plaid is not configured")
        query = select(BankLink).where(BankLink.provider == "plaid", BankLink.active == True)  # noqa: E712
        if link_id is not None:
            query = query.where(BankLink.id == link_id)
        links = session.exec(query).all()
        if not links:
            return SyncResult(ok=True, summary="no Plaid links")
        inserted = updated = removed = 0
        notes: list[str] = []
        ok = True
        for link in links:
            try:
                n_ins, n_upd, n_rem, account_ids = self.sync_link(session, api, link)
                inserted, updated, removed = inserted + n_ins, updated + n_upd, removed + n_rem
                finish_link(session, link, ok=True, accounts=account_ids)
                notes.append(f"{link.institution}: {n_ins} new, {n_upd} changed, {n_rem} removed")
            except PlaidError as error:
                ok = False
                if error.code == "ITEM_LOGIN_REQUIRED":
                    link.needs_login = True
                finish_link(session, link, ok=False, error=str(error))
                notes.append(f"{link.institution}: {error}")
            except httpx.HTTPError as error:
                ok = False
                finish_link(session, link, ok=False, error=f"{type(error).__name__}: {error}")
                notes.append(f"{link.institution}: {type(error).__name__}")
        return SyncResult(ok=ok, summary="; ".join(notes), inserted=inserted, details={"updated": updated, "removed": removed})

    def sync_link(self, session: Session, api: PlaidClient, link: BankLink) -> tuple[int, int, int, list[str]]:
        token = link_token(link)
        got = api.accounts(token)
        institution = link.institution or str((got.get("item") or {}).get("institution_name") or "")
        accounts = {}
        for a in got.get("accounts", []):
            balances = a.get("balances") or {}
            current = balances.get("current")
            accounts[a["account_id"]] = upsert_account(
                session, provider="plaid", external_id=a["account_id"], name=str(a.get("name") or a.get("official_name") or "Account"), institution=institution,
                kind=account_kind(str(a.get("type") or ""), a.get("subtype")), balance=float(current) if current is not None else None, last4=str(a.get("mask") or ""),
            )
        inserted = updated = removed = 0
        cursor = link.cursor or ""
        for _page in range(50):
            page = api.sync_page(token, cursor)
            for tx in page.get("added", []) + page.get("modified", []):
                if tx.get("pending"):
                    continue
                account = accounts.get(tx["account_id"])
                if account is None:
                    continue
                new = upsert_transaction(
                    session, provider="plaid", tx_id=tx["transaction_id"], account=account, when=date.fromisoformat(tx["date"]), amount=float(tx["amount"]),
                    merchant=str(tx.get("merchant_name") or tx.get("name") or ""), raw_name=str(tx.get("name") or ""), category=category_for(tx),
                    posted=date.fromisoformat(tx["date"]),
                )
                inserted, updated = inserted + int(new), updated + int(not new)
            for tx in page.get("removed", []):
                removed += int(remove_transaction(session, "plaid", tx["transaction_id"]))
            cursor = str(page.get("next_cursor") or cursor)
            if not page.get("has_more"):
                break
        link.cursor = cursor
        link.needs_login = False
        session.commit()
        return inserted, updated, removed, list(accounts)

    def connect(self, session: Session, api: PlaidClient, public_token: str, institution: str) -> BankLink:
        access_token, item_id = api.exchange(public_token)
        link = session.exec(select(BankLink).where(BankLink.provider == "plaid", BankLink.external_id == item_id)).first()
        if link is None:
            link = BankLink(provider="plaid", external_id=item_id, institution=institution)
        link.institution = institution or link.institution
        link.access_token_enc = crypto.encrypt(access_token)
        link.active = True
        link.needs_login = False
        link.cursor = ""
        session.add(link)
        session.commit()
        session.refresh(link)
        return link

    def disconnect(self, session: Session, api: PlaidClient | None, link: BankLink) -> None:
        if api is not None:
            try:
                api.remove(link_token(link))
            except (PlaidError, httpx.HTTPError, ValueError) as error:
                log.warning("item remove failed for %s: %s", link.institution, error)
        link.active = False
        link.access_token_enc = ""
        session.add(link)
        session.commit()
