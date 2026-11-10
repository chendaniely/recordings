# Flaky tagging part A tests: root causes (investigated 2026-10-31, read-only, in a scratch copy of c2f1d44)

| # | Test | Root cause | Fix (tests only) |
|---|---|---|---|
| 1 | e2e `test_the_headers_touch_targets_are_44_px_and_never_overlap` | Counts the chips' × before tagging is on: the Library draws before the writer's first open ends (spec §5: `can_tag: false`, "the archive is being opened"), and tagging comes on with a second `library` push. `xs.count()` doesn't wait. | `tagging_on(page)` in `open_library` (waits for `key-hint` attached and no `.opening` cannot-tag note) |
| 3 | e2e `test_t_on_a_selection_esc_keeps_it_and_cmd_z_undoes_after_the_toast` | Same race: T during the open shows the "Opening the archive…" toast, no picker; `fill` waits 30 s for the dialog. | Same helper, plus `tagging_on(page)` after `page.goto` in 15 more tests the same race breaks (listed in the diff) |
| 2 | e2e `test_the_status_page_does_not_scroll_sideways_on_a_phone` | Measures before the webfont loads: fonts are fetched only once the app draws text; with `font-display: swap` the first layout uses the fallback, and in Linux's DejaVu Sans the top bar is exactly 19 px too wide at 320 px (Atkinson leaves 3.3 px spare). | Wait for `document.fonts.ready` before measuring |
| 4 | Vitest `TagPicker.test.ts` "pointer movement over the list is a choice too" (and its sibling at line 178) | `tick()` is `setTimeout(0)`; React's Scheduler renders the continuous pointermove update in a `setImmediate`, which Node can run after the timer. | `vi.waitFor(...)`, as Sidebar and RecordingList tests already do |

Reproductions (each fails with CI's exact message before the fix, passes after):
- 1 and 3: a `sitecustomize.py` that delays the demo server's first `WriterThread.ensure_open` by `E2E_SLOW_OPEN` seconds; all 17 affected tests pass with the fix under a 3 s delay.
- 2: a pytest plugin that adds a DejaVu fallback after Atkinson and 1500 ms of CDP latency: `assert [0, 19] == [0, 0]` before, pass after; with no latency it passes, so it takes both.
- 4: a Vitest setup that starts each test from a `setImmediate` and slows any turn that sets a 0 ms timer: 2 failed before, 42 passed after.

Verified on the patched copy under normal conditions: tsc clean, Vitest 378 passed, ruff clean, all 132 e2e passed.

Patches: `fix-tagging.diff`, `fix-library.diff`, `fix-vitest.diff` beside this file.

A design note for Dan (not a test bug): at 320 px the top bar has only 3.3 px to spare in Atkinson Hyperlegible, so a larger default font, or a webfont that fails to load, would make the Status page scroll sideways for real. Making the top bar wrap or shrink is a design call.
