# Task 13 report: the worker process

**Status:** DONE_WITH_CONCERNS (all minor; nothing blocks the review)
**Base:** `5f30cb9` (recorded in `task-13-base`)
**Commit:** `8ecb3d7` feat(worker): 🤖 recordings worker: the tick, the heartbeat thread, scheduled turns and --alive; late consent listings at every tick

## What I implemented

The brief's Steps 1 to 8, in order, as one green cycle and one commit (the brief has one):

- `packages/core/src/recordings/worker.py`: Task 11's `TurnContext` and `conclude_turn` and its
  docstring paragraph kept; the brief's "The process (Task 13)" paragraph added after the first
  docstring paragraph; the brief's imports (then `ruff check --fix`); the brief's process code
  appended below `conclude_turn`: `TICK`, `HEARTBEAT`, the `ALIVE_*` limits, `Phase`, `SILENT`,
  `STUCK`, `FAILED`, `HEARTBEAT_KEY`, `PRUNE_EVERY`, `WRITER_LOCK_WAIT`, `TURN_LOCK_WAIT`,
  `INDEX_PATIENCE`, `INDEX_UNAVAILABLE`, `Progress`, `SyncTurn`, `stand_in_turn`,
  `heartbeat_once`, `alive`, `_starting_since`, `_file_key`, `Worker` (start, run, tick, the
  consent watch and blocker, late consent, the owed-private step, due, scheduled turns, prune) and
  `main`.
- `packages/core/src/recordings/cli.py`: `recordings worker [--alive]` (import, subparser,
  `cmd_worker`, the `commands` table entry), as Step 4.
- `packages/core/tests/test_worker.py` (new): the brief's 29 tests, plus 1 (see Adaptations).
- `CLAUDE.md`: the Archive rules' first bullet, as Step 6.

## TDD evidence

One cycle, one commit.

**RED (Step 2):** `uv run pytest packages/core/tests/test_worker.py -q`

```
E   ImportError: cannot import name 'FAILED' from 'recordings.worker' (<the stage-2b worktree>/packages/core/src/recordings/worker.py)
ERROR packages/core/tests/test_worker.py
1 error in 0.20s
```

As the brief expects: the module doesn't collect, so all 29 fail first.

**GREEN, with the brief's code as written (Steps 3 and 4):** same command

```
29 passed in 19.73s
```

**RED for the one behavioural adaptation** (`_still_owed`, see below). I added
`test_a_folder_under_a_parent_it_cant_enter_is_never_read_as_gone` and ran it against the brief's
code (`path_for(...).exists()`):
`uv run pytest packages/core/tests/test_worker.py -q -k "cant_enter"`

```
>           assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
E           AssertionError: assert 0 == 1
FAILED packages/core/tests/test_worker.py::test_a_folder_under_a_parent_it_cant_enter_is_never_read_as_gone
1 failed, 1 passed, 28 deselected in 1.13s
```

**GREEN after the adaptation:** same command, `2 passed, 28 deselected in 1.09s`; the whole file
`30 passed in 20.84s`; after the last test change, three runs in a row: `30 passed` each
(28.58 s, 20.99 s, 21.69 s).

## `make check`

Run twice, the second time on the final tree (after the last test-only change), both exit 0:

- ruff: `All checks passed!`, `145 files already formatted`
- pytest: `1789 passed, 141 deselected, 1 warning` (the warning is the existing fork
  DeprecationWarning in `test_demo_guard.py`)
- Vitest: `Test Files 34 passed (34)`, `Tests 378 passed (378)`
- pytest under `TZ=UTC`: `1789 passed, 141 deselected, 1 warning`
- gitleaks: `no leaks found`

No UI test failed, so nothing was rerun.

## Files changed

- `packages/core/src/recordings/worker.py`
- `packages/core/src/recordings/cli.py`
- `packages/core/tests/test_worker.py` (new)
- `CLAUDE.md`

## Adaptations to Tasks 1 to 12 as built

1. **`_still_owed` uses Task 8's `flags._gone`, not `Path.exists()`** (behavioural). The brief's
   `return self.writer.archive.path_for(event.recording_id).exists()` reads a recording as gone
   when its folder sits under a parent the worker can't enter: Python 3.14's `Path.exists()`
   returns False on any OSError. That closes the `private-unwritten` flag, and then the blocker,
   while the `private` is still owed. Task 8's "Built differently" fixed exactly this in
   `flags.py` (`_gone`: only a `stat` that says "not found" counts). The worker now does
   `return not _gone(self.writer.archive.path_for(event.recording_id))`, importing `_gone` from
   `recordings.plaud.flags` so the rule lives in one place. Pinned by the added test
   `test_a_folder_under_a_parent_it_cant_enter_is_never_read_as_gone` (it fails on the brief's
   line, shown above). The brief's own `test_a_folder_it_cant_enter_is_never_read_as_gone` (the
   folder itself chmod 0) passes with either line, because `stat` on the folder works while its
   parent can be entered. This adds a test and changes no existing test's proof. Note that this
   is a cross-module import of a private name, which the repo has no precedent for. If the
   controller prefers, `_gone` could be renamed public in `flags.py`.
2. **`PRIVATE_UNWRITTEN` is imported from `recordings.plaud.checks`,** its home since Task 8
   ("flag kinds and blocker names live once in `checks.py`"), not from `flags` as the brief's
   import block has it. `flags` re-exports it, so the brief's line would also have worked.
3. **The blocker name is the constant `PRIVATE_UNWRITTEN`** (checks.py: "an event kind and a
   blocker"), not the literal `"private-unwritten"`, in the three `open_blocker`/`clear_blocker`
   calls. The value is the same. The `consent` blocker has no constant in `checks.py`, so it
   stays a literal.
4. **The health imports keep Task 11's `record_ping`, `record_run` and `sync_health`** next to the
   brief's additions, because `conclude_turn` uses them. Task 11 built it with
   `health.record_ping`, not `worker._ping_record`. `ruff --fix` merged the import blocks.

Everything else consumed the code as built with no change: `LateReport.causes`,
`apply_late_consent`, `patterns_pass(writer, db, *, now, progress=)`, `PatternsPass.sentence()`,
`enforce_word` (through `PlaudDB.update`), `gate_now` (ISO timestamp only, raises on a database
error), `record_turn`, `open_blocker`/`clear_blocker`, `SyncHealth`'s NamedTuples (the tests
unpack `last_turn` as a tuple), `plaud_run`/`plaud_holder`, `keepout.read_consent`/`signature`,
and `Writer.open(cfg, docs=False, lock_timeout=10)`.

## Test changes beyond the brief (no change to what a test proves)

5. **`test_the_heartbeat_keeps_writing_during_a_long_synchronous_step`:** added
   `w._beating.join(5)` after `w.stopping.set()`. Without it, a beat already under way (the
   0.01 s loop) can land between the test's read of `meta.worker_heartbeat` and `alive`'s own
   read. That beat is 30 s later on the fake clock, so `alive(now=beat["at"])` would read a
   future stamp and say SILENT: a rare, load-dependent flake. The join only waits for that
   write.
6. **`test_a_failed_start_says_so_and_restarts_dont_renew_the_30_minutes`:** the `Writer.open`
   patch now runs in `with monkeypatch.context() as patched:` instead of
   `monkeypatch.setattr(...)` and then `monkeypatch.undo()`. The autouse
   `no_settings_from_this_machine` fixture (root `conftest.py`) uses the same `monkeypatch`
   instance, so `undo()` brought this machine's `RECORDINGS_*`/`RESTIC_*` variables and
   `config.DEFAULT_PATH` back for the rest of the test. Nothing there reads them today, because
   every call passes `setup.environ`, but it was one change away from reading real settings. The
   assertions and their order are as in the brief.

The commit message is exactly the brief's.

## Self-review findings

I read the whole new module against the brief and the spec points it cites. Points the
controller may want to weigh (none changed, since they are the brief's design):

- **`--alive` can exit 78, not only 0 or 1.** `main()` loads the config before it checks
  `--alive`, so a config error returns 78. The Interfaces section says "--alive exits 0 or 1",
  and Docker's health check reserves meanings only for 0, 1 and 2. In practice any non-zero
  exit reads as unhealthy. Untested either way.
- **`_open_writer` retries only `LockTimeout`.** Its docstring says "A busy index or lock is
  tried again each tick". A `Writer.open` whose index rebuild hits SQLite busy comes back as
  `NotTheWriter` (Writer wraps `sqlite3.DatabaseError`), which fails the start: heartbeat
  `failed`, exit 78, and Docker restarts it. That path is covered by the crash-loop accounting,
  so it's safe, just not a retry.
- **A `private-unwritten` flag keeps its first `cause`.** If the cause changes later (for
  example `StaleFile`, then a full disk's `OSError`), the flag's detail and so Task 19's
  sentence keep the first class. The blocker and the logs are right.
- **A turn that raises calls `conclude_turn(ctx, None)`,** so no `record_run` and no failing
  streak. Status shows `error: <Class>` from `K_LAST_TURN`, and the 2-hour grace brings the
  email. That matches the brief; noted because Phase 2's real turn will make this path common.
- **Privacy:** logs and stored values carry fixed phrases, counts, IDs and class names only.
  `K_CONSENT` holds the count, never the IDs, and a test pins that. No `exc_info`, no titles. The
  `ConfigError` text that `main` prints names settings only, as the rest of the CLI already does.
- **Threads and processes:** the heartbeat thread is a daemon, uses its own short `state.db`
  connections (`State.set_meta`), and every test worker is stopped at teardown. No worker, test
  server or background job was left running (checked with `pgrep`).
- **Docs:** only the two specs name `recordings worker`; the runbook's worker rows come with
  Task 17. CLAUDE.md is updated as Step 6 says.

## Concerns

- Adaptation 1 adds a test and a cross-module import of a private name (`flags._gone`).
- Test changes 5 and 6 are outside the brief's text; neither changes what its test proves.
- The `--alive` exit 78 vs "0 or 1" mismatch in the brief's interface (above). It is left as
  the brief wrote it, for the controller or Task 17 (which wires the compose health check) to
  decide.

## Fix round (the Tier 1 review's rulings)

**Commit:** `f165eb3` fix(worker): 🤖 --alive exits 0 or 1, the pass at start is part of the start and stops on SIGTERM, and flags owns the owed-private rule

One green cycle, one commit. The body gives each new test's failing line, and also what
8ecb3d7's body left out: the added test's `AssertionError: assert 0 == 1`, and the `_gone`
adaptation.

### What each ruling became

1. **CLAUDE.md:127 (Important).** It now says the docs are written whenever the web app's or the
   CLI's Writer opens, or the web app starts against an archive it may write, and names the
   three Writers that write none: the worker's, `recordings reindex`'s and `recordings plaud …`'s
   (`docs=False`). I checked every `Writer.open` in src: cli.py `_writer` (`reindex` passes
   `docs=False`), plaud/commands.py:242 (`docs=False`, every `recordings plaud …` subcommand),
   worker.py, and the web app's.
2. **`--alive` exits 0 or 1 (Important).** Under `--alive`, a `ConfigError` prints `NO_CONFIG`
   ("the Plaud worker's config.toml can't be read: run `recordings doctor --role worker`") on
   stdout, like SILENT and STUCK, and returns 1. The daemon path still prints the error's text
   and returns 78, as ruled. `cli.py`'s `--alive` help and `main`'s docstring say so. Test:
   `test_alive_with_a_config_it_cant_read_exits_1_with_a_fixed_line` (unclosed TOML, and a
   missing file; neither the path nor the text is printed).
3. **Spec finding 1 (Important).** No code change, as ruled.
4. **`starting_since` through `checking` (Minor).** `UNFINISHED = ("starting", "checking",
   "failed")`. While the phase is in it, the heartbeat carries `starting_since` and a restart
   keeps it. `alive` gives `checking` the same 30 minutes from `starting_since` as `starting`,
   and `checking` also needs progress within 15 minutes. I also moved the start's at-once `idle`
   beat to after the pass (`start()`: heartbeat, Writer, gate, `_enforce`, then `idle` written
   at once), because the pass is part of the start. Otherwise a crash early in the pass, before
   the heartbeat thread's first `checking` beat, would still leave `idle` behind and get a fresh
   30 minutes. With no pass due (most starts), `_enforce` takes milliseconds and nothing
   changes. Test: `test_a_crash_during_the_patterns_pass_at_start_never_gets_a_fresh_30_minutes`.
   The kill is simulated with a `BaseException` raised after the heartbeat thread's `checking`
   beat, so nothing after it writes.
5. **SIGTERM stops the pass at start between recordings (Minor).** `_check_one`, which the pass
   calls before each recording, raises a private `_Stopped` once `stopping` is set. `_patterns`
   catches it, logs a fixed line and returns None. `K_PATTERNS_PASS` isn't written, so the next
   start runs the pass again. Test:
   `test_sigterm_stops_the_patterns_pass_at_start_between_recordings` (`raise_signal(SIGTERM)`
   during the first reconcile, under `run()`).
6. **Teardown joins the heartbeat threads (Minor).** `make_worker`'s teardown sets `stopping` on
   every worker, then joins each started heartbeat thread and asserts it ended.
7. **`flags.gone` and `flags.still_owed` (Minor).** `_gone` was renamed `gone`; its two callers
   in flags.py and the module docstring were updated, and no test named it. The new
   `still_owed(archive, event, consent)` holds the worker's rules: a flag closes when the
   recording is private, or gone (by `gone`), or, for a consent-owed flag, when a list that reads
   cleanly names none of its IDs. Before that, a file it can't read keeps the flag open, and so
   does a stop or no list read yet. It shares a new `_ids_of` with `_plaud_ids`.
   `Worker._still_owed` is gone, and worker.py no longer imports `is_private`,
   `canonical_plaud_id` or `Event`. Tests: `test_gone_is_only_a_folder_that_isnt_there` and
   `test_a_private_unwritten_flag_is_owed_until_private_gone_or_no_longer_listed`. The second
   covers the "gone" case, which no worker test covered, with an ID that was never added.
8. **One constant, and docs that say what the code does (Minor).** The constant is `UNFINISHED`
   (it includes `checking`, per ruling 4). The module's first docstring line now names the
   process, its heartbeat and `--alive`. `_open_writer`'s docstring says only `LockTimeout` is
   retried and any other refusal ends the start as `failed`. CLAUDE.md's list of
   `reconcile_plaud`'s callers now names `recordings plaud baseline` and the pattern pass at
   start (`plaud/baseline.py`).
9. **A typo stop still applies the exclusions that read (Minor).** Test:
   `test_a_typo_stop_still_applies_the_exclusions_that_read`. It is a pin: it passed at once, as
   the reviewer's probe predicted.

Not changed, as ruled: `main` printing a `ConfigError`'s text on the daemon path,
`patterns_pass` giving `(done, total)`, `cmd_worker`'s argv, and the late-consent state object.

### TDD evidence

RED: `uv run pytest packages/core/tests/test_worker.py packages/core/tests/test_plaud_flags.py -q -k "<the six new tests>"`

```
E           KeyError: 'starting_since'
E       AssertionError: assert 2 == 1
E           AssertionError: assert 78 == 1
E       AttributeError: module 'recordings.plaud.flags' has no attribute 'gone'
E       AttributeError: module 'recordings.plaud.flags' has no attribute 'still_owed'
FAILED packages/core/tests/test_worker.py::test_a_crash_during_the_patterns_pass_at_start_never_gets_a_fresh_30_minutes
FAILED packages/core/tests/test_worker.py::test_sigterm_stops_the_patterns_pass_at_start_between_recordings
FAILED packages/core/tests/test_worker.py::test_alive_with_a_config_it_cant_read_exits_1_with_a_fixed_line
FAILED packages/core/tests/test_plaud_flags.py::test_gone_is_only_a_folder_that_isnt_there
FAILED packages/core/tests/test_plaud_flags.py::test_a_private_unwritten_flag_is_owed_until_private_gone_or_no_longer_listed
5 failed, 1 passed, 57 deselected in 6.49s
```

The one that passed is the #9 pin.

GREEN: `uv run pytest packages/core/tests/test_worker.py packages/core/tests/test_plaud_flags.py -q`:
`63 passed in 23.78s` (34 worker tests, 29 flags tests).

### `make check`

Exit 0:

- ruff: `All checks passed!`, `145 files already formatted`
- pytest: `1795 passed, 141 deselected, 1 warning` (local time zone and `TZ=UTC`)
- Vitest: `34 passed (34)` files, `380 passed (380)` tests (`0de8bf2`, another agent's UI
  commit on this branch, added two)
- gitleaks: `no leaks found`

One docstring-only reflow in worker.py landed while it ran. Afterwards `make lint` was clean and
the worker suite gave `34 passed`.

### Files changed

- `packages/core/src/recordings/worker.py`
- `packages/core/src/recordings/plaud/flags.py`
- `packages/core/src/recordings/cli.py`
- `packages/core/tests/test_worker.py`
- `packages/core/tests/test_plaud_flags.py`
- `CLAUDE.md`

### Concerns

- **The spec's §9.2 "Alive" line lags the code.** It still reads "progress in the last 15
  minutes (30 while the phase is `starting`)". It names neither decision 48's `starting_since`
  (the brief's), nor `failed`, nor now `checking`. With ruling 4, a pass at start that runs past
  30 minutes from the first unfinished start reads STUCK, even while it progresses. A real pass
  over about 1,234 recordings should take well under that. I didn't edit the spec: the
  controller or the phase end can decide its wording.
- **The start's order moved.** The `idle` beat is now written after the pass at start, not
  before it, as the brief's interface had it. That is ruling 4's consequence, explained above.
- **`still_owed` treats "owed for something other than consent" as owed until the recording is
  private or gone.** That is the rule spec finding 1's pattern flags would need, if the phase-end
  council rules for them.
