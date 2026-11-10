# Task 23 review: Standards (Tier 2), 7596f3d..47b400f

Run: `make test-js` 452 pass (also after `make build`); tree clean; `uv run pytest -m e2e -k "short_phone or selection or refresh"` 27 pass.

## CLAUDE.md UI rules: all hold
- React external: no `react-dom` import outside tests; build config untouched.
- Outputs: `plaud_arrivals` and `recording` have `useShinyOutputStatus` lines (`App.tsx:34,37`); the leaves only read them.
- No handler or input added; sends go through App's `PlaudContext`, only on click.
- CSS: still two 760 px blocks; no `@layer`, no new `!important`; no `stopPropagation`; `ui.js` untouched.
- Tagging part A: Refresh text, header-note order, Tag/Clear/Esc/just-tagged unchanged. The carried-over SelectionBar cases keep their assertions word for word.

## Commit
Subject, `Plan:` trailer, `Co-Authored-By` (Sonnet, per the plan) and the docs checked are all correct.

## Critical / Important
None.

## Minor
1. **The record (`47b400f` body):** "passed before" is true of the base (Task 22's close: 436 Vitest pass at 7596f3d, assertions identical). It omits what the report says: no Vitest case ran at the red step, because tsc stopped the run. Don't amend; say it in the close's `Rounds:`, as Task 22's close did.
2. **Fix needed:** in `SelectionBar.tsx`, the comment "(…, not handled yet, …)" and the test "says it comes with the next part…" describe a state that can't happen. `handles` and `why` read the same `view.handled`, so NOT_YET never shows with the button drawn. Use GATE in the test, and drop the phrase.
3. **Latent bug, forward to Phase 3:** the bar draws `reply` without checking `offered`, and keeps `plaud.last` past Clear. A later selection shows an earlier run's end line, or Details' run's.
4. **Smell, Duplicated Code / Feature Envy:** `RecordingList.tsx:360` and `TagsPage.tsx:602` repeat `resyncReason` and the `send` lambda, while the bar reads the same context. They send `[...sel.selected]`, not `targetsOf(...)`, which `lib/selection.ts:108` says the bar acts on.
5. **Smell, Duplicated Code:** one reply has two wordings: "Re-syncing 12 of 25…" in the bar and "Re-syncing (12 of 25)" from `replyText`. `HeadNotes` repeats `SyncSummary`'s "· See Status" markup.
6. **Inconsistent IDs:** the bar uses a fixed `id="resync-why"`; `PlaudDetails` and `FlagRow` use `useId`.
7. **Smell, Mysterious Name:** `n` (HeadNotes); `whyId` also names the heading; `plaud` means `Plaud` in the bar but `RecordingPlaud` in Details.
8. **Style:** `app.css:459` closes the media block on the rule's line (`}}`). `TagsPage.tsx:18` puts the `usePlaud` import among the `../lib/*` imports.

Verdict: approve
