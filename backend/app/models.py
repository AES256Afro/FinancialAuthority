"""The model of the money (docs/PLAN.md section 5) as SQLModel tables.

Amounts: positive is money out, negative is money in, on every transaction. Dates are ISO dates in the
owner's time zone. Anything secret (account numbers, credentials) is stored through crypto.encrypt.
Types are written ``dt.date`` because a column is called ``date``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> dt.datetime:
    """Timezone-aware UTC, which is what the datetime columns store."""
    return dt.datetime.now(dt.timezone.utc)


class Account(SQLModel, table=True):
    __tablename__ = "accounts"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    institution: str = ""
    kind: str = "checking"  # checking | savings | cash | card | loan | bnpl | advance | investment
    subtype: str = ""  # for investments: 401k | 403b | ira | roth_ira | brokerage | hsa | 529 | pension | crypto | other
    last4_enc: str = ""
    balance: float | None = None
    balance_at: dt.datetime | None = None
    spendable: bool | None = None
    credit_limit: float | None = None  # a card's line, when the bank says  # None means by kind: checking and cash are for spending, savings is kept
    connector: str = "manual"
    external_id: str = Field(default="", index=True)
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)
    person_id: int | None = Field(default=None, index=True)  # whose account: None is the owner's, otherwise the second person's
    shared: bool = False  # shared between the owner and the second person: a "shared accounts only" sign-in sees it


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    date: dt.date = Field(index=True)
    posted_date: dt.date | None = None
    merchant: str = Field(default="", index=True)
    raw_name: str = ""
    description: str = ""
    amount: float
    category: str = Field(default="Uncategorized", index=True)
    note: str = ""
    is_transfer: bool = False
    is_card_payment: bool = False
    is_income: bool = False
    is_ignored: bool = False
    pending: bool = False  # the bank has it but has not posted it; the posted row replaces it
    tags: str = ""  # the owner's own words, comma-separated, lower case
    source: str = "manual"  # manual | rocketmoney | csv | ofx | plaid | teller | era | alert | demo
    source_ref: str = ""
    row_hash: str = Field(index=True, unique=True)
    import_id: int | None = Field(default=None, index=True)  # the import that brought it, so that import can be undone
    created_at: dt.datetime = Field(default_factory=utcnow)

    @property
    def is_spending(self) -> bool:
        return not (self.is_transfer or self.is_card_payment or self.is_income or self.is_ignored)


class TransactionSplit(SQLModel, table=True):
    """One part of a transaction split across categories; the parts add up to the transaction."""

    __tablename__ = "transaction_splits"
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(index=True)
    category: str = "Uncategorized"
    amount: float = 0.0
    note: str = ""


class OtherAsset(SQLModel, table=True):
    """Something worth money that is not an account: a home, a car. Its value is the owner's, as of a date."""

    __tablename__ = "other_assets"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "other"  # home | car | other
    value: float = 0.0
    value_on: dt.date
    yearly_change: float | None = None  # a share a year: -0.15 loses 15% a year; None keeps the value as set
    update_every_days: int = 180
    owed: float = 0.0  # a loan against it, for equity; the loan itself stays on Debts
    note: str = ""
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class Passkey(SQLModel, table=True):
    """A passkey the owner made: its public key only, and the address it belongs to."""

    __tablename__ = "passkeys"
    id: int | None = Field(default=None, primary_key=True)
    credential_id: str = Field(index=True, unique=True)  # base64url
    public_key: str = ""  # base64url COSE key
    sign_count: int = 0
    name: str = "Passkey"
    rp_id: str = ""
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_used_at: dt.datetime | None = None
    person_id: int | None = Field(default=None, index=True)  # None: the owner's; otherwise the second person's


class CategoryBudget(SQLModel, table=True):
    """An amount for one spending category for the days one paycheck covers. What is not used carries into the
    next paycheck when `rollover` is "next"."""

    __tablename__ = "category_budgets"
    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(index=True, unique=True)
    per_paycheck: float = 0.0
    rollover: str = "next"  # next | none
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)


class TransactionPair(SQLModel, table=True):
    """Two rows that are one movement of money (a transfer, a card payment and its credit) or one purchase seen
    twice. `before` keeps both rows' flags as they were, so a "no" puts them back."""

    __tablename__ = "transaction_pairs"
    id: int | None = Field(default=None, primary_key=True)
    kind: str = "transfer"  # transfer | card_payment | duplicate
    a_id: int = Field(index=True)  # money out (or the row kept, for a duplicate)
    b_id: int = Field(index=True)  # money in (or the row hidden, for a duplicate)
    status: str = Field(default="pending", index=True)  # auto | pending | confirmed | rejected
    confidence: float = 0.0
    reason: str = ""
    before: str = "{}"
    created_at: dt.datetime = Field(default_factory=utcnow)
    decided_at: dt.datetime | None = None


class RecurringSeries(SQLModel, table=True):
    __tablename__ = "recurring_series"
    id: int | None = Field(default=None, primary_key=True)
    merchant_key: str = Field(index=True)
    merchant: str
    category: str = ""
    cadence: str  # weekly | biweekly | monthly | quarterly | yearly
    typical_amount: float
    latest_amount: float
    tolerance: float = 0.15
    charges_seen: int = 0
    first_seen: dt.date
    last_date: dt.date
    next_expected: dt.date
    active: bool = True
    kind: str = "unknown"  # unknown | bill | subscription | income | advance | installment | ignored
    confirmed: bool = False
    suggested_kind: str = ""  # what the category says it is; a default for the owner's tap, never a fact
    origin: str = ""  # "borrowing" when queued from a lender's payments rather than found by detection, which then leaves it be


class Income(SQLModel, table=True):
    __tablename__ = "incomes"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    cadence: str = "biweekly"  # weekly | biweekly | semimonthly | monthly
    expected_net: float  # the usual net; for a semi-monthly paycheck, the mid-month check
    expected_net_end: float | None = None  # a semi-monthly paycheck's end-of-month check, when it differs
    next_date: dt.date
    account_id: int | None = Field(default=None, foreign_key="accounts.id")
    series_id: int | None = Field(default=None, foreign_key="recurring_series.id")
    active: bool = True
    pay_days: str = ""  # semi-monthly only: "" pays the 15th and the last day, "1,15" the 1st and the 15th
    pay_day: int = 0  # monthly only: the day of the month it pays, 31 for month end (Feb 28, Apr 30); 0 until known, then the day of next_date


class Bill(SQLModel, table=True):
    __tablename__ = "bills"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "bill"  # bill | subscription
    amount: float
    amount_mode: str = "fixed"  # fixed | estimate
    cadence: str = "monthly"  # weekly | biweekly | monthly | quarterly | semiannual | yearly
    due_day: int = 1
    anchor_date: dt.date | None = None  # the next due date, which places a weekly, quarterly or yearly bill
    autopay: bool = False
    pay_from_account_id: int | None = Field(default=None, foreign_key="accounts.id")
    grace_days: int = 0
    late_fee: float = 0.0
    movable: bool = False
    paid_from: str = "due"  # due | mid | end: the paycheck that pays it (mid = the 15th check, end = the end-of-month check)
    move_window: str = ""
    category: str = ""
    series_id: int | None = Field(default=None, foreign_key="recurring_series.id")
    active: bool = True
    created_on: dt.date | None = None  # the day it was added; no payment is looked for before it (None: from before this was kept)
    starts_on: dt.date | None = None  # the first due date: nothing is due before it (a lease whose first full payment is later)
    ends_on: dt.date | None = None  # the last day it can come due (a lease that ends, a service stopped at a move)
    home_id: int | None = Field(default=None, index=True)  # made from a home's charges (Housing); edited there
    pays_to: str = ""  # the name the bank shows for its payments when it is not the bill's own (a home's rent, paid to the property)
    utility_id: int | None = Field(default=None, index=True)  # made from a utility; edited there
    match_names: str = ""  # JSON list: other names its payments carry at the bank (its own names before Housing renamed it), so they keep matching


class Home(SQLModel, table=True):
    """A place lived in: a lease or a mortgage, with its dates. Its charges make the bills every screen counts."""

    __tablename__ = "homes"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "rent"  # rent | mortgage | other
    payee: str = ""  # the landlord, the property, the servicer
    starts_on: dt.date | None = None  # moving in, or the lease's start
    ends_on: dt.date | None = None  # the day you move out: its bills stop here
    lease_ends_on: dt.date | None = None  # the lease term's end as the lease states it; it renews or runs month to month after, so bills go on
    first_due: dt.date | None = None  # the first regular payment (later than the start when the move-in check covers the first weeks)
    due_day: int = 1
    grace_days: int = 0
    late_fee: float = 0.0
    autopay: bool = False
    notice_days: int | None = None  # written notice the lease needs before moving out
    notice_given_on: dt.date | None = None
    deposit: float = 0.0  # held by the landlord, refundable
    deposit_back_on: dt.date | None = None  # when the deposit is expected back after moving out
    pay_from_account_id: int | None = Field(default=None, foreign_key="accounts.id")
    bill_id: int | None = None  # the bill its charges paid with the rent make
    note: str = ""
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)


class HomeCharge(SQLModel, table=True):
    """One part of what a home costs each month: the rent itself, pet rent, a fee, escrow, insurance."""

    __tablename__ = "home_charges"
    id: int | None = Field(default=None, primary_key=True)
    home_id: int = Field(index=True)
    name: str
    kind: str = "fee"  # rent | pet | fee | utility | insurance | principal_interest | escrow | pmi | hoa | parking | other
    amount: float | None = None  # None: not known yet (shown as missing, not counted)
    estimate: bool = False
    pays_to: str = "home"  # home: paid with the rent, in one payment; other: its own bill to someone else
    payee: str = ""  # for "other": who it is paid to
    due_day: int | None = None  # for "other": its own due day
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    bill_id: int | None = None  # for "other": the bill it makes
    note: str = ""
    active: bool = True


class HomeCost(SQLModel, table=True):
    """A one-time cost of getting into a home: a deposit, a fee, the move-in check, the truck. It becomes a one-time
    bill on its date, so every figure counts it, and the bank's payment of it is matched like any bill."""

    __tablename__ = "home_costs"
    id: int | None = Field(default=None, primary_key=True)
    home_id: int = Field(index=True)
    name: str
    amount: float | None = None  # None: not known yet (shown as missing, not counted)
    due_on: dt.date | None = None
    refundable: bool = False  # a deposit that comes back when you leave
    parts: str = "[]"  # JSON [{name, amount, refundable}]: what one check is made of, as the property itemised it
    paid: bool = False  # already paid before the app knew (an application fee): shown, not counted
    bill_id: int | None = None
    note: str = ""
    active: bool = True


class Utility(SQLModel, table=True):
    """A service at a home and where it comes from: who provides it and how it is billed."""

    __tablename__ = "utilities"
    id: int | None = Field(default=None, primary_key=True)
    home_id: int | None = Field(default=None, index=True)
    service: str = "electric"  # electric | water | gas | internet | trash | sewer | phone | other
    provider: str = ""
    billed: str = "direct"  # direct | landlord | submetered | included
    billing_company: str = ""  # who sends the bill when it is submetered
    account_last4_enc: str = ""
    amount: float | None = None  # a usual month; None: not known yet
    estimate: bool = True
    due_day: int | None = None
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    moves_with_you: bool = False  # the same account carries on at the new address
    bill_id: int | None = None  # the bill it makes when it is billed on its own
    note: str = ""
    active: bool = True


class Document(SQLModel, table=True):
    """A file the owner keeps with what it belongs to: a lease with its home, terms with a utility, a receipt with
    its transaction. The file is stored encrypted; its text is kept (encrypted) for search and for the models."""

    __tablename__ = "documents"
    id: int | None = Field(default=None, primary_key=True)
    title: str
    kind: str = "other"  # lease | addendum | charges | welcome | utility_terms | card_agreement | receipt | statement | paystub | other
    attach_to: str = Field(default="", index=True)  # home:3, utility:2, bill:5, account:1, transaction:9, or empty
    filename: str = ""
    content_type: str = ""
    size: int = 0
    sha256: str = Field(default="", index=True)
    pages_enc: str = ""  # the text, page by page, as JSON, encrypted
    pages: int = 0
    note: str = ""
    uploaded_at: dt.datetime = Field(default_factory=utcnow)
    period: str = Field(default="", index=True)  # YYYY-MM: the month a statement, paystub or receipt belongs to
    merchant: str = ""  # a receipt's store
    amount: float | None = None  # a receipt's total
    bought_on: dt.date | None = None
    return_by: dt.date | None = None  # the last day to return it
    warranty_until: dt.date | None = None
    source: str = ""  # upload | mail:<message> | import:<batch> | paystub upload


class Payment(SQLModel, table=True):
    __tablename__ = "payments"
    id: int | None = Field(default=None, primary_key=True)
    bill_id: int = Field(foreign_key="bills.id", index=True)
    due_date: dt.date = Field(index=True)
    amount: float
    status: str = "scheduled"  # scheduled | paid | late | past_due | missed
    paid_date: dt.date | None = None
    transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    evidence: str = ""


class Debt(SQLModel, table=True):
    __tablename__ = "debts"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "card"  # card | loan | medical | bnpl | advance
    balance: float
    apr: float = 0.0
    minimum: float = 0.0
    statement_day: int | None = None
    due_day: int | None = None
    account_id: int | None = Field(default=None, foreign_key="accounts.id")
    pay_in_full: bool = False
    credit_limit: float | None = None  # a card's line, from the bank when it says, else typed
    active: bool = True


class Scenario(SQLModel, table=True):
    """A what-if the owner saved to compare dates: its name and its spec, nothing from the registers."""

    __tablename__ = "scenarios"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    spec: str = "{}"  # JSON
    created_at: dt.datetime = Field(default_factory=utcnow)


class ImportBatch(SQLModel, table=True):
    """One import of a file or an export: what it added, what it changed, and the restore point taken before it,
    so the import can be undone exactly, or the whole database put back as it was."""

    __tablename__ = "import_batches"
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # file | rocketmoney | bills
    source_file: str = ""
    via: str = "upload"  # upload | folder | mail
    account_id: int | None = None
    inserted: int = 0
    duplicates: int = 0
    merged: int = 0
    accounts_created: str = "[]"  # json list of account ids this import created
    changes: str = "[]"  # json: bills created, and bills updated with their values before
    restore_point: str = ""
    summary: str = ""
    status: str = "kept"  # kept | undone
    created_at: dt.datetime = Field(default_factory=utcnow)
    undone_at: dt.datetime | None = None


class AccountMove(SQLModel, table=True):
    """Moving the banking from one account to another over weeks: the paycheck, the bills, the card payments and
    the subscriptions, each switched in its own window so nothing is charged to an account with no money in it."""

    __tablename__ = "account_moves"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    source_account_id: int = Field(foreign_key="accounts.id")
    target_account_id: int | None = Field(default=None, foreign_key="accounts.id")  # None until the new account is in the app
    target_name: str = ""
    started_on: dt.date
    finish_by: dt.date | None = None
    debit_card_on: dt.date | None = None  # when the new debit card arrived, for the subscriptions it pays
    status: str = "moving"  # moving | done | cancelled
    created_at: dt.datetime = Field(default_factory=utcnow)


class MoveItem(SQLModel, table=True):
    """One thing that has to move: found from the registers and the ledger, decided by the owner, switched by the
    owner at the payer, and confirmed by the ledger when a charge or a deposit lands on the new account."""

    __tablename__ = "move_items"
    id: int | None = Field(default=None, primary_key=True)
    move_id: int = Field(foreign_key="account_moves.id", index=True)
    ref: str  # income:1 | bill:12 | card:2 | plan:3 | advance:earnin | found:verizon | transfer:paypal
    kind: str  # income | bill | subscription | card_payment | plan | advance | found | transfer
    name: str
    amount: float = 0.0
    due_day: int | None = None
    match_key: str = ""  # the merchant the ledger shows it under
    decision: str = "move"  # move | keep | drop | undecided
    status: str = "todo"  # todo | switched | confirmed
    switched_on: dt.date | None = None
    confirmed_on: dt.date | None = None
    last_seen_old: dt.date | None = None
    last_seen_new: dt.date | None = None
    note: str = ""


class PlannedPayment(SQLModel, table=True):
    """Money the owner says is leaving before the ledger sees it; settles itself when the transaction posts."""

    __tablename__ = "planned_payments"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    amount: float
    date: dt.date = Field(index=True)
    kind: str = "other"  # card_payment | bill | transfer | other
    to_account_id: int | None = Field(default=None, foreign_key="accounts.id")
    note: str = ""
    done: bool = Field(default=False, index=True)
    transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    created_at: dt.datetime = Field(default_factory=utcnow)


class InstallmentPlan(SQLModel, table=True):
    __tablename__ = "installment_plans"
    id: int | None = Field(default=None, primary_key=True)
    provider: str
    merchant: str = ""
    total: float
    installment: float
    count: int
    remaining: int
    next_date: dt.date
    cadence_days: int = 14
    debt_id: int | None = Field(default=None, foreign_key="debts.id")
    active: bool = True
    loan_id: str = ""  # the lender's own id, which keeps one email from making two plans
    schedule: str = "[]"  # JSON [{date, amount, principal, interest}] from the lender's confirmation, when there is one
    finance_charge: float = 0.0  # what the loan costs over its life, the lender's figure
    loan_amount: float | None = None
    balance: float | None = None  # what is still owed, from the lender's statement when there is one
    apr: float | None = None  # the lender's rate for this loan
    balance_at: dt.date | None = None  # the statement period the balance is from
    pay_from_account_id: int | None = Field(default=None, foreign_key="accounts.id")  # the account the payments come out of; a card means they ride that card
    interest_paid: float = 0.0  # interest charged so far, as the lender's statements add it up


class Advance(SQLModel, table=True):
    __tablename__ = "advances"
    id: int | None = Field(default=None, primary_key=True)
    provider: str
    amount: float
    fee: float = 0.0
    taken_on: dt.date
    repay_date: dt.date
    repaid: bool = False
    debt_id: int | None = Field(default=None, foreign_key="debts.id")


class Rule(SQLModel, table=True):
    __tablename__ = "rules"
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # categorise | rename | tag | hide | preference | sender_autoconfirm | send_allowance
    match_field: str = ""
    pattern: str = ""
    action: str = ""
    value: str = ""
    source: str = "owner"
    created_at: dt.datetime = Field(default_factory=utcnow)


class Goal(SQLModel, table=True):
    __tablename__ = "goals"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "save"  # save | paydown
    target: float
    target_date: dt.date | None = None
    pace: float = 0.0
    priority: int = 1
    funded: float = 0.0
    debt_id: int | None = Field(default=None, foreign_key="debts.id")
    active: bool = True


class WebSession(SQLModel, table=True):
    """One signed-in browser. Signing out ends it; the owner can end any or all; a new password ends them all."""

    __tablename__ = "web_sessions"
    id: int | None = Field(default=None, primary_key=True)
    sid_hash: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_seen_at: dt.datetime | None = None
    user_agent: str = ""
    ip: str = ""
    revoked_at: dt.datetime | None = None
    person_id: int | None = Field(default=None, index=True)  # None: the owner signed in; otherwise the second person


class Person(SQLModel, table=True):
    """A second person the owner lets in, with a role: everything, or the shared accounts only. The person sets
    their own password from a one-time invitation; the owner never sees it. Their second step and passkeys are
    their own, and so are their sends: each person approves only their own."""

    __tablename__ = "people"
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)  # lower case
    display_name: str = ""
    role: str = "shared"  # everything | shared
    password_hash: str = ""  # scrypt; empty until the invitation is used
    password_set_at: dt.datetime | None = None
    invite_hash: str = Field(default="", index=True)
    invite_expires_at: dt.datetime | None = None
    totp_enc: str = ""
    totp_pending_enc: str = ""
    totp_recovery: str = "[]"  # hashed one-time codes
    totp_last_step: int = 0
    totp_since: str = ""
    allowances: str = "[]"  # recipients this person's sends go to without asking
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_sign_in_at: dt.datetime | None = None


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    prefix: str
    key_hash: str = Field(index=True, unique=True)
    scopes: str = "read"  # comma separated: read, confirm, approve, admin
    redaction: str = "full"  # full | merchants | aggregates
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_used_at: dt.datetime | None = None
    person_id: int | None = Field(default=None, index=True)  # made by a second person: revoked when they are turned off or removed
    revoked_at: dt.datetime | None = None

    @property
    def scope_set(self) -> set[str]:
        return {s.strip() for s in self.scopes.split(",") if s.strip()}


class JobRun(SQLModel, table=True):
    __tablename__ = "job_runs"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    started_at: dt.datetime = Field(default_factory=utcnow)
    finished_at: dt.datetime | None = None
    ok: bool | None = None
    summary: str = ""


class AuditEntry(SQLModel, table=True):
    __tablename__ = "audit_entries"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=utcnow, index=True)
    client: str
    client_kind: str = "key"  # owner | key | oauth
    tool: str
    args_hash: str = ""
    rows: int = 0
    redaction: str = "full"


class Setting(SQLModel, table=True):
    __tablename__ = "settings"
    key: str = Field(primary_key=True)
    value: str = ""  # JSON


class OAuthClient(SQLModel, table=True):
    """A client that registered itself (claude.ai, Claude Code, Open WebUI) to sign in through the app."""

    __tablename__ = "oauth_clients"
    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(index=True, unique=True)
    client_secret_hash: str = ""
    name: str = ""
    redirect_uris: str = "[]"  # JSON list
    scopes: str = "read"
    token_endpoint_auth_method: str = "none"
    registration: str = "{}"  # JSON, as registered (encrypted when a data key is set)
    redaction: str = "full"
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_used_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None


class OAuthCode(SQLModel, table=True):
    __tablename__ = "oauth_codes"
    id: int | None = Field(default=None, primary_key=True)
    code_hash: str = Field(index=True, unique=True)
    client_id: str = Field(index=True)
    redirect_uri: str = ""
    redirect_uri_explicit: bool = True
    code_challenge: str = ""
    scopes: str = "read"
    resource: str | None = None
    expires_at: dt.datetime
    used: bool = False
    created_at: dt.datetime = Field(default_factory=utcnow)


class OAuthToken(SQLModel, table=True):
    """Access and refresh tokens, hashed. Revoking a client revokes every token it holds."""

    __tablename__ = "oauth_tokens"
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    kind: str = "access"  # access | refresh
    client_id: str = Field(index=True)
    scopes: str = "read"
    resource: str | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_used_at: dt.datetime | None = None


class MailAccount(SQLModel, table=True):
    """A mailbox read over IMAP: Proton through the Bridge, Gmail with an app password, or any other."""

    __tablename__ = "mail_accounts"
    id: int | None = Field(default=None, primary_key=True)
    label: str
    kind: str = "imap"  # proton | gmail | imap
    host: str
    port: int = 993
    username: str
    password_enc: str = ""
    use_ssl: bool = True
    starttls: bool = False
    folders: str = "INBOX"  # comma separated
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_sync_at: dt.datetime | None = None
    last_error: str = ""
    messages_seen: int = 0
    # Sending, used only by an approved draft: the Bridge's SMTP, Gmail's, or any server; same credentials.
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_ssl: bool = False
    smtp_starttls: bool = True


class MessageRef(SQLModel, table=True):
    """A message by account, folder and UID. The body is not stored; a short excerpt stays with each fact."""

    __tablename__ = "message_refs"
    __table_args__ = (UniqueConstraint("account_id", "folder", "uid", name="uq_message_ref"),)
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="mail_accounts.id", index=True)
    folder: str
    uid: int
    message_id: str = ""
    sender: str = Field(default="", index=True)
    sender_name: str = ""
    subject: str = ""
    date: dt.datetime | None = Field(default=None, index=True)
    has_attachments: bool = False
    attachment_names: str = ""
    snippet: str = ""
    kind: str = ""  # what the classifier called it
    created_at: dt.datetime = Field(default_factory=utcnow)


class Paystub(SQLModel, table=True):
    """One pay statement: the figures, the lines, the deposit it matched, and how it compares with the usual."""

    __tablename__ = "paystubs"
    id: int | None = Field(default=None, primary_key=True)
    employer: str = ""
    provider: str = ""
    pay_date: dt.date = Field(index=True)
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    gross: float | None = None
    net: float
    hours: float | None = None
    deductions: str = "[]"  # JSON list of {name, amount}
    source: str = ""  # mail:<message ref id> | file:<name>
    fact_id: int | None = Field(default=None, foreign_key="extracted_facts.id")
    transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    expected_net: float | None = None
    difference: float | None = None
    stub_hash: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=utcnow)


class ExtractedFact(SQLModel, table=True):
    """Something the app took from a message or a document: pending until the owner, or an earned rule, confirms it."""

    __tablename__ = "extracted_facts"
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # bill_due | statement | paystub | installment | advance | price_change | payday | payment_confirmation | past_due | bank_alert | export
    message_ref_id: int | None = Field(default=None, foreign_key="message_refs.id", index=True)
    fields: str = "{}"  # JSON
    confidence: float = 0.0
    status: str = Field(default="pending", index=True)  # pending | confirmed | auto | rejected | applied
    excerpt: str = ""
    produced_by: str = ""
    applied_to: str = ""
    created_at: dt.datetime = Field(default_factory=utcnow)
    resolved_at: dt.datetime | None = None

class ReviewRun(SQLModel, table=True):
    """One run of the reviewer: what triggered it, which model, its notes with their statuses, and its cost."""

    __tablename__ = "review_runs"
    id: int | None = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=utcnow, index=True)
    trigger: str = "request"  # payday | change | request
    provider: str = ""
    model: str = ""
    ok: bool = False
    error: str = ""
    notes: str = "[]"  # JSON list of {n, kind, text, action, status, unverified}
    raw: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    packet_hash: str = ""

class BankLink(SQLModel, table=True):
    """A bank signed in through a vendor's window (Plaid Link, Teller Connect): the token, encrypted, and
    where the sync left off. The app never holds a bank password."""

    __tablename__ = "bank_links"
    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True)  # plaid | teller
    external_id: str = Field(default="", index=True)  # Plaid item id, Teller enrollment id
    environment: str = ""  # sandbox | production | development: where it was made, since Plaid's Trial counts production logins
    institution: str = ""
    access_token_enc: str = ""
    cursor: str = ""
    accounts: str = ""  # comma-separated vendor account ids
    active: bool = True
    needs_login: bool = False
    created_at: dt.datetime = Field(default_factory=utcnow)
    last_sync_at: dt.datetime | None = None
    last_error: str = ""
    webhook: str = ""  # the address Plaid sends this item's notices to, once registered


class Approval(SQLModel, table=True):
    """Something that would leave the app (today: an email), waiting for the owner. Nothing is sent until the
    status is approved, by a tap or by a standing allowance for the recipient; every step is recorded."""

    __tablename__ = "approvals"
    id: int | None = Field(default=None, primary_key=True)
    kind: str = "send"
    status: str = Field(default="pending", index=True)  # pending | approved | denied | sent | failed
    to: str = ""
    subject: str = ""
    body: str = ""
    reason: str = ""
    source: str = ""
    created_by: str = "owner"
    created_kind: str = "owner"  # owner | person | key | oauth
    for_person_id: int | None = Field(default=None, index=True)  # whose send: None is the owner's; each person approves only their own
    account_id: int | None = Field(default=None, foreign_key="mail_accounts.id")
    created_at: dt.datetime = Field(default_factory=utcnow, index=True)
    decided_by: str = ""
    decided_at: dt.datetime | None = None
    sent_at: dt.datetime | None = None
    message_id: str = ""
    error: str = ""


class Holding(SQLModel, table=True):
    """One position inside an investment account: what it is, how much, what it is worth, and as of when."""

    __tablename__ = "holdings"
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    external_id: str = Field(default="", index=True)  # the vendor's security id, or manual:<name>
    name: str = ""
    ticker: str = ""
    kind: str = "other"  # equity | etf | mutual fund | fixed income | cash | derivative | cryptocurrency | other
    quantity: float | None = None
    price: float | None = None
    value: float | None = None
    cost_basis: float | None = None
    as_of: dt.datetime | None = None
    updated_at: dt.datetime = Field(default_factory=utcnow)


class BalanceSnapshot(SQLModel, table=True):
    """An account's balance on a day, kept so net worth and each investment have a line over time."""

    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_balance_day"),)
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    date: dt.date = Field(index=True)
    balance: float
