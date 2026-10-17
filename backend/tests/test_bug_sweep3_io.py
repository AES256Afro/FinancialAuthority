"""The third bug review's findings around the engine (BUGIO): bank connectors, jobs, imports, restore points and
the off-box copy, the exports, push, the tool mirror for agents without MCP. Each is held by a test on synthetic
rows; no real bank, push service or mailbox is touched."""

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors import CONNECTORS
from app.db import engine
from app.models import Account, BankLink, Bill, Document, Payment, RecurringSeries, Transaction, TransactionPair, TransactionSplit
from app.store import get_setting, set_setting

TODAY = date(2026, 10, 14)
PASSPHRASE = "four plain words kept safe"


@pytest.fixture(autouse=True)
def _restore_points_left_as_found():
    """Restore points live beside the database, not in it: the ones a test here makes are removed after it."""
    from app import restore

    before = set(restore.folder().glob("*"))
    yield
    for path in set(restore.folder().glob("*")) - before:
        path.unlink(missing_ok=True)


# ---- a Plaid that answers from a script -------------------------------------------------------------------------


def _item(n: str, accounts: list[dict], pages: list[dict]):
    """One Plaid item: its accounts and its sync pages; every other call answers plainly."""
    pages = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/item/public_token/exchange":
            return httpx.Response(200, json={"access_token": f"access-sandbox-{n}", "item_id": f"item-{n}"})
        if path == "/accounts/get":
            return httpx.Response(200, json={"item": {"institution_name": "Example Bank"}, "accounts": accounts})
        if path == "/transactions/sync":
            return httpx.Response(200, json=pages.pop(0) if pages else {"added": [], "modified": [], "removed": [], "next_cursor": "end", "has_more": False})
        return httpx.Response(200, json={"removed": True, "link_token": "link-x"})

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _checking(n: str, balance: float = 900.0) -> list[dict]:
    return [{"account_id": f"chk-{n}", "name": "Everyday Checking", "mask": "1234", "type": "depository", "subtype": "checking", "balances": {"current": balance, "available": balance}}]


def _grocer_rows(n: str) -> list[dict]:
    return [{"added": [{"transaction_id": f"{n}-t{i}", "account_id": f"chk-{n}", "amount": 40.0 + i, "date": f"2026-10-0{i}", "name": "EXAMPLE GROCER", "merchant_name": "Example Grocer",
                        "pending": False, "personal_finance_category": {"primary": "FOOD_AND_DRINK"}} for i in range(1, 6)], "modified": [], "removed": [], "next_cursor": "c1", "has_more": False}]


@pytest.fixture
def plaid(owner: TestClient):
    before = CONNECTORS["plaid"].client_factory
    CONNECTORS["plaid"].client_factory = _item("keys", [], [])  # the key check answers from here, never from Plaid
    owner.put("/api/v1/banks/plaid/settings", json={"client_id": "cid", "secret": "synthetic-secret", "environment": "sandbox"})
    yield owner
    CONNECTORS["plaid"].client_factory = before


def _link(owner: TestClient, factory) -> dict:  # noqa: ANN001
    CONNECTORS["plaid"].client_factory = factory
    out = owner.post("/api/v1/banks/plaid/exchange", json={"public_token": "public-sandbox", "institution": "Example Bank"})
    assert out.status_code == 200, out.text
    return out.json()


# ---- BUGIO-01: undoing an import lets go of everything kept with its rows ---------------------------------------


def test_undoing_an_import_leaves_nothing_for_the_next_rows_to_inherit(owner: TestClient):
    from app import importlog
    from app.connectors.files import import_rows
    from app.engine.documents import save_text
    from app.engine.edits import set_splits

    def row(day: int, merchant: str, amount: float, category: str) -> dict:
        return {"date": date(2026, 10, day), "posted": None, "merchant": merchant, "description": merchant.upper(), "amount": amount, "category": category, "fitid": ""}

    with Session(engine) as s:
        first = import_rows(s, [row(1, "Synthetic Hardware", 100.0, "Home")], source_file="first.csv", account_name="Everyday Checking")
        tx = s.exec(select(Transaction)).one()
        set_splits(s, tx.id, [{"category": "Home", "amount": 60.0}, {"category": "Gifts", "amount": 40.0}])
        receipt = save_text(s, "Receipt: synthetic hardware, total $100.00", filename="receipt", title="Hardware receipt", kind="receipt", attach_to=f"transaction:{tx.id}", amount=100.0)
        other = Transaction(account_id=tx.account_id, date=date(2026, 10, 1), merchant="Synthetic Transfer", amount=-100.0, row_hash="bugio01-other", is_transfer=True)
        s.add(other)
        s.flush()
        s.add(TransactionPair(kind="transfer", a_id=tx.id, b_id=other.id, status="auto", before=json.dumps({"a": {"is_transfer": False}, "b": {"is_transfer": False}})))
        s.commit()
        importlog.undo(s, first.details["import_id"], TODAY)
        assert s.exec(select(TransactionSplit)).all() == [] and s.exec(select(TransactionPair)).all() == []
        assert s.get(Document, receipt.id).attach_to == ""  # kept, shown as not matched yet
        assert s.get(Transaction, other.id).is_transfer is False  # the pairing is undone on the row left behind
        import_rows(s, [row(3, "Synthetic Cafe", 25.0, "Dining & Drinks")], source_file="second.csv", account_name="Everyday Checking")
        cafe = s.exec(select(Transaction).where(Transaction.merchant == "Synthetic Cafe")).one()
        assert s.exec(select(TransactionSplit).where(TransactionSplit.transaction_id == cafe.id)).all() == []
        assert s.exec(select(Document).where(Document.attach_to == f"transaction:{cafe.id}")).all() == []


# ---- BUGIO-04: removing a link and linking the bank again ---------------------------------------------------------


def test_linking_a_bank_again_takes_over_its_accounts_and_history(plaid: TestClient):
    first = _link(plaid, _item("A", _checking("A"), _grocer_rows("A")))["link"]
    with Session(engine) as s:
        row = s.exec(select(Transaction).where(Transaction.source_ref == "A-t1")).one()
        row.category, row.note = "Household", "bulk buy"
        s.add(row)
        s.commit()
    assert plaid.delete(f"/api/v1/banks/{first['id']}").json() == {"ok": True}
    second = _link(plaid, _item("B", _checking("B"), _grocer_rows("B")))
    assert [a["id"] for a in second["link"]["accounts"]] == [a["id"] for a in first["accounts"]] and second["sync"]["inserted"] == 0
    with Session(engine) as s:
        active = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
        assert [(a.name, a.connector, a.external_id, a.balance) for a in active] == [("Everyday Checking", "plaid", "chk-B", 900.0)]
        grocer = s.exec(select(Transaction).where(Transaction.merchant == "Example Grocer")).all()
        assert len(grocer) == 5 and {t.source_ref for t in grocer} == {f"B-t{i}" for i in range(1, 6)} and {t.source for t in grocer} == {"plaid"}
        kept = next(t for t in grocer if t.source_ref == "B-t1")
        assert (kept.category, kept.note) == ("Household", "bulk buy")  # the owner's words came along


def test_linking_the_same_bank_twice_reads_each_account_once(plaid: TestClient):
    first = _link(plaid, _item("A", _checking("A"), _grocer_rows("A")))["link"]
    with Session(engine) as s:  # the first link needs a new sign-in, so the owner links the bank afresh: the new link stays
        broken = s.get(BankLink, first["id"])
        broken.needs_login = True
        s.add(broken)
        s.commit()
    second = _link(plaid, _item("C", _checking("C"), _grocer_rows("C")))
    assert "duplicate" not in second
    assert second["sync"]["inserted"] == 0 and "already read through your other Example Bank link" in second["sync"]["summary"]
    with Session(engine) as s:
        assert len(s.exec(select(Account).where(Account.active == True)).all()) == 1  # noqa: E712
        assert len(s.exec(select(Transaction).where(Transaction.merchant == "Example Grocer")).all()) == 5
    # once the first link is removed, the second takes the account over on its next sync
    plaid.delete(f"/api/v1/banks/{first['id']}")
    synced = plaid.post(f"/api/v1/banks/{second['link']['id']}/sync").json()
    assert [a["name"] for a in synced["link"]["accounts"]] == ["Everyday Checking"]
    with Session(engine) as s:
        assert [a.external_id for a in s.exec(select(Account).where(Account.active == True)).all()] == ["chk-C"]  # noqa: E712


def test_a_removed_links_accounts_can_become_history(plaid: TestClient):
    first = _link(plaid, _item("A", _checking("A"), _grocer_rows("A")))["link"]
    assert plaid.delete(f"/api/v1/banks/{first['id']}?accounts=nonsense").status_code == 400
    assert plaid.delete(f"/api/v1/banks/{first['id']}?accounts=history").json() == {"ok": True, "accounts_retired": 1}
    with Session(engine) as s:
        assert s.exec(select(Account).where(Account.active == True)).all() == []  # noqa: E712
        assert len(s.exec(select(Transaction)).all()) == 5  # kept, not counted
    _link(plaid, _item("B", _checking("B"), _grocer_rows("B")))
    with Session(engine) as s:
        assert [a.external_id for a in s.exec(select(Account).where(Account.active == True)).all()] == ["chk-B"]  # noqa: E712
        assert len(s.exec(select(Transaction)).all()) == 5


# ---- BUGIO-05: a pending charge that posts keeps its receipt and the owner's edits --------------------------------


def test_a_pending_charge_that_posts_keeps_its_receipt_split_and_edits(plaid: TestClient):
    from app.engine.documents import save_text
    from app.engine.edits import set_splits, update
    from app.engine.receipts import receipts_overview

    pages = [
        {"added": [], "modified": [], "removed": [], "next_cursor": "c0", "has_more": False},
        # the removal of the pending row comes a page before the posted row that names it
        {"added": [], "modified": [], "removed": [{"transaction_id": "p1"}], "next_cursor": "c1", "has_more": True},
        {"added": [{"transaction_id": "q1", "account_id": "chk-A", "amount": 79.99, "date": "2026-10-12", "name": "EXAMPLE ELECTRONICS", "merchant_name": "Example Electronics", "pending": False,
                    "pending_transaction_id": "p1", "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"}}], "modified": [], "removed": [], "next_cursor": "c2", "has_more": False},
    ]
    first = {"added": [{"transaction_id": "p1", "account_id": "chk-A", "amount": 79.99, "date": "2026-10-10", "authorized_date": "2026-10-10", "name": "EXAMPLE ELECTRONICS",
                        "merchant_name": "Example Electronics", "pending": True, "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"}}], "modified": [], "removed": [], "next_cursor": "c0", "has_more": False}
    link = _link(plaid, _item("A", _checking("A"), [first, *pages[1:]]))["link"]
    with Session(engine) as s:
        pending = s.exec(select(Transaction).where(Transaction.source_ref == "p1")).one()
        assert pending.pending
        update(s, pending.id, category="Electronics", note="gift for Sam", tags=["gift"])
        set_splits(s, pending.id, [{"category": "Electronics", "amount": 59.99}, {"category": "Gifts", "amount": 20.0}])
        receipt = save_text(s, "Order EX-1, total $79.99", filename="r", title="Receipt", kind="receipt", attach_to=f"transaction:{pending.id}", amount=79.99, merchant="Example Electronics",
                            bought_on=date(2026, 10, 10))
    plaid.post(f"/api/v1/banks/{link['id']}/sync")
    with Session(engine) as s:
        assert s.exec(select(Transaction).where(Transaction.source_ref == "p1")).first() is None
        posted = s.exec(select(Transaction).where(Transaction.source_ref == "q1")).one()
        assert (posted.category, posted.note, posted.tags) == ("Electronics", "gift for Sam", "gift")
        assert sorted(p.amount for p in s.exec(select(TransactionSplit).where(TransactionSplit.transaction_id == posted.id)).all()) == [20.0, 59.99]
        assert s.get(Document, receipt.id).attach_to == f"transaction:{posted.id}"
        assert receipts_overview(s, TODAY)["unmatched"] == []


# ---- BUGIO-10: an account the owner removed stays removed ----------------------------------------------------------


def test_an_account_removed_on_investments_stays_removed_after_a_sync(plaid: TestClient):
    brokerage = [{"account_id": "k401", "name": "Old Employer 401k", "mask": "4321", "type": "investment", "subtype": "401k", "balances": {"current": 5000.0}}]
    link = _link(plaid, _item("A", brokerage, []))["link"]
    account_id = link["accounts"][0]["id"]
    plaid.delete(f"/api/v1/investments/{account_id}")
    brokerage[0]["balances"]["current"] = 6000.0
    plaid.post(f"/api/v1/banks/{link['id']}/sync")
    with Session(engine) as s:
        account = s.get(Account, account_id)
        assert account.active is False and account.balance == 5000.0
    assert all(a["id"] != account_id for a in plaid.get("/api/v1/investments").json().get("accounts", []))


# ---- BUGIO-18: Sync now pays the bill its rows paid -----------------------------------------------------------------


def test_sync_now_matches_payments_like_the_scheduled_sync(plaid: TestClient):
    with Session(engine) as s:
        s.add(Bill(name="Example Grocer", amount=41.0, due_day=1, created_on=date(2026, 9, 1)))
        s.commit()
    linked = _link(plaid, _item("A", _checking("A"), _grocer_rows("A")))
    assert "payments" in linked["detection"]
    with Session(engine) as s:
        paid = s.exec(select(Payment).where(Payment.due_date == date(2026, 10, 1))).one()
        assert paid.status == "paid" and paid.transaction_id is not None


# ---- BUGIO-09: one writer at a time -------------------------------------------------------------------------------


def test_two_detection_runs_at_once_add_each_series_once():
    from app.engine.household import seed
    from app.jobs.detect import _series_key, run_detection

    with Session(engine) as s:
        seed(s)
        for series in s.exec(select(RecurringSeries)).all():
            s.delete(series)
        s.commit()
    gate = threading.Barrier(2)
    errors: list[Exception] = []

    def run() -> None:
        try:
            with Session(engine) as s:
                gate.wait()
                run_detection(s, TODAY)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with Session(engine) as s:
        keys = [_series_key({"merchant_key": r.merchant_key, "cadence": r.cadence, "typical_amount": r.typical_amount}) for r in s.exec(select(RecurringSeries)).all()]
        assert keys and len(keys) == len(set(keys))
        # a twin left by an older race, nobody's and unconfirmed, goes on the next run
        one = s.exec(select(RecurringSeries)).first()
        s.add(RecurringSeries(merchant_key=one.merchant_key, merchant=one.merchant, cadence=one.cadence, typical_amount=one.typical_amount, latest_amount=one.latest_amount,
                              first_seen=one.first_seen, last_date=one.last_date, next_expected=one.next_expected))
        s.commit()
        run_detection(s, TODAY)
        assert len(s.exec(select(RecurringSeries).where(RecurringSeries.merchant_key == one.merchant_key, RecurringSeries.cadence == one.cadence)).all()) == 1


def test_run_now_never_overlaps_the_same_jobs_scheduled_run():
    from app.jobs.scheduler import jobs

    live = {"now": 0, "most": 0}

    def slow() -> str:
        live["now"] += 1
        live["most"] = max(live["most"], live["now"])
        time.sleep(0.2)
        live["now"] -= 1
        return "done"

    jobs.register("bugio_overlap", slow, 3600, "a test job")
    try:
        threads = [threading.Thread(target=jobs.run, args=("bugio_overlap",)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert live["most"] == 1
    finally:
        jobs.specs.pop("bugio_overlap", None)
        jobs.running.pop("bugio_overlap", None)


# ---- BUGIO-11: push that fails is a failed job, and the self-check says so ----------------------------------------


def test_push_that_fails_fails_the_job_and_the_self_check_says_so(owner: TestClient, monkeypatch):
    from app import notify
    from app.engine.household import seed
    from app.jobs.scheduler import IDLE, jobs

    with Session(engine) as s:
        seed(s)
    owner.put("/api/v1/push", json={"kind": "ntfy", "url": "https://ntfy.example.test", "topic": "fa", "brief_hour": 0})
    owner.put("/api/v1/push/reminders", json={"bills_days": 2, "hour": 0})
    answer = {"status": 503}
    real = httpx.Client
    monkeypatch.setattr(notify.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(answer["status"], text="Service Unavailable"))))
    reminders = jobs.run("reminders")
    assert reminders.ok is False and "not delivered" in reminders.summary and not reminders.summary.startswith(IDLE)
    brief = jobs.run("morning_brief")
    assert brief.ok is False and brief.summary.startswith("RuntimeError: not sent")
    jobs.run("self_check")
    with Session(engine) as s:
        assert any(p.startswith("push to your phone failed") for p in get_setting(s, "selfcheck")["problems"])
    answer["status"] = 200
    again = jobs.run("reminders")
    assert again.ok is True and again.summary.startswith("sent ") and again.summary != "sent 0"
    jobs.run("self_check")
    with Session(engine) as s:
        assert not any(p.startswith("push to your phone failed") for p in get_setting(s, "selfcheck")["problems"])


def test_a_time_zone_the_server_does_not_know_never_stops_the_brief_or_the_reminders(monkeypatch):
    from app import notify
    from app.engine.reminders import run

    monkeypatch.setenv("TZ", "Nowhere/City")  # zoneinfo has no such zone: the app runs on the server's own time
    now = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
    with Session(engine) as s:
        set_setting(s, "push", {"kind": "ntfy", "url": "https://ntfy.example.test", "brief_hour": 0})
        assert notify.morning_brief_due(s, now) is True
        notify.mark_brief_sent(s, now)
        assert run(s, now, TODAY)["sent"] >= 0


# ---- BUGIO-15: stale balances are the ones a link should have refreshed --------------------------------------------


def test_the_self_check_counts_only_balances_a_link_should_refresh():
    from app.jobs.scheduler import self_check

    old = datetime.now(timezone.utc) - timedelta(days=5)
    with Session(engine) as s:
        s.add(Account(name="Synthetic Store Card", kind="card", connector="manual", balance=120.0, balance_at=old))
        s.add(Account(name="Synthetic Old Checking", kind="checking", connector="plaid", external_id="gone", balance=0.0, balance_at=old, active=False))
        s.add(Account(name="Synthetic Linked Checking", kind="checking", connector="plaid", external_id="live", balance=50.0, balance_at=old))
        s.commit()
    self_check()
    with Session(engine) as s:
        report = get_setting(s, "selfcheck")
    assert report["stale_accounts"] == ["Synthetic Linked Checking"]
    assert [t["name"] for t in report["typed_balances"]] == ["Synthetic Store Card"]
    assert not any("Store Card" in p or "Old Checking" in p for p in report["problems"])


# ---- BUGIO-06 and BUGIO-16: the off-box copy and the kept files ----------------------------------------------------


def _upload(owner: TestClient, text: str, title: str) -> tuple[int, str]:
    up = owner.post("/api/v1/documents", files={"file": (f"{title}.txt", text.encode(), "text/plain")}, data={"title": title, "kind": "other"})
    assert up.status_code == 200, up.text
    with Session(engine) as s:
        doc = s.get(Document, up.json()["id"])
        return doc.id, doc.sha256


def test_the_off_box_copy_never_pushes_out_the_owners_points(owner: TestClient, tmp_path):
    from app import restore

    owner.put("/api/v1/restore-points/offbox", json={"folder": str(tmp_path), "passphrase": PASSPHRASE, "every_days": 1})
    mine = owner.post("/api/v1/restore-points").json()["made"]["name"]
    restore.make_point("import", "an import after it")  # the newest point is not the database as it is now
    for _ in range(11):
        assert owner.post("/api/v1/restore-points/offbox/copy").status_code == 200
    points = restore.list_points()
    assert mine in [p["name"] for p in points]
    assert [p["name"] for p in points if p["kind"] == "manual"] == [mine]
    assert 1 <= len([p for p in points if p["kind"] == "offbox"]) <= restore.KEEP["offbox"]
    # a folder that cannot be written takes no point at all
    count = len(restore.list_points())
    owner.put("/api/v1/restore-points/offbox", json={"folder": str(tmp_path / "not-mounted")})
    assert owner.post("/api/v1/restore-points/offbox/copy").status_code == 400
    assert len(restore.list_points()) == count


def test_the_off_box_copy_carries_the_documents_and_an_old_copy_still_opens(owner: TestClient, tmp_path, monkeypatch, capsys):
    from app import offbox, restore
    from app.engine import documents as docs

    _, sha = _upload(owner, "Synthetic lease for the off-box copy test", "Lease")
    owner.put("/api/v1/restore-points/offbox", json={"folder": str(tmp_path), "passphrase": PASSPHRASE})
    made = owner.post("/api/v1/restore-points/offbox/copy").json()["made"]
    assert made["documents"] >= 1
    sealed = tmp_path / made["file"]
    assert sealed.read_bytes().startswith(offbox.MAGIC2)
    assert restore.check(_write(tmp_path / "unsealed.db", offbox.unseal(sealed.read_bytes(), PASSPHRASE)))["ok"]
    with pytest.raises(Exception):
        offbox.unseal(sealed.read_bytes()[:-1], PASSPHRASE)  # cut short: it does not open
    (tmp_path / "out").mkdir()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSPHRASE)
    assert offbox.main(["open", str(sealed), str(tmp_path / "out" / "restored.db")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["check"] == "ok" and printed["documents"] >= 1
    assert (tmp_path / "out" / "documents" / f"{sha}.bin").read_bytes() == (docs.folder() / f"{sha}.bin").read_bytes()
    # a copy made before documents were carried (FABK1, the database alone) still opens
    point = restore.make_point("manual", "for an old-format copy")
    old = tmp_path / "old.fabackup"
    old.write_bytes(offbox.seal((restore.folder() / point["name"]).read_bytes(), PASSPHRASE))
    opened = offbox.open_copy(old, PASSPHRASE, tmp_path / "old.db")
    assert opened["documents"] == 0 and restore.check(tmp_path / "old.db")["ok"]


def _write(path, data: bytes):  # noqa: ANN001, ANN202
    path.write_bytes(data)
    return path


def test_a_removed_documents_file_stays_while_a_restore_point_names_it(owner: TestClient):
    from app import restore
    from app.engine import documents as docs

    doc_id, sha = _upload(owner, "Synthetic warranty kept for the restore test, two years", "Old warranty")
    point = restore.make_point("manual", "before the delete")
    assert sha in restore.point_documents(point["name"])
    # what remove_document does: the row goes, then restore decides about the file
    with Session(engine) as s:
        s.delete(s.get(Document, doc_id))
        s.commit()
    assert restore.release_document_file(sha) is False and (docs.folder() / f"{sha}.bin").exists()
    done = owner.post(f"/api/v1/restore-points/{point['name']}/restore").json()
    assert done["documents_missing"] == 0
    assert owner.get(f"/api/v1/documents/{doc_id}/file").status_code == 200  # the row is back, and so is its file
    # once neither the database nor any point names it, the file goes
    with Session(engine) as s:
        s.delete(s.get(Document, doc_id))
        s.commit()
    for p in restore.list_points():
        if sha in restore.point_documents(p["name"]):
            restore.remove_point(p["name"])
    assert restore.release_document_file(sha) is True or not (docs.folder() / f"{sha}.bin").exists()
    assert not (docs.folder() / f"{sha}.bin").exists()


def test_prune_leaves_files_that_are_named_or_new():
    from app import restore

    where = restore.documents_folder()
    where.mkdir(parents=True, exist_ok=True)
    stray = where / ("e" * 64 + ".bin")
    fresh = where / ("f" * 64 + ".bin")
    stray.write_bytes(b"x")
    fresh.write_bytes(b"y")
    past = time.time() - 7200
    import os

    os.utime(stray, (past, past))
    assert restore.prune_documents() >= 1
    assert not stray.exists() and fresh.exists()
    fresh.unlink()


# ---- BUGIO-07: a restore leaves privacy, keys, push and bank links as they are now -------------------------------


def test_a_restore_keeps_privacy_backup_push_and_bank_access_as_they_are_now(owner: TestClient):
    from app import restore

    with Session(engine) as s:
        set_setting(s, "models", {"advisor": {"provider": "anthropic"}})
        set_setting(s, "models.secrets", {"anthropic": "old-sealed"})
        set_setting(s, "backups.offbox", {"folder": "/old", "passphrase_enc": "old"})
        set_setting(s, "webpush.vapid", {"public": "old", "private_enc": "old"})
        s.add(BankLink(provider="plaid", external_id="item-1", institution="Example Bank", access_token_enc="tok-1", cursor="c1"))
        s.commit()
    point = restore.make_point("manual", "before the changes")
    with Session(engine) as s:
        set_setting(s, "models", {"advisor": {"provider": "off"}})
        set_setting(s, "models.secrets", {})
        set_setting(s, "backups.offbox", {"folder": "/new", "passphrase_enc": "new"})
        set_setting(s, "webpush.vapid", {"public": "new", "private_enc": "new"})
        link = s.exec(select(BankLink)).one()
        link.active, link.access_token_enc, link.cursor = False, "", "c9"
        s.add(link)
        s.add(BankLink(provider="plaid", external_id="item-2", institution="Second Bank", access_token_enc="tok-2", cursor="z5"))
        s.commit()
    assert owner.post(f"/api/v1/restore-points/{point['name']}/restore").status_code == 200
    with Session(engine) as s:
        assert get_setting(s, "models") == {"advisor": {"provider": "off"}} and get_setting(s, "models.secrets") == {}
        assert get_setting(s, "backups.offbox")["folder"] == "/new" and get_setting(s, "webpush.vapid")["public"] == "new"
        links = {l.external_id: l for l in s.exec(select(BankLink)).all()}
        assert (links["item-1"].active, links["item-1"].access_token_enc, links["item-1"].cursor) == (False, "", "c1")  # removed stays removed; its cursor matches the rows
        assert (links["item-2"].active, links["item-2"].access_token_enc, links["item-2"].cursor) == (True, "tok-2", "")  # made since: kept, reads its bank again
    assert "Privacy" in " ".join(owner.get("/api/v1/restore-points").json()["kept_as_now"])


# ---- BUGIO-08: the REST mirror takes every parameter the published tools name ------------------------------------


def test_every_published_tool_parameter_lands_on_its_mirror(owner: TestClient):
    import re

    from app.main import app

    def walk(routes):  # noqa: ANN001, ANN202
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
            else:
                yield r

    routes = [r for r in walk(app.routes) if getattr(r, "methods", None)]
    for shape in ("openai", "anthropic"):
        spec = owner.get(f"/api/tools/{shape}.json").json()
        for tool in spec["tools"]:
            name = tool["function"]["name"] if shape == "openai" else tool["name"]
            params = (tool["function"]["parameters"] if shape == "openai" else tool["input_schema"]).get("properties") or {}
            mirror = spec["rest"].get(name, "")
            method, path = re.match(r"^(GET|POST) (\S+)", mirror).groups()  # type: ignore[union-attr]
            path = path.split("?")[0]
            placeholders = set(re.findall(r"\{([^}]+)\}", path))
            route = next(r for r in routes if method in r.methods and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", r.path), re.sub(r"\{[^}]+\}", "1", path)))
            takes = {q.alias for q in route.dependant.query_params} | placeholders
            for b in route.dependant.body_params:
                takes |= set(b.field_info.annotation.model_fields)
            assert set(params) <= takes, f"{name}: {sorted(set(params) - takes)} not taken by {mirror}"


def test_a_months_search_through_the_mirror_returns_that_month(owner: TestClient):
    from app.engine.household import seed

    with Session(engine) as s:
        seed(s)
    spec = owner.get("/api/tools/openai.json").json()
    props = next(t["function"]["parameters"]["properties"] for t in spec["tools"] if t["function"]["name"] == "transactions_search")
    assert {"start", "end"} <= set(props) and "start_date" not in props
    found = owner.get("/api/v1/transactions/search", params={"start": "2026-09-01", "end": "2026-09-30", "limit": 500}).json()
    assert found["transactions"] and all("2026-09-01" <= t["date"] <= "2026-09-30" for t in found["transactions"])


# ---- BUGIO-12: the exit export carries every register ----------------------------------------------------------------


def test_the_exit_export_carries_every_table_but_the_secret_ones(owner: TestClient):
    from sqlmodel import SQLModel

    from app.engine.household import seed
    from app.exports.model import NOT_EXPORTED, REGISTERS

    exported = {model.__tablename__ for _, model, _ in REGISTERS}
    assert set(SQLModel.metadata.tables) == exported | set(NOT_EXPORTED) and not exported & set(NOT_EXPORTED)
    with Session(engine) as s:
        seed(s)
    owner.post("/api/v1/housing", json={"name": "Synthetic Flat", "starts_on": "2026-01-01", "due_day": 1, "deposit": 500})
    assert owner.post("/api/v1/assets", json={"name": "Synthetic Car", "kind": "car", "value": 9000}).status_code == 200
    _upload(owner, "Synthetic lease text for the export test", "Synthetic Lease")
    tx = owner.get("/api/v1/transactions/search", params={"limit": 1}).json()["transactions"][0]
    owner.patch(f"/api/v1/transactions/{tx['id']}", json={"tags": ["tax"]})
    out = owner.get("/api/v1/exports/file", params={"format": "json"})
    assert out.status_code == 200, out.text
    doc = json.loads(out.content)
    regs = doc["registers"]
    assert {"homes", "assets", "documents", "splits", "incomes", "utilities", "holdings", "rules", "planned"} <= set(regs)
    assert any(h["name"] == "Synthetic Flat" for h in regs["homes"]) and any(a["name"] == "Synthetic Car" for a in regs["assets"])
    assert any(d["title"] == "Synthetic Lease" for d in regs["documents"]) and any(t["tags"] == "tax" for t in regs["transactions"])
    blob = json.dumps(regs)
    assert "pages_enc" not in blob and "access_token_enc" not in blob and "last4_enc" not in blob and "password" not in blob


# ---- BUGIO-17: a statement is kept once, and goes with its import ------------------------------------------------


def test_a_statement_is_kept_once_and_undoing_its_import_removes_it(owner: TestClient, monkeypatch):
    from app.parsers import paystub as paystub_parser

    text = "Example Credit Union\nStatement Period: 09/01/2026 - 09/30/2026\n09/05 EXAMPLE GROCER 123 54.12 3,385.88\n09/09 EXAMPLE UTILITY 80.00 3,305.88\n"
    monkeypatch.setattr(paystub_parser, "pdf_text", lambda data: text)
    for _ in range(2):
        r = owner.post("/api/v1/imports/file", files={"file": ("september.pdf", b"%PDF-1.4 synthetic statement", "application/pdf")}, data={"account_name": "Example Checking", "kind": "checking"})
        assert r.status_code == 200, r.text
    kept = [d for d in owner.get("/api/v1/documents").json()["documents"] if d["kind"] == "statement"]
    assert len(kept) == 1
    first = owner.get("/api/v1/imports/history").json()["imports"][-1]["id"]
    undone = owner.post(f"/api/v1/imports/{first}/undo").json()
    assert undone["documents_removed"] == 1
    assert [d for d in owner.get("/api/v1/documents").json()["documents"] if d["kind"] == "statement"] == []
