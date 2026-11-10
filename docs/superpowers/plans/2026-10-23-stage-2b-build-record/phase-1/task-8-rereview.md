# Task 8 re-review: fix round 964a412..8b0115e

165 tests pass and ruff is clean. Probes used synthetic archives in the scratchpad.

## Findings
1. **Addressed.** No logging is left in `flags.py:215`, and `state.py:366-372` returns early. Probe: the StaleFile, InvalidFile, FileNotFoundError and PermissionError cases ran three passes. After pass 1, the bytes of `state.db` and `-wal`, `plaud_events` and `flags` stayed the same, and no record was logged at any level. 2a impact: flags are removed only by `clear_flags` and `forget` (`writer.py:957-973`, `state.py:408`), and those are unchanged. The only reader is `ops.py:720`, which counts the flags and never reads `at`. So no flag can stick.
2. **Partly addressed.** `flags.py:203-210` is right. `:151` has the gap described in new problem 1.
3. **Addressed.** `flags.py:127` and `commands.py:106-128`. Test: `test_plaud_cli.py:133`.
4. **Addressed.** `commands.py:101`. Test: `test_plaud_cli.py:150`.
5. **Addressed.** `keepout.py:217` and `commands.py:142`.
6. **Addressed.** `checks.py:34-52`, `flags.py:159` and `:168`. No bare kind strings are left in src.
7. **Addressed.** `checks.py:383`.
8. **Addressed.** `commands.py:177-187`.
9. **Addressed.** Spec lines 2039-2042 and 3107. The wording matches `PRIVATE_UNWRITTEN_IO`.

## New problems
1. **Important: `flags.py:151` reads "can't stat" as "removed".**
   - **Cause:** on Python 3.14, `Path.exists()` returns False for any OSError. A probe showed that a folder under a parent it can't enter gives False.
   - **Effect:** take a pending recording that isn't in the index yet (an `add` whose index update failed), in a month folder it can't enter. The pass treats it as removed. The probe got an empty `LateReport`, `index_unavailable` False, and the recording is not private.
   - **Why it matters:** decision 46 is broken here, though only in this narrow case. The module docstring (`:16-17`) also says that only `mutate` decides a recording is gone.
   - **Fix:** add a `_gone(path)` helper that calls `os.stat`. Only `FileNotFoundError` or `NotADirectoryError` means gone; any other OSError means not gone. Use it at `:151` and `:206`, and add the locked-parent case to `test_plaud_flags.py:458` (skipped as root).
2. **Minor: the `_here` docstring at `commands.py:108-110` is garbled** (" them", and "busy,\n    ).").
3. **Minor: the new `FileNotFoundError` cause gets `PRIVATE_UNWRITTEN_IO`** ("check the archive disk and its permissions"). A missing `recording.json` isn't a disk or permission fault: the step is to restore the file, or to run `recordings reindex`. **Fix:** route it to its own sentence.

## Out of scope
- §16's row (spec line 2082) still gives only `recordings reindex` as the step; decision 58 says the remedy follows the cause.
- `Writer._catch_up_index` (`writer.py:464-473`) drops a missing or invalid file's pending mark at the next `add`, ending finding 2's `index_unavailable`. A `PermissionError` there is uncaught, so `add` fails.
- A missing pending file is reported as "the index can't answer" (`commands.py:200`).

Verdict: changes needed (0 Critical, 1 Important)
