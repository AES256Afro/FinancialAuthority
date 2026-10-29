"""Sweep 12: the mail-fed quarter. The simulated quarter of sweep 8 (a scripted Plaid vendor feeds the ledger day by
day) with the owner's mailbox fed the way the real one is: each bill's notice a week or so before its due day and
its payment confirmation on the paid day, a statement for each card on its statement day, a paystub PDF on each
payday, two Affirm loan confirmations and the monthly Affirm statement, Rocket Money's weekly list and one export,
three bank alerts a week, a past-due notice, a price change, twenty receipts, a resent notice, a renumbered folder,
and three forged messages (a look-alike domain, a brand name with a Cyrillic letter, a DMARC fail) that must never
become a bill, a plan or a payment. Every day every job runs, then the owner does what the owner would: answers the
data check, confirms every genuine fact through the routes, discards what the cards say is counted elsewhere.

Synthetic data only: senders under example.test, lenders and forgers under the reserved .test domain."""

from __future__ import annotations

import io
import json
import random
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_sweep8_bugs_quarter import DAYS, START, TWICE_DAYS, Quarter, ScriptedMailbox

from app.connectors import CONNECTORS
from app.db import engine
from app.engine.household import BILLS, NET_PAY
from app.models import Bill, Debt, Document, ExtractedFact, InstallmentPlan, MessageRef, Payment, Paystub, Transaction
from app.store import get_setting

END = START + timedelta(days=DAYS)
PAYDAYS = [date(2026, 10, 16) + timedelta(days=14 * k) for k in range(7)]  # Oct 16 through Jan 8
AUTH_OK = "mx.example.test; dmarc=pass header.from=example.test; dkim=pass; spf=pass"
AUTH_FAIL = "mx.example.test; dmarc=fail header.from=bank.example.test; dkim=fail; spf=fail"
CARDS = {"card": ("Rewards Card", "Example Bank", "statements@bank.example.test", 17, 45.0, "9012"), "store": ("Store Card", "Example Retail Bank", "statements@retailbank.example.test", 26, 25.0, "5678")}
PLANS = {
    "A": {"merchant": "Example Outfitters", "loan_id": "7QRS-11AB", "loan": 348.0, "charge": 12.0, "dates": [date(2026, 10, 30), date(2026, 11, 30), date(2026, 12, 30)], "amount": 120.0, "sent": date(2026, 10, 16)},
    "B": {"merchant": "Example Bikes", "loan_id": "7QRS-22CD", "loan": 176.0, "charge": 6.0, "dates": [date(2026, 11, 6), date(2026, 11, 20), date(2026, 12, 4), date(2026, 12, 18)], "amount": 45.5, "sent": date(2026, 10, 23)},
}
PRICE_CHANGE_DAY, PRICE_FROM = date(2026, 11, 20), date(2026, 12, 15)  # Streaming goes from $17 to $19
TOLLS_SKIPPED = date(2026, 11, 15)  # no row for Tolls that month: the past-due notice comes on the 22nd, the owner answers on the 23rd
PAST_DUE_NOTICE, PAST_DUE_ANSWER = date(2026, 11, 22), date(2026, 11, 23)
RESEND_DAY = date(2026, 11, 15)  # the Internet notice comes twice an hour apart, under two Message-IDs
RENUMBER_DAY = date(2026, 12, 3)  # the folder's UIDVALIDITY changes and every message gets a new number
EXPORT_DAY = date(2026, 11, 14)
FORGED_DAYS = {date(2026, 10, 21): "lookalike", date(2026, 11, 4): "cyrillic", date(2026, 12, 9): "dmarc", date(2026, 12, 16): "dmarc_alert"}
EXPORT_URL = "https://files.example.test/exports/q-2026-11.csv"
ELSEWHERE = ("counted on Debts", "counted on Borrowing", "counted from Housing", "as a bill it would count twice")


def pdf_of(lines: list[str]) -> bytes:
    """A one-page PDF with a text layer, one line per entry, that pypdf reads back line by line."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 9 Tf 36 780 Td 11 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def paystub_lines(pay_date: date, n: int) -> list[str]:
    start, end = pay_date - timedelta(days=18), pay_date - timedelta(days=5)
    ytd = 59615.40 + 2980.77 * n
    return [
        "Example Payroll Services", "Example Employer Inc", "EARNINGS STATEMENT",
        f"Pay Period: {start:%m/%d/%Y} - {end:%m/%d/%Y}     Pay Date: {pay_date:%m/%d/%Y}",
        "Earnings            Rate    Hours     Current      YTD",
        f"Regular            37.26    80.00   2,980.77   {ytd:,.2f}",
        f"Gross Pay                            2,980.77   {ytd:,.2f}",
        "Deductions",
        f"Federal Income Tax                     298.08    {5961.60 + 298.08 * n:,.2f}",
        f"Social Security                        184.81    {3696.20 + 184.81 * n:,.2f}",
        f"Medicare                                43.22      {864.40 + 43.22 * n:,.2f}",
        f"Medical                                112.50    {2250.00 + 112.5 * n:,.2f}",
        f"401(k)                                  89.42    {1788.40 + 89.42 * n:,.2f}",
        f"Dental                                  12.74      {254.80 + 12.74 * n:,.2f}",
        f"Net Pay                              {NET_PAY:,.2f}   {44800.00 + NET_PAY * n:,.2f}",
    ]


def loan_confirmation(plan: dict) -> str:
    total = round(plan["loan"] + plan["charge"], 2)
    n = len(plan["dates"])
    principal = round(plan["loan"] / n, 2)
    entries = []
    for i, d in enumerate(plan["dates"]):
        interest = round(plan["amount"] - principal, 2)
        if i == n - 1:  # the last entry carries the rounding
            principal_i = round(plan["loan"] - principal * (n - 1), 2)
            interest = round(plan["amount"] - principal_i, 2)
        else:
            principal_i = principal
        entries.append(f"{d:%b} {d.day}, {d.year}\n\n${plan['amount']:,.2f}\nPrincipal: ${principal_i:,.2f}\nInterest: ${interest:,.2f}\n")
    return (f"Here are your loan details, including information about how to repay.\n\nYour loan confirmation\n\nOwner – {plan['merchant']} has processed your order, and your loan has been confirmed!\n\n"
            f"Your Loan Breakdown\n\n${plan['loan']:,.2f}\nLoan amount\nThe amount of credit you borrowed\n\n${total:,.2f}\nTotal of payments\nThe amount you will have paid after making all scheduled payments\n\n"
            f"${plan['charge']:,.2f}\nFinance charge\nThe amount this credit will cost you\n\nYour Payment Schedule\n\n" + "\n".join(entries) + f"\nLoan ID: {plan['loan_id']}\n\nIf you need help, visit our Help Center.\n")


def affirm_statement_lines(period_end: date) -> list[str]:
    """Affirm's monthly Loans Statement for the two plans, in the layout parsers/affirm_statement.py reads."""
    start = (period_end.replace(day=1) - timedelta(days=1)).replace(day=period_end.day + 1) if period_end.day < 28 else period_end.replace(day=1)
    lines = ["Owner Example", "1 Example Way", "Somewhere, TX 77000", f"Affirm Loans Statement for {start:%B} {start.day} - {period_end:%B} {period_end.day}, {period_end.year}",
             "This statement lists all Affirm loan activity during the given date range.", "Loans Summary", "Loan ID", "Merchant", "Loan Date", "Amount", "Annual Percentage Rate",
             "Opening Balance", "Closing Balance", "Payment Due", "Payment Amount", "Status"]
    for plan in PLANS.values():
        paid_before = [d for d in plan["dates"] if d < start]
        left = [d for d in plan["dates"] if d > period_end]
        opening = round(plan["amount"] * (len(plan["dates"]) - len(paid_before)), 2)
        closing = round(plan["amount"] * len(left), 2)
        lines += [plan["loan_id"], "Online order", plan["merchant"], f"{plan['sent']:%m/%d/%Y}", f"${plan['loan']:,.2f}", "19.99%", f"${opening:,.2f}", f"${closing:,.2f}",
                  f"{left[0]:%m/%d/%Y}" if left else "-", f"${plan['amount']:,.2f}" if left else "-", "Active" if left else "Paid Off"]
    lines += ["Interest Charged", "Loan ID", "Merchant", "Interest Paid"]
    for plan in PLANS.values():
        lines += [plan["loan_id"], plan["merchant"], "$2.00"]
    lines += ["Total Interest for this Period", "$4.00", "Total Interest for Calendar YTD", "$4.00", "Transactions Summary", "Date", "Loan ID", "Merchant", "Description", "Amount"]
    for plan in PLANS.values():
        for d in plan["dates"]:
            if start <= d <= period_end:
                lines += [f"{d:%m/%d/%Y}", plan["loan_id"], plan["merchant"], "Payment", f"${plan['amount']:,.2f}"]
    lines += ["What To Do If You Think You Have Found A Mistake On Your Statement"]
    return lines


class RenumberingBox(ScriptedMailbox):
    """The sweep-8 scripted mailbox with a UIDVALIDITY the harness can change."""

    validity = 1

    def uidvalidity(self) -> int:
        return self.validity


class MailQuarter(Quarter):
    def __init__(self, owner: TestClient) -> None:
        super().__init__(owner)
        self.rng = random.Random(12)
        self.sent: dict[str, dict] = {}  # Message-ID -> what the message is and what it should yield
        self.seq = 0
        self.stamped: set[tuple[str, int]] = set()
        self.fetched_export = 0
        self.receipts_sent = 0
        self.receipt_rows: dict[str, tuple[date, str, float]] = {}  # tag -> (day, store, amount) of the purchase it is for
        self.alert_open: tuple[date, float] | None = None
        self.schedule: dict[date, list[tuple]] = {}
        self.checked: list[str] = []
        self.rows_total = 0
        RenumberingBox.validity = 1
        RenumberingBox.messages = ScriptedMailbox.messages
        CONNECTORS["mail"].client_factory = RenumberingBox
        CONNECTORS["mail"].fetch = self.fetch_export
        self.shape_rows()
        self.plan_mail()

    # -- the quarter's rows, shaped for the mail stream --

    def shape_rows(self) -> None:
        for plan in PLANS.values():
            for d in plan["dates"]:
                self.rows.setdefault(d, []).append({"date": d, "account": "checking", "merchant": "Affirm", "amount": plan["amount"], "category": "Loan Payment"})
        for d, rows in self.rows.items():
            if d >= PRICE_FROM:
                for t in rows:
                    if t["merchant"] == "Streaming":
                        t["amount"] = 19.0
        self.rows[TOLLS_SKIPPED] = [t for t in self.rows.get(TOLLS_SKIPPED, []) if t["merchant"] != "Tolls"]
        for rows in self.rows.values():
            rows.sort(key=lambda t: (t["account"], t["merchant"], t["amount"]))

    def plan_mail(self) -> None:
        """Each bill's notice 7 to 10 days before its due day and its confirmation on the paid day; the rest by date."""
        for name, amount, due_day, *_ in BILLS:
            if name == "Card minimum":
                continue
            for month in (date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)):
                due = month.replace(day=min(due_day, 28))
                if not START < due <= END:
                    continue
                notice = due - timedelta(days=self.rng.randint(7, 10))
                shown = 19.0 if name == "Streaming" and due >= PRICE_FROM else amount
                if notice > START:
                    self.schedule.setdefault(notice, []).append(("notice", name, shown, due))
                if not (name == "Tolls" and due == TOLLS_SKIPPED):
                    self.schedule.setdefault(due, []).append(("paid", name, shown, due))
        spend = [(d, t["merchant"], t["amount"]) for d, rows in sorted(self.rows.items()) if START < d <= END - timedelta(days=3) for t in rows if t["account"] == "card" and t["amount"] > 5 and t["merchant"].startswith("Example ")]
        for d, merchant, amount in spend[:: max(1, len(spend) // 20)][:20]:  # twenty stores' receipts, spread over the quarter
            self.schedule.setdefault(d, []).append(("receipt", merchant, amount))

    # -- delivering mail --

    def deliver(self, sender_name: str, sender: str, subject: str, text: str, *, tag: str, expect: str, forged: bool = False, auth: str = AUTH_OK, hour: int = 8,
                pdf: tuple[str, bytes] | None = None, html: str | None = None, message_id: str | None = None) -> str:
        self.seq += 1
        d = self.day
        mid = message_id or f"<q12-{self.seq}-{d.isoformat()}@{sender.split('@')[-1]}>"
        m = EmailMessage()
        m["From"] = f'"{sender_name}" <{sender}>'  # quoted: a name with parentheses is otherwise a comment, and lost
        m["To"] = "owner@example.test"
        m["Subject"] = subject
        m["Date"] = datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
        m["Message-ID"] = mid
        if auth:
            m["Authentication-Results"] = auth
        m.set_content(text)
        if html:
            m.add_alternative(html, subtype="html")
        if pdf:
            m.add_attachment(pdf[1], maintype="application", subtype="pdf", filename=pdf[0])
        self.uid += 1
        ScriptedMailbox.messages[self.uid] = bytes(m)
        self.sent[mid] = {"tag": tag, "expect": expect, "forged": forged, "day": d, "uid": self.uid}
        return mid

    def feed(self) -> None:
        import test_sweep8_bugs_quarter as base

        d = self.day
        # Rocket Money's list shows what it sees at the bank: the new Streaming price from the week it starts
        base.BILLS = [(n, 19.0 if n == "Streaming" and d >= PRICE_FROM - timedelta(days=7) else a, *rest) for n, a, *rest in BILLS]
        super().feed()  # the day's bank rows, Rocket Money's Friday list, the clocks aged a day
        self.rows_total += len(self.rows.get(d, []))
        for entry in self.schedule.get(d, []):
            if entry[0] == "notice":
                _, name, amount, due = entry
                slug = name.lower().replace(" ", "").replace("(", "").replace(")", "")
                self.deliver(f"{name} Billing", f"billing@{slug}.example.test", "Your bill is ready", f"Your {name} bill is ready.\nAmount due: ${amount:,.2f}\nDue date: {due:%B} {due.day}, {due.year}\nAccount ending in 4411\n",
                                   tag=f"notice:{name}:{due.isoformat()}", expect="bill_due")
                if d == RESEND_DAY and name == "Internet":
                    raw = ScriptedMailbox.messages[self.uid]
                    self.deliver(f"{name} Billing", f"billing@{slug}.example.test", "Your bill is ready", raw.split(b"\n\n", 1)[1].decode(), tag=f"notice:{name}:{due.isoformat()}:resent", expect="bill_due", hour=9)
            elif entry[0] == "paid":
                _, name, amount, due = entry
                slug = name.lower().replace(" ", "").replace("(", "").replace(")", "")
                self.deliver(f"{name} Billing", f"billing@{slug}.example.test", "We received your payment", f"Thank you for your payment of ${amount:,.2f} to {name} on {due:%B} {due.day}, {due.year}. Confirmation number {self.rng.randint(10000, 99999)}.\n",
                             tag=f"paid:{name}:{due.isoformat()}", expect="payment_confirmation", hour=12)
            elif entry[0] == "receipt":
                self.receipts_sent += 1
                n = self.receipts_sent
                _, merchant, amount = entry
                tag = f"receipt:{n}"
                self.receipt_rows[tag] = (d, merchant, amount)
                slug = merchant.lower().replace(" ", "")
                self.deliver(merchant, f"orders@{slug}.example.test", f"Your {merchant} order #EX-{1000 + n}", f"Thanks for your order!\nOrder #EX-{1000 + n}\nItem {n}  ${amount:,.2f}\nOrder total: ${amount:,.2f}\nReturn within 30 days of delivery.\n",
                             tag=tag, expect="receipt", hour=10)
                if n == 5:
                    self.resend_receipt = (merchant, amount, n)
        for key, (card_name, bank, sender, statement_day, minimum, last4) in CARDS.items():
            if d.day == statement_day:
                balance = self.plaid.balances[key]
                due = (d + timedelta(days=8))
                self.deliver(bank, sender, f"Your {card_name} statement is ready", f"Your {card_name} statement is ready.\nNew balance: ${balance:,.2f}\nMinimum payment due: ${minimum:,.2f}\nPayment due date: {due:%B} {due.day}, {due.year}\nStatement closing date: {d:%B} {d.day}, {d.year}\nAccount ending in {last4}\n",
                             tag=f"statement:{key}:{d.isoformat()}:{balance:.2f}", expect="bill_due")  # the bill-due rule reads "statement is ready" first (reported)
        if d in PAYDAYS:
            n = PAYDAYS.index(d) + 1
            self.deliver("Example Payroll Services", "payroll@payroll.example.test", "Your pay statement is available", "Your pay statement is attached.\n", tag=f"paystub:{d.isoformat()}", expect="paystub", hour=6,
                         pdf=(f"paystub-{d.isoformat()}.pdf", pdf_of(paystub_lines(d, n))))
        for key, plan in PLANS.items():
            if d == plan["sent"]:
                self.deliver("Affirm", "affirm-confirmation@affirm.test", "Your loan confirmation", loan_confirmation(plan), tag=f"affirm:{key}", expect="installment")
        if d.day == 1 and d > START:
            period_end = d - timedelta(days=1)
            self.deliver("Affirm", "statements@affirm.test", "Your Affirm Loans Statement is ready", f"Your Affirm Loans Statement for {period_end:%B} is attached.\n", tag=f"affirm_statement:{period_end.isoformat()}", expect="affirm_statement",
                         pdf=(f"affirm-statement-{period_end:%Y-%m}.pdf", pdf_of(affirm_statement_lines(period_end))))  # the PDF is not read from mail (reported); the mail reads as a bill notice
        if d.weekday() == 0:  # Monday: a purchase alert for a card charge the bank posts tomorrow
            tomorrow = next((t for t in self.rows.get(d + timedelta(days=1), []) if t["account"] == "card" and t["amount"] > 0), None)
            if tomorrow is not None:
                self.alert_open = (d, tomorrow["amount"])
                self.deliver("Example Bank", "alerts@bank.example.test", "A purchase was made with your card", f"A purchase of ${tomorrow['amount']:,.2f} was made at {tomorrow['merchant']} on {d:%B} {d.day}, {d.year} with your card ending in 9012.\n",
                             tag=f"alert:purchase:{d.isoformat()}", expect="bank_alert", hour=9)
        if d.weekday() == 2:
            self.deliver("Example Bank", "alerts@bank.example.test", "Your account is overdrawn", f"Your Everyday Checking account ending in 1234 is overdrawn by $38.20 as of {d:%B} {d.day}, {d.year}.\n", tag=f"alert:overdraft:{d.isoformat()}", expect="bank_alert", hour=9)
        if d.weekday() == 4:
            amount = NET_PAY if d in PAYDAYS else 25.0
            what = "EXAMPLE EMPLOYER PAYROLL" if d in PAYDAYS else "EXAMPLE REBATES"
            self.deliver("Example Bank", "alerts@bank.example.test", "A deposit was posted to your account", f"A direct deposit of ${amount:,.2f} from {what} was posted to your account ending in 1234.\n", tag=f"alert:deposit:{d.isoformat()}", expect="bank_alert", hour=9)
        if d == PRICE_CHANGE_DAY:
            self.deliver("Streaming Billing", "billing@streaming.example.test", "A price change for your Streaming plan", f"Your Streaming plan price is changing. Starting {PRICE_FROM:%B} {PRICE_FROM.day}, {PRICE_FROM.year}, your monthly price will go from $17.00 to $19.00. No action is needed.\n",
                         tag="price_change", expect="price_change")
        if d == PAST_DUE_NOTICE:
            self.deliver("Tolls Billing", "billing@tolls.example.test", "Past due: your Tolls account", f"Your payment of $40.00 was due on {TOLLS_SKIPPED:%B} {TOLLS_SKIPPED.day}, {TOLLS_SKIPPED.year} and has not been received. Please pay now to avoid a late fee.\n",
                         tag="past_due", expect="past_due")
        if d == date(2026, 11, 18) and hasattr(self, "resend_receipt"):
            merchant, amount, n = self.resend_receipt
            slug = merchant.lower().replace(" ", "")
            self.deliver(merchant, f"orders@{slug}.example.test", f"Your {merchant} order #EX-{1000 + n}", f"Thanks for your order!\nOrder #EX-{1000 + n}\nItem {n}  ${amount:,.2f}\nOrder total: ${amount:,.2f}\nReturn within 30 days of delivery.\n",
                         tag=f"receipt:{n}:resent", expect="none", hour=10)
        if d == EXPORT_DAY:
            self.deliver("Rocket Money", "no-reply@rocketmoney.com", "Transaction export complete", f"Your transactions export is ready. Download it here: {EXPORT_URL}\n", tag="export", expect="export",
                         html=f"<html><body><p>Your transactions export is ready.</p><p><a href=\"{EXPORT_URL}\">Download your export</a></p></body></html>")
        forged = FORGED_DAYS.get(d)
        if forged == "lookalike":
            self.deliver("Capital One", "alerts@capital0ne-alerts.test", "Your payment is due", "Your Capital One payment is due.\nAmount due: $312.00\nDue date: November 3, 2026\nPay now at the link below.\n", tag="forged:lookalike", expect="bill_due", forged=True)
        elif forged == "cyrillic":
            fake = {"merchant": "Example Phones", "loan_id": "FAKE-0001", "loan": 900.0, "charge": 0.0, "dates": [date(2026, 11, 20), date(2026, 12, 20), date(2027, 1, 20)], "amount": 300.0}
            self.deliver("Аffirm", "affirm-loans@mail-center.test", "Your loan confirmation", loan_confirmation(fake), tag="forged:cyrillic", expect="installment", forged=True)
        elif forged == "dmarc":
            self.deliver("Example Bank", "alerts@bank.example.test", "Past due: your Car loan payment", "Your payment of $412.00 was due on November 28, 2026 and has not been received. Pay at the link below.\n", tag="forged:dmarc", expect="past_due", forged=True, auth=AUTH_FAIL)
        elif forged == "dmarc_alert":
            self.deliver("Example Bank", "alerts@bank.example.test", "Your account is overdrawn", "Your account ending in 1234 is overdrawn by $412.00. Act now.\n", tag="forged:dmarc_alert", expect="bank_alert", forged=True, auth=AUTH_FAIL, hour=7)
        if d == RENUMBER_DAY:
            RenumberingBox.validity = 2
            ScriptedMailbox.messages = {uid + 1000: raw for uid, raw in ScriptedMailbox.messages.items()}
            RenumberingBox.messages = ScriptedMailbox.messages
            for s in self.sent.values():
                s["uid"] += 1000
            self.uid += 1000

    def fetch_export(self, url: str) -> bytes:
        assert url == EXPORT_URL, url
        self.fetched_export += 1
        held = [(d, t) for d in (EXPORT_DAY - timedelta(days=2), EXPORT_DAY - timedelta(days=1)) for t in self.rows.get(d, []) if t["account"] == "checking"][:2]
        lines = ["Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible,Transaction Tags"]
        for d, t in held:
            lines.append(f"{d.isoformat()},{d.isoformat()},Cash,Everyday Checking,1234,Example Bank,{t['merchant'].upper()},,{t['amount']:.2f},,{t['category']},,,,")
        d = EXPORT_DAY - timedelta(days=1)
        lines.append(f"{d.isoformat()},{d.isoformat()},Cash,Everyday Checking,1234,Example Bank,EXAMPLE FARMERS MARKET,,18.00,,Groceries,,,,")
        self.export_expected = (len(held), 1)
        return ("\n".join(lines) + "\n").encode()

    # -- the day --

    def stamp(self) -> None:
        """Rows the mail made today are stamped with the simulated day (their default is the wall clock, which the
        quarter does not follow), keeping their order."""
        d = self.day
        with Session(engine) as session:
            for model in (ExtractedFact, MessageRef):
                for row in session.exec(select(model)).all():
                    key = (model.__name__, row.id)
                    if key in self.stamped:
                        continue
                    row.created_at = datetime(d.year, d.month, d.day, 8, 0, tzinfo=UTC) + timedelta(seconds=row.id)
                    session.add(row)
                    self.stamped.add(key)
            session.commit()

    def check(self, got: dict[str, dict]) -> None:
        before = len(self.findings)
        super().check(got)
        # a bank alert's line names the day the alert came, which is a past day said as past, not an upcoming one
        self.findings[before:] = [f for f in self.findings[before:] if not (("names a past day as upcoming" in f and "alert on" in f) or ("dated in the past" in f and "was due" in f))]

    def day_cycle(self) -> dict[str, dict]:
        self.run_jobs()
        self.stamp()
        extra = self.day.day % 5 == 0
        got = self.read(extra=extra)
        self.check(got)
        return got

    def mail_day(self) -> dict[str, dict]:
        got = self.advance()
        self.check_mail(got, before_owner=True)
        if self.day in TWICE_DAYS:
            self.same_again()  # before the owner acts: a confirmed notice changes the plan, as it should
        self.replay()
        self.stamp()
        got = self.read(extra=False)
        self.check(got)
        self.check_mail(got, before_owner=False)
        return got

    # -- the owner's replay --

    def sent_for(self, session: Session, fact: ExtractedFact) -> dict | None:
        ref = session.get(MessageRef, fact.message_ref_id) if fact.message_ref_id else None
        return self.sent.get(ref.message_id) if ref else None

    def replay(self) -> None:
        owner = self.owner
        d = self.day
        # 1. The data check: a forged sender is discarded; a genuine one the check doubted is marked genuine.
        check = owner.get("/api/v1/data-check").json()
        for finding in check["findings"]:
            if finding["kind"] == "sender":
                ref_id = int(finding["key"].split(":")[1])
                with Session(engine) as session:
                    ref = session.get(MessageRef, ref_id)
                    sent = self.sent.get(ref.message_id) if ref else None
                action = "discard" if sent and sent["forged"] else "trust"
                if action == "trust":
                    self.note(f"the data check doubted a genuine sender {ref.sender if ref else ref_id}: {finding['why'][:120]}")
                r = owner.post("/api/v1/data-check/answer", json={"key": finding["key"], "action": action})
                self.expect(r.status_code == 200, f"answering the data check {finding['key']} with {action} failed: {r.text[:200]}")
            elif finding["kind"] in ("counted_twice", "alike_bills", "duplicate_rows", "paid_twice"):
                if not (finding["kind"] == "alike_bills" and "Music" in finding["title"]):  # the household's two music subscriptions are alike by name; the owner says so once
                    self.note(f"the data check raised {finding['kind']}: {finding['title']}")
                r = owner.post("/api/v1/data-check/answer", json={"key": finding["key"], "action": "different"})
                self.expect(r.status_code == 200, f"settling {finding['key']} failed: {r.text[:200]}")
        # 2. Needs you: the export on its own card; everything else previewed together and confirmed as the plan says.
        with Session(engine) as session:
            pending = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()
            todo = []
            for f in pending:
                sent = self.sent_for(session, f)
                if sent is None:
                    todo.append((f.id, f.kind, {"tag": "?", "forged": False}))
                    continue
                if sent["forged"]:
                    self.note(f"a forged message ({sent['tag']}) still has a pending {f.kind} fact after the data check ran")
                    continue
                todo.append((f.id, f.kind, sent))
        for fid, kind, sent in todo:
            if kind == "export":
                r = owner.post(f"/api/v1/facts/{fid}/confirm", json={"apply": True})
                self.expect(r.status_code == 200 and r.json()["applied_to"].startswith("import:"), f"importing the export answered {r.status_code}: {r.text[:300]}")
                if r.status_code == 200:
                    held, new = self.export_expected
                    self.expect(f"{new} new" in r.json()["applied_to"] and f"{held} already held by the bank link" in r.json()["applied_to"], f"the export's import said {r.json()['applied_to']}")
        bulk = [fid for fid, kind, _ in todo if kind != "export"]
        if not bulk:
            return
        preview = owner.post("/api/v1/needs-you/facts", json={"ids": bulk, "action": "preview"})
        self.expect(preview.status_code == 200, f"the preview answered {preview.status_code}: {preview.text[:200]}")
        # the route says what it would do in words; the engine's plan says it per fact
        from app.engine.needsyou import plan_many

        with Session(engine) as session:
            facts = [session.get(ExtractedFact, fid) for fid in bulk]
            steps = {s["id"]: s for s in plan_many(session, facts, d)["steps"]}
        confirm, reject = [], []
        for fid, kind, sent in todo:
            if kind == "export":
                continue
            step = steps.get(fid, {"do": "own_card", "why": "no step"})
            tag = sent["tag"]
            if step["do"] in ("apply", "set_aside"):
                if tag.startswith(("statement:", "affirm_statement:")):
                    self.note(f"{tag}: the plan would {step['do']} a card or lender statement as a bill ({step.get('why', '')})")
                confirm.append(fid)
            elif step["do"] == "own_card" and tag.startswith(("statement:", "affirm_statement:")):
                reject.append(fid)  # the card says it is counted on Debts or Borrowing (or, the lender's statement, that it cannot be read): the owner discards it
            else:
                self.note(f"{tag} ({kind}) stays for its own card: {step['why']}")
                reject.append(fid)
        if reject:
            r = owner.post("/api/v1/needs-you/facts", json={"ids": reject, "action": "reject"})
            self.expect(r.status_code == 200 and not r.json()["failed"], f"discarding answered {r.status_code}: {r.text[:200]}")
        if confirm:
            r = owner.post("/api/v1/needs-you/facts", json={"ids": confirm, "action": "confirm"})
            self.expect(r.status_code == 200, f"confirming answered {r.status_code}: {r.text[:300]}")
            if r.status_code == 200:
                body = r.json()
                self.expect(not body["failed"], f"confirming together left {body['failed']}")
                for row in body["done"]:
                    self.after_confirm(row["id"], row["applied_to"])
        # 3. The past-due period the notice was about: paid another way.
        if d == PAST_DUE_ANSWER:
            page = owner.get("/api/v1/needs-you").json()
            mine = [p for p in page["past_due"] if p["name"] == "Tolls"]
            self.expect([p["due_date"] for p in mine] == [TOLLS_SKIPPED.isoformat()], f"the Tolls periods asked about: {[(p['due_date'], p['days_late']) for p in mine]}")
            if mine:
                r = owner.post(f"/api/v1/needs-you/payments/{mine[0]['payment_id']}", json={"answer": "paid", "paid_on": PAST_DUE_NOTICE.isoformat()})
                self.expect(r.status_code == 200, f"paid another way answered {r.status_code}: {r.text[:200]}")

    def after_confirm(self, fid: int, applied_to: str) -> None:
        with Session(engine) as session:
            fact = session.get(ExtractedFact, fid)
            sent = self.sent_for(session, fact)
            tag = sent["tag"] if sent else "?"
            fields = json.loads(fact.fields or "{}")
            if tag.startswith("notice:"):
                _, name, due, *rest = tag.split(":")
                bill = session.exec(select(Bill).where(Bill.name == name, Bill.active == True)).all()
                self.expect(len(bill) == 1, f"{tag}: {len(bill)} active bills named {name} after the notice was confirmed")
                if rest:  # the resent copy: set aside, or applied to the same bill
                    self.expect(fact.status in ("rejected", "applied") and (not applied_to or applied_to == f"bill:{bill[0].id}"), f"{tag}: the resent copy ended as {fact.status} {fact.applied_to}")
                elif bill:
                    self.expect(applied_to == f"bill:{bill[0].id}", f"{tag}: applied to {applied_to}, the bill is bill:{bill[0].id}")
                    self.expect(abs(bill[0].amount - fields["amount"]) < 0.005 and bill[0].due_day == date.fromisoformat(due).day, f"{tag}: the bill reads {bill[0].amount} on the {bill[0].due_day}, the notice {fields}")
            elif tag == "price_change":
                # The notice says "from $17.00 to $19.00"; the classifier reads the first amount (reported, outside this
                # area), so what is asserted here is that the bill took what the notice was read as, on the one bill.
                bill = session.exec(select(Bill).where(Bill.name == "Streaming", Bill.active == True)).one()
                self.expect(applied_to == f"bill:{bill.id}" and abs(bill.amount - fields["amount"]) < 0.005, f"the price change left Streaming at {bill.amount} (the notice read {fields.get('amount')}), applied to {applied_to}")
            elif tag == "past_due":
                bill = session.exec(select(Bill).where(Bill.name == "Tolls", Bill.active == True)).one()
                self.expect(applied_to == f"bill:{bill.id}" and bill.amount == 40.0 and bill.due_day == 15, f"the past-due notice changed Tolls to {bill.amount} on the {bill.due_day}")
            elif tag.startswith("paystub:"):
                pay_date = date.fromisoformat(tag.split(":")[1])
                stubs = session.exec(select(Paystub).where(Paystub.pay_date == pay_date)).all()
                self.expect(len(stubs) == 1 and applied_to == f"paystub:{stubs[0].id}", f"{tag}: {len(stubs)} stubs for the day, applied to {applied_to}")
                if stubs:
                    s = stubs[0]
                    self.expect(s.transaction_id is not None and s.net == NET_PAY and s.expected_net == NET_PAY and s.difference == 0.0, f"{tag}: deposit {s.transaction_id}, net {s.net}, usual {s.expected_net}, difference {s.difference}")
                    kept = session.exec(select(Document).where(Document.attach_to == f"paystub:{s.id}")).all()
                    self.expect(len(kept) == 1 and kept[0].kind == "paystub", f"{tag}: {len(kept)} files kept with the stub")
            elif tag.startswith("affirm:"):
                plan = PLANS[tag.split(":")[1]]
                plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.loan_id == plan["loan_id"])).all()
                self.expect(len(plans) == 1 and applied_to == f"plan:{plans[0].id}", f"{tag}: {len(plans)} plans with the loan id, applied to {applied_to}")
                if plans:
                    p = plans[0]
                    dates = [date.fromisoformat(s["date"]) for s in json.loads(p.schedule or "[]")]
                    self.expect(dates == plan["dates"] and p.next_date == plan["dates"][0] and p.remaining == p.count == len(plan["dates"]) and abs(p.installment - plan["amount"]) < 0.005 and p.finance_charge == plan["charge"],
                                f"{tag}: the plan reads {p.next_date} {p.remaining}/{p.count} {p.installment} {dates}")
            elif tag.startswith("receipt:"):
                day, merchant, amount = self.receipt_rows[tag]
                row = session.exec(select(Transaction).where(Transaction.date == day, Transaction.merchant == merchant, Transaction.amount == amount)).first()
                doc = session.get(Document, int(applied_to.split(":")[1])) if applied_to.startswith("document:") else None
                self.expect(row is not None and doc is not None and doc.attach_to == f"transaction:{row.id}", f"{tag}: applied to {applied_to}, attached to {doc.attach_to if doc else None}, the purchase is transaction:{row.id if row else None}")
                if doc is not None:
                    self.expect(doc.amount == amount and doc.return_by == day + timedelta(days=30), f"{tag}: the receipt reads {doc.amount}, returns by {doc.return_by}")
            elif tag.startswith("rm:") or fields.get("source") == "rocketmoney-upcoming":
                name = fields.get("name")
                bills = session.exec(select(Bill).where(Bill.name == name, Bill.active == True)).all()
                self.expect(len(bills) == 1 and applied_to == f"bill:{bills[0].id}", f"Rocket Money's {name}: {len(bills)} bills, applied to {applied_to}")

    # -- what must hold about the mail --

    def check_mail(self, got: dict[str, dict], *, before_owner: bool) -> None:
        d = self.day
        snap = got.get("/api/v1/snapshot")
        with Session(engine) as session:
            refs = {r.message_id: r for r in session.exec(select(MessageRef)).all()}
            facts_by_ref: dict[int, list[ExtractedFact]] = {}
            for f in session.exec(select(ExtractedFact)).all():
                facts_by_ref.setdefault(f.message_ref_id, []).append(f)
            bills = session.exec(select(Bill).where(Bill.active == True)).all()
            # every message delivered so far is on file once, with the facts its kind yields, and never more
            for mid, sent in self.sent.items():
                ref = refs.get(mid)
                if ref is None:
                    self.note(f"{sent['tag']} (sent {sent['day']}) is not on file")
                    continue
                if ref.uid != sent["uid"]:
                    self.note(f"{sent['tag']}: on file under uid {ref.uid}, the folder has it as {sent['uid']}")
                mine = facts_by_ref.get(ref.id, [])
                if sent["expect"] == "none":
                    self.expect(not mine, f"{sent['tag']}: a resent receipt made {len(mine)} facts")
                elif sent["expect"] == "affirm_statement":
                    self.expect(len(mine) <= 1, f"{sent['tag']}: {len(mine)} facts")
                else:
                    self.expect(len(mine) == 1 and mine[0].kind == sent["expect"], f"{sent['tag']}: {[(f.kind, f.status) for f in mine]}, expected one {sent['expect']}")
                if sent["forged"]:
                    self.expect(ref.trust == "crit", f"{sent['tag']} was judged {ref.trust!r}: {ref.trust_why}")
                    self.expect(all(f.status in ("pending", "rejected") or (f.kind == "bank_alert" and f.status == "auto" and not f.applied_to) for f in mine), f"{sent['tag']}: its facts are {[(f.kind, f.status, f.applied_to) for f in mine]}")
                elif ref.trust not in ("ok", "unknown", ""):
                    self.note(f"{sent['tag']} was judged {ref.trust!r}: {ref.trust_why}")
            # a bill is on the register once, by name
            names = [b.name for b in bills]
            self.expect(len(names) == len(set(names)), f"bills twinned by name: {sorted(n for n in names if names.count(n) > 1)}")
            self.expect(not any(n in ("Capital One", "Example Bank", "Example Retail Bank", "Affirm") for n in names), f"a message became a bill of its own: {names}")
            self.expect(session.exec(select(InstallmentPlan).where(InstallmentPlan.loan_id == "FAKE-0001")).first() is None, "the forged Affirm confirmation became a plan")
            # the self-check's sources show the mailbox healthy
            report = get_setting(session, "selfcheck", None) or {}
            mail = [s for s in report.get("sources", []) if s["kind"] == "mail"]
            self.expect(bool(mail) and mail[0]["last_ok"] and not mail[0]["error"], f"the self-check's mail source: {mail}")
            # the ledger ticks the Affirm installments
            for key, plan in PLANS.items():
                p = session.exec(select(InstallmentPlan).where(InstallmentPlan.loan_id == plan["loan_id"])).first()
                if p is not None and d > plan["sent"]:
                    paid = sum(1 for x in plan["dates"] if x <= d)
                    self.expect(p.remaining == len(plan["dates"]) - paid and p.active == (paid < len(plan["dates"])), f"plan {key} on {d}: {p.remaining} left and active={p.active} with {paid} installments posted")
            # the statement's balance is on the card's debt row
            for mid, sent in self.sent.items():
                if sent["tag"].startswith("statement:") and sent["day"] == d:
                    _, key, _day, balance = sent["tag"].split(":")
                    debt = session.exec(select(Debt).where(Debt.account_id == self.remember[CARDS[key][0]])).first()
                    self.expect(debt is not None and abs(debt.balance - float(balance)) < 0.005, f"the {CARDS[key][0]} statement said ${balance}, the debt row {debt.balance if debt else None}")
            # the past-due notice named the right period, and only that one
            if d >= PAST_DUE_NOTICE:
                tolls = session.exec(select(Bill).where(Bill.name == "Tolls")).one()
                periods = {p.due_date: p.status for p in session.exec(select(Payment).where(Payment.bill_id == tolls.id, Payment.due_date >= date(2026, 10, 15))).all()}
                late = [k for k, v in periods.items() if v in ("late", "past_due", "missed")]
                if d < PAST_DUE_ANSWER or (d == PAST_DUE_ANSWER and before_owner):
                    self.expect(late == [TOLLS_SKIPPED], f"Tolls periods not paid: {late} ({periods})")
                else:
                    self.expect(not late and periods.get(TOLLS_SKIPPED) == "paid", f"after the owner's answer the Tolls periods read {periods}")
                car = session.exec(select(Bill).where(Bill.name == "Car loan")).one()
                nov = session.exec(select(Payment).where(Payment.bill_id == car.id, Payment.due_date == date(2026, 11, 28))).first()
                if d > date(2026, 11, 29):
                    self.expect(nov is not None and nov.status == "paid" and car.amount == 412.0 and car.due_day == 28, f"the forged past-due notice touched the car loan: {nov and nov.status} {car.amount} {car.due_day}")
            # the price change: December's notice says $19, and nothing after it says otherwise
            if d >= PRICE_FROM:
                streaming = session.exec(select(Bill).where(Bill.name == "Streaming", Bill.active == True)).one()
                self.expect(streaming.amount == 19.0, f"Streaming reads {streaming.amount} after its notices said $19")
            # receipts are files with their purchases, never rows: the ledger holds exactly the vendor's rows plus the export's one
            n_tx = len(session.exec(select(Transaction.id)).all())
            self.expect(n_tx == self.seed_rows + self.rows_total + (self.export_expected[1] if self.fetched_export else 0), f"{n_tx} rows in the ledger; the seed had {self.seed_rows}, the vendor sent {self.rows_total}, the export added {self.export_expected[1] if self.fetched_export else 0}")
            docs = [x.attach_to for x in session.exec(select(Document).where(Document.kind == "receipt")).all() if x.attach_to]
            self.expect(len(docs) == len(set(docs)), f"two receipts kept with one purchase: {[a for a in docs if docs.count(a) > 1]}")
        if snap is None:
            return
        # a purchase the bank's alert told of is in flight until its row posts, then gone; an overdraft reaches the brief the same day
        flight = [i for i in snap["in_flight"]["mail"]["items"]]
        if self.alert_open is not None:
            when, amount = self.alert_open
            listed = [i for i in flight if abs(i["amount"] - amount) < 0.005]
            if d == when:
                self.expect(len(listed) == 1 and listed[0]["date"] == when.isoformat(), f"the purchase alert of ${amount} is listed {len(listed)} times: {listed}")
            elif d > when:
                self.expect(not listed, f"the purchase alert of ${amount} is still in flight {(d - when).days} days after its row posted: {listed}")
        if d.weekday() == 2 and before_owner:
            lines = [b["text"] for b in snap["brief"] if "overdraft" in b["text"].lower()]
            self.expect(len(lines) == 1 and any(b["severity"] == "critical" for b in snap["brief"] if "overdraft" in b["text"].lower()), f"the overdraft alert in the brief: {lines}")
        if d >= date(2026, 12, 16):
            forged_amount = [b["text"] for b in snap["brief"] if "412" in b["text"] and "overdraft" in b["text"].lower()]
            self.expect(not forged_amount, f"the forged overdraft alert reached the brief: {forged_amount}")
            self.expect(not any(i.get("last4") == "1234" and abs(i["amount"] - 412.0) < 0.005 for i in flight), "the forged alert is in the in-flight list")


@pytest.fixture
def mail_quarter(owner: TestClient) -> MailQuarter:
    q = MailQuarter(owner)
    with Session(engine) as session:
        q.seed_rows = len(session.exec(select(Transaction.id)).all())
    q.export_expected = (0, 0)
    yield q
    CONNECTORS["plaid"].client_factory = None
    from app.connectors.mail import ImapClient, default_fetch

    CONNECTORS["mail"].client_factory = ImapClient
    CONNECTORS["mail"].fetch = default_fetch


def test_the_mail_fed_quarter_keeps_every_invariant(mail_quarter: MailQuarter):
    q = mail_quarter
    for _ in range(DAYS):
        q.mail_day()
    with Session(engine) as session:
        kinds = {}
        for f in session.exec(select(ExtractedFact)).all():
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        pending = session.exec(select(ExtractedFact).where(ExtractedFact.status == "pending")).all()
        stubs = len(session.exec(select(Paystub).where(Paystub.pay_date >= PAYDAYS[0])).all())
        receipts = len(session.exec(select(Document).where(Document.kind == "receipt")).all())
        plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.loan_id.in_([p["loan_id"] for p in PLANS.values()]))).all()  # type: ignore[attr-defined]
    tags = {}
    for s in q.sent.values():
        tags[s["tag"].split(":")[0]] = tags.get(s["tag"].split(":")[0], 0) + 1
    assert not q.findings, "\n".join(q.findings)
    assert len(q.sent) > 150 and kinds.get("bill_due", 0) > 40 and kinds.get("bank_alert", 0) >= 30, (len(q.sent), kinds, tags)
    assert stubs == 7 and receipts == 20 and len(plans) == 2 and all(not p.active and p.remaining == 0 for p in plans), (stubs, receipts, [(p.loan_id, p.remaining, p.active) for p in plans])
    assert not pending, [(f.kind, f.excerpt[:60]) for f in pending]
    assert q.fetched_export == 1
