"""Housing, utilities and documents: the homes with their charges and move-in costs, each utility and where it
comes from, and the files kept with them."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import auth, crypto, services
from ..db import get_session
from ..engine import documents as docs
from ..engine import housing as hs
from ..models import Bill, Document, Home, HomeCharge, HomeCost, Utility
from .routes import today

router = APIRouter(prefix="/api/v1", tags=["housing"])


@router.get("/housing")
def get_housing(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    return hs.overview(session, today())


class HomeIn(BaseModel):
    name: str
    kind: str = "rent"
    payee: str = ""
    starts_on: date | None = None
    ends_on: date | None = None
    lease_ends_on: date | None = None
    first_due: date | None = None
    due_day: int = 1
    grace_days: int = 0
    late_fee: float = 0.0
    autopay: bool = False
    notice_days: int | None = None
    notice_given_on: date | None = None
    deposit: float = 0.0
    deposit_back_on: date | None = None
    pay_from_account_id: int | None = None
    note: str = ""


def _apply_home(home: Home, body: HomeIn) -> None:
    if not body.name.strip():
        raise HTTPException(400, "A home needs a name")
    if body.kind not in hs.KINDS:
        raise HTTPException(400, "kind is rent, mortgage or other")
    if not 1 <= body.due_day <= 31 or not 0 <= body.grace_days <= 31 or (body.notice_days is not None and not 0 <= body.notice_days <= 365):
        raise HTTPException(400, "due day 1 to 31, grace 0 to 31 days, notice 0 to 365 days")
    if body.starts_on and body.ends_on and body.ends_on < body.starts_on:
        raise HTTPException(400, "The end comes after the start")
    for field, value in body.model_dump().items():
        setattr(home, field, value.strip()[:300] if isinstance(value, str) else value)


@router.post("/housing")
def add_home(body: HomeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = Home(name=body.name)
    _apply_home(home, body)
    session.add(home)
    session.commit()
    hs.sync_bills(session, home)
    return hs.overview(session, today())


@router.put("/housing/{home_id}")
def change_home(home_id: int, body: HomeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = session.get(Home, home_id)
    if home is None or not home.active:
        raise HTTPException(404, "No such home")
    _apply_home(home, body)
    session.add(home)
    session.commit()
    hs.sync_bills(session, home)
    return hs.overview(session, today())


@router.delete("/housing/{home_id}")
def remove_home(home_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = session.get(Home, home_id)
    if home is None:
        raise HTTPException(404, "No such home")
    home.active = False
    session.add(home)
    session.commit()
    hs.sync_bills(session, home)
    return hs.overview(session, today())


class NoticeIn(BaseModel):
    given_on: date | None = None


@router.post("/housing/{home_id}/notice")
def notice_given(home_id: int, body: NoticeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = session.get(Home, home_id)
    if home is None or not home.active:
        raise HTTPException(404, "No such home")
    home.notice_given_on = body.given_on
    session.add(home)
    session.commit()
    return hs.overview(session, today())


class AdoptIn(BaseModel):
    bill_id: int
    name: str
    kind: str = "rent"


@router.post("/housing/adopt")
def adopt(body: AdoptIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    bill = session.get(Bill, body.bill_id)
    if bill is None or not bill.active or bill.home_id:
        raise HTTPException(404, "No such rent bill")
    hs.adopt_bill(session, bill, name=body.name, kind=body.kind)
    return hs.overview(session, today())


class ChargeIn(BaseModel):
    name: str
    kind: str = "fee"
    amount: float | None = None
    estimate: bool = False
    pays_to: str = "home"
    payee: str = ""
    due_day: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    note: str = ""


def _apply_charge(c: HomeCharge, body: ChargeIn) -> None:
    if not body.name.strip() or body.kind not in hs.CHARGE_KINDS or body.pays_to not in ("home", "other"):
        raise HTTPException(400, "A charge has a name, a kind and who it is paid to (home or other)")
    if body.amount is not None and not 0 <= body.amount <= 100_000:
        raise HTTPException(400, "The amount is between 0 and 100,000")
    for field, value in body.model_dump().items():
        setattr(c, field, value.strip()[:200] if isinstance(value, str) else value)


@router.post("/housing/{home_id}/charges")
def add_charge(home_id: int, body: ChargeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = session.get(Home, home_id)
    if home is None:
        raise HTTPException(404, "No such home")
    c = HomeCharge(home_id=home_id, name=body.name)
    _apply_charge(c, body)
    session.add(c)
    session.commit()
    hs.sync_bills(session, home)
    return hs.overview(session, today())


@router.put("/housing/charges/{charge_id}")
def change_charge(charge_id: int, body: ChargeIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    c = session.get(HomeCharge, charge_id)
    if c is None:
        raise HTTPException(404, "No such charge")
    _apply_charge(c, body)
    session.add(c)
    session.commit()
    hs.sync_bills(session, session.get(Home, c.home_id))
    return hs.overview(session, today())


@router.delete("/housing/charges/{charge_id}")
def remove_charge(charge_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    c = session.get(HomeCharge, charge_id)
    if c is None:
        raise HTTPException(404, "No such charge")
    c.active = False
    session.add(c)
    session.commit()
    if c.bill_id and (b := session.get(Bill, c.bill_id)):
        b.active = False
        session.add(b)
        session.commit()
    hs.sync_bills(session, session.get(Home, c.home_id))
    return hs.overview(session, today())


class CostIn(BaseModel):
    name: str
    amount: float | None = None
    due_on: date | None = None
    refundable: bool = False
    parts: list[dict] = []
    paid: bool = False
    note: str = ""


def _apply_cost(c: HomeCost, body: CostIn) -> None:
    if not body.name.strip():
        raise HTTPException(400, "A cost needs a name")
    if body.amount is not None and not 0 <= body.amount <= 1_000_000:
        raise HTTPException(400, "The amount is between 0 and a million")
    parts = [{"name": str(p.get("name") or "")[:80], "amount": round(float(p.get("amount") or 0), 2), "refundable": bool(p.get("refundable"))} for p in body.parts[:40]]
    c.name, c.amount, c.due_on, c.refundable, c.paid, c.note = body.name.strip()[:80], body.amount, body.due_on, body.refundable, body.paid, body.note.strip()[:300]
    c.parts = json.dumps(parts)


@router.post("/housing/{home_id}/costs")
def add_cost(home_id: int, body: CostIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    home = session.get(Home, home_id)
    if home is None:
        raise HTTPException(404, "No such home")
    c = HomeCost(home_id=home_id, name=body.name)
    _apply_cost(c, body)
    session.add(c)
    session.commit()
    hs.sync_bills(session, home)
    return hs.overview(session, today())


@router.put("/housing/costs/{cost_id}")
def change_cost(cost_id: int, body: CostIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    c = session.get(HomeCost, cost_id)
    if c is None:
        raise HTTPException(404, "No such cost")
    _apply_cost(c, body)
    session.add(c)
    session.commit()
    hs.sync_bills(session, session.get(Home, c.home_id))
    return hs.overview(session, today())


@router.delete("/housing/costs/{cost_id}")
def remove_cost(cost_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    c = session.get(HomeCost, cost_id)
    if c is None:
        raise HTTPException(404, "No such cost")
    c.active = False
    session.add(c)
    session.commit()
    if c.bill_id and (b := session.get(Bill, c.bill_id)):
        b.active = False
        session.add(b)
        session.commit()
    hs.sync_bills(session, session.get(Home, c.home_id))
    return hs.overview(session, today())


@router.get("/housing/{home_id}/move")
def move(home_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    try:
        return hs.move_check(session, today(), home_id)
    except KeyError as error:
        raise HTTPException(404, "No such home, or it has no move-in date") from error


# ---- utilities ------------------------------------------------------------------------------------------------


class UtilityIn(BaseModel):
    home_id: int | None = None
    service: str = "electric"
    provider: str = ""
    billed: str = "direct"
    billing_company: str = ""
    account_last4: str | None = None
    amount: float | None = None
    estimate: bool = True
    due_day: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    moves_with_you: bool = False
    note: str = ""
    bill_id: int | None = None  # a bill already on the list that is this utility's


def _apply_utility(u: Utility, body: UtilityIn, session: Session) -> None:
    if body.service not in hs.SERVICES or body.billed not in hs.BILLED:
        raise HTTPException(400, f"service is one of {', '.join(hs.SERVICES)}; billed is one of {', '.join(hs.BILLED)}")
    if body.amount is not None and not 0 <= body.amount <= 10_000:
        raise HTTPException(400, "A usual month is between 0 and 10,000")
    for field in ("home_id", "service", "provider", "billed", "billing_company", "amount", "estimate", "due_day", "starts_on", "ends_on", "moves_with_you", "note"):
        value = getattr(body, field)
        setattr(u, field, value.strip()[:120] if isinstance(value, str) else value)
    if body.account_last4 is not None:
        digits = "".join(ch for ch in body.account_last4 if ch.isdigit())[-4:]
        u.account_last4_enc = crypto.encrypt(digits) if digits else ""
    if body.bill_id is not None and body.bill_id != u.bill_id:
        bill = session.get(Bill, body.bill_id)
        if bill is None or not bill.active or bill.home_id or (bill.utility_id and bill.utility_id != u.id):
            raise HTTPException(400, "That bill is not on the list, or it already belongs to a home or another utility")
        u.bill_id = bill.id
        bill.pays_to = bill.pays_to or bill.name  # its payments keep matching under the new name
        if u.amount is None:
            u.amount, u.estimate = bill.amount, bill.amount_mode == "estimate"
        u.due_day = u.due_day or bill.due_day
        session.add(bill)


@router.get("/utilities")
def list_utilities(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    rows = session.exec(select(Utility).where(Utility.active == True)).all()  # noqa: E712
    linkable = session.exec(select(Bill).where(Bill.active == True, Bill.home_id == None, Bill.utility_id == None)).all()  # noqa: E711, E712
    return {"utilities": [hs.utility_out(u) for u in rows], "services": list(hs.SERVICES), "billed": hs.BILLED_WORDS,
            "bills_to_link": [{"id": b.id, "name": b.name, "amount": b.amount, "due_day": b.due_day, "category": b.category} for b in linkable if b.kind == "bill"]}


@router.post("/utilities")
def add_utility(body: UtilityIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    u = Utility()
    _apply_utility(u, body, session)
    session.add(u)
    session.commit()
    hs.sync_utility(session, u)
    return list_utilities(session)


@router.put("/utilities/{utility_id}")
def change_utility(utility_id: int, body: UtilityIn, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    u = session.get(Utility, utility_id)
    if u is None:
        raise HTTPException(404, "No such utility")
    _apply_utility(u, body, session)
    session.add(u)
    session.commit()
    hs.sync_utility(session, u)
    return list_utilities(session)


@router.delete("/utilities/{utility_id}")
def remove_utility(utility_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    u = session.get(Utility, utility_id)
    if u is None:
        raise HTTPException(404, "No such utility")
    u.active = False
    hs.sync_utility(session, u)
    return list_utilities(session)


# ---- documents ------------------------------------------------------------------------------------------------


@router.get("/documents")
def list_documents(attach_to: str = "", kind: str = "", period: str = "", session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    query = select(Document).order_by(Document.uploaded_at.desc())
    if attach_to:
        query = query.where(Document.attach_to == attach_to)
    if kind:
        query = query.where(Document.kind == kind)
    if period:
        query = query.where(Document.period == period)
    return {"documents": [docs.doc_out(d) for d in session.exec(query).all()], "kinds": list(docs.KINDS)}


@router.get("/documents/months")
def documents_by_month(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """Every kept file with a month, grouped by month: statements, paystubs, receipts."""
    from ..engine.receipts import months

    got = months(session)
    undated = session.exec(select(Document).where(Document.period == "").order_by(Document.uploaded_at.desc())).all()
    return {"months": got, "undated": [docs.doc_out(d) for d in undated]}


@router.get("/receipts")
def get_receipts(session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    from ..engine.receipts import receipts_overview

    return receipts_overview(session, today())


@router.get("/receipts/candidates")
def receipt_candidates(amount: float, bought_on: date, merchant: str = "", session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_read)) -> dict:
    """The purchases a receipt with this total and day may belong to."""
    from ..engine.receipts import candidates

    return {"candidates": candidates(session, amount, bought_on, merchant, limit=5)}


def _form_date(value: str, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(400, f"{label} is a date, YYYY-MM-DD") from error


@router.post("/documents")
def upload_document(file: UploadFile = File(...), title: str = Form(""), kind: str = Form("other"), attach_to: str = Form(""), note: str = Form(""),
                    period: str = Form(""), merchant: str = Form(""), amount: str = Form(""), bought_on: str = Form(""), return_by: str = Form(""), warranty_until: str = Form(""),
                    session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    raw = file.file.read(docs.MAX_BYTES + 1)
    try:
        money = round(float(amount.replace(",", "").replace("$", "")), 2) if amount.strip() else None
    except ValueError as error:
        raise HTTPException(400, "The amount is a number, like 42.50") from error
    try:
        doc = docs.save_document(session, raw, filename=file.filename or "document", title=title, kind=kind, attach_to=attach_to, note=note, period=period.strip(),
                                 merchant=merchant, amount=money, bought_on=_form_date(bought_on, "The day bought"), return_by=_form_date(return_by, "The last day to return"),
                                 warranty_until=_form_date(warranty_until, "The warranty's end"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="document_upload", args={"kind": kind, "attach_to": attach_to}, rows=1)
    out = docs.doc_out(doc)
    if not out["has_text"]:
        out["note_text"] = "No text could be read from it (a photo or a scan). It is kept; type its key terms on the page it belongs to."
    return out


@router.get("/documents/search")
def search_documents(q: str, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    if principal.redaction != "full":
        raise HTTPException(403, "Documents are read with full access only")
    return {"hits": docs.search(session, q[:200])}


@router.get("/documents/{doc_id}/pages/{page}")
def document_page(doc_id: int, page: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    """One page's text, as the models read it through document_read."""
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    pages = docs.pages_of(doc)
    if not 1 <= page <= len(pages):
        raise HTTPException(404, f"It has {len(pages)} page{'s' if len(pages) != 1 else ''} of text")
    return {**docs.doc_out(doc), "page": page, "of": len(pages), "text": pages[page - 1]}


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_owner)) -> Response:
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    try:
        raw = docs.file_of(doc)
    except FileNotFoundError as error:
        raise HTTPException(410, "The file is gone from the data folder (a restore from before it was added?)") from error
    services.audit(session, client=principal.name, client_kind=principal.kind, tool="document_file", args={"id": doc_id}, rows=1)
    safe_name = "".join(ch for ch in doc.filename if ch.isalnum() or ch in "._- ")[:100] or "document"
    return Response(raw, media_type=doc.content_type, headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "Cache-Control": "no-store"})


class DocumentEdit(BaseModel):
    title: str | None = None
    kind: str | None = None
    attach_to: str | None = None
    note: str | None = None
    period: str | None = None
    merchant: str | None = None
    amount: float | None = None
    bought_on: date | None = None
    return_by: date | None = None
    warranty_until: date | None = None


@router.put("/documents/{doc_id}")
def edit_document(doc_id: int, body: DocumentEdit, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Change what a kept file belongs to, its month, or a receipt's store, total and dates. Fields left out stay."""
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    try:
        docs.update_document(session, doc, body.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return docs.doc_out(doc)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, session: Session = Depends(get_session), _: auth.Principal = Depends(auth.require_admin)) -> dict:
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "No such document")
    docs.remove_document(session, doc)
    return {"ok": True}
