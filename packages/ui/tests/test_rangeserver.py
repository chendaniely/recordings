"""scripts/rangeserver.py: the local stand-in for GitHub Pages (`make pages-serve`, and the Pages
smoke test). Media seeks only if Range gets a 206."""

import importlib.util
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("site")
    spec = importlib.util.spec_from_file_location("rangeserver", REPO / "scripts" / "rangeserver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "a.mp3").write_bytes(b"0123456789")
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    server = module.serve(tmp_path, prefix="recordings", quiet=True)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def get(url: str, range_: str | None = None):
    request = urllib.request.Request(url, headers={"Range": range_} if range_ else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def test_a_whole_file_says_it_accepts_ranges(site):
    status, headers, body = get(f"{site}/recordings/media/a.mp3")
    assert (status, body) == (200, b"0123456789")
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "audio/mpeg"


@pytest.mark.parametrize(("range_", "content_range", "body"), [
    ("bytes=2-4", "bytes 2-4/10", b"234"),
    ("bytes=7-", "bytes 7-9/10", b"789"),
    ("bytes=-3", "bytes 7-9/10", b"789"),
    ("bytes=8-99", "bytes 8-9/10", b"89"),
])
def test_a_range_gets_206_and_just_those_bytes(site, range_, content_range, body):
    status, headers, got = get(f"{site}/recordings/media/a.mp3", range_)
    assert (status, headers["Content-Range"], got) == (206, content_range, body)
    assert headers["Content-Length"] == str(len(body))


def test_a_range_past_the_end_is_416(site):
    status, headers, _ = get(f"{site}/recordings/media/a.mp3", "bytes=10-")
    assert (status, headers["Content-Range"]) == (416, "bytes */10")


def test_only_the_prefix_is_served_like_a_project_site(site):
    assert get(f"{site}/media/a.mp3")[0] == 404  # a root-absolute URL fails, as on Pages
    assert get(f"{site}/recordingsx/media/a.mp3")[0] == 404
    assert get(f"{site}/recordings")[2] == b"<p>hi</p>"  # redirected to /recordings/
    assert get(f"{site}/recordings/../../etc/passwd")[0] == 404
