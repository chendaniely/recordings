# Task 10 report

Status: DONE. Commit 6bbb949 (base in task-10-base).

## Implemented
- `locks.PLAUD_LOCK`; `plaud/runlock.py` (`plaud_run`, `plaud_holder`, `JOBS`, `CLI_WAIT`); `plaud/baseline.py` (baseline_privacy, enforced_words, enforce_word, drop_word, patterns_pass); `plaud baseline` and `plaud keep-out-words` in `plaud/commands.py`. All code and tests copied verbatim from the brief.

## TDD evidence
- RED: `uv run pytest packages/core/tests/test_plaud_baseline.py packages/core/tests/test_plaud_cli.py -q` -> collection error `ImportError: cannot import name 'PLAUD_LOCK' from 'recordings.locks'`. (I had created baseline.py and runlock.py before the first run, so the error is the missing lock name, not the brief's ModuleNotFoundError; the commit message says so.)
- GREEN: same files plus test_locks.py -> `31 passed`.
- `make check`: 1701 passed, 141 deselected, gitleaks no leaks, ruff clean (format unchanged).

## Adaptations to Tasks 1-9 as built
None needed: the brief's calls (`db.markers`, `report.private_added`, `kept_out_word`, `record_late_keep_out`, `fold` via `keepout`, `Writer.keep_out_words`) match the code as built. Marker comparison (`before.get(pid) != m`) works with the new `judged` field (Task 9 forward note).

## Files
locks.py, plaud/runlock.py, plaud/baseline.py, plaud/commands.py, tests/test_plaud_baseline.py, tests/test_plaud_cli.py (all under packages/core).

## Self-review
- Diff is the brief's code; no title printed (counts and IDs only); lock held across the whole baseline run, released on LockTimeout.
- Note: `baseline_privacy`'s `now` parameter is unused (brief's signature); `fold` is imported by baseline from `keepout` (which re-exports `models.fold`) as the brief wrote.
- Concern: none.

## Fix round (commit 7c2a421)

Rulings 1-8 done. RED was run in a throwaway worktree at 6bbb949 with the new tests (11 failed, 16 passed), e.g. `sqlite3.OperationalError: database is locked: Week 5` (escaped baseline_privacy), `AttributeError: 'PatternsPass' object has no attribute 'unwritten'`, `DID NOT RAISE KeepOutWordsError`, `assert 1 == 75`, `BAD_WORD ... not in 'configured: no archive! ...'`. GREEN: 41 passed in the three touched test files; `make check`: 1711 passed, Vitest 378 passed, ruff clean, no leaks.

- **One tuple:** `writer.RECONCILE_ERRORS` (with `sqlite3.Error`) used by `Writer.reindex`, `baseline_privacy`, `patterns_pass`. Side effect: reindex now logs every failure at warning with the class only (before: debug with `exc_info` for non-sqlite errors).
- **Shared loop:** `baseline._sweep`. LockTimeout -> busy; other errors -> unwritten with class; `archive.problems` -> unwritten with `InvalidFile` (ID from the folder name, `unnamed-folder` if it isn't an ID).
- **BaselineReport** gained `busy` and `causes`; `to_dict()` now also has `"busy": [...]` and `"causes": {id: class}` (the two brief tests' expected dicts were updated). Exit: unwritten -> 1; else busy -> 75; else 0. Fix text follows the cause.
- **keep-out-words:** `PlaudDB.update(key, change, *, now)` + `checks.UNCHANGED` (one `BEGIN IMMEDIATE` transaction); `KeepOutWordsError` for a non-list row (message names the key); listing uses `State.read_only`; invalid word prints `BAD_WORD` / `null`.
- Renames `_read_state`/`_write_state`; `_writer` uses `lock_timeout=10.0`; `FIRST_PATTERN_VERSION = 1`; `CLI_WAIT == 60.0` pinned; wait test checks elapsed >= 0.2.

### `K_PATTERNS_PASS` value shape (Tasks 13 and 19 read it)

```json
{"version": int, "attempted": int, "at": "<iso>", "checked": int, "tagged": int,
 "unwritten": ["<recording id>", ...], "busy": ["<recording id>", ...]}
```
- `version` = the newest pattern version whose pass COMPLETED (nothing unwritten or busy); it stays at the previous value (1 if none) when the pass was incomplete. `attempted` = the version this pass ran for. A pass runs when `current_patterns(...).version > version`.
- `unwritten`/`busy` are IDs only. `PatternsPass.unwritten` is a dict ID -> exception class name (like `LateReport.causes`); `PatternsPass.busy` a tuple; `.complete` property; `sentence()` appends ", N not written" and ", N busy".
- Added beyond the ruling: `attempted` and `busy` keys.

Not done: the review's Minor on §9.4 "the audio-router import also takes `plaud`" (no task covers it; for the controller to add to Phase 2).
