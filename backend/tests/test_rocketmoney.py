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


def test_an_export_lands_on_the_bank_links_account_and_merges_what_it_already_holds():
    """The same transaction seen through the bank link and the export lands once: the export's history goes
    on the live account the last four digits name, and a row the link already holds, same amount within three
    days, is merged rather than added. Older history the link never saw is added on that same account."""
    from app.connectors.banks import upsert_account

    with Session(engine) as session:
        live = upsert_account(session, provider="plaid", external_id="acc-1", name="Checking", institution="Example Bank", kind="checking", balance=500.0, last4="1234")
        session.add(Transaction(account_id=live.id, date=date(2026, 10, 3), merchant="Rent Llc", raw_name="RENT LLC", description="CHECK 101", amount=1295.0, category="Rent", source="plaid", source_ref="p1", row_hash="plaid-rent"))
        session.add(Transaction(account_id=live.id, date=date(2026, 10, 2), merchant="Employer Payroll", raw_name="EMPLOYER PAYROLL", description="ACH DEPOSIT", amount=-2240.0, category="Income", is_income=True, source="plaid", source_ref="p2", row_hash="plaid-pay"))
        session.commit()
        text = HEADER + ROWS + "2024-10-03,2024-10-03,Cash,360 Checking,1234,Example Bank,RENT LLC,Rent,1295.00,CHECK 77,Rent,,,,\n"
        result = import_rows(session, parse_csv(text, "export.csv"), "export.csv")
        # the payroll and the rent merge; the card payment and the old rent land on the live account; the coffee rows need the export's own card account
        assert result.details["merged"] == 2 and result.inserted == 4 and result.accounts == 1 and "already held by the bank link" in result.summary
        rows = session.exec(select(Transaction).where(Transaction.account_id == live.id)).all()
        assert len(rows) == 4 and sum(1 for t in rows if t.source == "rocketmoney") == 2
        own = session.exec(select(Account).where(Account.connector == "rocketmoney")).all()
        assert [a.name for a in own] == ["Rewards"]
        again = import_rows(session, parse_csv(text, "export.csv"), "export.csv")
        assert again.inserted == 0 and again.duplicates == 4 and again.details["merged"] == 2  # the overlap merges again rather than landing
