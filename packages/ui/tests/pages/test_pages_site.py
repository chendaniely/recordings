"""Smoke test for the static GitHub Pages demo (spec §17.1). Run with `make pages`, then
`make pages-test`.

It serves _site/ as Pages does: under /recordings/, answering Range requests
(scripts/rangeserver.py). The app runs on Pyodide, with Python 3.12 and older pydantic and
markdown-it than the server, so this also catches code that only works on the server's versions.
Each test gets a fresh browser context, so each one boots Pyodide cold.
"""

import importlib.util
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Frame, FrameLocator, Page, expect

pytestmark = pytest.mark.pages

REPO = Path(__file__).resolve().parents[4]
SITE = REPO / "_site"
PREFIX = "/recordings/"  # https://chendaniely.github.io/recordings/
BOOT_TIMEOUT = 60_000  # the first visit downloads about 16 MB and starts Python in the browser


def _rangeserver():
    spec = importlib.util.spec_from_file_location("rangeserver", REPO / "scripts" / "rangeserver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site_url():
    if not (SITE / "index.html").is_file():
        pytest.skip("_site/ is not built; run `make pages` first")
    server = _rangeserver().serve(SITE, prefix=PREFIX, quiet=True)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}{PREFIX}"
    finally:
        server.shutdown()
        server.server_close()


class Watch:
    """Console errors, and any request that leaves the site: another origin, or the local
    origin outside /recordings/ (a root-absolute URL, which 404s on Pages)."""

    def __init__(self, page: Page, site_url: str):
        self.site = urlsplit(site_url)
        self.errors: list[str] = []
        self.outside: list[str] = []
        self.seen = 0
        self.media_statuses: set[int] = set()
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.on("pageerror", lambda e: self.errors.append(f"uncaught: {e}"))
        # The context sees the page, its iframe, the workers and the service worker.
        page.context.on("request", self._request)
        page.context.on("response", self._response)

    def _request(self, request) -> None:
        url = urlsplit(request.url)
        if url.scheme in ("data", "blob", "about"):
            return
        self.seen += 1
        if url.netloc != self.site.netloc or not url.path.startswith(PREFIX):
            self.outside.append(request.url)

    def _response(self, response) -> None:
        if urlsplit(response.url).path.startswith(f"{PREFIX}media/"):
            self.media_statuses.add(response.status)

    def assert_clean(self) -> None:
        assert self.seen > 0
        assert not self.errors, f"console errors: {self.errors}"
        assert not self.outside, f"requests outside the site: {self.outside}"


@pytest.fixture
def watch(page: Page, site_url) -> Watch:
    return Watch(page, site_url)


def open_demo(page: Page, site_url: str) -> tuple[FrameLocator, Frame]:
    """Load the site and wait for the library. Shinylive runs the app in an iframe."""
    page.goto(site_url)
    app = page.frame_locator("iframe").first
    expect(app.get_by_test_id("recording-row")).to_have_count(4, timeout=BOOT_TIMEOUT)
    frame = page.locator("iframe").first.element_handle().content_frame()
    return app, frame


def test_the_library_renders_four_recordings(page: Page, site_url, watch, capsys):
    start = time.monotonic()
    open_demo(page, site_url)
    with capsys.disabled():
        print(f"\nPages demo: cold first load to 4 rows in {time.monotonic() - start:.1f} s")
    watch.assert_clean()


def test_jfk_plays_from_the_static_host_and_a_line_seeks(page: Page, site_url, watch, demo_ids):
    app, frame = open_demo(page, site_url)
    app.get_by_test_id("recording-row").filter(has_text="JFK").click()
    turns = app.get_by_test_id("turn")
    expect(turns.first).to_be_visible()
    assert turns.count() > 3
    frame.wait_for_function(
        "document.querySelector('[data-testid=media]')?.readyState >= 1", timeout=30_000)
    # Pages serves the file itself, under the site's media/ folder, with its extension.
    src = frame.evaluate("document.querySelector('[data-testid=media]').currentSrc")
    assert src == f"{site_url}media/{demo_ids['jfk-rice']}.mp3"
    turn = turns.nth(2)
    start = float(turn.get_attribute("data-start"))
    turn.click()
    # Seeking needs Range support (206); without it currentTime snaps back to 0.
    assert 206 in watch.media_statuses
    frame.wait_for_function(
        "s => Math.abs(document.querySelector('[data-testid=media]').currentTime - s) < 0.5",
        arg=start, timeout=10_000)
    expect(turn).to_have_class(re.compile(r"\bnow\b"))
    watch.assert_clean()


def test_notes_show_two_outputs_and_compare_side_by_side(page: Page, site_url, watch):
    app, _ = open_demo(page, site_url)
    app.get_by_test_id("recording-row").filter(has_text="JFK").click()
    app.get_by_test_id("tab-notes").click()
    expect(app.get_by_test_id("notes-output")).to_have_count(2)
    app.get_by_test_id("compare-select").select_option(index=1)
    expect(app.get_by_test_id("compare-view").locator("section")).to_have_count(2)
    watch.assert_clean()


def test_the_dark_theme_toggle_works(page: Page, site_url, watch):
    app, _ = open_demo(page, site_url)
    html = app.locator("html")
    app.get_by_test_id("theme-dark").click()
    expect(html).to_have_class(re.compile(r"\bdark\b"))
    app.get_by_test_id("theme-light").click()
    expect(html).to_have_class(re.compile(r"\blight\b"))
    expect(html).not_to_have_class(re.compile(r"\bdark\b"))
    watch.assert_clean()
