"""The don't-buy list on the fixture: advances, the buffer dip, duplicate subscriptions, price rises; every
item with a source; the route and the tool."""

from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.engine.household import seed
from app.engine.watchlist import watchlist
from app.jobs.scheduler import detect_recurring
from app.models import RecurringSeries

TODAY = date(2026, 10, 14)


def test_watchlist_names_the_reasons_with_sources(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    detect_recurring()
    with Session(engine) as session:
        out = watchlist(session, TODAY)
        kinds = [i["kind"] for i in out["items"]]
        assert "advances" in kinds and "duplicates" in kinds
        assert all(i["sources"] and i["text"] and i["severity"] in ("critical", "warning", "info") for i in out["items"])
        severities = [i["severity"] for i in out["items"]]
        assert severities == sorted(severities, key={"critical": 0, "warning": 1, "info": 2}.get)
        dup = next(i for i in out["items"] if i["kind"] == "duplicates")
        assert "Music" in dup["text"] and len(dup["sources"]) >= 2
        # A price rise on a known series lands on the list.
        series = session.exec(select(RecurringSeries).where(RecurringSeries.active == True)).first()  # noqa: E712
        series.typical_amount, series.latest_amount, series.kind = 15.0, 17.0, "subscription"
        session.add(series)
        session.commit()
        again = watchlist(session, TODAY)
        rise = [i for i in again["items"] if i["kind"] == "price"]
        assert rise and rise[0]["amount"] == 2.0 and rise[0]["sources"] == [f"series:{series.id}"]
    listed = owner.get("/api/v1/watchlist").json()
    assert listed["items"] and listed["not_advice"]
