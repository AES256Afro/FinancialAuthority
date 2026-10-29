"""The seventh sweep, pure functions: invariants checked on many random inputs with fixed seeds. Pay cadences,
bill dates, debt payoff, series detection and the document parsers. Every figure here is made up."""

from __future__ import annotations

import calendar
import math
import random
from datetime import date, timedelta

from app.engine import billdates, cadence, payoff, recurring
from app.engine.projection import series_dates

TODAY = date(2026, 10, 14)
SPECIAL_DAYS = [date(2026, 12, 31), date(2027, 1, 1), date(2027, 2, 28), date(2028, 2, 29), date(2028, 2, 28), date(2026, 3, 8), date(2026, 11, 1), date(2026, 1, 31), date(2026, 4, 30)]


def _day(rng: random.Random) -> date:
    if rng.random() < 0.3:
        return rng.choice(SPECIAL_DAYS)
    return date(2026, 1, 1) + timedelta(days=rng.randint(0, 730))


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ---- cadence.paydays: sorted, unique, consistent with previous_paydays, step_payday and payday_for ------------------


def test_paydays_are_sorted_unique_and_agree_forward_and_back():
    rng = random.Random(7001)
    for _ in range(400):
        kind = rng.choice(["weekly", "biweekly", "semimonthly", "monthly"])
        pay_days = rng.choice(["", "1,15"])
        first = _day(rng)
        pay_day = 0
        if kind == "semimonthly":
            year, month = first.year, first.month
            first = rng.choice(cadence.semimonthly_anchors(year, month, pay_days))
        if kind == "monthly":
            pay_day = cadence.pay_day_from(first) if rng.random() < 0.5 else first.day
        count = rng.randint(2, 30)
        out = cadence.next_paydays(first, kind, count, pay_days, pay_day)
        assert len(out) == count and out[0] == first
        assert all(a < b for a, b in zip(out, out[1:])), (kind, first, out)
        assert len(set(out)) == count
        # Stepping one at a time lands on the same days.
        stepped = [first]
        for _ in range(count - 1):
            stepped.append(cadence.step_payday(stepped[-1], kind, pay_days, pay_day))
        assert stepped == out, (kind, first, pay_day)
        # Walking back from the last lands on the same days.
        back = cadence.previous_paydays(out[-1], kind, count - 1, pay_days, pay_day)
        assert back == list(reversed(out[:-1])), (kind, first, pay_day, out, back)
        if kind in ("weekly", "biweekly"):
            step = 7 if kind == "weekly" else 14
            assert all((b - a).days == step for a, b in zip(out, out[1:]))
        if kind == "monthly":
            day = pay_day or first.day
            assert all(d == _clamp(d.year, d.month, day) for d in out), (first, pay_day, out)
            assert all((b.year * 12 + b.month) - (a.year * 12 + a.month) == 1 for a, b in zip(out, out[1:]))
        if kind == "semimonthly":
            assert all(d in cadence.semimonthly_anchors(d.year, d.month, pay_days) or d in cadence.semimonthly_anchors(*(((d + timedelta(days=3)).year, (d + timedelta(days=3)).month)), pay_days) for d in out), (first, pay_days, out)
        # paydays_through keeps every payday through the end and never fewer than asked.
        end = first + timedelta(days=rng.randint(0, 200))
        through = cadence.paydays_through(first, kind, end, at_least=rng.randint(1, 6), pay_days=pay_days, pay_day=pay_day)
        many = cadence.next_paydays(first, kind, 60, pay_days, pay_day)
        within = [d for d in many if d <= end]
        assert through[: len(within)] == within, (kind, first, end)
        assert all(a < b for a, b in zip(through, through[1:]))


def test_payday_for_picks_a_real_payday_near_the_due_date():
    rng = random.Random(7002)
    for _ in range(600):
        due = _day(rng)
        pay_days = rng.choice(["", "1,15"])
        which = rng.choice(["mid", "end"])
        got = cadence.payday_for(due, which, pay_days)
        anchors = {cadence.semimonthly_anchors(y, m, pay_days)[0 if which == "mid" else 1] for y, m in cadence._months_around(due)}
        assert got in anchors, (due, which, pay_days, got)
        near = [a for a in anchors if abs((a - due).days) <= 7]
        if near:
            assert abs((got - due).days) == min(abs((a - due).days) for a in near)
        else:
            before = [a for a in anchors if a <= due]
            assert got == (max(before) if before else min(anchors))
        assert got.weekday() < 5


# ---- billdates and series_dates over random cadence, anchor, due day, start and end ---------------------------------


class _Bill:
    def __init__(self, **kw):
        self.cadence = kw.get("cadence", "monthly")
        self.due_day = kw.get("due_day", 1)
        self.anchor_date = kw.get("anchor_date")
        self.starts_on = kw.get("starts_on")
        self.ends_on = kw.get("ends_on")


def _expected_month_stepped(anchor: date, months: int, day: int, start: date, end: date) -> list[date]:
    out = []
    for k in range(0, 60):
        m = anchor.month - 1 + k * months
        d = _clamp(anchor.year + m // 12, m % 12 + 1, day)
        if start <= d <= end:
            out.append(d)
    return out


def test_bill_dates_are_increasing_in_range_and_counted_by_the_cadence():
    rng = random.Random(7003)
    for _ in range(1500):
        kind = rng.choice(list(billdates.DAY_STEPS) + list(billdates.MONTH_STEPS))
        due_day = rng.choice([1, 15, 28, 29, 30, 31, rng.randint(1, 31), 0, 32])
        start = _day(rng)
        end = start + timedelta(days=rng.randint(0, 120))
        anchor = start + timedelta(days=rng.randint(-200, 100)) if rng.random() < 0.7 else None
        starts_on = start + timedelta(days=rng.randint(-30, 60)) if rng.random() < 0.3 else None
        ends_on = start + timedelta(days=rng.randint(-10, 150)) if rng.random() < 0.3 else None
        bill = _Bill(cadence=kind, due_day=due_day, anchor_date=anchor, starts_on=starts_on, ends_on=ends_on)
        got = billdates.bill_dates(bill, start, end)
        lo = max([start] + ([starts_on] if starts_on else []))
        hi = min([end] + ([ends_on] if ends_on else []))
        assert all(a < b for a, b in zip(got, got[1:])), (kind, got)
        assert all(lo <= d <= hi for d in got), (kind, due_day, anchor, start, end, starts_on, ends_on, got)
        day = max(1, min(31, due_day or 1))
        if kind in billdates.DAY_STEPS:
            step = billdates.DAY_STEPS[kind]
            assert all((b - a).days == step for a, b in zip(got, got[1:]))
            if lo <= hi:
                if anchor is not None:
                    first = anchor
                    while first < lo:
                        first += timedelta(days=step)
                    expected = (hi - first).days // step + 1 if first <= hi else 0
                    assert len(got) == expected, (kind, anchor, lo, hi, got)
                else:
                    assert len(got) <= (hi - lo).days // step + 1
            continue
        months = billdates.MONTH_STEPS[kind]
        if months > 1 and anchor is None:
            assert got == []
            continue
        if lo > hi:
            assert got == []
            continue
        assert all(d == _clamp(d.year, d.month, day) for d in got), (kind, due_day, got)  # the 31st lands on the last day of a short month
        if months == 1:
            expected = [_clamp(y, m, day) for y in range(lo.year, hi.year + 1) for m in range(1, 13)]
            assert got == [d for d in expected if lo <= d <= hi], (due_day, lo, hi, got)
        else:
            assert got == _expected_month_stepped(anchor, months, day, lo, hi), (kind, due_day, anchor, lo, hi, got)
            assert len(got) <= (hi - lo).days // (28 * months) + 1


def test_series_dates_are_increasing_in_range_and_never_invented():
    rng = random.Random(7004)
    for _ in range(600):
        kind = rng.choice(["weekly", "biweekly", "semimonthly", "monthly", "quarterly", "yearly"])
        first = _day(rng)
        start = first + timedelta(days=rng.randint(-40, 40))
        end = start + timedelta(days=rng.randint(0, 120))
        got = series_dates(kind, first, start, end)
        assert all(a < b for a, b in zip(got, got[1:])), (kind, first, got)
        assert all(start <= d <= end for d in got), (kind, first, start, end, got)
        if kind in ("weekly", "biweekly", "monthly", "quarterly", "yearly"):
            assert all(d >= first for d in got), (kind, first, got)  # stepped from its next expected date, never before it
        if kind in ("monthly", "quarterly", "yearly"):
            months = {"monthly": 1, "quarterly": 3, "yearly": 12}[kind]
            assert got == _expected_month_stepped(first, months, first.day, max(start, first), end), (kind, first, start, end, got)


# ---- payoff.simulate: arithmetic against an oracle, monotone in the extra, the orders ranked ------------------------


def _debts(rng: random.Random, n: int, finishing: bool = True) -> list[dict]:
    out = []
    for i in range(n):
        balance = round(rng.uniform(20, 20000), 2)
        apr = round(rng.choice([0.0, rng.uniform(0, 36)]), 2)
        floor = balance * apr / 1200
        minimum = round(max(25.0, floor + balance / 300 + rng.uniform(1, 400)), 2) if finishing else round(rng.uniform(1, 60), 2)  # finishes inside the cap
        out.append({"name": f"Debt {i}", "balance": balance, "apr": apr, "minimum": minimum, "source": f"debt:{i}"})
    return out


def _amortise(balance: float, apr: float, minimum: float) -> tuple[int, float]:
    """Months and interest paying a single debt at its minimum, as the simulation defines a month."""
    interest = 0.0
    months = 0
    while balance > 0.005 and months < payoff.MAX_MONTHS:
        months += 1
        i = balance * apr / 1200
        interest += i
        balance += i
        balance -= min(balance, minimum)
    return months, interest


def test_minimums_only_matches_an_amortisation_oracle_and_debts_are_independent():
    rng = random.Random(7005)
    start = date(2026, 10, 1)
    for _ in range(300):
        debts = _debts(rng, rng.randint(1, 5))
        got = payoff.simulate(debts, 0.0, "minimums", start)
        assert got["months"] is not None and not got["grows"]
        total_interest = 0.0
        for d, row in zip(debts, got["debts"]):
            months, interest = _amortise(d["balance"], d["apr"], d["minimum"])
            assert row["paid_on"] == payoff._add_months(start, months).isoformat(), (d, row)
            assert math.isclose(row["interest"], round(interest, 2), abs_tol=0.011), (d, row, interest)
            total_interest += interest
            alone = payoff.simulate([d], 0.0, "minimums", start)["debts"][0]
            assert alone == row, (d, alone, row)
        assert math.isclose(got["interest"], round(total_interest, 2), abs_tol=0.011 * len(debts))
        assert got["months"] == max(_amortise(d["balance"], d["apr"], d["minimum"])[0] for d in debts)


def test_extra_never_delays_a_debt_and_avalanche_never_costs_more_than_snowball():
    rng = random.Random(7006)
    start = date(2026, 10, 1)
    for _ in range(300):
        debts = _debts(rng, rng.randint(1, 5))
        base = payoff.simulate(debts, 0.0, "minimums", start)
        extras = sorted(rng.sample([0.0, 10.0, 50.0, 100.0, 250.0, 500.0, 1000.0], 3))
        previous = {"avalanche": None, "snowball": None}
        for extra in extras:
            ways = {o: payoff.simulate(debts, extra, o, start) for o in ("avalanche", "snowball")}
            for order, way in ways.items():
                assert way["months"] is not None and way["months"] <= base["months"], (order, extra, debts)
                assert way["interest"] <= base["interest"] + 0.01, (order, extra, way["interest"], base["interest"])
                for b, w in zip(base["debts"], way["debts"]):
                    assert w["paid_on"] <= b["paid_on"], (order, extra, b, w)
                    assert w["interest"] <= b["interest"] + 0.01, (order, extra, b, w)
                    assert w["interest"] >= 0.0
                # Avalanche puts every extra dollar on the highest rate, so more extra never costs more. Snowball
                # re-targets the smallest live balance each month and is not interest-optimal by design: its only
                # promise is never worse than the minimums (checked above).
                if previous[order] is not None and order == "avalanche":
                    assert way["months"] <= previous[order]["months"], (order, extra, debts)
                    assert way["interest"] <= previous[order]["interest"] + 0.01, (order, extra, debts)
                previous[order] = way
                assert date.fromisoformat(way["debt_free"]) == max(date.fromisoformat(d["paid_on"]) for d in way["debts"])
            assert ways["avalanche"]["interest"] <= ways["snowball"]["interest"] + 0.01, (extra, debts, ways["avalanche"]["interest"], ways["snowball"]["interest"])


def test_a_debt_whose_minimum_is_under_its_interest_grows_and_has_no_interest_figure():
    rng = random.Random(7007)
    start = date(2026, 10, 1)
    for _ in range(100):
        debts = _debts(rng, rng.randint(1, 4), finishing=False)
        if all(d["minimum"] > d["balance"] * d["apr"] / 1200 for d in debts):
            continue
        got = payoff.simulate(debts, 0.0, "minimums", start)
        assert got["grows"] and got["interest"] is None and got["months"] is None and got["debt_free"] is None
        assert all(r["interest"] is None for r in got["debts"] if r["paid_on"] is None)


# ---- recurring.detect over synthetic series with jitter, a skipped charge and a duplicate ----------------------------


def _series(rng: random.Random, merchant: str, kind: str, as_of: date) -> tuple[list[dict], float]:
    amount = round(rng.uniform(5, 400), 2)
    # A day or so on a short cycle, three days on a long one: the detector wants three gaps in five near the cycle.
    jitter = {"weekly": 1, "biweekly": 1, "monthly": 3, "quarterly": 3, "yearly": 3}[kind]
    n = {"weekly": rng.randint(8, 20), "biweekly": rng.randint(6, 14), "monthly": rng.randint(6, 14), "quarterly": rng.randint(4, 7), "yearly": rng.randint(3, 4)}[kind]
    if kind in ("weekly", "biweekly"):
        step = timedelta(days=7 if kind == "weekly" else 14)
        last = as_of - timedelta(days=rng.randint(0, step.days - 1))
        dates = [last - step * k for k in range(n)]
    else:
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}[kind]
        day = rng.randint(1, 28)
        last = _clamp(as_of.year, as_of.month, day)
        if last > as_of:
            last = cadence._add_months(last, -1, day)
        dates = [cadence._add_months(last, -k * months, day) for k in range(n)]
    dates = sorted(dates)
    # One charge the biller skipped, never the first or the last, and only when three gaps remain to tell the cadence by.
    skipped = rng.randrange(1, len(dates) - 1) if len(dates) >= 5 else -1
    rows = []
    for i, d in enumerate(dates):
        if i == skipped:
            continue
        when = d + timedelta(days=rng.randint(-jitter, jitter)) if 0 < i < len(dates) - 1 else d
        # Within 7% of the usual amount: the detector wants three in four charges within 15% of their median.
        rows.append({"date": when, "merchant": merchant, "amount": round(amount * rng.uniform(0.93, 1.07), 2), "category": "Entertainment"})
    rows.append(dict(rows[-1]))  # the last charge posted twice
    return rows, amount


def test_detection_finds_one_series_per_generator_with_its_cadence():
    rng = random.Random(7008)
    for _ in range(120):
        as_of = _day(rng)
        kinds = rng.sample(["weekly", "biweekly", "monthly", "quarterly", "yearly"], rng.randint(1, 4))
        rows, expected = [], {}
        for i, kind in enumerate(kinds):
            name = f"Example Merchant {i}"
            series, amount = _series(rng, name, kind, as_of)
            rows += series
            expected[recurring.merchant_key(name)] = (kind, amount)
        rng.shuffle(rows)
        found = recurring.detect(rows, as_of, lookback_days=2000, include_inactive=True)
        by_key: dict[str, list[dict]] = {}
        for f in found:
            by_key.setdefault(f["merchant_key"], []).append(f)
        for key, (kind, amount) in expected.items():
            assert len(by_key.get(key, [])) == 1, (key, kind, amount, by_key.get(key), [r for r in rows if recurring.merchant_key(r["merchant"]) == key])
            f = by_key[key][0]
            assert f["cadence"] == kind, (key, kind, f["cadence"], sorted(r["date"] for r in rows if recurring.merchant_key(r["merchant"]) == key))
            assert abs(f["typical_amount"] - amount) <= amount * 0.1 + 0.01, (key, amount, f["typical_amount"])
            assert f["next_expected"] > f["last_date"] >= f["first_seen"]
            assert f["active"]
            assert f["charges_seen"] == len({r["date"] for r in rows if recurring.merchant_key(r["merchant"]) == key})
        assert set(by_key) == set(expected)


# ---- parsers: a mutated document reads right or empty, never a wrong number ----------------------------------------

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
STUB_TRUTH = {"gross": 2980.77, "net": 2240.0, "pay_date": "2026-10-16", "period_start": "2026-09-28", "period_end": "2026-10-11", "hours": 80.0}
STUB_DEDUCTIONS = {"Federal income tax": 298.08, "Social Security": 184.81, "Medicare": 43.22, "Health insurance": 112.5, "401(k)": 89.42, "Dental": 12.74}

CONFIRMATION = """Here are your loan details, including information about how to repay.

Your loan confirmation

Jane – Example Outfitters has processed your order, and your loan has been confirmed!

Your Loan Breakdown

$687.65
Loan amount
The amount of credit you borrowed

$726.94
Total of payments
The amount you will have paid after making all scheduled payments

$39.29
Finance charge
The amount this credit will cost you

Your Payment Schedule

Oct 23, 2026

$242.32
Principal: $222.75
Interest: $19.57

Nov 23, 2026

$242.32
Principal: $229.09
Interest: $13.23

Dec 23, 2026

$242.30
Principal: $235.81
Interest: $6.49

Loan ID: 9ABC-DEF2

If you need help, visit our Help Center.
"""
LOAN_SCHEDULE = {"2026-10-23": (242.32, 222.75, 19.57), "2026-11-23": (242.32, 229.09, 13.23), "2026-12-23": (242.3, 235.81, 6.49)}

UPCOMING = (
    "Hi Owner, you have 3 recurring charges coming up in the next 7 days.\n\n"
    "Heads up Owner...\n\nYou have 3 recurring charges coming up within the next 7 days.\n\n"
    "Example Water Utility\n\nMonday, October 12\n\n$61.40\n\n"
    "Streamflix\n\nThursday, October 15\n\n$15.99\n\n"
    "Example Gym\n\nSaturday, October 17\n\n$29.00\n\n"
    "Open Rocket Money\n\nLooking to lower your bills?\n\nLower my bills\n"
)
UPCOMING_TRUTH = {"Example Water Utility": ("2026-10-12", 61.4), "Streamflix": ("2026-10-15", 15.99), "Example Gym": ("2026-10-17", 29.0)}

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
BANK_TRUTH = {"EXAMPLE EMPLOYER DIRECT DEPOSIT": ("2026-09-02", -2240.0), "EXAMPLE GROCER 123": ("2026-09-05", 54.12), "FUEL STOP 55": ("2026-09-07", -41.1), "RENT LLC": ("2026-09-09", 1295.0)}


def _mutate(rng: random.Random, text: str, stray: str) -> tuple[str, str]:
    lines = text.splitlines()
    how = rng.choice(["drop", "duplicate", "reorder", "separators", "stray", "two"])
    if how == "drop":
        del lines[rng.randrange(len(lines))]
    elif how == "duplicate":
        i = rng.randrange(len(lines))
        lines.insert(i, lines[i])
    elif how == "reorder":
        i, j = rng.randrange(len(lines)), rng.randrange(len(lines))
        lines[i], lines[j] = lines[j], lines[i]
    elif how == "separators":
        sep = rng.choice(["\t", "  ", " | ", "    "])
        lines = [line.replace("  ", sep) if rng.random() < 0.7 else line for line in lines]
    elif how == "stray":
        lines.insert(rng.randrange(len(lines) + 1), stray)
    else:
        a = _mutate(rng, "\n".join(lines), stray)[0]
        return _mutate(rng, a, stray)[0], "two"
    return "\n".join(lines) + "\n", how


def test_a_mutated_paystub_reads_right_or_not_at_all():
    from app.parsers.paystub import parse_paystub_text

    rng = random.Random(7009)
    for _ in range(250):
        text, how = _mutate(rng, STUB_TEXT, rng.choice(["Total Deductions                       740.77   14,815.40", "Total                                9,999.99   99,999.99", "YTD Total 44,800.00"]))
        got = parse_paystub_text(text, TODAY)
        if got is None:
            continue
        for key, truth in STUB_TRUTH.items():
            if key in got:
                assert got[key] == truth, (how, key, got[key], text)
        for row in got.get("deductions") or []:
            if row["name"] in STUB_DEDUCTIONS:
                assert row["amount"] == STUB_DEDUCTIONS[row["name"]], (how, row, text)
            else:
                assert row["amount"] in (740.77, 9999.99), (how, row, text)  # a stray line is listed under its own name, never as a known deduction
        if "gross" in got and "net" in got:
            assert got["net"] <= got["gross"] + 0.01 or got["confidence"] <= 0.4


def test_a_mutated_loan_confirmation_reads_right_or_is_doubted():
    from app.parsers.affirm import parse_loan_confirmation

    rng = random.Random(7010)
    for _ in range(250):
        text, how = _mutate(rng, CONFIRMATION, rng.choice(["Total: $1,000.00", "$999.99", "Jan 1, 2027"]))
        got = parse_loan_confirmation(text, TODAY)
        if got is None:
            continue
        for s in got["schedule"]:
            truth = LOAN_SCHEDULE.get(s["date"])
            if truth is not None:
                assert (s["amount"], s["principal"], s["interest"]) == truth or got["confidence"] <= 0.5, (how, s, text)
        dates = [s["date"] for s in got["schedule"]]
        assert dates == sorted(dates)
        full = [s for s in got["schedule"] if LOAN_SCHEDULE.get(s["date"]) == (s["amount"], s["principal"], s["interest"])]
        if got["confidence"] > 0.5:
            assert len(got["schedule"]) == 3 and len(full) == 3, (how, got, text)
            assert abs(got["schedule_total"] - 726.94) <= 0.01
        for key, truth in (("loan_amount", 687.65), ("finance_charge", 39.29)):
            if key in got:
                # A stray figure on the line above a label is that label's figure, as the mail lays it out.
                assert got[key] == truth or got["confidence"] <= 0.5 or (how in ("stray", "two") and got[key] in (1000.0, 999.99)), (how, key, got[key], text)
        if "loan_id" in got:
            assert got["loan_id"] == "9ABC-DEF2"


def test_a_mutated_upcoming_bills_mail_lists_only_real_charges():
    from app.parsers.rocketmoney_bills import parse_upcoming_bills

    rng = random.Random(7011)
    ref = date(2026, 10, 9)
    for _ in range(250):
        text, how = _mutate(rng, UPCOMING, rng.choice(["Total $106.39", "$1,000.00", "Friday, October 16"]))
        got = parse_upcoming_bills(text, ref)
        pairs = [(c["due_date"].isoformat(), c["amount"]) for c in got]
        assert len(pairs) == len(set(pairs)) or how in ("duplicate", "two"), (how, got, text)
        amounts = {t[1] for t in UPCOMING_TRUTH.values()}
        days = {t[0] for t in UPCOMING_TRUTH.values()} | {"2026-10-16"}
        for c in got:
            # The name is the line before the day, so a stray line there names the charge; the figures are the mail's own.
            assert c["name"] in UPCOMING_TRUTH or how in ("stray", "two", "reorder"), (how, c, text)
            # A stray amount line between a day and its amount is read as the amount, as the mail lays it out; a stray
            # total elsewhere never becomes a charge.
            assert c["amount"] in amounts or (how in ("stray", "two") and c["amount"] == 1000.0), (how, c, text)
            assert c["due_date"].isoformat() in days, (how, c, text)
            if how not in ("reorder", "two", "stray"):
                assert (c["due_date"].isoformat(), c["amount"]) == UPCOMING_TRUTH[c["name"]], (how, c, text)


def test_a_mutated_statement_reads_only_its_own_lines():
    from app.parsers.statement import parse_statement_text

    rng = random.Random(7012)
    for _ in range(250):
        text, how = _mutate(rng, BANK, rng.choice(["09/15 Total 3,000.00", "Total fees 12.00", "09/20 SUBTOTAL 100.00 200.00"]))
        got = parse_statement_text(text, TODAY)
        for r in got["rows"]:
            truth = BANK_TRUTH.get(r["description"])
            assert truth is not None, (how, r, text)  # never a balance or total line, never a stray one
            assert r["amount"] == truth[1], (how, r, text)
            assert r["date"].isoformat() == truth[0], (how, r, text)
        if how == "reorder":
            continue
        counted = {}
        for r in got["rows"]:
            counted[r["description"]] = counted.get(r["description"], 0) + 1
        lines = text.splitlines()
        for name in BANK_TRUTH:
            present = sum(1 for line in lines if name in line)
            assert counted.get(name, 0) <= present, (how, name, text)
