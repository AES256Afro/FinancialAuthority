"""Switching banks: moving the paycheck, the bills, the card payments and the subscriptions from one account to
another over a few weeks, so that nothing is charged to an account with no money in it and nothing is forgotten.

Everything comes from the registers and the ledger; nothing outside the app is needed. The items are found, not
typed: the paycheck that lands in the old account, every bill it pays, the cards it pays, the pay-later plans on
it, the advance apps that draw on it, and the recurring charges and transfers the ledger sees on it but the
registers do not name. Each item gets a window to switch in: after its next charge has gone through on the old
account and a few days before the one after, and never before the new account has money in it. The owner
switches at the payer and taps it done; the ledger confirms it when the charge or the deposit lands on the new
account, and says so when one still lands on the old. Through the move, each pay window shows what each account
needs and when to move money between them. The app never moves money; it says what you would move."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import Account, AccountMove, Advance, Bill, Debt, Income, InstallmentPlan, MoveItem, RecurringSeries, Transaction
from .borrowing import ADVANCE_PROVIDERS, INSTALLMENT_PROVIDERS, provider_of
from .projection import is_spendable, next_due
from .recurring import merchant_key

NOT_ADVICE = "Computed by the engine from your registers and the ledger. The app never moves money. Not financial advice."
LEAD_DAYS = 5  # a payer usually needs a few days before the next charge to use a new account
PAYROLL_DAYS = 10  # payroll usually needs a pay period's notice
QUIET_DAYS = 45  # the old account can close once nothing that moved has landed on it for this long
LOOKBACK = 120
TRANSFER_DAYS = 3  # money sent between two banks usually arrives in one to three business days
EXTRA_ADVANCE = {"albert": "Albert"}
# Wallets and payment apps draw on the bank account they are linked to; one switch at the wallet moves all of it.
WALLETS = {"PayPal": ("paypal", "pwp"), "Venmo": ("venmo",), "Cash App": ("cash app", "square cash"), "Zelle": ("zelle",)}
CASH = re.compile(r"(^|\s)(atm|cash withdrawal)(\s|$)")
PHASES = [
    ("ready", "Get ready", "The new account in the app and money in it before anything moves."),
    ("paycheck", "The paycheck", "It moves first: everything after waits for the first paycheck to land in the new account."),
    ("bills", "Bills and card payments", "Each in its own window, right after its next charge has gone through on the old account."),
    ("subscriptions", "Subscriptions on the debit card", "Once the new debit card is in hand."),
    ("wallets", "Wallets and payment apps", "They draw on the old account: add the new one, make it the default, and remove the old one at the close."),
    ("found", "Found in the ledger", "Recurring charges and transfers on the old account that no register names: decide each."),
    ("advances", "Advance apps", "The move is the chance to leave them on the old account and let them end."),
    ("close", "Close out", "When everything that moved has stayed moved for a while."),
]


def _usd(v: float) -> str:
    return f"${abs(float(v)):,.2f}"


def _short(d: date | None) -> str:
    return f"{d:%a %b} {d.day}" if d else "-"


def _money(v: float) -> float:
    return round(float(v), 2)


def _accounts(session: Session) -> dict[int, Account]:
    return {a.id: a for a in session.exec(select(Account).where(Account.active == True)).all()}  # noqa: E712


def _default_payer(session: Session, accounts: dict[int, Account]) -> int | None:
    from .cover import _deposit_account

    spend = [a for a in accounts.values() if is_spendable(a) and a.kind != "card"]
    dep = _deposit_account(session, accounts, spend)
    return dep.id if dep else None


def _wallet(key: str) -> str | None:
    key = (key or "").lower()
    return next((name for name, words in WALLETS.items() if any(re.search(rf"(^|\s){re.escape(w)}(\s|$)", key) for w in words)), None)


def _advance_name(text: str) -> str | None:
    name = provider_of(text, ADVANCE_PROVIDERS)
    if name:
        return name
    low = (text or "").lower()
    return next((v for k, v in EXTRA_ADVANCE.items() if low.startswith(k)), None)


# ---- finding the items ------------------------------------------------------------------------------------------

def find_items(session: Session, move: AccountMove, today: date) -> list[dict[str, Any]]:
    """Everything that touches the old account, as items to move, keep or drop."""
    src = move.source_account_id
    accounts = _accounts(session)
    source = accounts.get(src)
    default_id = _default_payer(session, accounts)
    out: list[dict[str, Any]] = []
    covered: set[str] = set()

    for inc in session.exec(select(Income).where(Income.active == True)).all():  # noqa: E712
        if inc.account_id == src or (inc.account_id is None and default_id == src):
            out.append({"ref": f"income:{inc.id}", "kind": "income", "name": inc.name, "amount": float(inc.expected_net), "due_day": None, "match_key": "", "decision": "move"})

    series_keys = {s.id: s.merchant_key for s in session.exec(select(RecurringSeries)).all()}
    for b in session.exec(select(Bill).where(Bill.active == True)).all():  # noqa: E712
        payer = b.pay_from_account_id or default_id
        if payer != src:
            continue
        key = series_keys.get(b.series_id) if b.series_id else merchant_key(b.name)
        covered.add(key or "")
        out.append({"ref": f"bill:{b.id}", "kind": "subscription" if b.kind == "subscription" else "bill", "name": b.name, "amount": float(b.amount), "due_day": b.due_day, "match_key": key or "", "decision": "move"})

    if source is not None and source.kind != "card":
        debts = {d.account_id: d for d in session.exec(select(Debt).where(Debt.active == True)).all() if d.account_id}  # noqa: E712
        for a in accounts.values():
            if a.kind != "card" or a.id == src:
                continue
            d = debts.get(a.id)
            key = (a.institution or a.name or "").lower().strip()
            covered.add(key)
            out.append({"ref": f"card:{a.id}", "kind": "card_payment", "name": f"{a.name} payments", "amount": float(a.balance or 0.0),
                        "due_day": d.due_day if d and d.due_day else None, "match_key": key, "decision": "move"})

    for p in session.exec(select(InstallmentPlan).where(InstallmentPlan.active == True)).all():  # noqa: E712
        if getattr(p, "pay_from_account_id", None) == src:
            out.append({"ref": f"plan:{p.id}", "kind": "plan", "name": f"{p.provider} · {p.merchant}" if p.merchant and p.merchant != p.provider else p.provider,
                        "amount": float(p.installment), "due_day": p.next_date.day, "match_key": p.provider.lower(), "decision": "move"})

    since = today - timedelta(days=LOOKBACK)
    rows = session.exec(select(Transaction).where(Transaction.account_id == src, Transaction.date >= since, Transaction.is_ignored == False)).all()  # noqa: E712
    advances: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        name = _advance_name(t.merchant) or (_advance_name(t.description) if t.category == "Borrowing" else None)
        if name:
            advances[name].append(t)
    # The advances register names apps the built-in list may not know; one still out repays from the paycheck's account.
    owed: dict[str, float] = {}
    for a in session.exec(select(Advance)).all():
        key = (a.provider or "").lower().strip()
        if not key:
            continue
        hits = [t for t in rows if key in merchant_key(t.merchant)]
        if hits and not _advance_name(a.provider):
            advances.setdefault(a.provider, []).extend(hits)
        if not a.repaid and default_id == src:
            owed[a.provider] = owed.get(a.provider, 0.0) + float(a.amount + a.fee)
    for name in owed:
        advances.setdefault(_advance_name(name) or name, [])
    for name, ts in advances.items():
        covered.add(name.lower())
        biggest = max([abs(t.amount) for t in ts] + [owed.get(name, 0.0)])
        out.append({"ref": f"advance:{name.lower()}", "kind": "advance", "name": name, "amount": _money(biggest), "due_day": None,
                    "match_key": name.lower(), "decision": "drop", "note": "Suggested: leave it on the old account and let it end."})

    wallets: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        borrowed = t.category == "Borrowing" or _advance_name(t.merchant)
        w = _wallet(merchant_key(t.merchant)) if t.amount > 0 and not t.is_card_payment and not borrowed else None
        if w:
            wallets[w].append(t)
    for name, ts in wallets.items():
        if len(ts) < 3:
            continue
        covered.add(name.lower())
        recent = sum(t.amount for t in ts if t.date >= today - timedelta(days=30))
        out.append({"ref": f"wallet:{name.lower()}", "kind": "wallet", "name": name, "amount": _money(recent or sorted(t.amount for t in ts)[len(ts) // 2]), "due_day": None,
                    "match_key": "|".join(WALLETS[name]), "decision": "move", "note": f"{len(ts)} charges and transfers from it in the last {LOOKBACK} days."})

    linked = {b.series_id for b in session.exec(select(Bill)).all() if b.series_id}
    from ..jobs.detect import account_for_series

    for s in session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).all():  # noqa: E712
        if s.id in linked or s.kind in ("ignored", "income", "advance", "installment") or s.merchant_key in covered:
            continue
        if provider_of(s.merchant, INSTALLMENT_PROVIDERS) or _advance_name(s.merchant) or _wallet(s.merchant_key) or CASH.search(s.merchant_key):
            continue
        if account_for_series(session, s) != src:
            continue
        covered.add(s.merchant_key)
        out.append({"ref": f"found:{s.merchant_key}", "kind": "found", "name": s.merchant, "amount": float(s.typical_amount), "due_day": s.next_expected.day if s.next_expected else None,
                    "match_key": s.merchant_key, "decision": "undecided"})

    transfers = Counter(merchant_key(t.merchant) for t in rows if t.is_transfer and t.amount > 0 and not t.is_card_payment)
    names = {merchant_key(t.merchant): t.merchant for t in rows}
    for key, n in transfers.items():
        # Cash from an ATM follows the debit card, and a wallet is one item above, so neither is a transfer to switch.
        if n >= 3 and key and key not in covered and not _wallet(key) and not CASH.search(key):
            covered.add(key)
            amounts = [t.amount for t in rows if merchant_key(t.merchant) == key and t.is_transfer and t.amount > 0]
            out.append({"ref": f"transfer:{key}", "kind": "transfer", "name": names.get(key, key), "amount": _money(sorted(amounts)[len(amounts) // 2]), "due_day": None,
                        "match_key": key, "decision": "undecided"})
    return out


def refresh_items(session: Session, move: AccountMove, today: date) -> int:
    """Add what is new on the old account and drop what is no longer found and untouched; never undo a decision
    the owner made."""
    existing = session.exec(select(MoveItem).where(MoveItem.move_id == move.id)).all()
    have = {i.ref for i in existing}
    found = find_items(session, move, today)
    refs = {f["ref"] for f in found}
    for i in existing:
        if i.ref not in refs and i.status == "todo" and i.decision == "undecided" and not i.note:
            session.delete(i)
    added = 0
    for f in found:
        if f["ref"] in have:
            continue
        session.add(MoveItem(move_id=move.id, ref=f["ref"], kind=f["kind"], name=f["name"][:120], amount=_money(f["amount"]), due_day=f["due_day"], match_key=f["match_key"],
                             decision=f["decision"], note=f.get("note", "")))
        added += 1
    session.commit()
    return added


def start_move(session: Session, today: date, *, source_id: int, target_id: int | None, target_name: str, finish_by: date | None, name: str | None = None) -> AccountMove:
    accounts = _accounts(session)
    if source_id not in accounts:
        raise ValueError("The account to move from is not in the app")
    if target_id is not None and (target_id not in accounts or target_id == source_id):
        raise ValueError("The account to move to must be another account in the app")
    if target_id is None and not target_name.strip():
        raise ValueError("Name the new account, or add it to the app first")
    default_id = _default_payer(session, accounts)
    # Make the old account's role explicit, so moving the paycheck does not silently move the bills with it.
    if default_id == source_id:
        for b in session.exec(select(Bill).where(Bill.active == True, Bill.pay_from_account_id == None)).all():  # noqa: E711, E712
            b.pay_from_account_id = source_id
            session.add(b)
        for inc in session.exec(select(Income).where(Income.active == True, Income.account_id == None)).all():  # noqa: E711, E712
            inc.account_id = source_id
            session.add(inc)
    tname = target_name.strip() or (accounts[target_id].name if target_id else "")
    move = AccountMove(name=(name or f"{accounts[source_id].name} to {tname}")[:80], source_account_id=source_id, target_account_id=target_id, target_name=tname[:80],
                       started_on=today, finish_by=finish_by)
    session.add(move)
    session.commit()
    session.refresh(move)
    refresh_items(session, move, today)
    return move


# ---- the registers follow a switch ------------------------------------------------------------------------------

def _point(session: Session, item: MoveItem, account_id: int | None) -> None:
    """Point the register behind an item at an account, so the plan and Cover count it there."""
    if account_id is None:
        return
    kind, _, rid = item.ref.partition(":")
    if kind == "bill" and rid.isdigit():
        b = session.get(Bill, int(rid))
        if b:
            b.pay_from_account_id = account_id
            session.add(b)
    elif kind == "income" and rid.isdigit():
        inc = session.get(Income, int(rid))
        if inc:
            inc.account_id = account_id
            session.add(inc)
    elif kind == "plan" and rid.isdigit():
        p = session.get(InstallmentPlan, int(rid))
        if p:
            p.pay_from_account_id = account_id
            session.add(p)


def set_item(session: Session, move: AccountMove, item: MoveItem, today: date, *, decision: str | None = None, status: str | None = None, note: str | None = None) -> MoveItem:
    if decision is not None:
        if decision not in ("move", "keep", "drop", "undecided"):
            raise ValueError("decision is move, keep, drop or undecided")
        item.decision = decision
        if decision != "move" and item.status != "todo":
            _point(session, item, move.source_account_id)
            item.status, item.switched_on, item.confirmed_on = "todo", None, None
    if status is not None:
        if status not in ("todo", "switched"):
            raise ValueError("status is todo or switched; the ledger confirms")
        if status == "switched":
            item.decision = "move"
            item.status, item.switched_on = "switched", today
            _point(session, item, move.target_account_id)
        else:
            item.status, item.switched_on, item.confirmed_on = "todo", None, None
            _point(session, item, move.source_account_id)
    if note is not None:
        item.note = note[:500]
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


# ---- the ledger confirms ----------------------------------------------------------------------------------------

def _matches(item: MoveItem, t: Transaction) -> bool:
    if item.kind == "income":
        return t.is_income and t.amount < 0
    if item.kind == "wallet":
        return t.amount != 0 and _wallet(merchant_key(t.merchant)) == item.name
    if item.kind == "card_payment":
        text = f"{t.merchant} {t.description}".lower()
        return t.amount > 0 and (t.is_card_payment or "payment" in text) and bool(item.match_key) and any(w in text for w in item.match_key.split() if len(w) > 3)
    key = item.match_key
    if not key:
        return False
    m = merchant_key(t.merchant)
    return t.amount != 0 and (m == key or (len(key) >= 4 and (key in m or m in key and len(m) >= 4)))


def verify(session: Session, move: AccountMove, today: date) -> dict[str, int]:
    """Read the ledger: when each item last landed on the old account and on the new, and confirm a switch when
    the new account sees it. Returns how many were confirmed now."""
    items = session.exec(select(MoveItem).where(MoveItem.move_id == move.id)).all()
    since = move.started_on - timedelta(days=LOOKBACK)
    ids = [move.source_account_id] + ([move.target_account_id] if move.target_account_id else [])
    rows = session.exec(select(Transaction).where(Transaction.account_id.in_(ids), Transaction.date >= since, Transaction.is_ignored == False)).all()  # noqa: E712
    confirmed = 0
    for item in items:
        old = [t.date for t in rows if t.account_id == move.source_account_id and _matches(item, t)]
        new = [t.date for t in rows if move.target_account_id and t.account_id == move.target_account_id and _matches(item, t) and t.date >= move.started_on]
        item.last_seen_old = max(old) if old else None
        item.last_seen_new = max(new) if new else None
        if item.decision == "move" and item.status != "confirmed" and item.last_seen_new:
            if item.status == "todo":
                item.switched_on = item.last_seen_new
                _point(session, item, move.target_account_id)
            item.status, item.confirmed_on = "confirmed", item.last_seen_new
            confirmed += 1
        session.add(item)
    session.commit()
    return {"confirmed": confirmed}


# ---- the plan ---------------------------------------------------------------------------------------------------

def _occurrences(due_day: int | None, start: date, end: date) -> list[date]:
    if not due_day:
        return []
    out, d = [], next_due(min(31, max(1, due_day)), start)
    while d <= end:
        out.append(d)
        d = next_due(min(31, max(1, due_day)), d + timedelta(days=1))
    return out


def move_plan(session: Session, move: AccountMove, today: date) -> dict[str, Any]:
    from .scenario import load_inputs
    from .projection import assemble

    accounts = _accounts(session)
    source = accounts.get(move.source_account_id)
    target = accounts.get(move.target_account_id) if move.target_account_id else None
    sname = source.name if source else "the old account"
    tname = target.name if target else (move.target_name or "the new account")
    items = list(session.exec(select(MoveItem).where(MoveItem.move_id == move.id)).all())
    inp = load_inputs(session, today, horizon_days=150)
    paydays = [d for d in inp["paydays"] if d >= today]
    horizon = today + timedelta(days=150)

    # When the paycheck lands in the new account: two pay periods' notice from today, or from the day it was switched.
    income_items = [i for i in items if i.kind == "income" and i.decision == "move"]
    if income_items and all(i.status == "confirmed" for i in income_items):
        first_new = min(i.confirmed_on for i in income_items if i.confirmed_on) if any(i.confirmed_on for i in income_items) else today
    else:
        asked = min((i.switched_on for i in income_items if i.switched_on), default=None)
        base = (asked or today) + timedelta(days=PAYROLL_DAYS)
        first_new = next((d for d in paydays if d >= base), base)
    card_ready = move.debit_card_on or (move.started_on + timedelta(days=10))
    paycheck_there = not income_items or all(i.status == "confirmed" for i in income_items)

    # Money through the move: each pay window, what each account brings in and pays out with the switches applied.
    events = assemble(bills=inp["bills"], plans=inp["plans"], advances=inp["advances"], today=today, accounts=inp["accounts"], paydays=inp["paydays"], card_mode=inp["card_mode"], debts=inp["debts"],
                      planned=inp["planned"], extra=inp["extra"], card_pace_by_account=inp["pace"]["per_day_by_account"], horizon_days=150)["math"]
    # Recurring charges the ledger found count on the old account too, until they move.
    for i in items:
        if i.kind == "found" and i.due_day:
            events += [{"date": d, "name": i.name, "amount": float(i.amount), "kind": "found", "source": i.ref, "from_account_id": move.source_account_id} for d in _occurrences(i.due_day, today, horizon)]
    default_id = _default_payer(session, accounts)
    debt_card = {f"debt:{d.id}": d.account_id for d in inp["debts"] if getattr(d, "account_id", None)}
    advance_rows = list(session.exec(select(Advance)).all())
    adv_provider = {f"advance:{a.id}": (_advance_name(a.provider) or a.provider).lower() for a in advance_rows}
    owed: dict[str, list[Advance]] = defaultdict(list)
    for a in advance_rows:
        if not a.repaid:
            owed[(_advance_name(a.provider) or a.provider).lower()].append(a)
    by_ref = {i.ref: i for i in items}

    def item_of(e: dict) -> MoveItem | None:
        src = e.get("source", "")
        if src.startswith(("bill:", "plan:", "found:")):
            return by_ref.get(src)
        if src.startswith("account:"):
            return by_ref.get(f"card:{src.split(':')[1]}")
        if src in debt_card:
            return by_ref.get(f"card:{debt_card[src]}")
        if src in adv_provider:
            return by_ref.get(f"advance:{adv_provider[src]}")
        return None

    def payer(e: dict) -> int | None:
        acc = accounts.get(e.get("from_account_id")) if e.get("from_account_id") else None
        if acc is not None and acc.kind != "card" and is_spendable(acc):
            return acc.id
        return default_id

    plan_items: list[dict[str, Any]] = []
    effective: dict[str, date | None] = {}
    for i in items:
        nxt = _occurrences(i.due_day, today, horizon)
        amount = float(i.amount)
        pays: list[dict] = []
        if i.kind == "card_payment":
            # A card is paid on the days the plan pays it, which can be well before its due date.
            pays = sorted((e for e in events if e["date"] >= today and (it := item_of(e)) is not None and it.ref == i.ref), key=lambda e: e["date"])
            nxt = sorted({e["date"] for e in pays}) or nxt
        ready = first_new if i.kind not in ("income", "advance") and not paycheck_there else today
        if i.kind in ("subscription", "found"):
            ready = max(ready, card_ready)
        switch_after = switch_by = eff = None
        out_now = owed.get(i.name.lower(), []) if i.kind == "advance" else []
        if i.kind == "income":
            switch_by = max(today, (paydays[0] if paydays else today) - timedelta(days=PAYROLL_DAYS))
            eff = first_new
        elif i.kind == "advance":
            # What is out is repaid from the old account first; the break comes after the last repayment.
            switch_after = max([today] + [a.repay_date for a in out_now])
            switch_by, eff = switch_after + timedelta(days=7), switch_after
        elif i.status in ("switched", "confirmed") and i.switched_on:
            eff = next((d for d in nxt if d >= i.switched_on + timedelta(days=LEAD_DAYS)), i.switched_on)
            switch_by = i.switched_on
        elif nxt:
            # The switch happens after the last charge on the old account and after the money is in the new one,
            # with a few days to spare before the first charge on the new one. A charge too close stays where it is.
            k = None
            for n, d in enumerate(nxt):
                lo = max(nxt[n - 1] if n > 0 else today, ready, today)
                if lo <= d - timedelta(days=LEAD_DAYS):
                    k = n
                    switch_after = lo
                    break
            if k is not None:
                eff = nxt[k]
                switch_by = eff - timedelta(days=LEAD_DAYS)
        else:
            # Nothing scheduled: any day in the pay period after the money is there, a few days before it is needed.
            switch_after = max(ready, today)
            switch_by = switch_after + timedelta(days=14)
            eff = switch_by + timedelta(days=LEAD_DAYS)
        if pays and eff:
            amount = next((float(e["amount"]) for e in pays if e["date"] >= eff), amount)
        effective[i.ref] = eff if i.decision in ("move", "drop") else None
        missed = i.status in ("switched", "confirmed") and i.switched_on is not None and i.last_seen_old is not None and i.last_seen_old > i.switched_on + timedelta(days=3)
        overdue = i.decision == "move" and i.status == "todo" and switch_by is not None and switch_by < today
        plan_items.append({
            "id": i.id, "ref": i.ref, "kind": i.kind, "name": i.name, "amount": _money(amount), "due_day": i.due_day, "decision": i.decision, "status": i.status,
            "switched_on": i.switched_on.isoformat() if i.switched_on else None, "confirmed_on": i.confirmed_on.isoformat() if i.confirmed_on else None,
            "last_seen_old": i.last_seen_old.isoformat() if i.last_seen_old else None, "last_seen_new": i.last_seen_new.isoformat() if i.last_seen_new else None,
            "next_due": nxt[0].isoformat() if nxt else None, "switch_after": switch_after.isoformat() if switch_after else None, "switch_by": switch_by.isoformat() if switch_by else None,
            "effective": eff.isoformat() if eff else None, "missed": missed, "overdue": overdue, "note": i.note,
            "instruction": _instruction(i, sname, tname, nxt[0] if nxt else None, switch_after, switch_by, eff, first_new, card_ready, move, owed=out_now),
            "owed": _money(sum(a.amount + a.fee for a in out_now)) if out_now else None,
            "phase": {"income": "paycheck", "bill": "bills", "card_payment": "bills", "plan": "bills", "subscription": "subscriptions", "wallet": "wallets", "found": "found", "transfer": "found",
                      "advance": "advances"}.get(i.kind, "found"),
        })

    pace_src = float(inp["pace"]["per_day_by_account"].get(move.source_account_id, 0.0))
    windows = []
    edges = [today] + [d for d in paydays if d > today][:5]  # a payday today opens the first window rather than an empty one
    bal_s = float(source.balance or 0.0) if source else 0.0
    bal_t = float(target.balance or 0.0) if target else 0.0
    buffer = float(inp["buffer"])
    for k in range(len(edges) - 1):
        start, end = edges[k], edges[k + 1]
        flows = {"source": {"in": 0.0, "out": 0.0}, "target": {"in": 0.0, "out": 0.0}}
        lines: dict[str, list[dict]] = {"source": [], "target": []}
        lands = inp["has_income"] and start in paydays
        if lands:
            side = "target" if (income_items and start >= first_new) else "source"
            flows[side]["in"] += float(inp["net_for"](start))
        for e in events:
            if not (start <= e["date"] < end) or e.get("from_kind") == "card" or payer(e) != move.source_account_id:
                continue
            it = item_of(e)
            eff = effective.get(it.ref) if it else None
            if it is not None and it.decision == "drop" and eff and e["date"] >= eff and e.get("kind") != "advance":
                continue
            side = "target" if (it is not None and it.decision == "move" and eff and e["date"] >= eff) else "source"
            flows[side]["out"] += e["amount"]
            lines[side].append({"name": e["name"], "amount": e["amount"], "date": e["date"].isoformat()})
        # Everyday spending on the debit card follows the paycheck.
        flows["target" if (income_items and start >= first_new) else "source"]["out"] += pace_src * (end - start).days
        # The account the paycheck lands in keeps the buffer; the other only needs what charges it. Money moves at the
        # window's start toward whichever would end the window short, if the other can spare it.
        paid_new = bool(income_items) and start >= first_new
        keep_s = 0.0 if paid_new else buffer
        keep_t = buffer if paid_new else 0.0
        s_end = bal_s + flows["source"]["in"] - flows["source"]["out"]
        t_end = bal_t + flows["target"]["in"] - flows["target"]["out"]
        moves: list[dict] = []
        paid_into = ("target" if paid_new else "source") if lands else None
        if flows["target"]["out"] > 0 and t_end < keep_t and (target is not None or move.target_name):
            amt = _money(min(keep_t - t_end, max(0.0, s_end - keep_s)) if s_end > keep_s else keep_t - t_end)
            if amt > 0.5:
                moves.append({"amount": amt, "on": start.isoformat(), "from": sname, "to": tname, **_how(lines["target"], start, paid_into == "source")})
                bal_s, bal_t, s_end, t_end = bal_s - amt, bal_t + amt, s_end - amt, t_end + amt
        if flows["source"]["out"] > 0 and s_end < keep_s and t_end > keep_t:
            amt = _money(min(keep_s - s_end, t_end - keep_t))
            if amt > 0.5:
                moves.append({"amount": amt, "on": start.isoformat(), "from": tname, "to": sname, **_how(lines["source"], start, paid_into == "target")})
                bal_s, bal_t, s_end, t_end = bal_s + amt, bal_t - amt, s_end + amt, t_end - amt
        bal_s, bal_t = s_end, t_end
        windows.append({
            "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(), "paycheck_in": ("target" if (income_items and start >= first_new) else "source") if lands else None,
            "source": {"in": _money(flows["source"]["in"]), "out": _money(flows["source"]["out"]), "end": _money(bal_s), "items": lines["source"][:12]},
            "target": {"in": _money(flows["target"]["in"]), "out": _money(flows["target"]["out"]), "end": _money(bal_t), "items": lines["target"][:12]},
            "move_money": moves,
        })

    # Close out: everything that moved is confirmed, and nothing has landed on the old account for a while.
    moving = [p for p in plan_items if p["decision"] == "move"]
    last_old = max((date.fromisoformat(p["last_seen_old"]) for p in moving if p["last_seen_old"]), default=None)
    all_confirmed = bool(moving) and all(p["status"] == "confirmed" for p in moving)
    quiet_since = (today - last_old).days if last_old else None
    close_ready = all_confirmed and (quiet_since is None or quiet_since >= QUIET_DAYS)
    if close_ready:
        close_text = f"Everything that moved has stayed moved. Move what is left in {sname} to {tname}, then close {sname} or keep it with a small balance."
    elif all_confirmed:
        close_text = f"Everything moved. Keep {sname} open until {_short(last_old + timedelta(days=QUIET_DAYS)) if last_old else 'a quiet month passes'}, in case a charge still finds it."
    else:
        left = len([p for p in moving if p["status"] != "confirmed"])
        close_text = f"{left} item{'s' if left != 1 else ''} still to confirm. Keep {sname} open, with enough in it for what still charges there."

    counts = Counter((p["decision"], p["status"]) for p in plan_items)
    progress = {
        "total": len(plan_items), "moving": len(moving), "confirmed": counts[("move", "confirmed")], "switched": counts[("move", "switched")], "todo": counts[("move", "todo")],
        "undecided": len([p for p in plan_items if p["decision"] == "undecided"]), "kept": len([p for p in plan_items if p["decision"] == "keep"]), "dropped": len([p for p in plan_items if p["decision"] == "drop"]),
    }
    ready_steps = []
    if target is None:
        ready_steps.append({"text": f"Add {tname} to the app: link it on Connectors (Plaid finds most credit unions), or add it by hand on Accounts. The plan then confirms each switch from its ledger.", "done": False, "link": "/settings/connectors?connect=plaid"})
    else:
        ready_steps.append({"text": f"{tname} is in the app.", "done": True, "link": "/accounts"})
    first_move = next((m for w in windows for m in w["move_money"] if m["to"] == tname), None)
    ready_steps.append({"text": (f"Put {_usd(first_move['amount'])} into {tname} by {_short(date.fromisoformat(first_move['on']))}, so the first things that move are covered before the paycheck arrives there."
                                 if first_move else f"{tname} has what the first switched items need."), "done": first_move is None, "link": None})
    ready_steps.append({"text": f"The {tname} debit card {'arrived ' + _short(move.debit_card_on) if move.debit_card_on else 'has not been marked as here yet; subscriptions wait for it'}.", "done": move.debit_card_on is not None, "link": None})
    split = next((m for w in windows for m in w["move_money"] if m["how"] == "split" and m["to"] == sname), None)
    if split:
        when = _short(date.fromisoformat(split["on"]))
        what = _and(split["for"]) if split["for"] else "what still charges there"
        ask = _split_amount(split["amount"])
        text = (f"For the {when} paycheck, ask payroll for a split: {_usd(ask)} to {sname}, the rest to {tname}. {what[0].upper() + what[1:]} come{'s' if len(split['for']) == 1 else ''} out of "
                f"{sname} within {TRANSFER_DAYS} days of it, too soon for a transfer to arrive. Or ask that the change start from the paycheck after.")
        for p in plan_items:
            if p["kind"] == "income" and p["decision"] == "move":
                p["instruction"] = p["instruction"].replace(f" If payroll allows a split, send a fixed amount to {tname} first.", "") + " " + text
        split_done = any(p["kind"] == "income" and p["status"] == "confirmed" for p in plan_items) or date.fromisoformat(split["on"]) < today
        ready_steps.append({"text": text, "done": split_done, "link": None})
    phases = []
    for key, title, text in PHASES:
        entry = {"key": key, "title": title, "text": text, "items": sorted([p for p in plan_items if p["phase"] == key], key=lambda p: (p["switch_by"] or "9999", p["name"]))}
        if key == "ready":
            entry["steps"] = ready_steps
        if key == "close":
            entry["steps"] = [{"text": close_text, "done": close_ready, "link": None}]
        phases.append(entry)

    timeline = sorted((p for p in plan_items if p["decision"] in ("move", "drop") and p["effective"]), key=lambda p: p["effective"])
    return {
        "as_of": today.isoformat(), "move": {"id": move.id, "name": move.name, "status": move.status, "started_on": move.started_on.isoformat(), "finish_by": move.finish_by.isoformat() if move.finish_by else None,
                                             "debit_card_on": move.debit_card_on.isoformat() if move.debit_card_on else None},
        "source": {"id": move.source_account_id, "name": sname, "balance": source.balance if source else None},
        "target": {"id": move.target_account_id, "name": tname, "balance": target.balance if target else None, "in_app": target is not None},
        "first_new_payday": first_new.isoformat(), "progress": progress, "phases": phases, "timeline": timeline, "windows": windows,
        "alerts": alerts_for(plan_items, windows, sname, tname, today, split=split), "close": {"ready": close_ready, "text": close_text}, "not_advice": NOT_ADVICE,
    }


def _how(needs: list[dict], start: date, paycheck_funds: bool) -> dict[str, Any]:
    """A transfer when there is time for it to arrive before the first charge that needs it; when there is not and
    the money is the paycheck that lands that day, a split of the paycheck at payroll."""
    need = min((date.fromisoformat(x["date"]) for x in needs), default=start)
    tight = (need - start).days < TRANSFER_DAYS
    return {"how": "split" if (paycheck_funds and tight) else "transfer", "need_by": need.isoformat(), "tight": tight,
            "for": [f"the {x['name'][:-7]} repayment" if x["name"].endswith(" repays") else x["name"] for x in needs if (date.fromisoformat(x["date"]) - start).days < TRANSFER_DAYS][:4]}


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _split_amount(v: float) -> float:
    """What to ask payroll for: the shortfall with a small cushion, rounded up to $50."""
    return float(-(-(float(v) + 25.0) // 50) * 50)


def _instruction(i: MoveItem, sname: str, tname: str, n1: date | None, after: date | None, by: date | None, eff: date | None, first_new: date, card_ready: date, move: AccountMove,
                 owed: list[Advance] | None = None) -> str:
    window = f"after {_short(after)} and before {_short(by)}" if after and by else (f"by {_short(by)}" if by else "when it next comes up")
    if i.kind == "income":
        return (f"Give payroll {tname}'s routing and account numbers; most credit unions have a direct-deposit form in online banking. It usually takes one or two pay periods, "
                f"so the first paycheck there is expected {_short(first_new)}. If payroll allows a split, send a fixed amount to {tname} first. Keep {sname} open until a paycheck lands in {tname}.")
    if i.decision == "keep":
        return f"Stays on {sname} for now. Keep enough there for it."
    if i.kind == "card_payment":
        return f"On the card company's site, change the account {i.name.replace(' payments', '')} is paid from to {tname}, {window}. The card itself stays."
    if i.kind == "plan":
        return f"In the lender's app, change the payment method to {tname}, {window}."
    if i.kind == "subscription":
        return f"Once the {tname} debit card is here, change the card on file at {i.name} {window}." + ("" if move.debit_card_on else f" Expected from {_short(card_ready)}.")
    if i.kind == "bill":
        return f"Change {i.name}'s payment account to {tname} {window}. The {_short(eff)} charge is the first from {tname}." if eff else f"Change {i.name}'s payment account to {tname}."
    if i.kind == "advance":
        if i.decision == "drop":
            if owed:
                total = sum(a.amount + a.fee for a in owed)
                last = max(a.repay_date for a in owed)
                return (f"{i.name} takes back {_usd(total)} from {sname} on {_short(last)}; the plan keeps it there. After that, do not take another, remove {sname} from {i.name}, "
                        f"and do not link {i.name} to {tname}. The move is the clean break from advances.")
            return f"Nothing is out. Remove {sname} from {i.name}, cancel its membership if it has one, and do not link it to {tname}. The move is the clean break from advances."
        return f"Link {i.name} to {tname} after the paycheck moves, and only if you still need it."
    if i.kind == "wallet":
        if i.name == "Zelle":
            return f"Zelle follows your email or phone number. Enroll it in {tname}'s online banking {window}; that moves it off {sname}."
        return f"{i.name} draws on {sname}. In {i.name}, add {tname} as a bank and make it the preferred one {window}. Remove {sname} from {i.name} at the close."
    if i.kind == "transfer":
        return f"Recurring transfers to {i.name} leave {sname}. Point them at {tname}, or stop them."
    return f"{i.name} charges {sname} about {_usd(i.amount)}. Decide: move it to {tname}, keep it on {sname} until it ends, or drop it."


def alerts_for(items: list[dict], windows: list[dict], sname: str, tname: str, today: date, split: dict | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    income_open = any(p["kind"] == "income" and p["decision"] == "move" and p["status"] != "confirmed" for p in items)
    if split and income_open and 0 <= (date.fromisoformat(split["on"]) - today).days <= 21:
        out.append({"severity": "warn" if (date.fromisoformat(split["on"]) - today).days <= 7 else "info",
                    "text": f"Keep {_usd(_split_amount(split['amount']))} of the {_short(date.fromisoformat(split['on']))} paycheck going to {sname}: {_and(split['for']) if split['for'] else 'charges'} come out there within {TRANSFER_DAYS} days of it.", "ref": "split"})
    for p in items:
        if p["missed"]:
            out.append({"severity": "crit", "text": f"{p['name']} still charged {sname} on {_short(date.fromisoformat(p['last_seen_old']))}, after you switched it. Check the change took.", "ref": p["ref"]})
        elif p["overdue"]:
            nxt = p["effective"] or p["next_due"]
            out.append({"severity": "warn", "text": f"{p['name']} is past its window: the {_short(date.fromisoformat(nxt)) if nxt else 'next'} charge stays on {sname}. Keep {_usd(p['amount'])} there and switch it after.", "ref": p["ref"]})
        elif p["decision"] == "move" and p["status"] == "todo" and p["switch_by"] and 0 <= (date.fromisoformat(p["switch_by"]) - today).days <= 3:
            out.append({"severity": "info", "text": f"Switch {p['name']} to {tname} by {_short(date.fromisoformat(p['switch_by']))}.", "ref": p["ref"]})
        if p["kind"] == "advance" and p["decision"] == "drop" and p["last_seen_new"]:
            out.append({"severity": "warn", "text": f"{p['name']} reached {tname} on {_short(date.fromisoformat(p['last_seen_new']))}. Remove {tname} from {p['name']} if you meant to leave it behind.", "ref": p["ref"]})
    for w in windows:
        for m in w.get("move_money") or []:
            if m.get("how") != "split" and 0 <= (date.fromisoformat(m["on"]) - today).days <= 2:
                out.append({"severity": "info", "text": f"Move {_usd(m['amount'])} from {m['from']} to {m['to']} by {_short(date.fromisoformat(m['on']))}.", "ref": "money"})
    undecided = [p for p in items if p["decision"] == "undecided"]
    if undecided:
        out.append({"severity": "info", "text": f"{len(undecided)} recurring charge{'s' if len(undecided) != 1 else ''} on {sname} need{'s' if len(undecided) == 1 else ''} a decision: move, keep or drop.", "ref": "undecided"})
    order = {"crit": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["severity"]])


def active_moves(session: Session) -> list[AccountMove]:
    return list(session.exec(select(AccountMove).where(AccountMove.status == "moving")).all())
