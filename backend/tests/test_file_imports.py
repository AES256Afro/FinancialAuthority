"""Files from any bank or vendor: a CSV with its columns worked out, debit and credit columns, an OFX statement,
a holdings file into a 401(k); de-duplication on a second import; the preview writes nothing."""

import io
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors.files import detect, parse_csv_rows, parse_ofx, sniff_holdings_csv
from app.db import engine
from app.models import Account, BalanceSnapshot, Holding, Transaction

BANK_CSV = (
    "Transaction Date,Posted Date,Description,Amount,Category\n"
    "10/02/2026,10/03/2026,EXAMPLE EMPLOYER PAYROLL,2240.00,Income\n"
    "10/05/2026,10/06/2026,EXAMPLE GROCER 123,-54.12,Groceries\n"
    "10/05/2026,10/06/2026,EXAMPLE GROCER 123,-54.12,Groceries\n"
    "10/07/2026,10/08/2026,\"FUEL STOP, 55\",\"($41.10)\",Gas\n"
)
DEBIT_CREDIT_CSV = "Date,Details,Debit,Credit\n2026-10-09,EXAMPLE TAQUERIA,18.40,\n2026-10-08,TRANSFER FROM SAVINGS,,200.00\n"
OFX = """OFXHEADER:100
DATA:OFXSGML
<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>Example Credit Union</ORG></FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>000123456789</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261009120000<TRNAMT>-18.40<FITID>2026100901<NAME>EXAMPLE TAQUERIA</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261002<TRNAMT>2240.00<FITID>2026100201<NAME>EXAMPLE EMPLOYER<MEMO>PAYROLL</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
HOLDINGS_CSV = "Investment Name,Symbol,Shares,Share Price,Total Value\nExample Target 2055 Fund,EXTGT,412.345,52.10,\"$21,483.17\"\nExample Bond Index,EXBND,300,11.09,3327.38\n"
INVEST_OFX = """<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>EX0001</UNIQUEID></SECID><UNITS>412.345<UNITPRICE>52.10<MKTVAL>21483.17<DTPRICEASOF>20261013</INVPOS></POSMF>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>
<SECLISTMSGSRSV1><SECLIST><MFINFO><SECINFO><SECID><UNIQUEID>EX0001</UNIQUEID></SECID><SECNAME>Example Target 2055 Fund<TICKER>EXTGT</SECINFO></MFINFO></SECLIST></SECLISTMSGSRSV1></OFX>"""


def test_csv_sniffing_and_sign_conventions():
    found = detect(BANK_CSV)
    assert found["kind"] == "csv" and found["mapping"]["date"] == "Transaction Date" and found["mapping"]["amount"] == "Amount" and found["mapping"]["description"] == "Description"
    assert found["sign"] == "bank" and found["ready"] and found["total"] == 4
    rows = parse_csv_rows(BANK_CSV)
    assert rows[0]["amount"] == -2240.0 and rows[1]["amount"] == 54.12 and rows[3]["amount"] == 41.10  # money out positive; parentheses are negative in the file
    assert rows[3]["merchant"] == "FUEL STOP, 55" and rows[0]["date"] == date(2026, 10, 2)
    split = parse_csv_rows(DEBIT_CREDIT_CSV)
    assert [r["amount"] for r in split] == [18.4, -200.0]


def test_ofx_statement_and_positions():
    parsed = parse_ofx(OFX)
    assert parsed["vendor"] == "Example Credit Union" and parsed["account_type"] == "checking" and parsed["account_id"].endswith("6789")
    assert [(r["date"].isoformat(), r["amount"], r["description"], r["fitid"]) for r in parsed["rows"]] == [("2026-10-09", 18.4, "EXAMPLE TAQUERIA", "2026100901"), ("2026-10-02", -2240.0, "EXAMPLE EMPLOYER PAYROLL", "2026100201")]
    positions = parse_ofx(INVEST_OFX)["positions"]
    assert positions[0]["ticker"] == "EXTGT" and positions[0]["kind"] == "mutual fund" and positions[0]["value"] == 21483.17 and positions[0]["as_of"] == date(2026, 10, 13)
    holdings = sniff_holdings_csv(HOLDINGS_CSV)
    assert holdings["ready"] and [h["ticker"] for h in holdings["positions"]] == ["EXTGT", "EXBND"] and holdings["positions"][0]["value"] == 21483.17


def test_import_routes_preview_import_dedupe_and_holdings(owner: TestClient):
    preview = owner.post("/api/v1/imports/preview", files={"file": ("bank.csv", io.BytesIO(BANK_CSV.encode()), "text/csv")}).json()
    assert preview["kind"] == "csv" and preview["ready"] and len(preview["sample"]) == 4 and preview["sample"][1]["amount"] == 54.12
    with Session(engine) as session:
        assert session.exec(select(Transaction)).all() == []  # the preview wrote nothing
    first = owner.post("/api/v1/imports/file", data={"account_name": "Everyday Checking", "institution": "Example Bank", "kind": "checking"}, files={"file": ("bank.csv", io.BytesIO(BANK_CSV.encode()), "text/csv")}).json()
    assert first["inserted"] == 4 and first["duplicates"] == 0 and first["accounts_created"] == 1
    again = owner.post("/api/v1/imports/file", data={"account_name": "Everyday Checking"}, files={"file": ("bank.csv", io.BytesIO(BANK_CSV.encode()), "text/csv")}).json()
    assert again["inserted"] == 0 and again["duplicates"] == 4
    ofx = owner.post("/api/v1/imports/file", data={}, files={"file": ("statement.qfx", io.BytesIO(OFX.encode()), "application/x-ofx")}).json()
    assert ofx["inserted"] == 2 and "Example Credit Union checking" in ofx["summary"]
    with Session(engine) as session:
        rows = session.exec(select(Transaction).where(Transaction.source == "file")).all()
        assert len(rows) == 6 and sum(1 for r in rows if r.is_income) == 2
        accounts = {a.name: a for a in session.exec(select(Account).where(Account.connector == "file")).all()}
        assert set(accounts) == {"Everyday Checking", "Example Credit Union checking"} and accounts["Everyday Checking"].institution == "Example Bank"
    bad = owner.post("/api/v1/imports/file", data={"account_name": "x"}, files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert bad.status_code == 400

    made = owner.post("/api/v1/investments", json={"name": "Work 401(k)", "institution": "Example Retirement Co", "subtype": "401k"}).json()
    plan_id = next(i["id"] for i in made["items"] if i["name"] == "Work 401(k)")
    held = owner.post(f"/api/v1/imports/holdings/{plan_id}", data={"as_of": "2026-10-13"}, files={"file": ("holdings.csv", io.BytesIO(HOLDINGS_CSV.encode()), "text/csv")}).json()
    assert held["holdings"] == 2 and held["total"] == round(21483.17 + 3327.38, 2)
    with Session(engine) as session:
        account = session.get(Account, plan_id)
        assert account.balance == held["total"] and account.balance_at.date() == date(2026, 10, 13)
        assert len(session.exec(select(Holding).where(Holding.account_id == plan_id)).all()) == 2
        assert session.exec(select(BalanceSnapshot).where(BalanceSnapshot.account_id == plan_id)).one().date == date(2026, 10, 13)
    again = owner.post(f"/api/v1/imports/holdings/{plan_id}", data={}, files={"file": ("positions.ofx", io.BytesIO(INVEST_OFX.encode()), "application/x-ofx")}).json()
    assert again["holdings"] == 1
    with Session(engine) as session:
        assert len(session.exec(select(Holding).where(Holding.account_id == plan_id)).all()) == 3  # EX0001 is a different id than file:extgt; both kept
    overview = owner.get("/api/v1/investments").json()
    assert next(i for i in overview["items"] if i["id"] == plan_id)["holdings"]


BILLS_CSV = (
    "Name,Amount,Frequency,Next Due,Autopay,Category\n"
    "Example Internet,79.99,Monthly,2026-11-03,Yes,Internet\n"
    "Example Streaming Co,15.99,monthly,11/15/2026,no,Streaming\n"
    "Example Insurance,142.50,every 6 months,,no,Insurance\n"
)


def test_a_bills_list_lands_on_the_register(owner: TestClient):
    """A sheet or export with a name, an amount and how often (Rocket Money's recurring page copied out, a
    budgeting app, a spreadsheet) is a bills list, not transactions: each row becomes a bill with its due day."""
    found = detect(BILLS_CSV)
    assert found["kind"] == "bills" and found["ready"] and found["mapping"]["cadence"] == "Frequency" and found["mapping"]["next_date"] == "Next Due"
    assert detect(BANK_CSV)["kind"] == "csv" and detect(DEBIT_CREDIT_CSV)["kind"] == "csv"  # bank exports still read as transactions
    preview = owner.post("/api/v1/imports/preview", files={"file": ("bills.csv", io.BytesIO(BILLS_CSV.encode()), "text/csv")}).json()
    assert preview["kind"] == "bills" and preview["total"] == 3 and preview["sample"][0]["name"] == "Example Internet" and preview["sample"][0]["due_day"] == 3
    out = owner.post("/api/v1/imports/file", files={"file": ("bills.csv", io.BytesIO(BILLS_CSV.encode()), "text/csv")}).json()
    assert out["inserted"] == 2 and "1 without a due day skipped" in out["summary"]
    bills = {b["name"]: b for b in owner.get("/api/v1/bills").json()["bills"]}
    assert bills["Example Internet"]["due_day"] == 3 and bills["Example Internet"]["autopay"] is True and bills["Example Internet"]["kind"] == "bill"
    assert bills["Example Streaming Co"]["due_day"] == 15 and bills["Example Streaming Co"]["kind"] == "subscription"
    again = owner.post("/api/v1/imports/file", files={"file": ("bills.csv", io.BytesIO(BILLS_CSV.encode()), "text/csv")}).json()
    assert again["inserted"] == 0 and "2 updated" in again["summary"]
