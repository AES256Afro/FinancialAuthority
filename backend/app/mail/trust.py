"""Is this email really from who it says (M52)? Three checks, all on what the message already carries, nothing sent
anywhere:

1. The receiving mail server's own verdict, written into the message as Authentication-Results: whether the
   sender's domain vouched for it (DMARC), signed it (DKIM) and sent it from its own servers (SPF).
2. The name against the address: a message that calls itself "Capital One" comes from Capital One's own domain,
   known from a table of common senders or learned from that name's earlier mail that passed check 1.
3. Look-alike domains: "capital0ne.com", "paypa1-secure.com", a brand inside someone else's domain.

The verdict is "ok", "warn" (it could not be shown to be genuine), "crit" (it looks forged), or "" when there was
nothing to judge. A fact from a "crit" message is never confirmed together with others, and its card says why."""

from __future__ import annotations

import re
from email.utils import getaddresses

# Common senders of money mail and their own domains. A name not here is judged by what it used before.
KNOWN: dict[str, tuple[str, ...]] = {
    "affirm": ("affirm.com",), "klarna": ("klarna.com",), "afterpay": ("afterpay.com",), "sezzle": ("sezzle.com",), "zip": ("zip.co", "quadpay.com"),
    "paypal": ("paypal.com",), "venmo": ("venmo.com",), "cash app": ("square.com", "cash.app"), "earnin": ("earnin.com",), "dave": ("dave.com",),
    "brigit": ("brigit.com", "hellobrigit.com"), "moneylion": ("moneylion.com",), "chime": ("chime.com",), "cleo": ("meetcleo.com",),
    "capital one": ("capitalone.com",), "chase": ("chase.com", "jpmorgan.com"), "bank of america": ("bankofamerica.com", "bofa.com"),
    "wells fargo": ("wellsfargo.com",), "citi": ("citi.com", "citibank.com", "citicards.com"), "discover": ("discover.com",),
    "american express": ("americanexpress.com", "aexp.com"), "amex": ("americanexpress.com", "aexp.com"), "us bank": ("usbank.com",),
    "apple": ("apple.com",), "amazon": ("amazon.com",), "xfinity": ("xfinity.com", "comcast.net", "comcast.com"), "comcast": ("comcast.net", "comcast.com", "xfinity.com"),
    "verizon": ("verizon.com", "verizonwireless.com"), "at&t": ("att.com", "att.net"), "t-mobile": ("t-mobile.com", "tmobile.com"),
    "state farm": ("statefarm.com", "statefarmservice.com"), "geico": ("geico.com",), "progressive": ("progressive.com",),
    "rocket money": ("rocketmoney.com", "truebill.com"), "experian": ("experian.com",), "equifax": ("equifax.com",), "transunion": ("transunion.com",),
    "irs": ("irs.gov",), "social security": ("ssa.gov",), "schwab": ("schwab.com",), "vanguard": ("vanguard.com", "vanguardretirement.com"), "fidelity": ("fidelity.com",),
}
LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s"})
# Words a forger adds to a brand to make a domain look official ("capitalone-alerts", "paypal-secure").
BAIT = ("secure", "verify", "verification", "login", "signin", "alert", "alerts", "account", "accounts", "support", "update", "billing", "service", "services",
        "notice", "confirm", "refund", "claim", "unlock", "help", "wallet", "pay")


def org_domain(address_or_domain: str) -> str:
    """The domain a sender's organisation owns: the last two labels of the address's domain (notify.capitalone.com
    is capitalone.com)."""
    domain = address_or_domain.rsplit("@", 1)[-1].strip().lower().strip(">").rstrip(".")
    labels = [x for x in domain.split(".") if x]
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "gov") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# Letters from other alphabets that draw like a Latin one, as a forger writes "Capitаl One" with a Cyrillic а so the
# name reads the same to the owner and nothing to the brand check. Folded to the letter they look like.
CONFUSABLE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d",
    "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "h", "О": "o", "Р": "p", "С": "c", "Т": "t", "Х": "x", "У": "y",
    "α": "a", "ο": "o", "ρ": "p", "ι": "i", "ν": "v", "Α": "a", "Β": "b", "Ε": "e", "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m",
    "Ν": "n", "Ο": "o", "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x", "З": "3", "І": "i",
})
# Characters that take no space on the screen: a zero-width space inside a word hides it from a word match.
INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿­͏᠎]")


def fold(name: str) -> str:
    """A display name as the owner reads it: full-width and other compatibility forms to plain letters (NFKC),
    invisible characters out, look-alike letters to the Latin ones they draw like, lower case."""
    import unicodedata

    plain = unicodedata.normalize("NFKC", INVISIBLE.sub("", name or ""))
    return plain.translate(CONFUSABLE).lower()


def _forms(brand: str) -> tuple[str, ...]:
    """A brand as a display name writes it: its words ("t mobile", once the hyphen is a space, as the name's own is),
    and run together ("tmobile", "capitalone", "att"). A forger who signs a domain of their own writes "CapitalOne"."""
    spaced = re.sub(r"[^a-z0-9&]+", " ", brand).strip()
    return tuple(dict.fromkeys((spaced, spaced.replace(" ", ""), spaced.replace(" ", "").replace("&", ""))))


_BRANDS = [(brand, _forms(brand)) for brand in sorted(KNOWN, key=len, reverse=True)]


def brand_of(name: str) -> str | None:
    """The common sender a display name claims to be, if any: "Capital One" in "Capital One Alerts", "CapitalOne",
    "T-Mobile", or "Capita1 One" with a digit for a letter."""
    plain = fold(name)
    for text in dict.fromkeys((plain, plain.translate(LEET))):
        low = f" {re.sub(r'[^a-z0-9&]+', ' ', text)} "
        for brand, forms in _BRANDS:
            if any(f" {form} " in low for form in forms):
                return brand
    return None


def _results(auth_results: str) -> dict[str, str]:
    """dmarc, dkim and spf from the receiving server's Authentication-Results, each the first result it states for that
    method. Only a clause that begins with the method counts: the words anywhere in the line let an envelope address of
    "dmarc=pass@attacker.test", which servers quote in a comment and in smtp.mailfrom, stand as the server's verdict
    (sweep 24)."""
    out: dict[str, str] = {}
    for method in ("dmarc", "dkim", "spf"):
        for clause in _clauses(auth_results, method):
            found = re.match(rf"\s*{method}\s*=\s*(\w+)", clause, re.I)
            if found:
                out[method] = found.group(1).lower()
                break
    return out


def _clauses(auth_results: str, method: str) -> list[str]:
    """Each result of one method in the receiving server's Authentication-Results, as written. Comments go first (a
    comment may hold a semicolon, "(2048-bit key; unprotected)", or a sender's own words, and comments nest), and a
    semicolon inside a quoted address ends nothing."""
    plain, before = auth_results or "", None
    for _ in range(8):
        if plain == before:
            break
        plain, before = re.sub(r"\([^()]{0,300}\)", " ", plain), plain
    plain = re.sub(r'"[^"]{0,300}"', lambda m: m.group(0).replace(";", " "), plain)
    return [c.strip() for c in plain.split(";") if re.match(rf"\s*{method}\s*=", c, re.I)]


def _about(clause: str, *properties: str) -> str:
    """The organisation a result speaks for (header.from, header.d, header.i=@...), or "" when it does not say."""
    found = re.search(rf"\b(?:{'|'.join(re.escape(p) for p in properties)})\s*=\s*\"?([^\s;\"]+)", clause, re.I)
    return org_domain(found.group(1)) if found else ""


def _dkim_vouches(auth_results: str, org: str) -> bool:
    """A DKIM signature that passed and speaks for the sender's own organisation. Anyone can sign a message with a
    domain of their own and write someone else's address in From: a pass under another domain says nothing about the
    From (sweep 24). A result that names no domain is taken as it stands; it is the receiving server's own line."""
    for clause in _clauses(auth_results, "dkim"):
        if re.match(r"\s*dkim\s*=\s*pass\b", clause, re.I):
            about = _about(clause, "header.d", "header.i")
            if not about or about == org:
                return True
    return False


def _owns(org: str, own: str) -> bool:
    """A domain the sender may own besides its main one: the same name with more to it (capitalonebank.com for
    capitalone.com) or the brand's own ending (notifications.health.amazon for amazon.com). Only the brand's ending
    is proof (nobody else registers under .amazon); the longer name is anyone's to register, so `_vouched` keeps it
    to a warning."""
    own_label = own.split(".")[0]
    label, tld = org.split(".")[0], org.rsplit(".", 1)[-1]
    return org == own or tld == own_label or (len(own_label) >= 4 and label.startswith(own_label) and not any(b in label for b in BAIT))


def _vouched(org: str, own: str) -> bool:
    """The brand's own domain for certain: listed, learned from its earlier verified mail, marked genuine by the owner,
    or under the brand's own ending. A domain that only starts with the name (capitalonebank.test) is not: a forger
    registers one, signs it and passes DMARC (sweep 23)."""
    return org == own or org.rsplit(".", 1)[-1] == own.split(".")[0]


def lookalike(domain: str) -> str | None:
    """A domain dressed as a known sender's: a digit for a letter (capital0ne.com), or the brand with a bait word
    (paypal-secure.com, capitalone-alerts.net). A different company that shares a word (square-enix.com) is not."""
    org = org_domain(domain)
    label = org.split(".")[0]
    plain = label.translate(LEET).replace("-", "")
    for domains in KNOWN.values():
        if any(_owns(org, own) for own in domains):
            return None
    for domains in KNOWN.values():
        for own in domains:
            own_label = own.split(".")[0]
            if len(own_label) < 4:
                continue
            if plain == own_label and label != own_label:
                return own  # capital0ne.com
            if own_label in plain and any(b in plain.replace(own_label, " ", 1) for b in BAIT):
                return own  # paypal-secure.com, capitalone-alerts.net
    return None


def judge(sender: str, sender_name: str, auth_results: str = "", reply_to: str = "", learned: dict[str, set[str]] | None = None, name_domains: set[str] | None = None) -> tuple[str, str]:
    """The verdict and why, in plain words. `name_domains` are the domains earlier verified mail under this same display
    name came from, for a name the table of common senders does not hold (the owner's own lender, say)."""
    sender = (sender or "").lower()
    if "@" not in sender:
        return "", ""
    org = org_domain(sender)
    res = _results(auth_results)
    brand = brand_of(sender_name)
    looks = lookalike(sender)
    if looks:
        return "crit", f"It comes from {org}, a look-alike of {looks}."
    if res.get("dmarc") == "fail" or (res.get("dkim") == "fail" and res.get("spf") == "fail"):
        return "crit", f"The mail server could not confirm it came from {org}: its domain did not vouch for it."
    verified_as = next((about for clause in _clauses(auth_results, "dmarc") if (about := _about(clause, "header.from"))), "")
    if res.get("dmarc") == "pass" and verified_as and verified_as != org:
        # The server checked one From and this reader took another (a message with two): the pass is about someone else.
        return "crit", f"The mail server's check was for {verified_as}, but the message says it comes from {org}."
    if brand:
        own = set(KNOWN[brand]) | (learned or {}).get(brand, set())
        if not any(_owns(org, d) for d in own):
            return "crit", f"It calls itself {sender_name.strip()}, but it comes from {org}, not {', '.join(sorted(own)[:2])}."
        if not any(_vouched(org, d) for d in own) and res.get("dmarc") != "fail":
            # A name that starts like the brand's is anyone's to register and sign: never "confirmed" (and so never
            # learned as the brand's), until the owner marks it genuine.
            return "warn", f"It calls itself {sender_name.strip()} and comes from {org}, which is not one of its known domains ({', '.join(sorted(own)[:2])}). Mark it genuine if it is."
    if not brand and name_domains and org not in name_domains:
        # A name the table does not hold is judged by what it used before: a lender's own name on a domain the attacker
        # registered and signed was "confirmed", and only the table's brands were looked at (sweep 24).
        return "warn", f"It calls itself {sender_name.strip()} and comes from {org}, but earlier mail under that name came from {', '.join(sorted(name_domains)[:2])}."
    if res.get("dmarc") == "pass" or _dkim_vouches(auth_results, org):
        if reply_to and "@" in reply_to and org_domain(reply_to) != org and not (brand and any(_owns(org_domain(reply_to), d) for d in set(KNOWN[brand]) | (learned or {}).get(brand, set()))):
            return "warn", f"It is from {org}, but replies would go to {org_domain(reply_to)}."
        return "ok", f"Confirmed from {org} by the mail server."
    if res:
        return "warn", f"The mail server could not fully confirm it came from {org}."
    return "", ""


MAX_RESULT_HEADERS = 8  # a server writes one header, or one per method (dkim, spf, arc, dmarc)


def auth_header(msg) -> tuple[str, str]:  # noqa: ANN001 - an email.message.Message
    """The receiving server's Authentication-Results and the Reply-To address. The server's own lines are the topmost
    ones, and the first is its own for certain. Some servers (Proton among them) write one header per method, so that
    first may say only "dkim=pass" for a signature of the attacker's, with the "dmarc=fail" on the next line: the lines
    below it that name the same server are read for what goes against the message, and never for a pass. Anyone can
    write "Authentication-Results: <that server's name>; dmarc=pass" among their own headers, right under the server's,
    and the first word on a method only helps when the server stated that method (sweep 24). A lower line naming anyone
    else is the sender's own words; the headers are read raw, so a thousand of them cost nothing."""
    first = ""
    against: list[str] = []
    server = ""
    seen = 0
    for name, value in msg.raw_items():
        if name.lower() != "authentication-results":
            continue
        line = " ".join(str(value).split())[:1000]
        who = line.split(";", 1)[0].strip().lower()
        if seen and who != server:
            break
        seen += 1
        if seen == 1:
            first, server = line, who
        else:
            against += [c for method in ("dmarc", "dkim", "spf") for c in _clauses(line, method) if not re.match(rf"{method}\s*=\s*pass\b", c, re.I)]
        if seen >= MAX_RESULT_HEADERS:
            break
    results = "; ".join([first, *against]) if first else str(msg.get("ARC-Authentication-Results") or "")
    reply = getaddresses([msg.get("Reply-To", "") or ""])
    return results[:4000], (reply[0][1].lower()[:320] if reply and reply[0][1] else "")
