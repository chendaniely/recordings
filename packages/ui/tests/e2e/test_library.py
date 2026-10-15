"""Browser tests against a real `recordings-ui --demo` server. Run with `make e2e`.

Chromium builds from Playwright have no H.264/AAC decoder, so the video test checks the element,
not playback; MP3 plays, so seeking is tested on the JFK audio.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    # One server for the module: these tests only read its temp copy of the demo archive. A test
    # that writes would need a server of its own.
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "RECORDINGS_ARCHIVE"}
    log_path = tmp_path_factory.mktemp("e2e") / "server.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "recordings_ui", "--demo", "--port", str(port)],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            if proc.poll() is not None:
                pytest.fail(f"demo server exited with {proc.returncode}:\n{log_path.read_text(errors='replace')[-2000:]}")
            try:
                with urllib.request.urlopen(f"{url}/healthz", timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail(f"demo server did not start in 20 s:\n{log_path.read_text(errors='replace')[-2000:]}")
                time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def open_library(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.get_by_test_id("recording-row")).to_have_count(4)


def test_untagged_filter_shows_only_apollo_13(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("filter-untagged").click()
    rows = page.get_by_test_id("recording-row")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Apollo 13")


def test_clicking_a_line_seeks_the_audio(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.wait_for_function("document.querySelector('[data-testid=media]')?.readyState >= 1", timeout=10_000)
    turn = page.get_by_test_id("turn").nth(2)
    start = float(turn.get_attribute("data-start"))
    turn.click()
    page.wait_for_function(
        "s => Math.abs(document.querySelector('[data-testid=media]').currentTime - s) < 0.5",
        arg=start, timeout=5_000,
    )
    expect(turn).to_have_class(re.compile(r"\bnow\b"))


def test_a_newly_chosen_recording_starts_from_the_beginning(page: Page, server_url):
    # why: playback time belongs to one mounted recording. JFK's third line starts at 20.4 s;
    # carried over to Apollo 11 (lines at 0, 12.0, 19.9 …) it would mark the third line, not the
    # first. Back on JFK (first line at 1.8 s) the fresh audio sits at 0, so no line is current.
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.wait_for_function("document.querySelector('[data-testid=media]')?.readyState >= 1", timeout=10_000)
    page.get_by_test_id("turn").nth(2).click()
    expect(page.get_by_test_id("turn").nth(2)).to_have_class(re.compile(r"\bnow\b"))
    page.get_by_test_id("recording-row").filter(has_text="Apollo 11").click()
    expect(page.locator("video[data-testid=media]")).to_have_count(1)
    expect(page.get_by_test_id("turn").first).to_have_class(re.compile(r"\bnow\b"))
    expect(page.locator("[data-testid=turn].now")).to_have_count(1)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    expect(page.locator("audio[data-testid=media]")).to_have_count(1)
    expect(page.locator("[data-testid=turn].now")).to_have_count(0)


def test_private_recording_shows_the_lock(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="Fireside").click()
    expect(page.get_by_test_id("lock")).to_be_visible()


def test_video_recording_renders_a_video_element(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="Apollo 11").click()
    expect(page.locator("video[data-testid=media]")).to_have_count(1)


def test_notes_compare_side_by_side(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.get_by_test_id("tab-notes").click()
    expect(page.get_by_test_id("notes-output")).to_have_count(2)
    page.get_by_test_id("compare-select").select_option(index=1)
    expect(page.get_by_test_id("compare-view").locator("section")).to_have_count(2)


def test_dark_mode_sticks_across_reloads(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("theme-dark").click()
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
    page.reload()
    expect(page.get_by_test_id("recording-row")).to_have_count(4)
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))


def test_the_status_page_shows_backups_and_disk(page: Page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="Status").click()
    expect(page.get_by_test_id("status-page")).to_be_visible()
    expect(page.get_by_test_id("backups-unconfigured")).to_be_visible()
    expect(page.get_by_role("heading", name="Disk")).to_be_visible()
    expect(page.get_by_test_id("disk-unknown")).to_be_visible()  # the demo shows no disk figure
    expect(page).to_have_url(re.compile(r"#/status$"))
    page.get_by_role("button", name="Library").click()
    expect(page.get_by_test_id("status-page")).to_have_count(0)


# Outputs whose Shiny binding points at an element that is no longer in the page.
_DETACHED_BINDINGS = """() => Object.entries(window.Shiny.shinyapp.$bindings)
    .filter(([, binding]) => binding.el && !binding.el.isConnected).map(([id]) => id)"""
# shinyreact cleans up an unmounted output on the next animation frame; wait past it.
_TWO_FRAMES = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"


def test_a_page_left_behind_keeps_no_binding_and_logs_nothing(page: Page, server_url):
    # why: shinyreact 0.1.0 removes an unmounted output's element before Shiny unbinds it, and
    # Shiny only unbinds what it finds in the page, so the binding outlives its element and every
    # later value for that output logs "Output <id> not found". The Pages test saw it on
    # 2026-10-15, when the recording's first value landed just after the Status click.
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    expect(page.get_by_test_id("recording-when")).to_be_visible()
    page.get_by_role("button", name="Status").click()
    expect(page.get_by_test_id("status-page")).to_be_visible()
    page.evaluate(_TWO_FRAMES)
    assert page.evaluate(_DETACHED_BINDINGS) == []
    # A value for the library's pane while it is away: the server clears the selection.
    page.evaluate("""() => new Promise((done) => {
        window.jQuery(document).one("shiny:idle", done);
        setTimeout(done, 5000);
        window.Shiny.setInputValue("selected_id:shinyreact.default", null);
    })""")
    page.get_by_role("button", name="Library").click()
    expect(page.get_by_test_id("recording-row")).to_have_count(4)
    page.evaluate(_TWO_FRAMES)
    assert page.evaluate(_DETACHED_BINDINGS) == []
    assert errors == []


def test_the_status_page_does_not_scroll_sideways_on_a_phone(page: Page, server_url):
    # why: a card's minimum width plus its padding is wider than a 320 px phone.
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto(f"{server_url}/#/status")
    expect(page.get_by_test_id("status-page")).to_be_visible()
    overflow = page.evaluate("""() => {
        const main = document.querySelector("[data-testid=status-page]");
        return [main.scrollWidth - main.clientWidth,
                document.documentElement.scrollWidth - document.documentElement.clientWidth];
    }""")
    assert overflow == [0, 0]


@pytest.mark.browser_context_args(timezone_id="Pacific/Auckland")
def test_the_header_shows_the_local_time_with_its_offset(page: Page, server_url):
    # why: the archive spans several time zones, so the header shows each recording's own local
    # time and UTC offset (spec §12.1), whatever zone the browser is in: here, 14 hours ahead of
    # the recording's own.
    assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "Pacific/Auckland"
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    expect(page.get_by_test_id("recording-when")).to_have_text("1962-09-12 10:00 UTC-05:00")
