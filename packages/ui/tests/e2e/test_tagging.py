"""Browser tests that tag (tagging spec §9). Each gets a server of its own, so what one test
writes never shows in another; each reads the files the app wrote, through Archive."""

import re
import time

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def wait_until(check, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def open_library(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.get_by_test_id("recording-row")).to_have_count(4)


def open_row(page: Page, text: str) -> None:
    page.get_by_test_id("recording-row").filter(has_text=text).click()
    expect(page.get_by_test_id("recording-header")).to_contain_text(text)


def test_the_headers_x_removes_a_tag_and_undo_puts_it_back(page: Page, server, console, demo_ids):
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    open_row(page, "JFK")
    header = page.get_by_test_id("recording-header")
    header.locator("[data-testid=chip][data-tag=talks]").hover()
    x = header.get_by_role("button", name=re.compile(r"^Remove tag talks from JFK"))
    box = x.bounding_box()
    assert box is not None and box["width"] >= 24 and box["height"] >= 24  # the × hit area
    x.click()
    toast = page.get_by_test_id("action-toast")
    expect(toast).to_contain_text("Removed talks from 1 recording")
    expect(header.locator("[data-testid=chip][data-tag=talks]")).to_have_count(0)
    assert server.tags(jfk) == [("notes/conference-talk", "auto")]
    # Focus went to the next control in the row: the remaining chip's ×.
    expect(
        header.get_by_role("button", name=re.compile(r"^Remove tag notes\/conference-talk"))
    ).to_be_focused()
    toast.get_by_role("button", name="Undo").click()
    expect(header.locator("[data-testid=chip][data-tag=talks]")).to_have_count(1)
    wait_until(lambda: server.tags(jfk) == [("talks", "you"), ("notes/conference-talk", "auto")])


def test_removing_a_last_private_tag_asks_first(page: Page, server, console, demo_ids):
    fdr = demo_ids["fdr-fireside-1"]
    open_library(page, server.url)
    open_row(page, "Fireside")
    header = page.get_by_test_id("recording-header")
    remove = header.get_by_role("button", name=re.compile(r"^Remove tag private from Fireside"))
    remove.click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Remove private from 1 recording?")
    expect(dialog).to_contain_text("1 will have no tags left, and stays hidden until its next tag.")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    page.keyboard.press("Escape")  # Esc means Cancel
    expect(dialog).to_have_count(0)
    # The × had no next control, so focus went to the title, and the dialog gave it back there.
    expect(header.locator("h1")).to_be_focused()
    expect(header.locator("[data-testid=chip][data-tag=private]")).to_have_count(1)
    assert server.tags(fdr) == [("private", "you")]
    remove.click()
    page.get_by_role("alertdialog").get_by_role("button", name="Remove").click()
    expect(page.get_by_test_id("action-toast")).to_contain_text("Removed private from 1 recording")
    wait_until(lambda: server.tags(fdr) == [])
    expect(header.get_by_test_id("lock")).to_have_count(0)  # the row's flag, from the server
