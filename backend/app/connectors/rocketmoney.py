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


def _live_accounts_by_last4(session: Session) -> dict[str, Account]:
    """The accounts a live connector holds, by the last four digits the bank gave them, so an export's history
    lands on the same account rather than beside it."""
    from .. import crypto

    out: dict[str, Account] = {}
    for a in session.exec(select(Account).where(Account.active == True, Account.connector != "rocketmoney")).all():  # noqa: E712
        if not a.last4_enc:
            continue
        try:
            last4 = crypto.decrypt(a.last4_enc) if crypto.is_encrypted(a.last4_enc) else a.last4_enc
        except Exception:  # noqa: BLE001 - a key that cannot be read is just no match
            continue
        if last4:
            out.setdefault(last4, a)
    return out


def _account_for(session: Session, r: dict, cache: dict[tuple, Account]) -> Account:
    key = (r["institution"], r["account_name"], r["account_number"])
    if key in cache:
        return cache[key]
    if "__live__" not in cache:
        cache["__live__"] = _live_accounts_by_last4(session)  # type: ignore[assignment]
    live = cache["__live__"].get(r["account_number"])  # type: ignore[union-attr]
    if live is not None and (not r["institution"] or not live.institution or r["institution"].lower().split()[0] in live.institution.lower()):
        cache[key] = live
        return live
    row = session.exec(
        select(Account).where(Account.connector == "rocketmoney", Account.institution == r["institution"], Account.name == r["account_name"], Account.external_id == r["account_number"])
    ).first()
    if row is None:
        row = Account(name=r["account_name"] or "Account", institution=r["institution"], kind=ACCOUNT_KINDS.get(r["account_type"], "checking"), connector="rocketmoney", external_id=r["account_number"])
        session.add(row)
        session.flush()
    elif not row.active:
        # Closed by an undone import; its rows are back, so it is open again, and this import owns that.
        row.active = True
        session.add(row)
        cache.setdefault("__reopened__", set()).add(row.id)  # type: ignore[union-attr]
    cache[key] = row
    return row


MERGE_DAYS = 3  # an export's date and a connector's can differ by posting; within this many days they are one


def import_rows(session: Session, rows: list[dict], source_file: str = "upload.csv", via: str = "upload") -> SyncResult:
    """Insert what is new. A key repeated within one file is a real repeat purchase; the same key already in
    the ledger is overlap between exports and is skipped; a row a live connector already holds on the same
    account, same amount within three days, is the same transaction seen twice and is merged, not added."""
    from .. import importlog

    batch = importlog.begin(session, "rocketmoney", source_file, via)
    accounts_known = set(session.exec(select(Account.id)).all())
    existing = set(session.exec(select(Transaction.row_hash)).all())
    rules = rules_for(session)
    cache: dict[tuple, Account] = {}
    seen_in_file: dict[tuple, int] = {}
    inserted = duplicates = merged = 0
    accounts_before = len(session.exec(select(Account.id)).all())
    # What the live connectors hold, by account and amount, each row claimable once.
    from .files import _words

    live_rows: dict[tuple[int, str], list[list]] = {}
    span = (min(r["date"] for r in rows), max(r["date"] for r in rows)) if rows else None
    for t in session.exec(select(Transaction).where(Transaction.source != "rocketmoney")).all():
        if span is not None and span[0] <= t.date <= span[1]:
            live_rows.setdefault((t.account_id, f"{t.amount:.2f}"), []).append([t.date, t.id, False, _words(f"{t.merchant} {t.raw_name} {t.description}")])
    for r in rows:
        k = row_key(r)
        repeat = seen_in_file.get(k, 0)
        seen_in_file[k] = repeat + 1
        h = row_hash(r, repeat)
        if h in existing:
            duplicates += 1
            continue
        account = _account_for(session, r, cache)
        if account.connector != "rocketmoney":
            twins = live_rows.get((account.id, f"{r['amount']:.2f}"), [])
            words = _words(f"{r['merchant']} {r['name']} {r['description']}")
            # the same amount, within three days, and by the same name or on the same day; the nearest day first
            twin = next((x for x in sorted(twins, key=lambda x: abs((x[0] - r["date"]).days))
                         if not x[2] and abs((x[0] - r["date"]).days) <= MERGE_DAYS and (x[0] == r["date"] or words & x[3])), None)
            if twin is not None:
                twin[2] = True
                merged += 1
                existing.add(h)
                continue
        row = Transaction(
            account_id=account.id, date=r["date"], posted_date=r["original_date"], merchant=r["merchant"], raw_name=r["name"], description=r["description"],
            amount=r["amount"], category=r["category"], note=r["note"],
            is_transfer=r["category"] in TRANSFER_CATEGORIES, is_card_payment=r["category"] == "Credit Card Payment",
            is_income=r["category"] == "Income", is_ignored=bool(r["ignored_from"]), source="rocketmoney", source_ref=source_file, row_hash=h, import_id=batch.id,
        )
        apply_rules(rules, row)
        session.add(row)
        existing.add(h)
        inserted += 1
    summary = f"{inserted} new, {duplicates} already known" + (f", {merged} already held by the bank link" if merged else "")
    session.flush()
    created = sorted((set(session.exec(select(Account.id)).all()) - accounts_known) | set(cache.get("__reopened__", set())))  # type: ignore[arg-type]
    importlog.finish(session, batch, inserted=inserted, duplicates=duplicates, merged=merged, accounts_created=created, summary=summary)
    session.commit()
    accounts_after = len(session.exec(select(Account.id)).all())
    return SyncResult(ok=True, summary=summary, inserted=inserted, duplicates=duplicates, accounts=accounts_after - accounts_before,
                      details={"merged": merged, "import_id": batch.id if inserted or created else None}, at=utcnow())


def export_csv(session: Session) -> str:
    """The ledger in Rocket Money's column shape, so it can go wherever that file goes."""
    from ..exports import safe

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    for t in session.exec(select(Transaction).order_by(Transaction.date.desc())).all():
        a = accounts.get(t.account_id)
        writer.writerow(safe.row({
            "Date": t.date.isoformat(), "Original Date": (t.posted_date or t.date).isoformat(),
            "Account Type": {"checking": "Cash", "card": "Credit Card", "loan": "Loan", "investment": "Investment", "savings": "Savings"}.get(a.kind if a else "", "Cash"),
            "Account Name": a.name if a else "", "Account Number": a.external_id if a else "", "Institution Name": a.institution if a else "",
            "Name": t.raw_name or t.merchant, "Custom Name": t.merchant if t.merchant != t.raw_name else "", "Amount": f"{t.amount:.2f}",
            "Description": t.description, "Category": t.category, "Note": t.note, "Ignored From": "ignored" if t.is_ignored else "", "Tax Deductible": "", "Transaction Tags": "",
        }))
    return out.getvalue()


class RocketMoneyConnector(Connector):
    id = "rocketmoney"
    title = "Rocket Money"
    yields = "Transactions and accounts from the CSV export, years of history for backfill; the weekly upcoming-bills email, read from the mailbox, keeps the bill schedule."
    settings_schema = []

    def sync(self, session: Session, text: str = "", source_file: str = "upload.csv", **_: object) -> SyncResult:
        rows = parse_csv(text, source_file)
        if rows is None:
            return SyncResult(ok=False, summary=f"{source_file}: not a Rocket Money transactions export (missing columns)")
        return import_rows(session, rows, source_file, via=str(_.get("via") or "upload"))
