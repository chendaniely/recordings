"""The Sync screen and Status against the real web app and worker, on the fake Plaud server (stage
2b spec §21's "e2e, live"; area A6): `scripts/demo_live.py`, as `make demo-live` runs it, after
its first catch-up. In order, sharing one demo (its start paces one call a second): Compare lists
what waits; Kept out and the flags show no title; Import selected imports with its toast; the
arrivals cue after a scheduled tick; Release's question, Let it in and Undo; a hold that turns
Sync now and the imports off, cleared by Accept this listing; a refused sign-in on Status; the
worker killed; then Ctrl-C stops everything, with no stray process. The breaker and Re-sync on a
selection come with Phase 3's handlers (areas B1 and B2). Run with `make e2e-live`."""

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


def test_ctrl_c_stops_everything_with_no_stray_process(demo):
    scratch = list(demo.tmp.glob("recordings-demo-live-*"))
    assert len(scratch) == 1
    assert demo.interrupt() == 0
    assert demo.stray() == []
    assert (scratch[0] / "logs" / "worker.log").is_file()  # --keep: kept, logs and all
    assert demo.lines[-1].startswith("Stopped:")
