"""M50: the tax-time pack. The year's paystubs summed (gross, net, each deduction), the receipts kept by their
purchase's category, the statements kept month by month, and one .zip with a summary and the files themselves,
for the owner only."""

import io
import json
import zipfile
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine import documents as docs
from app.models import Account, Paystub, Transaction


def _year(session: Session) -> None:
    acct = Account(name="Example Checking", kind="checking")
    session.add(acct)
    session.flush()
    tx = Transaction(account_id=acct.id, date=date(2026, 3, 2), merchant="EXAMPLE PHARMACY", amount=45.0, category="Medical", row_hash="tax-1")
    session.add(tx)
    for n, day in enumerate((date(2026, 1, 15), date(2026, 1, 29))):
        session.add(Paystub(employer="Example Employer", pay_date=day, gross=2000.0, net=1500.0, stub_hash=f"stub-{n}",
                            deductions=json.dumps([{"name": "Federal tax", "amount": 200.0}, {"name": "Social Security", "amount": 124.0}])))
    session.add(Paystub(employer="Example Employer", pay_date=date(2025, 12, 31), gross=2000.0, net=1500.0, stub_hash="stub-old"))  # another year
    session.commit()
    docs.save_text(session, "Example Pharmacy receipt, total 45.00", filename="r1", title="Receipt", kind="receipt", attach_to=f"transaction:{tx.id}",
                   merchant="Example Pharmacy", amount=45.0, bought_on=date(2026, 3, 2))
    docs.save_text(session, "Example Store receipt, total 12.00", filename="r2", title="Receipt", kind="receipt", merchant="Example Store", amount=12.0, bought_on=date(2026, 4, 9))
    docs.save_text(session, "Example Checking statement, February", filename="s2", title="Statement", kind="statement", attach_to=f"account:{acct.id}", period="2026-02")


def test_the_year_is_summed_with_its_paystubs_receipts_and_statements(owner: TestClient):
    with Session(engine) as session:
        _year(session)
    out = owner.get("/api/v1/reports/custom/tax?year=2026").json()
    pay = out["paystubs"]
    assert pay["count"] == 2 and pay["gross"] == 4000.0 and pay["net"] == 3000.0
    assert pay["deductions"] == [{"name": "Federal tax", "total": 400.0, "count": 2}, {"name": "Social Security", "total": 248.0, "count": 2}]
    assert out["receipts"][0] == {"name": "Medical", "total": 45.0, "count": 1, "tax": True, "sources": out["receipts"][0]["sources"]}
    assert out["receipts"][1]["name"] == "Not matched to a purchase" and out["receipts"][1]["tax"] is False
    assert out["statements"] == [{"account": "Example Checking", "months": [m == 2 for m in range(1, 13)], "count": 1}]
    assert out["files"] == 3 and out["categories"][0]["name"] == "Medical"
    assert out["said"] == "For 2026 the app keeps 2 paystubs, $4,000.00 gross, 2 receipts (1 in a tax category), 1 statement across 1 account. The pack holds the totals and 3 files."


def test_the_pack_holds_a_summary_and_the_files_and_only_the_owner_gets_it(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        _year(session)
    got = owner.get("/api/v1/reports/custom/tax/pack?year=2026")
    assert got.status_code == 200 and got.headers["content-disposition"] == 'attachment; filename="tax-pack-2026.zip"'
    z = zipfile.ZipFile(io.BytesIO(got.content))
    names = set(z.namelist())
    assert {"README.txt", "summary.csv", "summary.html", "statements/Example Checking/2026-02.txt"} <= names
    assert any(n.startswith("receipts/Medical/2026-03-02 Example Pharmacy 45.00") for n in names)
    assert z.read("statements/Example Checking/2026-02.txt") == b"Example Checking statement, February"
    assert "pay,Gross pay,4000.0,2" in z.read("summary.csv").decode()
    assert "not tax advice" in z.read("README.txt").decode()
    reader = owner.post("/api/v1/keys", json={"name": "reader", "scopes": ["read", "confirm"], "redaction": "full"}).json()["token"]
    anon = TestClient(client.app)
    assert anon.get("/api/v1/reports/custom/tax/pack?year=2026", headers={"Authorization": f"Bearer {reader}"}).status_code == 403
    assert anon.get("/api/v1/reports/custom/tax?year=2026", headers={"Authorization": f"Bearer {reader}"}).status_code == 200
