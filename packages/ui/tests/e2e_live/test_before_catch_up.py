"""`make demo-live --before-catch-up`'s side (stage 2b spec §21's "e2e, live": "before the catch-up,
only Compare is enabled"; area A6): the gate closed, Compare works and the rest say why; the
catch-up, typed as a command, opens it; then Ctrl-C stops the web app, the worker and the fake,
with no stray process. Run with `make e2e-live`."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_live

GATE = "Scheduled sync waits for the first catch-up (runbook)"


def test_before_the_catch_up_only_compare_is_enabled(page: Page, demo_before, console):
    page.goto(f"{demo_before.url}#/add")
    expect(page.get_by_test_id("sync-summary")).to_contain_text(GATE)
    actions = page.locator("#sync-actions")
    expect(actions.locator("[data-request=compare]")).to_be_enabled()
    expect(actions.locator("[data-request=sync]")).to_be_disabled()
    expect(actions).to_contain_text(GATE)
    actions.locator("[data-request=compare]").click()
    result = page.get_by_test_id("sync-last-result")
    expect(result).to_contain_text("Compared: listed 12 = here 0", timeout=90_000)
    expect(page.get_by_test_id("not-here-row").first).to_contain_text(
        "waits for the first catch-up"
    )
    expect(page.get_by_test_id("import-all")).to_be_disabled()


def test_the_catch_up_opens_the_gate(page: Page, demo_before, console):
    demo_before.send("catch-up", timeout=180)
    page.goto(f"{demo_before.url}#/add")
    expect(page.locator("#sync-actions [data-request=sync]")).to_be_enabled(timeout=30_000)
    expect(page.get_by_test_id("sync-summary")).not_to_contain_text(GATE)


# With `page`, as the module's other tests: pytest then keeps it last, in file order, instead
# of moving it after every test that opens a page.
@pytest.mark.usefixtures("page")
def test_ctrl_c_stops_the_web_app_the_worker_and_the_fake(demo_before):
    scratch = list(demo_before.tmp.glob("recordings-demo-live-*"))
    assert len(scratch) == 1
    assert demo_before.interrupt() == 0
    assert demo_before.stray() == []
    assert not scratch[0].exists()
