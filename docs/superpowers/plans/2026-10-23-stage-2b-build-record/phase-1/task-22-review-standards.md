# Task 22 review: Standards (Tier 2), 7050484..d2cf475

**What I checked.** The CLAUDE.md UI rules all hold:
- React stays external (`ReactNode` is a type import only).
- No new output, handler or input.
- `send` runs only from a click.
- No media query was added or changed, so PHONE, both 760 px blocks and the tablet block still match.
- No `!important`, no `stopPropagation`, no layer change, and `ui.js` is untracked.

`make test-js` passed (433). The commit's subject, its why, its failing line and its `Plan:` trailer are all correct; Co-Authored-By names the implementer's model. I measured the demo at 320 px, at 820 px touch and at 1,280 px with Playwright, feeding in a hold and a breaker.

## Important

**I1. `SyncPanel.tsx:23-24`: Accept doesn't wait for the gate.** On the Sync screen, Held is turned off by `gated("accept")`, which is `kindReason(...) ?? view.gate` (`SyncPage.tsx:112-113`). The panel uses only `kindReason`. While the gate is closed, the worker refuses `accept` and `accept-changes` (spec §9.3). So once Phase 3 widens `handled`, Status would show an enabled Accept that the worker refuses, while the Sync screen shows it off. This breaks `kindReason`'s rule that "a button left enabled is one the worker will answer". It can't happen in Phase 1. The brief's code has the same gap.
- Fix: `kindReason("accept", why, panel) ?? plaud.gate`, and the same for `accept-changes`.

**I2. `app.css:445-446`: on a phone, Details loses its triangle.** The rule `.sync-panel .sync-details > summary { display: flex; … }` replaces the `list-item` display, which removes the disclosure marker.
- Measured at 320 px: 44 px tall, no ▶. At 820 px the ▶ shows.
- On a phone, Details now looks like plain text, with no sign that it opens or whether it is open.
- Neither rule was in the brief. Both repeat Task 24 Step 5, which already plans `.sync-details > summary { padding-block: 12px }` and `.sync-panel [data-slot=button] { width: 100%; min-height: 44px }`.
- Fix: remove both rules and leave them to Task 24. Or use `padding-block: 12px` and keep `list-item`.

## Minor
- **M1. When state.db can't be read** (`plaud.py:691`, empty details), the panel draws:
  - "Normaliser: version " with no version;
  - "Keep-out words: none", which isn't known to be true;
  - an empty `<dl>`.
  - Fix: guard each of these rows on its value being known.
- **M2. Duplicated Code:** the comma-separated `<code>` list appears three times (`SyncPanel.tsx:70`, `SyncPanel.tsx:79`, `SyncSummary.tsx:137`). Extract one `CodeList`.
- **M3. Wording dependency:** `consentText(...).replace(/^Consent list: /, "")` depends on the exact wording of `consentText`. Export a `consentBody` instead. The Archive row, by contrast, keeps its own "In the archive from Plaud:" prefix after its label.
- **M4. Mysterious Name:** `x` and `d` would be clearer as `numbers` and `details`.
- **M5. Archive row:** it is drawn without the `listed` check that the Sync screen applies (`view.listed && view.archive`). This is harmless while the server sends null.
- **For information, touch tablet:** the summary is 24 px and Accept is 32 px, the same as on the Sync screen. §15.4 and J7 ask for 44 px only on phones.

Verdict: changes needed (0 Critical, 2 Important)
