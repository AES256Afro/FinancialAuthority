"""accounts.cover: for every account, what leaves it before each of the next two paydays, what it holds and the
difference, with the rows behind each figure; and the payday sheet, the moves that would cover it, in date order.
The app never moves money. The sheet says what the owner would move; the owner does the moving and ticks it off.

Checking (and any spendable account) pays its cash bills, the plans and advances drawn from it, the card
payments and the everyday pace; the paycheck lands in the account it is deposited to. A card holds what is owed,
what rides it until the statement, and the line, so the statement day's utilisation is known in advance. A
savings account is measured against the next rung of the saving ladder."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..models import Debt, Income
from ..store import get_setting
from .projection import is_spendable, next_due
NOT_ADVICE = "Computed by the engine from your registers and the ledger. Not financial advice."

UTILISATION_TARGET = 0.30
SHEET_KEY = "cover.ticks"


def _money(v: float) -> float:
    return round(float(v), 2)


def _usd(v: float) -> str:
    return f"${abs(float(v)):,.2f}"


def _short(d: date) -> str:
    return f"{d:%b} {d.day}"


def _deposit_account(session: Session, accounts: dict, spendables: list) -> object | None:
    """The account the paycheck lands in: the income's own account when it is a spending account, otherwise the
    spending account holding the most."""
    income = session.exec(select(Income).where(Income.active == True).order_by(Income.next_date)).first()  # noqa: E712
    if income and income.account_id in accounts and is_spendable(accounts[income.account_id]):
        return accounts[income.account_id]
    return max(spendables, key=lambda a: a.balance or 0.0) if spendables else None


def _payer_id(e: dict, accounts: dict, default_id: int | None) -> int | None:
    """Which spending account an event leaves. A bill names its account; everything else leaves the default."""
    acc = accounts.get(e.get("from_account_id")) if e.get("from_account_id") else None
    if acc is not None and acc.kind != "card" and is_spendable(acc):
        return acc.id
    return default_id


def _walk(start_balance: float, events: list[dict], pace: float, start: date, end: date, inflow: tuple[date, float] | None = None, pace_from_start: bool = False) -> tuple[float, date]:
    """The lowest the balance goes between start and end (inclusive), and the day it gets there: the balance at the
    close of each day, after that day's inflow, bills and pace. The pace is spent on every day after the start, and
    on the start day too when `pace_from_start` (the window after payday spends on the payday itself, as its
    `everyday` figure counts it)."""
    by_day: dict[date, float] = {}
    for e in events:
        by_day[e["date"]] = by_day.get(e["date"], 0.0) + e["amount"]
    balance = start_balance
    low: float | None = None  # the balance before the start day's inflow is not a day in the window
    low_day = start
    d = start
    while d <= end:
        if inflow and d == inflow[0]:
            balance += inflow[1]
        balance -= by_day.get(d, 0.0) + (pace if d > start or pace_from_start else 0.0)
        if low is None or balance < low:
            low, low_day = balance, d
        d += timedelta(days=1)
    return (start_balance if low is None else low), low_day


def account_cover(session: Session, today: date, *, saving: dict | None = None) -> dict:
    from .scenario import assembled, load_inputs

    inp = load_inputs(session, today, horizon_days=60)
    accounts = inp["accounts"]
    plan = assembled(inp, 60)
    math, card_bills, payoffs = plan["math"], plan["card_bills"], plan["payoffs"]
    paydays = inp["paydays"]
    p1 = paydays[0] if paydays else today + timedelta(days=14)
    p2 = paydays[1] if len(paydays) > 1 else p1 + timedelta(days=14)
    buffer = inp["buffer"]
    pace_by = inp["pace"]["per_day_by_account"]
    spendables = [a for a in accounts.values() if is_spendable(a) and a.kind != "card"]
    deposit = _deposit_account(session, accounts, spendables)
    default_id = deposit.id if deposit else None
    debts = session.exec(select(Debt).where(Debt.active == True)).all()  # noqa: E712
    ticks = get_setting(session, SHEET_KEY, {}) or {}
    if saving is None:
        from .saving import saving_plan

        saving = saving_plan(session, today)

    windows = [(today, p1 - timedelta(days=1)), (p1, p2 - timedelta(days=1))]
    out_accounts: list[dict] = []
    sheet: list[dict] = []

    for a in sorted(accounts.values(), key=lambda x: (0 if is_spendable(x) and x.kind != "card" else 1 if x.kind == "card" else 2, -(x.balance or 0.0))):
        base = {"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "balance": a.balance, "source": f"account:{a.id}"}
        if is_spendable(a) and a.kind != "card":
            mine = [e for e in math if e.get("from_kind") != "card" and _payer_id(e, accounts, default_id) == a.id]
            pace = max(0.0, float(pace_by.get(a.id, 0.0)))
            has = float(a.balance or 0.0)
            keep = buffer if a.id == default_id else 0.0
            out_w = []
            running = has
            for k, (start, end) in enumerate(windows):
                items = [e for e in mine if start <= e["date"] <= end]
                inflow = (p1, float(inp["net_for"](p1))) if (k == 1 and a.id == default_id and inp["has_income"]) else None
                days = max(0, (end - start).days + (1 if k == 1 else 0))
                spend = pace * days
                needs = sum(e["amount"] for e in items) + spend + keep
                start_balance = running
                low, low_day = _walk(start_balance - keep, items, pace, start, end, inflow, pace_from_start=k == 1)
                left = start_balance + (inflow[1] if inflow else 0.0) - needs
                out_w.append({
                    "start": start.isoformat(), "end": end.isoformat(), "has": _money(start_balance), "inflow": _money(inflow[1]) if inflow else 0.0, "has_total": _money(start_balance + (inflow[1] if inflow else 0.0)), "needs": _money(needs),
                    "bills": _money(sum(e["amount"] for e in items)), "everyday": _money(spend), "buffer": _money(keep), "left": _money(left),
                    "low": _money(low), "low_on": low_day.isoformat(),
                    "items": [{"name": e["name"], "amount": e["amount"], "date": e["date"].isoformat(), "kind": e["kind"], "source": e.get("source", "")} for e in items],
                })
                running = left + keep  # the buffer is still in the account; the next window keeps it again
            w1, w2 = out_w
            if a.balance is None:
                # No bank has reported this account's balance and none was typed: what it holds is unknown, not $0, so
                # no shortfall is worked out from it and nothing is put on the sheet.
                for w in out_w:
                    w.update(has=None, has_total=None, left=None, low=None, low_on=None)
                out_accounts.append({**base, "role": "spend", "status": "warn", "headline": "No balance yet: link the bank or type what it holds", "no_balance": True, "deposit": a.id == default_id, "pace_per_day": _money(pace), "windows": out_w})
                continue
            if w1["left"] < 0:
                status, headline = "crit", f"Short {_usd(w1['left'])} before {_short(p1)}; the balance runs lowest on {_short(date.fromisoformat(w1['low_on']))}"
            elif w2["left"] < 0:
                status, headline = "warn", f"Covered until {_short(p1)}; the {_short(p1)} check leaves it short {_usd(w2['left'])} before {_short(p2)}"
            else:
                status, headline = "good", f"Covered through {_short(p2 - timedelta(days=1))}; {_usd(w1['left'])} over before {_short(p1)}"
            out_accounts.append({**base, "role": "spend", "status": status, "headline": headline, "deposit": a.id == default_id, "pace_per_day": _money(pace), "windows": out_w})
            if w1["left"] < 0:
                sheet.append({"kind": "short", "date": w1["low_on"], "action": f"{a.name} runs short before payday", "amount": _money(-w1["left"]), "from": None, "to": a.name,
                              "so_that": "the bills before payday clear; the Borrowing and purchase pages show the ways to close it without a new advance", "source": f"account:{a.id}"})
        elif a.kind == "card":
            debt = next((d for d in debts if d.account_id == a.id), None) or next((d for d in debts if (d.name or "").strip().lower() == (a.name or "").strip().lower()), None)
            limit = a.credit_limit or (debt.credit_limit if debt else None)
            balance = float(a.balance or 0.0)
            statement = next_due(int(debt.statement_day), today) if debt and debt.statement_day else None
            mine_pay = [e for e in payoffs if e.get("source") == f"account:{a.id}" or (debt is not None and e.get("source") == f"debt:{debt.id}")]
            by_bill = plan["paid_by_bill"].get(a.id, set())  # a card paid by a bill on the register: that bill is its payment
            mine_pay = sorted(mine_pay + [e for e in math if e.get("bill_id") in by_bill], key=lambda e: e["date"])
            riding = [e for e in card_bills if e.get("from_account_id") == a.id]
            pace = float(pace_by.get(a.id, 0.0))
            planned_off = float(inp["planned"].get(a.id, 0.0))
            proj_statement = None
            if statement:
                charges = sum(e["amount"] for e in riding if today <= e["date"] < statement) + pace * (statement - today).days
                paid_before = sum(e["amount"] for e in mine_pay if e["date"] < statement)
                proj_statement = max(0.0, balance - planned_off + charges - paid_before)
            util_now = balance / limit if limit else None
            util_statement = proj_statement / limit if (limit and proj_statement is not None) else None
            to_target = max(0.0, (proj_statement or 0.0) - UTILISATION_TARGET * limit) if (limit and proj_statement is not None) else 0.0
            pay_by = statement - timedelta(days=2) if statement else None
            if limit and balance > limit:
                status = "crit"
            elif util_statement is not None and util_statement > UTILISATION_TARGET:
                status = "warn"
            else:
                status = "good"
            if status != "good" and pay_by and to_target > 0:
                headline = f"Pay {_usd(to_target)} by {_short(pay_by)} and the {_short(statement)} statement reports under {int(UTILISATION_TARGET * 100)}% used"
            elif mine_pay:
                headline = f"Next payment {_usd(mine_pay[0]['amount'])} on {_short(mine_pay[0]['date'])}" + (" (the minimum)" if mine_pay[0]["name"].endswith("minimum") else "")
            else:
                headline = "Nothing owed" if balance <= 0 else f"{_usd(balance)} owed"
            out_accounts.append({**base, "role": "card", "status": status, "headline": headline, "limit": _money(limit) if limit else None, "target_amount": _money(UTILISATION_TARGET * limit) if limit else None, "utilisation": round(util_now, 3) if util_now is not None else None,
                                 "statement_on": statement.isoformat() if statement else None, "statement_balance": _money(proj_statement) if proj_statement is not None else None,
                                 "statement_utilisation": round(util_statement, 3) if util_statement is not None else None, "to_target": _money(to_target), "pay_by": pay_by.isoformat() if pay_by else None,
                                 "planned": _money(planned_off), "riding": [{"name": e["name"], "amount": e["amount"], "date": e["date"].isoformat(), "source": e.get("source", "")} for e in riding if e["date"] < p2],
                                 "payments": [{"date": e["date"].isoformat(), "amount": e["amount"], "parts": e.get("parts", {}), "source": e.get("source", "")} for e in mine_pay if e["date"] < p2 + timedelta(days=14)],
                                 "per_window": [_money(sum(e["amount"] for e in mine_pay if e["date"] < p1)), _money(sum(e["amount"] for e in mine_pay if p1 <= e["date"] < p2))],
                                 "debt_id": debt.id if debt else None})
            payer = accounts.get(default_id).name if default_id in accounts else "checking"
            early_due = status != "good" and pay_by is not None and pay_by >= today and to_target > 0
            # The statement shows the balance after the payments that land before it. What it still needs to be
            # under the target goes early; that money then comes off the first payment after the statement.
            after = next((e for e in mine_pay if statement and e["date"] >= statement), None) if early_due else None
            covered = after is not None and to_target >= after["amount"] - 0.5
            if early_due:
                tail = (f"; it also covers the {_usd(after['amount'])} due {_short(after['date'])}" if covered else f"; it comes off the {_short(after['date'])} payment") if after else ""
                sheet.append({"kind": "pay", "date": pay_by.isoformat(), "action": f"Pay {a.name} from {payer}", "amount": _money(to_target), "from": payer, "to": a.name,
                              "so_that": f"the statement on {_short(statement)} reports under {int(UTILISATION_TARGET * 100)}% used{tail}", "source": f"account:{a.id}:early"})
            for e in mine_pay:
                if e["date"] > p2:
                    continue
                if after is not None and e is after:
                    if covered:
                        continue
                    sheet.append({"kind": "pay", "date": e["date"].isoformat(), "action": f"Pay {a.name} from {payer}", "amount": _money(e["amount"] - to_target), "from": payer, "to": a.name,
                                  "so_that": f"the rest of the card payment, {_usd(e['amount'])} in all", "source": e.get("source", "")})
                    continue
                sheet.append({"kind": "pay", "date": e["date"].isoformat(), "action": f"Pay {a.name} from {payer}", "amount": e["amount"], "from": payer, "to": a.name,
                              "so_that": "the card is paid as the plan counts it", "source": e.get("source", "")})
        elif a.kind in ("savings",) or (a.kind == "cash" and not is_spendable(a)):
            rung = saving.get("next_rung") if saving else None
            target = rung["target"] if rung else None
            has = float(a.balance or 0.0)
            share_total = saving.get("balance", has) if saving else has
            short = max(0.0, (target or 0.0) - share_total) if target else 0.0
            status = "good" if not target or short <= 0 else ("warn" if share_total > 0 else "crit")
            share_word = "your share" if (saving or {}).get("chosen") else "the suggested share"
            headline = f"Rung {rung['n']} reached" if rung and short <= 0 else (f"{_usd(short)} short of rung {rung['n']} ({rung['name'].lower()})" + (f"; {_short(date.fromisoformat(rung['eta']))} at {share_word}" if rung.get("eta") else "") if rung else "Kept, not spent")
            out_accounts.append({**base, "role": "save", "status": status, "headline": headline, "rung": rung, "short": _money(short)})
        else:
            out_accounts.append({**base, "role": "other", "status": "good", "headline": "Not counted in the paycheck plan"})

    # Payday moves: the set-aside stays in checking for the window after; the saving share moves out. They are
    # dated on the scheduled payday: today's, when today is payday, even once its check has posted.
    if deposit is not None and inp["has_income"]:
        pay_on = today if inp["payday_today"] else p1
        after = p1 if pay_on < p1 else p2
        snap_windows = (saving or {}).get("per_check") or []
        first = next((w for w in snap_windows if w.get("date") == pay_on.isoformat()), None)
        save_to = next((a for a in out_accounts if a["role"] == "save"), None)
        if first and first.get("keep", 0) > 0 and save_to:
            sheet.append({"kind": "move", "date": pay_on.isoformat(), "action": f"Move to {save_to['name']}", "amount": first["keep"], "from": deposit.name, "to": save_to["name"],
                          "so_that": f"{(saving or {}).get('share_used')}% of what the {_short(pay_on)} check leaves goes to the ladder" + ("" if (saving or {}).get("chosen") else " (the suggested share; choose yours on Saving)"), "source": "saving:share"})
        aside = next((w for w in (saving or {}).get("windows", []) if w.get("start") == pay_on.isoformat()), None)
        if aside and aside.get("set_aside", 0) > 0:
            sheet.append({"kind": "keep", "date": pay_on.isoformat(), "action": f"Leave in {deposit.name}", "amount": aside["set_aside"], "from": None, "to": deposit.name,
                          "so_that": f"the {_short(after)} window's bills are already there", "source": "snapshot:set_aside"})
    for row in sheet:
        row["id"] = f"{row['kind']}:{row['source']}:{row['date']}"
        row["done"] = row["id"] in ticks
    sheet.sort(key=lambda r: (r["date"], {"short": 0, "pay": 1, "keep": 2, "move": 3}.get(r["kind"], 9)))
    counts = {s: len([x for x in out_accounts if x["status"] == s]) for s in ("good", "warn", "crit")}
    return {"as_of": today.isoformat(), "paydays": [p1.isoformat(), p2.isoformat()], "buffer": _money(buffer), "deposit_account_id": default_id, "accounts": out_accounts, "sheet": sheet, "counts": counts, "not_advice": NOT_ADVICE}


def tick(session: Session, row_id: str, done: bool, today: date) -> dict:
    from ..store import set_setting

    ticks = get_setting(session, SHEET_KEY, {}) or {}
    if done:
        ticks[row_id] = today.isoformat()
    else:
        ticks.pop(row_id, None)
    # Ticks older than two months are forgotten; their rows are long gone.
    ticks = {k: v for k, v in ticks.items() if v >= (today - timedelta(days=62)).isoformat()}
    set_setting(session, SHEET_KEY, ticks)
    return {"id": row_id, "done": done}
