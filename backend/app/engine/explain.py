"""Why this number: one plain sentence per headline figure, written by the engine from the snapshot's own
figures, so every amount in it is one the engine returned. A model may restate these; it never writes them."""

from __future__ import annotations

from datetime import date


def _usd(v: float) -> str:
    return f"${abs(float(v)):,.2f}"


def _short(d: str | date) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d:%b} {d.day}"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def why_safe(snap: dict) -> str:
    s = snap["safe_to_spend"]
    p = s["parts"]
    payday = snap["next_paycheck"]["date"]
    n = len(snap["due_before_payday"]["items"])
    text = f"You have {_usd(p['cash'])} in checking. Before payday on {_short(payday)}, {_usd(p['due_before_payday'])} leaves for {_count(n, 'payment')}"
    if p["advances"]:
        text += f", and {_usd(p['advances'])} goes back to the advance apps"
    text += f". {_usd(p['buffer'])} stays as your buffer"
    if p["next_window_shortfall"]:
        text += f", and {_usd(p['next_window_shortfall'])} is held back because the {_short(payday)} check cannot cover its own window"
    amount = s["amount"]
    if amount < 0:
        text += f". That leaves {_usd(amount)} below zero, so nothing extra is safe until payday."
    else:
        text += f". That leaves {_usd(amount)} to spend through {_short(s['through'])}."
    if snap["on_card"]["total"]:
        text += f" Bills charged to a card, {_usd(snap['on_card']['total'])} of them, are paid with the card payment instead."
    return text


def why_due_before(snap: dict) -> str:
    d = snap["due_before_payday"]
    payday = snap["next_paycheck"]["date"]
    if not d["items"]:
        return f"Nothing leaves checking before payday on {_short(payday)}."
    parts = "; ".join(f"{g['label'].lower()} {_usd(g['total'])}" for g in d.get("groups", []))
    text = f"{_usd(d['total'])} leaves checking before payday on {_short(payday)}: {parts}."
    nc = d.get("next_check") or {}
    if nc.get("total"):
        text += f" The {_short(nc['start'])} check carries {_usd(nc['total'])} of its own, so it is not counted here."
    return text


def why_window(w: dict) -> str:
    start = w["start"]
    text = f"The {_short(start)} check brings {_usd(w['paycheck'])}"
    if w["carried_in"] > 0:
        text += f" and {_usd(w['carried_in'])} carried in"
    elif w["carried_in"] < 0:
        text += f", less {_usd(w['carried_in'])} the account is already short"
    n = len([i for i in w["bills"]["items"] if not i.get("via_card")])
    text += f". {_usd(w['bills']['total'])} pays {_count(n, 'payment')}"
    if w["advances"]["total"]:
        text += f", {_usd(w['advances']['total'])} repays advances"
    if w["set_aside"]["total"]:
        text += f", and {_usd(w['set_aside']['total'])} is set aside for the window after"
    if w.get("buffer"):
        text += f"; {_usd(w['buffer'])} stays as the buffer"
    text += f". {_usd(w['safe'])} is {'left' if w['safe'] >= 0 else 'missing'} through {_short(w['end'])}, before everyday spending."
    return text


def why_all(snap: dict) -> dict:
    out = {"safe_to_spend": why_safe(snap), "due_before_payday": why_due_before(snap)}
    for w in snap["windows"]:
        out[f"window:{w['start']}"] = why_window(w)
    return out
