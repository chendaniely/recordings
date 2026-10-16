"""Browser tests that tag (tagging spec §9). Each gets a server of its own, so what one test
writes never shows in another; each reads the files the app wrote, through Archive."""

import itertools
import re
import time

import pytest
from playwright.sync_api import Error as PlaywrightError
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
    box.press("Enter")  # the box is clear and nothing was chosen: no tag nobody picked (ruled)
    expect(page.get_by_test_id("picker-note")).to_have_text("Type a tag name, or use the arrows")
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
    # No recording has it now, so the server's recount drops it; the picker keeps it, as none,
    # under its lock, so a second Shift+Delete can't move on to another tag.
    expect(item).to_have_attribute("data-state", "none")
    box.press("Shift+Delete")
    expect(confirm).to_have_count(0)
    page.keyboard.press("Escape")  # now the picker
    expect(box).to_have_count(0)
    expect(plus).to_be_focused()  # Radix gives focus back to the +
    plus.click()
    expect(box).to_be_focused()
    header.locator("h1").click()  # an outside click still closes it
    expect(box).to_have_count(0)


ACTIVE = """() => {
    const box = document.querySelector('[data-testid=picker-input]');
    const on = document.querySelector('[cmdk-item][aria-selected=true]');
    return [box.getAttribute('aria-activedescendant'), on ? on.id : null];
}"""


def test_the_box_names_the_highlighted_item_as_it_moves(page: Page, server, console, demo_ids):
    # why: cmdk 1.1.1 left aria-activedescendant on the item before (or none) until an arrow key,
    # so a screen reader announced the wrong tag after opening, typing or an add (Task 11 council).
    # And with an empty box nothing is chosen, so nothing is shown or announced until an arrow
    # (fix round 2): what is seen and heard matches what Enter does.
    apollo13 = demo_ids["apollo13-problem"]

    def follows() -> bool:
        active, selected = page.evaluate(ACTIVE)
        return selected is not None and active == selected

    def silent() -> bool:
        return page.evaluate(ACTIVE)[0] is None

    open_library(page, server.url)
    open_row(page, "Apollo 13")
    page.get_by_test_id("recording-header").get_by_role(
        "button", name=re.compile(r"^Add tags to Apollo 13")
    ).click()
    box = page.get_by_test_id("picker-input")
    expect(box).to_be_focused()
    root = page.locator("[cmdk-root]")
    expect(root).to_have_attribute("data-highlight", "off")  # empty, nothing chosen: none shown
    wait_until(silent, timeout=3)  # and none announced
    first = page.get_by_test_id("picker-item").first
    top = first.get_attribute("data-tag")
    box.press("ArrowDown")  # the first arrow shows the top item, without stepping past it
    expect(root).to_have_attribute("data-highlight", "on")
    expect(first).to_have_attribute("aria-selected", "true")
    wait_until(follows, timeout=3)
    box.press("Enter")  # adds it
    wait_until(lambda: server.tags(apollo13) == [(top, "you")])
    expect(root).to_have_attribute("data-highlight", "off")  # the add cleared the box
    wait_until(silent, timeout=3)
    box.fill("spa")
    expect(root).to_have_attribute("data-highlight", "on")  # typing shows it
    wait_until(follows, timeout=3)  # after typing
    box.press("Enter")
    expect(box).to_have_value("")
    wait_until(silent, timeout=3)  # after Enter cleared the box


def luminance(rgb: list[int]) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: list[int], b: list[int]) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


COLOURS = """() => {
    const rgb = (css) => {
        const c = document.createElement('canvas');
        c.width = c.height = 1;
        const x = c.getContext('2d');
        x.fillStyle = css;
        x.fillRect(0, 0, 1, 1);
        return [...x.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const item = document.querySelector('[data-testid=picker-item][aria-selected=true]');
    const s = getComputedStyle(item);
    const plus = getComputedStyle(document.querySelector(
        '[data-testid=recording-header] [data-testid=add-tag]'));
    const probe = document.body.appendChild(document.createElement('span'));
    probe.style.color = 'var(--muted-foreground)';
    const muted = getComputedStyle(probe).color;
    probe.remove();
    return {
        ring: rgb(s.outlineColor), ringStyle: s.outlineStyle, ringWidth: parseFloat(s.outlineWidth),
        item: rgb(s.backgroundColor),
        popover: rgb(getComputedStyle(item.closest('[data-slot=popover-content]')).backgroundColor),
        plusBorder: plus.borderTopColor, plusStyle: plus.borderTopStyle, muted,
    };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_highlighted_item_and_the_plus_stand_out_in_both_themes(
    page: Page, server, console, scheme
):
    # why: the highlight was a background change of 1.2:1 (light) and 1.07:1 (dark), and the +'s
    # dashed border used --border, not --muted-foreground (main spec §12.6a; Task 11 council).
    page.emulate_media(color_scheme=scheme)
    open_library(page, server.url)
    open_row(page, "JFK")
    page.get_by_test_id("recording-header").get_by_role(
        "button", name=re.compile(r"^Add tags to JFK")
    ).click()
    item = page.locator("[data-testid=picker-item][aria-selected=true]")
    expect(item).to_have_count(1)
    # An empty box with nothing chosen shows no highlight at all (fix round 2)...
    expect(item).to_have_css("outline-style", "none")
    expect(item).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    page.get_by_test_id("picker-input").press("ArrowDown")  # ...until an arrow shows it
    expect(item).to_have_css("outline-style", "solid")
    c = page.evaluate(COLOURS)
    assert c["ringStyle"] == "solid" and c["ringWidth"] >= 2
    assert contrast(c["ring"], c["popover"]) >= 3, c
    assert contrast(c["ring"], c["item"]) >= 3, c
    assert c["plusStyle"] == "dashed" and c["plusBorder"] == c["muted"], c


def selected(page: Page):
    return page.locator("[data-testid=recording-row][aria-selected=true]")


def test_arrows_select_as_they_move_and_enter_opens(page: Page, server, console):
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.first.focus()  # the list's one tab stop
    page.keyboard.press("ArrowDown")
    expect(rows.nth(1)).to_have_attribute("aria-selected", "true")
    expect(selected(page)).to_have_count(1)
    expect(rows.nth(1)).to_have_attribute(
        "aria-current", "true"
    )  # the pane followed, after a pause
    page.keyboard.press("Shift+ArrowDown")
    expect(selected(page)).to_have_count(2)
    expect(rows.nth(2)).to_be_focused()  # focus moves in a frame after the key: wait for it
    page.wait_for_timeout(600)  # past the pane's pause: Shift moves never open a row
    expect(rows.nth(1)).to_have_attribute("aria-current", "true")
    page.keyboard.press("Enter")  # so only Enter can open this one
    expect(rows.nth(2)).to_have_attribute("aria-current", "true")


def test_a_rows_plus_adds_several_tags_without_selecting_it(page: Page, server, console, demo_ids):
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Add tags to JFK")).click()
    expect(row).to_have_attribute("aria-selected", "false")  # + never selects or opens
    box = page.get_by_test_id("picker-input")
    box.fill("space")
    box.press("Enter")
    box.fill("first")
    box.press("Shift+Enter")
    wait_until(
        lambda: (
            server.tags(jfk)
            == [
                ("talks", "you"),
                ("notes/conference-talk", "auto"),
                ("space/apollo", "you"),
                ("first", "you"),
            ]
        )
    )
    box.press("Escape")
    expect(row.locator("[data-testid=chip]")).to_have_count(4)


def test_a_rows_x_removes_a_tag_from_that_row_only(page: Page, server, console, demo_ids):
    apollo11 = demo_ids["apollo11-first-steps"]
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="Apollo 11")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Remove tag space\/apollo from Apollo 11")).click()
    expect(row.locator("[data-testid=chip][data-tag='space/apollo']")).to_have_count(0)
    expect(row).to_have_attribute("aria-selected", "false")
    wait_until(lambda: server.tags(apollo11) == [("notes/general-fallback", "auto")])


def test_t_on_a_selection_esc_keeps_it_and_cmd_z_undoes_after_the_toast(
    page: Page, server, console, demo_ids
):
    apollo13, jfk = demo_ids["apollo13-problem"], demo_ids["jfk-rice"]
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Apollo 13").click()
    rows.filter(has_text="JFK").click(modifiers=["ControlOrMeta"])
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    page.keyboard.press("t")
    dialog = page.get_by_role("dialog", name="Tag 2 recordings")
    box = dialog.get_by_test_id("picker-input")
    box.fill("review")
    box.press("Shift+Enter")
    wait_until(
        lambda: ("review", "you") in server.tags(apollo13) and ("review", "you") in server.tags(jfk)
    )
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    # Focus went back to the row T was pressed on (useReturnFocus; Task 11 carry).
    expect(rows.filter(has_text="JFK")).to_be_focused()
    expect(bar).to_contain_text("2 selected")  # Esc closed only the picker
    expect(bar).to_contain_text("just tagged review")
    # the toast has gone
    expect(page.get_by_test_id("action-toast")).to_have_count(0, timeout=15_000)
    page.keyboard.press("ControlOrMeta+z")
    wait_until(
        lambda: (
            ("review", "you") not in server.tags(jfk)
            and ("review", "you") not in server.tags(apollo13)
        )
    )


def test_rows_stay_put_until_refresh(page: Page, server, console):
    open_library(page, server.url)
    page.get_by_test_id("filter-untagged").click()
    rows = page.get_by_test_id("recording-row")
    expect(rows).to_have_count(1)
    rows.first.hover()
    rows.first.get_by_role("button", name=re.compile(r"^Add tags to Apollo 13")).click()
    box = page.get_by_test_id("picker-input")
    box.fill("talks")
    box.press("Enter")
    expect(rows.first).to_contain_text("Tagged")
    box.press("Escape")
    expect(rows).to_have_count(1)  # no longer untagged, and still here, marked done
    expect(page.get_by_test_id("refresh")).to_contain_text("1 tagged")
    rows.first.focus()
    page.keyboard.press("r")
    expect(rows).to_have_count(0)


def test_a_long_list_is_drawn_virtually_and_focus_survives_a_wheel_scroll(
    page: Page, big_server, console
):
    page.goto(big_server.url)
    rows = page.get_by_test_id("recording-row")
    expect(rows.first).to_be_visible()
    assert rows.count() < 100  # only what is on screen, and a little more
    grid = page.get_by_test_id("recording-list")
    expect(grid).to_have_attribute("aria-rowcount", "301")
    rows.nth(2).click()
    focused = page.evaluate("document.activeElement.dataset.id")
    grid.hover()
    page.mouse.wheel(0, 9000)
    # Wait until the list has scrolled well past the focused row (about 170 px down), not for a
    # fixed time: only then does "kept" prove anything.
    wait_until(lambda: grid.evaluate("el => el.scrollTop") > 3000)
    # kept
    expect(page.locator(f"[data-testid=recording-row][data-id='{focused}']")).to_have_count(1)
    page.keyboard.press("ArrowDown")
    expect(selected(page)).to_have_count(1)
    expect(selected(page)).to_be_in_viewport()
    expect(selected(page)).to_have_attribute("aria-rowindex", "5")


def test_cmd_a_selects_rows_not_drawn_and_page_down_moves_a_screenful(
    page: Page, big_server, console
):
    page.goto(big_server.url)
    rows = page.get_by_test_id("recording-row")
    rows.first.click()
    page.keyboard.press("ControlOrMeta+a")
    expect(page.get_by_test_id("selection-bar")).to_contain_text("300 selected")
    expect(page.locator(".list-head [role=status]")).to_have_text("300 selected")  # and says so
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("selection-bar")).to_have_count(0)
    page.keyboard.press("PageDown")
    expect(selected(page)).to_have_count(1)
    assert int(selected(page).get_attribute("aria-rowindex")) > 4  # a screenful, not one row


def test_a_rows_plus_picker_stays_open_when_its_row_scrolls_away(page: Page, big_server, console):
    # why: the virtualiser unmounts a row scrolled out of view, and the + picker anchored in it
    # went with it. A click focuses the + in Chromium only, which keeps its row as the focused one;
    # Safari, Firefox and touch don't, so the open picker must keep its row itself (Task 11
    # council, T11-F1). A dispatched click opens it without focusing, as they do.
    page.goto(big_server.url)
    rows = page.get_by_test_id("recording-row")
    rows.first.click()  # focus and the anchor are on the first row
    row = rows.nth(3)
    rid = row.get_attribute("data-id")
    row.get_by_test_id("add-tag").dispatch_event("click")
    box = page.get_by_test_id("picker-input")
    expect(box).to_be_focused()
    grid = page.get_by_test_id("recording-list")
    grid.evaluate("el => { el.scrollTop = 6000; }")
    # The rows near the top are gone, so the list has been drawn again since the scroll...
    expect(page.locator("[data-testid=recording-row][aria-rowindex='12']")).to_have_count(0)
    # ...and the picker's row is still drawn, with the picker open on it, on screen.
    expect(page.locator(f"[data-testid=recording-row][data-id='{rid}']")).to_have_count(1)
    expect(box).to_be_focused()
    expect(box).to_be_in_viewport()  # not following its row off the top (fix round 1)
    box.press("Escape")
    expect(box).to_have_count(0)


def test_keys_typed_in_a_rows_picker_stay_in_the_picker(page: Page, server, console, demo_ids):
    # why: the + picker is a portal whose React events bubble through the row that opened it, so
    # `t` typed there could open the T dialog, and Cmd-Z undo a tag (Task 11 carry).
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Add tags to JFK")).click()
    box = page.get_by_test_id("picker-input")
    box.fill("first")
    box.press("Shift+Enter")
    wait_until(lambda: ("first", "you") in server.tags(jfk))
    box.press("t")
    expect(box).to_have_value("t")  # text in the box
    expect(page.get_by_role("dialog")).to_have_count(1)  # the popover only: no T dialog
    box.press("ControlOrMeta+z")  # the box's own undo, never the list's
    box.fill("second")
    box.press("Shift+Enter")
    # Requests go one at a time, in order: an Undo queued by Cmd-Z would have run first.
    wait_until(lambda: ("second", "you") in server.tags(jfk))
    assert ("first", "you") in server.tags(jfk)


LONG = "a-made-up-tag-long-enough-to-wrap/across-more-than-one-line-of-the-list"


def test_a_focused_long_tag_unwraps_and_the_rows_below_make_room(
    page: Page, server, console, demo_ids
):
    # why: a focused chip shows its whole tag, which can wrap onto lines a fixed-height virtual
    # row doesn't have, over the row below (Task 10 carry). Rows are measured as drawn.
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    plus = row.get_by_role("button", name=re.compile(r"^Add tags to JFK"))
    plus.click()
    box = page.get_by_test_id("picker-input")
    box.fill(LONG)
    box.press("Shift+Enter")
    wait_until(lambda: (LONG, "you") in server.tags(jfk))
    box.press("Escape")
    expect(plus).to_be_focused()
    chip = row.locator(f"[data-testid=chip][data-tag='{LONG}']")
    expect(chip).to_have_count(1)
    folded = chip.bounding_box()["height"]
    page.keyboard.press("ArrowLeft")  # from the + to the long tag's ×
    x = row.get_by_role(
        "button", name=re.compile(rf"^Remove tag {re.escape(LONG).replace('/', r'\/')} from JFK")
    )
    expect(x).to_be_focused()
    wait_until(lambda: chip.bounding_box()["height"] > folded)  # shown whole, on two lines
    index = int(row.get_attribute("aria-rowindex"))
    below = page.locator(f"[data-testid=recording-row][aria-rowindex='{index + 1}']")

    def clear() -> bool:
        a, b = row.bounding_box(), below.bounding_box()
        return a["y"] + a["height"] <= b["y"] + 0.5

    wait_until(clear)


@pytest.mark.browser_context_args(
    is_mobile=True, has_touch=True, viewport={"width": 390, "height": 800}
)
def test_rows_on_a_touch_screen_show_their_checkbox_and_no_x(page: Page, server, console):
    # spec §6: on devices that can't hover, the checkbox always shows and list rows show no ×
    # (tags are removed from the header or the picker); Left and Right skip the hidden ×s.
    open_library(page, server.url)
    assert page.evaluate("() => matchMedia('(hover: none)').matches")
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    expect(row.locator(".check")).to_be_visible()
    # The only way to add to a selection by touch: a 24 px target (fix round 1), inside its row.
    check, box = row.locator(".check").bounding_box(), row.bounding_box()
    assert check["width"] >= 24 and check["height"] >= 24, check
    assert box["y"] <= check["y"] and check["y"] + check["height"] <= box["y"] + box["height"]
    expect(row.locator(".chip-x")).to_have_count(2)
    for i in range(2):
        expect(row.locator(".chip-x").nth(i)).to_be_hidden()
    row.focus()
    page.keyboard.press("ArrowRight")
    expect(row.get_by_test_id("add-tag")).to_be_focused()


def test_refresh_hands_focus_on_when_the_focused_row_goes(page: Page, big_server, console):
    # why: Refresh drops a row just tagged from the Untagged filter; with focus on it, focus fell
    # to <body>, so the keys stopped working until a click or Tab (spec §6, keyboard triage).
    page.goto(big_server.url)
    page.get_by_test_id("filter-untagged").click()
    grid = page.get_by_test_id("recording-list")
    expect(grid).to_have_attribute("aria-rowcount", "201")
    rows = page.get_by_test_id("recording-row")
    first = rows.first
    gone = first.get_attribute("data-id")
    first.click()
    page.keyboard.press("t")
    box = page.get_by_role("dialog", name=re.compile(r"^Tag Synthetic")).get_by_test_id(
        "picker-input"
    )
    box.fill("topic/t01")
    box.press("Enter")
    expect(first).to_contain_text("Tagged")
    page.keyboard.press("Escape")
    expect(first).to_be_focused()
    page.keyboard.press("r")
    expect(grid).to_have_attribute("aria-rowcount", "200")
    expect(page.locator(f"[data-testid=recording-row][data-id='{gone}']")).to_have_count(0)
    wait_until(lambda: page.evaluate("document.activeElement?.dataset.testid") == "recording-row")
    page.keyboard.press("ArrowDown")
    expect(selected(page)).to_have_attribute("aria-rowindex", "3")
    expect(selected(page)).to_be_focused()


# Fix round 1 (Task 12 council). Each test says what was wrong.


def focused_row(page: Page):
    return page.locator("[data-testid=recording-row]:focus")


def tag_with_t(page: Page, name: re.Pattern | str, tag: str, done: str) -> None:
    """T on the focused row or the selection: add an existing tag, then Esc back to the list."""
    page.keyboard.press("t")
    box = page.get_by_role("dialog", name=name).get_by_test_id("picker-input")
    box.fill(tag)
    box.press("Enter")
    expect(page.get_by_test_id("refresh")).to_contain_text(done)
    page.keyboard.press("Escape")
    expect(box).to_have_count(0)


def test_refresh_keeps_the_focused_row_in_view_when_rows_above_it_go(
    page: Page, big_server, console
):
    # why: rows above the focused row left on Refresh, the row moved up, and the list stayed where
    # it was, with the focused row off-screen.
    page.goto(big_server.url)
    page.get_by_test_id("filter-untagged").click()
    grid = page.get_by_test_id("recording-list")
    expect(grid).to_have_attribute("aria-rowcount", "201")
    page.get_by_test_id("recording-row").first.click()
    for _ in range(20):
        page.keyboard.press("Shift+ArrowDown")
    expect(page.get_by_test_id("selection-bar")).to_contain_text("21 selected")
    page.keyboard.press("ControlOrMeta+ArrowDown")  # focus moves on, outside the selection
    expect(focused_row(page)).to_have_attribute("aria-rowindex", "23")
    kept = focused_row(page).get_attribute("data-id")
    tag_with_t(page, "Tag 21 recordings", "topic/t01", "21 tagged")
    row = page.locator(f"[data-testid=recording-row][data-id='{kept}']")
    expect(row).to_be_focused()
    page.keyboard.press("r")
    expect(grid).to_have_attribute("aria-rowcount", "180")
    expect(row).to_have_attribute("aria-rowindex", "2")
    expect(row).to_be_in_viewport()
    expect(row).to_be_focused()


def test_refresh_moves_focus_to_the_row_that_followed_a_tagged_one(page: Page, big_server, console):
    # why: when the focused row itself left on Refresh (T, tag, R), focus went back to the first
    # row and the list to the top, losing the user's place (ruled: the row that followed it).
    page.goto(big_server.url)
    page.get_by_test_id("filter-untagged").click()
    grid = page.get_by_test_id("recording-list")
    expect(grid).to_have_attribute("aria-rowcount", "201")
    page.get_by_test_id("recording-row").first.click()
    for _ in range(4):
        page.keyboard.press("PageDown")
    wait_until(lambda: int(focused_row(page).get_attribute("aria-rowindex")) > 20)
    index = int(focused_row(page).get_attribute("aria-rowindex"))
    then = page.locator(f"[data-testid=recording-row][aria-rowindex='{index + 1}']")
    after = then.get_attribute("data-id")
    tag_with_t(page, re.compile(r"^Tag Synthetic"), "topic/t01", "1 tagged")
    page.keyboard.press("r")
    expect(grid).to_have_attribute("aria-rowcount", "200")
    following = page.locator(f"[data-testid=recording-row][data-id='{after}']")
    expect(following).to_be_focused()
    expect(following).to_have_attribute("aria-selected", "false")  # focus only: nothing selected
    expect(following).to_be_in_viewport()
    assert grid.evaluate("el => el.scrollTop") > 0


def test_just_tagged_describes_the_selection_it_was_applied_to(
    page: Page, server, console, demo_ids
):
    # why: "just tagged" was set by every add, a row's + included, and outlived the selection.
    apollo11 = demo_ids["apollo11-first-steps"]
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Apollo 13").click()
    rows.filter(has_text="JFK").click(modifiers=["ControlOrMeta"])
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    other = rows.filter(has_text="Apollo 11")
    other.hover()
    other.get_by_role("button", name=re.compile(r"^Add tags to Apollo 11")).click()
    box = page.get_by_test_id("picker-input")
    box.fill("review")
    box.press("Shift+Enter")
    wait_until(lambda: ("review", "you") in server.tags(apollo11))
    box.press("Escape")
    expect(box).to_have_count(0)
    expect(bar).to_contain_text("2 selected")
    expect(bar).not_to_contain_text("just tagged")  # a row's own + tagged that row only
    tag_with_t(page, "Tag 2 recordings", "review", "3 tagged")
    expect(bar).to_contain_text("just tagged review")
    rows.filter(has_text="Fireside").click(modifiers=["ControlOrMeta"])
    expect(bar).to_contain_text("3 selected")
    expect(bar).not_to_contain_text("just tagged")  # another selection


def test_a_filter_change_shows_the_first_row_and_leaves_focus_in_the_sidebar(
    page: Page, big_server, console
):
    # why: the list stayed scrolled where the old filter was, so the new first row (the tab stop)
    # was off-screen.
    page.goto(big_server.url)
    grid = page.get_by_test_id("recording-list")
    expect(grid).to_have_attribute("aria-rowcount", "301")
    grid.evaluate("el => { el.scrollTop = 6000; }")
    wait_until(lambda: grid.evaluate("el => el.scrollTop") > 3000)
    untagged = page.get_by_test_id("filter-untagged")
    untagged.click()
    expect(grid).to_have_attribute("aria-rowcount", "201")
    first = page.locator("[data-testid=recording-row][aria-rowindex='2']")
    expect(first).to_be_in_viewport()
    expect(first).to_have_attribute("tabindex", "0")  # the tab stop
    expect(untagged).to_be_focused()  # real focus stays in the sidebar


ROW_COLOURS = """() => {
    const c = document.createElement('canvas');
    c.width = c.height = 1;
    const x = c.getContext('2d');
    const paint = (...layers) => {
        x.clearRect(0, 0, 1, 1);
        for (const l of layers) { x.fillStyle = l; x.fillRect(0, 0, 1, 1); }
        return [...x.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const vrows = document.querySelector('.vrows');
    const list = getComputedStyle(document.querySelector('.list')).backgroundColor;
    const states = {
        plain: '', selected: 'sel', done: 'done', 'selected+done': 'sel done',
        'selected+failed': 'sel failed',
    };
    const out = {};
    for (const [name, cls] of Object.entries(states)) {
        const row = document.createElement('div');
        row.className = ('row ' + cls).trim();
        row.innerHTML = '<div class="row-cell"><span class="check"></span><div class="row-body">'
            + '<div class="m">meta</div><span class="done-label">Tagged</span>'
            + '<span class="failed-label"><svg></svg><span>reason</span></span></div></div>';
        vrows.appendChild(row);
        const of = (sel) => paint(getComputedStyle(row.querySelector(sel)).color);
        out[name] = {
            bg: paint('#fff', list, getComputedStyle(row).backgroundColor),
            meta: of('.m'), done: of('.done-label'), reason: of('.failed-label span'),
            glyph: of('.failed-label svg'),
        };
        row.remove();
    }
    return out;
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_list_rows_text_stays_readable_in_every_state(page: Page, server, console, scheme):
    # why: a selected row's failure reason, in --destructive on the selection's tint, was 4.44:1 in
    # the dark theme; and the brief's selected-and-done tint put muted text at 4.18:1 in the light.
    page.emulate_media(color_scheme=scheme)
    open_library(page, server.url)
    page.mouse.move(0, 0)  # no row hovered
    states = page.evaluate(ROW_COLOURS)
    for state, c in states.items():
        for text in ("meta", "done", "reason"):
            assert contrast(c[text], c["bg"]) >= 4.5, (scheme, state, text, c)
        assert contrast(c["glyph"], c["bg"]) >= 3, (scheme, state, "the ⚠", c)


def test_clear_in_the_selection_bar_gives_focus_back_to_the_list(page: Page, server, console):
    # why: Clear unmounted the bar under its own focused button, so focus fell to <body> and the
    # list's keys stopped until Tab (WCAG 2.4.3). Esc inside the bar does what its "Clear Esc" says.
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Apollo 13").click()
    jfk = rows.filter(has_text="JFK")
    jfk.click(modifiers=["ControlOrMeta"])
    bar = page.get_by_test_id("selection-bar")
    bar.get_by_role("button", name=re.compile(r"^Clear")).click()
    expect(bar).to_have_count(0)
    expect(jfk).to_be_focused()
    after = int(jfk.get_attribute("aria-rowindex")) + 1
    page.keyboard.press("Shift+ArrowDown")  # the list's keys work at once
    expect(bar).to_contain_text("2 selected")
    # Focus reaches the next row a frame after the key: wait for it, not for JFK still focused.
    expect(focused_row(page)).to_have_attribute("aria-rowindex", str(after))
    back = focused_row(page).get_attribute("data-id")
    bar.get_by_role("button", name=re.compile(r"^Clear")).focus()
    page.keyboard.press("Escape")
    expect(bar).to_have_count(0)
    expect(page.locator(f"[data-testid=recording-row][data-id='{back}']")).to_be_focused()


def test_a_cmd_arrow_cancels_the_pane_following_a_plain_one(page: Page, server, console):
    # why: a plain arrow's 300 ms follow outlived a Cmd/Ctrl+arrow pressed straight after it, and
    # opened the row focus had moved to, outside the selection (Cmd/Ctrl moves focus only).
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.first.click()
    expect(rows.first).to_have_attribute("aria-current", "true")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ControlOrMeta+ArrowDown")
    expect(rows.nth(2)).to_be_focused()
    page.wait_for_timeout(600)  # past the pane's pause
    expect(rows.nth(2)).not_to_have_attribute("aria-current", "true")
    expect(rows.first).to_have_attribute("aria-current", "true")


def test_space_on_a_row_doesnt_scroll_the_list(page: Page, big_server, console):
    # why: Space is the player's (main spec §12); with no player in the list, it scrolled the list
    # a page instead.
    page.goto(big_server.url)
    rows = page.get_by_test_id("recording-row")
    rows.first.click()
    grid = page.get_by_test_id("recording-list")
    page.keyboard.press("Space")
    page.wait_for_timeout(400)
    assert grid.evaluate("el => el.scrollTop") == 0
    expect(rows.first).to_be_focused()


TWO_FRAMES = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"


def test_a_filter_click_that_takes_no_focus_leaves_focus_alone(
    page: Page, server, console, demo_ids
):
    # why: Safari and Firefox on macOS don't focus a button on click: the row blurs with no
    # relatedTarget, as a removed row does, so a filter change pulled focus into the list. A
    # pointerdown outside the list now says focus left it. Simulated here: pointerdown, blur, click.
    jfk = demo_ids["jfk-rice"]
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    row.click()
    expect(row).to_be_focused()
    apollo = page.get_by_test_id("filter-tag").filter(has_text="space/apollo")
    apollo.dispatch_event("pointerdown")
    page.evaluate("() => document.activeElement.blur()")
    apollo.dispatch_event("click")
    expect(page.locator(f"[data-testid=recording-row][data-id='{jfk}']")).to_have_count(0)
    page.evaluate(TWO_FRAMES)
    page.evaluate(TWO_FRAMES)
    assert page.evaluate("() => document.activeElement === document.body")


def test_the_selection_bar_never_covers_the_focused_row(page: Page, big_server, console):
    # why: the bar mounts 43 px tall after the focused row was scrolled into view, over its bottom.
    page.goto(big_server.url)
    grid = page.get_by_test_id("recording-list")
    page.get_by_test_id("recording-row").first.click()
    for i in range(40):  # down to the bottom of the list's view: then it starts to scroll
        page.keyboard.press("ArrowDown")
        expect(focused_row(page)).to_have_attribute("aria-rowindex", str(i + 3))
        if grid.evaluate("el => el.scrollTop") > 0:
            break
    page.keyboard.press("Shift+ArrowDown")
    expect(page.get_by_test_id("selection-bar")).to_contain_text("2 selected")
    expect(focused_row(page)).to_have_attribute("aria-rowindex", str(i + 4))
    expect(focused_row(page)).to_be_in_viewport(ratio=1)


def test_a_rows_name_is_its_title_meta_and_tags_not_its_controls(page: Page, server, console):
    # why: a row was named by all its text, every × and + name with the title again included,
    # about 330 characters read out on every arrow.
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    expect(row).to_have_accessible_name(re.compile(r"^JFK.*1962.*talks notes/conference-talk"))
    expect(row).not_to_have_accessible_name(re.compile(r"Remove tag|Add tags"))


def test_a_key_on_a_focused_row_scrolled_away_brings_it_back(page: Page, big_server, console):
    # why: X toggled the focused row while it was scrolled out of view, with nothing to show it.
    page.goto(big_server.url)
    rows = page.get_by_test_id("recording-row")
    rows.nth(2).click()
    rid = rows.nth(2).get_attribute("data-id")
    row = page.locator(f"[data-testid=recording-row][data-id='{rid}']")
    grid = page.get_by_test_id("recording-list")
    for key, check in (("x", "false"), ("x", "true")):
        grid.hover()
        page.mouse.wheel(0, 9000)
        wait_until(lambda: grid.evaluate("el => el.scrollTop") > 3000)
        page.keyboard.press(key)
        expect(row).to_have_attribute("aria-selected", check)
        expect(row).to_be_in_viewport()
    grid.evaluate("el => { el.scrollTop = 9000; }")
    wait_until(lambda: grid.evaluate("el => el.scrollTop") > 3000)
    page.keyboard.press("Enter")
    expect(row).to_be_in_viewport()


# Fix round 2 (Task 12 re-review): "just tagged" keeps naming what this selection was tagged with.


def select_two_and_tag(page: Page, server, demo_ids) -> None:
    """Apollo 13 and JFK selected, then T adds a new tag, review, to both."""
    apollo13, jfk = demo_ids["apollo13-problem"], demo_ids["jfk-rice"]
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Apollo 13").click()
    rows.filter(has_text="JFK").click(modifiers=["ControlOrMeta"])
    page.keyboard.press("t")
    dialog = page.get_by_role("dialog", name="Tag 2 recordings")
    box = dialog.get_by_test_id("picker-input")
    box.fill("review")
    box.press("Shift+Enter")
    wait_until(
        lambda: ("review", "you") in server.tags(apollo13) and ("review", "you") in server.tags(jfk)
    )
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(page.get_by_test_id("selection-bar")).to_contain_text("just tagged review")


def test_just_tagged_stays_when_a_row_inside_the_selection_is_tagged_with_its_plus(
    page: Page, server, console, demo_ids
):
    # why: a row's + overwrote the last add, so the selection's note went though the selection
    # hadn't changed. A +'s one row never matches a selection of two or more.
    jfk = demo_ids["jfk-rice"]
    select_two_and_tag(page, server, demo_ids)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Add tags to JFK")).click()
    box = page.get_by_test_id("picker-input")
    box.fill("space")
    box.press("Enter")
    wait_until(lambda: ("space/apollo", "you") in server.tags(jfk))
    box.press("Escape")
    expect(box).to_have_count(0)
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    expect(bar).to_contain_text("just tagged review")


def test_just_tagged_stays_through_a_refresh_that_keeps_the_selection(
    page: Page, server, console, demo_ids
):
    # why: a Refresh builds a new selection Set even when every row stays, and the note compared
    # Sets by identity.
    select_two_and_tag(page, server, demo_ids)
    refresh = page.get_by_test_id("refresh")
    expect(refresh).to_contain_text("2 tagged")
    page.keyboard.press("r")  # focus is back on the row T was pressed on
    expect(refresh).to_have_text("Refresh")  # the new library has come: the marks are gone
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    expect(bar).to_contain_text("just tagged review")


def test_just_tagged_goes_when_an_undo_reverses_it(page: Page, server, console, demo_ids):
    # why (new ruling): after Cmd-Z undoes the add the bar names, the note would be false.
    apollo13, jfk = demo_ids["apollo13-problem"], demo_ids["jfk-rice"]
    select_two_and_tag(page, server, demo_ids)
    page.keyboard.press("ControlOrMeta+z")
    wait_until(
        lambda: (
            ("review", "you") not in server.tags(jfk)
            and ("review", "you") not in server.tags(apollo13)
        )
    )
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    expect(bar).not_to_contain_text("just tagged")


def test_a_rows_name_says_a_chip_is_pending(page: Page, server, console):
    # why: a pending chip's " (removing)" sat outside the labelled chip label, so the row's name
    # no longer said the chip was pending. The server's tag reply is held back to keep it pending.
    held: list = []
    socket: dict = {}

    def route(ws):
        socket["page"] = ws
        to_server = ws.connect_to_server()

        def from_server(message):
            if socket.get("hold", True) and "tag_result" in str(message):
                held.append(message)
            else:
                ws.send(message)

        to_server.on_message(from_server)

    page.route_web_socket(re.compile(r"/websocket/$"), route)
    open_library(page, server.url)
    row = page.get_by_test_id("recording-row").filter(has_text="JFK")
    chip = row.locator("[data-testid=chip][data-tag=talks]")
    row.hover()
    row.get_by_role("button", name=re.compile(r"^Remove tag talks from JFK")).click()
    expect(chip).to_have_class(re.compile(r"\bpending\b"))
    expect(row).to_have_accessible_name(re.compile(r"talks \(removing\)"))
    for _ in range(200):  # Playwright runs the route only while it is called: not in time.sleep
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the server's reply never came"
    socket["hold"] = False
    for message in held:  # the reply, at last
        socket["page"].send(message)
    expect(chip).to_have_count(0)
    expect(row).not_to_have_accessible_name(re.compile(r"removing"))


# Task 13: drag and drop onto the sidebar (spec §6, §9).

FOREIGN_DROP = """([id]) => {
    // A drop that didn't start in this page: text naming a recording, dropped on `private`.
    const target = [...document.querySelectorAll('[data-testid=filter-tag]')]
        .find((el) => el.textContent.includes('private'));
    const data = new DataTransfer();
    data.setData('text/plain', id);
    for (const type of ['dragenter', 'dragover', 'drop']) {
        const init = { bubbles: true, cancelable: true, dataTransfer: data };
        target.dispatchEvent(new DragEvent(type, init));
    }
}"""


def drag_by_title(row, target) -> None:
    """Drag a row by its title. drag_to presses at some visible point of the row unless told
    where, and a press on a chip's x, the + or the checkbox cancels the drag (onDragStart): the
    title is never one."""
    row_box = row.bounding_box()
    title = row.locator(".row-title").bounding_box()
    at = {"x": title["x"] - row_box["x"] + 4, "y": title["y"] - row_box["y"] + title["height"] / 2}
    row.drag_to(target, source_position=at)


def test_dragging_a_selection_onto_a_sidebar_tag_and_a_foreign_drop(
    page: Page, server, console, demo_ids
):
    apollo13, jfk = demo_ids["apollo13-problem"], demo_ids["jfk-rice"]
    frames: list[str] = []  # what the page sends on Shiny's websocket, from the first frame
    sockets: list = []

    def watch(ws) -> None:
        sockets.append(ws)
        ws.on("framesent", lambda payload: frames.append(str(payload)))

    page.on("websocket", watch)
    open_library(page, server.url)
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Apollo 13").click()
    rows.filter(has_text="JFK").click(modifiers=["ControlOrMeta"])
    target = page.get_by_test_id("filter-tag").filter(has_text="space/apollo")
    drag_by_title(rows.filter(has_text="JFK"), target)
    expect(page.get_by_test_id("action-toast")).to_contain_text(
        "Added space/apollo to 2 recordings"
    )
    wait_until(
        lambda: (
            ("space/apollo", "you") in server.tags(apollo13)
            and ("space/apollo", "you") in server.tags(jfk)
        )
    )
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("just tagged space/apollo")  # one add, of the whole selection
    drag_by_title(rows.filter(has_text="JFK"), target)  # nothing changes, and it says so
    expect(page.get_by_test_id("action-toast").filter(has_text="Already on all 2")).to_be_visible()

    def sent(name: str) -> int:
        return sum(f'"{name}' in f for f in frames)

    requests_before = sent("tag_request")
    page.evaluate(FOREIGN_DROP, [jfk])  # a drop event that comes anyway does nothing
    # A drop from outside the page, of text or a file, is refused at dragover, so the browser
    # never drops it here (Playwright's locator.drop throws when dragover isn't cancelled).
    hidden = page.get_by_test_id("filter-tag").filter(has_text="private")
    file = {"name": "ids.txt", "mimeType": "text/plain", "buffer": jfk.encode()}
    for payload in ({"data": {"text/plain": jfk}}, {"files": [file]}):
        with pytest.raises(PlaywrightError, match="did not accept the drop"):
            hidden.drop(payload)
    expect(hidden).not_to_have_class(re.compile(r"\bdrop\b"))
    # A positive sync point instead of a sleep: Refresh's input goes out after anything the drops
    # could have sent (Shiny sends inputs in order), so once it is heard, the count is final.
    # Playwright hears frames only while it is called (never in time.sleep): it waits here.
    (shiny,) = [ws for ws in sockets if ws.url.endswith("/websocket/")]
    with shiny.expect_event("framesent", lambda payload: '"refresh' in str(payload)):
        page.get_by_test_id("refresh").click()
    assert sent("tag_request") == requests_before  # the foreign drops sent no request
    assert ("private", "you") not in server.tags(jfk)
    # outside the selection
    drag_by_title(rows.filter(has_text="Apollo 11"), page.get_by_test_id("new-tag"))
    expect(page.get_by_role("dialog", name=re.compile(r"^Tag Apollo 11"))).to_be_visible()
    page.keyboard.press("Escape")
    expect(bar).to_contain_text("2 selected")  # left alone
    new_tag = page.get_by_test_id("new-tag")
    new_tag.click()  # a click: the picker for the selection, as T
    expect(page.get_by_role("dialog", name="Tag 2 recordings")).to_be_visible()
    page.keyboard.press("Escape")
    expect(new_tag).to_be_focused()  # back where it came from
