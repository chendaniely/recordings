# Task 21 review: Standards (Tier 2), aee952f..f25f75e

Ran: `make build`, `make test-js` (418 pass), `-k short_phone` (2 pass), and a probe of the phone Library at 360 px, at HEAD and at base (rebuilt in scratch).

**CLAUDE.md UI rules all hold.** React stays external (build.test passes on the new bundle). `plaud_sync` is already subscribed in `App`. The page has no input or handler of its own: it only calls `plaud.send`. Every send comes from a click: no effect, hash or storage sends one. No new `@media` block, layer, `!important` or `stopPropagation`. `www/ui.js` is untouched.

## Important

1. **`test_tagging.py:2594-2604`: the injected `.topbar { min-height: 90px }` hides a product defect, and the reason it gives is false.**
   - **What the probe shows (the `WHOLE` corner check):** at HEAD the selection bar is cut off at every height from 215 down to 120 px. At 170 px, 18 of its 43 px are hidden. The bar runs past the list's 40dvh cap, the list's `overflow: clip` cuts it off, and the pane shows through.
   - **The stated reason is wrong.** The rows are still reachable down to 120 px while the panes overflow. So the commit's and the comment's "at no height do the panes overflow with the rows still reachable" is false. The report's account is right.
   - **Who caused it.** The defect is older than this task: at base the bar was cut at 200 px and whole at 170, 185 and 220. The one-row top bar moved the bad band onto the test's own 170 px.
   - **Two smaller faults.** The injection also covers the 250 px case, which passes without it. The comment's "At 110 px" contradicts the 170 px the test runs at.
   - **Fix:** drop the injection. In the second phone block, set a floor on the list's cap, e.g. `.list { max-height: max(40dvh, 92px) }`, with a comment that 92 px is the list head (45) plus the bar (43) plus borders. Probed: the bar stays whole at every height from 250 to 115 px, and the test's own asserts hold at 250 and 170. Add a height from the bad band (200).
   - **If Dan defers it:** put it on the phase-end list and mark the case `xfail(strict=True)`. Don't test a layout the product never draws.

## Minor (judgement calls)

- **Duplicated Code:** `n` is defined in both SyncPage and SyncSummary. `equationText` and SyncPage's inline Jump equation build the same sentence.
- **Data Clumps:** `reasonsFor(prefix, …)` and `<Reasons prefix=…>` both repeat the prefix and the `-why-i` scheme. The pair `disabled={x !== null} aria-describedby={g.idOf(x)}` appears about 12 times; a `RequestButton` would replace it.
- **Shared props:** `SyncHoldLine` and `SyncBreakerLine` give Accept no `aria-describedby` (FlagRow does). When both lines can accept, the Held section says one reason twice. Tasks 22 and 23 inherit both (this overlaps the spec axis).
- **Primitive Obsession:** the page compares the server's display strings `FULL_UNKNOWN` and "No sync has run yet" (`plaud.py:217`, `:223`). The server should send a field instead.
- **Mysterious Name:** `b`; `off`, which returns a reason; `parking` and `previewing`, which hold reasons but read as booleans.
- **Long component:** SyncPage is one function of about 270 lines. Its sections could be components.
- **app.css:419-421:** the comment "must follow to win" is wrong for `.topbar button.nav`. Its specificity (0,2,1) beats `button.nav` (0,1,1) in any order.

**Commits:** the subject, the why, the failing lines and the `Plan:` trailer are all fine.

Verdict: changes needed (0 Critical, 1 Important)
