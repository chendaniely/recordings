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
def server_url():
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "RECORDINGS_ARCHIVE"}
    proc = subprocess.Popen([sys.executable, "-m", "recordings_ui", "--demo", "--port", str(port)], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("demo server did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


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
    page.wait_for_function("document.querySelector('[data-testid=media]')?.readyState >= 1")
    turn = page.get_by_test_id("turn").nth(2)
    start = float(turn.get_attribute("data-start"))
    turn.click()
    current = page.evaluate("document.querySelector('[data-testid=media]').currentTime")
    assert abs(current - start) < 0.5
    expect(turn).to_have_class(re.compile(r"\bnow\b"))


def test_a_newly_chosen_recording_starts_from_the_beginning(page: Page, server_url):
    # why: playback time belongs to one mounted recording. JFK's third line starts at 20.4 s;
    # carried over to Apollo 11 (lines at 0, 12.0, 19.9 …) it would mark the third line, not the
    # first. Back on JFK (first line at 1.8 s) the fresh audio sits at 0, so no line is current.
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.wait_for_function("document.querySelector('[data-testid=media]')?.readyState >= 1")
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
