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
    last4_enc: str = ""
    balance: float | None = None
    balance_at: dt.datetime | None = None
    connector: str = "manual"
    external_id: str = Field(default="", index=True)
    active: bool = True
    created_at: dt.datetime = Field(default_factory=utcnow)


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
    source: str = "manual"  # manual | rocketmoney | csv | ofx | plaid | teller | era | alert | demo
    source_ref: str = ""
    row_hash: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=utcnow)

    @property
    def is_spending(self) -> bool:
        return not (self.is_transfer or self.is_card_payment or self.is_income or self.is_ignored)


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


class Income(SQLModel, table=True):
    __tablename__ = "incomes"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    cadence: str = "biweekly"
    expected_net: float
    next_date: dt.date
    account_id: int | None = Field(default=None, foreign_key="accounts.id")
    series_id: int | None = Field(default=None, foreign_key="recurring_series.id")
    active: bool = True


class Bill(SQLModel, table=True):
    __tablename__ = "bills"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "bill"  # bill | subscription
    amount: float
    amount_mode: str = "fixed"  # fixed | estimate
    cadence: str = "monthly"
    due_day: int = 1
    autopay: bool = False
    pay_from_account_id: int | None = Field(default=None, foreign_key="accounts.id")
    grace_days: int = 0
    late_fee: float = 0.0
    movable: bool = False
    move_window: str = ""
    category: str = ""
    series_id: int | None = Field(default=None, foreign_key="recurring_series.id")
    active: bool = True


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
    active: bool = True


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
