# Final review re-review: stage 2b Phase 1 fixes (f03210b..ae5969c)

I ran these:
- `uv run pytest packages/core/tests packages/ui/tests -q`: 1961 passed. That is the spec review's 1954 plus the fixes' 7 new tests. The one warning is the existing fork warning in `test_demo_guard`.
- `ruff check` and `ruff format --check`: both clean.
- Two probes on synthetic archives in the session scratchpad: `probe_fix4.py` and `probe_fix2.py`.
- The worktree is unchanged.

## The four fixes

- **Fix 1 (f54c0be): addressed.**
  - Nothing outside the spec and the plans says that `make plaud-sign-in` exists now (`git grep`). These are the only remaining mentions:
    - the runbook, at `first-run.md:1097` and the alert table at `:1221`, both labelled as the next phase;
    - doctor's comment and its test (`doctor.py:716`, `test_doctor.py:956`);
    - the new guard test;
    - one fixture string in `SyncPanel.test.ts:24`, which is test data and not shipped.
  - The Makefile has no such target. `config.example.toml:113` and `.gitignore:37` now say "comes with stage 2b's next phase". The new test (`test_plaud_settings.py:176-183`) pins both, and skips once the target exists.

- **Fix 2 (ae320fe): addressed for its finding.**
  - In `plaud/commands.py:238-253`, `_writer` now prints a fixed phrase and the class for `OSError` and `sqlite3.Error`, with exit `EXIT_CONFIG`.
  - `WriterError` and `LockTimeout` keep their own text. Both are worded for display and name paths and IDs, never a title. That matches CLAUDE.md's `cannot_tag_reason` precedent, and recording folders are named by ID (`archive.path_for`).
  - Grep over `plaud/` and `worker.py`: every remaining `str(exc)` comes from a class worded for display. Those are `ConfigError`, `StateError`, `LockTimeout`, `WriterError`, `NoSyncYet` and `KeepOutWordsError`. `worker.main` prints the class only, except for `ConfigError`.
  - Two leftover paths sit outside the fix. They are new findings 1 and 2.

- **Fix 3 (0d74f78): addressed, and it opens no fail-open.**
  - **The callers.** `RECONCILE_ERRORS` has three callers: `Writer.reindex` (`writer.py:1022`), `baseline._sweep` (`baseline.py:128`) and now `apply_late_consent` (`flags.py:265`). The first two already used the list unchanged.
  - **The order of the catches.** In `apply_late_consent`, `KeyError` and `LockTimeout` are still caught first, so the gone, missing and busy cases behave as before. `LockTimeout` is a `RuntimeError`, and `RecursionError` is its sibling, not its parent, so the wider set never swallows busy.
  - **It can't hide a stop or open the gate.**
    - The pass only adds `private` and flags. It never touches `catch_up_done_at`, the gate or an import.
    - A newly caught class still lands in `unwritten` with its class in `causes`. `_owed_private` (`worker.py:626-654`) then opens one `private-unwritten` flag per recording, and while any flag is open the blocker stays open.
    - A systematic bug, such as a `TypeError` in every `mutate`, now flags every recording and opens the blocker. Before, it opened the blocker through `_late_failed`. Either way it fails closed, and Status names the class (probed through `flag_sentence`).
  - **The private tag stays owed.**
    - `still_owed` keeps the flag open while the recording isn't private, unless a clean list no longer names it.
    - The consent-late flag is still added after the failed write.
    - The extended parametrized test checks that the next recording is tagged and both are flagged.

- **Fix 4 (ae5969c): addressed.** Probe results:

  | Case | Kind | Notes |
  |---|---|---|
  | Off, before the catch-up | `no-sync` | |
  | Off, before the catch-up, with the consent blocker | `problem` | "Imports stopped: consent list" |
  | Off, before the catch-up, with the `private-unwritten` blocker | `problem` | |
  | Off, before the catch-up, failing streak | `problem` | "The sync is failing (since 11:00)" |
  | Off, 1 h after the catch-up | `problem` | No Needs attention entry yet |
  | Off, 30 h after the catch-up | `problem` | C6.14's "Scheduled sync has been off since …" entry |
  | An unreadable `catch_up_done_at` stamp | `no-sync` | Consistent with `gate_now`, which keeps the gate closed on the same value |

  - `sync_view` and `sync_panel` returned the same summary in every case: both call `_Read.summary_view`.
  - `off_before_catch_up` needs `stopped == OFF`. `SyncHealth.cause` returns `OFF` only after no blocker and no failing streak. So a blocker or a failing streak can never read plain.
  - Only `.sync-summary.problem` is drawn in `--destructive` (`app.css:347`).
  - The C6.14 test (`test_plaud_views.py:254`) still passes.

## New findings

1. **Minor. `packages/core/src/recordings/plaud/commands.py:281-287` (`cmd_baseline`).** This path is not changed by Fix 2.
   - **What happens:** only `LockTimeout` is caught around `runlock.plaud_run` and `baseline_privacy`. An `OSError` or `sqlite3.Error` raised outside the per-recording loop escapes `main` as an uncaught exception. Python's traceback then prints its text, and `--json` prints no JSON. Examples are `plaud_run`'s state.db write ("database is locked") and `iter_recordings`.
   - **Probe:** `probe_fix2.py` raised a synthetic text from `baseline_privacy`, and `OSError` and `OperationalError` both escaped `main` carrying it.
   - **Risk:** paths are named by ID and sqlite3 text names no row values, so no title is likely to leak. It still breaks §9.6's "fixed phrases" and `--json`'s one object.
   - **Fix:** catch both the way `_writer` now does.
   - **When:** this was there before the fixes. It is a phase-end list item.

2. **Minor. `packages/core/src/recordings/cli.py:226-231` (`_writer`), `:243-244` (`cmd_reindex`) and `:285-288` (`cmd_docs`).**
   - **What happens:** the stage 2a CLI still prints `str(exc)` for `OSError` and `sqlite3.Error`. This is the same pattern Fix 2 removed from `plaud/commands.py`.
   - **Scope:** it was there before 2b (blame: 2026-10-08) and is outside `recordings plaud …`'s §9.6 contract. Its paths are ID-based.
   - **When:** this goes on the phase-end list next to standards finding 5 (`_fail` and `_config` are duplicated across the two CLIs).

3. **Minor. `packages/core/src/recordings/plaud/flags.py:75-78`: the wording for the newly caught classes.** The implementer already noted this.
   - **What happens:** a `TypeError`, `RecursionError` or `ArithmeticError` now gets `PRIVATE_UNWRITTEN_IO`'s "check the archive disk and its permissions". For a code bug, that advice is wrong.
   - **Logs:** the worker's log says only "N not written" (`worker.py:618`), with no class. Before Fix 3, the pass-wide path logged "consent failed (TypeError)".
   - **Spec:** this follows §15.6 to the letter, and decision 58 covers any cause that is not an outside edit or an invalid file.
   - **Fix:** a sentence for non-I/O classes that points to the worker's log, and the class in `_log_late`.
   - **When:** this is a phase-end list item.

**Not a finding.** With the schedule off before the catch-up and a bad `keep_out_word`, the summary used to be red "Scheduled sync is off" and is now plain.
- §15.2's cause order puts "the schedule off" before "idle on a configuration fault". The summary never named the bad word, before or after the fix.
- Status's conditions still say "Plaud sync is idle: …".
- Nothing can import before the catch-up, so it stays fail-closed.

Verdict: approve
