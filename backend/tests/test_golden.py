"""M47: known answers for the synthetic household that must never drift by accident. The figures every screen leads
with (safe to spend, each paycheck window, each paycheck picture's parts, the credit record, the way off advances,
the tax-time totals) are kept in tests/golden/household.json. A change that moves one fails here; when the move is
meant, write the file again and commit it with the change that explains it:

    FA_GOLDEN_WRITE=1 uv run pytest tests/test_golden.py
"""

import json
import os
from pathlib import Path

from sqlmodel import Session

from app.db import engine
from app.engine.advance_exit import exit_plan
from app.engine.credit import credit_health
from app.engine.custom_reports import tax_totals
from app.engine.household import seed
from app.engine.payments import match_payments
from app.engine.paychecks import paycheck_views
from app.engine.snapshot import finance_snapshot

from tests.conftest import TODAY

GOLDEN = Path(__file__).parent / "golden" / "household.json"


def _figures() -> dict:
    with Session(engine) as session:
        seed(session)
        match_payments(session, TODAY)
        snap = finance_snapshot(session, TODAY)
        views = paycheck_views(session, TODAY)
        credit = credit_health(session, TODAY)
        way_off = exit_plan(session, TODAY)
        tax = tax_totals(session, TODAY, 2026)
    return {
        "safe_to_spend": snap["safe_to_spend"]["amount"],
        "windows": [{"start": w["start"], "paycheck": w["paycheck"], "bills": w["bills"]["total"], "advances": w["advances"]["total"], "set_aside": w["set_aside"]["total"], "safe": w["safe"]}
                    for w in snap["windows"]],
        "paycheck_parts": [{"start": w["start"], "parts": {p["key"]: p["amount"] for p in w["parts"]}} for w in views["windows"]],
        "credit": {"share": credit["overall"]["share"], "on_time": credit["payments"]["on_time"], "late": credit["payments"]["late"], "not_seen": credit["payments"]["not_seen"]},
        "way_off_advances": {"state": way_off["state"], "borrow": [s["borrow"] for s in way_off["steps"]], "fees_on_plan": way_off.get("fees_on_plan")},
        "tax": {c["name"]: c["total"] for c in tax["categories"]},
    }


def test_the_household_figures_have_not_drifted(owner):
    got = _figures()
    if os.environ.get("FA_GOLDEN_WRITE") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for key in want:
        assert got[key] == want[key], f"{key} moved. If that is meant, write the golden file again (see this file's notes) and say why in the commit."
    assert set(got) == set(want)
