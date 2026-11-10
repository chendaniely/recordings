# Final review fixes, stage 2b Phase 1

Status: DONE. Four commits, each after `make check` (lint, pytest, TZ=UTC pytest, Vitest, gitleaks) passed.

## Fix 1: f54c0be `docs(config)`
- Failing line: `AssertionError: config.example.toml / assert 'plaud-sign-in' not in '# recording... (stage 2)\n'`
- Changed: `config.example.toml` token_store comment and `.gitignore` local.mk comment now say the sign-in comes with stage 2b's next phase. New test in `packages/core/tests/test_plaud_settings.py` (skips once the Makefile has the target).
- Noticed: the only other pointers are in `docs/runbooks/first-run.md` (lines ~1097 and ~1221), both already labelled as the next phase, so left alone. `test_runbook.py:277` pins `make plaud-sign-in` in the alert section; that is the runbook, left alone.

## Fix 2: ae320fe `fix(plaud)`
- Failing line: `assert 'OSError' in '{"error": "/Volumes/archive/Synthetic 042: a title no command may print/recording.json"}\n'`
- Changed: `_writer` in `plaud/commands.py` prints a fixed phrase plus the class for `OSError` and `sqlite3.Error`. `WriterError` keeps its own text (the Writer words it for display). Test in `test_plaud_cli.py`, both exception types, exit code `EXIT_CONFIG`.
- Noticed: `_write_state` also prints `str(exc)` for `StateError`; it is a fixed-phrase error class, so I did not touch it.

## Fix 3: 0d74f78 `fix(plaud)`
- Failing line: `FAILED test_plaud_flags.py::test_one_recording_that_cant_be_written_stops_none_after_it[a TypeError]` (also RecursionError, ZeroDivisionError).
- Changed: `flags.py` imports `writer.RECONCILE_ERRORS`; `UNWRITTEN` removed; the module docstring updated. `KeyError` and `LockTimeout` are still caught first, so their handling is unchanged. The three new cases extend the existing parametrized test: the failed one is in `unwritten` with its class in `causes`, the next recording is tagged, and both are flagged.
- Caller check: the worker reads `unwritten` and `causes` as class names, so it opens the blocker and shows `flag_sentence`'s "couldn't be written (TypeError): check the archive disk" for them. That wording is a little off for a code bug, but it is what a ValueError already got, and it carries no text. Reindex, the baseline and the patterns pass already used the wider list.

## Fix 4: ae5969c `fix(ui)`
- Failing line: `Scheduled sync is off and 'problem' == 'no-sync'`
- Changed: `_Read.summary_view` in `recordings_ui/plaud.py` gives `"no-sync"` when the stopping reason is `health.OFF` and `catch_up_done is None` (new `off_before_catch_up`). After the catch-up it stays `"problem"`, and the 24-hour Needs attention entry (C6.14) is untouched. A blocker still gives `"problem"` after the catch-up (tested).
- Ran `make build` and `make e2e` (144 passed); no frontend change, so `make test-js` ran only inside `make check` (455 passed).
- Noticed: before the catch-up the schedule off with a blocker open shows the blocker's phrase, which is still a problem; not covered by a test before the catch-up.

## Process note
`make check` takes about 8 minutes here. Untracked scratch files `.superpowers/check*.txt`, `build.txt`, `e2e.txt` and `cli_new.py` are left in the worktree (not committed).
