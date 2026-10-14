"""The brief, version 0: what needs attention, as plain sentences the engine writes from its own numbers.

No model is involved. A model may read these through brief_today and say them in its own words.
"""

from __future__ import annotations

from .snapshot import _money


def money(v: float) -> str:
    v = round(v)
    return f"−${abs(v):,}" if v < 0 else f"${v:,}"


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
        items.append({"severity": "warning", "text": f"At your usual pace of {money(snap['usual_pace']['per_day'])} a day, the week of {w['start'][5:]} dips {money(-w['low'])} under the buffer.", "link": "/?view=month"})
    pending = snap["pending"]
    if pending["confirmations"]:
        items.append({"severity": "warning", "text": f"{pending['confirmations']} recurring charge{'s' if pending['confirmations'] != 1 else ''} need{'s' if pending['confirmations'] == 1 else ''} a name.", "link": "/confirm"})
    safe = snap["safe_to_spend"]
    items.append({"severity": "critical" if safe["amount"] < 0 else "info", "text": (f"Short by {money(-safe['amount'])} through {safe['through'][5:]} after bills and set-asides." if safe["amount"] < 0 else f"Safe to spend {money(safe['amount'])} through {safe['through'][5:]}."), "link": "/"})
    for it in items:
        it["amount_check"] = _money(0)
    return items
