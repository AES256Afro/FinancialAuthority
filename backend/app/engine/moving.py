"""Moving day plan (M43): one dated checklist from today to the first rent at the next home, built from what
Housing already holds. Each step says what to do, by when, and why; a step the app can see done (notice given, the
move-in check paid, an amount added) ticks itself, and the owner ticks the rest. The notice letter is drafted from the
lease's dates; it leaves the app only as an email the owner approves, or as text the owner copies.

Nothing here moves money or sends anything."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, Income, Payment
from ..store import get_setting, set_setting
from . import housing as hs

TICKS = "move.ticks"
SOON_DAYS = 7
DEPOSIT_DAYS = 30  # Texas gives a landlord 30 days after move-out (Property Code 92.103); a lease may say otherwise


def _words(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _long(d: date) -> str:
    return f"{d:%A, %B} {d.day}, {d.year}"


def _money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "an amount not known yet"


def ticks(session: Session) -> dict[str, str]:
    return get_setting(session, TICKS, {}) or {}


def tick(session: Session, step_id: str, done: bool, today: date) -> dict[str, str]:
    current = ticks(session)
    if done:
        current[step_id[:80]] = today.isoformat()
    else:
        current.pop(step_id, None)
    set_setting(session, TICKS, current)
    return current


def notice_letter(old: dict[str, Any], today: date) -> str:
    """The written notice, from the lease's dates. The owner fills in the bracketed parts before it goes anywhere."""
    out_on = date.fromisoformat(old["ends_on"]) if old.get("ends_on") else None
    to = old.get("payee") or "the leasing office"
    lines = [
        _long(today), "",
        f"To: {to}",
        f"Re: Notice to vacate, {old['name']}", "",
        f"This letter is my written notice that I will move out of {old['name']}" + (f" on {_long(out_on)}." if out_on else "."),
        "Please confirm in writing that you received this notice and the move-out date.", "",
        "Please send my security deposit, with an itemized list of any deductions, to my forwarding address:",
        "[your new address]", "",
        "Thank you,",
        "[your name]",
        "[your phone number]",
    ]
    return "\n".join(lines)


def moving_plan(session: Session, today: date) -> dict[str, Any]:
    ov = hs.overview(session, today)
    move = ov.get("move")
    homes = {h["id"]: h for h in ov["homes"]}
    old = homes.get(ov.get("current_id"))
    new = homes.get(ov.get("upcoming_id"))
    if not new:
        return {"as_of": today.isoformat(), "move": None, "steps": [], "counts": {}, "next": None, "letter": None,
                "note": "No move is planned. Add your next home on Housing, with the day you move in, and the plan builds itself."}
    on = date.fromisoformat(new["starts_on"])
    done_by_owner = ticks(session)
    steps: list[dict[str, Any]] = []

    def add(step_id: str, when: date | None, title: str, words: str, kind: str, *, done_auto: bool = False, link: str = "/move", source: str = "", action: str | None = None) -> None:
        done_on = done_by_owner.get(step_id)
        done = done_auto or bool(done_on)
        if done:
            state = "done"
        elif when is None:
            state = "open"
        elif when < today:
            state = "late"
        elif (when - today).days <= SOON_DAYS:
            state = "soon"
        else:
            state = "later"
        steps.append({"id": step_id, "date": when.isoformat() if when else None, "title": title, "words": words, "kind": kind, "state": state,
                      "done": done, "done_by": "the app" if done_auto else ("you" if done_on else None), "done_on": done_on, "link": link, "source": source, "action": action,
                      "days_left": (when - today).days if when else None})

    # The old lease: written notice, and the deposit coming back
    if old:
        n = old.get("notice")
        if n:
            by = date.fromisoformat(n["by"])
            add("notice", by, f"Give {old['name']} written notice", f"Notice that you move out on {_words(date.fromisoformat(old['ends_on']))}. The letter below is drafted from your lease's dates: fill in the bracketed parts, then give it in writing (the office's portal, an email, or on paper) and keep a copy.",
                "notice", done_auto=bool(n.get("given_on")), link="/housing", source=old["source"], action="letter")
        elif old.get("ends_on"):
            add("notice", None, f"Give {old['name']} written notice", "Add the days of notice your lease needs on Housing, and this step gets its date.", "notice", link="/housing", source=old["source"], action="letter")
        if old.get("ends_on"):
            out_on = date.fromisoformat(old["ends_on"])
            back = date.fromisoformat(old["deposit_back_on"]) if old.get("deposit_back_on") else out_on + timedelta(days=DEPOSIT_DAYS)
            held = old.get("deposit") or 0.0
            add("deposit-back", back, f"Your deposit back from {old['payee'] or old['name']}",
                (f"{_money(held)} held. " if held else "") + f"A landlord in Texas has {DEPOSIT_DAYS} days after you move out and give a forwarding address; your lease may say otherwise. If it has not come by then, ask in writing.",
                "deposit", link="/housing", source=old["source"])
        for u in old.get("utilities") or []:
            if u["billed"] in ("direct", "submetered") and not u.get("moves_with_you"):
                who = u.get("billing_company") or u.get("provider") or u["what"]
                add(f"final-{u['id']}", (date.fromisoformat(old["ends_on"]) + timedelta(days=30)) if old.get("ends_on") else None, f"The last bill from {who}" if who == u["what"] else f"The last {u['what'].lower()} bill from {who}",
                    "It comes after you move out, for the last days you were there. When it arrives, add its amount on Housing; the mail reader watches for it.", "utility", link="/housing", source=f"utility:{u['id']}")

    # Getting in: each dated cost (the move-in check first)
    check = None
    for c in sorted(new.get("costs") or [], key=lambda c: (c.get("due_on") or "9999", c["id"])):
        if c.get("paid"):
            continue
        paid = False
        if c.get("bill_id") and c.get("due_on"):
            paid = session.exec(select(Payment).where(Payment.bill_id == c["bill_id"], Payment.due_date == date.fromisoformat(c["due_on"]), Payment.status == "paid")).first() is not None
        when = date.fromisoformat(c["due_on"]) if c.get("due_on") else None
        if check is None and c.get("amount") and when:
            check = c
            account = session.get(Account, new["pay_from_account_id"]) if new.get("pay_from_account_id") else None
            words = f"Pay to {new['payee'] or new['name']}. Bring a cashier's check or money order for {_money(c['amount'])}" + (f", from {account.name}" if account else "") + "; a bank makes one at the counter, often for a small fee."
            try:
                mc = hs.move_check(session, today, new["id"])
                words += " " + mc["word"]
            except KeyError:
                pass
            add(f"cost-{c['id']}", when, f"{c['name']}: {_money(c['amount'])}", words, "check", done_auto=paid, link="/move", source=f"home:{new['id']}")
        else:
            add(f"cost-{c['id']}", when, c["name"] + (f": {_money(c['amount'])}" if c.get("amount") is not None else ""),
                "Amount not known yet: add it on Moving home and the plan counts it." if c.get("amount") is None else ("No date yet, so the plan does not count it: give it the day you pay." if not when else "Counted in your plan on its day."),
                "cost", done_auto=paid, link="/move", source=f"home:{new['id']}")

    # Services at the new home
    before = on - timedelta(days=7)
    for u in new.get("utilities") or []:
        if u.get("moves_with_you"):
            add(f"util-{u['id']}", before, f"Tell {u['provider'] or u['what']} you are moving", f"Give them the new address and the move date, {_words(on)}, so {u['what'].lower()} moves with you. Nothing new to set up.", "utility",
                link="/housing", source=f"utility:{u['id']}")
        elif u["billed"] in ("direct", "submetered"):
            who = u.get("billing_company") or u.get("provider") or "the property"
            add(f"util-{u['id']}", before, f"Set up {u['what'].lower()} for {new['name']}", f"With {who}, starting {_words(on)}." + (" Add its usual amount on Housing once you know it." if u.get("amount") is None else ""), "utility",
                link="/housing", source=f"utility:{u['id']}")
    for c in new.get("charges") or []:
        if c.get("kind") == "insurance":
            add(f"insurance-{c['id']}", on - timedelta(days=3), f"Renter's insurance from {_words(on)}", "Most leases ask for proof before the keys. Add the amount on Housing when you have the quote.", "insurance", link="/housing", source=new["source"])

    # The day itself, and telling people the new address
    add("move-day", on, f"Move into {new['name']}" + (f"; move out of {old['name']}" if old else ""), (new.get("note") or "Keys, the truck, and a last look at the old place.")[:240], "move", done_auto=today > on, link="/housing", source=new["source"])
    after = on + timedelta(days=3)
    add("address-mail", after, "Forward your mail", "Ask the post office to forward mail from the old address (USPS change of address, online or at a post office).", "address")
    for inst in sorted({(a.institution or a.name) for a in session.exec(select(Account).where(Account.active == True)).all() if a.kind in ("checking", "savings", "card", "loan")}):  # noqa: E712
        add(f"address-bank-{inst.lower()[:30]}", after, f"New address at {inst}", "In the bank's app or website, under your profile.", "address")
    for inc in session.exec(select(Income).where(Income.active == True)).all():  # noqa: E712
        add(f"address-pay-{inc.id}", after, f"New address for {inc.name}", "Your employer needs it for your pay records and your tax forms.", "address")

    # The first regular payment at the new home
    if new.get("first_due"):
        first = date.fromisoformat(new["first_due"])
        add("first-rent", first, f"First regular payment at {new['name']}: {_money(new.get('to_landlord_monthly'))}", f"To {new['payee'] or new['name']}, on the {first.day}{'st' if first.day in (1, 21, 31) else 'nd' if first.day in (2, 22) else 'rd' if first.day in (3, 23) else 'th'}, and every month after. Counted in your plan already.",
            "rent", done_auto=today > first, link="/housing", source=new["source"])

    steps.sort(key=lambda s: (s["date"] or "9999-12-31", s["kind"] != "notice", s["title"]))
    counts = {k: sum(1 for s in steps if s["state"] == k) for k in ("done", "late", "soon", "later", "open")}
    nxt = next((s for s in steps if not s["done"]), None)
    return {
        "as_of": today.isoformat(), "move": {"from": old["name"] if old else None, "to": new["name"], "on": on.isoformat(), "days_left": (on - today).days},
        "steps": steps, "counts": counts, "total": len(steps), "next": nxt, "letter": notice_letter(old, today) if old else None,
        "landlord": old.get("payee") if old else None,
        "not_advice": "A checklist from what Housing holds. Your lease, and the law where you live, are what hold. Not legal or financial advice.",
    }
