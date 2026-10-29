"""Sweep 15, part 1: leave and come back.

The synthetic household after a simulated month (the quarter harness, its taps, plus a split, tags, a rule and a
budget) was exported in every format and brought back into a fresh install through every path the app offers. The
off-box copy came back with every figure the same. The restore point did too, but for what it says it keeps as now
(the people and their accounts' sharing). The partial formats lost more than their shape had to. The findings,
reduced:

- the app's own transactions.csv, read back through the file importer, flipped the sign of every row (its
  description column is empty, so the sniff never saw the payroll and took the bank's sign), named every merchant
  "" and put every account's rows in one account called "Imported", with the flags, notes and tags dropped;
- a statement file of two accounts (the app's own OFX, or a bank's checking-and-savings download) put every row in
  the first account, and an XML statement's "A&amp;B" landed as written;
- a file row named "Paycheck advance" counted as pay;
- the Rocket Money export loaded back beside the bank link it came from added every row again under new accounts,
  and its tags were never written or read;
- the app's own OFX read back beside its accounts added every row again;
- the app's own bills.csv, read back as a bills list, brought a removed bill back, dropped each bill's start and end
  (the next home's rent and its one-off move-in check became monthly bills from today) and each anchor date.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import auth, offbox, restore
from app.config import settings
from app.connectors.files import parse_ofx
from app.connectors.rocketmoney import export_csv
from app.db import bump_version, engine, init_db
from app.engine.edits import set_splits, update
from app.engine.household import seed
from app.exports import data as data_formats
from app.models import Account, Bill, Transaction

TODAY = date(2026, 10, 14)


def _ledger(splits: bool = False) -> list[tuple]:
    with Session(engine) as s:
        names = {a.id: (a.name, a.kind) for a in s.exec(select(Account)).all()}
        return sorted((t.date.isoformat(), names[t.account_id], round(t.amount, 2), t.merchant, t.category, t.is_transfer, t.is_card_payment, t.is_income, t.is_ignored, t.note, t.tags)
                      for t in s.exec(select(Transaction)).all())


def _fresh(owner: TestClient) -> None:
    """A new, empty install: the database dropped and made again, signed in again."""
    engine.dispose()
    SQLModel.metadata.drop_all(engine)
    init_db()
    bump_version()
    auth.reset_lockouts()
    owner.cookies.clear()
    assert owner.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"}).status_code == 200


def _household() -> None:
    with Session(engine) as s:
        seed(s)
        rows = s.exec(select(Transaction).order_by(Transaction.id)).all()
        update(s, rows[3].id, tags=["example tag", "shared"], note="a note", hidden=True)
        set_splits(s, rows[5].id, [{"category": rows[5].category, "amount": round(rows[5].amount - 1, 2), "note": ""}, {"category": "Household", "amount": 1.0, "note": ""}])


def _post(owner: TestClient, name: str, body: bytes, path: str = "/api/v1/imports/file") -> dict:
    r = owner.post(path, files={"file": (name, io.BytesIO(body), "text/plain")})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_apps_own_transactions_csv_comes_back_as_it_was_once_and_not_beside_itself(owner: TestClient):
    _household()
    before = _ledger()
    with Session(engine) as s:
        csv = zipfile.ZipFile(io.BytesIO(data_formats.csv_zip(s, TODAY))).read("transactions.csv")
    beside = _post(owner, "transactions.csv", csv)
    assert beside["inserted"] == 0 and _ledger() == before, beside["summary"]  # beside the rows it came from: nothing new
    _fresh(owner)
    first = _post(owner, "transactions.csv", csv)
    assert _ledger() == before, (first["summary"], [x for x in _ledger() if x not in before][:3])  # signs, merchants, accounts and their kinds, flags, notes, tags
    again = _post(owner, "transactions.csv", csv)
    assert again["inserted"] == 0 and _ledger() == before


def test_a_statement_of_two_accounts_lands_each_in_its_own_account(owner: TestClient):
    ofx = ("<OFX><BANKMSGSRSV1>"
           "<STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>11112222</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM><BANKTRANLIST>"
           "<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261001</DTPOSTED><TRNAMT>-12.50</TRNAMT><FITID>c1</FITID><NAME>EXAMPLE A&amp;B MARKET</NAME></STMTTRN>"
           "</BANKTRANLIST></STMTRS></STMTTRNRS>"
           "<STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>33334444</ACCTID><ACCTTYPE>SAVINGS</ACCTTYPE></BANKACCTFROM><BANKTRANLIST>"
           "<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261002</DTPOSTED><TRNAMT>50.00</TRNAMT><FITID>s1</FITID><NAME>EXAMPLE INTEREST</NAME></STMTTRN>"
           "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1>"
           "<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CCACCTFROM><ACCTID>55556666</ACCTID></CCACCTFROM><BANKTRANLIST>"
           "<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261003</DTPOSTED><TRNAMT>-9.00</TRNAMT><FITID>k1</FITID><NAME>EXAMPLE COFFEE</NAME></STMTTRN>"
           "</BANKTRANLIST></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>")
    assert [(r["account"], r["merchant"]) for r in parse_ofx(ofx)["rows"]] == [("11112222", "EXAMPLE A&B MARKET"), ("33334444", "EXAMPLE INTEREST"), ("55556666", "EXAMPLE COFFEE")]
    landed = _post(owner, "statement.ofx", ofx.encode())
    assert landed["inserted"] == 3 and landed["accounts_created"] == 3, landed["summary"]
    assert sorted(_ledger()) == sorted([("2026-10-01", ("Account 2222", "checking"), 12.5, "EXAMPLE A&B MARKET", "Uncategorized", False, False, False, False, "", ""),
                                        ("2026-10-02", ("Account 4444", "checking"), -50.0, "EXAMPLE INTEREST", "Uncategorized", False, False, False, False, "", ""),
                                        ("2026-10-03", ("Account 6666", "card"), 9.0, "EXAMPLE COFFEE", "Uncategorized", False, False, False, False, "", "")])
    assert _post(owner, "statement.ofx", ofx.encode())["inserted"] == 0


def test_a_paycheck_advance_in_a_file_is_not_pay(owner: TestClient):
    csv = "Date,Description,Amount\n2026-10-01,EXAMPLE PAYCHECK ADVANCE,150.00\n2026-10-02,EXAMPLE EMPLOYER PAYROLL,2000.00\n"
    _post(owner, "bank.csv", csv.encode())
    with Session(engine) as s:
        assert {t.merchant: t.is_income for t in s.exec(select(Transaction)).all()} == {"EXAMPLE PAYCHECK ADVANCE": False, "EXAMPLE EMPLOYER PAYROLL": True}


def test_the_apps_own_ofx_read_back_beside_its_accounts_adds_nothing(owner: TestClient):
    _household()
    before = _ledger()
    with Session(engine) as s:
        ofx = data_formats.ofx(s, TODAY)
    landed = _post(owner, "export.ofx", ofx)
    assert landed["inserted"] == 0 and landed["accounts_created"] == 0 and _ledger() == before, landed["summary"]


def test_the_rocket_money_export_comes_back_with_its_tags_and_not_beside_the_link(owner: TestClient):
    _household()
    with Session(engine) as s:
        shape = export_csv(s)
    before = _ledger()
    beside = _post(owner, "rm.csv", shape.encode(), "/api/v1/imports/rocketmoney")
    assert beside["inserted"] == 0 and _ledger() == before, beside["summary"]
    _fresh(owner)
    _post(owner, "rm.csv", shape.encode(), "/api/v1/imports/rocketmoney")
    with Session(engine) as s:
        assert {t.tags for t in s.exec(select(Transaction)).all()} >= {"example tag,shared"}
    assert _post(owner, "rm.csv", shape.encode(), "/api/v1/imports/rocketmoney")["inserted"] == 0


def test_the_apps_own_bills_csv_keeps_each_bills_dates_and_leaves_a_removed_bill_off(owner: TestClient):
    with Session(engine) as s:
        s.add(Bill(name="Example Next Rent", amount=1295.0, cadence="monthly", due_day=1, starts_on=date(2027, 2, 1)))
        s.add(Bill(name="Example Move-in Check", amount=1920.0, cadence="monthly", due_day=13, starts_on=date(2027, 1, 13), ends_on=date(2027, 1, 13)))
        s.add(Bill(name="Example Registration", amount=180.0, cadence="yearly", due_day=10, anchor_date=date(2027, 3, 10)))
        s.add(Bill(name="Example Old Gym", amount=30.0, cadence="monthly", due_day=5, active=False))
        s.commit()
        bills = zipfile.ZipFile(io.BytesIO(data_formats.csv_zip(s, TODAY))).read("bills.csv")
    _fresh(owner)
    _post(owner, "bills.csv", bills)
    with Session(engine) as s:
        got = {b.name: (b.starts_on, b.ends_on, b.anchor_date) for b in s.exec(select(Bill)).all()}
    assert got == {"Example Next Rent": (date(2027, 2, 1), None, None), "Example Move-in Check": (date(2027, 1, 13), date(2027, 1, 13), None),
                   "Example Registration": (None, None, date(2027, 3, 10))}


def test_the_off_box_copy_comes_back_into_a_fresh_install_with_every_figure_the_same(owner: TestClient, tmp_path):
    """A guard: the one path the app presents as whole came back whole on the month-old household."""
    from app.engine.paychecks import paycheck_views
    from app.engine.payoff import payoff_plan
    from app.engine.snapshot import finance_snapshot

    def figures() -> str:
        bump_version()
        with Session(engine) as s:
            snap = finance_snapshot(s, TODAY)
            snap.pop("fresh", None)
            return json.dumps({"snap": snap, "windows": paycheck_views(s, TODAY, snap=snap)["windows"], "payoff": payoff_plan(s, TODAY)}, sort_keys=True, default=str)

    _household()
    with Session(engine) as s:
        offbox.save(s, folder=str(tmp_path), passphrase="example passphrase here", every_days=7)
        copy = offbox.copy_now(s)
    before, ledger = figures(), _ledger()
    _fresh(owner)
    for p in restore.folder().glob("*.db"):
        p.unlink()
    offbox.open_copy(tmp_path / copy["file"], "example passphrase here", tmp_path / "opened.db")
    engine.dispose()
    shutil.copy(tmp_path / "opened.db", settings.db_path)
    init_db()
    assert _ledger() == ledger and figures() == before
