"""Business and taxes (M56 to M60): the setup guide, the books, invoices, the write-off finder, the mileage log and the
tax center. Reading needs the read scope; every change is the owner's (admin)."""

from __future__ import annotations

import html
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import auth, services
from ..db import get_session
from ..engine import business as biz
from ..engine import taxcenter
from ..engine import taxrules as R
from ..models import Account, Business, Invoice, Transaction, Trip
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1/business", tags=["business"])
tax_router = APIRouter(prefix="/api/v1/taxes", tags=["taxes"])


def _out(b: Business) -> dict:
    return {"id": b.id, "name": b.name, "kind": b.kind, "state": b.state, "started_on": b.started_on.isoformat() if b.started_on else None, "what_it_does": b.what_it_does,
            "ein_on_file": b.ein_on_file, "assumed_name": b.assumed_name, "account_id": b.account_id, "home_office_sqft": b.home_office_sqft, "active": b.active}


def _get(session: Session, business_id: int) -> Business:
    b = session.get(Business, business_id)
    if b is None:
        raise HTTPException(404, "No such business")
    return b


@router.get("")
def overview(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Business).order_by(Business.id)).all()
    first = next((b for b in rows if b.active), None)
    return {"businesses": [_out(b) for b in rows], "guide": biz.guide(session, first), "lines": [{"key": k, **v} for k, v in R.TAX_LINES.items()]}


class BusinessIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "sole_prop"
    state: str = Field(default="TX", min_length=2, max_length=2)
    started_on: date | None = None
    what_it_does: str = Field(default="", max_length=300)
    ein_on_file: bool = False
    assumed_name: bool = False
    account_id: int | None = None
    home_office_sqft: int | None = Field(default=None, ge=0, le=10000)
    active: bool = True


def _apply(session: Session, b: Business, body: BusinessIn) -> None:
    if body.kind not in ("sole_prop", "llc"):
        raise HTTPException(400, "The kind is sole_prop or llc")
    if body.account_id is not None and session.get(Account, body.account_id) is None:
        raise HTTPException(400, "No such account")
    b.name, b.kind, b.state, b.started_on, b.what_it_does = body.name.strip(), body.kind, body.state.upper(), body.started_on, body.what_it_does.strip()
    b.ein_on_file, b.assumed_name, b.account_id, b.home_office_sqft, b.active = body.ein_on_file, body.assumed_name, body.account_id, body.home_office_sqft, body.active


@router.post("")
@under_writer
def add_business(body: BusinessIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    b = Business(name=body.name)
    _apply(session, b, body)
    session.add(b)
    session.commit()
    session.refresh(b)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="business_add", args={"id": b.id}, rows=1)
    return overview(session, principal)


@router.put("/{business_id}")
@under_writer
def change_business(business_id: int, body: BusinessIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    b = _get(session, business_id)
    _apply(session, b, body)
    session.add(b)
    session.commit()
    return overview(session, principal)


class TickIn(BaseModel):
    done: bool


@router.post("/setup/{step}")
@under_writer
def tick_step(step: str, body: TickIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    if step not in {s["id"] for s in biz.guide_steps(None, {}) + biz.guide_steps(Business(name="x", kind="llc"), {})}:
        raise HTTPException(404, "No such step")
    try:
        biz.tick(session, step, body.done, today())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return overview(session, principal)


@router.get("/finder")
def finder(years: int = Query(3, ge=1, le=10), session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return biz.finder(session, today(), years)


class AnswerIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    answer: str
    business_id: int | None = None
    tax_line: str = ""
    share: float = Field(default=1.0, gt=0, le=1)


@router.post("/finder/answer")
@under_writer
def answer_finder(body: AnswerIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        said = biz.answer_finder(session, body.ids, body.answer, body.business_id, body.tax_line, body.share)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="business_finder_answer", args={"answer": body.answer, "count": len(body.ids)}, rows=len(body.ids))
    return {**biz.finder(session, today()), "said": said}


class MarkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    business_id: int | None = None
    share: float = Field(default=1.0, gt=0, le=1)
    tax_line: str = ""


@router.post("/mark")
@under_writer
def mark(body: MarkIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    if body.business_id is not None:
        _get(session, body.business_id)
    try:
        n = biz.mark(session, body.ids, body.business_id, body.share, body.tax_line)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"marked": n}


@router.get("/{business_id}/books")
def books(business_id: int, year: int | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return biz.books(session, _get(session, business_id), year or today().year)


# ---- invoices ------------------------------------------------------------------------------------------------

class LineIn(BaseModel):
    what: str = Field(min_length=1, max_length=200)
    quantity: float = Field(default=1, ge=0, le=100000)
    price: float = Field(ge=0, le=10_000_000)


class InvoiceIn(BaseModel):
    direction: str = "out"
    counterparty: str = Field(min_length=1, max_length=120)
    issued_on: date
    due_on: date | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=100)
    status: str = "draft"
    tax_line: str = ""
    note: str = Field(default="", max_length=500)
    number: str = Field(default="", max_length=40)


def _invoice_out(session: Session, i: Invoice) -> dict:
    out = {"id": i.id, "business_id": i.business_id, "direction": i.direction, "number": i.number, "counterparty": i.counterparty, "issued_on": i.issued_on.isoformat(),
           "due_on": i.due_on.isoformat() if i.due_on else None, "lines": biz.json_lines(i.lines), "amount": i.amount, "status": i.status,
           "paid_on": i.paid_on.isoformat() if i.paid_on else None, "transaction_id": i.transaction_id, "tax_line": i.tax_line, "note": i.note, "source": f"invoice:{i.id}"}
    if i.status in ("draft", "sent"):
        out["candidates"] = biz.invoice_candidates(session, i)
    return out


def _apply_invoice(session: Session, i: Invoice, body: InvoiceIn) -> None:
    if body.direction not in ("out", "in") or body.status not in ("draft", "sent", "void"):
        raise HTTPException(400, "Direction is out or in; status is draft, sent or void (paid is set by matching the payment)")
    if body.tax_line and body.tax_line not in R.TAX_LINES:
        raise HTTPException(400, "Not a line the app knows")
    lines = [line.model_dump() for line in body.lines]
    i.direction, i.counterparty, i.issued_on, i.due_on = body.direction, body.counterparty.strip(), body.issued_on, body.due_on
    i.lines, i.amount, i.status, i.tax_line, i.note = json.dumps(lines), biz.invoice_total(lines), body.status, body.tax_line, body.note.strip()
    if body.number:
        i.number = body.number.strip()


@router.get("/{business_id}/invoices")
def invoices(business_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    _get(session, business_id)
    rows = session.exec(select(Invoice).where(Invoice.business_id == business_id).order_by(Invoice.issued_on.desc())).all()  # type: ignore[attr-defined]
    out = [_invoice_out(session, i) for i in rows]
    unpaid = [i for i in out if i["direction"] == "out" and i["status"] == "sent"]
    return {"invoices": out, "owed_to_you": round(sum(i["amount"] for i in unpaid), 2), "unpaid": len(unpaid)}


@router.post("/{business_id}/invoices")
@under_writer
def add_invoice(business_id: int, body: InvoiceIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    _get(session, business_id)
    i = Invoice(business_id=business_id, issued_on=body.issued_on)
    _apply_invoice(session, i, body)
    if not i.number and i.direction == "out":
        i.number = biz.next_invoice_number(session, business_id, body.issued_on.year)
    session.add(i)
    session.commit()
    session.refresh(i)
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="invoice_add", args={"id": i.id}, rows=1)
    return _invoice_out(session, i)


@router.put("/invoices/{invoice_id}")
@under_writer
def change_invoice(invoice_id: int, body: InvoiceIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    i = session.get(Invoice, invoice_id)
    if i is None:
        raise HTTPException(404, "No such invoice")
    if i.status == "paid":
        raise HTTPException(409, "A paid invoice is kept as it was; void it and make a new one")
    _apply_invoice(session, i, body)
    session.add(i)
    session.commit()
    return _invoice_out(session, i)


class PaidIn(BaseModel):
    transaction_id: int | None = None
    paid_on: date | None = None


@router.post("/invoices/{invoice_id}/paid")
@under_writer
def invoice_paid(invoice_id: int, body: PaidIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    i = session.get(Invoice, invoice_id)
    if i is None:
        raise HTTPException(404, "No such invoice")
    if body.transaction_id is not None:
        t = session.get(Transaction, body.transaction_id)
        if t is None:
            raise HTTPException(400, "No such transaction")
        i.transaction_id, i.paid_on = t.id, t.date
        t.business_id, t.business_share = i.business_id, 1.0
        if i.direction == "in":
            t.tax_line = i.tax_line
        session.add(t)
    else:
        i.paid_on = body.paid_on or today()
    i.status = "paid"
    session.add(i)
    session.commit()
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="invoice_paid", args={"id": i.id}, rows=1)
    return _invoice_out(session, i)


@router.delete("/invoices/{invoice_id}")
@under_writer
def remove_invoice(invoice_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    i = session.get(Invoice, invoice_id)
    if i is None:
        raise HTTPException(404, "No such invoice")
    if i.status != "draft":
        raise HTTPException(409, "Only a draft is removed; a sent or paid invoice is voided instead, so its number stays accounted for")
    session.delete(i)
    session.commit()
    return {"ok": True}


@router.get("/invoices/{invoice_id}/print")
def print_invoice(invoice_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_owner)) -> Response:
    """A printable invoice (print it to PDF from the browser). Every value is escaped: a name is shown, never run."""
    i = session.get(Invoice, invoice_id)
    if i is None or i.direction != "out":
        raise HTTPException(404, "No such invoice")
    b = _get(session, i.business_id)
    e = html.escape
    rows = "".join(f"<tr><td>{e(str(line.get('what', '')))}</td><td class=n>{float(line.get('quantity') or 1):g}</td><td class=n>${float(line.get('price') or 0):,.2f}</td>"
                   f"<td class=n>${float(line.get('quantity') or 1) * float(line.get('price') or 0):,.2f}</td></tr>" for line in biz.json_lines(i.lines))
    body = (f"<!doctype html><meta charset=utf-8><title>Invoice {e(i.number)}</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;color:#141518}}"
            f"table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}.n{{text-align:right}}h1{{margin:0}}</style>"
            f"<h1>Invoice {e(i.number)}</h1><p><strong>{e(b.name)}</strong></p><p>Billed to: <strong>{e(i.counterparty)}</strong><br>Date: {i.issued_on:%B %d, %Y}"
            f"{'<br>Due: ' + i.due_on.strftime('%B %d, %Y') if i.due_on else ''}</p><table><tr><th>What</th><th class=n>Quantity</th><th class=n>Price</th><th class=n>Amount</th></tr>{rows}"
            f"<tr><td colspan=3><strong>Total</strong></td><td class=n><strong>${i.amount:,.2f}</strong></td></tr></table>{'<p>' + e(i.note) + '</p>' if i.note else ''}")
    return Response(content=body, media_type="text/html", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


# ---- the mileage log -----------------------------------------------------------------------------------------

class TripIn(BaseModel):
    day: date
    miles: float = Field(gt=0, le=2000)
    purpose: str = Field(min_length=3, max_length=200)
    start: str = Field(default="", max_length=120)
    end: str = Field(default="", max_length=120)


@router.get("/{business_id}/trips")
def trips(business_id: int, year: int | None = None, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    _get(session, business_id)
    return biz.trips_year(session, business_id, year or today().year)


@router.post("/{business_id}/trips")
@under_writer
def add_trip(business_id: int, body: TripIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    _get(session, business_id)
    if body.day > today():
        raise HTTPException(400, "A trip is logged on or after the day it happens, not before")
    if "commut" in body.purpose.lower():
        raise HTTPException(400, "Commuting between home and a regular workplace never counts as a business trip")
    session.add(Trip(business_id=business_id, day=body.day, miles=round(body.miles, 1), purpose=body.purpose.strip(), start=body.start.strip(), end=body.end.strip()))
    session.commit()
    return biz.trips_year(session, business_id, body.day.year)


@router.delete("/trips/{trip_id}")
@under_writer
def remove_trip(trip_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    t = session.get(Trip, trip_id)
    if t is None:
        raise HTTPException(404, "No such trip")
    business_id, year = t.business_id, t.day.year
    session.delete(t)
    session.commit()
    return biz.trips_year(session, business_id, year)


# ---- the tax center ------------------------------------------------------------------------------------------

@tax_router.get("")
def tax_now(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return taxcenter.tax_year(session, today())


@tax_router.get("/{year}")
def tax_for(year: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    if not 2015 <= year <= today().year + 1:
        raise HTTPException(400, "A year from 2015 to next year")
    return taxcenter.tax_year(session, today(), year)


class AnswersIn(BaseModel):
    answers: dict[str, object]


@tax_router.post("/answers")
def tax_answers(body: AnswersIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    try:
        taxcenter.save_answers(session, body.answers)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return taxcenter.tax_year(session, today())
