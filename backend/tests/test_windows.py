from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.snapshot import finance_snapshot

TODAY = date(2026, 10, 14)


def test_windows_and_calendar_agree_with_the_headline():
    with Session(engine) as session:
        seed(session)
        snap = finance_snapshot(session, TODAY)
    windows = snap["windows"]
    assert len(windows) == 4
    first = windows[0]
    assert first["start"] == "2026-10-16" and first["end"] == "2026-10-29"
    assert first["paycheck"] == 2240.0 and first["advances"]["total"] == 231.0
    assert first["bills"]["total"] == snap["window"]["total"]
    assert first["set_aside"]["total"] == snap["set_aside"]["total"]
    assert first["safe"] == round(first["carried_in"] + first["paycheck"] - first["advances"]["total"] - first["bills"]["total"] - first["set_aside"]["total"], 2)
    # Safe to spend runs until payday and never spends the paycheck early: checking, less what is due before
    # payday, the advances, the buffer and any shortfall the paycheck leaves in its own window.
    parts = snap["safe_to_spend"]["parts"]
    assert snap["safe_to_spend"]["through"] == "2026-10-15" and snap["safe_to_spend"]["version"] == "v1"
    assert snap["safe_to_spend"]["amount"] == round(parts["cash"] - parts["due_before_payday"] - parts["advances"] - parts["buffer"] - parts["next_window_shortfall"], 2)
    assert parts["next_window_shortfall"] == round(max(0.0, snap["window"]["total"] + snap["set_aside"]["total"] + parts["advances_on_payday"] - snap["next_paycheck"]["amount"]), 2)
    second = windows[1]
    assert second["start"] == "2026-10-30" and second["carried_in"] == first["set_aside"]["total"]
    assert any(e["name"] == "Rent" for e in second["bills"]["items"])
    cal = snap["calendar"]
    paydays = [e for e in cal["events"] if e["kind"] == "paycheck"]
    assert [p["date"] for p in paydays][:2] == ["2026-10-16", "2026-10-30"]
    assert all(e["source"] for e in cal["events"])
    assert cal["weeks"][0]["start"] == "2026-10-11" and len(cal["weeks"]) >= 9
    assert all(w["low"] is not None for w in cal["weeks"][:8])
