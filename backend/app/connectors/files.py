"""Files from any bank or vendor: a CSV with its columns worked out, or an OFX/QFX statement. Transactions
land in the ledger; a holdings file lands in an investment account. Nothing is read from the vendor itself;
the owner downloaded the file and dropped it here."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from sqlmodel import Session, select

from ..engine.rules import apply_rules, rules_for
from ..models import Account, Transaction, utcnow
from .banks import record_balance, upsert_holding
from .base import SyncResult

DATE_KEYS = ("date", "transaction date", "posted date", "post date", "trade date", "settlement date", "run date", "posting date", "effective date")
AMOUNT_KEYS = ("amount", "transaction amount", "amount (usd)", "net amount", "amount ($)")
DEBIT_KEYS = ("debit", "withdrawal", "withdrawals", "money out", "outflow", "charge", "debit amount")
CREDIT_KEYS = ("credit", "deposit", "deposits", "money in", "inflow", "credit amount")
DESC_KEYS = ("description", "merchant", "name", "payee", "memo", "details", "transaction description", "investment name", "narrative", "transaction")
CATEGORY_KEYS = ("category", "transaction type", "type")
ACCOUNT_KEYS = ("account", "account name", "account number", "account id")
INFLOW_WORDS = re.compile(r"deposit|payroll|direct dep|salary|interest|refund|transfer in|paycheck", re.IGNORECASE)

HOLDING_SYMBOL = ("symbol", "ticker", "fund symbol")
HOLDING_NAME = ("investment name", "description", "name", "security", "security name", "fund name", "holding")
HOLDING_QTY = ("shares", "quantity", "units", "qty")
HOLDING_PRICE = ("share price", "last price", "price", "unit price", "nav")
HOLDING_VALUE = ("total value", "current value", "market value", "value", "balance", "amount")
HOLDING_KINDS = {"etf": "etf", "mutual fund": "mutual fund", "fund": "mutual fund", "stock": "equity", "equity": "equity", "bond": "fixed income", "fixed income": "fixed income", "cash": "cash", "money market": "cash", "crypto": "cryptocurrency"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower().replace("_", " "))


def _pick(columns: list[str], keys: tuple[str, ...]) -> str | None:
    normed = {_norm(c): c for c in columns}
    for key in keys:
        if key in normed:
            return normed[key]
    for key in keys:
        for n, c in normed.items():
            if key in n:
                return c
    return None


def parse_amount(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not text or text in ("-", "--"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if text.endswith("-"):
        negative, text = True, text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y%m%d", "%m-%d-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text[:10] if fmt in ("%Y-%m-%d",) else text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def sniff_csv(text: str) -> dict[str, Any]:
    """Which column is which, and the first rows as they would land."""
    reader = csv.DictReader(io.StringIO(text))
    columns = [c for c in (reader.fieldnames or []) if c is not None]
    mapping = {
        "date": _pick(columns, DATE_KEYS), "amount": _pick(columns, AMOUNT_KEYS), "debit": _pick(columns, DEBIT_KEYS), "credit": _pick(columns, CREDIT_KEYS),
        "description": _pick(columns, DESC_KEYS), "category": _pick(columns, CATEGORY_KEYS), "account": _pick(columns, ACCOUNT_KEYS),
    }
    if mapping["amount"] and mapping["amount"] in (mapping["debit"], mapping["credit"]):
        mapping["debit"] = mapping["credit"] = None
    rows = list(reader)
    sign = "bank"
    if mapping["amount"] and not (mapping["debit"] and mapping["credit"]):
        inflows = [parse_amount(r.get(mapping["amount"])) for r in rows if INFLOW_WORDS.search(str(r.get(mapping["description"] or "", "")))]
        inflows = [v for v in inflows if v is not None]
        if inflows and sum(1 for v in inflows if v < 0) > len(inflows) / 2:
            sign = "ledger"  # money in is already negative: the file speaks the ledger's own convention
    sample = [to_row(r, mapping, sign) for r in rows[:5]]
    return {"kind": "csv", "columns": columns, "mapping": mapping, "sign": sign, "total": len(rows), "sample": [s for s in sample if s], "ready": bool(mapping["date"] and (mapping["amount"] or (mapping["debit"] or mapping["credit"])))}


def to_row(raw: dict, mapping: dict, sign: str) -> dict | None:
    when = parse_date(raw.get(mapping.get("date") or "", ""))
    if when is None:
        return None
    if mapping.get("amount") and not (mapping.get("debit") and mapping.get("credit")):
        value = parse_amount(raw.get(mapping["amount"]))
        if value is None:
            return None
        amount = -value if sign == "bank" else value
    else:
        debit = parse_amount(raw.get(mapping.get("debit") or "", "")) or 0.0
        credit = parse_amount(raw.get(mapping.get("credit") or "", "")) or 0.0
        amount = abs(debit) - abs(credit)
    description = str(raw.get(mapping.get("description") or "", "") or "").strip()
    return {
        "date": when, "amount": round(amount, 2), "description": description, "merchant": re.sub(r"\s+", " ", description)[:80],
        "category": str(raw.get(mapping.get("category") or "", "") or "").strip() or "Uncategorized", "account": str(raw.get(mapping.get("account") or "", "") or "").strip(), "fitid": "",
    }


def parse_csv_rows(text: str, mapping: dict | None = None, sign: str | None = None) -> list[dict]:
    sniffed = sniff_csv(text)
    mapping = {**sniffed["mapping"], **{k: v for k, v in (mapping or {}).items() if v}}
    sign = sign or sniffed["sign"]
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for raw in reader:
        row = to_row(raw, mapping, sign)
        if row:
            out.append(row)
    return out


OFX_TAG = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)")


def _ofx_blocks(text: str, name: str) -> list[dict[str, str]]:
    blocks = []
    for m in re.finditer(rf"<{name}>(.*?)</{name}>", text, re.DOTALL | re.IGNORECASE):
        fields: dict[str, str] = {}
        for tag, value in OFX_TAG.findall(m.group(1)):
            if value.strip() and tag.upper() not in fields:
                fields[tag.upper()] = value.strip()
        blocks.append(fields)
    return blocks


def parse_ofx(text: str) -> dict[str, Any]:
    """Transactions and, for an investment statement, positions; the account it names; the vendor."""
    upper = text
    org = re.search(r"<ORG>([^<\r\n]*)", upper)
    acct = re.search(r"<ACCTID>([^<\r\n]*)", upper)
    acct_type = re.search(r"<ACCTTYPE>([^<\r\n]*)", upper)
    rows = []
    for t in _ofx_blocks(upper, "STMTTRN"):
        when = parse_date((t.get("DTPOSTED") or t.get("DTUSER") or "")[:8])
        amount = parse_amount(t.get("TRNAMT"))
        if when is None or amount is None:
            continue
        name = (t.get("NAME") or t.get("MEMO") or t.get("TRNTYPE") or "").strip()
        memo = (t.get("MEMO") or "").strip()
        description = f"{name} {memo}".strip() if memo and memo != name else name
        rows.append({"date": when, "amount": round(-amount, 2), "description": description, "merchant": name[:80], "category": "Uncategorized", "account": "", "fitid": t.get("FITID", "")})
    securities = {}
    for s in _ofx_blocks(upper, "SECINFO"):
        securities[s.get("UNIQUEID", "")] = {"name": s.get("SECNAME", ""), "ticker": s.get("TICKER", "")}
    positions = []
    for kind, tag in (("equity", "POSSTOCK"), ("mutual fund", "POSMF"), ("fixed income", "POSDEBT"), ("other", "POSOTHER"), ("etf", "POSOPT")):
        for p in _ofx_blocks(upper, tag):
            sec = securities.get(p.get("UNIQUEID", ""), {})
            value = parse_amount(p.get("MKTVAL"))
            positions.append({"external_id": p.get("UNIQUEID", "") or sec.get("ticker", "") or sec.get("name", ""), "name": sec.get("name", "") or p.get("UNIQUEID", "Holding"), "ticker": sec.get("ticker", ""), "kind": kind, "quantity": parse_amount(p.get("UNITS")), "price": parse_amount(p.get("UNITPRICE")), "value": value, "as_of": parse_date((p.get("DTPRICEASOF") or "")[:8])})
    return {"kind": "ofx", "vendor": org.group(1).strip() if org else "", "account_id": acct.group(1).strip() if acct else "", "account_type": (acct_type.group(1).strip().lower() if acct_type else ""), "rows": rows, "positions": positions, "total": len(rows)}


def detect(text: str) -> dict[str, Any]:
    if re.search(r"<OFX>|<STMTTRN>|OFXHEADER", text, re.IGNORECASE):
        parsed = parse_ofx(text)
        return {**parsed, "sample": parsed["rows"][:5], "ready": bool(parsed["rows"] or parsed["positions"])}
    return sniff_csv(text)


def row_hash(account_name: str, r: dict) -> str:
    key = r["fitid"] or f"{r['date'].isoformat()}|{r['amount']:.2f}|{r['description']}"
    return sha256(f"file|{account_name}|{key}".encode()).hexdigest()


def import_rows(session: Session, rows: list[dict], *, source_file: str, account_name: str, institution: str = "", kind: str = "checking") -> SyncResult:
    """Rows into the ledger under one account, de-duplicated against everything already there."""
    account = session.exec(select(Account).where(Account.connector == "file", Account.name == account_name)).first()
    created = account is None
    if account is None:
        account = Account(name=account_name or "Imported", institution=institution, kind=kind if kind in ("checking", "savings", "cash", "card", "loan", "investment") else "checking", connector="file", external_id=f"file:{account_name.lower()}")
        session.add(account)
        session.flush()
    existing = set(session.exec(select(Transaction.row_hash)).all())
    rules = rules_for(session)
    inserted = duplicates = 0
    seen: dict[str, int] = {}
    for r in rows:
        h = row_hash(account_name, r)
        repeat = seen.get(h, 0)
        seen[h] = repeat + 1
        if repeat and not r["fitid"]:
            h = sha256(f"{h}|{repeat}".encode()).hexdigest()  # a real repeat purchase on the same day
        if h in existing:
            duplicates += 1
            continue
        t = Transaction(account_id=account.id, date=r["date"], posted_date=r["date"], merchant=r["merchant"], raw_name=r["description"], description=r["description"], amount=r["amount"], category=r["category"], is_income=r["amount"] < 0 and bool(INFLOW_WORDS.search(r["description"])), source="file", source_ref=source_file, row_hash=h)
        apply_rules(rules, t)
        session.add(t)
        existing.add(h)
        inserted += 1
    session.commit()
    return SyncResult(ok=True, summary=f"{inserted} new, {duplicates} already known, into {account.name}", inserted=inserted, duplicates=duplicates, accounts=int(created), details={"account_id": account.id}, at=utcnow())


def sniff_holdings_csv(text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    columns = [c for c in (reader.fieldnames or []) if c is not None]
    mapping = {"symbol": _pick(columns, HOLDING_SYMBOL), "name": _pick(columns, HOLDING_NAME), "quantity": _pick(columns, HOLDING_QTY), "price": _pick(columns, HOLDING_PRICE), "value": _pick(columns, HOLDING_VALUE), "kind": _pick(columns, ("asset class", "type", "security type", "kind"))}
    rows = []
    for raw in reader:
        value = parse_amount(raw.get(mapping["value"] or "", ""))
        name = str(raw.get(mapping["name"] or "", "") or "").strip()
        symbol = str(raw.get(mapping["symbol"] or "", "") or "").strip().upper()
        if value is None or not (name or symbol):
            continue
        kind_text = str(raw.get(mapping["kind"] or "", "") or "").lower()
        kind = next((v for k, v in HOLDING_KINDS.items() if k in kind_text), "other")
        rows.append({"external_id": f"file:{symbol or name.lower()}", "name": name or symbol, "ticker": symbol, "kind": kind, "quantity": parse_amount(raw.get(mapping["quantity"] or "", "")), "price": parse_amount(raw.get(mapping["price"] or "", "")), "value": round(value, 2), "as_of": None})
    return {"kind": "holdings", "columns": columns, "mapping": mapping, "positions": rows, "total": len(rows), "ready": bool(rows)}


def import_holdings(session: Session, account: Account, positions: list[dict], *, as_of: date | None = None) -> SyncResult:
    """Positions into one investment account; the balance becomes their total, kept for the day."""
    when = as_of or utcnow().date()
    for p in positions:
        upsert_holding(session, account=account, external_id=p["external_id"], name=p["name"], ticker=p.get("ticker") or "", kind=p.get("kind") or "other", quantity=p.get("quantity"), price=p.get("price"), value=p.get("value"), cost_basis=None, as_of=datetime.combine(p["as_of"] or when, datetime.min.time(), tzinfo=timezone.utc))
    total = round(sum((p.get("value") or 0.0) for p in positions), 2)
    account.balance, account.balance_at = total, datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc)
    session.add(account)
    session.commit()
    record_balance(session, account, total, when)
    return SyncResult(ok=True, summary=f"{len(positions)} holdings, {total:,.2f} in all, into {account.name}", inserted=len(positions), details={"account_id": account.id, "total": total}, at=utcnow())
