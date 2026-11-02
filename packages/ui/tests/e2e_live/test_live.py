"""The Sync screen and Status against the real web app and worker, on the fake Plaud server (stage
2b spec §21's "e2e, live"; area A6): `scripts/demo_live.py`, as `make demo-live` runs it, after
its first catch-up. In order, sharing one demo (its start paces one call a second): Compare lists
what waits; Kept out and the flags show no title; Import selected imports with its toast; the
arrivals cue after a scheduled tick; Release's question, Let it in and Undo; a breaker trip that
offers Accept these changes, which clears it (area B1); Re-sync on a selection and a recording
that fails every check parked from Status (area B2); a hold that turns Sync now and the imports
off, cleared by Accept this listing; a refused sign-in on Status; the worker killed; then Ctrl-C
stops everything, with no stray process. Run with `make e2e-live`."""

from __future__ import annotations

import os
import signal
import time

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_live

# The demo's synthetic account (scripts/demo_live.py).
CONSENTED = "Synthetic guest talk (on the consent list)"
KEPT = "noarchive synthetic doctor's appointment"
RENAMED = "Synthetic doctor's appointment"
ARRIVING = ("Synthetic seminar, today", "Synthetic standup, today", "Private: synthetic catch-up")
RUN = 90_000  # ms: a request that imports paces one call a second


def open_sync(page: Page, demo) -> None:
    page.goto(f"{demo.url}#/add")
    expect(page.get_by_test_id("sync-page")).to_be_visible()
    expect(page.get_by_test_id("sync-summary")).to_be_visible()


def press(page: Page, kind: str) -> None:
    button = page.locator(f"#sync-actions [data-request={kind}]")
    expect(button).to_be_enabled()
    button.click()


def last_result(page: Page):
    return page.get_by_test_id("sync-last-result")


def test_compare_lists_what_waits_and_kept_out_shows_no_title(page: Page, demo, console):
    open_sync(page, demo)
    expect(page.get_by_test_id("sync-summary")).to_contain_text("All here · checked ")
    press(page, "compare")
    expect(last_result(page)).to_contain_text("Compared: listed 12 = here 7", timeout=RUN)
    rows = page.get_by_test_id("not-here-row")
    expect(rows).to_have_count(3)
    for title in ARRIVING:
        expect(page.get_by_test_id("sync-not-here")).to_contain_text(title)
    expect(rows.filter(has_text=ARRIVING[2])).to_contain_text("arrives private")
    kept = page.get_by_test_id("kept-out-row")
    expect(kept).to_have_count(2)
    expect(kept.filter(has_text="On the consent list")).to_have_count(1)
    expect(kept.filter(has_text="Kept out by title")).to_have_count(1)
    page_text = page.get_by_test_id("sync-page").inner_text()
    assert CONSENTED not in page_text and KEPT not in page_text
    expect(page.get_by_test_id("sync-equations")).to_contain_text("Consent list: 1 ID, 1 on Plaud")


def test_import_selected_imports_with_progress_and_a_toast(page: Page, demo, console):
    open_sync(page, demo)
    row = page.get_by_test_id("not-here-row").filter(has_text=ARRIVING[0])
    row.locator("input[type=checkbox]").check()
    page.get_by_test_id("import-selected").click()
    expect(page.get_by_text("1 imported", exact=False).first).to_be_visible(timeout=RUN)
    expect(page.get_by_test_id("not-here-row")).to_have_count(2, timeout=RUN)
    expect(last_result(page)).to_contain_text("Import selected")


def test_the_arrivals_cue_appears_after_a_scheduled_tick(page: Page, demo, console):
    page.goto(f"{demo.url}#/library")
    refresh = page.get_by_test_id("refresh")
    expect(refresh).to_be_visible()
    demo.send("tick")
    # The cue reads every 60 seconds (§15.3); the scheduled sync imports the two still waiting,
    # one of them private (the cue counts new and private apart).
    expect(refresh).to_contain_text("1 new · 1 private from Plaud", timeout=150_000)


def test_release_asks_with_the_title_lets_it_in_and_undo_works_until_the_next_sync(
    page: Page, demo, console
):
    demo.send("rename")
    open_sync(page, demo)
    press(page, "compare")
    expect(last_result(page)).to_contain_text("Compared:", timeout=RUN)
    row = page.get_by_test_id("kept-out-row").filter(has_text="Was kept out by title")
    expect(row).to_have_count(1, timeout=RUN)
    assert RENAMED not in row.inner_text()
    row.get_by_role("button", name="Release").click()
    dialog = page.get_by_test_id("release-dialog")
    expect(dialog).to_contain_text(RENAMED, timeout=RUN)
    dialog.get_by_role("button", name="Let it in").click()
    expect(row).to_contain_text("Released · comes in at the next sync", timeout=RUN)
    row.get_by_role("button", name="Undo").click()
    expect(row.get_by_role("button", name="Release")).to_be_visible(timeout=RUN)
    # Released again, then Sync now brings it in, and it leaves Kept out.
    row.get_by_role("button", name="Release").click()
    expect(dialog).to_contain_text(RENAMED, timeout=RUN)
    dialog.get_by_role("button", name="Let it in").click()
    expect(row).to_contain_text("Released · comes in at the next sync", timeout=RUN)
    press(page, "sync")
    expect(last_result(page)).to_contain_text("Synced: 1 imported", timeout=RUN)
    expect(page.get_by_test_id("kept-out-row")).to_have_count(1, timeout=RUN)
    assert RENAMED not in page.get_by_test_id("sync-kept-out").inner_text()


def test_a_breaker_trip_offers_accept_these_changes_which_clears_it(page: Page, demo, console):
    # §14.4, decision 11: Plaud sends a new field, the same on every fetch, on every recording;
    # the next sync's checks trip the breaker; Status names the field (never its value) and offers
    # Accept these changes, which closes it.
    assert demo.send("breaker").startswith("ok: Plaud sends a new field")
    demo.send("tick")
    deadline = time.monotonic() + 150
    while True:
        page.goto(f"{demo.url}#/status")
        expect(page.get_by_test_id("sync-panel")).to_be_visible()
        breaker = page.get_by_test_id("sync-breaker")
        if breaker.count():
            break
        assert time.monotonic() < deadline, "the breaker never tripped"
        time.sleep(3)
    expect(breaker).to_contain_text("Plaud started sending something new on")
    expect(breaker).to_contain_text("synthetic_feature")
    assert "on-" not in breaker.inner_text()  # the field's value, never shown
    expect(page.get_by_test_id("status-attention")).to_contain_text("Edit-sync is paused")
    breaker.get_by_role("button", name="Accept these changes").click()
    expect(page.get_by_test_id("sync-breaker")).to_have_count(0, timeout=RUN)


def test_resync_on_a_selection_updates_its_rows(page: Page, demo, console):
    # §14.6, decision 6, C9.05: two recordings get a speaker named in Plaud; selected in the
    # Library, one press of Re-sync from Plaud checks both now, with progress and its end line.
    assert demo.send("speakers").startswith("ok: 2 recordings here have a speaker named")
    page.goto(f"{demo.url}#/library")
    rows = page.get_by_test_id("recording-row")
    rows.filter(has_text="Synthetic weekly standup").click()
    rows.filter(has_text="Synthetic lecture, week 3").click(modifiers=["ControlOrMeta"])
    bar = page.get_by_test_id("selection-bar")
    expect(bar).to_contain_text("2 selected")
    resync = bar.locator("[data-request=resync]")
    expect(resync).to_be_enabled()
    resync.click()
    expect(bar.get_by_test_id("resync-reply")).to_contain_text(
        "2 checked: 2 updated (speakers 2", timeout=RUN
    )
    # Details shows the check, and a Re-sync of one now finds nothing new.
    rows.filter(has_text="Synthetic weekly standup").click()
    page.get_by_test_id("tab-details").click()
    details = page.get_by_test_id("plaud-details")
    expect(details).to_be_visible()
    details.locator("[data-request=resync]").click()
    expect(details.get_by_test_id("resync-reply")).to_contain_text("Up to date", timeout=RUN)
    # At 320 px Details' Re-sync is a 44 px target, and nothing scrolls the page sideways (J7).
    page.set_viewport_size({"width": 320, "height": 700})
    button = details.locator("[data-request=resync]")
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    assert box["height"] >= 44 and box["x"] + box["width"] <= 320 + 0.5, box
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 320
    page.set_viewport_size({"width": 1280, "height": 720})


def test_a_recording_that_fails_every_check_can_be_parked(page: Page, demo, console):
    # §14.6 and §15.2 (Dan, Phase 1's Q&A §6): Status's line with Re-sync and Park; Park moves it
    # to the Sync screen's Parked, with Unpark.
    said = demo.send("fail")
    assert said.startswith("ok: recording "), said
    rid = said.split()[2]
    page.goto(f"{demo.url}#/status")
    line = page.get_by_test_id("sync-failing-checks")
    expect(line).to_contain_text("1 recording fails every check", timeout=RUN)
    line.get_by_role("button", name="Re-sync").click()
    expect(page.get_by_text("Failed: ", exact=False).first).to_be_visible(timeout=RUN)
    line.get_by_role("button", name="Park").click()
    expect(page.get_by_test_id("sync-failing-checks")).to_have_count(0, timeout=RUN)
    open_sync(page, demo)
    parked = page.get_by_test_id("parked-row").filter(has_text=rid)
    expect(parked).to_have_count(1)
    demo.send("mend")
    parked.get_by_role("button", name="Unpark").click()
    expect(page.get_by_test_id("parked-row").filter(has_text=rid)).to_have_count(0, timeout=RUN)


def test_a_hold_turns_sync_now_and_the_imports_off_and_accept_clears_it(page: Page, demo, console):
    demo.send("hold")
    open_sync(page, demo)
    press(page, "sync")
    expect(last_result(page)).to_contain_text("Held: see Status", timeout=RUN)
    hold = page.get_by_test_id("sync-hold")
    expect(hold).to_contain_text("Plaud sync paused", timeout=RUN)
    expect(page.locator("#sync-actions [data-request=sync]")).to_be_disabled()
    expect(page.locator("#sync-actions [data-request=compare]")).to_be_enabled()
    expect(page.get_by_test_id("import-all")).to_be_disabled()
    hold.get_by_role("button", name="Accept this listing").click()
    expect(last_result(page)).to_contain_text("Accepted this listing", timeout=RUN)
    expect(page.get_by_test_id("gone-row")).to_have_count(6, timeout=RUN)
    expect(page.get_by_test_id("sync-hold")).to_have_count(0)
    demo.send("back")


def test_status_shows_a_refused_sign_in(page: Page, demo, console):
    demo.send("signout")
    demo.send("tick")
    deadline = time.monotonic() + 90
    while True:
        page.goto(f"{demo.url}#/status")
        expect(page.get_by_test_id("sync-panel")).to_be_visible()
        attention = page.get_by_test_id("status-attention")
        if attention.count() and "Plaud sign-in needed" in attention.inner_text():
            break
        assert time.monotonic() < deadline, "Status never said the sign-in is needed"
        time.sleep(3)
    expect(page.get_by_test_id("sync-summary")).to_contain_text("Sign-in needed")
    expect(attention).to_contain_text("make plaud-sign-in")
    demo.send("signin")


def test_with_the_worker_killed_the_screen_says_it_isnt_running(page: Page, demo, console):
    os.kill(demo.pids["worker"], signal.SIGKILL)
    open_sync(page, demo)
    # A press is answered from the worker's heartbeat, silent for 90 seconds (§9.2).
    deadline = time.monotonic() + 180
    pending = page.get_by_test_id("sync-pending")
    while True:
        press(page, "compare")
        try:
            expect(pending).to_contain_text("The Plaud worker isn't running", timeout=10_000)
            break
        except AssertionError:
            assert time.monotonic() < deadline, "the screen never said the worker isn't running"


# With `page`, as the module's other tests: pytest then keeps it last, in file order, instead
# of moving it after every test that opens a page.
@pytest.mark.usefixtures("page")
def test_ctrl_c_stops_everything_with_no_stray_process(demo):
    scratch = list(demo.tmp.glob("recordings-demo-live-*"))
    assert len(scratch) == 1
    assert demo.interrupt() == 0
    assert demo.stray() == []
    assert (scratch[0] / "logs" / "worker.log").is_file()  # --keep: kept, logs and all
    assert demo.lines[-1].startswith("Stopped:")
