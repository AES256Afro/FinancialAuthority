"""The saving ladder: five rungs computed from the owner's own bills, a share of what each check truly leaves
suggested for saving, and the rollover, where a pay-later plan's payment keeps going into the ladder once the
plan ends. Suggested, never automatic: nothing moves until the owner moves it, and the share stays unset until
the owner picks one. Every target and date is the engine's; the page and the models only show them."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Account, InstallmentPlan
from ..store import get_setting, set_setting
from .cadence import next_paydays
from .projection import CASH_KINDS, is_spendable
NOT_ADVICE = "Computed by the engine from your registers and the ledger. Not financial advice."

SETTING = "saving"
PRESETS = {"gentle": 10, "steady": 25, "hard": 50}
SUGGESTED = 25
SHARES = list(range(0, 65, 5))
CHECKS_AHEAD = 160  # about six years of biweekly checks
CHECKS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def _money(v: float) -> float:
    return round(float(v), 2)


def _round_up(v: float, step: float = 10.0) -> float:
    return float(math.ceil(max(0.0, v) / step) * step)


def settings_of(session: Session) -> dict:
    cfg = get_setting(session, SETTING, {}) or {}
    share = cfg.get("share")
    return {"share": int(share) if share is not None else None, "starter": float(cfg.get("starter", 500.0)), "rollover": bool(cfg.get("rollover", True))}


def save_settings(session: Session, *, share: int | None = None, starter: float | None = None, rollover: bool | None = None, clear_share: bool = False) -> dict:
    cfg = get_setting(session, SETTING, {}) or {}
    if clear_share:
        cfg.pop("share", None)
    elif share is not None:
        cfg["share"] = max(0, min(80, int(share)))
    if starter is not None:
        cfg["starter"] = max(0.0, float(starter))
    if rollover is not None:
        cfg["rollover"] = bool(rollover)
    set_setting(session, SETTING, cfg)
    return settings_of(session)


def _last_payment(p: InstallmentPlan) -> date | None:
    try:
        rows = sorted(json.loads(getattr(p, "schedule", "") or "[]"), key=lambda r: r.get("date", ""))
    except ValueError:
        rows = []
    if rows and rows[-1].get("date"):
        return date.fromisoformat(rows[-1]["date"])
    if p.remaining <= 0:
        return None
    return p.next_date + timedelta(days=p.cadence_days * (p.remaining - 1))


def saving_plan(session: Session, today: date, *, snap: dict | None = None) -> dict:
    from .snapshot import finance_snapshot

    if snap is None:
        snap = finance_snapshot(session, today)
    cfg = settings_of(session)
    accounts = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    saves = [a for a in accounts if a.kind in CASH_KINDS and not is_spendable(a)]
    balance = sum(float(a.balance or 0.0) for a in saves)
    windows = snap["windows"]
    pace_day = float(snap["usual_pace"]["per_day_cash"])
    cadence = snap["next_paycheck"].get("cadence") or "biweekly"

    # The rungs, from the owner's own figures.
    first_two = windows[:2]
    must = max((w["bills"]["total"] + w["advances"]["total"]) for w in first_two) if first_two else 0.0
    span = (date.fromisoformat(first_two[-1]["end"]) - date.fromisoformat(first_two[0]["start"])).days + 1 if first_two else 30
    obligations_month = sum(w["bills"]["total"] + w["advances"]["total"] for w in first_two) * 30.0 / max(span, 1)
    month = obligations_month + pace_day * 30.0
    rung_defs = [
        (1, "A starter", cfg["starter"], "A first cushion, so a surprise is paid from savings instead of an advance."),
        (2, "One pay period of must-pays", _round_up(must), "The bills, card payments and advances of the heavier of your next two checks."),
        (3, "One month", _round_up(month), "A month of the bills and your usual everyday spending."),
        (4, "Three months", _round_up(month * 3), "Three months of the same: a job change or a lost check without borrowing."),
        (5, "Six months", _round_up(month * 6), "Six months: the cushion most guides call complete."),
    ]

    # What each check truly leaves: the window's own figure less the everyday pace over its days.
    def left_of(w: dict) -> float:
        days = (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1
        return w["safe"] - pace_day * days

    lefts = [left_of(w) for w in windows]
    share = cfg["share"]
    used = share if share is not None else SUGGESTED
    per_check = [{"date": w["start"], "left": _money(lefts[k]), "keep": _money(max(0.0, lefts[k]) * used / 100.0)} for k, w in enumerate(windows)]
    asides = [{"start": w["start"], "set_aside": w["set_aside"]["total"]} for w in windows]
    pay = snap.get("payday") or {}
    if pay.get("landed"):
        # Today's check has posted, so the windows start at the next one; what today's check keeps and leaves are
        # the snapshot's own figures for it, and the payday moves still have them.
        left = float(pay["left"]) - pace_day * ((date.fromisoformat(pay["end"]) - today).days + 1)
        per_check.insert(0, {"date": today.isoformat(), "left": _money(left), "keep": _money(max(0.0, left) * used / 100.0)})
        asides.insert(0, {"start": today.isoformat(), "set_aside": pay["set_aside"]})

    # The rollover: a plan's payment, freed when it ends, keeps going into the ladder.
    check_days = 365.0 / CHECKS_PER_YEAR.get(cadence, 26)
    plans = session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all()  # noqa: E712
    roll = []
    for p in plans:
        last = _last_payment(p)
        if last is None or p.installment <= 0:
            continue
        per_check_freed = p.installment * check_days / max(p.cadence_days or 30, 1)
        roll.append({"id": p.id, "name": f"{p.provider} · {p.merchant}".strip(" ·") if p.merchant and p.merchant != p.provider else p.provider, "installment": _money(p.installment),
                     "per_check": _money(per_check_freed), "last_payment": last.isoformat(), "source": f"plan:{p.id}"})
    roll.sort(key=lambda r: r["last_payment"])

    p1 = date.fromisoformat(snap["next_paycheck"]["date"])
    days = {k: snap["next_paycheck"][k] for k in ("pay_days", "pay_day") if k in snap["next_paycheck"]}
    checks = next_paydays(p1, cadence, CHECKS_AHEAD, **days) if snap["next_paycheck"].get("known", True) else []

    def simulate(pct: int, with_rollover: bool) -> list[str | None]:
        """The date each rung is reached, keeping `pct` percent of what each check leaves (the first check
        as projected, then the next two alternating), plus the freed payments once their plans end."""
        if not checks:
            return [None] * len(rung_defs)
        targets = [t for _, _, t, _ in rung_defs]
        etas: list[str | None] = [today.isoformat() if balance >= t else None for t in targets]
        bal = balance
        steady = lefts[1:3] if len(lefts) >= 3 else lefts[:1]
        for i, d in enumerate(checks):
            left = lefts[0] if i == 0 else steady[(i - 1) % len(steady)]
            freed = sum(r["per_check"] for r in roll if date.fromisoformat(r["last_payment"]) < d)
            bal += max(0.0, left) * pct / 100.0 + freed * (1.0 if with_rollover else pct / 100.0)
            for k, t in enumerate(targets):
                if etas[k] is None and bal >= t:
                    etas[k] = d.isoformat()
            if all(e is not None for e in etas):
                break
        return etas

    etas = simulate(used, cfg["rollover"])
    etas_plain = simulate(used, False)
    rungs = []
    for (n, name, target, why), eta, eta_plain in zip(rung_defs, etas, etas_plain):
        reached = balance >= target
        rungs.append({"n": n, "name": name, "target": _money(target), "why": why, "reached": reached, "short": _money(max(0.0, target - balance)),
                      "progress": round(min(1.0, balance / target), 3) if target else 1.0, "eta": eta, "eta_without_rollover": eta_plain})
    next_rung = next((r for r in rungs if not r["reached"]), None)
    by_share = []
    for pct in SHARES:
        e = simulate(pct, cfg["rollover"])
        avg = sum(max(0.0, x) for x in lefts[1:3] or lefts[:1]) / max(len(lefts[1:3] or lefts[:1]), 1) * pct / 100.0
        by_share.append({"share": pct, "per_check": _money(avg), "etas": e})
    return {
        "as_of": today.isoformat(), "balance": _money(balance), "accounts": [{"id": a.id, "name": a.name, "balance": a.balance, "source": f"account:{a.id}"} for a in saves],
        "share": share, "chosen": share is not None, "suggested": SUGGESTED, "share_used": used, "presets": PRESETS, "starter": cfg["starter"],
        "per_check": per_check, "windows": asides,
        "rungs": rungs, "next_rung": next_rung, "by_share": by_share, "month": _money(month), "must_pays": _money(must),
        "rollover": {"enabled": cfg["rollover"], "items": roll, "freed_per_check": _money(sum(r["per_check"] for r in roll))},
        "basis": "Rungs from the next two checks' bills and advances and the usual everyday pace; each check's share from what its window leaves after the pace.",
        "not_advice": NOT_ADVICE,
    }
