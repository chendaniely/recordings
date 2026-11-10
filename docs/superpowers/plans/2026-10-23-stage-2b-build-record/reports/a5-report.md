# Area A5: the sync run, the handlers, and the first catch-up: report

Status: **DONE_WITH_CONCERNS** (everything in A5's scope is built and tested against the fake;
the readings past the spec's letter all fail closed and are rows in the guesses file; one place
where my own first commit departed from §11.1 is fixed in the third; the sweep's part of the run is
B1's, and B2's kinds wait as before).

Branch `stage-2b`, from `d8735a7`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `fae0c6c` | feat(plaud): 🤖 the sync run behind the worker's scheduled and light turns |
| `74b1906` | feat(plaud): 🤖 Compare, Sync now, the imports and Park as requests, with coalescing |
| `fa53b79` | feat(plaud): 🤖 the first catch-up: sync --dry-run, --catch-up and sync, and their make targets |

Each message passed `scripts/commitmsg.py` and carries BUILD-FAST's trailer. `make check` was
green before each commit (pytest twice, Vitest, ruff, gitleaks); `make e2e` (145 passed) before
`74b1906` and `fa53b79`, which changed `recordings_ui/plaud.py`. `fae0c6c` changed no UI file.

## What was built, by spec section and scenario

### §11.1, §11.5's run rules, §11.7, §9.7: the run (`packages/core/src/recordings/plaud/sync.py`)

`run_sync(Run, mode=, scheduled=, named=, import_all=) -> SyncReport`, one function behind every
path: a scheduled turn and a light turn (`worker_turn`), Compare, Sync now, Import selected and
Import all (the worker's handlers), and the CLI's `sync`, `sync --dry-run` and `sync --catch-up`.
Its steps, in §11.1's order, use the earlier areas' pieces by the names their reports gave:
1. The gate (a sync or an import waits for the catch-up; `--catch-up` runs only while it is
   closed); a person's sync or import is refused while a hold is on ("Held: see Status") or a rate
   limit's wait runs ("rate limited until 14:40"), with no Plaud call (A2's suggestion).
2. The consent list read afresh, failing closed; 3. late consent listings in the CLI's syncs (the
   worker's tick does them itself).
4. The sign-in: `tokens.check_sign_in` (A1); a refusal ends the run "sign-in needed" (77);
   `TokenStoreUnsafe` 78; a busy `tokens.lock` 75. A person's run bypasses the pause (A1's
   handoff), since only scheduled turns check `signin.paused`.
5. The listing (`PlaudSource.list_all`, A3); a partial or bad one ends the run with its error's
   phrase, recording `signin.record_refused` or `health.record_rate_limit` (A1, A2).
6. Privacy from the listing (`privacy_from_listing`, A4), complete or partial, in every run but a
   Compare (§11.1: a Compare stops after classifying and writes nothing to the archive).
7. The compare (`compare.take_listing`, A3), with the disk's level. A sync's and the catch-up's
   record the hold's verdict; a Compare's, an import request's and a consent stop's never do.
   `worker.newest_listing` is set (Release reads it, A4).
8. A held listing imports nothing (its §15.6 phrase); a consent stop imports nothing (exit 78).
9. The imports (`importer.Importer`, A4), newest first; a scheduled run's cap of 20 inside the
   throttle's 10-minute budget (`throttle.budget()`, A2), skipping a failure's backoff
   (`next_import_at`); a person's run has none of those. `ImportStopped` stops the imports with its
   phrase (`STOPPED_DISK` sets the disk cause); `ListingStale` lists again (at most 12 times); a
   run-ending Plaud error (`ends_the_run`) ends the run and counts as an attempt without success;
   `BudgetSpent` leaves the rest for the next run. Between two recordings `Run.between` runs.
10. `SyncReport`: §15.6's phrase, §9.6's exit code, counts and IDs (never a title), `outcome()` for
    `conclude_turn` (§11.7). The disk at its stop margin with recordings waiting counts as the
    guard refusing them (C6.11).

The catch-up sets `catch_up_done_at` (A4's `K_CATCH_UP_DONE`) at a clean end: no run-level
failure; one recording's failure isn't one (J8 step 5). Its listing is the last good one.

### §9.2, §9.7, §16: the worker's turns (`worker.py`)

- The worker's default turn is the real run (`sync_turn`, which imports `plaud.sync` only when it
  runs: `test_importing_the_worker_brings_no_client_token_store_or_sync` still passes, and so does
  `test_plaud_imports.py`).
- **The phase-end list's Task 11 row:** a turn that raised reaches `conclude_turn` as an attempt
  without success (`raised`), never as None, so its failing streak is the work's and only a run
  with a success ends it. SIGTERM between recordings (`Stopping`, made public from `_Stopped`)
  stops a run as no failure: nothing recorded, nothing pinged.
- `between_recordings`: progress, SIGTERM, and the tick's privacy steps (the consent list, its
  blocker, the cache's titles, late consent listings, the `private-unwritten` flags) between two
  recordings of any run, so a long Import all never holds back a consent edit.
- Each scheduled run is in `ops_runs` (`plaud-sync`), its phrase as the detail.

### §9.3, §9.4: the handlers and coalescing (`worker.py`, `plaud/requests.py`, `recordings_ui/plaud.py`)

- `compare`, `sync`, `import`, `park`, `unpark` handled; `requests.HANDLED` widened in the same
  commit; CLAUDE.md's recipe walked for real (the handlers, HANDLED, `check_request`, the three
  pins) and extended with `worker.PLAUD_KINDS` and `requests.COALESCED`.
- `_sync_request`: a person's run under `plaud` (`plaud-compare`, `plaud-sync`, `plaud-import`), its
  progress on the row ("Importing", done, total), its reply §15.1's ("Compared: listed …", "Synced:
  2 imported (1 private) · All here", "31 imported · 4 arrived private", "Held: see Status"), its
  result the run's counts and IDs. A request never pings or records a turn.
- **Coalescing** (§9.3, "one 'Already queued' per kind"): a press a queued request already covers
  gets its own row that *follows* that one (`result` {"follows": id}): never claimed, it runs and
  ends with its leader, so every press sees the one run's end. A sync upgrades a queued compare and
  follows it ("Queued"); a compare or sync while a sync is queued, or a compare while a compare
  is, is "Already queued"; imports queued together merge. Orphaned followers expire.
- **A request waits its turn** (§9.4): while another run holds `plaud`, the worker leaves Compare,
  Sync now, the imports and Accept queued and serves the other kinds. This replaces A3's "Accept
  answers busy" (its test and guess row updated).
- Park and Unpark of a Plaud ID that failed to import (`plaud_listing.parked_at`); a recording's
  parking (its checks) is B2's: web and the worker answer NOT_YET for recording IDs.
- Web's `check_request`: Compare and Sync now carry nothing; an import carries Plaud IDs or `all`;
  Park and Unpark carry Plaud IDs.
- **The phase-end list's Task 14 row** (A3 passed it on): the deadline check before the commit has
  its test (`test_a_deadline_that_passes_during_the_insert_rolls_it_back_before_the_commit`),
  `MAX_PLAUD_ID_LEN`'s comment, `claim_next` reads its row by column name. **The council's R1**,
  first half: a request's end is written again, a moment apart, if `state.db` is busy.

### §11.6, §9.6, §9.4, §17.5: the first catch-up (`plaud/commands.py`, `sources/audio_router.py`, `Makefile`, the runbook)

- `recordings plaud sync --dry-run [--list]`: a Compare under `plaud` (`plaud-compare`): both
  equations, the missing ones by reason, the census, repeats and malformed rows, the consent list's
  lines read as kept, any hold and why Accept would be refused, and "the first catch-up would
  import N"; `--list` adds every listed ID by bucket, date, time and duration. Imports nothing,
  writes nothing to the archive, never moves the last good listing.
- `recordings plaud sync --catch-up`: `ops` and `plaud` in one hold (`plaud-catch-up`), no cap,
  refused once done ("already done (date): use `recordings plaud sync`"), stops on a hold (then
  `accept-listing` and run it again), one progress line per recording on stderr, by number only.
  C5.19 (its gate state, A4's) and C5.20 run end to end.
- plain `recordings plaud sync`: refused before the catch-up and while a hold is on; no cap.
- §9.6's exit codes, `--json` (counts, IDs, phrases), `ops_runs` rows (`plaud-catch-up`,
  `plaud-sync`).
- The audio-router import holds `plaud` beside `ops` (§9.4), announced on both; its CLI's peek asks
  both; Status's `JOB_LABELS` names it.
- `make plaud-dry-run` and `make plaud-catch-up`: run on the server in its checkout, through
  `docker/up-deployed.sh` (the runbook's `rc`); the catch-up in §11.6's order: the backup service
  stopped, a `pre-import` backup, `plaud baseline`, `sync --catch-up`, `validate /archive --deep`, a
  `post-import` backup, and the backup service started again by a trap however it ends; each
  failure names its step; the catch-up's exit 2 goes on. Tested against a stand-in
  `up-deployed.sh` only: never Docker, the server or Plaud.
- The runbook gains "## 7. The dry run" and "## 8. The catch-up" (where the deploy guard's "run it
  again by hand" now points, the phase-end list's Task 18 row), three checklist rows, and a line
  for a turn that raised; its intro is true again. The README gains both targets.

### Docs

The user guide gains "The first catch-up: dry run first", "What the sync commands' exit codes
mean", "The scheduled sync" and "The Sync screen's buttons". Fourteen A5 rows in the guesses file
(and A3's Accept row marked superseded). Spec: Scenarios rows C5.09, C5.12, C5.20, C5.25, C6.04,
C6.05 and C6.11 built, three Revisions lines. CLAUDE.md's request-kind recipe.

## Tests

- New: `test_plaud_sync.py` (13: the run through the worker's real turn against the fake: C5.09
  and C5.12's run halves, C6.04 both ways, C6.05, C6.11, the 40-recording backlog over two runs,
  the backoff, a rebuild's review rows left out, a turn that raised, SIGTERM, the light turn),
  `test_plaud_sync_requests.py` (10: each handler, coalescing, waiting its turn, progress, Park,
  a Compare writing nothing), `test_plaud_sync_cli.py` (9: the dry run and its list and JSON with
  no title, the catch-up under `ops` and `plaud`, interrupted then finished then the schedule
  starting, held then accepted, the refusals, every exit code), `test_catch_up_targets.py` (9);
  `test_plaud_requests.py` +3, `test_plaud_requests_ui.py` +1, `test_import_run.py` +1 (the
  `plaud` lock), `test_runbook.py` +1. `packages/core/tests/plaud_live.py` (the `live` fixture): a
  worker against the fake with a sign-in, a consent list, a fake clock and Healthchecks.
- Changed pins: HANDLED (core, web, the views, the session test's unhandled kind), A3's Accept busy
  test (now waits its turn), the runbook's headings.
- Mutation checks: the Compare guard (`mode != "compare"` removed: the new test fails); the disk
  rule and the backoff test each failed before their code was right (seen while building).
- `make check` before the last commit: pytest 2,388 passed (and again under `TZ=UTC`), Vitest 458,
  ruff clean, gitleaks clean; `make e2e` 145 passed. The one warning is the existing
  `test_demo_guard.py` fork warning. No flaky test seen.

## From audio-router, and from the spec alone

Nothing new from audio-router: the run joins A1 to A4's pieces, which carry what audio-router
showed. The rest is the spec's, with these readings (each a guesses row, each failing closed):
newest first under the cap; a consent stop's listing never a baseline; the disk at its margin with
recordings waiting counts as refused; a turn that raised is a work streak; SIGTERM between
recordings is no failure; the tick's privacy steps between recordings; what each person's press
imports; coalescing as followers, and imports merging; a request waiting while `plaud` is held;
a person's press refused during a rate limit's wait; Park for a Plaud ID only; the make targets'
shape; the exit codes' mapping; the dry run counting the catch-up's recordings as missing.

## Questions for Dan

None that change a decision or a scenario. Three readings worth a glance while testing:
1. **Sync now and "failed 3 times":** Sync now (like a scheduled sync) leaves a recording that
   failed 3 times alone; Import all and Import selected try it. Keep, or should Sync now retry
   them too?
2. **The dry run's numbers before the catch-up:** the ~30 recordings the catch-up will bring show
   as "missing 30: waits for the first catch-up" (the equation says "waiting 0"), with "the first
   catch-up would import 30" beside it. §17.5 words the check as "waiting and missing". Fine, or
   should the dry run count them as waiting?
3. **A request pressed during a long CLI run** (the catch-up) waits, queued, and expires after an
   hour like any queued row, saying "the Plaud worker didn't run it within the hour" though the
   worker was up. Acceptable, or should a request waiting for `plaud` never expire?

The known open question (decision 16's gap, a new ID listed beside a still-listed excluded one) is
untouched, as the brief said.

## What's left

- **B1:** the sweep, the companion check and the breaker join the run at step 9's end (below).
- **B2:** `--full`, `resync` and a recording's Park; Sync everything's light turns.
- **The council's R1, second half:** a running row whose end couldn't be written after three tries
  stays `running` until the next start (its session keeps following it); the worker-down notice
  for a running row isn't built.
- **The council's R3** (build before the deploy guard) and **R6** (wall-clock bounds in the request
  tests) were "Phase 2" rows not named for A5; not done.
- **A CLI `sync` that runs long** (a big backlog, no cap) serves no light turn: scheduled turns are
  skipped as busy meanwhile, and past 2 hours the check would email. The catch-up is unaffected
  (the gate holds every turn while it runs). §9.2's light turns inside long runs come with B2.
- **A turn that raised** shows on Status as "the sync is failing" (the work's streak), not as
  "error: `<Class>`"; the runbook's row says where the class is (`rc logs worker`).
- **CLAUDE.md** doesn't yet name the `live` fixture beside `plaud_server` and `scene`; A6's flow
  test is its first other user.

## What A6 and B1 need from me, by name

### A6 (the views, the flow test, `make e2e-live`, `make demo-live`, the runbook)

- **A live worker:** `packages/core/tests/plaud_live.py`'s `Live` (the `live` fixture in
  `packages/core/tests/conftest.py`): an archive, a config.toml on the fake's API, a consent list
  (`live.consent_ids(*ids, extra=…)`), a sign-in installed through the fake's token endpoint, the
  sync's ping on the fake Healthchecks (`live.pings()`), a fake clock (`live.clock.advance(…)`),
  the fake's account (`live.account`), `live.started()`, `live.catch_up_done()`, `live.here()`
  (canonical Plaud ID → recording ID), `live.fetched(plaud_id)`. Only `Live.__init__`'s
  `monkeypatch` is pytest's (it sets `importer.SETTLE_SECONDS` to 0); `make demo-live` can do the
  same by hand. The CLI against it: see `test_plaud_sync_cli.py`'s `cli` fixture (a long-lived
  sign-in, because the CLI's token store reads the real clock).
- **C4 with the real turn:** the worker's default turn is now the real run, so the flow test can
  re-prove C4.01 to C4.04 with `live` and no stand-in.
- **The run by hand:** `plaud.sync.run_sync(Run(writer=, client=, settings=, timezone_name=),
  mode="compare" | "sync" | "import" | "catch-up", scheduled=, named=, import_all=)` →
  `SyncReport` (`.to_dict()`, `.outcome()`, `sync.summary(report)` for a log line). The worker's:
  `sync.worker_run(worker, **overrides)`, `sync.worker_turn(worker, light=)`.
- **What the requests write** (`plaud_requests`): Compare's "Compared: listed N = here … + waiting
  … + excluded … + missing …" with `result.equation`, `archive`, `consent`, `titles_hidden`; Sync
  now's "Synced: …" lines; an import's "31 imported · 4 arrived private" and its running message
  "Importing" with `progress` {done, total}; `result.recording_ids` and `failed_ids`; Park and
  Unpark's `result` {"parked" | "unparked": [Plaud IDs], "nothing": […]}. Followers (coalesced
  presses) carry `result` {"follows": id} while queued.
- **Readers that now have data:** `plaud_listing.parked_at` for the Sync screen's Parked rows
  (`ParkedRow` kind "plaud", `id` the Plaud ID; Unpark already sends `plaud_ids`), and
  `NotHereRow.park` for `state == compare.FAILED`. Under a hold (`K_HOLD`) the Sync screen should
  disable Sync now and the import buttons with the hold's line: the worker refuses them anyway
  ("Held: see Status"), but the buttons don't say so yet.
- **ops_runs:** `plaud-sync` and `plaud-catch-up` rows with the §15.6 phrase as `detail` and `ok`
  from `health.judge_run`; `K_LAST_TURN.detail.result` is the run's phrase.
- **The runbook:** sections 7 and 8 are written (with `make plaud-dry-run` and `make
  plaud-catch-up`); A6's list still has section 6's sign-in step, "Plaud sign-in", and the hold's and
  the consent stop's recovery in full.

### B1 (checks, the sweep, the breaker)

- **Where the sweep joins the run:** `plaud/sync.py`'s `_one_listing`, after `_import_rows` (step 9)
  and before `_final_phrase`: a chunk of the sweep, the companion check (§11.7), the breaker
  (§14.4). Their attempts and successes add to `SyncReport.attempts` / `.successes` (a degraded
  check is an attempt without success); `run.between()` before each check; `run.client.throttle
  .spent()` stops at the budget; a Compare and a held listing return before step 9, so the sweep
  pauses with the imports (§11.4 step 3). A light turn (`worker_turn(…, light=True)`) runs the
  sweep's light part only.
- **`Writer.add_snapshot` must call `gate.mark_snapshot` first** (A4's note still stands).

### B2 (Re-sync, parking's effects, Sync everything)

- `recordings plaud sync --full` joins `cmd_sync`'s mutually exclusive group; the `full` and
  `resync` handlers follow `worker._sync_request`; `worker.PLAUD_KINDS` and, for the resync merge,
  `requests.COALESCED` / `_coalesce` take them; `check_request` then lets `park` and `unpark` carry
  recording IDs (NOT_YET today) and `_parking` sets `plaud_checks.parked_at` for them. Status's
  failing-checks Park button is enabled now that `park` is handled; it only appears once checks
  write failures, and until B2 a press answers NOT_YET.
- Sync everything's light turns: between recordings, when a turn is due, `worker.turn(worker,
  light=True)` then `conclude_turn(worker._ctx, outcome)` (§9.2); a long CLI `sync` could do the
  same.
