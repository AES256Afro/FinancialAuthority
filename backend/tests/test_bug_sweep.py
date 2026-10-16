"""The bug sweep's findings, each held by a test on the synthetic household or on rows made here."""

import io
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models import Account, Income, InstallmentPlan, PlannedPayment, RecurringSeries, Transaction

TODAY = date(2026, 10, 14)
RM_HEADER = "Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible,Transaction Tags\n"


def _upload(owner: TestClient, name: str, text: str, **data) -> dict:
    return owner.post("/api/v1/imports/file", data=data, files={"file": (name, io.BytesIO(text.encode()), "text/plain")}).json()


def _upload_rm(owner: TestClient, text: str) -> dict:
    got = owner.post("/api/v1/imports/rocketmoney", files={"file": ("rm.csv", io.BytesIO(text.encode()), "text/csv")}).json()
    return {**got, "import_id": (got.get("details") or {}).get("import_id", got.get("import_id"))}


def test_a_rocket_money_file_brought_back_after_an_undo_opens_its_account_again(owner: TestClient):
    text = RM_HEADER + "2026-10-04,2026-10-04,Credit Card,Rewards,9876,Example Bank,COFFEE PLACE,,4.50,POS,Dining & Drinks,,,,\n"
    first = _upload_rm(owner, text)
    owner.post(f"/api/v1/imports/{first['import_id']}/undo")
    with Session(engine) as session:
        assert not session.exec(select(Account).where(Account.name == "Rewards")).first().active
    again = _upload_rm(owner, text)
    with Session(engine) as session:
        account = session.exec(select(Account).where(Account.name == "Rewards")).first()
        assert account.active and len(session.exec(select(Transaction).where(Transaction.account_id == account.id)).all()) == 1
    owner.post(f"/api/v1/imports/{again['import_id']}/undo")  # and undoing it closes it again
    with Session(engine) as session:
        assert not session.exec(select(Account).where(Account.name == "Rewards")).first().active


def test_a_purchase_after_the_statement_ends_is_never_merged_into_it(owner: TestClient):
    with Session(engine) as session:
        live = Account(name="Checking", institution="Example CU", kind="checking", connector="plaid", external_id="acc-1")
        session.add(live)
        session.flush()
        for when in (date(2026, 10, 2), date(2026, 10, 4)):  # two coffees the link saw after the statement closed
            session.add(Transaction(account_id=live.id, date=when, merchant="Example Coffee", amount=5.25, source="plaid", row_hash=f"plaid-coffee-{when}"))
        session.commit()
        live_id = live.id
    statement = "Date,Description,Amount\n09/28/2026,EXAMPLE GROCER,-20.00\n09/30/2026,EXAMPLE COFFEE,-5.25\n10/01/2026,EXAMPLE COFFEE,-5.25\n"
    got = _upload(owner, "sept.csv", statement, account_id=str(live_id))
    assert got["merged"] == 0 and got["inserted"] == 3
    with Session(engine) as session:
        assert sum(1 for t in session.exec(select(Transaction).where(Transaction.account_id == live_id)).all() if t.amount == 5.25) == 4


def test_detection_retires_the_streams_an_undo_took_away(owner: TestClient):
    rows = "".join(f"2026-{m:02d}-05,2026-{m:02d}-05,Cash,Checking,1234,Example Bank,EXAMPLE PAYLATER,,25.00,ACH,Loans,,,,\n" for m in range(5, 11))
    made = _upload_rm(owner, RM_HEADER + rows)
    with Session(engine) as session:
        assert session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).first() is not None  # noqa: E712
    owner.post(f"/api/v1/imports/{made['import_id']}/undo")
    with Session(engine) as session:
        assert session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).first() is None  # noqa: E712


def test_a_planned_payment_settles_when_its_payment_is_imported(owner: TestClient):
    with Session(engine) as session:
        account = Account(name="Checking", kind="checking", connector="file", external_id="file:checking", balance=900.0)
        session.add(account)
        session.flush()
        session.add(PlannedPayment(name="Example Power", amount=80.0, date=date(2026, 10, 13), kind="bill"))
        session.commit()
        account_id = account.id
    _upload(owner, "oct.csv", "Date,Description,Amount\n10/13/2026,EXAMPLE POWER,-80.00\n10/12/2026,EXAMPLE GROCER,-12.00\n", account_id=str(account_id))
    with Session(engine) as session:
        assert session.exec(select(PlannedPayment)).first().done  # settled by the import, before any page opened


def test_weekly_and_monthly_paydays_fill_the_whole_line(owner: TestClient):
    from app.engine.cadence import paydays_through

    weekly = paydays_through(date(2026, 10, 16), "weekly", TODAY + timedelta(days=60))
    assert len(weekly) == 9 and weekly[-1] == date(2026, 12, 11)
    assert paydays_through(date(2026, 10, 30), "monthly", TODAY + timedelta(days=60), at_least=3)[:3] == [date(2026, 10, 30), date(2026, 11, 30), date(2026, 12, 30)]
    with Session(engine) as session:
        session.add(Account(name="Checking", kind="checking", connector="manual", external_id="m:1", balance=500.0))
        session.add(Income(name="Example Employer", cadence="weekly", expected_net=300.0, next_date=date(2026, 10, 16)))
        session.commit()
    line = {p["date"]: p["cash"] for p in owner.get("/api/v1/snapshot").json()["projection"]}
    assert line["2026-12-11"] - line["2026-12-10"] == 300.0  # the ninth weekly check is on the line


def test_goals_count_the_paychecks_of_the_owners_cadence():
    from app.engine.goals import paychecks_until

    assert paychecks_until(TODAY, date(2026, 12, 24), date(2026, 10, 16), "biweekly") == 5
    assert paychecks_until(TODAY, date(2026, 12, 24), date(2026, 10, 30), "monthly") == 2  # Oct 30, Nov 30
    assert paychecks_until(TODAY, date(2026, 12, 24), date(2026, 10, 16), "weekly") == 10


def test_the_one_and_fifteen_payer_is_twice_a_month():
    from app.engine.cadence import business_day_on_or_before, next_paydays
    from app.engine.recurring import detect

    days = [business_day_on_or_before(date(2026, m, d)) for m in range(4, 11) for d in (1, 15)]
    found = detect([{"date": d, "merchant": "Example Employer", "amount": -2000.0, "category": "Income"} for d in days], date(2026, 10, 16), 400, include_inactive=True, income=True)
    assert found[0]["cadence"] == "semimonthly" and found[0]["pay_days"] == "1,15"
    assert next_paydays(date(2026, 10, 15), "semimonthly", 3, "1,15") == [date(2026, 10, 15), date(2026, 10, 30), date(2026, 11, 13)]  # Nov 1 is a Sunday, Nov 15 too


def test_the_latest_price_is_the_latest_by_date():
    from app.engine.recurring import detect

    rows = [{"date": date(2026, m, 3), "merchant": "Example Stream", "amount": 17.0 if m >= 9 else 15.0, "category": "Entertainment"} for m in range(10, 4, -1)]  # newest first
    found = detect(rows, TODAY, 400)
    assert found[0]["latest_amount"] == 17.0


def test_small_findings():
    from app.engine.borrowing import ADVANCE_PROVIDERS, provider_of
    from app.engine.decisions import _charges

    # an installment split keeps every cent
    assert round(sum(a for _, a in _charges(100.0, TODAY, 3)), 2) == 100.0
    # "Dave" the app, not a restaurant or a person
    assert provider_of("DAVE INC DEBIT", ADVANCE_PROVIDERS) == "Dave" and provider_of("Dave.com ExtraCash", ADVANCE_PROVIDERS) == "Dave"
    assert provider_of("DAVE'S HOT CHICKEN", ADVANCE_PROVIDERS) is None and provider_of("Zelle payment to Dave Smith", ADVANCE_PROVIDERS) is None


def test_a_purchase_check_refuses_what_is_not_a_number(owner: TestClient):
    assert owner.get("/api/v1/check", params={"amount": "nan"}).status_code in (400, 422)
    assert owner.get("/api/v1/check", params={"amount": "inf"}).status_code in (400, 422)


def test_a_card_in_credit_owes_nothing(owner: TestClient):
    from app.engine.reports import debt_mountain

    with Session(engine) as session:
        session.add(Account(name="Store card", kind="card", connector="manual", external_id="m:card", balance=-45.0))
        session.add(Account(name="Checking", kind="checking", connector="manual", external_id="m:chk", balance=400.0))
        session.commit()
        mountain = debt_mountain(session, TODAY)
    assert "Store card" not in mountain["order"]["snowball"]
    snap = owner.get("/api/v1/snapshot").json()
    credit = [x for x in snap["safe_to_spend"]["left_out"] if x["name"] == "Store card"]
    assert len(credit) == 1 and credit[0]["verb"] == "holds a credit of" and credit[0]["amount"] == 45.0


def test_a_refund_is_not_a_paycheck(owner: TestClient):
    _upload(owner, "oct.csv", "Date,Description,Amount\n10/02/2026,EXAMPLE EMPLOYER PAYROLL,2240.00\n10/03/2026,REFUND EXAMPLE STORE,30.00\n10/04/2026,TRANSFER IN FROM SAVINGS,100.00\n", account_name="Checking")
    with Session(engine) as session:
        income = {t.description: t.is_income for t in session.exec(select(Transaction)).all()}
    assert income["EXAMPLE EMPLOYER PAYROLL"] and not income["REFUND EXAMPLE STORE"] and not income["TRANSFER IN FROM SAVINGS"]


def test_a_card_export_with_charges_positive_reads_the_right_way():
    from app.connectors.files import parse_csv_rows

    rows = parse_csv_rows("Date,Description,Amount\n10/01/2026,EXAMPLE GROCER,54.12\n10/03/2026,PAYMENT THANK YOU,-300.00\n10/05/2026,FUEL STOP,41.10\n")
    assert {r["description"]: r["amount"] for r in rows} == {"EXAMPLE GROCER": 54.12, "PAYMENT THANK YOU": -300.0, "FUEL STOP": 41.10}


def test_a_plan_without_a_schedule_ticks_down_by_its_payments():
    from app.engine.borrowing import refresh_plan_progress

    with Session(engine) as session:
        account = Account(name="Checking", kind="checking", connector="manual", external_id="m:1", balance=500.0)
        session.add(account)
        session.flush()
        session.add(InstallmentPlan(provider="Klarna", merchant="Example Shop", total=200.0, installment=50.0, count=4, remaining=3, next_date=date(2026, 9, 20), cadence_days=14))
        for when in (date(2026, 9, 20), date(2026, 10, 4)):
            session.add(Transaction(account_id=account.id, date=when, merchant="KLARNA", amount=50.0, source="plaid", row_hash=f"k-{when}"))
        session.commit()
        assert refresh_plan_progress(session, TODAY) == 1
        plan = session.exec(select(InstallmentPlan)).first()
        assert plan.remaining == 1 and plan.next_date == date(2026, 10, 18)
        assert refresh_plan_progress(session, TODAY) == 0  # a payment counts once


def test_a_bad_mapping_is_a_plain_refusal(owner: TestClient):
    r = owner.post("/api/v1/imports/file", data={"account_name": "Checking", "mapping": "[1, 2]"}, files={"file": ("a.csv", io.BytesIO(b"Date,Description,Amount\n10/01/2026,X,1.00\n"), "text/plain")})
    assert r.status_code == 400


def test_a_refused_import_leaves_no_restore_point(owner: TestClient):
    from app import restore

    before = len(restore.list_points())
    r = owner.post("/api/v1/imports/file", data={"account_id": "9999"}, files={"file": ("a.csv", io.BytesIO(b"Date,Description,Amount\n10/01/2026,X,1.00\n"), "text/plain")})
    assert r.status_code == 400 and len(restore.list_points()) == before
