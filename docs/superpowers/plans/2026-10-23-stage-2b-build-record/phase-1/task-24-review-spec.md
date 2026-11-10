# Task 24 review: Spec axis (Tier 2), 0f94d32..a03ed4c

Ran: `make build`; `test_sync.py` 8 passed; `make pages pages-test` 10 passed; the new build and guard tests 8 passed. Probed demo mode at 320 px with Playwright. All five request buttons are disabled, and each one's `aria-describedby` resolves to "Demo: nothing is synced". The note shows three times: once in the summary and once each in Sync and Not here yet. That is once per section, as designed. axe (WCAG 2.2 AA) finds nothing in either theme. Nothing scrolls sideways, the console stays clean and no request leaves the origin.

## Important
1. `docs/superpowers/specs/2026-10-08-recordings-design.md:2607` (main spec §17.1) still says "the build exports only `demo/archive/`". The build now also exports the canned view. Fix: add "and the hand-written canned Plaud view `demo/plaud-sync.json`".
2. `packages/ui/tests/test_demo_guard.py`, `test_the_demos_state_db_never_gets_a_request_row`, cannot fail in Phase 1, which §21 requires of a guard test. `check_request` refuses `import` with NOT_YET before any insert, whatever the demo's guard does. Fix: send a handled kind, `{"kind": "keep-here", "recording_ids": [<a demo ID>], "plaud_ids": [], "all": false}`.

## Minor
- No browser test covers the Library and Details at 320 px, because the demo draws none of their 2b parts: arrivals are 0, `recording_plaud` is None in the demo and Re-sync is hidden. Task 23's `.plaud-details button` 44 px rule and `.resync-long` are unverified. Put them on the phase-end list for e2e-live.
- §21's e2e-live list sits in Phase 2's outline, but its breaker and "Re-sync on a selection" items need Phase 3's handlers, and Phase 3's outline doesn't name them.
- `test_a_row_stacks_its_date_and_title_at_320` passes without the CSS. It also never checks §15.4's "date and duration on one line". The probe measured 20 px, one line. Assert `.sync-when`'s height.
- `test_axe_finds_nothing_in_light_and_dark` never checks that `html.dark` took effect.

## Scenarios
- **C9.01:** proven by `test_sync.py`. The four tabs are pinned by `test_tagging.py`'s top-bar test at 320 px.
- **C9.07:** proven by the Pages smoke test (canned view, no `plaud_request`, nothing outside the site) and the crafted-refusal test. Important 2 weakens the no-request-row part.

## Security (lines 1, 4, 6)
1. **Secrets: fine.** `_site/app.json` holds 116 files: the entry, the vendored source, the 25 tracked `demo/archive` files (no media) and `demo/plaud-sync.json`, byte-equal to the committed file. Its IDs run `of_…0001` to `of_…0005` and its titles are invented. The only grep hits are code: normalise's `X-Amz-` redaction regex and config's `100.64.0.1` example. No token, JWT, path, LAN host or email address. `_site/media` holds the 4 demo files.
4. **Failing closed: fine.** `demo_view()` turns every action off whatever the file says, and never raises. A crafted request is refused with the demo's reason before `sqlite3` loads. Pages has no state and carries the reason. The build refuses a missing or symlinked view.
6. **Logging: fine.** No new log calls. Build errors carry repo paths only.

Verdict: changes needed (0 Critical, 2 Important)
