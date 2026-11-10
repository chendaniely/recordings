# Task 21 review: Spec axis (Tier 2)

Ran `make test-js` (418 pass) and `make build`. Probed demo mode at 320, 360 and 1,280 px with Playwright.

## Important
1. **SyncPage.tsx:269** says "Nothing changed on Plaud this week." in every Phase 1 view, but nothing reads Plaud edits until edit-sync (Phase 3). That breaks P2 ("true now or absent") and council X5. Fix: while there is no edit-sync reader, draw Updated only when it has rows, or say only what was measured.
2. **app.css:422-424, the size gap.** J7 and §15.4 require 44 px targets at 320 px. §15's build order step 7 gives that to Task 24, whose CSS covers Copy ID, the checkboxes, the jumps and More. So those 24 px controls are planned, not this task's defect. The tabs are not covered: I measured them at 24 px tall at 320 px, and Task 24's `SMALL_TARGETS` scans only `.sync-page` and `.sync-panel`, so nobody owns them. Fix: give `button.nav` `min-height: 44px` (with a negative block margin, like `.theme-icon`), and add `.topbar` to Task 24's scan.
3. **test_tagging.py:2604:** the injected `.topbar { min-height: 90px }` makes a guard test pass on a top bar the product doesn't have. I probed at 360×170 with the real bar: the panes scroll and `.pane` covers the selection bar's bottom corners. With the injected bar it is whole, so this is a real regression. Line 2597 says "110 px" but the test still runs 170. The commit text blames only geometry. Fix: correct the overlap and test at a height where the panes overflow (125 or 140). Otherwise, put it on the phase-end list with Dan's OK and mark the 170 case xfail with a reason.

## Minor
- SyncSummary.tsx:106,121: the disabled Accept and Accept these changes buttons have no `aria-describedby`, which the brief requires of every request button. With both a hold and a breaker, `not_yet` shows twice. This is latent in Phase 1.
- Focus goes to `<body>` when a Keep it here row disappears on the redraw, and when Import selected disables itself.
- Copy ID: the fixed `aria-label` hides "Copied" from a screen reader. Copying works on a laptop over plain HTTP (the `execCommand` fallback). On an iPhone it is unverified: there is no `setSelectionRange`, and the textarea's font is under 16 px.
- SyncPage.tsx:206 shows `r.title` for a review row and under `titles_hidden`, trusting the server. A UI guard would match the guard Kept out already has.
- Keep it here's "Queued" or "worker down" reply shows in the Sync card, not next to the flag.

## Scenarios
C5.06, C9.02, C9.03 and C9.04 are each proved in SyncPage.test.ts, at the UI level, from fixtures.

## Docs
Nothing was renamed. Main spec §12 already lists Add. No doc is made untrue beyond item 3's comment and commit text.

## Security
1. Secrets: fine. The change is UI only, and requests carry IDs only.
4. Failing closed: fine. Buttons for unhandled kinds are off, and the server refuses in the same order. Before a listing, no counts are shown. One note: the error branch shows `error.message`, which is exception text (the same as StatusPage). That is Minor.
6. Logging: not applicable. There is no Python or console logging.
- **P13:** fine. A title smuggled into a Kept out row is not drawn. Flags carry no title. Release's title appears only in its dialog, never in a toast. See the review-row Minor.

Verdict: changes needed (0 Critical, 3 Important)
