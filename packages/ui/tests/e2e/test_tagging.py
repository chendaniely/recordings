"""Browser tests that tag (tagging spec §9). Each gets a server of its own, so what one test
writes never shows in another; each reads the files the app wrote, through Archive."""

import itertools
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
    # Focus went to the next control in the row: the remaining chip's ×. It shows, though the
    # click was a mouse's, and its chip shows the whole tag (Task 10 council).
    after = header.get_by_role("button", name=re.compile(r"^Remove tag notes\/conference-talk"))
    expect(after).to_be_focused()
    page.mouse.move(0, 0)
    expect(after).to_have_css("opacity", "1")
    label = header.locator("[data-testid=chip][data-tag='notes/conference-talk'] .chip-label")
    expect(label).to_have_css("max-width", "none")
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
    # Focus went to the ×'s next control, the +, and the dialog gave it back there.
    expect(header.get_by_role("button", name=re.compile(r"^Add tags to Fireside"))).to_be_focused()
    expect(header.locator("[data-testid=chip][data-tag=private]")).to_have_count(1)
    assert server.tags(fdr) == [("private", "you")]
    remove.click()
    page.get_by_role("alertdialog").get_by_role("button", name="Remove").click()
    expect(page.get_by_test_id("action-toast")).to_contain_text("Removed private from 1 recording")
    wait_until(lambda: server.tags(fdr) == [])
    expect(header.get_by_test_id("lock")).to_have_count(0)  # the row's flag, from the server


IN_TOASTER = "() => document.activeElement?.closest('[data-sonner-toaster]') != null"


def test_a_toast_reached_from_the_keyboard_stays_and_its_undo_gives_focus_back(
    page: Page, server, console, demo_ids
):
    # why: the pause sat on the toast's inner text, so focus on its <li> (Alt+T, Tab) let it go
    # at 8 s; and Undo from the keyboard left focus on <body> (Task 10 council).
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    open_row(page, "JFK")
    header = page.get_by_test_id("recording-header")
    header.get_by_role("button", name=re.compile(r"^Remove tag talks from JFK")).focus()
    page.keyboard.press("Enter")
    toast = page.get_by_test_id("action-toast")
    expect(toast).to_contain_text("Removed talks from 1 recording")
    after = header.get_by_role("button", name=re.compile(r"^Remove tag notes\/conference-talk"))
    expect(after).to_be_focused()
    page.keyboard.press("Alt+KeyT")  # Sonner's "go to notifications"
    page.keyboard.press("Tab")
    assert page.evaluate(IN_TOASTER)
    page.wait_for_timeout(9_000)  # longer than a toast's 8 s
    expect(toast).to_be_visible()
    undo = toast.get_by_role("button", name="Undo")
    while not page.evaluate("() => document.activeElement?.textContent === 'Undo'"):
        page.keyboard.press("Tab")
        assert page.evaluate(IN_TOASTER)
    page.keyboard.press("Enter")
    expect(undo).to_have_count(0)
    expect(after).to_be_focused()  # back where it was, not on <body>
    wait_until(lambda: server.tags(jfk) == [("talks", "you"), ("notes/conference-talk", "auto")])


def test_redo_says_redone_and_offers_undo(page: Page, server, console, demo_ids):
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    open_row(page, "JFK")
    header = page.get_by_test_id("recording-header")
    header.locator("[data-testid=chip][data-tag=talks]").hover()
    header.get_by_role("button", name=re.compile(r"^Remove tag talks from JFK")).click()
    page.get_by_test_id("action-toast").filter(has_text="Removed talks").get_by_role(
        "button", name="Undo"
    ).click()
    undone = page.get_by_test_id("action-toast").filter(has_text="Undone on 1 recording")
    undone.get_by_role("button", name="Redo").click()
    redone = page.get_by_test_id("action-toast").filter(has_text="Redone on 1 recording")
    expect(redone.get_by_role("button", name="Undo")).to_be_visible()
    wait_until(lambda: server.tags(jfk) == [("notes/conference-talk", "auto")])


@pytest.mark.browser_context_args(
    is_mobile=True, has_touch=True, viewport={"width": 300, "height": 700}
)
def test_the_headers_touch_targets_are_44_px_and_never_overlap(page: Page, server, console):
    # why: 44 px targets that overhang their chips, on rows 12 px apart, overlapped, so a slightly
    # low tap removed the tag on the row below (Task 10 council). Whether two targets line up
    # depends on the tags' lengths, so this checks what makes it impossible for any tags: the
    # targets of one row end before the next row's begin.
    open_library(page, server.url)
    open_row(page, "JFK")
    assert page.evaluate("() => matchMedia('(hover: none)').matches")
    header = page.get_by_test_id("recording-header")
    chips = header.get_by_test_id("chip")
    xs = header.locator(".chip-x")
    assert xs.count() == chips.count()
    rows: dict[int, list[dict]] = {}
    for i in range(chips.count()):
        target = xs.nth(i).bounding_box()
        assert target["width"] >= 44 and target["height"] >= 44
        rows.setdefault(round(chips.nth(i).bounding_box()["y"]), []).append(target)
    assert len(rows) >= 2, "the chips must wrap for this test to mean anything"
    bands = [rows[y] for y in sorted(rows)]
    for above, below in itertools.pairwise(bands):
        lowest = max(b["y"] + b["height"] for b in above)
        highest = min(b["y"] for b in below)
        assert lowest <= highest + 0.5, f"targets overlap by {lowest - highest:.1f} px"
    for row in bands:  # and side by side, never
        for a, b in itertools.pairwise(row):
            assert a["x"] + a["width"] <= b["x"] + 0.5


def test_the_plus_adds_several_tags_creates_removes_and_stays_open(
    page: Page, server, console, demo_ids
):
    apollo13 = demo_ids["apollo13-problem"]
    open_library(page, server.url)
    open_row(page, "Apollo 13")
    page.get_by_test_id("recording-header").get_by_role(
        "button", name=re.compile(r"^Add tags to Apollo 13")
    ).click()
    box = page.get_by_test_id("picker-input")
    expect(box).to_be_focused()
    box.fill("lectures")
    box.press("Shift+Enter")  # creates exactly the typed text
    expect(box).to_have_value("")
    box.fill("apol")
    box.press("Enter")  # Enter on a partial name picks the existing tag, never Create
    expect(box).to_be_visible()  # the picker stays open for the next tag
    wait_until(lambda: server.tags(apollo13) == [("lectures", "you"), ("space/apollo", "you")])
    box.fill("apollo")
    item = page.locator("[data-testid=picker-item][data-tag='space/apollo']")
    expect(item).to_have_attribute("data-state", "all")
    expect(item).to_contain_text("on it")
    box.press("Enter")
    expect(page.get_by_test_id("picker-note")).to_have_text("Already on it")
    box.press("Shift+Delete")  # removes the highlighted tag
    wait_until(lambda: server.tags(apollo13) == [("lectures", "you")])
    box.fill("Lectures")
    expect(page.get_by_test_id("picker-create")).to_have_count(0)  # never a case-variant
    box.fill("notes/x")
    expect(page.get_by_test_id("picker-create")).to_have_count(0)  # never a notes/ tag
    box.press("Escape")
    expect(box).to_have_count(0)
    header = page.get_by_test_id("recording-header")
    expect(header.locator("[data-testid=chip]")).to_have_count(1)


def test_the_confirmation_opens_over_the_plus_picker_and_leaves_it_open(
    page: Page, server, console, demo_ids
):
    # why: a focus move into the Alert Dialog is "outside" the Popover, so Radix would close the
    # picker under the confirmation; and Esc must close only the topmost layer (Task 10 council).
    fdr = demo_ids["fdr-fireside-1"]
    open_library(page, server.url)
    open_row(page, "Fireside")
    header = page.get_by_test_id("recording-header")
    plus = header.get_by_role("button", name=re.compile(r"^Add tags to Fireside"))
    plus.click()
    picker = page.get_by_role("dialog", name=re.compile(r"^Tag Fireside"))
    box = page.get_by_test_id("picker-input")  # by test id: the modal confirmation hides the rest
    expect(box).to_be_focused()
    expect(picker.get_by_role("group", name="Private")).to_contain_text("private")  # by its flag
    item = picker.locator("[data-testid=picker-item][data-tag=private]")
    expect(item).to_have_attribute("data-state", "all")
    item.get_by_test_id("picker-x").click()  # the mouse's way to remove
    confirm = page.get_by_role("alertdialog")
    expect(confirm).to_contain_text("Remove private from 1 recording?")
    expect(confirm.get_by_role("button", name="Cancel")).to_be_focused()
    expect(box).to_be_visible()  # opened over the picker, which stays
    confirm.get_by_role("button", name="Cancel").click()
    expect(confirm).to_have_count(0)
    expect(box).to_be_focused()  # back on the picker's input
    assert server.tags(fdr) == [("private", "you")]
    box.fill("private")
    box.press("Shift+Delete")  # the keyboard's way asks too
    expect(confirm).to_be_visible()
    page.keyboard.press("Escape")  # closes only the confirmation, as Cancel
    expect(confirm).to_have_count(0)
    expect(box).to_be_focused()
    box.press("Shift+Delete")
    confirm.get_by_role("button", name="Remove").click()
    expect(confirm).to_have_count(0)
    expect(box).to_be_focused()
    wait_until(lambda: server.tags(fdr) == [])
    expect(item).to_have_count(0)  # no recording has it now: the server's recount drops it
    page.keyboard.press("Escape")  # now the picker
    expect(box).to_have_count(0)
    expect(plus).to_be_focused()  # Radix gives focus back to the +
    plus.click()
    expect(box).to_be_focused()
    header.locator("h1").click()  # an outside click still closes it
    expect(box).to_have_count(0)
