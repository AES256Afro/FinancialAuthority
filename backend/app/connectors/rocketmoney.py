"""Rocket Money: the transaction export, three ways in and one way out.

Rocket Money has no API. Its CSV export (Transactions > Export) arrives as an emailed download link that
lives seven days. This connector reads such a file from an upload or the import folder (M1.2 adds the
mail capture and the export-page button) and writes the ledger back out in the same column shape.
Ported from the owner's rocketmoney-mcp server: same columns, same de-duplication, same sign rule.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from hashlib import sha256

from sqlmodel import Session, select

from ..engine.rules import apply_rules, rules_for
from ..models import Account, Transaction, utcnow
from .base import Connector, SyncResult

REQUIRED_COLUMNS = {"Date", "Name", "Amount", "Category"}
EXPORT_COLUMNS = [
    "Date", "Original Date", "Account Type", "Account Name", "Account Number", "Institution Name", "Name", "Custom Name",
    "Amount", "Description", "Category", "Note", "Ignored From", "Tax Deductible", "Transaction Tags",
]
TRANSFER_CATEGORIES = {"Internal Transfers", "Transfers", "Transfer"}
ACCOUNT_KINDS = {"Cash": "checking", "Credit Card": "card", "Loan": "loan", "Investment": "investment", "Savings": "savings"}


def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(s: str) -> float | None:
    s = (s or "").strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def parse_csv(text: str, source_file: str = "upload.csv") -> list[dict] | None:
    """Rows from an export, or None when the file is not a Rocket Money transactions export."""
    reader = csv.DictReader(io.StringIO(text))
    if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
        return None
    rows: list[dict] = []
    for r in reader:
        d, amount = parse_date(r.get("Date", "")), parse_amount(r.get("Amount", ""))
        if d is None or amount is None:
            continue

        def g(k: str) -> str:
            return (r.get(k) or "").strip()

        rows.append({
            "date": d,
            "original_date": parse_date(g("Original Date")) or d,
            "account_type": g("Account Type"),
            "account_name": g("Account Name"),
            "account_number": g("Account Number"),
            "institution": g("Institution Name"),
            "merchant": g("Custom Name") or g("Name"),
            "name": g("Name"),
            "description": g("Description"),
            "amount": amount,
            "category": g("Category") or "Uncategorized",
            "note": g("Note"),
            "ignored_from": g("Ignored From"),
            "tax_deductible": g("Tax Deductible"),
            "tags": g("Transaction Tags"),
            "source_file": source_file,
        })
    flip_if_needed(rows)
    return rows


def flip_if_needed(rows: list[dict]) -> bool:
    """Rocket Money exports expenses as positive. If income looks positive, the file uses the opposite
    convention, so flip everything to keep positive = money out."""
    income = [r["amount"] for r in rows if r["category"] == "Income"]
    sample = income or [-r["amount"] for r in rows]
    flipped = bool(sample) and sum(1 for a in sample if a > 0) > len(sample) / 2
    if flipped:
        for r in rows:
            r["amount"] = -r["amount"]
    return flipped


def row_key(r: dict) -> tuple:
    return (r["original_date"].isoformat(), r["account_name"], r["account_number"], r["name"], r["description"], f"{r['amount']:.2f}")


def row_hash(r: dict, repeat: int = 0) -> str:
    return sha256(("rocketmoney|" + "|".join(row_key(r)) + f"|{repeat}").encode()).hexdigest()


def _account_for(session: Session, r: dict, cache: dict[tuple, Account]) -> Account:
    key = (r["institution"], r["account_name"], r["account_number"])
    if key in cache:
        return cache[key]
    row = session.exec(
        select(Account).where(Account.connector == "rocketmoney", Account.institution == r["institution"], Account.name == r["account_name"], Account.external_id == r["account_number"])
    ).first()
    if row is None:
        row = Account(name=r["account_name"] or "Account", institution=r["institution"], kind=ACCOUNT_KINDS.get(r["account_type"], "checking"), connector="rocketmoney", external_id=r["account_number"])
        session.add(row)
        session.flush()
    cache[key] = row
    return row


def import_rows(session: Session, rows: list[dict], source_file: str = "upload.csv") -> SyncResult:
    """Insert what is new. A key repeated within one file is a real repeat purchase; the same key already in
    the ledger is overlap between exports and is skipped."""
    existing = set(session.exec(select(Transaction.row_hash)).all())
    rules = rules_for(session)
    cache: dict[tuple, Account] = {}
    seen_in_file: dict[tuple, int] = {}
    inserted = duplicates = 0
    accounts_before = len(session.exec(select(Account.id)).all())
    for r in rows:
        k = row_key(r)
        repeat = seen_in_file.get(k, 0)
        seen_in_file[k] = repeat + 1
        h = row_hash(r, repeat)
        if h in existing:
            duplicates += 1
            continue
        account = _account_for(session, r, cache)
        row = Transaction(
            account_id=account.id, date=r["date"], posted_date=r["original_date"], merchant=r["merchant"], raw_name=r["name"], description=r["description"],
            amount=r["amount"], category=r["category"], note=r["note"],
            is_transfer=r["category"] in TRANSFER_CATEGORIES, is_card_payment=r["category"] == "Credit Card Payment",
            is_income=r["category"] == "Income", is_ignored=bool(r["ignored_from"]), source="rocketmoney", source_ref=source_file, row_hash=h,
        )
        apply_rules(rules, row)
        session.add(row)
        existing.add(h)
        inserted += 1
    session.commit()
    accounts_after = len(session.exec(select(Account.id)).all())
    return SyncResult(ok=True, summary=f"{inserted} new, {duplicates} already known", inserted=inserted, duplicates=duplicates, accounts=accounts_after - accounts_before, at=utcnow())


def export_csv(session: Session) -> str:
    """The ledger in Rocket Money's column shape, so it can go wherever that file goes."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    for t in session.exec(select(Transaction).order_by(Transaction.date.desc())).all():
        a = accounts.get(t.account_id)
        writer.writerow({
            "Date": t.date.isoformat(), "Original Date": (t.posted_date or t.date).isoformat(),
            "Account Type": {"checking": "Cash", "card": "Credit Card", "loan": "Loan", "investment": "Investment", "savings": "Savings"}.get(a.kind if a else "", "Cash"),
            "Account Name": a.name if a else "", "Account Number": a.external_id if a else "", "Institution Name": a.institution if a else "",
            "Name": t.raw_name or t.merchant, "Custom Name": t.merchant if t.merchant != t.raw_name else "", "Amount": f"{t.amount:.2f}",
            "Description": t.description, "Category": t.category, "Note": t.note, "Ignored From": "ignored" if t.is_ignored else "", "Tax Deductible": "", "Transaction Tags": "",
        })
    return out.getvalue()


class RocketMoneyConnector(Connector):
    id = "rocketmoney"
    title = "Rocket Money"
    yields = "Transactions and accounts from the CSV export; years of history for backfill."
    settings_schema = []

    def sync(self, session: Session, text: str = "", source_file: str = "upload.csv", **_: object) -> SyncResult:
        rows = parse_csv(text, source_file)
        if rows is None:
            return SyncResult(ok=False, summary=f"{source_file}: not a Rocket Money transactions export (missing columns)")
        return import_rows(session, rows, source_file)
