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
# Every wait after a click that runs Python in the browser: Pyodide renders slower than the server,
# and slower still on a CI runner, than Playwright's default 5 s allows.
RENDER_TIMEOUT = 30_000


def _rangeserver():
    spec = importlib.util.spec_from_file_location(
        "rangeserver", REPO / "scripts" / "rangeserver.py"
    )
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
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.on("pageerror", lambda e: self.errors.append(f"uncaught: {e}"))
        # The context sees the page, its iframe, the workers and the service worker.
        page.context.on("request", self._request)

    def _request(self, request) -> None:
        url = urlsplit(request.url)
        if url.scheme in ("data", "blob", "about"):
            return
        self.seen += 1
        if url.netloc != self.site.netloc or not url.path.startswith(PREFIX):
            self.outside.append(request.url)

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
    expect(turns.first).to_be_visible(timeout=RENDER_TIMEOUT)
    assert turns.count() > 3
    frame.wait_for_function(
        "document.querySelector('[data-testid=media]')?.readyState >= 1", timeout=RENDER_TIMEOUT
    )
    # Pages serves the file itself, under the site's media/ folder, with its extension.
    src = frame.evaluate("document.querySelector('[data-testid=media]').currentSrc")
    assert src == f"{site_url}media/{demo_ids['jfk-rice']}.mp3"
    turn = turns.nth(2)
    start = float(turn.get_attribute("data-start"))
    turn.click()
    # Only currentTime is checked, not a ranged response to the seek. The first request
    # (Range: bytes=0-, answered 206) buffers all 90 s of this MP3, so Chromium seeks within its
    # buffer and fetches nothing new. tests/test_rangeserver.py covers the server's 206s.
    frame.wait_for_function(
        "s => Math.abs(document.querySelector('[data-testid=media]').currentTime - s) < 0.5",
        arg=start,
        timeout=RENDER_TIMEOUT,
    )
    expect(turn).to_have_class(re.compile(r"\bnow\b"), timeout=RENDER_TIMEOUT)
    watch.assert_clean()


def test_notes_show_two_outputs_and_compare_side_by_side(page: Page, site_url, watch):
    app, _ = open_demo(page, site_url)
    app.get_by_test_id("recording-row").filter(has_text="JFK").click()
    app.get_by_test_id("tab-notes").click()
    expect(app.get_by_test_id("notes-output")).to_have_count(2, timeout=RENDER_TIMEOUT)
    app.get_by_test_id("compare-select").select_option(index=1)
    expect(app.get_by_test_id("compare-view").locator("section")).to_have_count(
        2, timeout=RENDER_TIMEOUT
    )
    watch.assert_clean()


def test_the_dark_theme_toggle_works(page: Page, site_url, watch):
    app, _ = open_demo(page, site_url)
    html = app.locator("html")
    app.get_by_test_id("theme-dark").click()
    expect(html).to_have_class(re.compile(r"\bdark\b"), timeout=RENDER_TIMEOUT)
    app.get_by_test_id("theme-light").click()
    expect(html).to_have_class(re.compile(r"\blight\b"), timeout=RENDER_TIMEOUT)
    expect(html).not_to_have_class(re.compile(r"\bdark\b"), timeout=RENDER_TIMEOUT)
    watch.assert_clean()


def test_the_status_page_shows_no_made_up_disk(page: Page, site_url, watch):
    # why: on Pages the "disk" is Pyodide's in-memory file system. Status opens, and says the free
    # space is unknown rather than showing a figure for it.
    app, _ = open_demo(page, site_url)
    app.get_by_role("button", name="Status").click()
    expect(app.get_by_test_id("status-page")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("backups-unconfigured")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("disk-unknown")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_role("meter")).to_have_count(0)
    watch.assert_clean()


def test_tagging_works_in_the_browser_and_a_reload_brings_the_demo_back(
    page: Page, site_url, watch
):
    # why: tagging spec §1 and §5. Pages tags in memory through the same operations, says so, and
    # saves nothing: a reload shows the demo as it was. It also proves recordings_ui.tagging and
    # recordings.tagops import on Pyodide, and that Shinylive delivers send_message (spec §11):
    # the reply lands as a toast and the chip leaves "pending". The demo must not look like a lost
    # connection (no alert toast), and a second tag still works afterwards (Task 10 carry).
    app, _ = open_demo(page, site_url)
    expect(app.locator(".demo-note").first).to_have_text(
        "Demo: changes aren't saved", timeout=RENDER_TIMEOUT
    )
    row = app.get_by_test_id("recording-row").filter(has_text="Apollo 13")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Add tags to Apollo 13")).click()
    box = app.get_by_test_id("picker-input")
    box.fill("in-the-browser")
    box.press("Shift+Enter")
    chip = row.locator("[data-testid=chip][data-tag='in-the-browser']")
    expect(chip).to_have_count(1, timeout=RENDER_TIMEOUT)
    expect(chip).not_to_have_class(re.compile(r"\bpending\b"), timeout=RENDER_TIMEOUT)  # the reply
    expect(app.get_by_test_id("action-toast").first).to_contain_text(
        "Demo: changes aren't saved", timeout=RENDER_TIMEOUT
    )
    expect(app.get_by_test_id("alert-toast")).to_have_count(0)
    box.fill("second-one")
    box.press("Shift+Enter")
    second = row.locator("[data-testid=chip][data-tag='second-one']")
    expect(second).to_have_count(1, timeout=RENDER_TIMEOUT)
    expect(second).not_to_have_class(re.compile(r"\bpending\b"), timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("alert-toast")).to_have_count(0)
    box.press("Escape")
    app, _ = open_demo(page, site_url)  # a reload
    again = app.get_by_test_id("recording-row").filter(has_text="Apollo 13")
    expect(again.locator("[data-testid=chip]")).to_have_count(0, timeout=RENDER_TIMEOUT)
    watch.assert_clean()


def test_removing_a_last_private_tag_asks_first_on_pages_too(page: Page, site_url, watch):
    app, _ = open_demo(page, site_url)
    app.get_by_test_id("recording-row").filter(has_text="Fireside").click()
    header = app.get_by_test_id("recording-header")
    expect(header).to_contain_text("Fireside", timeout=RENDER_TIMEOUT)
    header.get_by_role("button", name=re.compile(r"^Remove tag private from Fireside")).click()
    dialog = app.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Remove private from 1 recording?", timeout=RENDER_TIMEOUT)
    dialog.get_by_role("button", name="Cancel").click()
    expect(header.locator("[data-testid=chip][data-tag=private]")).to_have_count(
        1, timeout=RENDER_TIMEOUT
    )
    watch.assert_clean()


def test_dragging_a_row_onto_a_sidebar_tag_tags_it_in_memory(page: Page, site_url, watch):
    # why: Task 13 carry. The Library's drag and drop runs on Pyodide too (rows drag only with
    # (hover: hover), which desktop Chromium has), with the demo's toast.
    app, _ = open_demo(page, site_url)
    row = app.get_by_test_id("recording-row").filter(has_text="Apollo 13")
    target = app.get_by_test_id("filter-tag").filter(has_text="space/apollo")
    expect(target).to_have_count(1, timeout=RENDER_TIMEOUT)
    row_box = row.bounding_box()
    title = row.locator(".row-title").bounding_box()
    at = {"x": title["x"] - row_box["x"] + 4, "y": title["y"] - row_box["y"] + title["height"] / 2}
    row.drag_to(target, source_position=at)
    toast = app.get_by_test_id("action-toast").first
    expect(toast).to_contain_text("Added space/apollo to 1 recording", timeout=RENDER_TIMEOUT)
    expect(toast).to_contain_text("Demo: changes aren't saved")
    expect(app.get_by_test_id("alert-toast")).to_have_count(0)
    watch.assert_clean()


@pytest.mark.browser_context_args(
    viewport={"width": 375, "height": 740}, is_mobile=True, has_touch=True
)
def test_on_a_phone_the_tags_page_is_the_untagged_list_and_toasts_sit_at_the_top(
    page: Page, site_url, watch
):
    # why: Task 15 carry. Every other Pages test runs at desktop size. The phone layout runs here
    # once (Pyodide boots cold for each test): no grid, the + opens a dialog, toasts at the top.
    page.goto(site_url)
    app = page.frame_locator("iframe").first
    rows = app.get_by_test_id("recording-row")
    expect(rows.first).to_be_visible(timeout=BOOT_TIMEOUT)
    frame = page.locator("iframe").first.element_handle().content_frame()
    frame.evaluate("() => { location.hash = '#/tags'; }")
    expect(app.get_by_test_id("left-count")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("tags-grid")).to_have_count(0)
    expect(rows).to_have_count(1, timeout=RENDER_TIMEOUT)  # the Untagged list: Apollo 13
    rows.first.get_by_role("button", name=re.compile(r"^Add tags to Apollo 13")).dispatch_event(
        "click"
    )
    sheet = app.get_by_role("dialog")
    expect(sheet).to_be_visible(timeout=RENDER_TIMEOUT)
    box = sheet.get_by_test_id("picker-input")
    box.fill("on-the-go")
    box.press("Shift+Enter")
    expect(app.get_by_test_id("action-toast").first).to_contain_text(
        "Added on-the-go to 1 recording", timeout=RENDER_TIMEOUT
    )
    toaster = app.locator("[data-sonner-toaster]")
    assert toaster.bounding_box()["y"] < 370, toaster.bounding_box()  # the top half of the screen
    watch.assert_clean()
