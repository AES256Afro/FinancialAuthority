"""Round twelve, the empty install: an account no bank has reported a balance for holds an unknown amount, not $0."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.main import app
from app.models import Account, Bill, Income

TODAY = date(2026, 10, 14)


def _install(balance: float | None, *, second: float | None = None, with_plan: bool = True) -> None:
    with Session(engine) as session:
        session.add(Account(name="Example Checking", institution="Example Bank", kind="checking", balance=balance, connector="csv"))
        if second is not None or with_plan == "two":
            session.add(Account(name="Example Cash Box", institution="", kind="cash", balance=second, connector="manual"))
        if with_plan:
            session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1200.0, next_date=date(2026, 10, 23)))
            session.add(Bill(name="Example Rent", amount=950.0, due_day=20))
            session.add(Bill(name="Example Phone", amount=60.0, due_day=17, autopay=True))
        session.commit()


def _sentences(obj) -> str:
    """The words a screen says out loud: every text, headline, line and action in a response."""
    if isinstance(obj, dict):
        return " ".join(str(v) if k in ("text", "headline", "line", "action", "one_thing") and isinstance(v, str) else _sentences(v) for k, v in obj.items())
    if isinstance(obj, list):
        return " ".join(_sentences(v) for v in obj)
    return ""


def _no_shortfall_words(text: str) -> None:
    low = text.lower()
    assert "short" not in low and "−$" not in text and "below zero" not in low and "dips" not in low, text


def test_a_new_install_with_no_balance_says_so_instead_of_a_shortfall(owner: TestClient):
    _install(None)
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is True and snap["cash"] is None
    safe = snap["safe_to_spend"]
    assert safe["amount"] is None and safe["after_goals"] is None and safe["no_balance"] is True and safe["parts"]["cash"] is None
    assert any("No bank has reported a balance yet" in c for c in snap["caveats"])
    assert snap["windows"][0]["carried_in"] is None and snap["windows"][0]["safe"] is None
    assert all(w["carried_in"] is not None for w in snap["windows"][1:])  # later windows start from set-asides, which are known
    assert all(w["low"] is None for w in snap["calendar"]["weeks"])
    assert [s["amount"] for s in safe["steps"] if s["label"].startswith("In ")] == [None]
    assert "as entered" not in str(safe["steps"])
    for item in snap["brief"]:
        _no_shortfall_words(item["text"])
    assert any("No bank has reported a balance" in i["text"] for i in snap["brief"])
    _no_shortfall_words(snap["why"]["safe_to_spend"])
    for key, text in snap["why"].items():
        _no_shortfall_words(text)

    today = owner.get("/api/v1/today").json() if owner.get("/api/v1/today").status_code == 200 else None
    if today is not None:
        _no_shortfall_words(_sentences(today))
    cover = owner.get("/api/v1/cover").json()
    spend = [a for a in cover["accounts"] if a["role"] == "spend"]
    assert spend and spend[0]["no_balance"] and "No balance yet" in spend[0]["headline"]
    assert not [r for r in cover["sheet"] if r["kind"] == "short"]
    check = owner.get("/api/v1/check", params={"amount": 50}).json()
    assert check["verdict"] == "outside" and check["breaks"] is None and check["no_balance"] is True and check["fits_safe_to_spend"] is None


def test_every_screen_answers_on_a_balance_with_no_figure(owner: TestClient):
    _install(None)
    skip = {"/api/v1/events", "/api/v1/stream"}
    failures = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/") or "{" in path or "GET" not in getattr(route, "methods", set()) or path in skip:
            continue
        r = owner.get(path)
        if r.status_code >= 500:
            failures.append((path, r.status_code))
    assert not failures, failures


def test_the_one_thing_the_watchlist_and_the_checkin_say_what_is_missing(owner: TestClient):
    _install(None)
    for path in ("/api/v1/guide/one-thing", "/api/v1/watchlist", "/api/v1/checkin", "/api/v1/glance"):
        r = owner.get(path)
        if r.status_code == 404:
            continue
        assert r.status_code == 200, (path, r.text)
        _no_shortfall_words(_sentences(r.json()))


def test_some_accounts_with_a_balance_are_counted_and_the_rest_named(owner: TestClient):
    _install(800.0, second=None, with_plan="two")
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is False and snap["cash"] == 800.0 and snap["safe_to_spend"]["amount"] is not None
    assert any("Example Cash Box" in c and "counted as holding nothing" in c for c in snap["caveats"])
    rows = {r["name"]: r for r in snap["safe_to_spend"]["by_account"]}
    assert rows["Example Cash Box"].get("no_balance") is True and not rows["Example Checking"].get("no_balance")


def test_a_balance_of_zero_is_a_balance(owner: TestClient):
    _install(0.0)
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is False and snap["cash"] == 0.0 and snap["safe_to_spend"]["amount"] < 0  # a real $0 is short, and says so


@pytest.mark.parametrize("plan", [True, False])
def test_no_accounts_at_all_is_no_balance(owner: TestClient, plan: bool):
    if plan:
        with Session(engine) as session:
            session.add(Income(name="Example Employer", cadence="biweekly", expected_net=1200.0, next_date=date(2026, 10, 23)))
            session.commit()
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is True and snap["safe_to_spend"]["amount"] is None
    for item in snap["brief"]:
        _no_shortfall_words(item["text"])


def test_the_report_answers_with_no_balance(owner: TestClient):
    _install(None)
    r = owner.post("/api/v1/exports/preview", json={})
    assert r.status_code < 500, r.text
    from app.exports.model import build_report
    with Session(engine) as session:
        build_report(session, TODAY)


# ---- naming a charge that the mail already made a bill for, and the first run's fourth step -----------------


def _series(merchant: str, amount: float):
    from app.models import RecurringSeries

    with Session(engine) as session:
        s = RecurringSeries(merchant_key=merchant.lower(), merchant=merchant, category="Bills & Utilities", cadence="monthly", typical_amount=amount, latest_amount=amount,
                            charges_seen=4, first_seen=date(2026, 6, 3), last_date=date(2026, 10, 3), next_expected=date(2026, 11, 3), active=True)
        session.add(s)
        session.commit()
        return s.id


def test_naming_a_bank_charge_links_the_bill_the_mail_made(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Northwind Power", amount=84.0, amount_mode="estimate", due_day=3))
        session.add(Bill(name="Northwind Power Gas", amount=40.0, due_day=9))  # a loose match at another amount is not it
        session.commit()
    sid = _series("NORTHWIND POWER AUTOPAY", 91.37)
    out = owner.post(f"/api/v1/recurring/{sid}/classify", json={"kind": "bill"}).json()
    assert out.get("linked") is True and out["bill"]["name"] == "Northwind Power" and out["bill"]["series_id"] == sid
    names = [b["name"] for b in owner.get("/api/v1/bills").json()["bills"]]
    assert sorted(names) == ["Northwind Power", "Northwind Power Gas"]  # no second Northwind Power


def test_naming_links_only_a_bill_of_a_similar_amount_and_the_exact_name_first(owner: TestClient):
    with Session(engine) as session:
        session.add(Bill(name="Example Water", amount=30.0, due_day=5))
        session.add(Bill(name="Example Water Works", amount=31.0, due_day=5))
        session.commit()
    sid = _series("Example Water Works", 31.0)
    out = owner.post(f"/api/v1/recurring/{sid}/classify", json={"kind": "bill"}).json()
    assert out["bill"]["name"] == "Example Water Works"  # the exact name wins over the one it starts with
    far = _series("Example Gym Club", 120.0)
    with Session(engine) as session:
        session.add(Bill(name="Example Gym", amount=20.0, kind="subscription", due_day=5))
        session.commit()
    made = owner.post(f"/api/v1/recurring/{far}/classify", json={"kind": "subscription"}).json()
    assert not made.get("linked") and made["bill"]["name"] == "Example Gym Club" and made["bill"]["amount"] == 120.0


def test_the_checks_step_ticks_for_an_earner_offered_only_the_check_before_it_is_due(owner: TestClient):
    _install(500.0)  # a biweekly earner with two bills paid from "due", the only choice Bills offers them
    steps = {s["id"]: s for s in owner.get("/api/v1/firstrun").json()["steps"]}
    assert steps["checks"]["done"] is True
    assert steps["paid"]["link"] == "/paystubs#paycheck"
    with Session(engine) as session:
        session.add(Income(name="Example Second Job", cadence="semimonthly", expected_net=600.0, next_date=date(2026, 10, 15)))
        session.commit()
    steps = {s["id"]: s for s in owner.get("/api/v1/firstrun").json()["steps"]}
    assert steps["checks"]["done"] is False  # a semi-monthly check is offered the 15th or the end: that is a real choice left


# ---- files only: the statement's own balance, and a typed balance on an account no bank feeds ----------------

OFX = """OFXHEADER:100
<OFX><SIGNONMSGSRSV1><SONRS><FI><ORG>Example Bank</ORG></FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD<BANKACCTFROM><ACCTID>000012345678<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261010<TRNAMT>-42.10<FITID>a1<NAME>Example Grocer</STMTTRN></BANKTRANLIST>
<LEDGERBAL><BALAMT>1534.20<DTASOF>20261012120000</LEDGERBAL>
<AVAILBAL><BALAMT>1480.05<DTASOF>20261013</AVAILBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CCACCTFROM><ACCTID>4111000000009876</CCACCTFROM>
<LEDGERBAL><BALAMT>-312.44<DTASOF>20261011</LEDGERBAL></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>"""


def test_the_ofx_parser_reads_each_statements_balance():
    from app.parsers.ofx_balance import balance_to_count, statement_balances

    found = statement_balances(OFX)
    assert [(f["account"], f["card"]) for f in found] == [("000012345678", False), ("4111000000009876", True)]
    assert balance_to_count(found[0]) == (1480.05, date(2026, 10, 13))  # the available balance, holds already out
    assert balance_to_count(found[1]) == (312.44, date(2026, 10, 11))  # a card: what is owed
    assert statement_balances("<OFX><STMTRS><LEDGERBAL><BALAMT>1e400<DTASOF>2026</LEDGERBAL></STMTRS>") == []
    assert statement_balances("no statement here") == []


def test_a_dropped_ofx_gives_a_files_only_owner_a_real_safe_to_spend(owner: TestClient, tmp_path):
    from app.jobs import scheduler

    path = tmp_path / "statement.ofx"
    path.write_text(OFX)
    with Session(engine) as session:
        said = scheduler._import_table(session, path)
    assert "new" in said
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is False
    checking = next(a for a in snap["accounts"] if a["kind"] == "checking")
    assert checking["balance"] == 1480.05 and checking["balance_at"].startswith("2026-10-13")


def test_an_uploaded_ofx_sets_the_statements_balance_too(owner: TestClient):
    import io

    r = owner.post("/api/v1/imports/file", files={"file": ("statement.ofx", io.BytesIO(OFX.encode()), "application/octet-stream")})
    assert r.status_code == 200, r.text
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is False
    landed = next(a for a in snap["accounts"] if a["id"] == r.json()["account_id"])
    assert landed["balance"] == 1480.05


def test_a_statement_never_overwrites_a_newer_balance_or_a_bank_feed():
    from datetime import datetime, timezone

    from app.services import apply_statement_balances

    with Session(engine) as session:
        typed = Account(name="Example Files Checking", institution="Example Bank", kind="checking", balance=900.0, balance_at=datetime(2026, 10, 14, 9, tzinfo=timezone.utc), connector="file")
        linked = Account(name="Example Linked", institution="Example Bank", kind="checking", balance=50.0, balance_at=datetime(2026, 10, 1, tzinfo=timezone.utc), connector="plaid")
        session.add_all([typed, linked])
        session.commit()
        assert apply_statement_balances(session, OFX.split("<CREDITCARDMSGSRSV1>")[0], typed.id) == []
        assert apply_statement_balances(session, OFX.split("<CREDITCARDMSGSRSV1>")[0], linked.id) == []
        session.refresh(typed)
        session.refresh(linked)
        assert typed.balance == 900.0 and linked.balance == 50.0


def test_a_typed_balance_on_an_unlinked_account_stands_past_a_day(owner: TestClient):
    from app.api import routes

    _install(None)
    with Session(engine) as session:
        aid = session.exec(__import__("sqlmodel").select(Account.id)).first()
    assert owner.post(f"/api/v1/banks/accounts/{aid}/shows", json={"amount": 640.0}).status_code == 200
    routes.set_today_override(date(2026, 10, 18))
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["no_balance"] is False and snap["cash"] == 640.0


def test_backups_on_an_install_minutes_old_need_no_look(owner: TestClient, monkeypatch):
    from datetime import datetime, timezone

    from app import restore
    from app.engine import apphealth
    from app.models import JobRun

    monkeypatch.setattr(restore, "list_points", lambda: [])  # a new install: no copy taken yet
    status = owner.get("/api/v1/restore-points").json()["status"]
    assert status["count"] == 0 and status["copy_due"] is False and status["state"] == "new"
    part = next(c for c in apphealth.checks_from_report({"free_gb": 50}, None, {"key": "jobs", "label": "Jobs", "state": "good", "note": ""}, datetime.now(timezone.utc), copy_due=False) if c["key"] == "backups")
    assert part["state"] == "good"
    with Session(engine) as session:
        session.add(JobRun(name="restore_point_daily", finished_at=datetime.now(timezone.utc), ok=False))
        session.commit()
    status = owner.get("/api/v1/restore-points").json()["status"]
    assert status["copy_due"] is True and status["state"] == "warn"  # the daily copy ran and left nothing: that is a problem


def test_the_brief_says_which_tap(owner: TestClient):
    _install(2000.0)
    text = next(i["text"] for i in owner.get("/api/v1/snapshot").json()["brief"] if "clears before payday" in i["text"])
    assert "needs a tap" not in text and "Example Rent is not on autopay: pay it yourself, or turn on autopay on Bills." in text


def test_a_weekly_bills_mail_with_no_heading_in_its_body_still_lists_each_charge():
    from app.parsers.rocketmoney_bills import parse_upcoming_bills

    body = "Example Water Utility\n\nMonday, October 12\n\n$61.40\n\nStreamflix\n\nThursday, October 15\n\n$15.99\n\nOpen Rocket Money\n"
    assert [c["name"] for c in parse_upcoming_bills(body, date(2026, 10, 9))] == ["Example Water Utility", "Streamflix"]
    headed = "Hi, 1 recurring charge is coming up this week.\n\nExample Gym\n\nSaturday, October 17\n\n$29.00\n"
    assert [c["amount"] for c in parse_upcoming_bills(headed, date(2026, 10, 9))] == [29.0]


def test_readiness_never_counts_a_missing_balance_as_a_cushion_of_nothing():
    """No balance yet: the cushion item cannot be judged, so it shows no figure and points at the banks, not $0 short."""
    from datetime import date as _date

    from sqlmodel import Session as _Session

    from app.db import engine as _engine
    from app.engine.readiness import readiness

    with _Session(_engine) as session:
        unknown = next(i for i in readiness(session, _date(2026, 10, 14), safe_to_spend=None)["items"] if i["key"] == "buffer")
        known = next(i for i in readiness(session, _date(2026, 10, 14), safe_to_spend=900.0)["items"] if i["key"] == "buffer")
    assert unknown["value"] is None and unknown["met"] is False and "no bank has reported a balance" in unknown["note"]
    assert known["value"] == 900.0 and known["met"] is True
