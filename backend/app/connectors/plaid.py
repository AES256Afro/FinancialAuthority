"""Plaid on the Trial plan: the bank's own login page in Plaid Link, then transactions and balances, free for
the first ten logins. The owner pastes the client id and secret on the Connectors page; the app never sees a
bank password.

Link: the app makes a link token, Plaid Link runs in the browser, the public token comes back and is
exchanged for an access token, which is stored encrypted. Sync: `/transactions/sync` with a cursor, so each
run takes only what changed; `/accounts/get` for balances.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from .. import crypto
from ..jobs.writing import one_writer
from ..models import BankLink, Transaction
from .banks import (
    HANDED_OVER,
    MAX_NAME_CHARS,
    MAX_RESPONSE_BYTES,
    PLAID_CATEGORIES,
    PLAID_DETAILED,
    PLAID_KINDS,
    SAVINGS_SUBTYPES,
    SEEN,
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
    tx_hash,
    upsert_account,
    upsert_holding,
    upsert_transaction,
)
from .base import Connector, SyncResult

log = logging.getLogger("fa.plaid")

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
def left_out_note(names: list[str]) -> str:
    """'Checking is' or 'Checking, Savings and Credit Card are'."""
    if len(names) == 1:
        return f"{names[0]} is"
    return f"{', '.join(names[:-1])} and {names[-1]} are"


LINK_NOTES = "fa_link_notes"  # session.info: what a link sync has to say beyond its counts
PLAID_VERSION = "2020-09-14"
REQUEST_SECONDS = 120.0  # one call, from the request to the last byte of the answer, whatever the socket allows
MAX_CURSOR_CHARS = 2000  # Plaid's cursors are a few hundred characters
ClientFactory = Callable[[], httpx.Client]


# Item errors only a new sign-in on the same link mends; Sync now cannot. NO_ACCOUNTS is an item whose every
# account was withdrawn, at the bank's OAuth page or by closing them.
SIGN_IN_CODES = {"ITEM_LOGIN_REQUIRED", "NO_ACCOUNTS"}
MUTATION_RETRIES = 3


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
        # What Plaid might echo back and must never land in a record: the secret and the tokens this call carries.
        hidden = [self.secret] + [str(v) for k, v in body.items() if k in ("access_token", "public_token") and v]
        try:
            with self.client_factory() as client:
                with client.stream("POST", f"{self.host}{path}", json=payload, headers={"Content-Type": "application/json", "Plaid-Version": PLAID_VERSION}) as response:
                    status = response.status_code
                    raw = read_bounded(response, max_bytes=MAX_RESPONSE_BYTES, deadline=time.monotonic() + REQUEST_SECONDS)
        except VendorAnswer as error:
            raise PlaidError("BAD_RESPONSE", str(error)) from error
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise PlaidError("BAD_RESPONSE", f"{status}: not JSON ({hide_secrets(clean_text(raw[:120].decode('utf-8', 'replace'), 120), hidden)})") from error
        if not isinstance(data, dict):
            raise PlaidError("BAD_RESPONSE", f"{status}: not an object")
        if status >= 400 or data.get("error_code"):
            message = clean_text(data.get("display_message") or data.get("error_message") or "Plaid refused the request", 300)
            raise PlaidError(clean_text(data.get("error_code") or status, 80), hide_secrets(message, hidden))
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
            # Update mode: the owner signs in again on the same item and picks its accounts again, which is how an
            # item that lost every account (NO_ACCOUNTS, a bank's OAuth consent withdrawn) gets them back.
            body["access_token"] = access_token
            body["update"] = {"account_selection_enabled": True}
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
        token, item = clean_id(data.get("access_token"), 500), clean_id(data.get("item_id"))
        if not token or not item:
            raise PlaidError("BAD_RESPONSE", "the exchange answered without a usable access token and item id")
        return token, item

    def accounts(self, access_token: str) -> dict[str, Any]:
        return self.post("/accounts/get", {"access_token": access_token})

    def bank_updated(self, access_token: str) -> datetime | None:
        """When Plaid last got new data from the bank for this item, on Plaid's own schedule; None when it does not say."""
        try:
            status = self.post("/item/get", {"access_token": access_token}).get("status")
        except (PlaidError, httpx.HTTPError):
            return None
        transactions = status.get("transactions") if isinstance(status, dict) else None
        raw = clean_text((transactions or {}).get("last_successful_update") if isinstance(transactions, dict) else "", 40)
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
        except ValueError:
            return None

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


def _rows(page: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The objects under `key` of a sync page; anything else the vendor put there is not a row."""
    rows = page.get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def category_for(tx: dict[str, Any]) -> str:
    pfc = tx.get("personal_finance_category") if isinstance(tx.get("personal_finance_category"), dict) else {}
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
            # One sync of a link at a time, whoever asks (the hour, Sync now, a webhook, the first sync after
            # linking): two from the same cursor would write the same rows and balances twice.
            with one_writer():
                session.info.pop(SEEN, None)  # the accounts this link takes; every other link's are on its list
                try:
                    session.refresh(link)
                    if not link.active:
                        continue  # removed while this sync waited its turn
                    session.info.pop(HANDED_OVER, None)
                    n_ins, n_upd, n_rem, account_ids = self.sync_link(session, api, link)
                    if session.info.pop(HANDED_OVER, False) and link.cursor:
                        # The link took over a removed link's accounts after its cursor had passed their rows, so
                        # those rows are read again from the start: the old rows are claimed, and no gap is left.
                        link.cursor = ""
                        session.info.pop(SEEN, None)
                        more = self.sync_link(session, api, link)
                        n_ins, n_upd, n_rem, account_ids = n_ins + more[0], n_upd + more[1], n_rem + more[2], more[3]
                    inserted, updated, removed = inserted + n_ins, updated + n_upd, removed + n_rem
                    flush(session)
                    finish_link(session, link, ok=True, accounts=account_ids)
                    notes.append(f"{link.institution}: {n_ins} new, {n_upd} changed, {n_rem} removed" + "".join(f"; {n}" for n in session.info.pop(LINK_NOTES, [])))
                except Exception as error:  # noqa: BLE001 - one link's failure is recorded on it and never stops the others
                    session.rollback()
                    session.info.pop(LINK_NOTES, None)
                    session.info.pop(SEEN, None)
                    ok = False
                    if isinstance(error, PlaidError) and error.code in SIGN_IN_CODES:
                        link.needs_login = True
                    if isinstance(error, PlaidError):
                        message = str(error)
                    elif isinstance(error, httpx.HTTPError):
                        # The network, not the bank: a timeout, a refused connection, a dropped reply. Plain words on the
                        # link, and the next link is read; the next sync tries this one again.
                        message = f"Plaid could not be reached ({type(error).__name__.replace('Error', '').lower() or 'network'}); the next sync tries again"
                    else:
                        message = f"{type(error).__name__}: {error}"
                    finish_link(session, link, ok=False, error=message[:300])
                    notes.append(f"{link.institution}: {message[:120]}")
        flush(session)
        session.commit()
        return SyncResult(ok=ok, summary="; ".join(notes), inserted=inserted, details={"updated": updated, "removed": removed})

    def sync_link(self, session: Session, api: PlaidClient, link: BankLink) -> tuple[int, int, int, list[str]]:
        """Everything is fetched from Plaid first; then it is written in one short transaction, so the database is
        never locked while the app waits on the network."""
        token = link_token(link)
        bank_as_of = api.bank_updated(token)
        got = api.accounts(token)
        pages: list[dict] = []
        for attempt in range(MUTATION_RETRIES):
            # Plaid can change the item's rows while the pages are read (a first two-year pull often does);
            # it then refuses the next page, and every page is read again from the cursor the read began at.
            pages, cursor = [], link.cursor or ""
            try:
                for _page in range(50):
                    page = api.sync_page(token, cursor)
                    pages.append(page)
                    cursor = str(page.get("next_cursor") or cursor)
                    if len(cursor) > MAX_CURSOR_CHARS:
                        raise PlaidError("BAD_RESPONSE", "the sync cursor is longer than any Plaid sends")
                    if not page.get("has_more"):
                        break
                break
            except PlaidError as error:
                if error.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" or attempt == MUTATION_RETRIES - 1:
                    raise
        holdings = None
        if any(account_kind(str(a.get("type") or ""), a.get("subtype")) == "investment" for a in got.get("accounts", [])):
            holdings = self.fetch_holdings(api, token)
        institution = clean_text(link.institution or (got.get("item") or {}).get("institution_name") or "", MAX_NAME_CHARS)
        accounts = {}
        listed: list[str] = []
        left_out = False
        left_out_names: list[str] = []  # said once per sync, not once per account
        unreadable = 0  # accounts and rows the answer carried that are not ones: no id, no amount, no date
        for a in got.get("accounts", []) if isinstance(got.get("accounts"), list) else []:
            account_id = clean_id(a.get("account_id")) if isinstance(a, dict) else None
            if account_id is None:
                unreadable += 1
                continue
            balances = a.get("balances") if isinstance(a.get("balances"), dict) else {}
            current = finite_amount(balances.get("current"))
            available = finite_amount(balances.get("available"))
            kind = account_kind(str(a.get("type") or ""), a.get("subtype") if isinstance(a.get("subtype"), str) else None)
            # What can be spent is the available balance (holds and pending debits already out); a card owes its current balance.
            balance = available if (kind in ("checking", "savings", "cash") and available is not None) else current
            limit = finite_amount(balances.get("limit"))
            name = clean_text(a.get("name") or a.get("official_name") or "Account", MAX_NAME_CHARS) or "Account"
            a = {**a, "account_id": account_id, "mask": clean_text(a.get("mask") or "", 8)}
            listed.append(account_id)
            if duplicate_of(session, link=link, external_id=account_id, institution=institution, last4=str(a.get("mask") or "")) is not None:
                # The same account is read through another link to this bank: it stays with that one. Its rows are
                # not read, so the cursor stays where it was, for this link to read them once the other is removed.
                left_out = True
                left_out_names.append(name)
                continue
            account = upsert_account(
                session, provider="plaid", external_id=a["account_id"], name=name, institution=institution,
                kind=kind, balance=balance, last4=str(a.get("mask") or ""),
                subtype=investment_subtype(a.get("subtype") if isinstance(a.get("subtype"), str) else None) if kind == "investment" else "", credit_limit=limit if (limit is not None and kind == "card") else None,
                as_of=bank_as_of,
            )
            if account.active:
                accounts[account_id] = account  # an account the owner removed gets no balance, holdings or rows
        if holdings is not None:
            self.write_holdings(session, holdings, accounts)
        inserted = updated = removed = 0
        # Every page's new and changed rows first, then every removal: a pending row's removal can come a page
        # before the posted row that replaces it, and what is kept with the pending row must move, not vanish.
        for page in pages:
            for tx in _rows(page, "added") + _rows(page, "modified"):
                tx_id, account_id, amount = clean_id(tx.get("transaction_id")), clean_id(tx.get("account_id")), finite_amount(tx.get("amount"))
                pending = bool(tx.get("pending"))
                day = iso_day(tx.get("date"))
                when = (iso_day(tx.get("authorized_date")) or day) if pending else day
                if tx_id is None or account_id is None or amount is None or when is None or day is None:
                    unreadable += 1  # a row without an id, an amount or a day is not a transaction; it is left out and said so
                    continue
                account = accounts.get(account_id)
                if account is None:
                    continue
                new = upsert_transaction(
                    session, provider="plaid", tx_id=tx_id, account=account, when=when, amount=amount,
                    merchant=str(tx.get("merchant_name") or tx.get("name") or ""), raw_name=str(tx.get("name") or ""), category=category_for(tx),
                    posted=None if pending else day, pending=pending,
                )
                inserted, updated = inserted + int(new), updated + int(not new)
                # A pending charge is kept, marked pending, so it shows minutes after the purchase; the checking
                # balance Plaid reports already has it taken off. When it posts, Plaid sends a new row that names
                # the pending one, and the pending row goes: never counted twice. Its receipt, split, note,
                # category and tags move to the posted row.
                earlier = clean_id(tx.get("pending_transaction_id")) if not pending else None
                if earlier:
                    session.flush()
                    posted = session.exec(select(Transaction).where(Transaction.row_hash == tx_hash("plaid", tx_id))).first()
                    removed += int(remove_transaction(session, "plaid", earlier, replaced_by=posted))
        for page in pages:
            for tx in _rows(page, "removed"):
                tx_id = clean_id(tx.get("transaction_id"))
                if tx_id:
                    removed += int(remove_transaction(session, "plaid", tx_id))
        if unreadable:
            session.info.setdefault(LINK_NOTES, []).append(f"{unreadable} {'row' if unreadable == 1 else 'rows'} Plaid sent could not be read (no id, amount or day) and were left out")
        if left_out_names:
            session.info.setdefault(LINK_NOTES, []).append(f"{left_out_note(left_out_names)} already read through your other {institution or 'bank'} link, so this link leaves {'it' if len(left_out_names) == 1 else 'them'} out; remove one of the two links")
        if not left_out:
            link.cursor = cursor
        link.needs_login = False
        if bank_as_of is not None:
            link.bank_updated_at = bank_as_of
        session.commit()
        self.register_webhook(api, link, token)
        return inserted, updated, removed, listed

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
        securities = {str(s.get("security_id")): s for s in _rows(got, "securities")}
        count = 0
        for h in _rows(got, "holdings"):
            account = accounts.get(h.get("account_id"))
            security_id = clean_id(h.get("security_id"))
            if account is None or account.kind != "investment" or security_id is None:
                continue
            sec = securities.get(security_id, {})
            as_of_day = iso_day(h.get("institution_price_as_of"))
            as_of = datetime.combine(as_of_day, datetime.min.time(), tzinfo=timezone.utc) if as_of_day else None
            upsert_holding(
                session, account=account, external_id=security_id, name=clean_text(sec.get("name") or sec.get("ticker_symbol") or "Holding", MAX_NAME_CHARS) or "Holding", ticker=clean_text(sec.get("ticker_symbol") or "", 20),
                kind=security_kind(sec), quantity=finite_amount(h.get("quantity")), price=finite_amount(h.get("institution_price")), value=finite_amount(h.get("institution_value")), cost_basis=finite_amount(h.get("cost_basis")), as_of=as_of,
            )
            count += 1
        return count

    def connect(self, session: Session, api: PlaidClient, public_token: str, institution: str) -> BankLink:
        access_token, item_id = api.exchange(public_token)
        link = session.exec(select(BankLink).where(BankLink.provider == "plaid", BankLink.external_id == item_id)).first()
        institution = clean_text(institution, MAX_NAME_CHARS)
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
