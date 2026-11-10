# Task 24 review: Standards (Tier 2), 0f94d32..a03ed4c

Ran: `make build`; the `test_sync.py` e2e (8 passed); `make test-js` (455 passed); `make pages pages-test` (10 passed). `_site/app.json` holds the vendored code, `demo/archive/` and `demo/plaud-sync.json`, nothing else.

## Important

1. **`e2e/test_sync.py:292-298`: the row-stacking test can't fail against its CSS** (`app.css:472-473`). At 320 px the row is 262 px wide. The checkbox and "2026-10-23 09:05 · 47 min" already take about 226 px, so plain `flex-wrap` puts the title under the date anyway. Nothing pins `flex-direction: column` or `align-self`.
   - **Fix:** assert what only the rule gives, and see it fail with the rule removed. For example: on every not-here row, `.sync-state` starts below `.sync-title`. Row 3 (about 207 px) fits on one line under wrap.
   - **Or:** keep the checkbox beside the date (§15.4: "date and duration on one line, title below") with `.sync-row .sync-title { flex-basis: 100% }`, and test that.

## Controller note (not counted)

The main spec §17.1 (line 2607) still says "the build exports only `demo/archive/`", which is now untrue. Decision 35 bars this task from fixing it, so log it under Drift or Phase-end.

## Minor

- `app.css:469,472`: `.sync-copy` and `.sync-row` are unscoped, so on a phone they also restyle Details' Plaud section. Its Copy ID repeats line 459's 44 px, and its flag rows now stack. Scope the rules to `.sync-page`, or say so in the comment.
- `app.css:471` repeats line 453. Merge them as `.sync-more > summary, .sync-details > summary`, as line 360 does.
- No test pins `app.css:468`, because the canned panel has no buttons. For the same reason, `test_sync.py:277`'s panel check proves nothing.
- `.sync-jump` is a target in a sentence, which the test's comment exempts. The test counts it anyway, and the CSS makes it 44 px tall but about 9 px wide. Either exempt it or make it 44×44.
- The Pages `SPY_INPUTS` has no positive control. Assert that the spy saw some input.
- Duplicated Code: `axe`, `AXE` and `WCAG` copy `test_tagging.py`; `DETACHED` copies `test_library.py`; `TWO_FRAMES` copies `test_tagging.py`. The websocket read loop appears three times in `test_demo_guard.py`.

## Checked, fine

- **CSS:** inside the second 760 px block, unlayered, no new `!important`; Tasks 21–23's rules untouched.
- **Docs:** CLAUDE.md, README, Makefile, pages.yml, `build_pages` and `pages/app.py` all name both files.
- **Commit:** format, failing lines and `Plan:` trailer present.

Verdict: changes needed (0 Critical, 1 Important)
