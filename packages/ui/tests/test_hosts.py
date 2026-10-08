"""The app answers only to its own names, and refuses websockets opened by other sites.

Without this, a website in your own browser could rebind its DNS name to the app's address
(DNS rebinding: the request then carries the website's Host), or open Shiny's websocket
directly (the browser sends the website's Origin) and be handed a private recording.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from recordings_ui import runtime
from recordings_ui.app import create_app
from recordings_ui.hosts import LOCAL_HOSTS, HostGuard
from recordings_ui.settings import Settings


async def _hello(request):
    return PlainTextResponse("hi")


async def _echo(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def guarded(allowed=LOCAL_HOSTS, lifespan=None) -> TestClient:
    inner = Starlette(routes=[Route("/", _hello), WebSocketRoute("/ws", _echo)], lifespan=lifespan)
    return TestClient(HostGuard(inner, frozenset(allowed)), base_url="http://localhost")


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8000",
                                  "localhost.attacker.example", "evil-localhost", "[::2]:8000", ""])
def test_an_unknown_host_is_400(host):
    r = guarded().get("/", headers={"Host": host})
    assert r.status_code == 400 and r.text == "unknown host"


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000",
                                  "[::1]", "LOCALHOST:8000", "LocalHost"])
def test_allowed_host_variants_pass(host):
    r = guarded().get("/", headers={"Host": host})
    assert r.status_code == 200 and r.text == "hi"


def test_allowed_names_are_compared_lowercased():
    client = guarded(allowed={"My-Homelab"})
    assert client.get("/", headers={"Host": "my-homelab:8000"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost"}).status_code == 400  # only what it's given


def _refused(client, url, headers) -> int:
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(url, headers=headers):
        pass
    return exc.value.code


@pytest.mark.parametrize("origin", ["http://evil.example", "https://localhost.evil.example",
                                    "null", "http://[::1"])
def test_a_websocket_from_a_foreign_origin_is_refused(origin):
    assert _refused(guarded(), "ws://localhost/ws", {"Origin": origin}) == 1008


def test_a_websocket_to_an_unknown_host_is_refused():
    assert _refused(guarded(), "ws://attacker.example/ws", {"Origin": "http://attacker.example"}) == 1008


@pytest.mark.parametrize("origin", ["http://localhost:8000", "http://127.0.0.1:8000",
                                    "http://[::1]:8000", "HTTP://LOCALHOST"])
def test_a_websocket_from_an_allowed_origin_is_accepted(origin):
    with guarded().websocket_connect("ws://localhost:8000/ws", headers={"Origin": origin}) as ws:
        assert ws.receive_text() == "hello"


def test_a_websocket_without_an_origin_is_accepted():
    # Browsers always send Origin on a websocket; a client without one is not a web page.
    with guarded().websocket_connect("ws://localhost/ws") as ws:
        assert ws.receive_text() == "hello"


def test_lifespan_passes_straight_through():
    from contextlib import asynccontextmanager

    started = []

    @asynccontextmanager
    async def lifespan(app):
        started.append(True)
        yield

    with guarded(lifespan=lifespan):
        assert started == [True]


# ---- the real app ----------------------------------------------------------------------

@pytest.fixture
def app_client(demo_archive):
    settings = Settings(archive=demo_archive, demo=True)
    with TestClient(create_app(settings), base_url="http://localhost") as c:
        yield c
    runtime.configure(None)


def test_the_app_refuses_a_foreign_host_everywhere(app_client, demo_ids):
    foreign = {"Host": "attacker.example:8000"}
    assert app_client.get("/healthz", headers=foreign).status_code == 400
    assert app_client.get(f"/media/{demo_ids['fdr-fireside-1']}", headers=foreign).status_code == 400
    assert app_client.get("/", headers=foreign).status_code == 400  # the mounted Shiny app


def test_the_app_refuses_a_cross_site_websocket(app_client):
    # The review's exploit: a page on another site opening Shiny's websocket.
    assert _refused(app_client, "ws://localhost:8000/websocket/",
                    {"Origin": "http://evil.example"}) == 1008


def test_the_app_accepts_its_own_websocket(app_client):
    with app_client.websocket_connect("ws://localhost:8000/websocket/",
                                      headers={"Origin": "http://localhost:8000"}):
        pass  # accepted: entering the session did not raise WebSocketDisconnect


def test_the_app_answers_to_its_configured_names(demo_archive):
    settings = Settings(archive=demo_archive, demo=False,
                        allowed_hosts=LOCAL_HOSTS | {"my-homelab"})
    try:
        with TestClient(create_app(settings), base_url="http://my-homelab:8000") as c:
            assert c.get("/healthz").status_code == 200
            assert c.get("/healthz", headers={"Host": "other-host"}).status_code == 400
    finally:
        runtime.configure(None)
