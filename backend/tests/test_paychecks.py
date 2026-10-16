"""The paycheck views every picture draws, and the appearance the owner arranges. Each check's parts add up to
what it brings in, in a fixed order; past checks come from the ledger; the months from the registers; a moved
due day is judged against the low point. Appearance is validated on the server, a mark included."""

import base64
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed
from app.engine.paychecks import PARTS, paycheck_views

TODAY = date(2026, 10, 14)
ORDER = [k for k, _ in PARTS]


def test_every_check_adds_up_in_the_fixed_order():
    with Session(engine) as session:
        seed(session)
        v = paycheck_views(session, TODAY)
    assert [p["key"] for p in v["parts"]] == ORDER
    assert len(v["windows"]) == 4
    for w in v["windows"]:
        assert [p["key"] for p in w["parts"]] == ORDER
        total = round(sum(p["amount"] for p in w["parts"]), 2)
        assert total == round(w["inflow"] + w["short"], 2) if w["short"] else total == w["inflow"]
        assert all(p["amount"] >= 0 for p in w["parts"])
    # the rent sits in the end-of-month check, as the household pays it
    assert next(p for p in v["windows"][1]["parts"] if p["key"] == "housing")["amount"] > 0
    assert len(v["history"]) == 6 and all(h["actual"] for h in v["history"])
    for h in v["history"]:
        assert [p["key"] for p in h["parts"]] == ORDER
        spent = sum(p["amount"] for p in h["parts"] if p["key"] != "yours")
        assert round(spent + next(p for p in h["parts"] if p["key"] == "yours")["amount"], 2) == round(max(h["inflow"], spent), 2)
    assert len(v["months"]) == 6 and v["months"][0]["from"] == TODAY.isoformat()
    # a carried card owes its minimum every month, so the card band never vanishes from the stream
    assert all(next(p for p in m["parts"] if p["key"] == "card")["amount"] > 0 for m in v["months"][:3])
    for b in v["beads"]:
        dates = [i["date"] for i in b["items"]]
        assert dates == sorted(dates) and all(b["start"] <= d <= b["end"] for d in dates)


def test_a_moved_due_day_is_judged_against_the_low_point(owner: TestClient):
    with Session(engine) as session:
        seed(session)
    v = owner.get("/api/v1/paychecks").json()
    movable = next(i for b in v["beads"] for i in b["items"] if i["movable"] and i["bill_id"])
    out = owner.get(f"/api/v1/bills/{movable['bill_id']}/move_check", params={"day": 3}).json()
    assert out["to_day"] == 3 and out["verdict"] in ("better", "worse", "same")
    assert out["change"] == round(out["low_after"] - out["low_before"], 2) and "Hello" in out["draft"]
    assert owner.get("/api/v1/bills/99999/move_check", params={"day": 3}).status_code == 404
    assert owner.get(f"/api/v1/bills/{movable['bill_id']}/move_check", params={"day": 31}).status_code == 400


def _png() -> str:
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def test_appearance_is_kept_on_the_server_and_checked(owner: TestClient):
    first = owner.get("/api/v1/appearance").json()
    assert first["saved"] is False and first["look"] == "light"
    body = {
        "look": "dark", "active": "mine", "type_size": "larger", "density": "compact", "quiet": True,
        "arrangements": [{"id": "mine", "name": "My home", "sections": [{"key": "one_thing"}, {"key": "paycheck", "show_as": "ribbon"}, {"key": "cover", "show_as": "sheet", "hidden": True}]}],
        "show_as": {"paycheck": "ribbon"}, "colours": {"light": {"housing": "#2A78D6"}}, "brand": {"name": "Our house", "mark": _png()},
    }
    saved = owner.put("/api/v1/appearance", json=body).json()
    assert saved["saved"] and saved["colours"]["light"]["housing"] == "#2a78d6" and saved["brand"]["name"] == "Our house"
    again = owner.get("/api/v1/appearance").json()
    assert again["arrangements"][0]["sections"][2]["hidden"] is True and again["quiet"] is True
    assert next(s for s in owner.get("/api/v1/firstrun").json()["steps"] if s["id"] == "picture")["done"]

    def refused(change: dict) -> int:
        return owner.put("/api/v1/appearance", json={**body, **change}).status_code

    assert refused({"look": "neon"}) == 400
    assert refused({"arrangements": [{"id": "x", "name": "X", "sections": [{"key": "evil"}]}]}) == 400
    assert refused({"colours": {"light": {"housing": "red"}}}) == 400
    assert refused({"colours": {"light": {"status": "#ffffff"}}}) == 400
    script = "data:image/svg+xml;base64," + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>").decode()
    assert refused({"brand": {"name": "x", "mark": script}}) == 400
    handler = "data:image/svg+xml;base64," + base64.b64encode(b"<svg onload='alert(1)'></svg>").decode()
    assert refused({"brand": {"name": "x", "mark": handler}}) == 400
    fake_png = "data:image/png;base64," + base64.b64encode(b"GIF89a....").decode()
    assert refused({"brand": {"name": "x", "mark": fake_png}}) == 400
    big = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"\x00" * 400_000).decode()
    assert refused({"brand": {"name": "x", "mark": big}}) == 400
    assert refused({"brand": {"name": "<b>x</b>", "mark": ""}}) == 400
    plain_svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='4' fill='#000'/></svg>").decode()
    assert refused({"brand": {"name": "x", "mark": plain_svg}}) == 200


def test_on_a_payday_each_stretch_of_beads_starts_once():
    payday = date(2026, 10, 16)
    with Session(engine) as session:
        seed(session)
        v = paycheck_views(session, payday)
    starts = [b["start"] for b in v["beads"]]
    assert starts[0] == payday.isoformat() and len(starts) == len(set(starts))
    assert all(b["start"] <= b["end"] for b in v["beads"])
