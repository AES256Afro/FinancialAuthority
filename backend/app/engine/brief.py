"""The brief, version 0: what needs attention, as plain sentences the engine writes from its own numbers.

No model is involved. A model may read these through brief_today and say them in its own words.
"""

from __future__ import annotations


def money(v: float) -> str:
    v = round(v)
    return f"−${abs(v):,}" if v < 0 else f"${v:,}"


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def when(iso: str) -> str:
    """'2026-10-25' as 'Oct 25'."""
    y, m, d = iso.split("-")
    return f"{MONTHS[int(m) - 1]} {int(d)}"


def build_brief(snap: dict) -> list[dict]:
    items: list[dict] = []
    pay = snap["next_paycheck"]
    before = snap["due_before_payday"]
    if before["total"] > 0:
        names = ", ".join(e["name"] for e in before["items"][:3])
        taps = [e["name"] for e in before["items"] if not e["autopay"]]
        items.append({
            "severity": "warning" if snap["cash"] - before["total"] < snap["buffer"] else "info",
            "text": f"{money(before['total'])} clears before payday: {names}." + (f" {', '.join(taps)} needs a tap." if taps else " All autopay."),
            "link": "/bills",
        })
    if pay["amount"]:
        items.append({"severity": "info", "text": f"Payday in {pay['days']} days, {money(pay['amount'])} expected." if pay["days"] > 1 else ("Payday today." if pay["days"] == 0 else f"Payday tomorrow, {money(pay['amount'])} expected."), "link": "/"})
    adv = snap["advances"]
    if adv["total"] > 0:
        items.append({"severity": "info", "text": f"{len(adv['items'])} advance{'s' if len(adv['items']) != 1 else ''} repay {money(adv['total'])} as the paycheck lands.", "link": "/"})
    borrowing = snap.get("borrowing") or {}
    if borrowing.get("since_payday", {}).get("advances"):
        sp = borrowing["since_payday"]
        items.append({"severity": "warning", "text": f"{sp['advances']} advance{'s' if sp['advances'] != 1 else ''} taken since payday, {money(sp['advanced'])} from {', '.join(sp['providers'])}; each costs its fee on the next check.", "link": "/borrowing"})
    if borrowing.get("fees_90d", 0) > 0:
        items.append({"severity": "warning", "text": f"Borrowing cost {money(borrowing['fees_90d'])} in fees in the last 90 days, about {money(borrowing['fees_month'])} a month.", "link": "/borrowing"})
    mail = snap.get("mail", {"pending": [], "alerts": []})
    for alert in mail.get("alerts", [])[:3]:
        what = alert["fields"].get("alert", "balance")
        if what in ("overdraft", "declined"):
            amount = alert["fields"].get("amount")
            items.append({"severity": "critical", "text": f"{alert['sender_name'] or 'The bank'} sent {'an overdraft' if what == 'overdraft' else 'a declined-charge'} alert on {when(alert['date']) if alert.get('date') else 'a recent day'}{f' for {money(amount)}' if amount else ''}.", "link": "/mail"})
    past_due_notices = [f for f in mail.get("pending", []) if f["kind"] == "past_due"]
    if past_due_notices:
        names = ", ".join(f["sender_name"] or f["subject"] for f in past_due_notices[:3])
        items.append({"severity": "critical", "text": f"Past-due notice{'s' if len(past_due_notices) > 1 else ''} in the mail from {names}.", "link": "/confirm"})
    for stub in snap.get("paystubs", {}).get("short", [])[:1]:
        items.append({"severity": "warning", "text": f"The {when(stub['pay_date'])} paystub was {money(-stub['difference'])} under the usual net of {money(stub['expected_net'])}.", "link": "/paystubs"})
    payments = snap.get("payments", {})
    if payments.get("past_due"):
        names = ", ".join(f"{p['name']} {money(p['amount'])}" for p in payments["past_due"][:3])
        items.append({"severity": "critical", "text": f"Past due: {names}.", "link": "/bills"})
    if payments.get("late"):
        names = ", ".join(p["name"] for p in payments["late"][:3])
        items.append({"severity": "warning", "text": f"Not matched to a payment yet: {names}. If paid, the ledger will catch up within a sync.", "link": "/bills"})
    bad_weeks = [w for w in snap["calendar"]["weeks"][:6] if w["low"] is not None and w["low"] < 0]
    if bad_weeks:
        w = bad_weeks[0]
        items.append({"severity": "warning", "text": f"At your usual pace of {money(snap['usual_pace']['per_day'])} a day, the week of {when(w['start'])} dips {money(-w['low'])} under the buffer.", "link": "/?view=month"})
    pending = snap["pending"]
    if pending.get("approvals"):
        n = pending["approvals"]
        items.append({"severity": "warning", "text": f"{n} send{'s' if n != 1 else ''} wait{'s' if n == 1 else ''} for your approval; nothing goes out until you tap.", "link": "/confirm"})
    series = pending.get("series", pending["confirmations"])
    if series:
        items.append({"severity": "warning", "text": f"{series} recurring charge{'s' if series != 1 else ''} need{'s' if series == 1 else ''} a name.", "link": "/confirm"})
    mail_pending = [f for f in mail.get("pending", []) if f["kind"] != "past_due"]
    if mail_pending:
        bills = [f for f in mail_pending if f["kind"] == "bill_due"]
        text = f"{len(bills)} bill{'s' if len(bills) != 1 else ''} found in the mail need a look" if bills else f"{len(mail_pending)} thing{'s' if len(mail_pending) != 1 else ''} from the mail need a look"
        items.append({"severity": "warning", "text": text + ".", "link": "/confirm"})
    goals = snap.get("goals", {"per_paycheck": 0.0, "items": []})
    if goals["per_paycheck"] > 0:
        behind = [g["name"] for g in goals["items"] if not g["on_track"]]
        items.append({"severity": "warning" if behind else "info", "text": f"Goals take {money(goals['per_paycheck'])} of this paycheck" + (f"; {', '.join(behind)} {'is' if len(behind) == 1 else 'are'} behind." if behind else ", all on track."), "link": "/today"})
    safe = snap["safe_to_spend"]
    items.append({"severity": "critical" if safe["amount"] < 0 else "info", "text": (f"Short by {money(-safe['amount'])} through {when(safe['through'])} after bills and set-asides." if safe["amount"] < 0 else f"Safe to spend {money(safe['amount'])} through {when(safe['through'])}." + (f" After goals, {money(safe['after_goals'])}." if goals["per_paycheck"] > 0 else "")), "link": "/"})
    return items
