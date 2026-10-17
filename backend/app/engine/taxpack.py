"""The tax-time pack (M50): everything for filing in one place, from what the app already keeps. The year's totals in
the categories a return asks about (custom_reports.tax_totals), the paystubs' gross pay, net pay and each deduction
summed, the receipts kept for the year by their purchase's category, and which months of each account's statements
are kept. The pack is one .zip: a summary to read, the same as a spreadsheet, and the files themselves in folders.
Every figure is summed here, with its sources; not tax advice."""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Document, Paystub, Transaction
from . import documents as docs
from .custom_reports import NOT_ADVICE, TAX_CATEGORIES, tax_totals

MAX_PACK_BYTES = 300 * 1024 * 1024  # a household's year of files; past this the pack is refused, not cut short
UNMATCHED = "Not matched to a purchase"


def _money(v: float) -> float:
    return round(float(v), 2)


def _in_year(doc: Document, year: int) -> bool:
    if doc.bought_on is not None:
        return doc.bought_on.year == year
    return (doc.period or "").startswith(f"{year}-")


def _paystubs(session: Session, year: int) -> dict[str, Any]:
    stubs = session.exec(select(Paystub).where(Paystub.pay_date >= date(year, 1, 1), Paystub.pay_date <= date(year, 12, 31)).order_by(Paystub.pay_date)).all()
    gross = [s.gross for s in stubs if s.gross is not None]
    deductions: dict[str, dict[str, Any]] = {}
    for s in stubs:
        try:
            lines = json.loads(s.deductions or "[]")
        except ValueError:
            lines = []
        for line in lines if isinstance(lines, list) else []:
            name = str(line.get("name") or "").strip()
            try:
                amount = float(line.get("amount"))
            except (TypeError, ValueError):
                continue
            if not name:
                continue
            d = deductions.setdefault(name.lower(), {"name": name, "total": 0.0, "count": 0})
            d["total"] += amount
            d["count"] += 1
    return {
        "count": len(stubs), "gross": _money(sum(gross)) if gross else None, "gross_missing": len(stubs) - len(gross), "net": _money(sum(s.net for s in stubs)),
        "deductions": sorted(({**d, "total": _money(d["total"])} for d in deductions.values()), key=lambda d: -d["total"]),
        "employers": sorted({s.employer for s in stubs if s.employer}), "sources": [f"paystub:{s.id}" for s in stubs],
    }


def _receipts(session: Session, year: int) -> list[Document]:
    return [d for d in session.exec(select(Document).where(Document.kind == "receipt")).all() if _in_year(d, year)]


def _receipt_category(session: Session, doc: Document, cache: dict[int, Transaction | None]) -> str:
    if not doc.attach_to.startswith("transaction:"):
        return UNMATCHED
    try:
        tid = int(doc.attach_to.split(":", 1)[1])
    except ValueError:
        return UNMATCHED
    if tid not in cache:
        cache[tid] = session.get(Transaction, tid)
    tx = cache[tid]
    return (tx.category or "Uncategorized") if tx else UNMATCHED


def _account_name(session: Session, attach_to: str, cache: dict[int, str]) -> str:
    if attach_to.startswith("account:"):
        try:
            aid = int(attach_to.split(":", 1)[1])
        except ValueError:
            return "Other statements"
        if aid not in cache:
            a = session.get(Account, aid)
            cache[aid] = a.name if a else "Other statements"
        return cache[aid]
    return "Other statements"


def tax_pack(session: Session, today: date, year: int | None = None) -> dict[str, Any]:
    """The year at tax time: the category totals, the paystubs summed, the receipts by category and the statements
    kept month by month, with how many files the pack will hold."""
    totals = tax_totals(session, today, year)
    year = totals["year"]
    stubs = _paystubs(session, year)

    cache: dict[int, Transaction | None] = {}
    by_cat: dict[str, dict[str, Any]] = {}
    receipts = _receipts(session, year)
    for d in receipts:
        cat = _receipt_category(session, d, cache)
        g = by_cat.setdefault(cat, {"name": cat, "total": 0.0, "count": 0, "tax": cat in TAX_CATEGORIES, "sources": []})
        g["total"] += float(d.amount or 0.0)
        g["count"] += 1
        g["sources"].append(f"document:{d.id}")
    receipt_groups = sorted(({**g, "total": _money(g["total"])} for g in by_cat.values()), key=lambda g: (not g["tax"], -g["total"]))

    names: dict[int, str] = {}
    months: dict[str, set[int]] = defaultdict(set)
    statements = [d for d in session.exec(select(Document).where(Document.kind == "statement")).all() if (d.period or "").startswith(f"{year}-")]
    for d in statements:
        try:
            months[_account_name(session, d.attach_to, names)].add(int(d.period[5:7]))
        except ValueError:
            continue
    statement_rows = [{"account": a, "months": [m in got for m in range(1, 13)], "count": len(got)} for a, got in sorted(months.items())]
    paystub_files = [d for d in session.exec(select(Document).where(Document.kind == "paystub")).all() if (d.period or "").startswith(f"{year}-")]

    files = len(receipts) + len(statements) + len(paystub_files)
    parts = []
    if stubs["count"]:
        parts.append(f"{stubs['count']} paystub{'s' if stubs['count'] != 1 else ''}" + (f", ${stubs['gross']:,.2f} gross" if stubs["gross"] is not None else ""))
    if receipts:
        tax_receipts = sum(g["count"] for g in receipt_groups if g["tax"])
        parts.append(f"{len(receipts)} receipt{'s' if len(receipts) != 1 else ''}" + (f" ({tax_receipts} in a tax category)" if tax_receipts else ""))
    if statements:
        parts.append(f"{len(statements)} statement{'s' if len(statements) != 1 else ''} across {len(statement_rows)} account{'s' if len(statement_rows) != 1 else ''}")
    said = (f"For {year} the app keeps " + ", ".join(parts) + f". The pack holds the totals and {files} file{'s' if files != 1 else ''}.") if parts else \
        f"For {year} the app keeps no paystubs, receipts or statements yet; the pack holds the category totals only."
    return {**totals, "paystubs": stubs, "receipts": receipt_groups, "statements": statement_rows, "files": files, "said": said}


def _safe(name: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w\- .,&()]", "", name or "").strip(" .")
    return (cleaned or "file")[:limit]


def _ext(doc: Document) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", doc.filename or "")
    return f".{m.group(1).lower()}" if m else (".txt" if (doc.content_type or "").startswith("text/") else ".bin")


def build_pack(session: Session, today: date, year: int | None = None) -> tuple[bytes, str]:
    """The .zip: README, summary.html, summary.csv, and receipts/, statements/ and paystubs/ with each file as it was
    kept. A file that cannot be found is named in the README instead."""
    pack = tax_pack(session, today, year)
    year = pack["year"]
    out = io.BytesIO()
    missing: list[str] = []
    used: set[str] = set()
    total = 0

    def unique(path: str) -> str:
        base, n = path, 2
        while path in used:
            stem, dot, ext = base.rpartition(".")
            path = f"{stem} ({n}).{ext}" if dot else f"{base} ({n})"
            n += 1
        used.add(path)
        return path

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        def put(path: str, doc: Document) -> None:
            nonlocal total
            try:
                raw = docs.file_of(doc)
            except (FileNotFoundError, ValueError):
                missing.append(f"{path} (document {doc.id}: its file is not on this server)")
                return
            total += len(raw)
            if total > MAX_PACK_BYTES:
                raise ValueError("The files for this year come to more than 300 MB; download them from Documents instead")
            z.writestr(unique(path), raw)

        cache: dict[int, Transaction | None] = {}
        for d in _receipts(session, year):
            when = d.bought_on.isoformat() if d.bought_on else (d.period or "")
            amount = f" ${d.amount:,.2f}" if d.amount is not None else ""
            put(f"receipts/{_safe(_receipt_category(session, d, cache))}/{_safe(f'{when} {d.merchant or d.title}{amount}')}{_ext(d)}", d)
        names: dict[int, str] = {}
        for d in session.exec(select(Document).where(Document.kind == "statement")).all():
            if (d.period or "").startswith(f"{year}-"):
                put(f"statements/{_safe(_account_name(session, d.attach_to, names))}/{_safe(d.period)}{_ext(d)}", d)
        for d in session.exec(select(Document).where(Document.kind == "paystub")).all():
            if (d.period or "").startswith(f"{year}-"):
                put(f"paystubs/{_safe(d.title or d.period)}{_ext(d)}", d)
        z.writestr("summary.csv", _csv(pack))
        z.writestr("summary.html", _html(pack))
        z.writestr("README.txt", _readme(pack, missing))
    return out.getvalue(), f"tax-pack-{year}.zip"


def _rows(pack: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [["section", "name", "amount", "count"]]
    for c in pack["categories"]:
        rows.append(["category", c["name"], c["total"], c["count"]])
    if pack.get("tagged_tax"):
        rows.append(["tagged tax", 'Tagged "tax"', pack["tagged_tax"]["total"], pack["tagged_tax"]["count"]])
    s = pack["paystubs"]
    if s["count"]:
        if s["gross"] is not None:
            rows.append(["pay", "Gross pay", s["gross"], s["count"] - s["gross_missing"]])
        rows.append(["pay", "Net pay", s["net"], s["count"]])
        for d in s["deductions"]:
            rows.append(["deduction", d["name"], d["total"], d["count"]])
    for r in pack["receipts"]:
        rows.append(["receipts", r["name"], r["total"], r["count"]])
    for st in pack["statements"]:
        rows.append(["statements", st["account"], "", st["count"]])
    return rows


def _csv(pack: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in _rows(pack):
        # A cell a spreadsheet would run as a formula is kept as text.
        writer.writerow([f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row])
    return buf.getvalue()


def _html(pack: dict[str, Any]) -> str:
    e = html.escape
    sections: dict[str, list[list[Any]]] = defaultdict(list)
    for section, name, amount, count in _rows(pack)[1:]:
        sections[section].append([name, amount, count])
    titles = {"category": "Spending in the categories a return asks about", "tagged tax": 'Tagged "tax"', "pay": "Pay for the year",
              "deduction": "Taken from pay (summed from the paystubs)", "receipts": "Receipts kept, by the purchase's category", "statements": "Statements kept"}
    body = [f"<h1>Tax pack, {pack['year']}</h1>", f"<p>{e(pack['said'])}</p>", f"<p>From {e(pack['from'])} to {e(pack['to'])}.</p>"]
    for key, title in titles.items():
        if not sections.get(key):
            continue
        body.append(f"<h2>{e(title)}</h2><table><tr><th>What</th><th>Amount</th><th>How many</th></tr>")
        for name, amount, count in sections[key]:
            shown = f"${amount:,.2f}" if isinstance(amount, (int, float)) else ""
            body.append(f"<tr><td>{e(str(name))}</td><td>{shown}</td><td>{count}</td></tr>")
        body.append("</table>")
    body.append(f"<p><em>{e(pack['not_advice'])}</em></p>")
    style = "body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#141518}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:6px;text-align:left}td:nth-child(n+2),th:nth-child(n+2){text-align:right}"
    return f"<!doctype html><meta charset=utf-8><title>Tax pack {pack['year']}</title><style>{style}</style>" + "".join(body)


def _readme(pack: dict[str, Any], missing: list[str]) -> str:
    lines = [
        f"Tax pack for {pack['year']}, made by FinancialAuthority.",
        "",
        "summary.html   the year's totals to read",
        "summary.csv    the same, for a spreadsheet",
        "receipts/      each receipt kept, in a folder per purchase category",
        "statements/    each statement kept, in a folder per account, named by month",
        "paystubs/      each paystub kept, named by pay date",
        "",
        pack["said"],
        "",
        NOT_ADVICE,
    ]
    if missing:
        lines += ["", "Not included (the file is not on this server):", *missing]
    return "\n".join(lines) + "\n"
