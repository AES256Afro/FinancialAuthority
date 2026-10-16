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
from ..clock import local_today
from ..engine.rules import apply_rules, rules_for
from ..models import Account, BalanceSnapshot, BankLink, Holding, Transaction, utcnow
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


def upsert_account(session: Session, *, provider: str, external_id: str, name: str, institution: str, kind: str, balance: float | None, last4: str = "", subtype: str = "", credit_limit: float | None = None) -> Account:
    row = session.exec(select(Account).where(Account.connector == provider, Account.external_id == external_id)).first()
    if row is None:
        row = Account(name=name or "Account", institution=institution, kind=kind, connector=provider, external_id=external_id)
        session.add(row)
    row.name = name or row.name
    row.institution = institution or row.institution
    row.kind = kind
    if subtype:
        row.subtype = subtype
    if last4:
        try:
            row.last4_enc = crypto.encrypt(last4)
        except crypto.DataKeyMissing:
            row.last4_enc = ""
    if balance is not None:
        row.balance = round(float(balance), 2)
        row.balance_at = utcnow()
    if credit_limit is not None:
        row.credit_limit = round(float(credit_limit), 2)
    row.active = True
    session.flush()
    if balance is not None:
        record_balance(session, row, float(balance), local_today(), commit=False)
    if kind == "card":
        sync_card_debt(session, row)
    return row


def sync_card_debt(session: Session, account: Account) -> None:
    """Every card account has a debt row with it: the balance follows the account; the APR, the statement day,
    the due day and whether it is paid in full are the owner's to type, the line is the bank's when it says."""
    from ..models import Debt

    debt = session.exec(select(Debt).where(Debt.account_id == account.id)).first()
    if debt is None:
        debt = Debt(name=account.name, kind="card", balance=float(account.balance or 0.0), account_id=account.id, credit_limit=account.credit_limit)
    else:
        debt.balance = float(account.balance or 0.0)
        if debt.credit_limit is None and account.credit_limit:
            debt.credit_limit = account.credit_limit
        debt.active = account.active
    session.add(debt)
    session.flush()


def record_balance(session: Session, account: Account, balance: float, when: date, *, commit: bool = True) -> None:
    """One balance per account per day, the latest winning, so every account has a line over time."""
    row = session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == account.id, BalanceSnapshot.date == when)).first()
    if row is None:
        row = BalanceSnapshot(account_id=account.id, date=when, balance=round(float(balance), 2))
    else:
        row.balance = round(float(balance), 2)
    session.add(row)
    if commit:
        session.commit()


def upsert_holding(session: Session, *, account: Account, external_id: str, name: str, ticker: str, kind: str, quantity: float | None, price: float | None, value: float | None, cost_basis: float | None, as_of: datetime | None) -> Holding:
    row = session.exec(select(Holding).where(Holding.account_id == account.id, Holding.external_id == external_id)).first()
    if row is None:
        row = Holding(account_id=account.id, external_id=external_id)
    row.name, row.ticker, row.kind = name or row.name or "Holding", (ticker or "").upper(), kind or "other"
    row.quantity, row.price, row.value, row.cost_basis = quantity, price, round(float(value), 2) if value is not None else None, cost_basis
    row.as_of, row.updated_at = as_of, utcnow()
    session.add(row)
    return row


def upsert_transaction(session: Session, *, provider: str, tx_id: str, account: Account, when: date, amount: float, merchant: str, raw_name: str, category: str, description: str = "", posted: date | None = None, pending: bool = False) -> bool:
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
    row.pending = pending
    row.is_transfer = category == "Transfer"
    row.is_card_payment = category == "Credit Card Payment"
    row.is_income = category == "Income" or (amount < 0 and category in ("Income", "Paycheck"))
    apply_rules(rules_for(session), row)
    return new


def remove_transaction(session: Session, provider: str, tx_id: str) -> bool:
    """A transaction the bank withdrew. What was matched to it (a bill payment, a planned payment, a paystub) is
    unmatched first, so the removal never fails on them; the matcher runs again later."""
    from ..models import Payment, PlannedPayment, Paystub

    row = session.exec(select(Transaction).where(Transaction.row_hash == tx_hash(provider, tx_id))).first()
    if row is None:
        return False
    for p in session.exec(select(Payment).where(Payment.transaction_id == row.id)).all():
        p.transaction_id = None
        if p.status == "paid":
            p.status = "scheduled"
        session.add(p)
    for model in (PlannedPayment, Paystub):
        for r in session.exec(select(model).where(model.transaction_id == row.id)).all():
            r.transaction_id = None
            session.add(r)
    session.flush()
    session.delete(row)
    return True


def finish_link(session: Session, link: BankLink, *, ok: bool, error: str = "", accounts: list[str] | None = None) -> None:
    link.last_sync_at = datetime.now(timezone.utc)
    link.last_error = "" if ok else error[:500]
    if accounts is not None:
        link.accounts = ",".join(accounts)
    session.add(link)
    session.commit()
