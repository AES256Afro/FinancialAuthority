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


LIVE_PROVIDERS = ("plaid", "teller")
ADOPTABLE = ("rocketmoney", "file", "manual")  # accounts made by an import or by hand, which a link brings live
MERGE_DAYS = 3  # a link's date and an import's can differ by posting; within this many days they are one
UNLINKED = "-unlinked"  # a row a removed link brought, waiting for the link that replaces it to claim it ("plaid-unlinked")
REMOVED = "accounts.removed"  # accounts the owner removed: a sync leaves them removed
SEEN = "fa_link_accounts"  # session.info: the accounts a sync in this session already took, so no second account takes them too


def removed_by_owner(session: Session) -> set[int]:
    return {int(i) for i in (get_setting(session, REMOVED, []) or [])}


def mark_removed(session: Session, account_id: int, removed: bool = True, *, commit: bool = True) -> None:
    """The owner removed an account (or brought it back): the next bank sync leaves it as the owner set it."""
    import json

    from ..models import Setting

    ids = removed_by_owner(session)
    ids = ids | {account_id} if removed else ids - {account_id}
    row = session.get(Setting, REMOVED) or Setting(key=REMOVED)
    row.value = json.dumps(sorted(ids))
    session.add(row)
    if commit:
        session.commit()


def _last4_of(a: Account) -> str:
    if a.last4_enc:
        try:
            return crypto.decrypt(a.last4_enc) if crypto.is_encrypted(a.last4_enc) else a.last4_enc
        except Exception:  # noqa: BLE001 - a key that cannot be read is just no match
            return ""
    digits = "".join(ch for ch in (a.external_id or "") if ch.isdigit())  # an export keeps the account's number as its key
    return digits[-4:] if a.connector == "rocketmoney" and len(digits) >= 4 else ""


def _listed_by_links(session: Session, but: int | None = None) -> set[tuple[str, str]]:
    """Every (provider, account id) an active link lists (leaving out the link `but`)."""
    out: set[tuple[str, str]] = set()
    for link in session.exec(select(BankLink).where(BankLink.active == True)).all():  # noqa: E712
        if link.id != but:
            out.update((link.provider, i) for i in (link.accounts or "").split(",") if i)
    return out


def _same_institution(a: Account, institution: str) -> bool:
    mine, theirs = (a.institution or "").strip().lower(), (institution or "").strip().lower()
    return not mine or not theirs or mine == theirs


def _left_by_a_link(session: Session, *, institution: str, last4: str) -> Account | None:
    """An account a removed link left behind (or one its bank stopped listing): the same last four digits at the
    same institution. Linking the bank again takes it over with its history, instead of a second account beside it."""
    if not last4:
        return None
    listed = _listed_by_links(session)
    taken = session.info.get(SEEN, set())
    found = [a for a in session.exec(select(Account).where(Account.connector.in_(LIVE_PROVIDERS))).all()  # type: ignore[attr-defined]
             if (a.connector, a.external_id) not in listed and a.id not in taken and _last4_of(a) == last4 and _same_institution(a, institution)]
    return max(found, key=lambda a: (a.active, a.id or 0), default=None)


def duplicate_of(session: Session, *, link: BankLink, external_id: str, institution: str, last4: str) -> Account | None:
    """The same bank account already read through another active link (the bank linked twice): that account, so
    the second link leaves it to the first instead of putting its history in twice."""
    if not last4 or session.exec(select(Account).where(Account.connector == link.provider, Account.external_id == external_id)).first() is not None:
        return None
    listed = _listed_by_links(session, but=link.id)
    taken = session.info.get(SEEN, set())
    for a in session.exec(select(Account).where(Account.connector.in_(LIVE_PROVIDERS), Account.active == True)).all():  # type: ignore[attr-defined]  # noqa: E712
        if (a.connector, a.external_id) in listed and a.id not in taken and _last4_of(a) == last4 and _same_institution(a, institution):
            return a
    return None


def _hand_over(session: Session, account: Account) -> None:
    """A removed link's account is taken over by a new link: its posted rows wait to be claimed by the new link's
    transaction ids (the same amount within MERGE_DAYS), keeping the owner's categories, notes, splits and receipts;
    its pending rows go, because the new link sends its own and the old one can no longer post them."""
    for t in session.exec(select(Transaction).where(Transaction.account_id == account.id, Transaction.source.in_(LIVE_PROVIDERS))).all():  # type: ignore[attr-defined]
        if t.pending:
            detach(session, [t.id])  # type: ignore[list-item]
            session.delete(t)
        else:
            t.source = f"{t.source}{UNLINKED}"
            session.add(t)
    session.flush()


def _imported_account(session: Session, *, name: str, institution: str, last4: str) -> Account | None:
    """The account a link's first sync takes over: one an import or the owner made, with the same last four digits,
    else the same name at the same institution. Its history is then the link's, not a second account beside it."""
    candidates = [a for a in session.exec(select(Account).where(Account.active == True, Account.connector.in_(ADOPTABLE))).all() if a.kind != "investment"]  # noqa: E712
    if last4:
        same = [a for a in candidates if _last4_of(a) == last4]
        if same:
            return same[0]
    inst = (institution or "").strip().lower()
    named = [a for a in candidates if (a.name or "").strip().lower() == (name or "").strip().lower() and (a.institution or "").strip().lower() == inst]
    return next((a for a in named if not (last4 and _last4_of(a))), None) if name else None  # two different numbers are two accounts, whatever their names


OWNER_BALANCE = "banks.owner_balance"  # {account id: {"amount": x, "at": iso}}: what the owner read off the bank's own screen
TOLD_HOURS = 24  # the longest a balance the owner typed stands: by then the bank's feed has caught up


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def tell_balance(session: Session, account: Account, amount: float) -> None:
    """The owner reads a balance off the bank's own screen (for checking, the available balance: what can be spent
    now, holds already out) and types it. A bank's feed can run a day behind and leave holds out; the typed figure
    stands until the bank sends the app something newer, and never longer than a day."""
    now = utcnow()
    told = dict(get_setting(session, OWNER_BALANCE, {}) or {})
    prev = told.get(str(account.id)) or {}
    # The bank's own last figure is kept beside the typed one, so a slip can be cleared back to it.
    # An earlier typed figure without the bank's beside it leaves the bank's unknown: the balance is a typed one.
    bank = prev["bank"] if "bank" in prev else (None if prev else account.balance)
    bank_at = prev.get("bank_at") if prev else (account.balance_at.isoformat() if account.balance_at else None)
    told[str(account.id)] = {"amount": round(float(amount), 2), "at": now.isoformat(), "bank": bank, "bank_at": bank_at}
    set_setting(session, OWNER_BALANCE, told)
    account.balance, account.balance_at = round(float(amount), 2), now
    session.add(account)
    record_balance(session, account, float(amount), local_today(), commit=False)
    if account.kind == "card":
        sync_card_debt(session, account)
    session.commit()


def forget_told(session: Session, account: Account) -> str:
    """The owner takes a typed balance back (a slip of the keys): the bank's own last figure is the balance again."""
    told = dict(get_setting(session, OWNER_BALANCE, {}) or {})
    mine = told.pop(str(account.id), None)
    if mine is None:
        raise ValueError("There is no balance you typed for this account")
    set_setting(session, OWNER_BALANCE, told)
    bank = mine.get("bank")
    if bank is not None:
        account.balance = float(bank)
        account.balance_at = _aware(datetime.fromisoformat(mine["bank_at"])) if mine.get("bank_at") else account.balance_at
        session.add(account)
        record_balance(session, account, float(bank), local_today(), commit=False)
        if account.kind == "card":
            sync_card_debt(session, account)
    session.commit()
    if bank is None:
        return f"Cleared. {account.name} takes the bank's balance at its next read."
    return f"Cleared. {account.name} is back to what the bank last sent: ${float(bank):,.2f}."


def told_balances(session: Session) -> dict[int, dict]:
    """The balances the owner typed that still stand, by account id, with the bank's own last figure beside each."""
    now = utcnow()
    out = {}
    for key, v in (get_setting(session, OWNER_BALANCE, {}) or {}).items():
        try:
            at = _aware(datetime.fromisoformat(v["at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (now - at).total_seconds() < TOLD_HOURS * 3600:
            out[int(key)] = {"amount": float(v["amount"]), "at": at.isoformat(), "bank": v.get("bank")}
    return out


def _told_stands(session: Session, account_id: int, bank_as_of: datetime) -> bool:
    """Whether the balance the owner typed still stands over the one a sync brings: until the bank's data is newer
    than it. Once it is, or the day is up, the typed figure is dropped and the bank's is the balance again."""
    told = get_setting(session, OWNER_BALANCE, {}) or {}
    if str(account_id) not in told:
        return False
    mine = told_balances(session).get(account_id)
    if mine is not None and _aware(bank_as_of) <= datetime.fromisoformat(mine["at"]):
        return True
    rest = dict(told)
    rest.pop(str(account_id), None)
    set_setting(session, OWNER_BALANCE, rest)
    return False


def only_duplicates(session: Session, link: BankLink) -> bool:
    """A new link that took no account: every account it lists is read through another link already, the same
    sign-in linked twice. It brings nothing new. Not while that other link is failing or needs a new sign-in: then
    the new link is the way past it, and takes the accounts over once the old link is removed."""
    ids = [i for i in (link.accounts or "").split(",") if i]
    if not ids:
        return False
    inst = (link.institution or "").strip().lower()
    others = session.exec(select(BankLink).where(BankLink.provider == link.provider, BankLink.active == True, BankLink.id != link.id)).all()  # noqa: E712
    if any((o.needs_login or o.last_error) and (o.institution or "").strip().lower() == inst for o in others):
        return False
    return session.exec(select(Account.id).where(Account.connector == link.provider, Account.external_id.in_(ids))).first() is None  # type: ignore[attr-defined]


def upsert_account(session: Session, *, provider: str, external_id: str, name: str, institution: str, kind: str, balance: float | None, last4: str = "", subtype: str = "", credit_limit: float | None = None,
                   as_of: datetime | None = None) -> Account:
    """The link's account, made or taken over. An account the owner removed stays removed: it comes back inactive
    with nothing written to it, and the caller reads nothing into it. `as_of` is when the bank itself last sent the
    vendor its data (Plaid refreshes on its own schedule), so the balance is dated by the bank, not by the read; a
    balance the owner typed since then stands over it."""
    row = session.exec(select(Account).where(Account.connector == provider, Account.external_id == external_id)).first()
    if row is None and provider in LIVE_PROVIDERS:
        row = _left_by_a_link(session, institution=institution, last4=last4) or _imported_account(session, name=name, institution=institution, last4=last4)
        if row is not None:
            if row.connector in LIVE_PROVIDERS:
                _hand_over(session, row)
            row.connector, row.external_id = provider, external_id  # the link takes over the account its history was imported into
    if row is None:
        row = Account(name=name or "Account", institution=institution, kind=kind, connector=provider, external_id=external_id)
        session.add(row)
        session.flush()
    session.info.setdefault(SEEN, set()).add(row.id)
    if row.id in removed_by_owner(session):
        if not row.active:
            session.add(row)
            session.flush()
            return row
        mark_removed(session, row.id, removed=False, commit=False)  # brought back since: the old removal no longer holds
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
    stamp = min(_aware(as_of), utcnow()) if as_of is not None else utcnow()
    if balance is not None and row.id is not None and _told_stands(session, row.id, stamp):
        balance = None  # the owner's figure is newer than anything the bank has sent
    if balance is not None:
        row.balance = round(float(balance), 2)
        row.balance_at = stamp
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
    """Insert or update one transaction by its vendor id. Returns True when it is new. A posted row the account
    already holds from an import (the same amount within three days, by the same name or on the same day) is the
    same transaction: the link takes that row over instead of adding a second. A row a removed link brought is
    taken over the same way, and keeps its category and name as they were (the bank's, or the owner's)."""
    h = tx_hash(provider, tx_id)
    row = session.exec(select(Transaction).where(Transaction.row_hash == h)).first()
    kept = False
    if row is None and not pending and account.id is not None:
        row = _imported_twin(session, account, when, round(float(amount), 2), f"{merchant} {raw_name} {description}")
        if row is not None:
            kept = (row.source or "").endswith(UNLINKED)
            row.row_hash, row.source, row.source_ref, row.import_id = h, provider, tx_id, None  # the bank's own row now; undoing the import leaves it
            session.add(row)
    new = row is None
    if row is None:
        row = Transaction(account_id=account.id, date=when, amount=amount, row_hash=h, source=provider, source_ref=tx_id)
        session.add(row)
    row.account_id = account.id
    row.date = when
    row.posted_date = posted or when
    row.amount = round(float(amount), 2)
    row.raw_name = raw_name
    row.description = description
    row.pending = pending
    if kept:
        return new
    row.merchant = merchant or raw_name
    row.category = category or "Uncategorized"
    row.is_transfer = category == "Transfer"
    row.is_card_payment = category == "Credit Card Payment"
    row.is_income = category == "Income" or (amount < 0 and category in ("Income", "Paycheck"))
    apply_rules(rules_for(session), row)
    return new


def _imported_twin(session: Session, account: Account, when: date, amount: float, text: str) -> Transaction | None:
    """The row an import (or the owner) already put in this account for the same transaction: posted, the same
    amount, within three days, and by a shared word of the name or on the same day; the nearest day first. A row
    a link already holds is its own and never a twin."""
    from datetime import timedelta

    from .files import _words

    rows = session.exec(select(Transaction).where(Transaction.account_id == account.id, Transaction.pending == False, Transaction.source.not_in(LIVE_PROVIDERS),  # noqa: E712
                                                  Transaction.date >= when - timedelta(days=MERGE_DAYS), Transaction.date <= when + timedelta(days=MERGE_DAYS),
                                                  Transaction.amount >= amount - 0.005, Transaction.amount <= amount + 0.005)).all()
    words = _words(text)
    twins = [t for t in rows if t.date == when or words & _words(f"{t.merchant} {t.raw_name} {t.description}")]
    return min(twins, key=lambda t: (abs((t.date - when).days), t.id), default=None)


def detach(session: Session, ids: list[int], *, replaced_by: Transaction | None = None) -> None:
    """Everything that hangs off rows about to be deleted. SQLite gives a deleted row's id to the next row added,
    so nothing may go on naming it: what was matched to the rows (a bill payment, a planned payment, a paystub) is
    unmatched, their pairings with other rows are undone, their split parts go, and a receipt or other file kept
    with them is kept on its own (shown as not matched yet). With `replaced_by` (a pending charge's posted row) all
    of it moves to that row instead; a split moves only while its parts still add up to the posted amount."""
    from ..engine.pairs import _restore
    from ..models import Document, Payment, PlannedPayment, Paystub, TransactionPair, TransactionSplit

    ids = [i for i in ids if i is not None]
    if not ids:
        return
    to = replaced_by.id if replaced_by is not None else None
    for p in session.exec(select(Payment).where(Payment.transaction_id.in_(ids))).all():  # type: ignore[union-attr]
        p.transaction_id = to
        if to is None:
            p.evidence = ""
            if p.status == "paid":
                p.status = "scheduled"
        else:
            p.evidence = f"transaction:{to}"
        session.add(p)
    for model in (PlannedPayment, Paystub):
        for r in session.exec(select(model).where(model.transaction_id.in_(ids))).all():  # type: ignore[attr-defined]
            r.transaction_id = to
            session.add(r)
    for pair in session.exec(select(TransactionPair).where(TransactionPair.a_id.in_(ids) | TransactionPair.b_id.in_(ids))).all():  # type: ignore[attr-defined]
        _restore(session, pair)  # the other row gets back the flags it had before the pairing
        session.delete(pair)
    parts = list(session.exec(select(TransactionSplit).where(TransactionSplit.transaction_id.in_(ids))).all())  # type: ignore[attr-defined]
    move = (replaced_by is not None and bool(parts) and abs(sum(p.amount for p in parts) - float(replaced_by.amount)) <= 0.005
            and session.exec(select(TransactionSplit.id).where(TransactionSplit.transaction_id == to)).first() is None)
    for part in parts:
        if move:
            part.transaction_id = to  # type: ignore[assignment]
            session.add(part)
        else:
            session.delete(part)
    names = [f"transaction:{i}" for i in ids]
    for doc in session.exec(select(Document).where(Document.attach_to.in_(names))).all():  # type: ignore[attr-defined]
        doc.attach_to = f"transaction:{to}" if to is not None else ""
        session.add(doc)
    session.flush()


def _carry_edits(old: Transaction, new: Transaction) -> None:
    """What the owner set on a pending charge stays when it posts: its category (with what that says about the
    row), its note, its tags, and hidden."""
    if old.category and old.category not in ("Uncategorized", new.category):
        new.category, new.is_transfer, new.is_card_payment = old.category, old.is_transfer, old.is_card_payment
    if old.note and old.note not in (new.note or ""):
        new.note = f"{new.note} · {old.note}" if new.note else old.note
    tags = [t for t in (new.tags or "").split(",") if t]
    tags += [t for t in (old.tags or "").split(",") if t and t not in tags]
    new.tags = ",".join(tags)
    new.is_ignored = new.is_ignored or old.is_ignored


def remove_transaction(session: Session, provider: str, tx_id: str, *, replaced_by: Transaction | None = None) -> bool:
    """A transaction the bank withdrew. What was matched or kept with it is let go first (see `detach`), so the
    removal never fails on it and nothing points at its id afterwards; the matcher runs again later. With
    `replaced_by`, the posted row that replaces a pending one, the owner's edits and everything kept with the
    pending row move to that row."""
    row = session.exec(select(Transaction).where(Transaction.row_hash == tx_hash(provider, tx_id))).first()
    if row is None or (replaced_by is not None and replaced_by.id == row.id):
        return False
    detach(session, [row.id], replaced_by=replaced_by)  # type: ignore[list-item]
    if replaced_by is not None:
        _carry_edits(row, replaced_by)
        session.add(replaced_by)
    session.delete(row)
    return True


def finish_link(session: Session, link: BankLink, *, ok: bool, error: str = "", accounts: list[str] | None = None) -> None:
    link.last_sync_at = datetime.now(timezone.utc)
    link.last_error = "" if ok else error[:500]
    if accounts is not None:
        link.accounts = ",".join(accounts)
    session.add(link)
    session.commit()
