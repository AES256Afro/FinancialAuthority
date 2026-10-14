from datetime import date, timedelta

from app.engine.recurring import detect


def rows_for(merchant: str, amount: float, every: int, count: int, start: date, category: str = "Entertainment") -> list[dict]:
    return [{"date": start + timedelta(days=every * i), "merchant": merchant, "amount": amount, "category": category} for i in range(count)]


def test_monthly_and_biweekly_found_noise_ignored():
    as_of = date(2026, 10, 14)
    rows = []
    rows += rows_for("Streamflix", 15.99, 30, 6, date(2026, 4, 20))
    rows += rows_for("Couch plan installment", 62.0, 14, 3, date(2026, 9, 9), "Loan Payment")
    rows += [{"date": date(2026, 9, 1), "merchant": "Grocer", "amount": 44.1, "category": "Groceries"},
             {"date": date(2026, 9, 12), "merchant": "Grocer", "amount": 71.3, "category": "Groceries"},
             {"date": date(2026, 10, 2), "merchant": "Grocer", "amount": 38.0, "category": "Groceries"}]
    found = detect(rows, as_of)
    names = {f["merchant"]: f for f in found}
    assert names["Streamflix"]["cadence"] == "monthly" and names["Streamflix"]["typical_amount"] == 15.99
    assert names["Couch plan installment"]["cadence"] == "biweekly"
    assert "Grocer" not in names
    assert names["Streamflix"]["next_expected"] == date(2026, 4, 20) + timedelta(days=30 * 5) + timedelta(days=30)


def test_income_detection_uses_money_in():
    as_of = date(2026, 10, 14)
    rows = [{"date": date(2026, 10, 2) - timedelta(days=14 * i), "merchant": "Employer Payroll", "amount": -2240.0, "category": "Income"} for i in range(6)]
    found = detect(rows, as_of, income=True)
    assert len(found) == 1 and found[0]["cadence"] == "biweekly" and found[0]["kind"] == "income"
    assert found[0]["next_expected"] == date(2026, 10, 16)


def test_inactive_series_hidden_unless_asked():
    as_of = date(2026, 10, 14)
    rows = rows_for("Old Gym", 30.0, 30, 5, date(2025, 10, 1))
    assert detect(rows, as_of) == []
    assert detect(rows, as_of, include_inactive=True)[0]["active"] is False
