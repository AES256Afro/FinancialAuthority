"""Semi-monthly pay: paydays on the 15th and the last day, moved before a weekend; a bill paid from the
end-of-month check sits in that check's window whatever its due date; the usual net is kept per position of the
month; the columns earnings-statement layout reads deterministically; a stub handed over as a file lands on
Needs you. Every figure here is synthetic."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.cadence import is_semimonthly, next_paydays, payday_for, payday_position
from app.engine.decisions import billing_date_suggestions
from app.engine.paystubs import record_paystub
from app.engine.projection import next_due
from app.engine.recurring import detect
from app.engine.snapshot import finance_snapshot
from app.models import Account, Bill, Income
from app.parsers import paystub as paystub_parser
from app.parsers.paystub import parse_paystub_text

TODAY = date(2026, 10, 15)

STATEMENT = """Earnings Statement DOE, JANE Q
Pay Date: 10/15/2026 Company: 0AB12 - EXAMPLE STAFFING LLC Emp #: Z99Z
Period Start: 09/16/2026 100 EXAMPLE WAY Dept:  5802 - EXAMPLE CLIENT
Period End: 09/30/2026 SOMEWHERE  VA  20191    (555) 555-0100
Pay Basis: Hourly
Rate Hours/Units Current Period Year To Date
Earnings
Regular 31.25 88.00 2750.00 27500.00
Gross Pay 88.00 2750.00 27500.00
W/H Taxes
Federal W/H(S) 301.12 3011.20
Medicare 39.88 398.80
Social Security 170.50 1705.00
Deductions
SUPPORT ORDER - MED $ 1 50.00 500.00
Dental Pre-Tax 9.07 90.70
Medical Pre-Tax 120.00 1200.00
401K Pre-Tax 87.50 875.00
Net Pay 1971.93 19719.30 Voucher No.  123456789DD
Net Pay Distribution
Direct Deposit Net Check 1971.93 19719.30 A/C:1234
Voucher No.  123456789DD
EXAMPLE STAFFING LLC
100 EXAMPLE WAY SUITE 1 DATE: 10/15/2026
Net Pay: 1971.93
One Thousand Nine Hundred Seventy One And 93/100 Dollars
"""


def test_semimonthly_paydays_move_before_weekends():
    days = next_paydays(date(2026, 10, 15), "semimonthly", 6)
    assert [d.isoformat() for d in days] == ["2026-10-15", "2026-10-30", "2026-11-13", "2026-11-30", "2026-12-15", "2026-12-31"]
    assert next_paydays(date(2026, 10, 16), "biweekly", 3) == [date(2026, 10, 16), date(2026, 10, 30), date(2026, 11, 13)]
    assert next_paydays(date(2026, 1, 31), "monthly", 3) == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert next_due(31, date(2026, 10, 15)) == date(2026, 10, 31) and next_due(1, date(2026, 10, 15)) == date(2026, 11, 1) and next_due(31, date(2026, 11, 2)) == date(2026, 11, 30)
    assert payday_for(date(2026, 11, 1), "end") == date(2026, 10, 30) and payday_for(date(2026, 11, 20), "mid") == date(2026, 11, 13)
    assert payday_position(date(2026, 10, 30)) == "end" and payday_position(date(2026, 11, 13)) == "mid"
    paid = [date(2026, 6, 15), date(2026, 6, 30), date(2026, 7, 15), date(2026, 7, 31), date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]
    assert is_semimonthly(paid)
    assert not is_semimonthly([date(2026, 6, 19) + timedelta(days=14 * i) for i in range(8)])


def test_detector_calls_the_fifteenth_and_month_end_semimonthly():
    dates = [date(2026, 6, 15), date(2026, 6, 30), date(2026, 7, 15), date(2026, 7, 31), date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]
    rows = [{"date": d, "merchant": "Example Staffing Payroll", "amount": -(2000.0 if d.day > 20 else 2200.0), "category": "Income"} for d in dates]
    found = detect(rows, date(2026, 10, 2), income=True)
    assert len(found) == 1 and found[0]["cadence"] == "semimonthly" and found[0]["next_expected"] == date(2026, 10, 15) and found[0]["kind"] == "income"


def test_rent_paid_from_the_end_of_month_check_is_not_due_before_payday(owner: TestClient):
    with Session(engine) as session:
        session.add(Income(name="Example Staffing", cadence="semimonthly", expected_net=2200.0, expected_net_end=2000.0, next_date=date(2026, 10, 30)))
        session.add(Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=1000.0, connector="demo", external_id="t:chk"))
        session.add(Account(name="Rainy Day", institution="Example Bank", kind="savings", balance=5000.0, connector="demo", external_id="t:sav"))
        session.add(Bill(name="Example Apartments", kind="bill", amount=1500.0, amount_mode="fixed", cadence="monthly", due_day=1, paid_from="end", category="Rent"))
        session.add(Bill(name="Example Internet", kind="bill", amount=80.0, amount_mode="fixed", cadence="monthly", due_day=20, category="Internet"))
        session.add(Bill(name="Example Power", kind="bill", amount=120.0, amount_mode="fixed", cadence="monthly", due_day=31, category="Electricity"))
        session.commit()
        snap = finance_snapshot(session, TODAY)
        moves = billing_date_suggestions(session, TODAY)
    assert snap["next_paycheck"]["date"] == "2026-10-30" and snap["next_paycheck"]["amount"] == 2000.0 and snap["next_paycheck"]["cadence"] == "semimonthly"
    assert [p["date"] for p in snap["paydays"]] == ["2026-10-30", "2026-11-13", "2026-11-30", "2026-12-15"]
    assert [p["amount"] for p in snap["paydays"]] == [2000.0, 2200.0, 2000.0, 2200.0]
    assert {e["name"] for e in snap["due_before_payday"]["items"]} == {"Example Internet"}  # rent rides the end-of-month check; the power bill is due after payday
    groups = {g["key"]: g for g in snap["due_before_payday"]["groups"]}
    assert list(groups) == ["bill"] and groups["bill"]["total"] == 80.0 and groups["bill"]["names"] == ["Example Internet"]
    nxt = snap["due_before_payday"]["next_check"]
    assert nxt["position"] == "end" and nxt["start"] == "2026-10-30" and nxt["top"][0]["name"] == "Example Apartments" and nxt["total"] == 1620.0
    window = {e["name"]: e["date"] for e in snap["window"]["items"]}
    assert window["Example Apartments"] == "2026-11-01" and window["Example Power"] == "2026-10-31" and snap["window"]["end"] == "2026-11-12"
    assert snap["windows"][1]["start"] == "2026-11-13" and snap["windows"][0]["paycheck"] == 2000.0 and snap["windows"][1]["paycheck"] == 2200.0  # each window shows its own check
    # Savings is kept, not spent: the plan runs on checking. Safe to spend until payday is checking, less the
    # internet bill due before it, less the buffer; the end-of-month check covers its own window, so no shortfall.
    assert snap["cash"] == 1000.0 and snap["cash_all"] == 6000.0 and snap["savings"] == 5000.0
    assert snap["safe_to_spend"]["through"] == "2026-10-29" and snap["safe_to_spend"]["parts"]["next_window_shortfall"] == 0.0
    assert snap["safe_to_spend"]["amount"] == round(1000.0 - 80.0 - snap["buffer"], 2)
    assert moves["baseline"]["low_date"] >= "2026-10-15"
    bills = {b["name"]: b for b in owner.get("/api/v1/bills").json()["bills"]}
    assert bills["Example Apartments"]["paid_from"] == "end" and bills["Example Power"]["due_day"] == 31
    changed = owner.put(f"/api/v1/bills/{bills['Example Internet']['id']}", json={"paid_from": "mid", "due_day": 31}).json()
    assert changed["paid_from"] == "mid" and changed["due_day"] == 31
    assert owner.put(f"/api/v1/bills/{bills['Example Internet']['id']}", json={"paid_from": "whenever"}).status_code == 400


def test_usual_net_is_kept_per_position_for_a_semimonthly_paycheck():
    with Session(engine) as session:
        income = Income(name="Example Staffing", cadence="semimonthly", expected_net=1900.0, next_date=date(2026, 10, 30))
        session.add(income)
        session.commit()
        first = record_paystub(session, {"net": 1800.4, "gross": 2500.0, "pay_date": "2026-09-30"}, "upload", None, TODAY)
        second = record_paystub(session, {"net": 1971.93, "gross": 2750.0, "pay_date": "2026-10-15"}, "upload", None, TODAY)
        session.refresh(income)
        assert income.expected_net_end == 1800.4 and income.expected_net == 1971.93  # the first stub in each position sets its figure
        assert first.expected_net == 1900.0 and second.expected_net == 1900.0  # each stub was judged against what was known when it landed
        third = record_paystub(session, {"net": 1810.0, "gross": 2500.0, "pay_date": "2026-10-30"}, "upload", None, TODAY)
        assert third.expected_net == 1800.4  # an end-of-month check is judged against end-of-month checks
        from app.engine.paystubs import paystubs_overview

        overview = paystubs_overview(session)
        assert overview["short"] == [] and all(abs(p["difference"]) < 20 for p in overview["recent"])  # judged afresh by position: none is short


def test_columns_layout_reads_every_figure():
    stub = parse_paystub_text(STATEMENT, TODAY)
    assert stub["layout"] == "columns" and stub["net"] == 1971.93 and stub["gross"] == 2750.0 and stub["hours"] == 88.0 and stub["rate"] == 31.25
    assert stub["pay_date"] == "2026-10-15" and stub["period_start"] == "2026-09-16" and stub["period_end"] == "2026-09-30" and stub["pay_basis"] == "hourly"
    assert stub["employer"] == "EXAMPLE STAFFING LLC" and stub["deposit_last4"] == "1234" and stub["net_ytd"] == 19719.3 and stub["gross_ytd"] == 27500.0
    by_name = {d["name"]: d for d in stub["deductions"]}
    assert by_name["Federal income tax"]["amount"] == 301.12 and by_name["Federal income tax"]["tax"] is True and by_name["Federal income tax"]["ytd"] == 3011.2
    assert by_name["401(k)"]["amount"] == 87.5 and by_name["401(k)"]["pre_tax"] is True and by_name["Health insurance"]["pre_tax"] is True
    assert by_name["SUPPORT ORDER - MED $ 1"]["amount"] == 50.0 and by_name["SUPPORT ORDER - MED $ 1"]["tax"] is False
    assert abs(stub["gross"] - sum(d["amount"] for d in stub["deductions"]) - stub["net"]) < 0.01
    assert stub["confidence"] >= 0.9 and stub["earnings"][0]["hours"] == 88.0


def test_a_paystub_file_lands_on_needs_you(owner: TestClient, monkeypatch):
    monkeypatch.setattr(paystub_parser, "pdf_text", lambda data: STATEMENT)
    out = owner.post("/api/v1/imports/paystub", files={"file": ("stub.pdf", b"%PDF-1.4 fake", "application/pdf")}).json()
    assert out["ok"] and out["net"] == 1971.93 and out["pay_date"] == "2026-10-15"
    facts = owner.get("/api/v1/facts").json()["facts"]
    fact = next(f for f in facts if f["kind"] == "paystub")
    assert fact["fields"]["attachment"] == "stub.pdf" and fact["fields"]["source"] == "upload" and fact["message"] is None
    done = owner.post(f"/api/v1/facts/{fact['id']}/confirm", json={"apply": True}).json()
    assert done["applied_to"].startswith("paystub:")
    listed = owner.get("/api/v1/paystubs").json()
    assert listed["last"]["net"] == 1971.93 and listed["last"]["employer"] == "EXAMPLE STAFFING LLC"
    refused = owner.post("/api/v1/imports/paystub", files={"file": ("notes.txt", b"just a note", "text/plain")})
    assert refused.status_code == 400


def test_a_paycheck_first_called_biweekly_moves_to_semimonthly_without_a_twin():
    from app.jobs.detect import run_detection
    from app.models import RecurringSeries, Transaction

    dates = [date(2026, 6, 15), date(2026, 6, 30), date(2026, 7, 15), date(2026, 7, 31), date(2026, 8, 14), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]
    with Session(engine) as session:
        account = Account(name="Everyday Checking", institution="Example Bank", kind="checking", balance=900.0, connector="demo", external_id="t:chk2")
        session.add(account)
        session.flush()
        for i, d in enumerate(dates):
            session.add(Transaction(account_id=account.id, date=d, merchant="Example Staffing Payroll", raw_name="EXAMPLE STAFFING PAYROLL", description="ACH", amount=-(2000.0 if d.day > 20 else 2200.0), category="Income", is_income=True, source="demo", source_ref=str(i), row_hash=f"semi-{i}"))
        series = RecurringSeries(merchant_key="example staffing payroll", merchant="Example Staffing Payroll", category="Income", cadence="biweekly", typical_amount=2100.0, latest_amount=2000.0, charges_seen=8, first_seen=dates[0], last_date=dates[-1], next_expected=date(2026, 10, 14), kind="income", confirmed=True)
        session.add(series)
        session.flush()
        session.add(Income(name="Example Staffing", cadence="biweekly", expected_net=2100.0, next_date=date(2026, 10, 14), series_id=series.id))
        session.commit()
        run_detection(session, date(2026, 10, 2))
        rows = session.exec(__import__("sqlmodel").select(RecurringSeries)).all()
        assert len(rows) == 1 and rows[0].cadence == "semimonthly" and rows[0].confirmed is True
        income = session.exec(__import__("sqlmodel").select(Income)).one()
        assert income.cadence == "semimonthly" and income.next_date == date(2026, 10, 15)
