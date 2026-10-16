"""M20: money between the owner's own accounts matched, and the same purchase seen twice offered for merging."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.pairs import decide, find_pairs
from app.models import Account, Transaction, TransactionPair

TODAY = date(2026, 10, 14)


def _accounts(session: Session) -> tuple[Account, Account, Account]:
    chk = Account(name="Checking", kind="checking", connector="plaid", external_id="a-chk", balance=900.0)
    sav = Account(name="Savings", kind="savings", connector="plaid", external_id="a-sav", balance=1500.0)
    card = Account(name="Card", kind="card", connector="plaid", external_id="a-card", balance=300.0)
    session.add_all([chk, sav, card])
    session.flush()
    return chk, sav, card


def _tx(session: Session, account: Account, when: date, amount: float, merchant: str, source: str = "plaid", **flags) -> Transaction:
    t = Transaction(account_id=account.id, date=when, merchant=merchant, amount=amount, source=source, row_hash=f"{source}-{account.id}-{when}-{amount}-{merchant}", **flags)
    session.add(t)
    session.flush()
    return t


def test_a_transfer_and_a_card_payment_are_matched_and_leave_spending():
    with Session(engine) as session:
        chk, sav, card = _accounts(session)
        out = _tx(session, chk, date(2026, 10, 5), 200.0, "Online Transfer to Savings")
        arrived = _tx(session, sav, date(2026, 10, 6), -200.0, "Transfer from Checking")
        pay = _tx(session, chk, date(2026, 10, 7), 300.0, "CARD AUTOPAY")
        credit = _tx(session, card, date(2026, 10, 8), -300.0, "PAYMENT THANK YOU")
        session.commit()
        got = find_pairs(session, TODAY)
        assert got == {"matched": 2, "waiting": 0, "gone": 0}
        for row in (out, arrived):
            session.refresh(row)
            assert row.is_transfer and not row.is_spending
        for row in (pay, credit):
            session.refresh(row)
            assert row.is_card_payment
        assert find_pairs(session, TODAY)["matched"] == 0  # once only


def test_an_unsure_pair_waits_and_a_no_puts_the_rows_back():
    with Session(engine) as session:
        chk, sav, _card = _accounts(session)
        a = _tx(session, chk, date(2026, 10, 5), 75.0, "Example Shop")
        b = _tx(session, sav, date(2026, 10, 5), -75.0, "Example Refund")
        session.commit()
        assert find_pairs(session, TODAY)["waiting"] == 1
        session.refresh(a)
        assert not a.is_transfer  # nothing changes until the owner says yes
        pair = session.exec(select(TransactionPair)).one()
        decide(session, pair.id, "yes")
        session.refresh(a)
        session.refresh(b)
        assert a.is_transfer and b.is_transfer
        decide(session, pair.id, "no")
        session.refresh(a)
        assert not a.is_transfer and session.get(TransactionPair, pair.id).status == "rejected"
        assert find_pairs(session, TODAY)["waiting"] == 0  # a no is never asked again


def test_the_same_purchase_twice_is_offered_and_merging_hides_the_second(owner: TestClient):
    with Session(engine) as session:
        chk, _sav, _card = _accounts(session)
        _tx(session, chk, date(2026, 10, 9), 23.5, "EXAMPLE CAFE")
        dup = _tx(session, chk, date(2026, 10, 9), 23.5, "Example Cafe", source="rocketmoney")
        session.commit()
        find_pairs(session, TODAY)
        dup_id = dup.id
    listed = owner.get("/api/v1/pairs").json()
    assert listed["counts"]["waiting"] == 1 and listed["waiting"][0]["kind"] == "duplicate"
    assert owner.get("/api/v1/snapshot").json()["pending"]["pairs"] == 1
    decided = owner.post(f"/api/v1/pairs/{listed['waiting'][0]['id']}", json={"decision": "yes"}).json()
    assert decided["status"] == "confirmed"
    with Session(engine) as session:
        assert session.get(Transaction, dup_id).is_ignored
    assert owner.post("/api/v1/pairs/9999", json={"decision": "yes"}).status_code == 404
    assert owner.post(f"/api/v1/pairs/{listed['waiting'][0]['id']}", json={"decision": "maybe"}).status_code == 400
