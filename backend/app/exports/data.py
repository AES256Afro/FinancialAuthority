"""The data formats: every register as CSV in a zip, everything as JSON (the exit), a calendar of paydays and due
dates, and the ledger as Beancount, Ledger, QIF and OFX for the open-source and older tools. Amounts in the
registers are positive for money out; the accounting formats turn that into postings that balance."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Transaction
from . import safe
from .model import registers


def csv_zip(session: Session, today: date) -> bytes:
    regs = registers(session)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in regs.items():
            buf = io.StringIO()
            if rows:
                w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
                w.writeheader()
                w.writerows(safe.row(r) for r in rows)
            z.writestr(f"{name}.csv", buf.getvalue())
        z.writestr("README.txt", f"FinancialAuthority export, {today.isoformat()}. One file per register. Amounts are positive for money out. No passwords, keys or tokens are included.\n")
    return out.getvalue()


def everything_json(session: Session, today: date) -> bytes:
    from ..engine.cover import account_cover
    from ..engine.paychecks import paycheck_views
    from ..engine.saving import saving_plan
    from ..engine.snapshot import finance_snapshot
    from ..version import __version__

    snap = finance_snapshot(session, today)
    sv = saving_plan(session, today, snap=snap)
    doc = {
        "exported": datetime.now(timezone.utc).isoformat(), "as_of": today.isoformat(), "version": __version__,
        "snapshot": snap, "saving": sv, "cover": account_cover(session, today, saving=sv), "paychecks": paycheck_views(session, today, snap=snap, saving=sv),
        "registers": registers(session),
        "note": "Everything the app holds about your money, as of this date. Passwords, keys, tokens and mail are never included.",
    }
    return json.dumps(doc, indent=1, default=str).encode()


# ---- the calendar --------------------------------------------------------------------------------------------

def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _fold(line: str) -> str:
    out, raw = [], line.encode()
    while len(raw) > 73:
        cut = 73
        while (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(raw[:cut].decode())
        raw = raw[cut:]
    out.append(raw.decode())
    return "\r\n ".join(out)


def calendar(session: Session, today: date, *, amounts: bool = True, name: str = "FinancialAuthority") -> bytes:
    """Paydays, bill due dates, card payments, pay-later payments and advance repayments for ninety days, as
    all-day events a phone's calendar can subscribe to. With amounts off, only names and dates."""
    from ..engine.scenario import assembled, load_inputs

    inp = load_inputs(session, today, horizon_days=90)
    events = assembled(inp, 90)["display"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FinancialAuthority//Paydays and bills//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(name)}: paydays and bills", "X-PUBLISHED-TTL:PT6H"]

    def add(uid: str, when: date, summary: str, description: str) -> None:
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@financialauthority", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{when:%Y%m%d}", f"DTEND;VALUE=DATE:{when + timedelta(days=1):%Y%m%d}",
                      _fold(f"SUMMARY:{_ics_text(summary)}"), _fold(f"DESCRIPTION:{_ics_text(description)}"), "TRANSP:TRANSPARENT", "END:VEVENT"])

    if inp["has_income"]:
        for d in inp["paydays"]:
            if today <= d <= today + timedelta(days=90):
                net = inp["net_for"](d)
                add(f"payday-{d.isoformat()}", d, f"Payday{f' · ${net:,.0f}' if amounts else ''}", "The paycheck the plan expects.")
    for e in events:
        if e["date"] < today:
            continue
        what = {"card_payment": "Card payment", "installment": "Pay-later", "advance": "Advance repays", "subscription": "Subscription"}.get(e["kind"], "Bill")
        tail = f" · ${e['amount']:,.0f}" if amounts else ""
        via = " (on the card)" if e.get("via_card") else ""
        add(f"{re.sub(r'[^a-z0-9:-]', '', (e.get('source') or 'event').lower())}-{e['date'].isoformat()}", e["date"], f"{e['name']}{tail}{via}", f"{what}, from the plan. Not financial advice.")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode()


# ---- plain-text accounting -----------------------------------------------------------------------------------

def _acct(a: Account) -> str:
    base = {"checking": "Assets:Checking", "savings": "Assets:Savings", "cash": "Assets:Cash", "card": "Liabilities:Card", "loan": "Liabilities:Loan", "investment": "Assets:Investments"}.get(a.kind, "Assets:Other")
    name = re.sub(r"[^A-Za-z0-9]+", "-", a.name).strip("-") or f"Account{a.id}"
    return f"{base}:{name[0].upper() + name[1:]}"


def _cat(category: str, income: bool) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "-", category or "Uncategorized").strip("-") or "Uncategorized"
    name = name[0].upper() + name[1:]
    return f"Income:{name}" if income else f"Expenses:{name}"


def _counter(t: Transaction) -> str:
    if t.is_transfer or t.is_card_payment:
        return "Equity:Transfers"
    return _cat(t.category, t.amount < 0)


def _q(s: str) -> str:
    return safe.line(s).replace('"', "'").replace("\\", "/")[:120]


def beancount(session: Session, today: date) -> bytes:
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    txns = session.exec(select(Transaction).where(Transaction.is_ignored == False).order_by(Transaction.date, Transaction.id)).all()  # noqa: E712
    first = txns[0].date if txns else today
    opened: set[str] = set()
    lines = [f"; FinancialAuthority export, {today.isoformat()}. Each transaction balances; transfers and card payments meet in Equity:Transfers.", 'option "operating_currency" "USD"', ""]
    used = {_acct(accounts[t.account_id]) for t in txns if t.account_id in accounts} | {_counter(t) for t in txns}
    for name in sorted(used):
        lines.append(f"{first.isoformat()} open {name} USD")
        opened.add(name)
    lines.append("")
    for t in txns:
        a = accounts.get(t.account_id)
        if a is None:
            continue
        amt = -t.amount  # money out is positive in the register
        lines.append(f'{t.date.isoformat()} * "{_q(t.merchant)}" "{_q(t.description or t.category)}"')
        lines.append(f"  {_acct(a)}  {amt:.2f} USD")
        lines.append(f"  {_counter(t)}  {-amt:.2f} USD")
        lines.append("")
    return "\n".join(lines).encode()


def ledger(session: Session, today: date) -> bytes:
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    txns = session.exec(select(Transaction).where(Transaction.is_ignored == False).order_by(Transaction.date, Transaction.id)).all()  # noqa: E712
    lines = [f"; FinancialAuthority export, {today.isoformat()}. Transfers and card payments meet in Equity:Transfers.", ""]
    for t in txns:
        a = accounts.get(t.account_id)
        if a is None:
            continue
        amt = -t.amount
        lines.append(f"{t.date:%Y/%m/%d} * {_q(t.merchant)}")
        lines.append(f"    {_acct(a)}    ${amt:,.2f}")
        lines.append(f"    {_counter(t)}    ${-amt:,.2f}")
        lines.append("")
    return "\n".join(lines).encode()


def qif(session: Session, today: date) -> bytes:
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    txns = session.exec(select(Transaction).where(Transaction.is_ignored == False).order_by(Transaction.account_id, Transaction.date)).all()  # noqa: E712
    lines: list[str] = []
    current = None
    for t in txns:
        a = accounts.get(t.account_id)
        if a is None:
            continue
        if current != a.id:
            current = a.id
            lines += ["!Account", f"N{safe.line(a.name)}", f"T{'CCard' if a.kind == 'card' else 'Bank'}", "^", f"!Type:{'CCard' if a.kind == 'card' else 'Bank'}"]
        lines += [f"D{t.date:%m/%d/%Y}", f"T{-t.amount:.2f}", f"P{_q(t.merchant)}", f"L{safe.line(t.category) or 'Uncategorized'}", "^"]
    return ("\n".join(lines) + "\n").encode()


def ofx(session: Session, today: date) -> bytes:
    from xml.sax.saxutils import escape

    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    txns = session.exec(select(Transaction).where(Transaction.is_ignored == False).order_by(Transaction.account_id, Transaction.date)).all()  # noqa: E712
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    by: dict[int, list[Transaction]] = {}
    for t in txns:
        by.setdefault(t.account_id, []).append(t)
    bank, card = [], []
    for aid, rows in by.items():
        a = accounts.get(aid)
        if a is None:
            continue
        items = "".join(
            f"<STMTTRN><TRNTYPE>{'CREDIT' if t.amount < 0 else 'DEBIT'}</TRNTYPE><DTPOSTED>{t.date:%Y%m%d}</DTPOSTED><TRNAMT>{-t.amount:.2f}</TRNAMT><FITID>fa-{t.id}</FITID><NAME>{escape(_q(t.merchant)[:32])}</NAME><MEMO>{escape(_q(t.category))}</MEMO></STMTTRN>"
            for t in rows)
        tran = f"<BANKTRANLIST><DTSTART>{rows[0].date:%Y%m%d}</DTSTART><DTEND>{rows[-1].date:%Y%m%d}</DTEND>{items}</BANKTRANLIST>"
        bal = f"<LEDGERBAL><BALAMT>{(a.balance or 0.0):.2f}</BALAMT><DTASOF>{now}</DTASOF></LEDGERBAL>"
        acct_id = f"FA{a.id:06d}"
        if a.kind == "card":
            card.append(f"<CCSTMTTRNRS><TRNUID>{a.id}</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS><CCSTMTRS><CURDEF>USD</CURDEF><CCACCTFROM><ACCTID>{acct_id}</ACCTID></CCACCTFROM>{tran}{bal}</CCSTMTRS></CCSTMTTRNRS>")
        else:
            bank.append(f"<STMTTRNRS><TRNUID>{a.id}</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS><STMTRS><CURDEF>USD</CURDEF><BANKACCTFROM><BANKID>FA</BANKID><ACCTID>{acct_id}</ACCTID><ACCTTYPE>{'SAVINGS' if a.kind == 'savings' else 'CHECKING'}</ACCTTYPE></BANKACCTFROM>{tran}{bal}</STMTRS></STMTTRNRS>")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>\n'
        f"<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS><DTSERVER>{now}</DTSERVER><LANGUAGE>ENG</LANGUAGE></SONRS></SIGNONMSGSRSV1>"
        + (f"<BANKMSGSRSV1>{''.join(bank)}</BANKMSGSRSV1>" if bank else "") + (f"<CREDITCARDMSGSRSV1>{''.join(card)}</CREDITCARDMSGSRSV1>" if card else "") + "</OFX>\n"
    )
    return body.encode()


FORMATS: dict[str, dict[str, Any]] = {
    "csv": {"name": "CSV, every register", "kind": "data", "ext": "zip", "mime": "application/zip", "description": "One spreadsheet-ready file per register, in a zip."},
    "json": {"name": "JSON, everything", "kind": "data", "ext": "json", "mime": "application/json", "description": "The whole plan and every register: the exit, and what a model reads."},
    "ics": {"name": "Calendar (ICS)", "kind": "data", "ext": "ics", "mime": "text/calendar", "description": "Paydays and due dates for ninety days; or subscribe to the live feed below."},
    "beancount": {"name": "Beancount", "kind": "data", "ext": "beancount", "mime": "text/plain", "description": "Plain-text double-entry for Fava and Beancount."},
    "ledger": {"name": "Ledger", "kind": "data", "ext": "ledger", "mime": "text/plain", "description": "Plain-text accounting for ledger-cli and hledger."},
    "qif": {"name": "QIF", "kind": "data", "ext": "qif", "mime": "application/qif", "description": "For GnuCash, Quicken and the older tools."},
    "ofx": {"name": "OFX", "kind": "data", "ext": "ofx", "mime": "application/x-ofx", "description": "The bank statement format most tools import."},
    "md": {"name": "Markdown", "kind": "report", "ext": "md", "mime": "text/markdown", "description": "For notes apps and Obsidian, or to paste into a chat."},
    "html": {"name": "Web page, one file", "kind": "report", "ext": "html", "mime": "text/html", "description": "Opens anywhere; print it to PDF from the browser."},
    "pdf": {"name": "PDF", "kind": "report", "ext": "pdf", "mime": "application/pdf", "description": "To send, file or print."},
    "docx": {"name": "Word", "kind": "report", "ext": "docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "description": "For Word, Pages and Google Docs, on light paper."},
    "xlsx": {"name": "Excel", "kind": "report", "ext": "xlsx", "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "description": "A workbook: the plan with a chart, then one sheet per register with totals."},
}
DATA_WRITERS = {"csv": csv_zip, "json": everything_json, "ics": calendar, "beancount": beancount, "ledger": ledger, "qif": qif, "ofx": ofx}
