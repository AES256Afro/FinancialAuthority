"""Sweep 15, part 3: undo is undo.

Each undo the app offers, on the synthetic household: every table dumped before the action, the action, the undo,
every table dumped again. What differs must be what the app says stays (the owner's own acts since an import, the
sign-ins and keys a restore carries, the rows a rule changed). The findings, reduced:

- an import whose row was then marked as an invoice's payment could not be undone at all (the database refused to
  delete a row the invoice still named; the undo answered 500);
- a planned payment one of the import's rows had settled stayed done after the undo, so the plan stopped counting
  a payment that had not posted;
- undoing a bills list put back every field of a bill it updated, wiping the owner's own edit made since;
- a bank-move switch the ledger confirmed from an imported row stayed confirmed, its bill paid from the new
  account, after that row was undone.
"""

from __future__ import annotations

import io
from collections import Counter
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.api import routes
from app.db import engine
from app.engine.household import seed
from app.models import Account, Bill, Invoice, MoveItem, PlannedPayment, Transaction

# The record of runs and reads, sign-ins, and the import log itself; a retired detection series is kept by design
# (detection never deletes one) and counts in no figure.
NOISE = {"job_runs", "health_samples", "audit_entries", "web_sessions", "import_batches", "review_runs", "oauth_tokens", "oauth_codes", "recurring_series"}
NOISE_SETTINGS = {"jobs.detected_through", "jobs.matched_through", "selfcheck", "usage.calls", "apphealth.last", "review.pending", "datacheck.last", "public.last_seen"}


def tables() -> dict[str, Counter]:
    out = {}
    with engine.connect() as c:
        for t in SQLModel.metadata.sorted_tables:
            if t.name in NOISE:
                continue
            cols = [x.name for x in t.columns if not x.name.endswith("_at")]
            rows = c.execute(text(f'SELECT {", ".join(chr(34) + x + chr(34) for x in cols)} FROM "{t.name}"')).all()
            if t.name == "settings":
                rows = [r for r in rows if r[0] not in NOISE_SETTINGS and not str(r[0]).startswith("watch.")]
            out[t.name] = Counter(tuple(zip(cols, r)) for r in rows)
    return out


def changed(a: dict[str, Counter], b: dict[str, Counter]) -> list[str]:
    return [f"{t}: +{list((b[t] - a[t]).elements())[:3]} -{list((a[t] - b[t]).elements())[:3]}" for t in sorted(a) if a[t] != b.get(t)]


def household(today: date = date(2026, 10, 14)) -> int:
    """The seeded household with detection, borrowing and matching run once: the steady state an undo returns to."""
    from app.engine.borrowing import sync_borrowing
    from app.engine.payments import match_payments
    from app.jobs.detect import run_detection

    routes.set_today_override(today)
    with Session(engine) as s:
        seed(s)
        run_detection(s, today)
        sync_borrowing(s, today)
        match_payments(s, today)
        return s.exec(select(Account).where(Account.kind == "checking")).first().id


def upload(owner: TestClient, name: str, body: str, **data: str) -> dict:
    r = owner.post("/api/v1/imports/file", files={"file": (name, io.BytesIO(body.encode()), "text/csv")}, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_import_kind_undone_leaves_every_table_as_it_was(owner: TestClient):
    chk = household(date(2026, 10, 24))
    ofx = ("OFXHEADER:100\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>99990001<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST>"
           + "".join(f"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>2026{m:02d}01<TRNAMT>-19.99<FITID>A{m}<NAME>EXAMPLE STREAMING</STMTTRN>" for m in (7, 8, 9, 10))
           + "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
    rm = ("Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible\n"
          "2026-10-01,2026-10-01,Credit Card,Example RM Card,4444,Example Bank,EXAMPLE DINER,,25.00,EXAMPLE DINER,Dining & Drinks,,,\n")
    for name, send in (
        ("rows paying two bills", lambda: upload(owner, "x.csv", "Date,Description,Amount\n2026-10-22,PHONE,-96.00\n2026-10-24,INTERNET,-85.00\n", account_id=str(chk))),
        ("an ofx statement", lambda: upload(owner, "s.ofx", ofx, account_id=str(chk))),
        ("a bills list", lambda: upload(owner, "bills.csv", "Name,Amount,Due Day,Frequency\nRent,1300.00,1,monthly\n")),
        ("a rocket money export", lambda: owner.post("/api/v1/imports/rocketmoney", files={"file": ("rm.csv", io.BytesIO(rm.encode()), "text/csv")}).json()),
    ):
        before = tables()
        done = send()
        household_jobs(date(2026, 10, 24))
        undone = owner.post(f"/api/v1/imports/{done['import_id']}/undo")
        assert undone.status_code == 200, undone.text
        household_jobs(date(2026, 10, 24))
        after = tables()
        # A bank or a bill list made no account here; an account the export made is closed, as the undo says.
        after["accounts"] = Counter({r: n for r, n in after["accounts"].items() if dict(r)["active"] or dict(r)["connector"] != "rocketmoney"})
        assert not changed(before, after), (name, changed(before, after))


def household_jobs(today: date) -> None:
    from app.engine.payments import match_payments
    from app.jobs.detect import run_detection

    with Session(engine) as s:
        run_detection(s, today)
        match_payments(s, today)


def _studio(owner: TestClient) -> tuple[int, int]:
    biz = owner.post("/api/v1/business", json={"name": "Example Studio", "kind": "sole_prop", "state": "TX", "started_on": "2026-01-10"}).json()["businesses"][0]["id"]
    inv = owner.post(f"/api/v1/business/{biz}/invoices", json={"direction": "out", "counterparty": "Example Client", "issued_on": "2026-10-01", "due_on": "2026-10-30",
                                                                "lines": [{"what": "Work", "quantity": 1, "price": 450.0}], "status": "sent"}).json()
    return biz, inv["id"]


def test_an_import_whose_row_paid_an_invoice_can_still_be_undone(owner: TestClient):
    chk = household()
    _, invoice = _studio(owner)
    done = upload(owner, "x.csv", "Date,Description,Amount\n2026-10-13,EXAMPLE CLIENT,450.00\n", account_id=str(chk))
    with Session(engine) as s:
        deposit = s.exec(select(Transaction).where(Transaction.merchant == "EXAMPLE CLIENT")).one().id
    assert owner.post(f"/api/v1/business/invoices/{invoice}/paid", json={"transaction_id": deposit}).status_code == 200
    undone = owner.post(f"/api/v1/imports/{done['import_id']}/undo")
    assert undone.status_code == 200 and undone.json()["removed"] == 1, undone.text
    upload(owner, "y.csv", "Date,Description,Amount\n2026-10-14,EXAMPLE NEWSSTAND,-3.00\n", account_id=str(chk))  # takes the freed id
    with Session(engine) as s:
        i = s.get(Invoice, invoice)
        assert i.status == "paid" and i.transaction_id is None  # the owner's "paid" stays; it names no row another import took


def test_undoing_an_import_opens_again_the_planned_payment_its_row_settled(owner: TestClient):
    chk = household()
    plan = owner.post("/api/v1/planned", json={"name": "Example Extra", "amount": 100.0, "date": "2026-10-12", "kind": "other"}).json()["id"]
    before = tables()
    done = upload(owner, "x.csv", "Date,Description,Amount\n2026-10-12,EXAMPLE EXTRA,-100.00\n", account_id=str(chk))
    with Session(engine) as s:
        assert s.get(PlannedPayment, plan).done
    owner.post(f"/api/v1/imports/{done['import_id']}/undo")
    with Session(engine) as s:
        assert not s.get(PlannedPayment, plan).done
    assert not changed(before, tables())


def test_undoing_a_bills_list_keeps_what_the_owner_changed_since(owner: TestClient):
    household()
    with Session(engine) as s:
        rent = s.exec(select(Bill).where(Bill.name == "Rent")).one().id
    done = upload(owner, "bills.csv", "Name,Amount,Due Day,Frequency\nRent,1300.00,1,monthly\n")
    assert owner.put(f"/api/v1/bills/{rent}", json={"due_day": 3, "category": "Housing"}).status_code == 200
    owner.post(f"/api/v1/imports/{done['import_id']}/undo")
    with Session(engine) as s:
        b = s.get(Bill, rent)
        assert (b.amount, b.due_day, b.category) == (1295.0, 3, "Housing")  # the import's amount taken back, the owner's day and category kept


def test_a_switch_the_ledger_confirmed_goes_back_when_its_row_is_undone(owner: TestClient):
    chk = household()
    new = owner.post("/api/v1/accounts", json={"name": "Example Union Checking", "kind": "checking", "balance": 100.0}).json()["id"]
    move = owner.post("/api/v1/moves", json={"source_account_id": chk, "target_account_id": new}).json()["move"]["id"]
    household_jobs(date(2026, 10, 24))
    routes.set_today_override(date(2026, 10, 24))
    owner.get(f"/api/v1/moves/{move}")
    before = tables()
    done = upload(owner, "cu.csv", "Date,Description,Amount\n2026-10-22,PHONE,-96.00\n", account_id=str(new))
    owner.get(f"/api/v1/moves/{move}")
    with Session(engine) as s:
        assert [i.name for i in s.exec(select(MoveItem).where(MoveItem.status == "confirmed")).all()] == ["Phone"]
    owner.post(f"/api/v1/imports/{done['import_id']}/undo")
    owner.get(f"/api/v1/moves/{move}")
    assert not changed(before, tables())
