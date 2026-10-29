"""Round twelve: cross-origin attacks from a real browser.

Chrome was driven against the app at one https host behind a TLS proxy (forwarded headers, as the tunnel sends),
with an attacker on another site and on a sibling subdomain of the same registrable domain (where the owner's demo
lives). SameSite=Lax holds against the other site; against the sibling it holds nothing, since the sibling is the
same site. What failed:

- A form posted at /api/auth/logout from either page signed the owner out: the route asked for no header, and the
  browser takes a cookie deletion from a top-level navigation's answer even when it sent no cookie.
- The sibling set `fa_session` for the parent domain from script; Chrome sent both cookies, the app read the later
  one, so the owner was signed out (a planted value of a real session would have signed them into someone else's).
  Over https the cookie is now `__Host-fa_session`, which a sibling cannot set, and the plain name is not read there.
- The sibling's <script>, <img> and <link> at JSON routes went out with the owner's cookie and were answered in full
  (the browser then refused to run or show them, but the work, the timing and the bytes reached the page's process).
  A request from another site that is neither CORS nor a top-level page load is now refused before any route runs,
  and every /api answer says Cross-Origin-Resource-Policy: same-origin.

The browser harness (Chrome, a TLS proxy for three hosts, the probes) is kept outside the repository; these are the
in-process forms of what it found.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

PW = "correct horse battery"
HDR = {"X-Requested-With": "FinancialAuthority"}


def _https_owner() -> tuple[TestClient, str]:
    """Signed in over https, as the tunnel's address is: the cookie set and its raw value."""
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/api/auth/login", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text
    return c, r.headers["set-cookie"]


# ---- logout CSRF -------------------------------------------------------------------------------------------------


def test_a_form_posted_from_another_page_does_not_sign_the_owner_out(owner: TestClient):
    bare = {k: v for k, v in owner.headers.items() if k.lower() != "x-requested-with"}
    owner.headers.clear()
    owner.headers.update(bare)
    r = owner.post("/api/auth/logout", data={"x": "1"})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers  # nothing deleted in the browser either
    assert owner.get("/api/auth/me").status_code == 200
    r = owner.post("/api/auth/logout", headers=HDR)
    assert r.status_code == 200
    assert owner.get("/api/auth/me").status_code == 401


# ---- cookie tossing ----------------------------------------------------------------------------------------------


def test_the_session_cookie_is_host_prefixed_over_https():
    _, raw = _https_owner()
    assert raw.startswith("__Host-fa_session="), raw
    low = raw.lower()
    assert "secure" in low and "path=/" in low and "domain=" not in low and "httponly" in low


def test_the_session_cookie_keeps_its_plain_name_over_plain_http(client: TestClient):
    r = client.post("/api/auth/login", json={"username": "owner", "password": PW})
    assert r.status_code == 200 and r.headers["set-cookie"].startswith("fa_session="), r.headers["set-cookie"]


def test_a_cookie_planted_by_a_sibling_is_not_read_over_https():
    c, raw = _https_owner()
    token = raw.split(";", 1)[0].split("=", 1)[1]
    fresh = TestClient(app, base_url="https://testserver")
    # The sibling planted fa_session for the parent domain after the owner signed in: Chrome sends both, later last.
    both = fresh.get("/api/auth/me", headers={"cookie": f"__Host-fa_session={token}; fa_session=planted-by-a-sibling"})
    assert both.status_code == 200, both.text
    # A real session's value under the plain name (the sibling holding a session of its own) signs nobody in at https.
    planted = fresh.get("/api/auth/me", headers={"cookie": f"fa_session={token}"})
    assert planted.status_code == 401


def test_sign_out_over_https_clears_the_host_cookie():
    c, _ = _https_owner()
    r = c.post("/api/auth/logout", headers=HDR)
    assert r.status_code == 200
    cleared = [h for k, h in r.headers.multi_items() if k == "set-cookie" and h.startswith("__Host-fa_session=")]
    assert cleared and "secure" in cleared[0].lower() and "path=/" in cleared[0].lower()
    assert c.get("/api/auth/me").status_code == 401


def test_the_consent_sign_in_sets_the_host_cookie_over_https():
    from app import oauth

    req = oauth._signer().dumps({"client_id": "c", "redirect_uri": "https://claude.ai/cb", "explicit": True, "code_challenge": "x", "state": "s", "scopes": ["read"]})
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/oauth/consent", data={"req": req, "decision": "signin", "username": "owner", "password": PW}, headers={"origin": "https://testserver", "sec-fetch-site": "same-origin"})
    assert r.status_code == 200 and "Let an app in?" in r.text, r.text[:300]
    assert r.headers["set-cookie"].startswith("__Host-fa_session="), r.headers["set-cookie"]


def test_the_second_step_sets_the_host_cookie_over_https():
    from starlette.requests import Request
    from starlette.responses import Response

    from app import factors

    request = Request({"type": "http", "scheme": "https", "method": "POST", "path": "/", "headers": [], "query_string": b"", "server": ("testserver", 443)})
    response = Response()
    factors._set_cookie(response, request, "token")
    assert response.headers["set-cookie"].startswith("__Host-fa_session=token"), response.headers["set-cookie"]


# ---- subresources from other sites -----------------------------------------------------------------------------


@pytest.mark.parametrize("site,mode,dest", [
    ("same-site", "no-cors", "script"),
    ("same-site", "no-cors", "image"),
    ("same-site", "no-cors", "style"),
    ("same-site", "no-cors", "empty"),  # fetch(..., {mode: "no-cors"})
    ("cross-site", "no-cors", "script"),
    ("cross-site", "navigate", "iframe"),
    ("same-site", "no-cors", "object"),
])
def test_other_sites_cannot_load_the_api_as_a_subresource(owner: TestClient, site: str, mode: str, dest: str):
    r = owner.get("/api/v1/snapshot", headers={"sec-fetch-site": site, "sec-fetch-mode": mode, "sec-fetch-dest": dest})
    assert r.status_code == 403, (site, mode, dest, r.status_code)
    assert "accounts" not in r.text


@pytest.mark.parametrize("site,mode,dest", [
    ("same-origin", "cors", "empty"),  # the app's own pages
    ("same-origin", "no-cors", "image"),  # the app's own <img> (the household mark)
    ("none", "navigate", "document"),  # a typed address or a bookmark
    ("cross-site", "navigate", "document"),  # a link followed from elsewhere: the page loads, its reads stay the app's
    ("cross-site", "cors", "empty"),  # CORS decides; FA_CORS_ORIGINS names who may read
    (None, None, None),  # a key, a script, Home Assistant, a calendar server
])
def test_the_app_itself_and_links_still_reach_the_api(owner: TestClient, site, mode, dest):
    headers = {k: v for k, v in {"sec-fetch-site": site, "sec-fetch-mode": mode, "sec-fetch-dest": dest}.items() if v}
    r = owner.get("/api/v1/snapshot", headers=headers)
    assert r.status_code == 200, (site, mode, dest, r.text[:200])


def test_a_write_from_another_site_is_refused_before_the_route(owner: TestClient):
    # A sibling's no-cors POST never reaches the route (the app header already refused it there; now earlier).
    r = owner.post("/api/v1/keys", content=b"{}", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "no-cors", "sec-fetch-dest": "empty"})
    assert r.status_code == 403


def test_api_answers_say_same_origin_resource_policy(owner: TestClient, client: TestClient):
    for c, path in ((owner, "/api/v1/snapshot"), (client, "/api/health"), (client, "/api/v1/snapshot")):
        r = c.get(path)
        assert r.headers.get("cross-origin-resource-policy") == "same-origin", (path, r.status_code)
    refused = owner.get("/api/v1/snapshot", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "no-cors", "sec-fetch-dest": "script"})
    assert refused.headers.get("cross-origin-resource-policy") == "same-origin"
    assert refused.headers.get("x-content-type-options") == "nosniff"


def test_the_public_calendar_feed_and_health_still_answer_without_fetch_metadata(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/calendar/not-a-token.ics").status_code in (401, 403, 404)


def test_the_cookie_name_constants_are_the_ones_the_routes_use():
    assert auth.COOKIE == "fa_session" and auth.HOST_COOKIE == "__Host-fa_session"
