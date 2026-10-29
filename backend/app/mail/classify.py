"""Classification and extraction, rules first. A model may run second (M4.6); every fact still waits for the owner.

Kinds: bill_due, statement, paystub, installment, advance, price_change, payday, payment_confirmation, past_due,
bank_alert, receipt, export, none. Rules match on the sender and the subject; extraction reads amounts, dates and account
tails from the text. The text is data: nothing here executes or follows anything it says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")
DATE_WORDS = re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b", re.IGNORECASE)
DATE_WORDS_NOYEAR = re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?)\b", re.IGNORECASE)
DATE_NUM = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b")
LAST4 = re.compile(r"(?:ending in|ending|last four|xx|\*\*|\.\.\.)\s*(\d{4})\b", re.IGNORECASE)  # linear: no repeated run before the digits


@dataclass
class Rule:
    name: str
    kind: str
    sender: str | None = None
    subject: str | None = None
    confidence: float = 0.9
    unless: str | None = None  # a subject that rules this rule out


RULES: list[Rule] = [
    # Rocket Money's own domain only: "rocketmoney-support@elsewhere" is not Rocket Money.
    Rule("export", "export", sender=r"@(?:[\w-]+\.)*(?:rocketmoney|truebill)\.com$", subject=r"export (is )?(complete|ready)"),
    Rule("rm-upcoming", "upcoming_bills", sender=r"@(?:[\w-]+\.)*(?:rocketmoney|truebill)\.com$", subject=r"upcoming bills"),
    Rule("rm-income", "payday", sender=r"@(?:[\w-]+\.)*(?:rocketmoney|truebill)\.com$", subject=r"received money"),
    Rule("rm-balance", "bank_alert", sender=r"@(?:[\w-]+\.)*(?:rocketmoney|truebill)\.com$", subject=r"balance alert", confidence=0.8),
    Rule("overdraft", "bank_alert", subject=r"overdrawn|overdraft|negative balance|insufficient funds"),
    Rule("declined", "bank_alert", subject=r"\bdeclined\b"),
    Rule("deposit", "bank_alert", subject=r"deposit (was )?(made|received|posted)|direct deposit|you (just )?received money"),
    Rule("purchase", "bank_alert", subject=r"(a )?(purchase|transaction|charge) (was )?(made|posted|alert)|card was used", confidence=0.7),
    Rule("past-due", "past_due", subject=r"past due|overdue|late (payment|notice)|missed payment|final notice"),
    Rule("payment-received", "payment_confirmation", subject=r"(we('ve| have)? )?received your payment|payment (confirmation|received|successful|processed|posted)|thank you for your payment|payment confirmed"),
    Rule("autopay-scheduled", "payment_confirmation", subject=r"autopay (is )?(set|scheduled)|payment (is )?scheduled", confidence=0.8),
    Rule("paystub", "paystub", subject=r"pay ?stub|pay statement|earnings statement|payslip|pay advice|your pay(check)? (is|has)"),
    Rule("installment", "installment", subject=r"installment|pay in 4|loan confirmation|payment plan at .+ is approved|your (loan|plan) (is )?(approved|confirmed)|enabled auto ?pay|turned off auto ?pay|auto ?pay (is )?(on|off)\b|purchase (is )?complete", sender=r"affirm|klarna|afterpay|sezzle|paypal|zip", confidence=0.8),
    Rule("advance", "advance", sender=r"earnin|dave|brigit|moneylion|chime|cleo|empower", subject=r"advance|repay|cash out|your (advance|cash)", confidence=0.8),
    Rule("price-change", "price_change", subject=r"price (change|increase|update)|new price|rate (change|increase)|updating (our|your) (price|rate)s?"),
    Rule("investment-statement", "investment_balance", sender=r"fidelity|netbenefits|vanguard|schwab|empower|principal|voya|tiaa|troweprice|t\.? ?rowe|johnhancock|transamerica|merrill|etrade|morganstanley|robinhood|betterment|wealthfront|coinbase|tsp\.gov|adp", subject=r"statement|balance|account (value|summary)|quarterly|your (401|retirement|ira|brokerage|portfolio)", confidence=0.8),
    Rule("bill-due", "bill_due", subject=r"(bill|invoice|statement) (is )?(ready|available|due)|amount due|payment (is )?due|due (on|tomorrow|soon|today)|your (monthly )?(bill|invoice)"),
    Rule("statement", "statement", subject=r"statement (is )?(ready|available|here)|e-?statement|monthly statement|account statement|your .* statement"),
    # A store's receipt; the shipping and delivery notes about the same order are not.
    Rule("receipt", "receipt", subject=r"\breceipt\b|order (confirmation|confirmed|receipt)|your (\S+ )?order (#|no\.?|number|from)|thanks? (you )?for (your )?(order|purchase|shopping)|purchase confirmation|order #\s*\w+",
         unless=r"shipped|delivered|out for delivery|on (its|the) way|tracking|refund|cancel|return (started|label)|review", confidence=0.8),
]


def classify(sender: str, subject: str, text: str = "") -> tuple[str, float, str]:
    s, j = (sender or "").lower(), (subject or "").lower()
    for rule in RULES:
        if rule.sender and not re.search(rule.sender, s):
            continue
        if rule.subject and not re.search(rule.subject, j):
            continue
        if rule.unless and re.search(rule.unless, j):
            continue
        return rule.kind, rule.confidence, f"rule:{rule.name}"
    body = (text or "")[:4000].lower()
    if re.search(r"amount due|total due|minimum (payment )?due|payment due", body) and re.search(r"\bdue\b", body):
        return "bill_due", 0.6, "rule:body-due"
    return "none", 0.0, ""


def parse_date_text(value: str, ref: date | None = None) -> date | None:
    v = re.sub(r"(\d)(st|nd|rd|th)", r"\1", value.strip().replace(".", ""))
    for fmt in ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    if ref is not None:
        for fmt in ("%b %d", "%B %d"):
            try:
                d = datetime.strptime(v, fmt).date().replace(year=ref.year)
                if (d - ref).days < -200:
                    d = d.replace(year=ref.year + 1)
                return d
            except ValueError:
                pass
    return None


def _amount_near(text: str, keywords: tuple[str, ...]) -> float | None:
    low = text.lower()
    for key in keywords:
        for m in re.finditer(key, low):
            window = text[m.start(): m.start() + 160]
            a = AMOUNT.search(window)
            if a:
                return float(a.group(1).replace(",", ""))
    return None


def _date_near(text: str, keywords: tuple[str, ...], ref: date | None) -> date | None:
    low = text.lower()
    for key in keywords:
        for m in re.finditer(key, low):
            window = text[m.start(): m.start() + 120]
            for pattern in (DATE_WORDS, DATE_NUM, DATE_WORDS_NOYEAR):
                d = pattern.search(window)
                if d:
                    parsed = parse_date_text(d.group(1), ref)
                    if parsed:
                        return parsed
    return None


def extract(kind: str, subject: str, text: str, ref: date | None = None) -> dict:
    """Fields for a kind, from the subject and the text. Missing fields are simply absent."""
    blob = f"{subject}\n{text}"
    fields: dict = {}
    if kind in ("bill_due", "past_due", "statement", "installment", "price_change", "advance"):
        amount = _amount_near(blob, ("amount due", "total due", "payment due", "minimum (payment )?due", "balance due", "new balance", "total amount", "amount:", "total:", "due:"))
        if kind == "price_change":
            # "from $17.00 to $19.00": the new price is the one after "to", "now", "will be" or "new price", never the first amount seen.
            new_price = re.search(r"(?:\bto|\bnow|will be|new (?:monthly |annual )?(?:price|rate)(?: of| is|:)?)\s*(?:of\s*)?\$\s?([\d,]+(?:\.\d{2})?)", blob, re.IGNORECASE)
            if new_price:
                amount = float(new_price.group(1).replace(",", ""))
        if amount is None:
            a = AMOUNT.search(blob)
            amount = float(a.group(1).replace(",", "")) if a else None
        if amount is not None:
            fields["amount"] = round(amount, 2)
        due = _date_near(blob, ("due date", "due on", "due by", "is due", "payment due", "due:", "due "), ref)
        if due:
            fields["due_date"] = due.isoformat()
    if kind in ("payment_confirmation",):
        amount = _amount_near(blob, ("payment of", "amount", "paid", "received", "total")) or (float(AMOUNT.search(blob).group(1).replace(",", "")) if AMOUNT.search(blob) else None)
        if amount is not None:
            fields["amount"] = round(amount, 2)
        paid = _date_near(blob, ("on ", "date", "scheduled for", "posted"), ref)
        if paid:
            fields["paid_date"] = paid.isoformat()
    if kind == "investment_balance":
        balance = _amount_near(blob, ("ending balance", "account value", "total value", "current balance", "balance as of", "total balance", "your balance", "balance:", "value:", "market value", "vested balance"))
        if balance is None:
            a = AMOUNT.search(blob)
            balance = float(a.group(1).replace(",", "")) if a else None
        if balance is not None:
            fields["balance"] = round(balance, 2)
        as_of = _date_near(blob, ("as of", "ending", "statement date", "through", "period ending"), ref)
        if as_of:
            fields["as_of"] = as_of.isoformat()
        low = blob.lower()
        for key, word in (("401k", "401(k)"), ("401(k)", "401(k)"), ("403b", "403(b)"), ("roth", "Roth IRA"), ("ira", "IRA"), ("hsa", "HSA"), ("529", "529 plan"), ("brokerage", "brokerage"), ("pension", "pension")):
            if key in low:
                fields["what"] = word
                break
    if kind == "bank_alert":
        low = blob.lower()
        fields["alert"] = "overdraft" if re.search(r"overdrawn|overdraft|negative balance", low) else "declined" if "declined" in low else "deposit" if re.search(r"deposit|received money", low) else "purchase" if re.search(r"purchase|transaction|charge", low) else "balance"
        a = AMOUNT.search(blob)
        if a:
            fields["amount"] = round(float(a.group(1).replace(",", "")), 2)
        if fields["alert"] in ("purchase", "declined"):
            at = re.search(r"\bat\s+([A-Za-z0-9][A-Za-z0-9&'*#./ -]{1,40}?)(?=\s+(?:on|for|was|using|with)\b|[.,;!\n]|$)", blob)
            if at:
                fields["merchant"] = re.sub(r"\s+", " ", at.group(1)).strip()[:60]
        net = _amount_near(blob, ("net pay", "net amount", "take home", "net:"))
        gross = _amount_near(blob, ("gross pay", "gross earnings", "gross:"))
        if net is not None:
            fields["net"] = round(net, 2)
        if gross is not None:
            fields["gross"] = round(gross, 2)
        pay_date = _date_near(blob, ("pay date", "paid on", "check date"), ref)
        if pay_date:
            fields["pay_date"] = pay_date.isoformat()
    if kind == "receipt":
        total = _amount_near(blob, ("order total", "grand total", "total charged", "amount charged", "amount paid", "total paid", "you paid", "payment total", "total:", "total"))
        if total is not None:
            fields["total"] = round(total, 2)
        order = re.search(r"order\s*(?:#|no\.?|number)[:\s]*([A-Z0-9][A-Z0-9-]{3,29})", blob, re.IGNORECASE)
        if order:
            fields["order_no"] = order.group(1)
        back = _date_near(blob, ("return by", "returns? (are )?accepted (until|through)", "return window (closes|ends)", "eligible for return (until|through)", "return it by"), ref)
        if back:
            fields["return_by"] = back.isoformat()
        else:
            days = re.search(r"(\d{1,3})[- ]day returns?|returns? within (\d{1,3}) days|(\d{1,3}) days to return", blob, re.IGNORECASE)
            if days:
                fields["return_days"] = int(next(g for g in days.groups() if g))
        until = _date_near(blob, ("warranty (expires|ends|until|through|valid through)", "covered (until|through)"), ref)
        if until:
            fields["warranty_until"] = until.isoformat()
        else:
            span = re.search(r"(\d{1,2})[- ](year|month)s?(?: limited)? warranty|warranty(?: of| for)? (\d{1,2}) (year|month)s?", blob, re.IGNORECASE)
            if span:
                n = int(span.group(1) or span.group(3))
                unit = (span.group(2) or span.group(4)).lower()
                fields["warranty_months"] = n * 12 if unit == "year" else n
    tail = LAST4.search(blob)
    if tail:
        fields["last4"] = tail.group(1)
    period = re.search(r"(?:service|billing|statement) period:?\s*([A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})\s*(?:to|-|–|through)\s*([A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})", blob, re.IGNORECASE)
    if period:
        a, b = parse_date_text(period.group(1), ref), parse_date_text(period.group(2), ref)
        if a and b:
            fields["period_start"], fields["period_end"] = a.isoformat(), b.isoformat()
    return fields


def excerpt_for(text: str, fields: dict, limit: int = 240) -> str:
    """The shortest stretch of the text that holds the amount and the date, for the confirm card."""
    body = re.sub(r"\s+", " ", text or "").strip()
    anchors = [str(fields.get("amount", "")), fields.get("due_date", ""), fields.get("net", ""), str(fields.get("total", ""))]
    for anchor in anchors:
        if not anchor:
            continue
        needle = f"{float(anchor):,.2f}" if isinstance(anchor, (int, float)) or re.match(r"^\d+(\.\d+)?$", str(anchor)) else str(anchor)
        idx = body.find(needle)
        if idx >= 0:
            start = max(0, idx - limit // 2)
            if start > 0 and body[start - 1] != " ":
                start = body.find(" ", start) + 1  # start on a word, not inside one
            return body[start: start + limit].strip()
    return body[:limit]
