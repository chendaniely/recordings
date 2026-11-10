# Task 24 report

Status: DONE_WITH_CONCERNS. Commit a03ed4c (base 0f94d32).

## Built
- `packages/ui/tests/e2e/test_sync.py` (8 tests): canned view at 1,280 and 320 px; every request button disabled with the demo note; 44 px targets and full-width buttons at 320; axe in light and dark (Sync screen and Status panel); clean console and no detached bindings.
- Demo guard: crafted `plaud_request` refused, state.db has no request row, no worker started.
- `build_pages.DEMO_VIEW`, `check_inputs(view=)`, `assemble(view=)` (refuse missing or symlinked canned view; added a symlink test). `pages/app.py` configures the canned view and reason. Pages smoke test (C9.07) and the Pyodide-never-imports probe.
- `app.css`: phone rules for the Sync screen. Docs: CLAUDE.md, README, Makefile, pages.yml.

## Steps
- Step 3 failures seen: `TypeError: check_inputs() got an unexpected keyword argument 'view'`, `TypeError: assemble() ... 'view'`, `FileNotFoundError ... demo/plaud-sync.json`, `AttributeError: 'NoneType' object has no attribute 'demo_view'`, and the probe's `rows 0 != 3`. The three new demo-guard tests passed at once (they pin Tasks 14, 19, 20). E2E before CSS: `test_targets_are_at_least_44_pixels_at_320` failed, "Sync now: 32 px", 17 more small targets; the other 7 passed.
- After: `make check` 1953 passed + gitleaks clean; `make test-js` 455 passed; `make build`; `make e2e` 144 passed; `make pages pages-test` 10 passed.

## Built differently
- Dropped the brief's four-tabs test: test_tagging's top-bar test pins it. `.topbar` not added to SMALL_TARGETS.
- The equation assertion keeps the full text "Listed 9 = 4 here + 2 waiting + 2 excluded + 1 missing" (the screen draws it that way).
- `open_panel` helper also waits for the Details to be open. Added a `test_a_row_stacks_its_date_and_title_at_320`; it passed at once, because rows already wrapped at 320 px, so it pins rather than drives the stacking rule.
- Added a symlinked-canned-view refusal test in test_build_pages.py.
- CSS left out / changed: the brief's `.sync-panel [data-slot=button] { min-height: 44px }` duplicated Task 22's `.sync-panel button { min-height: 44px }`, so the panel rule is `width: 100%` only; `.sync-page [data-slot=button]` keeps both. `.sync-more > summary` gets padding only (Status's Details has its own).
- pages.yml header comment rewrapped to stay under the line width.

## Docs read (Step 1)
Tailwind adding-custom-styles page (says utilities are layered; does not itself state that unlayered CSS beats layers, which is the CSS cascade rule, confirmed by the e2e); shadcn Button page (lists only `size`, no heights; the h-8 is in components/ui/button.tsx); WCAG 2.5.8 Understanding page (inline exception read).

## _site/
`app.json` holds 116 files: `demo/plaud-sync.json` plus `demo/archive/` only (no config.toml, state, or other archive); `_site/media/` has the 4 demo media files. Nothing real exported.

## Self-review
- UI rules: no new `!important`; rules unlayered inside the existing second 760px block (no third block); React untouched.
- Accessibility: axe clean both themes; targets at least 44 px; `.sync-jump` buttons inside the equation sentence are 44 px tall by the brief's rule.
- Privacy: only synthetic hand-written view and demo archive exported; no Plaud call.

## Concerns
- The row-stacking test doesn't fail without the CSS (rows already wrapped at 320 px).
- Tailwind doc page did not state the layer precedence; relied on CSS spec plus the passing e2e.

## Fix round 1 (commit 507aa65)
1. Rows: chose "box and date on one line, title below at full width" (`.sync-page .sync-row .sync-title { flex-basis: 100% }`), which is §15.4 literally; dropped the column rule. Test asserts, for all 3 rows, date one line high, box beside the date, title spans the row. Failed with the rule renamed: `(0, 'the title has its own line', {... 'width': 99.58 ...}, {... 'width': 262 ...}) assert 99.578125 >= (262 - 1)`.
2. State.db test now sends keep-here (handled), so only the demo guard stops the row. Added a control test lifting the guard (`runtime._plaud_state`, `_plaud_reason`): 1 row lands. The real test with the guard lifted failed: `assert 1 == 0`.
3-4. Phone rules scoped to `.sync-page` (`.sync-copy`, title basis); summary padding is now one rule with Task 22's.
5. Target test's panel part commented: it checks the open Details summary.
6. `.sync-jump` treated as the inline exception: no 44 px rule; excluded from SMALL_TARGETS (`button:not(.sync-jump)`), with comments.
7. Pages spy: positive control (choosing a recording sends `selected_id`). A first attempt with no control failed with `assert []`, showing the spy had seen nothing before.
8. Axe test asserts html.dark (or its absence for light) before running.
Checks after last change: ruff, make check 1954 passed, test-js 455, build, e2e 144, pages-test 10 passed. Details on a phone is unaffected by the scoped rules (e2e library tests pass).
