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


SAME_AS_CSV_OFX = """<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>Example Bank</ORG></FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>55554321</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261003<TRNAMT>2240.00<FITID>A1<NAME>EXAMPLE EMPLOYER<MEMO>PAYROLL</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261006<TRNAMT>-54.12<FITID>A2<NAME>EXAMPLE GROCER</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261006<TRNAMT>-54.12<FITID>A3<NAME>EXAMPLE GROCER</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261008<TRNAMT>-41.10<FITID>A4<NAME>FUEL STOP</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261010<TRNAMT>-12.00<FITID>A5<NAME>EXAMPLE BAKERY</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


def _upload(owner: TestClient, name: str, text: str, **data) -> dict:
    return owner.post("/api/v1/imports/file", data=data, files={"file": (name, io.BytesIO(text.encode()), "text/plain")}).json()


def test_the_same_statement_in_another_format_is_not_added_twice(owner: TestClient):
    first = _upload(owner, "bank.csv", BANK_CSV, account_name="Everyday Checking")
    assert first["inserted"] == 4
    # The OFX of the same month, a day later by posting date and with other descriptions, into the same account.
    second = _upload(owner, "bank.ofx", SAME_AS_CSV_OFX, account_name="everyday checking")
    assert second["merged"] == 4 and second["inserted"] == 1 and second["account_id"] == first["account_id"]
    with Session(engine) as session:
        assert len(session.exec(select(Transaction)).all()) == 5


def test_a_file_for_a_linked_account_adds_only_what_the_link_missed(owner: TestClient):
    with Session(engine) as session:
        live = Account(name="Credit Union Checking", institution="Example CU", kind="checking", connector="plaid", external_id="acc-1")
        session.add(live)
        session.flush()
        for when, merchant, amount in ((date(2026, 10, 3), "Example Employer", -2240.0), (date(2026, 10, 6), "Example Grocer", 54.12), (date(2026, 10, 8), "Fuel Stop", 41.10)):
            session.add(Transaction(account_id=live.id, date=when, merchant=merchant, amount=amount, source="plaid", row_hash=f"plaid-{merchant}"))
        session.commit()
        live_id = live.id
    got = _upload(owner, "older.csv", BANK_CSV, account_id=str(live_id))
    # the link holds one of the two same-day grocer charges: one is matched, the other is new
    assert got["account_id"] == live_id and got["merged"] == 3 and got["inserted"] == 1 and got["accounts_created"] == 0
    with Session(engine) as session:
        rows = session.exec(select(Transaction).where(Transaction.account_id == live_id)).all()
        assert len(rows) == 4 and sum(1 for r in rows if r.amount == 54.12) == 2


def test_an_import_can_be_undone_exactly(owner: TestClient):
    from app import restore

    before = len(restore.list_points())
    first = _upload(owner, "bank.csv", BANK_CSV, account_name="Everyday Checking")
    assert first["import_id"] and len(restore.list_points()) == before + 1
    again = _upload(owner, "bank.csv", BANK_CSV, account_name="Everyday Checking")
    assert again["import_id"] is None and len(restore.list_points()) == before + 1  # nothing changed: no entry, no copy
    other = _upload(owner, "other.csv", DEBIT_CREDIT_CSV, account_name="Everyday Checking")
    history = owner.get("/api/v1/imports/history").json()["imports"]
    assert [h["id"] for h in history] == [other["import_id"], first["import_id"]] and history[1]["restore_point"]
    undone = owner.post(f"/api/v1/imports/{first['import_id']}/undo").json()
    assert undone["removed"] == 4 and undone["accounts_closed"] == 0  # the later import still uses the account
    with Session(engine) as session:
        assert len(session.exec(select(Transaction)).all()) == 2  # the later import stays
    assert owner.post(f"/api/v1/imports/{first['import_id']}/undo").status_code == 400
    owner.post(f"/api/v1/imports/{other['import_id']}/undo")
    with Session(engine) as session:
        assert session.exec(select(Transaction)).all() == [] and session.get(Account, first["account_id"]).active is False


def test_undoing_a_bills_list_puts_the_bills_back(owner: TestClient):
    first = _upload(owner, "bills.csv", BILLS_CSV)
    changed = BILLS_CSV.replace("79.99", "89.99")
    second = _upload(owner, "bills-new.csv", changed + "Example Gym,25.00,Monthly,2026-11-20,no,Fitness\n")
    assert second["inserted"] == 1 and first["import_id"] != second["import_id"]
    owner.post(f"/api/v1/imports/{second['import_id']}/undo")
    bills = {b["name"]: b for b in owner.get("/api/v1/bills").json()["bills"]}
    assert bills["Example Internet"]["amount"] == 79.99 and "Example Gym" not in bills


def test_a_restore_point_puts_the_whole_database_back(owner: TestClient, client: TestClient):
    from app import restore

    made = owner.post("/api/v1/restore-points").json()["made"]
    _upload(owner, "bank.csv", BANK_CSV, account_name="Everyday Checking")
    key = owner.post("/api/v1/keys", json={"name": "admin-key", "scopes": ["read", "admin"]})
    if key.status_code == 200:
        reader = TestClient(client.app)
        assert reader.post(f"/api/v1/restore-points/{made['name']}/restore", headers={"Authorization": f"Bearer {key.json()['token']}"}).status_code in (401, 403)
    assert owner.post("/api/v1/restore-points/not-a-point.db/restore").status_code == 404
    done = owner.post(f"/api/v1/restore-points/{made['name']}/restore").json()
    assert done["restored"] == made["name"] and done["before"]["kind"] == "before-restore"
    with Session(engine) as session:
        assert session.exec(select(Transaction)).all() == []
    # and the restore is undone the same way
    owner.post(f"/api/v1/restore-points/{done['before']['name']}/restore")
    with Session(engine) as session:
        assert len(session.exec(select(Transaction)).all()) == 4
    assert any(p["kind"] == "before-restore" for p in restore.list_points())


def test_the_daily_copy_is_taken_once_a_day():
    from app import restore
    from app.jobs.scheduler import restore_point_daily

    restore_point_daily()
    count = len([p for p in restore.list_points() if p["kind"] == "daily"])
    assert "today's copy is there" in restore_point_daily()
    assert len([p for p in restore.list_points() if p["kind"] == "daily"]) == count


def test_each_copy_reads_back_and_a_backup_manager_can_ask(capsys):
    import json as _json

    from app import restore

    made = restore.make_point("manual", "test")
    assert made["verified"] and made["tables"] > 10
    assert restore.main(["status"]) == 0
    status = _json.loads(capsys.readouterr().out)
    assert status["database"]["ok"] and status["newest"]["manual"]["name"] == made["name"] and status["latest_age_hours"] < 1
    assert restore.main(["check"]) == 0 and _json.loads(capsys.readouterr().out)["database"]["result"] == "ok"
    assert restore.main(["restore", made["name"]]) == 2  # restoring stays in the app
