"""What the screens need to hide a control the server would refuse a second person: whose device, whose calendar feed."""

from fastapi.testclient import TestClient

from app.main import app

H = {"X-Requested-With": "FinancialAuthority"}
DEVICE = {"keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbUlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM", "auth": "tBHItJI5svbpez7KI4CCXg"}}


def _everything(owner: TestClient) -> TestClient:
    token = owner.post("/api/v1/people", json={"username": "robin", "display_name": "Robin Example", "role": "everything"}).json()["invite"]["link"].split("#", 1)[1]
    person = TestClient(app)
    person.headers.update(H)
    assert person.post("/api/auth/join", json={"token": token, "password": "plum river lantern seven"}).status_code == 200
    return person


def test_a_device_says_who_added_it_so_the_screen_offers_remove_only_on_ones_own(owner: TestClient):
    person = _everything(owner)
    mine = person.get("/api/auth/me").json()["person_id"]
    theirs = owner.post("/api/v1/push/devices", json={**DEVICE, "endpoint": "https://fcm.googleapis.com/fcm/send/owner", "label": "Owner phone"}).json()["device"]["id"]
    own = person.post("/api/v1/push/devices", json={**DEVICE, "endpoint": "https://fcm.googleapis.com/fcm/send/robin", "label": "Robin phone"}).json()["device"]["id"]
    who = {d["id"]: d["person_id"] for d in person.get("/api/v1/push/devices").json()["devices"]}
    assert who == {theirs: None, own: mine}
    assert "endpoint" not in str(person.get("/api/v1/push/devices").json()["devices"])
    assert person.delete(f"/api/v1/push/devices/{theirs}").status_code == 403
    assert person.delete(f"/api/v1/push/devices/{own}").status_code == 200


def test_the_catalog_says_whether_the_feed_on_file_is_ones_own_as_the_server_decides(owner: TestClient):
    person = _everything(owner)
    assert person.get("/api/v1/exports").json()["feed"]["mine"] is True  # no feed yet: anyone with the role may make one
    assert owner.post("/api/v1/calendar/feed", json={"amounts": False}).status_code == 200
    assert owner.get("/api/v1/exports").json()["feed"]["mine"] is True
    assert person.get("/api/v1/exports").json()["feed"]["mine"] is False
    assert person.post("/api/v1/calendar/feed", json={"amounts": False}).status_code == 403
    assert owner.delete("/api/v1/calendar/feed").status_code == 200
    assert person.post("/api/v1/calendar/feed", json={"amounts": False}).status_code == 200
    assert person.get("/api/v1/exports").json()["feed"]["mine"] is True
