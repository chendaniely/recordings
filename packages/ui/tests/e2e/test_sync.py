"""The Sync screen and Status's sync panel in demo mode (2b spec §15.4, §15.5, §21; phase 1 plan,
Task 24): the canned view at 1,280 and 320 pixels, every request button disabled with the demo's
note, 44-pixel targets on a phone, axe in both themes, and a clean console. The top bar at 320
pixels is pinned by test_tagging.py. Run with `make e2e`; each test gets its own demo server
(conftest's `server`)."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from recordings_ui.plaud import DEMO_NOTE, DEMO_STATUS

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
# Every button, disclosure and checkbox target in `scope` under 44 px tall, by its text. Inline
# links and the equation's numbers (.sync-jump, buttons inside a sentence) are WCAG 2.5.8's inline
# exception, and aren't counted.
SMALL_TARGETS = """(scope) => [...document.querySelectorAll(
        `${scope} button:not(.sync-jump), ${scope} summary, ${scope} .sync-check`)]
    .map((el) => [el, el.getBoundingClientRect()])
    .filter(([, r]) => r.width > 0 && r.height > 0 && r.height < 43.5)
    .map(([el, r]) => `${(el.textContent || el.className).trim()}: ${Math.round(r.height)} px`)"""
SIDEWAYS = """(sel) => {
    const el = document.querySelector(sel);
    return [el.scrollWidth - el.clientWidth,
            document.documentElement.scrollWidth - document.documentElement.clientWidth];
}"""
# Outputs whose Shiny binding points at an element no longer in the page (test_library.py).
DETACHED = """() => Object.entries(window.Shiny.shinyapp.$bindings)
    .filter(([, binding]) => binding.el && !binding.el.isConnected).map(([id]) => id)"""
TWO_FRAMES = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"


def axe(page: Page, selector: str) -> list[str]:
    """axe-core's WCAG A and AA violations inside `selector`, as in test_tagging.py."""
    if not page.evaluate("() => typeof window.axe === 'object'"):
        page.add_script_tag(path=str(AXE))
    return page.evaluate(
        """async ([selector, tags]) => {
            const el = document.querySelector(selector);
            if (!el) throw new Error(`nothing matches ${selector}`);
            const result = await axe.run(el, { runOnly: { type: "tag", values: tags } });
            return result.violations.map((v) => `${v.id} (${v.nodes.length}): `
                + v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | "));
        }""",
        [selector, WCAG],
    )


def open_sync(page: Page, url: str) -> None:
    page.goto(f"{url}/#/add")
    expect(page.get_by_test_id("sync-page")).to_be_visible()
    expect(page.get_by_test_id("sync-summary")).to_be_visible()


def open_more(page: Page) -> None:
    page.locator("details.sync-more > summary").click()
    expect(page.locator("[data-request=full]")).to_be_visible()


def open_panel(page: Page, url: str) -> None:
    page.goto(f"{url}/#/status")
    expect(page.get_by_test_id("sync-panel")).to_be_visible()
    page.get_by_test_id("sync-details").locator("summary").click()
    expect(page.get_by_test_id("sync-details")).to_have_attribute("open", "")


@pytest.mark.parametrize("width", [1280, 320])
def test_the_sync_screen_and_status_panel_draw_the_canned_view_at_1280_and_320(
    page: Page, server, console, width
):
    # C9.01.
    page.set_viewport_size({"width": width, "height": 800})
    open_sync(page, server.url)
    summary = page.get_by_test_id("sync-summary")
    expect(summary).to_contain_text(DEMO_NOTE)
    expect(page.get_by_test_id("not-here-row")).to_have_count(3)
    expect(page.get_by_test_id("kept-out-row")).to_have_count(2)
    expect(page.get_by_test_id("updated-row")).to_have_count(1)
    expect(page.get_by_test_id("sync-equations")).to_contain_text(
        "Listed 9 = 4 here + 2 waiting + 2 excluded + 1 missing"
    )
    expect(page.get_by_test_id("sync-not-here")).to_contain_text("Synthetic 042")
    expect(page.get_by_test_id("sync-kept-out")).not_to_contain_text("Synthetic")
    # The summary comes first (§15.4), and nothing scrolls sideways.
    top = summary.bounding_box()["y"]
    assert top < page.get_by_test_id("sync-actions").bounding_box()["y"]
    assert page.evaluate(SIDEWAYS, "[data-testid=sync-page]") == [0, 0]
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="Status").click()
    panel = page.get_by_test_id("sync-panel")
    expect(panel).to_contain_text(DEMO_STATUS)
    expect(panel.get_by_test_id("sync-numbers")).to_contain_text("14:20 (9 listed)")
    expect(panel.get_by_test_id("sync-details")).not_to_have_attribute("open", "")
    assert page.evaluate(SIDEWAYS, "[data-testid=status-page]") == [0, 0]


def test_every_sync_button_is_disabled_with_the_demo_note(page: Page, server, console):
    open_sync(page, server.url)
    open_more(page)
    asks = page.locator("[data-request]")
    assert asks.count() >= 5  # Sync now, Compare, Sync everything, Import selected, Import all
    for i in range(asks.count()):
        expect(asks.nth(i)).to_be_disabled()
    expect(page.locator(".sync-why", has_text=DEMO_NOTE).first).to_be_visible()
    expect(page.get_by_test_id("copy-id").first).to_be_enabled()  # copying sends nothing
    page.goto(f"{server.url}/#/status")
    expect(page.get_by_test_id("sync-panel")).to_be_visible()
    expect(page.get_by_test_id("sync-panel").locator("[data-request]:enabled")).to_have_count(0)


def test_targets_are_at_least_44_pixels_at_320(page: Page, server, console):
    page.set_viewport_size({"width": 320, "height": 800})
    open_sync(page, server.url)
    open_more(page)
    assert page.evaluate(SMALL_TARGETS, ".sync-page") == []
    full = page.locator("[data-request=compare]").bounding_box()
    card = page.get_by_test_id("sync-actions").bounding_box()
    assert full["width"] > card["width"] - 40  # a full-width button, inside the card's padding
    # The canned panel has no request buttons; what this checks is its open Details summary.
    open_panel(page, server.url)
    assert page.evaluate(SMALL_TARGETS, ".sync-panel") == []


def test_a_row_stacks_its_date_and_title_at_320(page: Page, server, console):
    # §15.4: the box and the date with its duration on one line, the title below on a line of its
    # own. Plain wrapping already puts a long title under the date, but a short one ("Synthetic
    # 043") would sit beside it: only the title's full-width basis puts every title below, so each
    # title spans the row, and the date is one line high.
    page.set_viewport_size({"width": 320, "height": 800})
    open_sync(page, server.url)
    rows = page.get_by_test_id("not-here-row")
    for i in range(rows.count()):
        row = rows.nth(i)
        box = row.bounding_box()
        check = row.locator(".sync-check").bounding_box()
        when = row.locator(".sync-when").bounding_box()
        title = row.locator(".sync-title").bounding_box()
        line = row.locator(".sync-when").evaluate(
            "(el) => parseFloat(getComputedStyle(el).lineHeight)"
        )
        assert when["height"] < line * 1.5, (i, "the date wraps", when, line)
        assert abs(check["y"] - when["y"]) < check["height"], (i, "the box is beside the date")
        assert title["y"] >= when["y"] + when["height"] - 1, (i, when, title)
        assert title["width"] >= box["width"] - 1, (i, "the title has its own line", title, box)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_finds_nothing_in_light_and_dark(page: Page, server, console, theme):
    open_sync(page, server.url)
    page.get_by_test_id(f"theme-{theme}").click()
    dark = re.compile(r"\bdark\b")
    if theme == "dark":
        expect(page.locator("html")).to_have_class(dark)
    else:
        expect(page.locator("html")).not_to_have_class(dark)
    open_more(page)
    assert axe(page, ".sync-page") == []
    open_panel(page, server.url)
    assert axe(page, ".sync-panel") == []


def test_the_console_stays_clean(page: Page, server, console):
    # The `console` fixture fails the test on any console error. Leaving the Sync screen leaves no
    # output bound to a removed element (CLAUDE.md: every output is subscribed in App).
    open_sync(page, server.url)
    open_more(page)
    nav = page.get_by_role("navigation", name="Pages")
    nav.get_by_role("button", name="Status").click()
    expect(page.get_by_test_id("sync-panel")).to_be_visible()
    page.get_by_test_id("sync-details").locator("summary").click()
    nav.get_by_role("button", name="Library").click()
    expect(page.get_by_test_id("recording-row")).to_have_count(4)
    nav.get_by_role("button", name="Add").click()
    expect(page.get_by_test_id("sync-page")).to_be_visible()
    page.evaluate(TWO_FRAMES)
    assert page.evaluate(DETACHED) == []
