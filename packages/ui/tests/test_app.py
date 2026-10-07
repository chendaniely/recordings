from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recordings_ui.app import create_app
from recordings_ui.settings import Settings

WWW = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "www"


@pytest.fixture
def client(demo_archive):
    with TestClient(create_app(Settings(archive=demo_archive, demo=True))) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "demo": True}


def test_media_is_served_with_ranges(client, demo_ids):
    url = f"/media/{demo_ids['jfk-rice']}"
    full = client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/mpeg"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
    assert len(part.content) == 100


def test_a_range_past_the_end_is_416_not_500(client, demo_ids):
    r = client.get(f"/media/{demo_ids['jfk-rice']}", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


@pytest.mark.parametrize(
    "bad", ["20200101T000000+0000_00000000", "not-an-id", "20261399T256199+0000_deadbeef"])
def test_unknown_or_malformed_media_is_404(client, bad):
    assert client.get(f"/media/{bad}").status_code == 404


def test_video_media_type(client, demo_ids):
    r = client.get(f"/media/{demo_ids['apollo11-first-steps']}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"


@pytest.mark.skipif(not (WWW / "ui.js").is_file(), reason="frontend not built (make build)")
def test_the_page_and_the_fonts_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "ui.js" in page.text
    font = client.get("/fonts/AtkinsonHyperlegible-Regular.woff2")
    assert font.status_code == 200
