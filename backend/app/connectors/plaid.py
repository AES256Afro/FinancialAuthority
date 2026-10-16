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
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..models import BankLink
from .banks import PLAID_CATEGORIES, PLAID_DETAILED, PLAID_KINDS, SAVINGS_SUBTYPES, bank_settings, finish_link, link_token, remove_transaction, upsert_account, upsert_holding, upsert_transaction
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
        self.environment = environment if environment in HOSTS else "sandbox"
        self.host = HOSTS[self.environment]
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)))

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        from ..usage import note

        if self.environment != "sandbox":
            note("plaid", path)
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

    def check_keys(self) -> dict[str, str]:
        """Whether the keys fit the chosen environment. Plaid issues one secret per environment under the same
        client id, so the usual slip is the sandbox secret with the environment set to production; the check
        names that, instead of leaving a bare "invalid keys"."""
        try:
            self.post("/link/token/create", {"user": {"client_user_id": "owner"}, "client_name": "FinancialAuthority", "products": ["transactions"], "country_codes": ["US"], "language": "en"})
            return {"result": "ok", "message": f"Plaid accepts the keys for {self.environment}."}
        except PlaidError as error:
            if error.code != "INVALID_API_KEYS":
                return {"result": "error", "message": str(error)[:200]}
        other = "sandbox" if self.environment == "production" else "production"
        try:
            PlaidClient(self.client_id, self.secret, other, self.client_factory).post("/link/token/create", {"user": {"client_user_id": "owner"}, "client_name": "FinancialAuthority", "products": ["transactions"], "country_codes": ["US"], "language": "en"})
        except PlaidError:
            return {"result": "invalid", "message": "Plaid does not accept this client id and secret in either environment. Copy both again from the Plaid dashboard (Developers, Keys)."}
        if self.environment == "production":
            return {"result": "sandbox_secret", "message": "The secret pasted is the sandbox one, and the environment is set to production. Paste the production secret from the Plaid dashboard (Developers, Keys), or set the environment back to sandbox for test banks. Production also needs Plaid's go-ahead for production access."}
        return {"result": "production_secret", "message": "The secret pasted is the production one, and the environment is set to sandbox. Set the environment to production, or paste the sandbox secret for test banks."}

    @staticmethod
    def redirect_uri(public_url: str) -> str | None:
        """Where an OAuth bank sends the browser back: the Connectors page at the published https address.
        Plaid accepts it only once it is registered in the Plaid dashboard."""
        return public_url.rstrip("/") + "/settings/connectors" if public_url.startswith("https://") else None

    def link_token(self, *, public_url: str, access_token: str | None = None, purpose: str = "bank", redirect: bool = True) -> str:
        """A Link token. `purpose` "bank" asks for transactions with investments optional; "investments" asks for
        the investments product first (a 401(k), an IRA, a brokerage account), with transactions optional, so
        a retirement vendor that has no transactions still links. `redirect` False leaves the OAuth redirect
        URI out, which lets banks that sign in without OAuth connect before the URI is registered."""
        body: dict[str, Any] = {
            "client_name": "FinancialAuthority", "language": "en", "country_codes": ["US"], "user": {"client_user_id": "owner"},
        }
        if access_token:
            body["access_token"] = access_token  # update mode: the owner signs in again on the same item
        elif purpose == "investments":
            body["products"] = ["investments"]
            body["optional_products"] = ["transactions"]
        else:
            body["products"] = ["transactions"]
            body["optional_products"] = ["investments"]
            body["transactions"] = {"days_requested": 730}
        uri = self.redirect_uri(public_url) if redirect else None
        if uri:
            body["redirect_uri"] = uri
        from .plaid_webhook import webhook_url

        hook = webhook_url(public_url)
        if hook and not access_token:
            body["webhook"] = hook  # new transactions are announced, so they arrive in minutes
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

    def webhook_key(self, key_id: str) -> dict[str, Any]:
        """The public key Plaid signs its notices with, by its id."""
        return dict(self.post("/webhook_verification_key/get", {"key_id": key_id})["key"])

    def set_webhook(self, access_token: str, url: str) -> None:
        """Tell Plaid where to send this item's notices."""
        self.post("/item/webhook/update", {"access_token": access_token, "webhook": url})

    def remove(self, access_token: str) -> None:
        self.post("/item/remove", {"access_token": access_token})

    def holdings(self, access_token: str) -> dict[str, Any]:
        return self.post("/investments/holdings/get", {"access_token": access_token})


def account_kind(plaid_type: str, subtype: str | None) -> str:
    if plaid_type == "depository" and (subtype or "").lower() in SAVINGS_SUBTYPES:
        return "savings"
    return PLAID_KINDS.get(plaid_type, "checking")


def investment_subtype(subtype: str | None) -> str:
    """Plaid's investment subtypes to the app's: 401k, 403b, 457b, ira, roth_ira, brokerage, hsa, 529, pension, crypto, other."""
    key = (subtype or "").lower().replace(" ", "_")
    if key in ("401k", "401a", "403b", "457b", "ira", "sep_ira", "simple_ira", "brokerage", "hsa", "529", "pension", "tsp", "profit_sharing_plan", "stock_plan", "crypto_exchange", "cash_management", "trust", "ugma", "utma", "roth", "roth_401k", "keogh", "sarsep", "non-custodial_wallet", "mutual_fund", "retirement", "education_savings_account"):
        return {"401a": "401k", "roth": "roth_ira", "roth_401k": "401k", "crypto_exchange": "crypto", "non-custodial_wallet": "crypto", "profit_sharing_plan": "pension", "mutual_fund": "brokerage", "education_savings_account": "529", "keogh": "pension", "sarsep": "sep_ira"}.get(key, key)
    return "other"


def security_kind(sec: dict[str, Any]) -> str:
    kind = str(sec.get("type") or "other").lower()
    return {"equity": "equity", "etf": "etf", "mutual fund": "mutual fund", "fixed income": "fixed income", "cash": "cash", "derivative": "derivative", "cryptocurrency": "cryptocurrency", "loan": "fixed income"}.get(kind, "other")


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
        from ..usage import flush

        for link in links:
            try:
                n_ins, n_upd, n_rem, account_ids = self.sync_link(session, api, link)
                inserted, updated, removed = inserted + n_ins, updated + n_upd, removed + n_rem
                flush(session)
                finish_link(session, link, ok=True, accounts=account_ids)
                notes.append(f"{link.institution}: {n_ins} new, {n_upd} changed, {n_rem} removed")
            except Exception as error:  # noqa: BLE001 - one link's failure is recorded on it and never stops the others
                session.rollback()
                ok = False
                if isinstance(error, PlaidError) and error.code == "ITEM_LOGIN_REQUIRED":
                    link.needs_login = True
                message = str(error) if isinstance(error, PlaidError) else f"{type(error).__name__}: {error}"
                finish_link(session, link, ok=False, error=message[:300])
                notes.append(f"{link.institution}: {message[:120]}")
        flush(session)
        session.commit()
        return SyncResult(ok=ok, summary="; ".join(notes), inserted=inserted, details={"updated": updated, "removed": removed})

    def sync_link(self, session: Session, api: PlaidClient, link: BankLink) -> tuple[int, int, int, list[str]]:
        """Everything is fetched from Plaid first; then it is written in one short transaction, so the database is
        never locked while the app waits on the network."""
        token = link_token(link)
        got = api.accounts(token)
        pages: list[dict] = []
        cursor = link.cursor or ""
        for _page in range(50):
            page = api.sync_page(token, cursor)
            pages.append(page)
            cursor = str(page.get("next_cursor") or cursor)
            if not page.get("has_more"):
                break
        holdings = None
        if any(account_kind(str(a.get("type") or ""), a.get("subtype")) == "investment" for a in got.get("accounts", [])):
            holdings = self.fetch_holdings(api, token)
        institution = link.institution or str((got.get("item") or {}).get("institution_name") or "")
        accounts = {}
        for a in got.get("accounts", []):
            balances = a.get("balances") or {}
            current = balances.get("current")
            available = balances.get("available")
            kind = account_kind(str(a.get("type") or ""), a.get("subtype"))
            # What can be spent is the available balance (holds and pending debits already out); a card owes its current balance.
            balance = available if (kind in ("checking", "savings", "cash") and available is not None) else current
            limit = balances.get("limit")
            accounts[a["account_id"]] = upsert_account(
                session, provider="plaid", external_id=a["account_id"], name=str(a.get("name") or a.get("official_name") or "Account"), institution=institution,
                kind=kind, balance=float(balance) if balance is not None else None, last4=str(a.get("mask") or ""),
                subtype=investment_subtype(a.get("subtype")) if kind == "investment" else "", credit_limit=float(limit) if (limit is not None and kind == "card") else None,
            )
        if holdings is not None:
            self.write_holdings(session, holdings, accounts)
        inserted = updated = removed = 0
        for page in pages:
            for tx in page.get("added", []) + page.get("modified", []):
                account = accounts.get(tx["account_id"])
                if account is None:
                    continue
                pending = bool(tx.get("pending"))
                # A pending charge is kept, marked pending, so it shows minutes after the purchase; the checking
                # balance Plaid reports already has it taken off. When it posts, Plaid sends a new row that names
                # the pending one, and the pending row goes: never counted twice.
                if not pending and tx.get("pending_transaction_id"):
                    removed += int(remove_transaction(session, "plaid", str(tx["pending_transaction_id"])))
                new = upsert_transaction(
                    session, provider="plaid", tx_id=tx["transaction_id"], account=account, when=date.fromisoformat(tx.get("authorized_date") or tx["date"]) if pending else date.fromisoformat(tx["date"]), amount=float(tx["amount"]),
                    merchant=str(tx.get("merchant_name") or tx.get("name") or ""), raw_name=str(tx.get("name") or ""), category=category_for(tx),
                    posted=None if pending else date.fromisoformat(tx["date"]), pending=pending,
                )
                inserted, updated = inserted + int(new), updated + int(not new)
            for tx in page.get("removed", []):
                removed += int(remove_transaction(session, "plaid", tx["transaction_id"]))
        link.cursor = cursor
        link.needs_login = False
        session.commit()
        self.register_webhook(api, link, token)
        return inserted, updated, removed, list(accounts)

    def register_webhook(self, api: PlaidClient, link: BankLink, token: str) -> None:
        """An item made before the app was published learns the notice address once; a failure only means the
        scheduled syncs carry on as before."""
        from ..config import settings
        from .plaid_webhook import webhook_url

        hook = webhook_url(settings.public_url)
        if not hook or link.webhook == hook:
            return
        try:
            api.set_webhook(token, hook)
            link.webhook = hook
        except PlaidError as error:
            log.info("plaid webhook not registered for %s: %s", link.institution, error)

    def fetch_holdings(self, api: PlaidClient, token: str) -> dict | None:
        """The item's positions; None for a vendor without the product."""
        try:
            return api.holdings(token)
        except PlaidError as error:
            if error.code in ("PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_READY", "NO_INVESTMENT_ACCOUNTS", "INVALID_PRODUCT"):
                log.info("no holdings for this item: %s", error)
                return None
            raise

    def sync_holdings(self, session: Session, api: PlaidClient, token: str, accounts: dict[str, Any]) -> int:
        got = self.fetch_holdings(api, token)
        return self.write_holdings(session, got, accounts) if got is not None else 0

    def write_holdings(self, session: Session, got: dict, accounts: dict[str, Any]) -> int:
        """Every position in the item's investment accounts."""
        securities = {str(s.get("security_id")): s for s in got.get("securities", [])}
        count = 0
        for h in got.get("holdings", []):
            account = accounts.get(h.get("account_id"))
            if account is None or account.kind != "investment":
                continue
            sec = securities.get(str(h.get("security_id")), {})
            as_of_raw = h.get("institution_price_as_of")
            as_of = datetime.combine(date.fromisoformat(as_of_raw), datetime.min.time(), tzinfo=timezone.utc) if as_of_raw else None
            upsert_holding(
                session, account=account, external_id=str(h.get("security_id")), name=str(sec.get("name") or sec.get("ticker_symbol") or "Holding"), ticker=str(sec.get("ticker_symbol") or ""),
                kind=security_kind(sec), quantity=h.get("quantity"), price=h.get("institution_price"), value=h.get("institution_value"), cost_basis=h.get("cost_basis"), as_of=as_of,
            )
            count += 1
        return count

    def connect(self, session: Session, api: PlaidClient, public_token: str, institution: str) -> BankLink:
        access_token, item_id = api.exchange(public_token)
        link = session.exec(select(BankLink).where(BankLink.provider == "plaid", BankLink.external_id == item_id)).first()
        if link is None:
            link = BankLink(provider="plaid", external_id=item_id, institution=institution, environment=api.environment)
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
