"""Next steps: everything the app needs from the owner, each with the exact place to do it. A vendor that
wants a new sign-in, a key to paste, a balance to update from a statement, a mailbox to fix, a send to
approve. The screens show them as tappable links; the models can list them too."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from ..config import settings
from ..models import Account, Approval, BankLink, ExtractedFact, Income, MailAccount, Paystub, RecurringSeries
from ..store import get_setting

ORDER = {"critical": 0, "warning": 1, "info": 2}
STALE_DAYS = 35


def _step(id: str, severity: str, text: str, action: str, link: str, source: str = "") -> dict:  # noqa: A002
    return {"id": id, "severity": severity, "text": text, "action": action, "link": link, "source": source}


def next_steps(session: Session, today: date) -> dict:
    steps: list[dict] = []

    if not settings.data_key:
        steps.append(_step("data-key", "critical", "Secrets cannot be stored until FA_DATA_KEY is set in the app's environment.", "How", "/settings/setup#secrets", "setting:data_key"))

    links = session.exec(select(BankLink).where(BankLink.active == True)).all()  # noqa: E712
    link_by_account: dict[str, BankLink] = {}
    for link in links:
        for ext in (link.accounts or "").split(","):
            if ext:
                link_by_account[f"{link.provider}:{ext}"] = link
        if link.needs_login:
            steps.append(_step(f"link-signin-{link.id}", "critical", f"{link.institution or 'A bank'} wants a new sign-in before it can be read again.", "Sign in again", f"/settings/connectors?signin={link.id}", f"link:{link.id}"))
        elif link.last_error:
            steps.append(_step(f"link-error-{link.id}", "warning", f"{link.institution or 'A bank'} did not sync: {link.last_error[:90]}", "Sync now", f"/settings/connectors?sync={link.id}", f"link:{link.id}"))
    banks = get_setting(session, "banks.secrets", {}) or {}
    plaid_ready = bool((banks.get("plaid") or {}).get("client_id") and (banks.get("plaid") or {}).get("secret"))
    teller_ready = bool((banks.get("teller") or {}).get("application_id"))
    plaid_state = get_setting(session, "banks.plaid", {}) or {}
    if plaid_ready and (banks.get("plaid") or {}).get("environment", "sandbox") in ("", "sandbox"):
        steps.append(_step("plaid-sandbox", "info", "Plaid is in its sandbox, which reaches test banks only. For your real bank, ask Plaid's dashboard for production access on the Trial plan, then set the environment to production and paste the production secret.", "Connectors", "/settings/connectors#banks", "banks"))
    keys = plaid_state.get("keys") or {}
    if plaid_ready and keys.get("result") not in (None, "ok"):
        steps.append(_step("plaid-keys", "warning", f"Plaid: {keys.get('message', 'the keys were refused')}", "Connectors", "/settings/connectors#banks", "banks"))
    if plaid_ready and plaid_state.get("redirect_ok") is False and plaid_state.get("redirect_uri"):
        steps.append(_step("plaid-redirect", "warning", f"Register {plaid_state['redirect_uri']} as an allowed redirect URI in the Plaid dashboard (Team Settings, API), or banks that sign in with OAuth, Capital One among them, cannot connect.", "Then connect", "/settings/connectors?connect=plaid", "banks"))
    if not links:
        if plaid_ready or teller_ready:
            via = "Plaid" if plaid_ready else "Teller"
            steps.append(_step("bank-connect", "info", f"Connect your bank through {via}: the bank's own login opens, and the ledger reads itself.", "Connect", f"/settings/connectors?connect={via.lower()}", "banks"))
        else:
            steps.append(_step("bank-keys", "info", "Connect a bank: paste your free Plaid or Teller developer keys, then sign in at the bank.", "Connectors", "/settings/connectors#banks", "banks"))

    mailboxes = session.exec(select(MailAccount).where(MailAccount.active == True)).all()  # noqa: E712
    if not mailboxes:
        steps.append(_step("mail-connect", "info", "Connect a mailbox so bills, statements and paystubs read themselves.", "Add a mailbox", "/settings/connectors#mail", "mail"))
    for m in mailboxes:
        if m.last_error and m.host == "bridge" and m.last_error.startswith("The Bridge is not running"):
            steps.append(_step(f"bridge-signin-{m.id}", "warning", "Proton Mail waits on the Bridge: sign in to it once on the server, with your Proton password and code, then paste the password it prints. The commands are under Mailboxes.", "Connectors", "/settings/connectors#mail", f"mailbox:{m.id}"))
        elif m.last_error:
            steps.append(_step(f"mail-error-{m.id}", "warning", f"{m.label} could not be read: {m.last_error[:90]}", "Fix the mailbox", "/settings/connectors#mail", f"mailbox:{m.id}"))

    investments = session.exec(select(Account).where(Account.active == True, Account.kind == "investment")).all()  # noqa: E712
    if not investments:
        steps.append(_step("invest-add", "info", "Add your 401(k), IRA or brokerage account: sign in at the vendor, or enter the balance from the last statement.", "Investing", "/investments", "investments"))
    for a in investments:
        age = (today - a.balance_at.date()).days if a.balance_at else None
        if age is None or age > STALE_DAYS:
            what = f"{a.name} at {a.institution}" if a.institution else a.name
            if a.connector == "manual":
                steps.append(_step(f"invest-stale-{a.id}", "warning", f"{what}: {'no balance yet' if age is None else f'the balance is {age} days old'}. Enter the new one from the statement, or import the vendor's holdings file.", "Update the balance", f"/investments?update={a.id}", f"account:{a.id}"))
            else:
                link = link_by_account.get(f"{a.connector}:{a.external_id}")
                target = f"/settings/connectors?sync={link.id}" if link else "/settings/connectors#banks"
                steps.append(_step(f"invest-stale-{a.id}", "warning", f"{what}: the balance is {age} days old. Sync at the vendor.", "Sync", target, f"account:{a.id}"))

    from .borrowing import borrowing_summary

    borrowing = borrowing_summary(session, today)
    if borrowing["fees_90d"] > 0 or borrowing["outstanding"]:
        steps.append(_step("borrowing", "warning", f"Borrowing cost ${borrowing['fees_90d']:,.0f} in fees in the last 90 days" + (f"; {borrowing['outstanding']} advance{'s' if borrowing['outstanding'] != 1 else ''} still out" if borrowing["outstanding"] else "") + ". The cycle, its cost and the way out are laid out.", "Borrowing", "/borrowing", "borrowing"))
    for a in session.exec(select(Account).where(Account.active == True, Account.connector == "manual", Account.kind == "card")).all():  # noqa: E712
        steps.append(_step(f"link-card-{a.id}", "warning", f"{a.name} is on file by hand, with {'no balance' if a.balance is None else 'a typed balance'}: link it through Plaid and its balance, its payments and the Affirm charges on it count by themselves.", "Connect", "/settings/connectors?connect=plaid", f"account:{a.id}"))
    from ..models import AccountMove

    for mv in session.exec(select(AccountMove).where(AccountMove.status == "moving")).all():
        if mv.target_account_id is None:
            steps.append(_step(f"move-target-{mv.id}", "warning", f"{mv.name}: add {mv.target_name or 'the new account'} to the app (link it through Plaid, or add it by hand) so each switch is confirmed from its ledger.", "Connect", "/settings/connectors?connect=plaid", f"move:{mv.id}"))
    from .assets import assets_overview

    for a in assets_overview(session, today)["items"]:
        if a["needs_update"]:
            steps.append(_step(f"asset-{a['id']}", "info", f"{a['name']}: its value was set on {a['value_on']}. Update it so net worth stays true.", "Update the value", "/accounts#other", a["source"]))
    if session.exec(select(Income.id).where(Income.active == True)).first() is None:  # noqa: E712
        steps.append(_step("paycheck-none", "warning", "No paycheck on file: add it. Until then safe to spend counts no pay, and the payday it shows is a placeholder two weeks out.", "Add your paycheck", "/paystubs#paycheck", "incomes"))
    if session.exec(select(Paystub.id)).first() is None:
        steps.append(_step("paystub-none", "info", "No paystub yet: forward a pay statement PDF to the mailbox, or drop it into Imports.", "Paystubs", "/paystubs", "paystubs"))

    from .housing import overview as housing_overview

    housing = housing_overview(session, today)
    for h in housing["homes"]:
        n = h["notice"]
        if n and not n["given_on"] and h["state"] == "current":
            steps.append(_step(f"home-notice-{h['id']}", "critical" if n["status"] == "crit" else "warning", f"{h['name']}: {n['word']}. Then mark it given on Housing.", "Housing", f"/housing#home-{h['id']}", h["source"]))
        if h["state"] == "upcoming" and (h["missing"] or h["costs_missing"]):
            what = ", ".join((h["missing"] + h["costs_missing"])[:4])
            steps.append(_step(f"home-missing-{h['id']}", "info", f"{h['name']}: no amount yet for {what}. Each one you add is counted in the plan.", "Housing", f"/housing#home-{h['id']}", h["source"]))
        if h["state"] == "current" and h["lease_word"] and h["lease_ends_on"] and h["notice_days"]:
            from datetime import date as _d, timedelta as _td

            if 0 <= (_d.fromisoformat(h["lease_ends_on"]) - _td(days=h["notice_days"] + 1) - today).days <= 30:
                steps.append(_step(f"home-lease-{h['id']}", "warning", f"{h['name']}: {h['lease_word']}", "Housing", f"/housing#home-{h['id']}", h["source"]))
        if h["state"] == "upcoming" and h["costs_undated"]:
            what = ", ".join(h["costs_undated"][:3])
            steps.append(_step(f"home-undated-{h['id']}", "info", f"{h['name']}: {what} has no date, so the plan does not count it. Give it the day you pay, or mark it paid if it is.", "Moving home", "/move", h["source"]))
    if housing.get("upcoming_id"):
        # The moving day plan's steps due within the week (the notice has its own step above)
        from .moving import moving_plan

        for st in moving_plan(session, today)["steps"]:
            if st["done"] or st["id"] == "notice" or st["date"] is None or st["state"] not in ("late", "soon"):
                continue
            steps.append(_step(f"move-{st['id']}", "warning" if st["state"] == "late" else "info",
                               f"{st['title']}, {'was due' if st['state'] == 'late' else 'by'} {date.fromisoformat(st['date']):%a %b} {date.fromisoformat(st['date']).day}.", "Moving day plan", "/move", st["source"] or "move"))
    from .receipts import deadlines

    for d in deadlines(session, today, within_days=30):
        if d["what"] == "return" and d["days_left"] <= 7:
            steps.append(_step(f"return-{d['document_id']}", "critical" if d["days_left"] <= 2 else "warning", f"{d['word']} ({d['days_left']} day{'s' if d['days_left'] != 1 else ''} left). Keep it, or take it back.", "The receipt", "/documents#receipts", d["source"]))
        elif d["what"] == "warranty":
            steps.append(_step(f"warranty-{d['document_id']}", "info", f"{d['word']}: if anything is wrong with it, claim before then.", "The receipt", "/documents#receipts", d["source"]))
    # A rent bill not tied to a home: with no home yet, make it one; beside a home, it may be the same rent twice.
    for b in housing["rent_bills_to_adopt"][:1]:
        if not housing["homes"]:
            steps.append(_step(f"home-adopt-{b['id']}", "info", f"Make {b['name']} your home on Housing: its fees, due day, lease end and notice then count too.", "Housing", "/housing", f"bill:{b['id']}"))
        else:
            said = next((w for w in housing.get("warnings", []) if w.startswith(b["name"])), None)
            steps.append(_step(f"home-adopt-{b['id']}", "warning", said or f"{b['name']} is on your bills list as rent and is not tied to a home on Housing. If it is a home's rent, make it that home's.", "Housing", "/housing", f"bill:{b['id']}"))

    pending_approvals = len(session.exec(select(Approval.id).where(Approval.status == "pending", Approval.for_person_id == None)).all())  # noqa: E711
    if pending_approvals:
        steps.append(_step("approvals", "warning", f"{pending_approvals} send{'s' if pending_approvals != 1 else ''} wait{'s' if pending_approvals == 1 else ''} for your approval; nothing goes out until you tap.", "Approve or deny", "/confirm", "approvals"))
    pending_facts = len(session.exec(select(ExtractedFact.id).where(ExtractedFact.status == "pending")).all())
    if pending_facts:
        steps.append(_step("facts", "warning", f"{pending_facts} thing{'s' if pending_facts != 1 else ''} from the mail and your documents need{'s' if pending_facts == 1 else ''} a look.", "Needs you", "/confirm", "facts"))
    unnamed = len(session.exec(select(RecurringSeries.id).where(RecurringSeries.active == True, RecurringSeries.confirmed == False)).all())  # noqa: E712
    if unnamed:
        steps.append(_step("series", "warning", f"{unnamed} recurring charge{'s' if unnamed != 1 else ''} need{'s' if unnamed == 1 else ''} a name.", "Name them", "/confirm", "series"))

    models = get_setting(session, "models", {}) or {}
    if (models.get("reviewer") or {}).get("provider", "none") == "none":
        steps.append(_step("reviewer", "info", "Choose a reviewer model for a second opinion on each paycheck.", "Models", "/settings/models", "models"))
    push = get_setting(session, "push", {}) or {}
    if push.get("kind", "off") == "off":
        steps.append(_step("push", "info", "Get the morning brief on your phone through ntfy or Gotify.", "Notifications", "/settings/notifications", "push"))
    if not settings.public_url:
        steps.append(_step("publish", "info", "Publish the app to reach it, and let Claude reach it, from anywhere.", "How", "/settings/setup#publish", "public_url"))
    if get_setting(session, "review.pending", None) and (models.get("reviewer") or {}).get("provider", "none") != "none":
        steps.append(_step("review-due", "info", "A review is due after the last change; it runs within the hour, or now.", "Review now", "/chat", "review"))
    for i, problem in enumerate((get_setting(session, "selfcheck", {}) or {}).get("problems", [])[:4]):
        steps.append(_step(f"selfcheck-{i}", "warning", f"Self-check: {problem}", "Jobs", "/settings/jobs", "selfcheck"))

    steps.sort(key=lambda s: ORDER[s["severity"]])
    return {"as_of": today.isoformat(), "steps": steps[:14], "count": len(steps)}
