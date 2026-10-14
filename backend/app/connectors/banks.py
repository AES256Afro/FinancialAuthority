"""What the live bank connectors share: the encrypted credential store, the category maps, and the upserts
into the account and transaction registers.

A bank link is a token the owner obtained by signing in on the bank's own page inside the vendor's window.
The app stores the token encrypted and reads with it; it never sees, asks for or stores a bank password.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from sqlmodel import Session, select

from .. import crypto
from ..models import Account, BankLink, Transaction, utcnow
from ..store import get_setting, set_setting

SECRET_FIELDS = {"plaid": {"secret"}, "teller": {"certificate", "private_key"}}
PLAIN_FIELDS = {"plaid": {"client_id", "environment"}, "teller": {"application_id", "environment"}}

# Plaid's personal-finance categories and Teller's categories, to the ledger's own words.
PLAID_CATEGORIES = {
    "INCOME": "Income", "TRANSFER_IN": "Transfer", "TRANSFER_OUT": "Transfer", "LOAN_PAYMENTS": "Loan Payment",
    "BANK_FEES": "Fees & Charges", "ENTERTAINMENT": "Entertainment", "FOOD_AND_DRINK": "Dining & Drinks",
    "GENERAL_MERCHANDISE": "Shopping", "HOME_IMPROVEMENT": "Home", "MEDICAL": "Health & Fitness", "PERSONAL_CARE": "Personal Care",
    "GENERAL_SERVICES": "Services", "GOVERNMENT_AND_NON_PROFIT": "Government & Charity", "TRANSPORTATION": "Auto & Transport",
    "TRAVEL": "Travel", "RENT_AND_UTILITIES": "Bills & Utilities",
}
PLAID_DETAILED = {
    "FOOD_AND_DRINK_GROCERIES": "Groceries", "TRANSPORTATION_GAS": "Gas", "RENT_AND_UTILITIES_RENT": "Rent",
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": "Credit Card Payment", "RENT_AND_UTILITIES_INTERNET_AND_CABLE": "Internet",
    "RENT_AND_UTILITIES_TELEPHONE": "Phone", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": "Electricity",
    "RENT_AND_UTILITIES_WATER": "Water and trash", "GENERAL_SERVICES_INSURANCE": "Insurance", "INCOME_WAGES": "Income",
    "ENTERTAINMENT_TV_AND_MOVIES": "Streaming", "ENTERTAINMENT_MUSIC_AND_AUDIO": "Music", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS": "Gym",
}
TELLER_CATEGORIES = {
    "income": "Income", "transfer": "Transfer", "loan": "Loan Payment", "groceries": "Groceries", "dining": "Dining & Drinks", "bar": "Dining & Drinks",
    "fuel": "Gas", "transport": "Auto & Transport", "transportation": "Auto & Transport", "utilities": "Bills & Utilities", "phone": "Phone",
    "insurance": "Insurance", "entertainment": "Entertainment", "shopping": "Shopping", "clothing": "Shopping", "electronics": "Shopping",
    "health": "Health & Fitness", "sport": "Health & Fitness", "home": "Home", "accommodation": "Travel", "education": "Education",
    "software": "Software", "service": "Services", "general": "Uncategorized", "tax": "Taxes", "charity": "Government & Charity",
    "investment": "Investment", "office": "Services", "advertising": "Services",
}
PLAID_KINDS = {"depository": "checking", "credit": "card", "loan": "loan", "investment": "investment", "brokerage": "investment"}
SAVINGS_SUBTYPES = {"savings", "money market", "money_market", "cd", "certificate_of_deposit", "hsa"}


def bank_settings(session: Session, provider: str) -> dict[str, Any]:
    """The provider's settings with secrets decrypted; empty strings where nothing is stored."""
    stored = (get_setting(session, "banks.secrets", {}) or {}).get(provider, {})
    out: dict[str, Any] = {}
    for key in PLAIN_FIELDS[provider] | SECRET_FIELDS[provider]:
        value = stored.get(key, "")
        if key in SECRET_FIELDS[provider] and crypto.is_encrypted(value):
            try:
                value = crypto.decrypt(value)
            except (ValueError, crypto.DataKeyMissing):
                value = ""
        out[key] = value or ""
    return out


def save_bank_settings(session: Session, provider: str, values: dict[str, Any]) -> None:
    everything = get_setting(session, "banks.secrets", {}) or {}
    stored = dict(everything.get(provider, {}))
    for key, value in values.items():
        if key not in PLAIN_FIELDS[provider] | SECRET_FIELDS[provider] or value is None:
            continue
        text = str(value).strip()
        if key in SECRET_FIELDS[provider]:
            if text:
                stored[key] = crypto.encrypt(text)
            # an empty secret leaves the stored one alone, so a form that omits it does not erase it
        else:
            stored[key] = text
    everything[provider] = stored
    set_setting(session, "banks.secrets", everything)


def bank_state(session: Session, provider: str) -> dict[str, Any]:
    """What the screen may see: which fields are set, never the secrets."""
    s = bank_settings(session, provider)
    if provider == "plaid":
        return {"configured": bool(s["client_id"] and s["secret"]), "environment": s["environment"] or "sandbox", "client_id": s["client_id"], "secret_set": bool(s["secret"])}
    return {"configured": bool(s["application_id"]), "environment": s["environment"] or "development", "application_id": s["application_id"], "certificate_set": bool(s["certificate"]), "private_key_set": bool(s["private_key"])}


def link_token(link: BankLink) -> str:
    return crypto.decrypt(link.access_token_enc) if crypto.is_encrypted(link.access_token_enc) else link.access_token_enc


def tx_hash(provider: str, tx_id: str) -> str:
    return sha256(f"{provider}:{tx_id}".encode()).hexdigest()


def upsert_account(session: Session, *, provider: str, external_id: str, name: str, institution: str, kind: str, balance: float | None, last4: str = "") -> Account:
    row = session.exec(select(Account).where(Account.connector == provider, Account.external_id == external_id)).first()
    if row is None:
        row = Account(name=name or "Account", institution=institution, kind=kind, connector=provider, external_id=external_id)
        session.add(row)
    row.name = name or row.name
    row.institution = institution or row.institution
    row.kind = kind
    if last4:
        try:
            row.last4_enc = crypto.encrypt(last4)
        except crypto.DataKeyMissing:
            row.last4_enc = ""
    if balance is not None:
        row.balance = round(float(balance), 2)
        row.balance_at = utcnow()
    row.active = True
    session.flush()
    return row


def upsert_transaction(session: Session, *, provider: str, tx_id: str, account: Account, when: date, amount: float, merchant: str, raw_name: str, category: str, description: str = "", posted: date | None = None) -> bool:
    """Insert or update one transaction by its vendor id. Returns True when it is new."""
    h = tx_hash(provider, tx_id)
    row = session.exec(select(Transaction).where(Transaction.row_hash == h)).first()
    new = row is None
    if row is None:
        row = Transaction(account_id=account.id, date=when, amount=amount, row_hash=h, source=provider, source_ref=tx_id)
        session.add(row)
    row.account_id = account.id
    row.date = when
    row.posted_date = posted or when
    row.amount = round(float(amount), 2)
    row.merchant = merchant or raw_name
    row.raw_name = raw_name
    row.description = description
    row.category = category or "Uncategorized"
    row.is_transfer = category == "Transfer"
    row.is_card_payment = category == "Credit Card Payment"
    row.is_income = category == "Income" or (amount < 0 and category in ("Income", "Paycheck"))
    return new


def remove_transaction(session: Session, provider: str, tx_id: str) -> bool:
    row = session.exec(select(Transaction).where(Transaction.row_hash == tx_hash(provider, tx_id))).first()
    if row is None:
        return False
    session.delete(row)
    return True


def finish_link(session: Session, link: BankLink, *, ok: bool, error: str = "", accounts: list[str] | None = None) -> None:
    link.last_sync_at = datetime.now(timezone.utc)
    link.last_error = "" if ok else error[:500]
    if accounts is not None:
        link.accounts = ",".join(accounts)
    session.add(link)
    session.commit()
