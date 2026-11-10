# Task 22 report: Status's sync panel

Commit: d2cf475 (base 7050484).

## Built
- `SyncPanel.tsx` (new): the panel as the brief listed it. Summary (link "add"), failing, not-pinged, conditions, hold/breaker lines with one `Reasons` block, numbers, folded Details.
- `SyncPanel.test.ts` (new), `StatusPage.tsx` (panel after Needs attention), `StatusPage.test.ts` (provider, mock, two tests), `app.css`.

## Steps
- Step 1: read shinyreact-build-app SKILL.md "Loading vs. recalculating" and shadcn `rules/composition.md` (both local, read).
- Step 3: `make test-js` failed at typecheck: `src/components/SyncPanel.test.ts(12,27): error TS2307: Cannot find module './SyncPanel' or its corresponding type declarations.` (StatusPage's new test was not seen failing alone; the panel and its placement were written together.)
- Step 5: make test-js: 37 files, 433 tests pass.
- Step 6: ruff format (no change), make check (1942 passed, gitleaks clean), make build, make e2e (136 passed).
- Step 7: commit message checked by commitmsg.py, committed.

## Built differently
None in the listing: every import (`n`, `reasonsFor`, `Reasons`, `SyncHoldLine`/`SyncBreakerLine` with `whyId`, `kindReason`, `NO_PLAUD`) matched the code as built. `SyncSummary` already hides "Open the Sync screen" for `no-sync` and `demo`. The CSS addition: I added to the brief's phone block two rules for the 44 px targets (below).

## Self-review
- UI rules: React untouched; no handler or input outside App (send comes from context, on click only); no new `!important`; no stopPropagation; ui.js rebuilt not edited; the phone rules went in the existing `max-width: 760px` block (no media query added or changed).
- Accessibility: in the phone block, `.sync-panel .sync-details > summary` is flex with `min-height: 44px` and `.sync-panel button` has `min-height: 44px`. The rule `.sync-details > summary { min-height: 24px }` from Task 21 is outranked by the added rule's specificity. A disabled Accept names its reason through `aria-describedby`, and the reason is drawn once (test checks one `.sync-why`).
- Privacy: no title field is read; a test with a smuggled `title` passes.

## Concerns
- The 44 px rules apply only at phone width (max-width 760px), as J7 asks for 320 px; a touch tablet gets the 24 px summary. The "Open the Sync screen" inline link is not enlarged. Task 24's scan should say whether links count.
- I did not verify visually in a browser at 320 px; e2e has no check for the new panel's sizes.

## Fix round 1

Commit db9a546 (on d2cf475). All 11 items fixed. After the last change: ruff format clean, make check (1945 py tests, gitleaks clean), make test-js (436), make build, make e2e (136) all pass.

1. Gate as problem: `_Read.held_by_gate()` (no blocker, no failing, schedule on, last turn outcome `gate`) makes `summary_view` kind `no-sync`. Test records a `gate` turn via `record_turn`. Seen failing before: `assert ('problem' == 'no-sync'`.
2. Link: `SyncSummary` link gained `"none"`; the panel passes it for `demo` or `no_sync_yet`; "add" always links. Vitest covers the gate summary (link), demo and no-sync-yet (none).
3. Duplicates: fixed at the source in `_panel_from` (conditions drop anything in `attention`). Python test; StatusPage Vitest fixture now has `conditions: []` and asserts the late line appears once. Failing before: line was in both lists.
4. Unreadable: the view model's unreadable panel carries an empty normaliser; the panel draws numbers and Details only when `!no_sync_yet && normaliser !== ""`. Vitest + an assertion in `_assert_unreadable`.
5. Accept: `kindReason(...) ?? plaud.gate` for both; Vitest with a gate set and `handled` = every kind.
6. Details marker: now `padding-block: 12px`, `display` stays list-item. Measured with Playwright (demo, 320 px wide): summary 44.3 px tall, 254 px wide, display `list-item`, list-style-type `disclosure-closed`. (Accept buttons keep `min-height: 44px`.)
7. C6.09: Python test for "last ping failed 13:40 (HTTP 404)" and "(unreachable)"; the `PANEL` fixture's not-pinged time is now 14:40, matching the failed ping.
8. "failing since" said once: when the summary says "failing (since …)", the failing line is "Plaud sync failing (detail)." and the not-pinged cause is "the sync is failing". Test counts "14:00" once across the three. Existing assertions in the C6.06 test updated to match.
9. first-run.md: now "Status's "Plaud sync failing" line says why."
10. Demo: dropped `next_sync` from the demo's panel numbers only (the Sync screen's canned view keeps its own "14:40"), so the panel no longer shows a next sync beside "isn't part of the demo".
11. `CodeList` (used 3 places), `consentBody` (consentText now wraps it), `numbers`/`details`, Archive row only when `panel.listed` (Vitest).

Concerns: the Vitest tests for items 2, 4, 5 were not run against the old code to see them fail (Python ones were). A `gate` turn recorded earlier than the gate opening would keep saying the stale reason as no-sync until the worker's next turn; that is how health.py reports it, not changed here.
