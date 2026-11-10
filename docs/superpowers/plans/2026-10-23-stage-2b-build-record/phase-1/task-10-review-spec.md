# Task 10 review: Spec axis (e3a9dfa..6bbb949, Tier 1)

The code is the brief's, verbatim, and its 31 tests pass. Probes on synthetic archives held: A, "Private: x", A is judged once, and a `private` Dan removed stays removed through a second baseline and `reindex`. A pass stopped by `progress` or by a failed recording resumes, misses nothing and re-adds nothing (decision 3). A removed pattern re-checks nothing (§13.1). A holder killed with SIGKILL releases `plaud`, and its leftover text is ignored. When `plaud` times out under `with_ops`, `ops` is released. Nothing logs a title, pattern or word. The brief names no Scenarios.

## Important
1. **baseline.py:43-52.** `RECONCILE_ERRORS` leaves out `sqlite3.Error`, although `Writer.reindex` catches it (Task 9's fix, writer.py:1006). The comment says "as Writer.reindex catches it", which is no longer true. Probe: a single `OperationalError` from `set_markers` stops `baseline_privacy`. The CLI ends with a traceback, and the rest of the archive goes unchecked. A busy state.db at the worker's start would also abandon the pass. **Fix:** share one tuple with `reindex` that includes `sqlite3.Error`, and add a test.
2. **baseline.py:136-187.** `PatternsPass` doesn't say which recordings failed. A recording that was busy or couldn't be written shows up only in a log line. It is retried at the next worker start, which could be weeks away. Task 13 logs `sentence()` as if the pass had finished. §13.2 asks for "Needs attention shows it at once, by ID", with a lock timeout "retried at the next run". **Fix:** add `unwritten` IDs and their classes, as `LateReport` has. Have the sentence count them, and let Task 13 retry or flag them.
3. **baseline.py:8, 74-80.** The docstring says "never skipped silently". But `iter_recordings` drops an invalid `recording.json` into `archive.problems`. Probe: the baseline then reports `unwritten: []` and exits 0, and that recording's title is never checked. **Fix:** list `writer.archive.problems`' IDs as unwritten, or as their own count, so the command exits 1.

## Minor
- **commands.py:298, 304.** An invalid configured word is echoed verbatim. `settings.py` never prints a value; print `BAD_WORD` and `null` instead.
- **baseline.py:108-121.** A row that isn't a list reads as no words, and `enforce_word` then overwrites it, so the older words are lost (probe). The spike refuses such a row; this should fail closed the same way. `drop_word` and `enforce_word` also read and then write outside one transaction, so two writers at once can lose a word.
- **test_plaud_cli.py:233.** The wait test passes even with `timeout=0`. Pin `CLI_WAIT == 60.0` and check that the elapsed time is at least the patched wait (§21).
- **commands.py:273.** The "run `recordings reindex`" fix is printed whatever the error class, against decision 58.
- §9.4 says "The audio-router import also takes `plaud`". No task or phase-end item covers this; add it for Phase 2.

## Security checklist
1. Secrets: **fine.** No tokens, URLs or secrets here.
2. Permissions: **fine.** `plaud.lock` is created 0600 in `locks_dir`. No new mounts or folders.
3. Network: **fine.** `baseline` and `runlock` import no client, token or sync module, nothing listens, and the tests stay offline.
4. Failing closed: **findings.** Important 1 and 3, and Minor 2 (a row that isn't a list). The gate holds: the baseline only ever adds `private`, through `SetPlaudFields`.
5. Injection: **fine.** SQL goes through `PlaudDB` with parameters. The job name is checked against `JOBS`, lock keys are constants, and IDs come from validated folders.
6. Logging: **fine.** Only the ID and the class name are logged, with no `exc_info`. `keep-out-words` prints words to stdout by design (§9.6); see the Minor on the invalid word.

Verdict: changes needed (0 Critical, 3 Important)
