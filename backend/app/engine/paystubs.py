"""Paystubs in the engine: record a stub, match it to its deposit, and compare it with the usual net."""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from hashlib import sha256

from sqlmodel import Session, select

from ..models import ExtractedFact, Income, Paystub, Transaction
from .cadence import payday_position

TOLERANCE = 20.0  # dollars under the usual before it is a discrepancy


def _position(income: Income | None, pay_date: date) -> str:
    return payday_position(pay_date) if income is not None and income.cadence == "semimonthly" else ""


def expected_net(session: Session, before: date) -> float | None:
    """The usual net for a check landing on `before`: the median of the last stubs in the same position of the
    month (a semi-monthly paycheck's mid-month and end-of-month checks differ), else what the income says."""
    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    position = _position(income, before)
    recent = session.exec(select(Paystub).where(Paystub.pay_date < before).order_by(Paystub.pay_date.desc()).limit(8)).all()
    nets = [p.net for p in recent if not position or payday_position(p.pay_date) == position][:4]
    if len(nets) >= 2:
        return round(statistics.median(nets), 2)
    if income is None:
        return None
    if position == "end" and income.expected_net_end is not None:
        return income.expected_net_end
    return income.expected_net


def paystub_fact_from_file(session: Session, data: bytes, filename: str, today: date, source: str) -> ExtractedFact | None:
    """A paystub the owner handed over as a file (an upload, or the import folder): read it and queue it on
    Needs you as a pending fact, the same as one that came by mail. None when it does not read as a stub."""
    from ..mail.classify import excerpt_for
    from ..parsers.paystub import parse_paystub_text, pdf_text

    text = pdf_text(data) if filename.lower().endswith(".pdf") else data.decode("utf-8", errors="replace")
    stub = parse_paystub_text(text, today)
    if not stub:
        return None
    confidence = stub.pop("confidence")
    stub.update({"source": source, "attachment": filename, "sender_name": stub.get("employer") or ""})
    fact = ExtractedFact(kind="paystub", message_ref_id=None, fields=json.dumps(stub), confidence=confidence, status="pending", excerpt=excerpt_for(text, {"amount": stub.get("net"), "due_date": stub.get("pay_date")}), produced_by=f"{source}+parser:paystub")
    session.add(fact)
    session.commit()
    session.refresh(fact)
    return fact


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
    # Keep the paycheck's expected net current. A semi-monthly paycheck keeps one figure per position of the
    # month: the first stub in a position sets it, and from two stubs the median does.
    income = session.exec(select(Income).where(Income.active == True)).first()  # noqa: E712
    if income is not None:
        position = _position(income, pay_date)
        recent = session.exec(select(Paystub).order_by(Paystub.pay_date.desc()).limit(8)).all()
        same = [p.net for p in recent if not position or payday_position(p.pay_date) == position][:3]
        target = "expected_net_end" if position == "end" else "expected_net"
        current = getattr(income, target)
        if position and len(same) == 1 and (current is None or abs(same[0] - current) > TOLERANCE):
            setattr(income, target, round(same[0], 2))
            session.add(income)
            session.commit()
        elif (len(same) >= 2 if position else len(same) >= 3):
            median = round(statistics.median(same), 2)
            if current is None or abs(median - current) > 1:
                setattr(income, target, median)
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
