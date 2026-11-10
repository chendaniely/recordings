# Area B2: Re-sync, parking, Sync everything, and held removals: report

Status: **DONE_WITH_CONCERNS** (everything in B2's scope is built, tested and documented; the
readings past the spec's letter all fail closed and are rows in the guesses file; with it the
worker handles every request kind. The concerns are three questions for Dan, below, and the
phase-end refactors A6 listed, which B2 didn't need to touch).

Branch `stage-2b`, from `f09f052`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `df0df52` | feat(plaud): 🤖 Re-sync, parking, Sync everything and held removals, with their screens |
| `694b6a9` | test(demo): 🤖 make demo-live renames speakers and fails a recording; e2e-live re-syncs and parks |

Both messages passed `scripts/commitmsg.py` and carry BUILD-FAST's trailer. Before the first:
`make check` green (pytest twice, Vitest, ruff, gitleaks) and `make e2e` green (a last CSS line
and its e2e assertion came after that `make check`, so `make lint`, `make test-js` and the whole
`make e2e` ran again on them; neither touches pytest). Before the second: `make check` and
`make e2e-live` green. After both, `make pages pages-test` passed too. The first commit is the whole feature with its screens, because the
handlers, the web checks and the UI pins move together (HANDLED widened makes Re-sync appear in
the selection bar, whose CSS had to change in the same commit for `make e2e` to stay green); the
second is the demo and the live e2e items, which `make check` doesn't run.

## What was built, by spec section and scenario

### §14.6 Re-sync (`plaud/resync.py`, kind `resync`, `recordings plaud resync <id>…`)

- **One recording and a selection, one request** (decision 6): each named recording checked now
  through B1's `Checker.check(…, parked_ok=True)`, the loop copied from `sync._sweep` (between,
  a consent stop, `CheckStopped`, `KeepOutWordsError`, a run-ending Plaud error). A parked one is
  tried, and a success unparks it.
- **Refused before any Plaud call:** before the first catch-up (the gate; `serve_request` also
  refuses it, `GATE_ALLOWED` unchanged), under a hold ("Held: see Status"), during a rate limit's
  wait, during a consent stop. Then the sign-in (`sync._check_sign_in`), then the listing: the
  worker's newest while one schedule interval fresh, else one listing, never recorded.
- **Replies (§14.6, §15.6):** "Up to date", "Updated: speakers", "Not checked: kept out", "Not
  checked: on the consent list", "Gone from Plaud", "Edit-sync paused: see Status", "Failed:
  Plaud unavailable" (a Plaud error's own fixed message, else "error: `<Class>`"), plus "Not
  checked: Plaud doesn't list it now", "Not checked: it didn't come from Plaud", "Busy: …",
  "Degraded: …". A selection: "25 checked: 7 updated (speakers 6, title 1) · 18 up to date", with
  paused, degraded, failed and "N not checked (kept out 1, no Plaud source 2)" counts after.
  Progress "Re-syncing" with done/total ("Re-syncing (12 of 25)" on screen). The result carries
  counts, `updated_ids`, `failed_ids`, `unparked`, `no_plaud` and `each` (each recording's own
  line), never a title.
- **§9.3's merge:** a second press merges into a queued `resync` (`requests._merge_resync`,
  `UPDATE … WHERE state = 'queued'`, up to 1,000), following it; "Already queued" when it adds
  nothing. `COALESCED` gains `resync`. Details shows its own recording's line from `each`
  (`lineFor` in `usePlaud.tsx`).
- **"The open list re-reads the changed rows in place, as a tag reply does":** `shiny_app.follow`
  reads the Library rows of a finished Re-sync's `updated_ids` through the tag store
  (`views.row`) and sends them as the reply's `rows`; `usePlaud`'s new `onRows` hands them to
  `useTagging`'s new `mergeRows` (`mergeReply` with no counts or tags). Titles there, as in tag
  replies' rows; CLAUDE.md says so.
- **The breaker** is evaluated after a Re-sync, as after the sweep (its checks log as any check).
- **C2.01's Re-sync half** (12 recordings, 5 renamed, one Re-sync shows them all at once), **C5.23**
  (a Re-sync then a restart doesn't read as rebuilt), C9.05's worker half.

### §14.6 Parking a recording (`park`/`unpark` with recording IDs, `recordings plaud park <id>… [--undo]`)

- `resync.park_recordings`: `plaud_checks.parked_at` for recordings here that came from Plaud
  and aren't gone; unparked, `next_check_at` is now and the failure count stays. Only state.db
  changes. Web's `check_request` takes recording IDs for park and unpark (never with Plaud IDs).
- Its effects were already in place by B1 and A6 (the sweep's `covered`, the checker, the
  failing list, the Sync screen's Parked rows with Unpark); Sync everything counts parked as fine
  and the manifest marks it `parked`.
- **Status's "fails every check · Re-sync · Park" is wired** (P1 wrap's line): both kinds are
  handled now, and the line's buttons act on every recording it counts, one press (the earlier
  ruling, kept; a question for Dan below).

### §14.6 Sync everything (`plaud/full.py`, `plaud/fullstate.py`, kind `full`, `recordings plaud sync --full`)

- **One run:** a person's sync (`run_sync(mode="sync", sweep_checks=False)`: the gate, a hold, a
  rate limit's wait, the consent list, the sign-in, the listing, privacy, the compare and its hold
  verdict, the imports of what is waiting, no cap); then every recording `sweep.covered` names,
  oldest check first, through B1's checker (seeding any with no row first); its failures once
  more; the breaker.
- **The verdict:** each listed Plaud ID is excluded, parked, flagged (here, but consent-listed or
  kept out late: named by archive ID), here and checked (degraded counts), or not: failed, not
  here, needs review, not checked. Exit 0 only when the listing was good and all are fine; 2
  otherwise; a run-level failure exits as a sync's would (69, 77, 78, 1). **C3.01–C3.04.**
- **Resume (C3.05):** `K_FULL.running` keeps the start; a SIGTERM leaves it unfinished, and the
  next run (the worker's tick, a press, or the CLI) skips recordings checked since it started.
  Any other error finishes it, failed (never a crash loop of resumes).
- **When:** a press (refused while one is queued: "Already queued"; while one runs: "Sync
  everything is running (412 of 1,234)"; within an hour of the last: "Sync everything ran less
  than an hour ago"; "Resuming (started 14:20)" while one is unfinished), the CLI, and the
  worker's tick (`Worker._full_if_due`): an unfinished one resumes; otherwise 30 days after the
  last finished one (or after the catch-up before any) while the schedule is on, never while the
  gate is closed, the sign-in paused, a rate limit waits, the keep-out word is bad, `plaud` is
  held, or the sync is retired. A failed one waits for next month or a press.
- **C5.26:** after a rebuild it leaves what needs review out of its imports, and counts it.
- **The manifest** (DAN-15): `<state>/manifests/plaud-<date>.json`, atomic, 0600, by the default
  zone's date: each Plaud ID with its archive ID, last snapshot, state and the part hashes of the
  newest complete snapshot without `title` and `speakers`; consent-listed and kept-out IDs not
  here by ID only. The worker makes the folder (0700) at its start.
- **The record:** `K_FULL = {"running": …, "last": …}`; `last` holds the line ("all 1,234 checked
  (2 parked, 1 degraded)", "1,230 of 1,234 fine: 3 failed, 1 not here", "failed (Plaud
  unavailable)"), `ok` (no run-level failure and no newly failing recording), the exit code,
  counts, the flagged IDs, the measured pace and the manifest. `ops_runs` gets a `plaud-full` row.
  It is never a blocker (C6.02 already pins it).
- **The estimate** ("about 40 minutes") from the last pace times the recordings covered now;
  `FULL_UNKNOWN` before any.

### §9.2 Long runs, the CLI's included

- `Worker.long_between`: `between_recordings`, then the light turn of a due scheduled sync
  (`scheduled_turn(light=True)`, under the run's hold, ending in `conclude_turn`, so it pings),
  then one queued Compare or one-recording Re-sync (`claim_next(inside=True)`). Never inside
  itself. Handlers take `plaud` through `Worker._hold`, which takes nothing while a long run of
  the same process holds it.
- Long runs: Sync everything (press, monthly, CLI), a Re-sync of more than 20 recordings, and the
  CLI's plain `sync`, `sync --full` and `resync`, through `Worker.for_cli` (never started: no
  heartbeat, no patterns pass, so it keeps no `private-unwritten` flag; its first light turn waits
  one schedule interval). This closes A5's "a long CLI sync serves no light turn".
- A request queued while Sync everything holds `plaud` answers "Waiting for Sync everything (412
  of 1,234)" (§15.6).

### §14.7 Held removals

`PlaudDB.held_removals()` reads every check row's `held_removals` (B1 writes them); Status's
`numbers.held` counts them and its condition says "3 changes from Plaud are held for your review
(the review list comes with stage 3a)."; `recordings plaud held [--json]` lists them by recording
ID, slot and reason. No Accept button.

### §15.2, §15.1, §15.3 (the screens)

Status: the failing line acts; `numbers.full` ("2026-10-01: all 1,234 checked (2 parked, 1
degraded)", or "running since 14:20 (412 of 1,234)"); `numbers.held`; the held condition. The
Sync screen: `full_estimate`; Sync everything's own reason (`full_wait`). Library and Details:
Re-sync drawn (it was gated on HANDLED); rows re-read in place; Details' own line.

**At 320 px** (the outline's "the Library and Details at 320 px, with Re-sync drawn"; the
phase-end list's row 23 and G5, both marked fixed in the plan): the Library's Refresh and its
header's "See Status" are 44 px targets with negative block margins, so the header stays 45 px
and the short-screen floor (92 px) holds; Details' tab row scrolls within itself, so the panes
never scroll sideways; the selection bar's buttons have a whole 20 px line, so a two-line bar (Re-
sync from Plaud in the 339 px desktop list column) is a whole 75 px and never hides the focused
row by a fraction (the one `make e2e` failure the new button caused). `.resync-long` and
`.plaud-details button`'s 44 px were already there.

### §17.2, §17.4, §17.5, decision 9 (operations)

- Doctor (`backup`, with a repository): `extra_paths` without `/state/manifests` (or the state
  folder's own `manifests`) is a warning.
- The runbook: section 6 step 3 adds `/state/manifests` (made first: `install -d -m 700`), the
  rollback takes it out and puts it back, "Restoring for real" restores it and remakes the folder
  (steps 3 and 6), and two new sections: "When a recording fails every check: Re-sync and Park"
  and "Sync everything, and DAN-15's last run" (the last run, `schedule_minutes = 0`,
  `recordings plaud retire`, the check deleted). The template's comment names the folder.
- **`retired_at`:** `recordings plaud retire [--undo]` (`K_RETIRED`): no scheduled turn, no
  monthly Sync everything; Status's summary says "Plaud sync retired on …: nothing more is
  fetched from Plaud (runbook, DAN-15)", Needs attention never asks to turn the schedule back on,
  and "isn't being pinged" never shows. A person's presses still work.

### Docs

README's stage 2b paragraph (Re-sync, Sync everything) and its keep-out paragraph (deleting or
renaming in Plaud doesn't remove it here; S9 not measured; the forgiving match and Release); the
user guide's "Re-sync from Plaud", "When a recording fails every check", "Sync everything",
"Changes held for your review", the Sync screen's More, Status's numbers and failing line, the
exit codes, and demo-live's new commands; main spec §9.1.1's on-demand and DAN-15 bullets; the 2b
spec's §14.6 "Built so" note, §9.6's `retire` row, the Scenarios rows (C2.01, C3.01–C3.05, C5.23,
C5.26, C9.05) and §21's e2e-live line, and Revisions lines; CLAUDE.md's request-kind recipe (long
runs, `_hold`) and the replies' rows; 13 guesses rows.

## Tests

- New: `test_plaud_resync.py` (13: one recording's lines, a selection with progress, the other
  lines, not from Plaud, the gate and the hold, the merge, a parked one unparked, Park and Unpark
  from the failing line, C5.23, C2.01's half, the CLI's resync/park/held, its gate, the phrases);
  `test_plaud_full.py` (13: C3.01 with the manifest and the pace, C3.02, C3.03, C3.04, an outage
  part-way, C3.05, C5.26, the press's refusals and the waiting notice, the monthly run and
  retire, the light turn and a one-recording Re-sync served inside, the constants `requests`
  copies, the manifests folder at start, the CLI's `--full` and `retire`); two view tests (Sync everything's numbers, estimate, button
  and held; the retired sync); a doctor test; a session test (a Re-sync's rows); two Vitest cases
  (rows merged once, `lineFor`); an e2e test at 320 px; two e2e-live tests; the flow test's
  Sync everything, manifest, Re-sync and `held` inside its leak scan.
- Pins moved: HANDLED is every kind (three files; the "no handler yet" tests now take a handler
  away to test the refusal), the doctor fixture's `extra_paths`, the runbook's manifests and
  rollback lines.
- Found by the tests while building: the verdict overwrote a run-level failure's exit code (fixed,
  with its test); `make e2e` caught the selection bar's fractional height once Re-sync was drawn,
  and then the tab row squeezed to 6 px by its own `overflow-x` (both fixed, the second with a
  height assertion in the 320 px test); `make e2e-live` caught that Details is its own tab.
- Last runs: pytest 2,494 passed twice (local and `TZ=UTC`), Vitest 462, ruff clean, gitleaks
  clean; `make e2e` 146; `make e2e-live` 14 (about 6½ minutes); `make pages pages-test` 10. The
  one warning is the existing `test_demo_guard.py` fork warning. No flaky test seen.

## Guesses (rows in `docs/superpowers/plans/2026-11-01-stage-2b-guesses.md`)

S9's delay note left out; Re-sync's refusals (hold, rate limit, consent stop) and its listing
reuse; the extra reply lines; the merge and Details' own line; Park's reach and Unpark's next
check; Sync everything importing as Sync now does; review rows counted as not fine; the monthly
run's first date and resume; Status's line versus the exit code; the manifest's file and shape;
what a long run is and serves, the CLI's first light turn; `retire`; held removals' slots.

## Questions for Dan

None changes a decision or a scenario. Worth a glance while testing:
1. **Status's failing line acts on every recording it counts** (P1 wrap's ruling, kept): one
   press of Park parks them all. Fine, or one row per recording with its own buttons?
2. **Re-sync under a hold** answers "Held: see Status" with no Plaud call (the newest listing is
   suspect then). Should Re-sync of one recording work through a hold instead?
3. **Sync everything and recordings that need review after a rebuild:** left out and counted as
   not fine, so DAN-15's last run exits 2 until you decide about each. Or should they count as
   fine ("waiting for your decision")?
4. Known, untouched: decision 16's gap (a new ID listed beside a still-listed excluded one).

## What's left

- **A6's phase-end refactors of `recordings_ui/plaud.py`** (→ Phase 2: `plaud_views.py`, fields
  instead of display text, conditions vs attention, P15, G2, the Sync screen's split, one e2e
  helpers module, `usePlaud`'s local pending entry): not done; B2's additions follow the existing
  shape (`_Read` readers, `_panel_from`).
- **A5's council items:** R1's second half (a running row whose end couldn't be written stays
  running until the next start), R3 and R6: not B2's code, still open.
- **Details' Plaud lines in the demos:** `recording_plaud` returns None in the demos, so the
  Pages demo shows no Details Re-sync; the selection bar's Re-sync shows, off with the demo's
  reason.
- A request queued while a long CLI run holds `plaud` still expires after an hour if it isn't one
  the run serves inside (A5's question 3).
- The 320 px fixes for G5 and row 23 have no test that fails without them (the e2e test was
  written after the fix; the tab row's height assertion was added after a regression it would
  have caught).
- From audio-router: nothing new; this area joins B1's checker and A5's run. Everything else is
  the spec's, with the guesses above.

## What B3 needs from me, by name

### The user guide and the setup page

The guide's new sections are "Re-sync from Plaud: one recording, or a selection", "When a
recording fails every check: Re-sync and Park", "Sync everything" and "Changes held for your
review"; the demo table has `speakers`, `fail` and `mend`. The runbook's new sections are "When a
recording fails every check: Re-sync and Park" and "Sync everything, and DAN-15's last run"; the
setup page's section 6 step must include `/state/manifests` in `extra_paths` and its `install -d`.

### The Pages demo's canned 2b view (`demo/plaud-sync.json`)

`demo_view` forces every button off with "Demo: nothing is synced" and `handled` from
`requests.HANDLED` (now every kind), so the Library's selection bar shows **Re-sync** (off). The
states the canned view should show, by key, for B2's parts:
- `sync.full_estimate`: "about 4 minutes" (already there); `sync.buttons.full`: the demo note.
- `sync.parked`: a recording row (`{"id": "<demo archive ID>", "kind": "recording"}`) beside any
  Plaud ID one, each with Unpark (off in the demo).
- `sync.updated`: one row whose `parts` include "removal held" (e.g. `["speakers", "removal
  held"]`), by a demo archive ID and its title.
- `panel.failing_checks`: one row (already there: the Apollo 13 recording), so the line "1
  recording fails every check (since …) · Re-sync · Park" shows, both off.
- `panel.numbers.full`: "2026-10-01: all 4 checked" (already there); try "2026-10-01: all 4
  checked (1 parked)" once `parked` has a row; `panel.numbers.held`: e.g. 1, with
  `panel.conditions` gaining "1 change from Plaud is held for your review (the review list comes
  with stage 3a)."; `panel.numbers.parked` matching `sync.parked`.
- Not for the canned view: the retired summary (DAN-15's end) and "running since …".
- A real one to start from: `make demo-live ARGS=--keep`, then `speakers` (and a Re-sync in the
  Library), `fail` then Park on Status, Sync everything under More, then `dump`.
