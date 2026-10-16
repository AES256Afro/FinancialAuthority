"""M21: a statement's text read into rows, deterministically."""

from datetime import date

from app.parsers.statement import parse_statement_text

TODAY = date(2026, 10, 14)
BANK = """Example Credit Union
Statement Period: 09/01/2026 - 09/30/2026
Date Description Amount Balance
09/01 Beginning balance 1,200.00
09/02 EXAMPLE EMPLOYER DIRECT DEPOSIT 2,240.00 3,440.00
09/05 EXAMPLE GROCER 123 54.12 3,385.88
09/07 FUEL STOP 55 (41.10) 3,344.78
Sep 9 RENT LLC 1,295.00 2,049.78
09/30 Ending balance 2,049.78
"""
CARD = """Example Card Statement
Closing Date: Oct 3, 2026
09/12 EXAMPLE CAFE 6.50
09/20 PAYMENT THANK YOU 300.00
"""


def test_a_bank_statement_reads_into_rows_with_signs_and_skips_balances():
    got = parse_statement_text(BANK, TODAY)
    rows = {r["description"]: (r["date"].isoformat(), r["amount"]) for r in got["rows"]}
    assert rows["EXAMPLE EMPLOYER DIRECT DEPOSIT"] == ("2026-09-02", -2240.0)  # money in
    assert rows["EXAMPLE GROCER 123"] == ("2026-09-05", 54.12)
    assert rows["FUEL STOP 55"] == ("2026-09-07", -41.1)  # parentheses mark money in on a bank statement
    assert rows["RENT LLC"] == ("2026-09-09", 1295.0)
    assert not any("balance" in d.lower() for d in rows) and got["skipped"] == 2


def test_a_card_statement_reads_charges_out_and_payments_in():
    got = parse_statement_text(CARD, TODAY, card=True)
    rows = {r["description"]: r["amount"] for r in got["rows"]}
    assert rows == {"EXAMPLE CAFE": 6.5, "PAYMENT THANK YOU": -300.0}


def test_a_long_or_odd_text_reads_quickly():
    import time

    started = time.perf_counter()
    parse_statement_text(("09/02 " + "x" * 5000 + " 1.00\n") * 400 + "1/1 " * 100000, TODAY)
    assert time.perf_counter() - started < 2.0
