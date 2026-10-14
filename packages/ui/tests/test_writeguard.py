import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from recordings_ui import runtime
from recordings_ui.app import create_app
from recordings_ui.hosts import LOCAL_HOSTS, WriteGuard
from recordings_ui.settings import Settings


async def echo(request):
    return JSONResponse({"ok": True})


@pytest.fixture
def client():
    app = Starlette(routes=[Route("/api/thing", echo, methods=["GET", "POST", "DELETE"])])
    with TestClient(WriteGuard(app, LOCAL_HOSTS), base_url="http://localhost:8000") as c:
        yield c


JSON = {"Content-Type": "application/json"}


def test_a_same_origin_json_write_passes(client):
    assert client.post("/api/thing", json={}, headers={"Origin": "http://localhost:8000",
                                                       "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_write_from_no_browser_passes(client):
    # No Origin and no Sec-Fetch-Site: curl, the CLI's --remote, a script. No website behind it.
    assert client.post("/api/thing", content=b"{}", headers=JSON).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},  # a sibling subdomain is another origin
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:9999"},  # the same name at another port: maybe another program
    {"Origin": "https://localhost:8000"},  # another scheme
    {"Origin": "http://127.0.0.1:8000"},  # an allowed name, but not the page's own origin
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "cross-site"},
], ids=["cross-site", "same-site", "foreign-origin", "null-origin", "other-port", "other-scheme",
        "other-allowed-name", "mixed"])
def test_a_cross_site_write_is_refused(client, headers):
    # why: §15. A page on another site, or another port of this host, can post to the app's name.
    response = client.post("/api/thing", content=b"{}", headers={**JSON, **headers})
    assert response.status_code == 403


def test_a_write_to_an_unknown_host_is_refused_even_from_that_origin(client):
    # why: DNS rebinding. Host and Origin both name the attacker's site, so they agree.
    response = client.post("/api/thing", content=b"{}", headers={
        **JSON, "Host": "evil.example", "Origin": "http://evil.example"})
    assert response.status_code == 403


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data; boundary=x",
                                   "application/json; charset=utf-8", ""])
def test_anything_but_exactly_json_is_refused(client, ctype):
    # why: §15. A browser sends text/plain or a form cross-site without a CORS preflight.
    headers = {"Content-Type": ctype} if ctype else {}
    refused = client.post("/api/thing", content=b"{}", headers=headers)
    assert refused.status_code == 415
    assert "exactly application/json" in refused.text  # says why a charset is refused too
    assert client.delete("/api/thing", headers=headers).status_code == 415


def test_reads_are_not_affected(client):
    assert client.get("/api/thing", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


KEPT_TO_THIS_ORIGIN = {"cross-origin-resource-policy": "same-origin",
                       "content-security-policy": "frame-ancestors 'none'",
                       "x-frame-options": "DENY",
                       "cross-origin-opener-policy": "same-origin",
                       "referrer-policy": "same-origin"}


def test_every_response_keeps_its_resources_to_this_origin(client):
    # why: §15. Without CORP, another site could embed /media in an <audio> tag, or load the API's
    # answers with a no-cors request, by the app's real name. Without the framing headers, any
    # page could frame the app, whose own websocket passes every check, under a decoy. Without
    # the opener and referrer policies, a page that opens the app shares its browsing context,
    # and a link in a note sends the app's address to the site it opens.
    refused = client.post("/api/thing", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"})
    for response in (client.get("/api/thing"), refused):
        assert {k: response.headers.get(k) for k in KEPT_TO_THIS_ORIGIN} == KEPT_TO_THIS_ORIGIN


def test_the_real_app_is_wrapped(demo_archive, demo_ids):
    app = create_app(Settings(archive=demo_archive, demo=True))
    try:
        with TestClient(app, base_url="http://localhost:8000") as c:
            assert c.post("/", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
            assert c.post("/healthz", json={}, headers={"Origin": "http://localhost:9999"}).status_code == 403
            assert c.post("/healthz", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
            media = c.get(f"/media/{demo_ids['fdr-fireside-1']}", headers={"Range": "bytes=0-9"})
            assert media.status_code == 206
            assert media.headers["cross-origin-resource-policy"] == "same-origin"
            # why: a private recording's audio would otherwise sit in the disk cache of every
            # device that played it. Range requests still seek: the browser holds the bytes it
            # needs while it plays.
            assert media.headers["cache-control"] == "no-store"
            health = c.get("/healthz")
            assert health.headers["cross-origin-resource-policy"] == "same-origin"
            assert "cache-control" not in health.headers  # only /media is kept off the disk
            page = c.get("/")  # the Shiny page itself: no other page may frame it
            assert {k: page.headers.get(k) for k in KEPT_TO_THIS_ORIGIN} == KEPT_TO_THIS_ORIGIN
    finally:
        runtime.configure(None)
