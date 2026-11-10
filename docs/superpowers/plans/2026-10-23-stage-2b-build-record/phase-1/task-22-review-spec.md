# Task 22 review: Spec axis, Tier 2 (7050484..d2cf475)

Ran `make test-js` (433 pass) and `make build`. Demo at 320 px: no sideways scroll, numbers stacked, the Details toggle 44 px. Across a 60 s `status` redraw, `<details>` stayed open with no DOM change, so no flash.

## Important
1. **The gate draws as a red "problem" after the worker's first turn.** `plaud.py:596` (Task 19). After a gated turn (`worker.py:692` records one each tick), `SyncHealth.cause()` returns the gate's reason (`health.py:235`). The kind becomes "problem", against Task 19's fix and §11.6 ("isn't a failure"). `test_plaud_views.py:66` records no turn, so it passes. Fix: treat a `gate` outcome as no-sync, and record one in that test.
2. **Status drops its link to the Sync screen whenever the kind is `no-sync`.** `SyncSummary.tsx:18`. §15 says the two link to each other. Once 1 is fixed, that covers all of Phase 1, yet Compare (J8) and a late keep-out's "or Keep it here" are only on the Sync screen. Fix: hide the link only for `panel.no_sync_yet` or the demo.
3. **Status says the same sentence twice.** `plaud.py:974-975` and `999-1002` put the consent stop, each flag line and the disk line in both `conditions` and `attention`. Status draws Needs attention, then the panel's conditions (`SyncPanel.tsx:34`). A late keep-out shows the same sentence twice, one under the other. `StatusPage.test.ts:91` builds exactly this and never checks that the line appears once. Fix: in the panel, skip conditions already in `panel.attention`, and test it.
4. **When the records can't be read, Details shows values nothing measured (P2).** `plaud.py:871` builds on `_empty_panel()` with `no_sync_yet: False`. The panel draws an empty `<dl>`, "Normaliser: version " with no number, and "Keep-out words: none" (`SyncPanel.tsx:69,79`). Fix: draw no numbers or Details in this state, and test it.

## Minor
- C6.09: no Python test covers `_panel_from`'s "last ping failed … (HTTP 404)" branch. Both named tests would still pass with it broken.
- `PANEL` says "not pinged since 14:20" beside a ping that failed at 14:40. `_not_pinged` counts the last attempt, failed or not, so it would say 14:40.
- When the sync fails, the summary, the failing line and the not-pinged line each say "failing since 14:00".
- `first-run.md:1231` says "Status's numbers say why", but the reason is in the failing line.
- The P13 test only shows that an unknown key isn't drawn.
- The demo shows "Next sync 14:40" beside "isn't part of the demo".

## Scenarios
- C6.06 is proven, together with `test_plaud_views.py:162`.
- C6.09 is only partly proven (first Minor).

## Security
1. Secrets: fine. The panel draws only server phrases, and the ping row has a time and a status, no URL. Fixtures are synthetic.
4. Failing closed: fine. `handled` keeps Accept off, and the server refuses it (NOT_YET). Unreadable records give a fixed line with the exception's class.
6. Logging: not applicable, since no logging was added.
P13: fine. The panel reads named fields only, and `_panel_from` names recordings by ID.

Verdict: changes needed (0 Critical, 4 Important)
