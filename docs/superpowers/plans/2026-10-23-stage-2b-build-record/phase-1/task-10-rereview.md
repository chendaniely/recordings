# Task 10 re-review: fix round 6bbb949..7c2a421

The three touched test files pass (41 tests) and ruff is clean. All probes ran on synthetic archives under the scratchpad.

## Findings

1. **Addressed.** `writer.py:106` holds the shared tuple, used by `reindex` (`:1022`) and `_sweep` (`baseline.py:128`), with a test at `test_plaud_baseline.py:174`. Reindex's logging change loses nothing 2a relied on. `reindex` has no `--debug` (`cli.py:114,122`), and no test reads that log. The old traceback could also print a title from the exception's text. A probe showed the warning now carries only the ID and the class.
2. **Addressed.** `baseline.py:203-266`. Nothing loops within a run. A probe ran 3 starts against an invalid file: each swept again, wrote nothing, and kept `version` 1 and `attempted` 2.
3. **Addressed.** `baseline.py:116-118`, `commands.py:299`.
4. **Addressed.** `baseline.py:124-127`, `commands.py:299-301`. Exits 0, 1 and 75 match §9.6.
5. **Addressed.** `commands.py:255-261` follows decision 58.
6. **Addressed.** `checks.py:282-295`, `commands.py:304-353`. A probe showed `BEGIN IMMEDIATE` takes the write lock before the read: another writer is blocked while `change` runs. A raised error rolls back, and `UNCHANGED` releases the lock. Three threads of `enforce_word`, then concurrent `drop_word`/`enforce_word` pairs, lost nothing. It can't deadlock: each call uses its own connection, the callbacks are pure, and no file lock is held. A caller already in a state.db write transaction on the same thread would wait 30 s, then get `OperationalError`. No caller does, and Task 13's `_enforce` doesn't either.
7. **Addressed.** `commands.py:119,230,242`; `baseline.py:51,107`.
8. **Addressed.** `test_plaud_baseline.py:273`, `test_plaud_cli.py:244`.

## New problems

- **Minor, `baseline.py:116-118`.** Every `archive.problems` entry is labelled `InvalidFile`. That includes a recording.json that can't be read because of its permissions (an `OSError`, `archive.py:87`) and a copied folder whose ID doesn't match. Both are told "run `recordings reindex`", which can't fix them; decision 58 sends an `OSError` to the disk check. Two folders whose names aren't IDs were listed as one `unnamed-folder` (probe). **Fix:** have `Problem` carry the exception's class, key each folder by its relative path, and add "or fix it by hand".
- **Minor, `baseline.py:116`.** A broken recording.json that isn't a Plaud recording blocks the pattern pass for good. Every worker start re-sweeps and logs "patterns changed: 0 titles checked, …, 1 not written". That is visible and fails closed, so it is acceptable. Forward note: Task 13 or 19 should show `K_PATTERNS_PASS.unwritten` in Needs attention.
- **Minor, `commands.py:257`.** `_fix_for` repeats `flags.EDITED_CAUSES`. No CLI test covers the disk branch or the `FileNotFoundError` branch.

## Out of scope

- `writer.py:929` (Task 9): `Writer.keep_out_words` reads a row that isn't a list as having no words, so reconcile and the baseline check only the configured word.
- §9.4's "the audio-router import also takes `plaud`" still has no task.

Verdict: approve
