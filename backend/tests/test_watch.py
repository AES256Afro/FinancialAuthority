"""M34: the assistant that watches the ledger."""

from datetime import date, timedelta

from sqlmodel import Session

from app.db import engine
from app.engine.watch import dismiss, findings, to_announce
from app.models import Account, RecurringSeries, Transaction

TODAY = date(2026, 10, 14)


def _ledger(session: Session) -> Account:
    chk = Account(name="Checking", kind="checking", connector="plaid", external_id="w-chk", balance=900.0)
    session.add(chk)
    session.flush()
    rows = [(TODAY - timedelta(days=d), 12.0, "Example Gym") for d in (90, 60, 30)]
    rows += [(TODAY - timedelta(days=3), 89.0, "Example Gym")]  # far more than the gym usually takes
    rows += [(TODAY - timedelta(days=5), 49.99, "Example Store"), (TODAY - timedelta(days=4), 49.99, "Example Store")]  # twice
    for i, (when, amount, merchant) in enumerate(rows):
        session.add(Transaction(account_id=chk.id, date=when, merchant=merchant, amount=amount, category="Shopping", source="plaid", row_hash=f"w-{i}"))
    session.add(RecurringSeries(merchant_key="example stream", merchant="Example Stream", cadence="monthly", typical_amount=9.99, latest_amount=12.99, charges_seen=3,
                                first_seen=TODAY - timedelta(days=20), last_date=TODAY - timedelta(days=2), next_expected=TODAY + timedelta(days=28)))
    session.commit()
    return chk


def test_the_assistant_finds_what_deserves_a_look_and_forgets_what_is_expected():
    with Session(engine) as session:
        _ledger(session)
        found = {f["kind"]: f for f in findings(session, TODAY)}
        assert set(found) == {"unusual", "double", "new", "rise"}
        assert found["unusual"]["rows"][0]["merchant"] == "Example Gym" and found["unusual"]["amount"] == 77.0
        assert len(found["double"]["rows"]) == 2
        assert all(f["text"] and "$" in f["text"] for f in found.values())
        dismiss(session, found["double"]["key"], TODAY)
        assert "double" not in {f["kind"] for f in findings(session, TODAY)}
        first = to_announce(session, TODAY)
        assert {f["kind"] for f in first} == {"unusual", "new", "rise"} and to_announce(session, TODAY) == []  # each told once
