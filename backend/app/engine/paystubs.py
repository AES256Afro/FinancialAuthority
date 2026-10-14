"""Paystubs in the engine: record a stub, match it to its deposit, and compare it with the usual net."""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from hashlib import sha256

from sqlmodel import Session, select

from ..models import Income, Paystub, Transaction

TOLERANCE = 20.0  # dollars under the usual before it is a discrepancy


def expected_net(session: Session, before: date) -> float | None:
    recent = session.exec(select(Paystub).where(Paystub.pay_date < before).order_by(Paystub.pay_date.desc()).limit(4)).all()
    nets = [p.net for p in recent]
    if len(nets) >= 2:
        return round(statistics.median(nets), 2)
    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    return income.expected_net if income else None


def match_deposit(session: Session, pay_date: date, net: float) -> Transaction | None:
    rows = session.exec(select(Transaction).where(Transaction.is_income == True, Transaction.date >= pay_date - timedelta(days=3), Transaction.date <= pay_date + timedelta(days=4))).all()  # noqa: E712
    best = None
    for t in rows:
        if abs(-t.amount - net) <= max(1.0, 0.02 * net):
            if best is None or abs((t.date - pay_date).days) < abs((best.date - pay_date).days):
                best = t
    return best


def record_paystub(session: Session, fields: dict, source: str, fact_id: int | None, today: date) -> Paystub:
    pay_date = date.fromisoformat(fields["pay_date"])
    net = round(float(fields["net"]), 2)
    gross = float(fields["gross"]) if fields.get("gross") is not None else None
    stub_hash = sha256(f"{pay_date.isoformat()}|{net:.2f}|{gross or ''}".encode()).hexdigest()
    existing = session.exec(select(Paystub).where(Paystub.stub_hash == stub_hash)).first()
    if existing is not None:
        return existing
    usual = expected_net(session, pay_date)
    deposit = match_deposit(session, pay_date, net)
    stub = Paystub(
        employer=str(fields.get("employer") or fields.get("sender_name") or ""), provider=str(fields.get("provider") or ""), pay_date=pay_date,
        period_start=date.fromisoformat(fields["period_start"]) if fields.get("period_start") else None, period_end=date.fromisoformat(fields["period_end"]) if fields.get("period_end") else None,
        gross=gross, net=net, hours=float(fields["hours"]) if fields.get("hours") is not None else None, deductions=json.dumps(fields.get("deductions") or []),
        source=source, fact_id=fact_id, transaction_id=deposit.id if deposit else None, expected_net=usual, difference=round(net - usual, 2) if usual is not None else None, stub_hash=stub_hash,
    )
    session.add(stub)
    session.commit()
    session.refresh(stub)
    # Keep the paycheck's expected net current once three stubs agree.
    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    recent = session.exec(select(Paystub).order_by(Paystub.pay_date.desc()).limit(3)).all()
    if income is not None and len(recent) >= 3:
        median = round(statistics.median(p.net for p in recent), 2)
        if abs(median - income.expected_net) > 1:
            income.expected_net = median
            session.add(income)
            session.commit()
    return stub


def paystub_out(p: Paystub) -> dict:
    return {
        "id": p.id, "employer": p.employer, "provider": p.provider, "pay_date": p.pay_date.isoformat(), "period_start": p.period_start.isoformat() if p.period_start else None,
        "period_end": p.period_end.isoformat() if p.period_end else None, "gross": p.gross, "net": p.net, "hours": p.hours, "deductions": json.loads(p.deductions or "[]"),
        "source": p.source, "transaction_id": p.transaction_id, "matched": p.transaction_id is not None, "expected_net": p.expected_net, "difference": p.difference, "sourcelink": f"paystub:{p.id}",
    }


def paystubs_overview(session: Session, limit: int = 8) -> dict:
    rows = session.exec(select(Paystub).order_by(Paystub.pay_date.desc()).limit(limit)).all()
    out = [paystub_out(p) for p in rows]
    last = out[0] if out else None
    short = [p for p in out[:2] if p["difference"] is not None and p["difference"] < -TOLERANCE]
    return {"recent": out, "last": last, "short": short}
