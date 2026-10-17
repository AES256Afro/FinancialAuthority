"""Files from any bank or vendor: a CSV with its columns worked out, or an OFX/QFX statement. Transactions
land in the ledger; a holdings file lands in an investment account; a bills list (name, amount, how often,
due day) lands on the bills register. Nothing is read from the vendor itself; the owner downloaded the file
and dropped it here."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from sqlmodel import Session, select

from ..engine.rules import apply_rules, rules_for
from ..jobs.writing import one_writer
from ..models import Account, Transaction, utcnow
from .banks import record_balance, upsert_holding
from .base import SyncResult

DATE_KEYS = ("date", "transaction date", "posted date", "post date", "trade date", "settlement date", "run date", "posting date", "effective date")
POSTED_KEYS = ("posted date", "post date", "posting date", "settlement date")
AMOUNT_KEYS = ("amount", "transaction amount", "amount (usd)", "net amount", "amount ($)")
DEBIT_KEYS = ("debit", "withdrawal", "withdrawals", "money out", "outflow", "charge", "debit amount")
CREDIT_KEYS = ("credit", "deposit", "deposits", "money in", "inflow", "credit amount")
DESC_KEYS = ("description", "merchant", "name", "payee", "memo", "details", "transaction description", "investment name", "narrative", "transaction")
CATEGORY_KEYS = ("category", "transaction type", "type")
ACCOUNT_KEYS = ("account", "account name", "account number", "account id")
INFLOW_WORDS = re.compile(r"deposit|payroll|direct dep|salary|interest|refund|transfer in|paycheck|payment thank you|payment received|autopay payment|online payment", re.IGNORECASE)
PAY_WORDS = re.compile(r"payroll|direct dep|salary|paycheck|wages", re.IGNORECASE)  # income; a refund or a transfer in is not

HOLDING_SYMBOL = ("symbol", "ticker", "fund symbol")
HOLDING_NAME = ("investment name", "description", "name", "security", "security name", "fund name", "holding")
HOLDING_QTY = ("shares", "quantity", "units", "qty")
HOLDING_PRICE = ("share price", "last price", "price", "unit price", "nav")
HOLDING_VALUE = ("total value", "current value", "market value", "value", "balance", "amount")
BILL_NAME_KEYS = ("bill", "name", "payee", "biller", "merchant", "subscription", "service", "description")
BILL_AMOUNT_KEYS = ("amount", "cost", "price", "payment", "monthly cost", "total")
BILL_DUE_DAY_KEYS = ("due day", "day of month", "day due")
BILL_NEXT_KEYS = ("next due", "next date", "next payment", "due date", "next charge", "renews", "renewal date", "next bill", "due")
BILL_CADENCE_KEYS = ("frequency", "cadence", "repeats", "billing cycle", "cycle", "every", "interval", "period", "schedule")
BILL_AUTOPAY_KEYS = ("autopay", "auto pay", "auto-pay", "automatic")
BILL_CATEGORY_KEYS = ("category", "type", "kind")
CADENCE_WORDS = (("semiannual", "semiannual"), ("semi-annual", "semiannual"), ("6 months", "semiannual"), ("six months", "semiannual"), ("half year", "semiannual"), ("biweekly", "biweekly"), ("bi-weekly", "biweekly"), ("every 2 weeks", "biweekly"), ("every two weeks", "biweekly"), ("fortnight", "biweekly"), ("2 weeks", "biweekly"), ("weekly", "weekly"), ("week", "weekly"), ("quarterly", "quarterly"), ("quarter", "quarterly"), ("3 months", "quarterly"), ("annual", "yearly"), ("yearly", "yearly"), ("year", "yearly"), ("12 months", "yearly"), ("monthly", "monthly"), ("month", "monthly"))
PLAIN_DATE_COLUMNS = ("date", "transaction date", "posted date", "post date", "posting date", "trade date")
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
    posted = _pick(columns, POSTED_KEYS)
    mapping["posted"] = posted if posted and posted != mapping["date"] else None
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
        "posted": parse_date(raw.get(mapping.get("posted") or "", "")) if mapping.get("posted") else None,
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
    """Each <name>...</name> block, found in one pass: an unclosed tag ends the search instead of rescanning."""
    blocks = []
    opening, closing = re.compile(rf"<{name}>", re.IGNORECASE), re.compile(rf"</{name}>", re.IGNORECASE)
    pos = 0
    while True:
        start = opening.search(text, pos)
        if start is None:
            break
        end = closing.search(text, start.end())
        if end is None:
            break
        body = text[start.end():end.start()]
        pos = end.end()
        fields: dict[str, str] = {}
        for tag, value in OFX_TAG.findall(body):
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
        rows.append({"date": when, "amount": round(-amount, 2), "description": description, "merchant": name[:80], "category": "Uncategorized", "account": "", "fitid": t.get("FITID", ""),
                     "posted": parse_date((t.get("DTPOSTED") or "")[:8])})
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
    bills = sniff_bills_csv(text)
    if bills["ready"]:
        return bills
    return sniff_csv(text)


def row_hash(account_name: str, r: dict) -> str:
    key = r["fitid"] or f"{r['date'].isoformat()}|{r['amount']:.2f}|{r['description']}"
    return sha256(f"file|{account_name}|{key}".encode()).hexdigest()


MERGE_DAYS = 3  # a file's date and a bank link's can differ by posting; within this many days they are one
GENERIC_WORDS = {"pos", "debit", "credit", "card", "purchase", "payment", "ach", "the", "and", "withdrawal", "deposit", "online", "transfer", "from", "with",
                 "www", "com", "inc", "llc", "recurring", "pending", "visa", "mastercard", "checkcard", "check", "web", "pmt", "bill", "autopay"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in GENERIC_WORDS}


def _target_account(session: Session, *, account_id: int | None, account_name: str, account_number: str, institution: str, kind: str) -> tuple[Account, bool]:
    """The account the rows belong to: the one chosen; else the account the bank link holds under the same last
    four digits; else the account of the same name; else a new one."""
    if account_id:
        chosen = session.get(Account, account_id)
        if chosen is None or not chosen.active:
            raise ValueError("No such account")
        return chosen, False
    digits = re.sub(r"\D", "", account_number or "")
    if len(digits) >= 4:
        from .rocketmoney import _live_accounts_by_last4

        live = _live_accounts_by_last4(session).get(digits[-4:])
        if live is not None:
            return live, False
    name = (account_name or "Imported").strip()
    for a in session.exec(select(Account).where(Account.active == True)).all():  # noqa: E712
        if a.name.strip().lower() == name.lower():
            return a, False
    account = Account(name=name, institution=institution, kind=kind if kind in ("checking", "savings", "cash", "card", "loan", "investment") else "checking", connector="file", external_id=f"file:{name.lower()}")
    if len(digits) >= 4:
        from .. import crypto

        try:
            account.last4_enc = crypto.encrypt(digits[-4:])
        except Exception:  # noqa: BLE001 - without the data key the digits are simply not kept
            account.last4_enc = ""
    session.add(account)
    session.flush()
    return account, True


def folder_account(session: Session, filename: str, rows: list[dict]) -> dict[str, Any]:
    """Which account a bank's CSV dropped in the import folder belongs to, as import_rows takes it: the account
    whose last four digits the file itself carries, else the account whose name the file's name starts with (a
    copy's " (1)" ignored, the longest name first), else a new one named after the file."""
    from pathlib import Path

    from .banks import _last4_of

    stem = re.sub(r"\s*\(\d+\)$", "", Path(filename).stem).strip() or "Imported"  # "chase (1).csv" is chase.csv again
    number = next((d for d in (re.sub(r"\D", "", str(r.get("account") or "")) for r in rows) if len(d) >= 4), "")
    accounts = [a for a in session.exec(select(Account).where(Account.active == True)).all() if a.kind != "investment"]  # noqa: E712
    if number:
        same = next((a for a in accounts if _last4_of(a) == number[-4:]), None)
        if same is not None:
            return {"account_id": same.id, "account_name": same.name}
    low = stem.lower()
    named = sorted((a for a in accounts if a.name and low.startswith(a.name.strip().lower())), key=lambda a: -len(a.name.strip()))
    if named:
        return {"account_id": named[0].id, "account_name": named[0].name}
    return {"account_name": stem, "account_number": number}


def import_rows(session: Session, rows: list[dict], *, source_file: str, account_name: str, institution: str = "", kind: str = "checking", account_id: int | None = None,
                account_number: str = "", via: str = "upload") -> SyncResult:
    """Rows into the ledger under one account, without adding what is already there. A row an earlier import of
    this or an overlapping file brought is skipped by its key. A row the account already holds from any other
    source (a bank link, an export, the same statement in another format) on the same amount within three days,
    by the same name or on the same day, is the same transaction seen twice and is not added. A key repeated
    within one file is a real repeat purchase. The import is logged with a restore point, so it can be undone. It
    runs under the write lock: the same file dropped and uploaded at once imports once."""
    with one_writer():
        return _import_rows(session, rows, source_file=source_file, account_name=account_name, institution=institution, kind=kind, account_id=account_id,
                            account_number=account_number, via=via)


def _import_rows(session: Session, rows: list[dict], *, source_file: str, account_name: str, institution: str, kind: str, account_id: int | None, account_number: str,
                 via: str) -> SyncResult:
    from .. import importlog

    account, created = _target_account(session, account_id=account_id, account_name=account_name, account_number=account_number, institution=institution, kind=kind)
    batch = importlog.begin(session, "file", source_file, via)
    existing = set(session.exec(select(Transaction.row_hash)).all())
    # What the account already holds, by amount; each row can stand for one row of the file, once.
    held: dict[str, list[list]] = {}
    # Only what falls inside the file's own dates can be the same row: a purchase the day after the statement ends
    # is a different purchase, however alike.
    # A row's day may be when it was bought or when it posted; both count, on either side.
    file_days = [d for r in rows for d in (r["date"], r.get("posted")) if d]
    span = (min(file_days), max(file_days)) if file_days else None
    for t in session.exec(select(Transaction).where(Transaction.account_id == account.id, Transaction.pending == False)).all():  # noqa: E712
        days = {d for d in (t.date, t.posted_date) if d}
        if span is None or not any(span[0] <= d <= span[1] for d in days):
            continue
        held.setdefault(f"{t.amount:.2f}", []).append([days, _words(f"{t.merchant} {t.raw_name} {t.description}"), False])
    rules = rules_for(session)
    inserted = duplicates = merged = 0
    seen: dict[str, int] = {}
    for r in rows:
        h = row_hash(account.name if account_id else account_name, r)
        repeat = seen.get(h, 0)
        seen[h] = repeat + 1
        if repeat and not r["fitid"]:
            h = sha256(f"{h}|{repeat}".encode()).hexdigest()  # a real repeat purchase on the same day
        if h in existing:
            duplicates += 1
            continue
        words = _words(f"{r['merchant']} {r['description']}")
        mine = {d for d in (r["date"], r.get("posted")) if d}

        def gap(x: list) -> int:
            return min(abs((a - b).days) for a in x[0] for b in mine)

        near = [x for x in held.get(f"{r['amount']:.2f}", []) if not x[2] and gap(x) <= MERGE_DAYS and (gap(x) == 0 or words & x[1])]
        if near:
            twin = min(near, key=lambda x: (gap(x), 0 if words & x[1] else 1))
            twin[2] = True
            merged += 1
            continue
        t = Transaction(account_id=account.id, date=r["date"], posted_date=r.get("posted") or r["date"], merchant=r["merchant"], raw_name=r["description"], description=r["description"], amount=r["amount"], category=r["category"], is_income=r["amount"] < 0 and bool(PAY_WORDS.search(r["description"])), source="file", source_ref=source_file, row_hash=h, import_id=batch.id)
        apply_rules(rules, t)
        session.add(t)
        existing.add(h)
        inserted += 1
    summary = f"{inserted} new, {duplicates} already imported" + (f", {merged} already in {account.name} from another source" if merged else "") + f", into {account.name}"
    importlog.finish(session, batch, inserted=inserted, duplicates=duplicates, merged=merged, account_id=account.id, accounts_created=[account.id] if created else [], summary=summary)
    session.commit()
    return SyncResult(ok=True, summary=summary, inserted=inserted, duplicates=duplicates, accounts=int(created),
                      details={"account_id": account.id, "merged": merged, "import_id": batch.id if inserted or created else None}, at=utcnow())


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
    from ..clock import local_today

    when = as_of or local_today()
    for p in positions:
        upsert_holding(session, account=account, external_id=p["external_id"], name=p["name"], ticker=p.get("ticker") or "", kind=p.get("kind") or "other", quantity=p.get("quantity"), price=p.get("price"), value=p.get("value"), cost_basis=None, as_of=datetime.combine(p["as_of"] or when, datetime.min.time(), tzinfo=timezone.utc))
    total = round(sum((p.get("value") or 0.0) for p in positions), 2)
    account.balance, account.balance_at = total, datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc)
    session.add(account)
    session.commit()
    record_balance(session, account, total, when)
    return SyncResult(ok=True, summary=f"{len(positions)} holdings, {total:,.2f} in all, into {account.name}", inserted=len(positions), details={"account_id": account.id, "total": total}, at=utcnow())


# ---- a bills list ------------------------------------------------------------------------------


def _cadence_word(value: str) -> str:
    v = (value or "").strip().lower()
    for word, cadence in CADENCE_WORDS:
        if word in v:
            return cadence
    return "monthly"


def sniff_bills_csv(text: str) -> dict[str, Any]:
    """A list of bills rather than of transactions: a name and an amount, plus how often or when. The tell is a
    frequency or due-day column, or a next-due column with no plain date column, which no bank export has."""
    reader = csv.DictReader(io.StringIO(text))
    columns = [c for c in (reader.fieldnames or []) if c is not None]
    mapping = {
        "name": _pick(columns, BILL_NAME_KEYS), "amount": _pick(columns, BILL_AMOUNT_KEYS), "due_day": _pick(columns, BILL_DUE_DAY_KEYS),
        "next_date": _pick(columns, BILL_NEXT_KEYS), "cadence": _pick(columns, BILL_CADENCE_KEYS), "autopay": _pick(columns, BILL_AUTOPAY_KEYS), "category": _pick(columns, BILL_CATEGORY_KEYS),
    }
    if mapping["due_day"] and mapping["due_day"] == mapping["next_date"]:
        mapping["next_date"] = None
    plain_date = any(_norm(c) in PLAIN_DATE_COLUMNS for c in columns)
    looks_like_bills = bool(mapping["name"] and mapping["amount"] and (mapping["cadence"] or mapping["due_day"] or (mapping["next_date"] and not plain_date)))
    rows = parse_bills_rows(text, mapping) if looks_like_bills else []
    return {"kind": "bills", "columns": columns, "mapping": mapping, "total": len(rows), "sample": rows[:8], "ready": looks_like_bills and bool(rows)}


def parse_bills_rows(text: str, mapping: dict | None = None) -> list[dict]:
    m = mapping or sniff_bills_csv(text)["mapping"]
    rows: list[dict] = []
    for raw in csv.DictReader(io.StringIO(text)):
        name = str(raw.get(m.get("name") or "", "") or "").strip()
        amount = parse_amount(raw.get(m.get("amount") or "", "")) if m.get("amount") else None
        if not name or amount is None:
            continue
        next_date = parse_date(raw.get(m.get("next_date") or "", "")) if m.get("next_date") else None
        due_day = None
        if m.get("due_day"):
            try:
                due_day = int(float(str(raw.get(m["due_day"], "") or "").strip()))
            except ValueError:
                due_day = None
        if due_day is None and next_date is not None:
            due_day = next_date.day
        cadence = _cadence_word(str(raw.get(m.get("cadence") or "", "") or "")) if m.get("cadence") else "monthly"
        autopay = str(raw.get(m.get("autopay") or "", "") or "").strip().lower() in ("yes", "y", "true", "1", "on", "auto", "autopay") if m.get("autopay") else False
        category = str(raw.get(m.get("category") or "", "") or "").strip()
        rows.append({"name": name[:80], "amount": round(abs(amount), 2), "cadence": cadence, "due_day": max(1, min(31, due_day)) if due_day else None, "next_date": next_date, "autopay": autopay, "category": category})
    return rows


def import_bills(session: Session, rows: list[dict], *, source_file: str, today: date | None = None) -> SyncResult:
    """Each row becomes a bill on the register, or updates the bill of the same name. A row without a due day
    (no due-day, next-date or date column it could come from) is skipped and said so. A new bill is dated today,
    so no payment is looked for in the months before it was added. Under the write lock, so a list imported twice
    at once never makes each bill twice."""
    with one_writer():
        return _import_bills(session, rows, source_file=source_file, today=today)


def _import_bills(session: Session, rows: list[dict], *, source_file: str, today: date | None) -> SyncResult:
    from ..clock import local_today
    from ..engine.recurring import bill_named, merchant_key, suggest_kind
    from ..models import Bill

    from .. import importlog

    today = today or local_today()
    batch = importlog.begin(session, "bills", source_file)
    bills = list(session.exec(select(Bill).where(Bill.active == True)).all())  # noqa: E712
    created = updated = skipped = 0
    undated: list[str] = []
    ids: list[int] = []
    changes: list[dict] = []
    for r in rows:
        if r["due_day"] is None:
            skipped += 1
            continue
        key = merchant_key(r["name"])
        bill = bill_named(bills, key)
        kind = "subscription" if suggest_kind(r["category"], r["name"]) == "subscription" else "bill"
        if bill is None:
            bill = Bill(name=r["name"], kind=kind, amount=r["amount"], amount_mode="fixed", cadence=r["cadence"], due_day=r["due_day"], anchor_date=r.get("next_date"), autopay=r["autopay"], movable=True, move_window="1-28", category=r["category"],
                        created_on=today)
            session.add(bill)
            session.flush()
            if bill.cadence not in ("weekly", "biweekly", "monthly") and bill.anchor_date is None:
                from ..engine.payments import anchor_from_ledger

                bill.anchor_date = anchor_from_ledger(session, bill, today)
                if bill.anchor_date is None:
                    undated.append(bill.name)
                session.add(bill)
            bills.append(bill)
            created += 1
            changes.append({"op": "bill_created", "id": bill.id})
        else:
            changes.append({"op": "bill_updated", "id": bill.id, "before": importlog.bill_before(bill)})
            bill.amount, bill.cadence, bill.due_day = r["amount"], r["cadence"], r["due_day"]
            if r.get("next_date"):
                bill.anchor_date = r["next_date"]
            bill.autopay = r["autopay"] or bill.autopay
            if r["category"]:
                bill.category = r["category"]
            session.add(bill)
            updated += 1
        ids.append(int(bill.id or 0))
    summary = f"{created} bill{'s' if created != 1 else ''} added, {updated} updated" + (f", {skipped} without a due day skipped" if skipped else "") + (
        f". Needs its next due date on Bills: {', '.join(undated[:5])}" if undated else "")
    importlog.finish(session, batch, changes=changes, summary=summary)  # the restore point is taken here, before anything is committed
    session.commit()
    if created or updated:
        from ..llm.reviewer import mark_pending

        mark_pending(session, f"bills imported from {source_file}")  # it commits, so only once the import's own point is made
    return SyncResult(ok=True, summary=summary, inserted=created, details={"created": created, "updated": updated, "skipped": skipped, "bill_ids": ids, "source_file": source_file,
                                                                        "import_id": batch.id if changes else None}, at=utcnow())
