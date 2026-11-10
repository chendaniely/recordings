# Area B1: checks, the sweep, and the breaker: report

Status: **DONE_WITH_CONCERNS** (everything in B1's scope is built, tested and documented; the
readings past the spec's letter all fail closed and are rows in the guesses file; a few small
pieces are left, below, none reachable half-built).

Branch `stage-2b`, from `c44afa6`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `e94367b` | feat(plaud): 🤖 edit-sync: the check, the rolling sweep and the circuit breaker |
| `1da2d82` | feat(plaud): 🤖 baseline seeds check state, check --volatility, and breaker reset |
| `6554a3b` | feat(ui): 🤖 the Sync screen and Status show the breaker, the sweep and the checks |
| `f09f052` | test(demo): 🤖 make demo-live trips the breaker, e2e-live accepts it, the flow test checks |

Every message passed `scripts/commitmsg.py` and carries BUILD-FAST's trailer. `make check` was
green before each (pytest twice, Vitest, ruff, gitleaks); `make e2e` (145 passed) before the
first three (each touched `recordings_ui`); `make e2e-live` (12 passed) before the fourth.

## What was built, by spec section and scenario

### §14.1, a check: `plaud/editsync.py` (`Checker.check`)

- **Never checked** (no Plaud call, not an attempt): any of its Plaud IDs on the consent list
  (read again before each check through the run's `ConsentGuard`) or kept out
  (`plaud_listing.kept_out_at`, an open `kept-out-late`/`consent-late` flag, Keep it here's
  `kept_here_at`), gone, parked (unless `parked_ok`: Re-sync's door for B2), or no Plaud ID in
  the newest listing ("not listed"). **C5.03 is whole:** nothing of a late keep-out is fetched
  again, by an import or a check.
- **The fetch, outside every lock:** the envelope bare (`enrich=False`) with a listed ID in the
  form listed; its `id` must be this recording's; the consent list again; its title through
  §13.1 exactly as the listing's (`privacy_from_listing`, a no-op when the marker has seen it):
  a private title first seen here is tagged **before** the snapshot lands, and a keep-out word in
  the envelope alone is a late keep-out that stops the check before any block (nothing
  written). Then `enrich`; link errors refetch the whole envelope once; still link errors =
  **degraded**.
- **Compare** with the newest complete snapshot of that Plaud ID, both sides through
  `part_hashes`; parts named title, transcript, speakers, notes, own notes, other.
- **Degraded:** its snapshot kept once (not when an earlier one of that ID is the same,
  normalised), never a baseline (reconcile skips it); an attempt without success.
- **Changed, settled, past its baseline:** fetched again at once (§14.4); a changed part whose
  hash differs between the two fetches is volatile; the second fetch is written.
- **Counted** (only "other" changed, or a part volatile; never `baseline_pending`) and
  **suppressed** (breaker open and the change only in "other" or volatile parts: no snapshot,
  scheduled as unchanged).
- **Written** through the new `Writer.add_snapshot` (the snapshot mark first, A4's note; the
  file under the recording's lock; its `SourceRef` through `mutate` with origin
  `plaud-check:<run>`), `_meta` the import's stamp.
- **Always reconciled** after (held removals into `plaud_checks.held_removals`); a reconcile that
  fails is repaired by the next check (tested).
- **Recorded:** `plaud_check_log` (`PlaudDB.log_check`; an "other" change's field names as
  `other:<name>` entries in `parts_changed`, `CheckLog.fields`), the schedule, and an `updated`
  event with its parts (plus "removal held" when reconcile holds a new removal).
- **Failures:** tried once more, then counted (`failures`, `failing_since`, backing off): 3 in a
  row and `PlaudDB.failing_checks()` lists it, so Status's line (P1 wrap) has data. Busy is
  neither. A run-ending Plaud error is raised for the run; an archive-level error is
  `CheckStopped`.
- **`validate`'s orphan warning:** `selfdoc.orphan_snapshots`; `recordings validate` prints
  `warnings` without changing its exit code; FORMAT.md (and the demo copy) says so.

### §14.2, the rolling sweep: `plaud/schedule.py` (pure) and `plaud/sweep.py`

- Intervals: unsettled (the schedule interval for 2 hours after import, then hourly); settled
  doubling to `full_sweep_days`; after a change two at the interval, then min(6 h, cap), then
  doubling; failures doubling separately.
- The plan: covered recordings (`sweep.covered`), seeded first if they have no row; extras
  (unsettled, or marked by this run's listing: `Compare.marked`); the cold sweep most overdue
  first up to ceil(1.5 × N / (days × runs/day)); **the companion check (§11.7)** first when only
  failing-list recordings would be tried.
- **Listing changes:** `PlaudDB.write_listing` compares each here-ID's stored title hash and
  duration with the new ones in its own transaction and sets those recordings' `next_check_at`
  to the listing's time (first sight is no change); it returns them, `take_listing` puts them on
  `Compare.marked`.
- **The cap after a 429** (A2's handoff): `run.client.throttle.rate_limits` before vs after the
  run (or a run ended rate limited) halves it; good runs grow it back by one (`sweep.K_SWEEP`).
- **The run** (A5's slot): `sync._sweep` after `_import_rows`, in mode `sync` only (never a light
  turn, a Compare, an import or the catch-up), `run.between()` between checks, the budget, then
  `sync._breaker`, then the cap. A check is an attempt; a degraded one isn't a success; every
  check failing reads "every fetch failed". `worker_turn(light=True)` checks nothing.
- **§14.3:** S5/S9 skipped; audio-router says Plaud has "no change signal of any kind", so step 3:
  `change_marker` stays empty (spec §14.3 says so).

### §14.4, the breaker: `plaud/breaker.py`

Window `max(now − 24 h, window_start)` (checks at the very instant of an Accept or reset left
out); trips at ≥ 10 counted recordings and > 5% of those checked; `K_BREAKER` record and the
`breaker` blocker; Accept offered only for stable "other"-only fields never accepted before
(`again` otherwise); `accept_changes` = rebaseline (every row's next check excused and spread over
`full_sweep_days`) then close with the fields kept; `close_on_release` at the worker's start
(`Worker._close_on_release`); `reset`; `view` (SyncBreaker) and `closed_line`. The run says
"paused: unexpected changes" while it is open and nothing else went wrong.
- **Accept these changes** as a request: `worker._accept_changes` (holds `plaud` as the new job
  `plaud-breaker`; the press in `ops_runs` as `plaud-accept-changes`), `requests.HANDLED`,
  `worker.PLAUD_KINDS` (waits its turn), web's `check_request` (carries nothing), the pins.
- **`recordings plaud breaker [reset]`**, **`recordings plaud check --volatility`** (three
  recordings, none private, kept out or consent-listed, twice a minute apart, bare envelope first,
  writes nothing, exit 1 when a part differs; job `plaud-volatility`).

### §14.5, the baseline

`recordings plaud baseline` seeds check state (last checked = newest snapshot's fetch time,
next spread over `full_sweep_days`, `baseline_pending`) and prints "check state seeded" /
"already seeded"; `--all` rebaselines. The sweep seeds automatically any covered recording with no
row (a rebuilt or restored `state.db`: tested, nothing trips). An import is its own baseline
(`baseline_pending`), and is settled at once when recorded 48 h or more before.

### The screens (A6's names): `recordings_ui/plaud.py`, the frontend

`_Read.breaker_view()` on both screens; Status's condition while open and Needs attention's
"Edit-sync is paused since …"; the release's "closed by the release on …" line for a day;
`numbers["sweep"]`; `details["changes_24h"]` and `["fields"]`; Details' `checked` and `next_by`;
`SyncView.checked_week` and Updated's "Nothing changed on Plaud this week." (the phase-end list's
row 21); `SyncBreaker.note`. `details["named_share"]` stays null.

### The live demo, e2e-live, the flow test

`make demo-live`'s `breaker` command; `make e2e-live`'s breaker item (A6 named it for B1); the
flow test's check pass (a speaker rename and a new field through the worker's real turns; the
leak scan now covers `plaud_check_log` and the speaker's name and the field's value).

### Scenarios

C2.01 (the sweep's half: N = 1,000 on an injected clock, 40 renames each found within
`full_sweep_days` + one interval; Re-sync's half is B2's), C2.03, C2.04, C2.05, C2.06 built;
C5.03 whole; C6.04's checks half. The spec's Scenarios rows, §14.3 and §21 say so, with five
Revisions lines.

## Tests

- New: `test_plaud_editsync.py` (23), `test_plaud_sweep.py` (19), `test_plaud_breaker.py` (9:
  C2.03–C2.06 through the worker's real turns, the window, reset), `test_plaud_edit_cli.py` (5),
  three view tests in `test_plaud_views.py`, two Vitest cases, an e2e-live test, the flow test's
  check pass. Changed pins: HANDLED (three files), the baseline's JSON, the import's check row.
- Mutation checks (each guard removed, its test failing): the kept-out skip, suppression, the
  companion check, the baseline excuse, Accept for a volatile part, listing changes.
- Last runs: pytest 2,464+ passed twice, Vitest 460, ruff clean, gitleaks clean; `make e2e` 145;
  `make e2e-live` 12 (about 6 minutes). The one warning is the existing `test_demo_guard.py` fork
  warning. No flaky test seen.

## From audio-router, and from the spec alone

From audio-router (`src/audio_router/archive/sources/plaud.py`, read only): "no change signal of
any kind -- no updated_at, no etag", for §14.3. Everything else is the spec's, with these
guesses (15 B1 rows in the guesses file): §14.3 closing at step 3; a degraded snapshot kept once
and scheduled as unchanged; the double fetch only for a settled recording past its baseline; the
5% denominator without failed checks; field names as plain keys ("?" otherwise); the check judging
the envelope's title as the listing's; the companion first; an import its own baseline and
settled by age; the extras; a suppressed check a success, and the run's phrase while open; the
`plaud-breaker` job; field names in `parts_changed`; the volatility check's order and wait; what
the baseline seeds; Status's sweep estimate and the empty named share; Updated's empty sentence;
the demo's `breaker` command.

## Questions for Dan

None that change a decision or a scenario. Readings worth a glance while testing:
1. **Volatility on new recordings:** the double fetch runs only for settled recordings past their
   baseline (§14.4 says "a settled recording"). A field that changes on every fetch, met first on
   recordings still unsettled, trips the breaker *with* Accept offered (it looked stable);
   accepting it re-trips at once with "needs a code fix". Double-fetch every change instead (more
   calls), or keep it?
2. **A degraded check keeps its snapshot once** (deduped by normalised hash) and never puts a
   recording on the failing list, though "every link refused" still stops the pings. Keep, or
   should a recording degraded 3 times in a row join the failing list (and Park)?
3. **Accept these changes** spreads every recording's next check over `full_sweep_days`; the
   recordings already written with the new field before the trip find nothing new, so "each gets
   one new snapshot" holds for those that still need one. Fine?
4. **The check judges the envelope's title** with the listing's own rule, moving the marker to it:
   a listing and an envelope that keep disagreeing on a title flip the marker each run (§13.2's
   accepted A→B→A, erring private). Fine?
5. Known, untouched: decision 16's gap (a new ID beside a still-listed excluded one).

## What's left

- **Status's "share of named segments"** (§15.2's details): null; nothing measures it cheaply yet.
- **Held removals:** the check writes `plaud_checks.held_removals` and an Updated part "removal
  held"; counting them on Status and `recordings plaud held` are B2's (§14.7).
- **Re-sync, parking a recording, Sync everything:** B2's, as the dispatch said.
- The Library and Details at 320 px with Re-sync drawn: B2's.

## What B2 and B3 need from me, by name

### B2 (Re-sync, parking, Sync everything, held removals)

- **One check:** `plaud.editsync.Checker(writer=, db=, client=, consent=<importer.ConsentGuard>,
  timezone_name=, run_id=, schedule_minutes=, full_sweep_days=, breaker_open=breaker.is_open(db),
  clock=)`, then `.check(recording_id, {row.canonical_id: row for row in listing.rows},
  parked_ok=True)` for a Re-sync (a parked one is tried; on success B2 unparks it).
  `CheckResult`: `.result` (`UP_TO_DATE`, `UPDATED`, `DEGRADED`, `SUPPRESSED`, `FAILED`, `BUSY`,
  `NOT_CONSENT`, `NOT_KEPT_OUT`, `GONE`, `PARKED`, `NOT_LISTED`), `.parts`, `.volatile`,
  `.attempt`, `.success`, `.phrase()` (§15.6's "updated: speakers, title", "failed: <Class>",
  "not checked: kept out", …). It raises run-ending `PlaudError`s, `editsync.CheckStopped`,
  `importer.ImportStopped` (consent stop) and `KeepOutWordsError`, as `sync._sweep` handles them;
  copy that loop for `resync` and `--full`. Re-sync's replies map `.result` to §14.6's lines
  ("Edit-sync paused: see Status" is `SUPPRESSED`).
- **Sync everything:** `sweep.covered(here, rows, db, consent_ids=)` is what it must check;
  `schedule.after_check` does the scheduling inside the check; degraded counts as checked.
  `PlaudDB.check_rows()` and `check_log_since()` for the manifest and its state.
- **Parking a recording:** `plaud_checks.parked_at` (the checker and `sweep.covered` already skip
  it; `PlaudDB.parked_recordings()` and the views already read it).
- **Held removals:** `CheckRow.held_removals` is a tuple of `{"snapshot", "slot", "reason"}`,
  rewritten by every check's reconcile (None when it couldn't run): count them for
  `numbers["held"]` and `recordings plaud held`.
- **e2e-live's Re-sync item:** `scripts/demo_live.py`'s `Demo.due_for_checks` and the fake's
  `FakeAccount.rename_speaker(plaud_id, name)` are there to reuse.
- The C5.23 restart test: a Re-sync's snapshot goes through `Writer.add_snapshot`, which commits
  the mark first, so a restart doesn't read as rebuilt.

### B3 (the guide, the setup, the Pages demo)

- The user guide has "Edits you make in Plaud: the checks" and "When edit-sync pauses: the
  breaker, and Accept these changes", the Sync screen's and Status's items, and demo-live's
  `breaker` row; the runbook has "When edit-sync pauses: the breaker" and section 8's volatility
  step.
- **The canned view** (`demo/plaud-sync.json`): `sync.checked_week` is now a key (true); a
  `breaker` object (`sentence`, `fields`, `accept`, `code_fix`, `note`), `numbers.sweep`,
  `details.changes_24h`/`fields` can be filled by hand; `make demo-live ARGS=--keep`, `breaker`,
  `tick`, `dump` gives a real one to start from (`demo_view` forces Accept off).
