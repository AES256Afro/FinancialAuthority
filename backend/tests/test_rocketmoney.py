from datetime import date

from sqlmodel import Session, select

from app.connectors.rocketmoney import export_csv, import_rows, parse_csv
from app.db import engine
from app.models import Account, Transaction

HEADER = "Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible,Transaction Tags\n"
ROWS = (
    "2026-10-02,2026-10-02,Cash,360 Checking,1234,Example Bank,EMPLOYER PAYROLL,,-2240.00,ACH DEPOSIT,Income,,,,\n"
    "2026-10-03,2026-10-03,Cash,360 Checking,1234,Example Bank,RENT LLC,Rent,1295.00,CHECK 101,Rent,,,,\n"
    "2026-10-04,2026-10-04,Credit Card,Rewards,9876,Example Bank,COFFEE PLACE,,4.50,POS,Dining & Drinks,,,,\n"
    "2026-10-04,2026-10-04,Credit Card,Rewards,9876,Example Bank,COFFEE PLACE,,4.50,POS,Dining & Drinks,,,,\n"
    "2026-10-05,2026-10-05,Cash,360 Checking,1234,Example Bank,PAYMENT TO CARD,,45.00,ONLINE,Credit Card Payment,,,,\n"
)


def test_parse_keeps_sign_rule_and_fields():
    rows = parse_csv(HEADER + ROWS, "a.csv")
    assert rows is not None and len(rows) == 5
    income = next(r for r in rows if r["category"] == "Income")
    assert income["amount"] == -2240.0
    rent = next(r for r in rows if r["merchant"] == "Rent")
    assert rent["amount"] == 1295.0 and rent["date"] == date(2026, 10, 3)


def test_parse_flips_opposite_convention():
    flipped = (HEADER + ROWS).replace("-2240.00", "2240.00").replace("1295.00", "-1295.00").replace("4.50", "-4.50").replace("45.00", "-45.00")
    rows = parse_csv(flipped)
    assert next(r for r in rows if r["category"] == "Income")["amount"] == -2240.0
    assert next(r for r in rows if r["merchant"] == "Rent")["amount"] == 1295.0


def test_rejects_other_csv():
    assert parse_csv("a,b,c\n1,2,3\n") is None


def test_import_dedupes_across_files_but_keeps_in_file_repeats():
    with Session(engine) as session:
        first = import_rows(session, parse_csv(HEADER + ROWS, "a.csv"), "a.csv")
        assert first.inserted == 5 and first.accounts == 2
        second = import_rows(session, parse_csv(HEADER + ROWS, "b.csv"), "b.csv")
        assert second.inserted == 0 and second.duplicates == 5
        coffee = session.exec(select(Transaction).where(Transaction.merchant == "COFFEE PLACE")).all()
        assert len(coffee) == 2  # the same key twice in one file is two coffees
        card = session.exec(select(Account).where(Account.name == "Rewards")).one()
        assert card.kind == "card"
        payment = session.exec(select(Transaction).where(Transaction.category == "Credit Card Payment")).one()
        assert payment.is_card_payment and not payment.is_spending


def test_export_matches_rocket_money_columns():
    with Session(engine) as session:
        import_rows(session, parse_csv(HEADER + ROWS, "a.csv"), "a.csv")
        text = export_csv(session)
        assert text.splitlines()[0] == HEADER.strip()
        assert "Rent" in text and "-2240.00" in text
        again = parse_csv(text, "roundtrip.csv")
        assert again is not None and len(again) == 5
