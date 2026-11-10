# P1 wrap: Phase 1's last answers, and the phase-end list sorted

Read `briefs/BUILD-FAST.md` (same folder) first: it is the shared rules for this whole build.

## 1. Build Dan's three phase-end answers

They are in `docs/superpowers/plans/2026-10-23-stage-2b-phase-1-qa.md`, sections 5, 6 and 7. Read each
section in full; build what it says the phase end does. In short:

- **§5, 320 px and a larger text size:** the top bar's tab row scrolls sideways (`overflow-x: auto`,
  `min-width: 0` where the flex row needs it), never the page. An e2e test at 320 px under a larger
  root font size (for example `html { font-size: 20px }` set through the page, not by editing product
  CSS for the test) proves the page has no horizontal scroll and every tab is reachable.
- **§6, Status's "fails every check · Re-sync · Park" line:** the data contract only, now. Phase 3
  builds the checks that fill it. Add the field to `SyncPanel` in
  `packages/ui/frontend/src/types.ts` and the view model (`packages/ui/src/recordings_ui/plaud.py`),
  read from whatever state Phase 3 will write (choose a `plaud_state` key name, document it in the
  view model's docstring and the spec's §15 where the panel is described, and make it an empty list
  when absent or unreadable, failing closed per the view model's existing pattern for corrupt rows).
  Render the line with its two buttons only if the list is non-empty; the buttons send the existing
  `plaud_request` kinds if they exist (`resync`, `park`), or are omitted until Phase 3 adds those
  kinds; say which in the report. Add `demo/plaud-sync.json` data only if it stays valid.
- **§7, doctor's "sign-in needed":** while no `make plaud-sign-in` exists it is a note and doctor
  exits 0: doctor's code, its test, `docs/runbooks/first-run.md` section 6's expected lines, and the
  `WORKER_PROBLEMS` list in `scripts/compose_smoke.py`. (Area A1, next, turns it back into a problem
  when it adds `make plaud-sign-in`, so write it so that change is one line.)

## 2. The phase-end list, sorted in one commit

The plan `docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md` ends with "## The phase-end list".
For every row not yet marked done:

- if the code already does it, mark it done with the commit that did it;
- otherwise append where it goes: `→ Phase 2`, `→ Phase 3`, or `→ Later (reason)`, using the Phase 2
  and Phase 3 outlines just above the list (each of those bullets is now a to-do for the overnight
  build). A row that is small, local and safe to fix now (a doc line, a wording) may be fixed now in
  its own commit instead.

Then in Task 25: tick step 4; mark steps 5 and 6 as replaced ("replaced by building Phases 2 and 3
straight through, Dan 2026-11-01, Q&A §8"); leave step 7 (CHANGELOG) and 8 (PR) open, since the
overnight build does them at its end. Set the plan's **Now** line to
`Now: Phases 2 and 3, built straight through (Q&A §8): area A1, open.`

## Commits and report

Commit trailer for this area: `Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Phase end`
(not BUILD-FAST's trailer, since this is still Phase 1's end). `make check` before each commit; the UI
change also needs `make test-js`, `make build`, `make e2e`.

If a test your change doesn't touch fails, rerun it alone; if it passes alone, note it in the report
(this machine runs heavily loaded) and go on. Never weaken a test to pass.

Write your report to `.superpowers/sdd/2026-10-23-stage-2b-phase-1/reports/p1-wrap-report.md`
and return only: status, commits, a one-line test summary, concerns.
