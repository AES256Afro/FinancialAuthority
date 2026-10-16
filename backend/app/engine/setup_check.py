"""Setup that checks itself (M29): each step of Settings › Setting up with its state now, read from what the app
already records, and a deeper test on request that tries each connection for real. Every check says what is
wrong in plain words and where to fix it. Nothing here changes anything."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from ..config import settings
from ..models import BankLink, Income, MailAccount
from ..store import get_setting

OK, WARN, TODO, ERROR = "ok", "warn", "todo", "error"


def _check(id: str, title: str, state: str, detail: str, fix: str = "", link: str = "") -> dict[str, Any]:  # noqa: A002
    return {"id": id, "title": title, "state": state, "detail": detail, "fix": fix, "link": link}


def _age(when: datetime | None) -> timedelta | None:
    if when is None:
        return None
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when


def _hours(delta: timedelta | None) -> str:
    if delta is None:
        return "never"
    h = delta.total_seconds() / 3600
    return "under an hour ago" if h < 1 else f"{round(h)} hours ago" if h < 48 else f"{round(h / 24)} days ago"


def checks(session: Session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    missing = [name for name, value in (("FA_PASSWORD", settings.password), ("FA_SESSION_SECRET", settings.session_secret), ("FA_DATA_KEY", settings.data_key)) if not value]
    out.append(_check("secrets", "The app's own secrets", OK if not missing else ERROR,
                      "All three are set." if not missing else f"Not set: {', '.join(missing)}.",
                      "" if not missing else "Set them in the app's settings (BoxPilot: the app's card; Docker Compose: the .env file), then restart the app.", "/settings/setup#secrets"))

    has_income = session.exec(select(Income).where(Income.active == True)).first() is not None  # noqa: E712
    out.append(_check("paycheck", "Your paycheck", OK if has_income else TODO,
                      "The app knows when you are paid." if has_income else "The app does not know your paycheck yet, so it cannot plan by paycheck.",
                      "" if has_income else "Name your paycheck: how often, how much and the next payday.", "/confirm"))

    boxes = list(session.exec(select(MailAccount).where(MailAccount.active == True)).all())  # noqa: E712
    if not boxes:
        out.append(_check("mail", "Mail", TODO, "No mailbox is connected, so bills and paystubs do not read themselves.", "Add a mailbox: Proton Mail through the Bridge, Gmail, or any other.", "/settings/connectors#mail"))
    for m in boxes:
        age = _age(m.last_sync_at)
        if m.last_error:
            state, detail = ERROR, f"Last read failed: {m.last_error[:160]}"
            fix = "Open the mailbox on Connectors and press Test first; the message says what to change." + (" For Proton, the Bridge's sign-in steps are there too." if m.kind == "proton" else "")
        elif age is None:
            state, detail, fix = WARN, "Saved, not read yet.", "Press Sync now on Connectors, or wait up to fifteen minutes."
        elif age > timedelta(hours=2):
            state, detail, fix = WARN, f"Last read {_hours(age)}; it should be every fifteen minutes.", "Check Settings › Jobs for mail_sync."
        else:
            state, detail, fix = OK, f"Read {_hours(age)}; {m.messages_seen} messages seen.", ""
        out.append(_check(f"mail-{m.id}", f"Mail: {m.label}", state, detail, fix, "/settings/connectors#mail"))

    links = list(session.exec(select(BankLink).where(BankLink.active == True)).all())  # noqa: E712
    every = timedelta(hours=float((get_setting(session, "banks.freshness", {}) or {}).get("sync_every_hours", 4)))
    if not links:
        out.append(_check("bank", "Your bank", TODO, "No bank is linked; transactions come only from imported files.", "Link a bank through Plaid or Teller, or import your bank's file.", "/settings/connectors#banks"))
    for link in links:
        age = _age(link.last_sync_at)
        if link.needs_login:
            state, detail, fix = ERROR, "The bank wants you to sign in again.", "Press Sign in again on Connectors; the bank's own page opens."
        elif link.last_error:
            state, detail, fix = ERROR, f"Last read failed: {link.last_error[:160]}", "Press Sync now on Connectors; if it fails again, sign in again."
        elif age is None or age > every * 2 + timedelta(hours=1):
            state, detail, fix = WARN, f"Last read {_hours(age)}.", "Press Sync now on Connectors, and check Settings › Jobs for bank_sync."
        else:
            state, detail, fix = OK, f"Read {_hours(age)}.", ""
        out.append(_check(f"bank-{link.id}", f"Bank: {link.institution or link.provider}", state, detail, fix, "/settings/connectors#banks"))

    url = settings.public_url or ""
    report = get_setting(session, "selfcheck", {}) or {}
    if not url:
        out.append(_check("publish", "Reach it from anywhere", TODO, "There is no https address yet; the app works on your home network only.", "Publish it (BoxPilot's Cloudflare Tunnel app, or Tailscale) and set FA_PUBLIC_URL.", "/settings/setup#publish"))
    elif not url.startswith("https://"):
        out.append(_check("publish", "Reach it from anywhere", WARN, f"{url} is not https; phones and Claude need https.", "Use the https address your tunnel gives.", "/settings/setup#publish"))
    elif report.get("public_url_reachable") is False:
        out.append(_check("publish", "Reach it from anywhere", WARN, f"{url} did not answer the last self-check.", "Check the tunnel or Tailscale; the address must reach this app.", "/settings/setup#publish"))
    else:
        out.append(_check("publish", "Reach it from anywhere", OK, f"Published at {url}.", "", "/settings/setup#publish"))

    from ..notify import push_state
    from ..webpush import subscriptions

    devices = subscriptions(session)
    push = push_state(session)
    if devices or push["configured"]:
        out.append(_check("phone", "Your phone", OK, f"{len(devices)} device{'s' if len(devices) != 1 else ''} get the app's own notifications" + (f"; {push['kind']} is set up too." if push["configured"] else "."), "", "/settings/notifications"))
    else:
        out.append(_check("phone", "Your phone", TODO, "No phone gets the morning brief or reminders yet.", "Add the app to your Home Screen, then turn on notifications.", "/settings/notifications"))

    from ..llm.roles import provider_for

    advisor = provider_for(session, "advisor")
    out.append(_check("models", "The chat's model", OK if advisor else WARN,
                      "A model answers in the chat." if advisor else "No model is set for the chat; the engine still answers in its own words.",
                      "" if advisor else "Choose Ollama on the server (free), or add a key for Claude.", "/settings/models"))

    from .. import restore

    try:
        b = restore.status()
        fresh = b["latest_age_hours"] is not None and b["latest_age_hours"] <= 48
        state = OK if (b["database"]["ok"] and fresh) else ERROR if not b["database"]["ok"] else WARN
        hours = round(b["latest_age_hours"] or 0)
        newest = "under an hour" if hours < 1 else f"{hours} hour{'s' if hours != 1 else ''}"
        detail = (f"{b['count']} {'copy' if b['count'] == 1 else 'copies'}; the newest is {newest} old. The database passes its check."
                  if state == OK else f"The database did not pass its check: {b['database']['result']}." if not b["database"]["ok"] else "No copy in the last two days.")
        out.append(_check("backups", "Backups", state, detail, "" if state == OK else "Open Backups and take a copy now; check Settings › Jobs for restore_point_daily.", "/settings/backups"))
    except Exception as error:  # noqa: BLE001 - a check that cannot run is itself the answer
        out.append(_check("backups", "Backups", ERROR, f"The copies could not be read: {error}", "Check the data folder's free space and permissions.", "/settings/backups"))
    return out


def summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {OK: 0, WARN: 0, TODO: 0, ERROR: 0}
    for c in items:
        counts[c["state"]] += 1
    return counts


def test_everything(session: Session) -> list[dict[str, Any]]:
    """Try each connection now: every mailbox signs in over IMAP, the bank keys are checked with Plaid, and the
    https address is fetched. Returns one result per test, in plain words."""
    import httpx

    from ..connectors.mail import account_password, explain_error, test_account

    results: list[dict[str, Any]] = []
    for m in session.exec(select(MailAccount).where(MailAccount.active == True)).all():  # noqa: E712
        try:
            got = test_account(m.host, m.port, m.username, account_password(m), m.use_ssl, m.starttls)
            results.append({"id": f"mail-{m.id}", "title": f"Mail: {m.label}", "ok": True, "detail": f"Signed in; {got.get('inbox_messages', 0)} messages in the inbox so far." + (" The Bridge is signed in and answering." if m.kind == "proton" else "")})
        except Exception as error:  # noqa: BLE001
            results.append({"id": f"mail-{m.id}", "title": f"Mail: {m.label}", "ok": False, "detail": explain_error(m.host, error)})
    from ..connectors import CONNECTORS

    plaid = CONNECTORS["plaid"]
    api = plaid.api(session)  # type: ignore[attr-defined]
    if api is not None:
        try:
            keys = api.check_keys()
            results.append({"id": "plaid", "title": "Plaid keys", "ok": keys.get("result") == "ok", "detail": keys.get("message", "")})
        except Exception as error:  # noqa: BLE001
            results.append({"id": "plaid", "title": "Plaid keys", "ok": False, "detail": str(error)[:200]})
    if settings.public_url.startswith("https://"):
        try:
            ok = httpx.get(settings.public_url.rstrip("/") + "/api/health", timeout=8.0).status_code == 200
            results.append({"id": "publish", "title": "The https address", "ok": ok, "detail": "It answered." if ok else "It did not answer with this app."})
        except httpx.HTTPError as error:
            results.append({"id": "publish", "title": "The https address", "ok": False, "detail": f"It did not answer: {type(error).__name__}. From inside the server a tailnet address may not answer even when your devices reach it."})
    return results
