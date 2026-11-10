# Flaky e2e `test_the_confirmation_opens_over_the_plus_picker_and_leaves_it_open`: root cause

Investigated 2026-10-31, read-only, in a scratch copy of 5f30cb9. CI failures: run 41188016662 on
e048326 and run 41198043532 on f92c1c1, both the `to_have_count(0)` right after the Esc on the
second confirmation (`test_tagging.py:278`).

## Verdict: an app fix (tagging part A's `TagPicker.tsx`)
The test waits for the right thing (Cancel focused) and presses Esc. For a few milliseconds after
the confirmation (Radix Alert Dialog) opens over the + picker (Radix Popover), Esc still reaches
the picker's listener: the picker closes, the confirmation stays. Breaks tagging spec §6, "Esc
closes the topmost layer only". No person can hit a window that short; a busy CI runner can.

## Root cause
Radix Dismissable Layer 1.2.1 listens for Esc only while it is the highest layer, worked out in
render (`dist/index.mjs` 50, 90-108). A new layer joins the set in an effect (117); `dispatchUpdate`
(118) re-renders every layer, and only in that render's effects does the new layer add its Esc
listener and the one below remove its own. Focus Scope's mount effect focuses Cancel in the same
pass as the join (`react-alert-dialog` 60). Under React 19.2 the forced render and its effects run
as two more scheduler tasks (counted: focus at task 28, pointer-events at 29 (+7 ms), listener swap
at 30 (+2 ms)). A layer opened over nothing has no gap (its index, -1 before it joins, equals
`0 - 1`), which is why only this test flakes.

## Reproduction
A scratch pytest plugin that delays React's next 3 scheduler tasks by 300 ms once focus enters an
alertdialog fails 5 of 5 with CI's exact message; its log shows the Popover's listener taking the
Esc. Controls (confirmations opened over nothing) pass under the same delay. Unmodified, 25 local
runs passed (one Esc landed 1 ms after the swap).

## Fix (`fix-2.diff`)
`pickerEscape(tagging)` replaces `imeEscape` as `onEscapeKeyDown` in both picker forms: the IME rule
first; then, if an Esc reaches the picker while `tagging.confirm` is set, `preventDefault()` (the
picker stays) and `tagging.answerConfirm(false)` (Cancel, as the Alert Dialog's own Esc does).
Mirrors `keepOpen`. Two Vitest tests (one per form) fail before (`expected [] to deeply equal
[ false ]`) and pass after. A `docs/upstream-issues.md` entry (local note only). Verified on the
patched copy: the repro passes 5 of 5, tsc clean, Vitest 380, all 132 e2e passed.

A test-only alternative (wait for the alertdialog's `pointer-events: auto` before Esc) only narrows
the window; nothing in the DOM marks the listener swap.
