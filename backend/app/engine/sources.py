"""Sources that tell you, once (M61): which mailboxes and bank links need the owner, each as one card with the exact
step; which came back; and what to tell the phone about it. The cards are worked out from the source's own row and its
health record (connectors/health.py), so there is one per source however many times it failed, and a card leaves by
itself the moment a read succeeds. A short network failure is no card at all: it mends itself, and it becomes one only
if it lasts past DOWN_AFTER. What is sent to the phone or put in the brief is the source's name and fixed words, never
what a server said."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from ..clock import zone
from ..connectors import health
from ..connectors.health import NETWORK, OTHER, SIGNIN
from ..models import BankLink, MailAccount
from ..store import get_setting, set_setting

DOWN_AFTER = timedelta(hours=1)  # how long a network failure may last before it asks for anything
BACK_HOURS = 24  # "is back" stays in the brief this long, so the next morning's brief says it exactly once
TELL_EVERY = timedelta(hours=24)  # the phone hears of a source at most this often, however it flaps
OPEN = "sources.open"  # the outages being told about, and when each was last told
BACK = "sources.back"  # the outages that ended, for the brief
MAX_PUSHES = 3  # more than this at once goes as one message


def bridge_state(host: str, failure: str, said: str) -> str | None:
    """The Bridge's state from the IMAP answer alone, for a mailbox read through it. `said` is what the server or the
    connection answered, empty when the read went through."""
    if host != "bridge":
        return None
    if not said:
        return "signed_in"
    if failure == NETWORK:
        return "not_running"
    if failure == SIGNIN:
        return "no_account" if "no such user" in said.lower() or "no account" in said.lower() else "password_refused"
    return "signed_in"  # it let the app in; something past the sign-in failed


def _since(state: dict[str, Any], last: datetime | None) -> str | None:
    at = health.moment(state.get("since")) or health.moment(last.isoformat() if last else None)
    return at.isoformat() if at else None


def _card(key: str, kind: str, id_: int, label: str, failure: str, card_id: str, text: str, step: str, action: str, link: str, detail: str, state: dict[str, Any], last: datetime | None, now: datetime) -> dict[str, Any]:
    until = health.moment(state.get("until"))
    return {
        "key": key, "kind": kind, "id": id_, "label": label, "failure": failure, "severity": "critical" if failure == SIGNIN else "warning", "card_id": card_id,
        "text": text, "step": step, "action": action, "link": link, "detail": detail, "since": _since(state, last), "retry_at": until.isoformat() if until and until > now else None,
        "retry": f"/api/v1/mail/accounts/{id_}/sync" if kind == "mail" else f"/api/v1/banks/{id_}/sync", "source": f"mailbox:{id_}" if kind == "mail" else f"link:{id_}",
    }


def _quiet(failure: str, state: dict[str, Any], now: datetime) -> bool:
    """A network failure that has not lasted yet: on the source's row and the self-check, and no card."""
    since = health.moment(state.get("since"))
    return failure == NETWORK and since is not None and now - since < DOWN_AFTER


def _failure_of(state: dict[str, Any]) -> str:
    """A record from before M61 holds only the count, and only a refused sign-in made one; a row with an error and no
    record at all is shown as it stands."""
    return str(state.get("failure") or (SIGNIN if state.get("fails") else OTHER))


def mail_card(account: MailAccount, state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    error = account.last_error or ""
    if not error:
        return None
    failure = _failure_of(state)
    if _quiet(failure, state, now):
        return None
    label = account.label or "A mailbox"
    bridge = state.get("bridge") or ("not_running" if account.host == "bridge" and error.startswith("The Bridge is not") else None)
    common = (account.id, label, failure)
    if bridge in ("not_running", "no_account"):
        words = "has no account signed in" if bridge == "no_account" else "is not running"
        return _card(f"mail:{account.id}", "mail", *common, f"bridge-signin-{account.id}", f"{label} waits on the Bridge, which {words}.",
                     "Sign in to the Bridge once on the server, with your Proton password and code, then paste the password it prints. The commands are under Mailboxes on Connectors.",
                     "Connectors", "/settings/connectors#mail", error, state, account.last_sync_at, now)
    if failure == SIGNIN:
        if bridge == "password_refused":
            step = "Change the password on Connectors to the one the Bridge printed with info. It is not your Proton account password."
        elif account.kind == "gmail":
            step = "Make a new app password in your Google account, then change the password for this mailbox on Connectors."
        else:
            step = "Check the username, then change the password for this mailbox on Connectors. Where the server gives app passwords, make a new one."
        return _card(f"mail:{account.id}", "mail", *common, f"mail-error-{account.id}", f"{label} refused the sign-in.", step, "Fix the mailbox", "/settings/connectors#mail", error, state, account.last_sync_at, now)
    if failure == NETWORK:
        return _card(f"mail:{account.id}", "mail", *common, f"mail-error-{account.id}", f"{label} could not be reached for over an hour.",
                     "It keeps trying by itself. If this stays, check that the mail server's host and port on Connectors are right and that the server can reach it.", "Fix the mailbox", "/settings/connectors#mail", error, state, account.last_sync_at, now)
    return _card(f"mail:{account.id}", "mail", *common, f"mail-error-{account.id}", f"{label} could not be read.", "The line beside it on Connectors says what went wrong; change that, then press Sync now.", "Fix the mailbox", "/settings/connectors#mail", error, state, account.last_sync_at, now)


def link_card(link: BankLink, state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    label = link.institution or "A bank"
    if link.needs_login:
        return _card(f"bank:{link.id}", "bank", link.id, label, SIGNIN, f"link-signin-{link.id}", f"{label} wants a new sign-in before it can be read again.",
                     "Press Sign in again on Connectors; the bank's own page opens. The app never sees the password.", "Sign in again", f"/settings/connectors?signin={link.id}", link.last_error or "", state, link.last_sync_at, now)
    error = link.last_error or ""
    if not error:
        return None
    failure = _failure_of(state)
    if _quiet(failure, state, now):
        return None
    if failure == NETWORK:
        return _card(f"bank:{link.id}", "bank", link.id, label, failure, f"link-error-{link.id}", f"{label} could not be reached for over an hour.", "It keeps trying by itself. Press Sync now to try at once.",
                     "Sync now", f"/settings/connectors?sync={link.id}", error, state, link.last_sync_at, now)
    return _card(f"bank:{link.id}", "bank", link.id, label, failure, f"link-error-{link.id}", f"{label} did not sync.", "Press Sync now on Connectors. If it fails again, sign in again there.",
                 "Sync now", f"/settings/connectors?sync={link.id}", error, state, link.last_sync_at, now)


def down(session: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """One card per source that needs the owner now, most urgent first."""
    now = now or health.now_utc()
    cards: list[dict[str, Any]] = []
    for account in session.exec(select(MailAccount).where(MailAccount.active == True)).all():  # noqa: E712
        cards.append(mail_card(account, health.read(session, health.mail_key(account.id)), now))
    for link in session.exec(select(BankLink).where(BankLink.active == True)).all():  # noqa: E712
        cards.append(link_card(link, health.read(session, health.bank_key(link.id)), now))
    return sorted((c for c in cards if c), key=lambda c: (c["severity"] != "critical", c["kind"], c["label"]))


def active_keys(session: Session) -> set[str]:
    return {f"mail:{i}" for i in session.exec(select(MailAccount.id).where(MailAccount.active == True)).all()} | {f"bank:{i}" for i in session.exec(select(BankLink.id).where(BankLink.active == True)).all()}  # noqa: E712


def back(session: Session, now: datetime | None = None, cards: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The outages that ended in the last BACK_HOURS and have not started again, each once."""
    now = now or health.now_utc()
    still = {c["key"] for c in (down(session, now) if cards is None else cards)}
    active = active_keys(session)
    fresh = [b for b in (get_setting(session, BACK, []) or []) if (at := health.moment(b.get("at"))) is not None and now - at < timedelta(hours=BACK_HOURS)]
    return [b for b in fresh if b.get("key") not in still and b.get("key") in active]


def _span(since: str | None, at: datetime) -> str:
    start = health.moment(since)
    if start is None:
        return ""
    hours = (at - start).total_seconds() / 3600
    if hours < 1:
        return " after under an hour"
    if hours < 48:
        return f" after {round(hours)} hour{'s' if round(hours) != 1 else ''}"
    return f" after {round(hours / 24)} days"


def snapshot_part(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """For the plan and the brief: the cards without what a server said, and the day each began in the owner's time."""
    now = now or health.now_utc()
    cards = down(session, now)
    out = []
    for c in cards:
        start = health.moment(c["since"])
        out.append({**{k: v for k, v in c.items() if k != "detail"}, "since_day": start.astimezone(zone()).date().isoformat() if start else None})
    return {"down": out, "back": back(session, now, cards)}


def plan_telling(prior: dict[str, Any], cards: list[dict[str, Any]], active: set[str], now: datetime) -> tuple[list[dict[str, str]], dict[str, Any], list[dict[str, Any]]]:
    """What to tell the phone, the record to keep, and the outages that ended. Pure: the caller sends and saves, and marks
    `told` only on what was delivered. Told once when an outage starts and once when it ends; an outage that comes back
    within TELL_EVERY of the last telling stays on its card and in the brief without another push, and one with no
    channel to hear it is told when there is one."""
    state: dict[str, Any] = {}
    messages: list[dict[str, str]] = []
    ended: list[dict[str, Any]] = []
    seen = set()
    for c in cards:
        key = c["key"]
        seen.add(key)
        was = prior.get(key) or {}
        entry = {"label": c["label"], "since": c["since"], "open": True, "told": was.get("told"), "told_this": bool(was.get("open") and was.get("told_this"))}
        told = health.moment(entry["told"])
        if not entry["told_this"] and (told is None or now - told >= TELL_EVERY):
            messages.append({"key": key, "title": f"Needs you: {c['label']}", "body": f"{c['text']} {c['step']}", "link": "/confirm#sources", "kind": "down"})
        state[key] = entry
    for key, was in prior.items():
        if key in seen:
            continue
        if key not in active:
            continue  # removed: forgotten, without a message
        if was.get("open"):
            label = was.get("label") or "A source"
            text = f"{label} is being read again{_span(was.get('since'), now)}."
            ended.append({"key": key, "label": label, "text": text, "since": was.get("since"), "at": now.isoformat()})
            if was.get("told_this"):
                messages.append({"key": key, "title": f"Back: {label}", "body": text, "link": "/settings/connectors", "kind": "back"})
        told = health.moment(was.get("told"))
        if told is not None and now - told < TELL_EVERY:
            state[key] = {**was, "open": False, "told_this": False}  # kept only to hold the next push back
    return messages, state, ended


def keep(session: Session, state: dict[str, Any], ended: list[dict[str, Any]], now: datetime) -> None:
    """Write what changed, and only that: a setting written moves the data version and throws the worked-out plan away."""
    if state != (get_setting(session, OPEN, {}) or {}):
        set_setting(session, OPEN, state)
    if ended:
        kept = [b for b in (get_setting(session, BACK, []) or []) if b.get("key") not in {e["key"] for e in ended} and (at := health.moment(b.get("at"))) is not None and now - at < timedelta(hours=BACK_HOURS)]
        set_setting(session, BACK, kept + ended)


def run_words(name: str, ok: bool | None, summary: str) -> str:
    """A job run's line for a model or a key. A failed run's summary is the failure as it was said, a mail server's or a
    vendor's words among it, and the self-check's lists its problems the same way. The Jobs page keeps them for the owner;
    a model, which would read them as part of its context, gets the name of the run and fixed words (sweep 26)."""
    if ok is False:
        return "this run did not go through; the Jobs page says why"
    if name == "self_check":
        return summary.split(": ", 1)[0]
    return summary


def check_words(check: dict[str, Any]) -> dict[str, Any]:
    """The self-check for a model or a key: each source's name and how it is, each problem without what was said."""
    sources = check.get("sources") or []
    named = {f"{s.get('name')}: {s.get('error')}" for s in sources if s.get("error")}
    problems = [f"{s.get('name')}: could not be read" for s in sources if s.get("error")] + [p.split(": ", 1)[0] for p in check.get("problems") or [] if p not in named]
    return {**check, "problems": problems, "sources": [{**s, "error": "could not be read" if s.get("error") else ""} for s in sources]}
