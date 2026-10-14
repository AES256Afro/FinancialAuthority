from datetime import date
from email.message import EmailMessage

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crypto
from app.connectors import CONNECTORS
from app.connectors.mail import MailConnector
from app.db import engine
from app.engine.household import seed
from app.engine.paystubs import record_paystub
from app.models import MailAccount, Paystub
from app.parsers import paystub as paystub_parser
from app.parsers.paystub import parse_paystub_text

TODAY = date(2026, 10, 14)

STUB_TEXT = """Example Payroll Services
Example Employer Inc
EARNINGS STATEMENT
Pay Period: 09/28/2026 - 10/11/2026     Pay Date: 10/16/2026
Earnings            Rate    Hours     Current      YTD
Regular            37.26    80.00   2,980.77   59,615.40
Gross Pay                            2,980.77   59,615.40
Deductions
Federal Income Tax                     298.08    5,961.60
Social Security                        184.81    3,696.20
Medicare                                43.22      864.40
Medical                                112.50    2,250.00
401(k)                                  89.42    1,788.40
Dental                                  12.74      254.80
Net Pay                              2,240.00   44,800.00
"""


def raw_with_pdf(from_: str, subject: str) -> bytes:
    m = EmailMessage()
    m["From"] = from_
    m["To"] = "owner@example.test"
    m["Subject"] = subject
    m["Date"] = "Fri, 16 Oct 2026 06:02:00 -0500"
    m["Message-ID"] = "<stub-1@example.test>"
    m.set_content("Your pay statement is attached.")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="paystub-2026-10-16.pdf")
    return bytes(m)


MESSAGES = {1: raw_with_pdf("Me <owner@example.test>", "Fw: pay stubs")}


class FakeClient:
    def __init__(self, host, port, username, password, use_ssl=True, starttls=False):  # noqa: ANN001
        self.folder = None

    def connect(self) -> None:
        pass

    def folders(self) -> list[str]:
        return ["INBOX"]

    def select_readonly(self, folder: str) -> tuple[int, int]:
        self.folder = folder
        return (1, 2)

    def new_uids(self, since_uid: int) -> list[int]:
        return [u for u in MESSAGES if u > since_uid]

    def fetch_raw(self, uid: int) -> bytes:
        return MESSAGES[uid]

    def close(self) -> None:
        pass


def test_parse_paystub_text_reads_every_line():
    fields = parse_paystub_text(STUB_TEXT, TODAY)
    assert fields is not None
    assert fields["gross"] == 2980.77 and fields["net"] == 2240.0 and fields["pay_date"] == "2026-10-16"
    assert fields["period_start"] == "2026-09-28" and fields["period_end"] == "2026-10-11"
    assert fields["hours"] == 80.0
    names = {d["name"]: d["amount"] for d in fields["deductions"]}
    assert names["Federal income tax"] == 298.08 and names["Social Security"] == 184.81 and names["Medicare"] == 43.22
    assert names["Health insurance"] == 112.50 and names["401(k)"] == 89.42 and names["Dental"] == 12.74
    assert fields["confidence"] >= 0.9
    assert parse_paystub_text("Weekend deals, 20% off", TODAY) is None


def test_record_matches_the_deposit_and_flags_a_short_one():
    with Session(engine) as session:
        seed(session)
        recent = session.exec(select(Paystub).order_by(Paystub.pay_date.desc())).all()
        assert len(recent) == 6 and all(p.transaction_id is not None for p in recent)
        short = next(p for p in recent if p.pay_date == date(2026, 8, 21))
        assert short.difference is not None and short.difference < -100
        stub = record_paystub(session, {"pay_date": "2026-10-16", "net": 2240.0, "gross": 2980.77, "deductions": [], "employer": "Example Employer"}, source="test", fact_id=None, today=TODAY)
        assert stub.expected_net == 2240.0 and stub.difference == 0.0
        again = record_paystub(session, {"pay_date": "2026-10-16", "net": 2240.0, "gross": 2980.77}, source="test", fact_id=None, today=TODAY)
        assert again.id == stub.id


def test_pdf_attachment_becomes_a_paystub_fact_and_confirms(owner: TestClient, monkeypatch):
    monkeypatch.setattr(paystub_parser, "pdf_text", lambda data: STUB_TEXT)
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", FakeClient)
    with Session(engine) as session:
        seed(session)
        account = MailAccount(label="Proton", kind="proton", host="bridge", port=1143, username="o", password_enc=crypto.encrypt("x"), use_ssl=False, starttls=True)
        session.add(account)
        session.commit()
        result = MailConnector(client_factory=FakeClient).sync(session, today=TODAY)
        assert result.ok and result.inserted == 1
    pending = owner.get("/api/v1/facts").json()
    stub_fact = next(f for f in pending["facts"] if f["kind"] == "paystub")
    assert stub_fact["fields"]["net"] == 2240.0 and stub_fact["fields"]["attachment"] == "paystub-2026-10-16.pdf"
    done = owner.post(f"/api/v1/facts/{stub_fact['id']}/confirm", json={"apply": True}).json()
    assert done["applied_to"].startswith("paystub:")
    listed = owner.get("/api/v1/paystubs").json()
    assert listed["last"]["pay_date"] == "2026-10-16" and listed["last"]["net"] == 2240.0 and len(listed["last"]["deductions"]) == 6
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["paystubs"]["last"]["pay_date"] == "2026-10-16"
    assert any("paystub was" in b["text"] for b in snap["brief"]) is False  # the latest stub is not short
