"""M23: the plan kept before payday, set beside what happened."""

from datetime import date

from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.recap import recap, record_plan, week_recap


def test_the_plan_is_kept_before_payday_and_compared_after():
    with Session(engine) as session:
        seed(session)
        kept = record_plan(session, date(2026, 10, 14))  # payday is Oct 16
        assert kept and kept["start"] == "2026-10-16" and "free" in kept
        later = recap(session, date(2026, 10, 31))
        first = later["windows"][0]
        assert first["plan"]["start"] == "2026-10-16" and first["finished"] is True
        assert first["status"] in ("good", "warn", "crit") and ("under" in first["word"] or "over" in first["word"])
        week = week_recap(session, date(2026, 10, 14))
        assert week["text"].startswith("This week:") and week["usual_week"] >= 0
