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
    "irs": ("irs.gov",), "social security": ("ssa.gov",), "schwab": ("schwab.com",), "vanguard": ("vanguard.com",), "fidelity": ("fidelity.com",),
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


def brand_of(name: str) -> str | None:
    """The common sender a display name claims to be, if any: "Capital One" in "Capital One Alerts"."""
    low = f" {re.sub(r'[^a-z0-9&]+', ' ', (name or '').lower())} "
    for brand in sorted(KNOWN, key=len, reverse=True):
        if f" {brand} " in low:
            return brand
    return None


def _results(auth_results: str) -> dict[str, str]:
    """dmarc, dkim and spf from the receiving server's Authentication-Results (the first header is its own)."""
    out: dict[str, str] = {}
    for method in ("dmarc", "dkim", "spf"):
        m = re.search(rf"\b{method}=(\w+)", auth_results or "", re.I)
        if m:
            out[method] = m.group(1).lower()
    return out


def _owns(org: str, own: str) -> bool:
    """A domain the sender owns besides its main one: the same name with more to it (vanguardretirement.com for
    vanguard.com) or the brand's own ending (notifications.health.amazon for amazon.com)."""
    own_label = own.split(".")[0]
    label, tld = org.split(".")[0], org.rsplit(".", 1)[-1]
    return org == own or tld == own_label or (len(own_label) >= 4 and label.startswith(own_label) and not any(b in label for b in BAIT))


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


def judge(sender: str, sender_name: str, auth_results: str = "", reply_to: str = "", learned: dict[str, set[str]] | None = None) -> tuple[str, str]:
    """The verdict and why, in plain words."""
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
    if brand:
        own = set(KNOWN[brand]) | (learned or {}).get(brand, set())
        if not any(_owns(org, d) for d in own):
            return "crit", f"It calls itself {sender_name.strip()}, but it comes from {org}, not {', '.join(sorted(own)[:2])}."
    if res.get("dmarc") == "pass" or res.get("dkim") == "pass":
        if reply_to and "@" in reply_to and org_domain(reply_to) != org and brand:
            return "warn", f"It is from {org}, but replies would go to {org_domain(reply_to)}."
        return "ok", f"Confirmed from {org} by the mail server."
    if res:
        return "warn", f"The mail server could not fully confirm it came from {org}."
    return "", ""


def auth_header(msg) -> tuple[str, str]:  # noqa: ANN001 - an email.message.Message
    """The receiving server's Authentication-Results (the first, topmost header, which is its own) and the
    Reply-To address."""
    results = msg.get_all("Authentication-Results") or msg.get_all("ARC-Authentication-Results") or []
    reply = getaddresses([msg.get("Reply-To", "") or ""])
    return (str(results[0])[:1000] if results else ""), (reply[0][1].lower()[:320] if reply and reply[0][1] else "")
