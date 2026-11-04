"""M61: a source that fails to sign in becomes one Needs you card with the exact step, one push when a channel exists
and a line in the morning brief; the job backs off (an hour, four, a day); a network failure is not a refusal; the
back-off survives a restart; a read that succeeds clears all of it; a person can always try at once."""

import imaplib
import json
import os
import random
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crypto, notify
from app.connectors import CONNECTORS, health
from app.connectors import mail as mail_connector
from app.connectors.mail import MailConnector, failure_of, signin_refused
from app.connectors.plaid import PlaidError
from app.connectors.plaid_webhook import handle
from app.connectors.teller import TellerError
from app.db import engine, session_scope
from app.engine import sources
from app.engine.household import seed
from app.engine.next_steps import next_steps
from app.engine.payments import match_payments
from app.engine.snapshot import _finance_snapshot
from app.jobs.scheduler import app_watch, jobs, self_check, source_watch
from app.models import BankLink, MailAccount
from app.store import get_setting, set_setting

from tests.conftest import TODAY

START = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
GOLDEN = Path(__file__).parent / "golden" / "sources.json"


class Box:
    """A mailbox server: answers as `how` says, and counts the sign-ins asked of it."""

    how: Exception | None = None
    tries = 0

    def __init__(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        pass

    def connect(self) -> None:
        Box.tries += 1
        if Box.how is not None:
            raise Box.how

    def select_readonly(self, folder: str) -> tuple[int, int]:
        return (0, 1)

    def new_uids(self, since_uid: int) -> list[int]:
        return []

    def close(self) -> None:
        pass


NO_ACCOUNT = imaplib.IMAP4.error(b"no such user")
REFUSED = imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] the server at imap.secrethost.test says no")


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = START
        monkeypatch.setattr(health, "now_utc", lambda: self.now)

    def on(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture
def box(monkeypatch):
    Box.how, Box.tries = None, 0
    monkeypatch.setattr(CONNECTORS["mail"], "client_factory", Box)
    return Box


@pytest.fixture
def pushes(monkeypatch):
    sent: list[dict] = []

    def send(session, title, body, **kwargs):  # noqa: ANN001, ANN003
        sent.append({"title": title, "body": body, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(notify, "send", send)
    return sent


def add_mailbox(label="Example Mail", host="imap.example.test", kind="imap") -> int:
    with Session(engine) as session:
        row = MailAccount(label=label, kind=kind, host=host, port=993, username="owner@example.test", password_enc=crypto.encrypt("app-pass"))
        session.add(row)
        session.commit()
        return row.id


def add_link(provider="plaid", institution="Example Bank") -> int:
    with Session(engine) as session:
        row = BankLink(provider=provider, external_id=f"{provider}-item", institution=institution, access_token_enc=crypto.encrypt("token"))
        session.add(row)
        session.commit()
        set_setting(session, "banks.freshness", {"sync_every_hours": 1})
        return row.id


def cards() -> list[dict]:
    with Session(engine) as session:
        return sources.down(session, health.now_utc())


def waiting(key: str):  # noqa: ANN201
    with Session(engine) as session:
        return health.waiting_until(session, key, health.now_utc())


# --- the roadmap's own test ---------------------------------------------------------------------------------------


def test_a_refused_mailbox_makes_one_card_one_push_and_three_tries_in_a_day_not_ninety_six(clock, box, pushes, owner: TestClient):
    box_id = add_mailbox()
    box.how = REFUSED
    failed = []
    for _ in range(96):
        failed.append(not jobs.run("mail_sync").ok)
        jobs.run("source_watch")
        clock.on(minutes=15)
    assert box.tries == 3  # at noon, an hour on, four hours after that; the next is a day after that
    assert sum(failed) == 3  # and those are the only failed runs; a run that only waited is not one
    assert len(cards()) == 1 and cards()[0]["key"] == f"mail:{box_id}" and cards()[0]["severity"] == "critical"
    assert [p["title"] for p in pushes] == ["Needs you: Example Mail"]

    with Session(engine) as session:
        steps = [s for s in next_steps(session, TODAY)["steps"] if s["source"] == f"mailbox:{box_id}"]
    assert len(steps) == 1 and "change the password" in steps[0]["text"]
    tri = owner.get("/api/v1/needs-you").json()
    assert len(tri["sources"]) == 1 and tri["total"] == 1 and "1 source to fix" in tri["word"]
    snap = owner.get("/api/v1/snapshot").json()
    assert snap["pending"]["sources"] == 1 and snap["pending"]["confirmations"] == 1  # the badge and the page agree
    lines = [b for b in snap["brief"] if "Example Mail" in b["text"]]
    assert len(lines) == 1 and "change the password" in lines[0]["text"] and lines[0]["severity"] == "critical"


def test_the_self_check_and_the_health_watch_do_not_tell_it_a_second_time(clock, box, pushes):
    box_id = add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    clock.on(hours=1, minutes=1)
    jobs.run("mail_sync")  # two failed runs in a row, which alone would make the jobs part "failing"
    self_check()
    source_watch()
    app_watch()
    clock.on(hours=25)
    self_check()
    app_watch()
    assert [p["title"] for p in pushes] == ["Needs you: Example Mail"]
    with Session(engine) as session:
        report = get_setting(session, "selfcheck", {})
        assert any("Example Mail" in p for p in report["problems"])  # the report still says so
        steps = next_steps(session, TODAY)["steps"]
    assert [s["id"] for s in steps if "Example Mail" in s["text"]] == [f"mail-error-{box_id}"]


def test_what_goes_to_the_phone_and_the_brief_is_never_what_the_server_said(clock, box, pushes):
    add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    source_watch()
    with Session(engine) as session:
        snap = _finance_snapshot(session, TODAY)
    assert "secrethost" not in json.dumps(pushes) and "secrethost" not in json.dumps(snap["source_health"]) and "secrethost" not in json.dumps(snap["brief"])


# --- backing off ----------------------------------------------------------------------------------------------------


def test_the_back_off_survives_a_restart(clock, box):
    box_id = add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    assert box.tries == 1
    engine.dispose()  # the process is gone: nothing but the database carries over
    fresh = MailConnector(client_factory=Box)
    clock.on(minutes=30)
    with Session(engine) as session:
        result = fresh.sync(session, today=TODAY)
    assert result.ok and box.tries == 1 and result.details["only_waited"]
    assert waiting(health.mail_key(box_id)) == START + timedelta(hours=1)


def test_a_waiting_run_is_quiet_on_the_jobs_page(clock, box):
    add_mailbox()
    box.how = REFUSED
    assert not jobs.run("mail_sync").ok
    clock.on(minutes=15)
    run = jobs.run("mail_sync")
    assert run.ok and run.summary.startswith("not due")  # pruned like every other run that did nothing


def test_a_bank_link_backs_off_as_a_mailbox_does(clock, monkeypatch):
    tries = {"n": 0}

    class Api:
        def bank_updated(self, token):  # noqa: ANN001, ANN201
            return None

        def accounts(self, token):  # noqa: ANN001, ANN201
            tries["n"] += 1
            raise PlaidError("ITEM_LOGIN_REQUIRED", "the login details of this item have changed")

    monkeypatch.setattr(CONNECTORS["plaid"], "api", lambda session: Api())
    link_id = add_link()
    for _ in range(24):
        jobs.run("bank_sync")
        clock.on(hours=1)
    assert tries["n"] == 3
    assert [c["card_id"] for c in cards()] == [f"link-signin-{link_id}"]


# --- a network failure is not a refused sign-in ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "want"),
    [
        (ConnectionRefusedError(61, "Connection refused"), health.NETWORK),
        (socket.gaierror(8, "nodename nor servname provided"), health.NETWORK),
        (TimeoutError("timed out"), health.NETWORK),
        (EOFError("socket closed"), health.NETWORK),
        (imaplib.IMAP4.abort("LOGIN => socket error: EOF"), health.NETWORK),
        (imaplib.IMAP4.error(b"[UNAVAILABLE] Temporary authentication failure"), health.NETWORK),
        (mail_connector.ServerStopped("the mail server stopped answering in Login Alerts (TimeoutError)"), health.NETWORK),
        (NO_ACCOUNT, health.SIGNIN),
        (REFUSED, health.SIGNIN),
        (imaplib.IMAP4.error(b"too many login attempts"), health.SIGNIN),
        (RuntimeError("cannot open Archive"), health.OTHER),
    ],
)
def test_each_failure_is_told_apart(error, want):
    assert failure_of(error) == want


def test_a_folder_that_will_not_open_is_not_a_refused_sign_in_whatever_its_name(clock, box, monkeypatch):
    add_mailbox()

    def no_folder(self, folder):  # noqa: ANN001, ANN202
        raise imaplib.IMAP4.error(b"[NONEXISTENT] Invalid mailbox name")

    monkeypatch.setattr(Box, "select_readonly", no_folder)
    jobs.run("mail_sync")
    clock.on(minutes=15)
    jobs.run("mail_sync")
    assert box.tries == 2  # no wait: the sign-in went through
    assert [c["failure"] for c in cards()] == [health.OTHER]  # and the owner sees it now: it will not mend itself


def test_a_network_failure_keeps_trying_says_nothing_for_an_hour_and_then_once(clock, box, pushes):
    box_id = add_mailbox()
    box.how = ConnectionRefusedError(61, "Connection refused")
    for _ in range(3):
        jobs.run("mail_sync")
        source_watch()
        clock.on(minutes=15)
    assert box.tries == 3 and waiting(health.mail_key(box_id)) is None  # no wait: nothing is locked out by asking
    assert cards() == [] and pushes == []
    with Session(engine) as session:
        assert all("Example Mail" not in s["text"] for s in next_steps(session, TODAY)["steps"])
        assert sources.snapshot_part(session, clock.now)["down"] == []
    for _ in range(12):
        jobs.run("mail_sync")
        source_watch()
        clock.on(minutes=15)
    assert box.tries == 15
    assert [c["failure"] for c in cards()] == [health.NETWORK] and cards()[0]["severity"] == "warning"
    assert [p["title"] for p in pushes] == ["Needs you: Example Mail"]


def test_a_blip_that_mends_within_the_hour_leaves_no_trace(clock, box, pushes):
    box_id = add_mailbox()
    box.how = TimeoutError("timed out")
    jobs.run("mail_sync")
    source_watch()
    clock.on(minutes=15)
    box.how = None
    jobs.run("mail_sync")
    source_watch()
    with Session(engine) as session:
        assert get_setting(session, health.mail_key(box_id)) is None
        assert sources.snapshot_part(session, clock.now) == {"down": [], "back": []}
    assert pushes == []


def test_a_bank_link_tells_the_network_from_the_sign_in(clock, monkeypatch):
    class Api:
        def bank_updated(self, token):  # noqa: ANN001, ANN201
            return None

        def accounts(self, token):  # noqa: ANN001, ANN201
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(CONNECTORS["plaid"], "api", lambda session: Api())
    link_id = add_link()
    jobs.run("bank_sync")
    assert waiting(health.bank_key(link_id)) is None and cards() == []
    with Session(engine) as session:
        assert session.get(BankLink, link_id).needs_login is False
    clock.on(hours=1, minutes=1)
    assert [c["card_id"] for c in cards()] == [f"link-error-{link_id}"]


def test_teller_tells_them_apart_too(clock, monkeypatch):
    from app.connectors.teller import failure_of as teller_failure

    assert teller_failure(TellerError("enrollment.disconnected", "sign in again")) == health.SIGNIN
    assert teller_failure(TellerError("503", "down")) == health.NETWORK
    assert teller_failure(httpx.ConnectError("no route")) == health.NETWORK

    class Api:
        def accounts(self, token):  # noqa: ANN001, ANN201
            raise TellerError("enrollment.disconnected", "sign in again")

    monkeypatch.setattr(CONNECTORS["teller"], "api", lambda session: Api())
    link_id = add_link(provider="teller", institution="Example Credit Union")
    assert not jobs.run("bank_sync").ok
    assert waiting(health.bank_key(link_id)) == START + timedelta(hours=1)
    assert [c["card_id"] for c in cards()] == [f"link-signin-{link_id}"]


# --- recovery and healing -------------------------------------------------------------------------------------------


def test_recovery_clears_the_card_says_so_once_and_starts_the_wait_over(clock, box, pushes):
    box_id = add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    source_watch()
    clock.on(hours=5)
    box.how = None
    assert jobs.run("mail_sync").ok
    assert cards() == []  # at once, not when a job next looks
    source_watch()
    source_watch()
    assert [p["title"] for p in pushes] == ["Needs you: Example Mail", "Back: Example Mail"]
    with Session(engine) as session:
        assert get_setting(session, health.mail_key(box_id)) is None
        came = sources.snapshot_part(session, clock.now)["back"]
        assert [c["text"] for c in came] == ["Example Mail is being read again after 5 hours."]
        brief = _finance_snapshot(session, TODAY)["brief"]
    assert [b["severity"] for b in brief if "Example Mail" in b["text"]] == ["info"]
    clock.on(hours=25)  # one morning's brief has said it
    with Session(engine) as session:
        assert sources.snapshot_part(session, clock.now)["back"] == []
    box.how = REFUSED
    jobs.run("mail_sync")
    assert waiting(health.mail_key(box_id)) == clock.now + timedelta(hours=1)  # an hour again, not a day


def test_a_source_that_flaps_never_floods_the_phone(clock, box, pushes):
    box_id = add_mailbox()
    for i in range(24):
        box.how = REFUSED if i % 2 == 0 else None
        with Session(engine) as session:
            CONNECTORS["mail"].sync(session, today=TODAY, account_id=box_id)  # Sync now, so no wait hides the flap
        source_watch()
        assert len(cards()) <= 1
        clock.on(hours=1)
    assert len(pushes) == 2  # down and back, the first time; the rest stay on the card and in the brief


def test_the_invariant_holds_for_any_order_of_events():
    """However sources fail and return, the phone hears a source is down at most once a day, and that it is back only
    after hearing it was down."""
    rng = random.Random(61)
    for _ in range(25):
        now, state, told = START, {}, []
        for _tick in range(rng.randint(10, 200)):
            now += timedelta(minutes=rng.choice([5, 15, 60, 240]))
            keys = {k for k in ("mail:1", "mail:2", "bank:1") if rng.random() < 0.5}
            batch = [{"key": k, "label": k, "since": START.isoformat(), "text": "t", "step": "s"} for k in sorted(keys)]
            messages, state, _ended = sources.plan_telling(state, batch, {"mail:1", "mail:2", "bank:1"}, now)
            assert len({c["key"] for c in batch}) == len(batch)
            for m in messages:
                told.append((m["key"], m["kind"], now))
                if m["kind"] == "down":
                    state[m["key"]] = {**state[m["key"]], "told": now.isoformat(), "told_this": True}
        for key, kind, at in told:
            if kind == "down":
                assert sum(1 for k, w, t in told if k == key and w == "down" and at <= t < at + timedelta(hours=24)) == 1
        for key in {k for k, _w, _t in told}:
            words = [w for k, w, _t in told if k == key]
            assert all(a != b for a, b in zip(words, words[1:], strict=False)) and words[0] == "down"  # each back follows its down


def test_with_no_channel_nothing_counts_as_told_until_there_is_one(clock, box, monkeypatch):
    add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": False, "skipped": True})
    source_watch()
    with Session(engine) as session:
        assert get_setting(session, sources.OPEN, {})["mail:1"]["told"] is None
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, *a, **k: sent.append(title) or {"ok": True})
    source_watch()
    source_watch()
    assert sent == ["Needs you: Example Mail"]


def test_a_channel_that_refuses_is_a_failed_run_and_is_tried_again(clock, box, monkeypatch):
    add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"ok": False, "detail": "401"})
    run = jobs.run("source_watch")
    assert not run.ok and "401" in run.summary
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda session, title, *a, **k: sent.append(title) or {"ok": True})
    assert jobs.run("source_watch").ok and sent == ["Needs you: Example Mail"]


def test_a_new_password_does_not_wait_out_the_old_ones_back_off(clock, box, owner: TestClient):
    box_id = add_mailbox()
    box.how = REFUSED
    for _ in range(3):
        owner.post(f"/api/v1/mail/accounts/{box_id}/sync")
    assert waiting(health.mail_key(box_id)) == START + timedelta(hours=24)
    assert owner.put(f"/api/v1/mail/accounts/{box_id}", json={"password": "new-app-pass"}).status_code == 200
    assert waiting(health.mail_key(box_id)) is None
    box.how = None
    clock.on(minutes=15)
    tried = box.tries
    assert jobs.run("mail_sync").ok and box.tries == tried + 1 and cards() == []


def test_plaid_saying_the_login_is_repaired_reads_it_at_once(clock, monkeypatch):
    link_id = add_link()
    started: list[int] = []

    def factory():  # noqa: ANN202
        return session_scope()

    said = handle({"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "plaid-item"}, started.append, factory)
    assert said == "needs a new sign-in" and [c["card_id"] for c in cards()] == [f"link-signin-{link_id}"]
    assert waiting(health.bank_key(link_id)) is None  # a notice is no refused try
    with Session(engine) as session:
        health.note_failure(session, health.bank_key(link_id), health.SIGNIN, clock.now)
    assert waiting(health.bank_key(link_id)) is not None
    said = handle({"webhook_type": "ITEM", "webhook_code": "LOGIN_REPAIRED", "item_id": "plaid-item"}, started.append, factory)
    assert said == "sign-in repaired; sync started" and started == [link_id]
    assert waiting(health.bank_key(link_id)) is None


# --- trying at once -------------------------------------------------------------------------------------------------


def test_a_person_can_try_a_mailbox_at_once_during_the_wait(clock, box, owner: TestClient):
    box_id = add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    assert waiting(health.mail_key(box_id)) is not None and box.tries == 1
    card = owner.get("/api/v1/needs-you").json()["sources"][0]
    assert card["retry"] == f"/api/v1/mail/accounts/{box_id}/sync" and card["retry_at"] == (START + timedelta(hours=1)).isoformat()
    box.how = None
    answer = owner.post(card["retry"]).json()
    assert answer["ok"] and box.tries == 2
    assert owner.get("/api/v1/needs-you").json()["sources"] == []


def test_a_person_can_try_a_bank_link_at_once_during_the_wait(clock, monkeypatch, owner: TestClient):
    state = {"refuse": True, "tries": 0}

    class Api:
        def bank_updated(self, token):  # noqa: ANN001, ANN201
            return None

        def accounts(self, token):  # noqa: ANN001, ANN201
            state["tries"] += 1
            if state["refuse"]:
                raise PlaidError("ITEM_LOGIN_REQUIRED", "sign in again")
            return {"accounts": []}

        def sync_page(self, token, cursor):  # noqa: ANN001, ANN201
            return {"next_cursor": "c", "has_more": False}

    monkeypatch.setattr(CONNECTORS["plaid"], "api", lambda session: Api())
    link_id = add_link()
    jobs.run("bank_sync")
    assert waiting(health.bank_key(link_id)) is not None
    card = owner.get("/api/v1/needs-you").json()["sources"][0]
    state["refuse"] = False
    answer = owner.post(card["retry"]).json()
    assert answer["ok"] and state["tries"] == 2 and answer["link"]["needs_login"] is False
    assert waiting(health.bank_key(link_id)) is None and cards() == []


def test_a_read_key_gets_the_step_and_not_what_the_server_said(clock, box, owner: TestClient, client: TestClient):
    add_mailbox()
    box.how = RuntimeError("the proxy at imap.secrethost.test would not pass it")
    jobs.run("mail_sync")
    assert "secrethost" in json.dumps(owner.get("/api/v1/needs-you").json()["sources"])  # the owner's own screen shows it
    token = owner.post("/api/v1/keys", json={"name": "a model", "scopes": ["read"], "redaction": "full"}).json()["token"]
    owner.post("/api/v1/auth/logout")
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    tri = client.get("/api/v1/needs-you", headers=bearer).json()
    assert tri["sources"][0]["step"] and "secrethost" not in json.dumps(tri["sources"])
    assert "secrethost" not in json.dumps(client.get("/api/v1/snapshot", headers=bearer).json()["source_health"])


# --- the Bridge's three states, from the IMAP answer alone ----------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "state", "card"),
    [
        (socket.gaierror(8, "nodename nor servname provided"), "not_running", "bridge-signin"),
        (NO_ACCOUNT, "no_account", "bridge-signin"),
        (imaplib.IMAP4.error(b"Invalid username or password"), "password_refused", "mail-error"),
        (None, "signed_in", None),
    ],
)
def test_the_self_check_knows_the_bridges_state(clock, box, pushes, answer, state, card):
    box_id = add_mailbox(label="Proton", host="bridge", kind="proton")
    box.how = answer
    jobs.run("mail_sync")
    clock.on(hours=1, minutes=1)  # past the wait of a refusal, and past the hour a network failure is given
    jobs.run("mail_sync")
    self_check()
    with Session(engine) as session:
        report = get_setting(session, "selfcheck", {})
    assert report["sources"][0]["bridge"] == state
    assert [c["card_id"] for c in cards()] == ([f"{card}-{box_id}"] if card else [])


# --- the golden on the synthetic household --------------------------------------------------------------------------


def _day_on_the_household(clock: Clock, box: type[Box], pushes: list[dict], monkeypatch: pytest.MonkeyPatch) -> dict:
    with Session(engine) as session:
        seed(session)
        match_payments(session, TODAY)
        before = _finance_snapshot(session, TODAY)

    class Api:
        def bank_updated(self, token):  # noqa: ANN001, ANN201
            return None

        def accounts(self, token):  # noqa: ANN001, ANN201
            raise PlaidError("ITEM_LOGIN_REQUIRED", "the login details of this item have changed")

    monkeypatch.setattr(CONNECTORS["plaid"], "api", lambda session: Api())
    add_mailbox(label="Example Mail")
    add_link()
    box.how = REFUSED
    for _ in range(24 * 4):
        jobs.run("mail_sync")
        jobs.run("bank_sync")
        jobs.run("source_watch")
        clock.on(minutes=15)

    def seen() -> dict:
        with Session(engine) as session:
            snap = _finance_snapshot(session, TODAY)
        health_part = snap["source_health"]
        return {
            "cards": [{k: c[k] for k in ("key", "severity", "failure", "text", "step", "action", "link", "since", "retry_at")} for c in health_part["down"]],
            "back": [b["text"] for b in health_part["back"]],
            "brief": [b for b in snap["brief"] if "Example Mail" in b["text"] or "Example Bank wants" in b["text"]],
            "pending_sources": snap["pending"]["sources"],
            "safe_to_spend": snap["safe_to_spend"]["amount"],
        }

    down = seen()
    box.how = None
    with Session(engine) as session:
        CONNECTORS["mail"].sync(session, today=TODAY, account_id=1)
    jobs.run("source_watch")
    after = seen()
    return {
        "down": down, "after_the_mailbox_is_back": after, "tries": {"mail": box.tries}, "pushes": [p["title"] for p in pushes],
        "safe_to_spend_before": before["safe_to_spend"]["amount"],
    }


def test_the_golden_day_on_the_synthetic_household(clock, box, pushes, monkeypatch, owner):
    got = _day_on_the_household(clock, box, pushes, monkeypatch)
    # A source being down moves no figure: it only says so.
    assert got["down"]["safe_to_spend"] == got["safe_to_spend_before"] == got["after_the_mailbox_is_back"]["safe_to_spend"]
    if os.environ.get("FA_GOLDEN_WRITE") == "1":
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8")), "If that is meant, write the golden file again (see tests/test_golden.py) and say why in the commit."


def test_a_row_from_before_m61_with_only_its_error_still_makes_its_one_card(clock):
    with Session(engine) as session:
        session.add(MailAccount(label="Example Mail", kind="imap", host="imap.example.test", port=993, username="owner@example.test", last_error="The mail server refused the sign-in. Trying again at 10:00."))
        session.add(BankLink(provider="plaid", external_id="x", institution="Example Bank", needs_login=True))
        session.commit()
    assert sorted(c["key"][:4] for c in cards()) == ["bank", "mail"]


def test_a_removed_source_is_forgotten_without_a_message(clock, box, pushes, owner: TestClient):
    box_id = add_mailbox()
    box.how = REFUSED
    jobs.run("mail_sync")
    source_watch()
    owner.delete(f"/api/v1/mail/accounts/{box_id}")
    source_watch()
    assert [p["title"] for p in pushes] == ["Needs you: Example Mail"] and cards() == []


def test_signin_refused_keeps_its_old_answers():
    assert not signin_refused(ConnectionRefusedError(111, "Connection refused"))
    assert signin_refused(RuntimeError("LOGIN failed"))
