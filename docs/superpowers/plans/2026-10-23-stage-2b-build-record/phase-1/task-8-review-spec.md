# Task 8 review: Spec axis (Tier 1), 1c7f1d9..e048326

The code matches the brief byte for byte (diffed), and the runbook section is §12.4 word for word. All 40 tests pass. Probes used synthetic archives in the scratchpad.

## Findings

1. **Important: an unwritten recording logs a WARNING and rewrites `state.db` at every tick (`flags.py:196`, `:205`, `:136`).** The brief says "run at every tick, so a run where nothing changed writes nothing", and §9.2 runs the pass every 2 seconds. In a probe, a StaleFile recording gave 3 warnings in 3 passes, and its `flags` row in `state.db` got a new time stamp each pass (`Writer._load_fresh` → `State.flag`). That is 43,200 lines a day for each stuck recording, and the worker's logs rotate at 50 MB (`compose.yml:17`), so the lines Dan needs get pushed out. An index that can't answer does the same at `:136`. Task 13's `_log_late` already logs once per change. Fix: remove these warnings, or log once per ID and class. Then either document the retry at every tick (which stamps `flags` again) or skip `mutate` while the file's SHA is unchanged, and test it.
2. **Important: a folder with no `recording.json` is skipped silently (`flags.py:190-191`, and the same at `:141-142`).** `mutate` raises `KeyError` when the file is missing, even while the folder and its media are still there. The pass then doesn't tag, flag or report it: the probe gave an empty `LateReport` and no flag. §12.1 says the index lookup is "never skipped", and Task 13's `_still_owed` already treats a folder that exists as not gone. Fix: on `KeyError`, if `path_for(rid).exists()`, flag the recording and report it in `unwritten` (cause `FileNotFoundError`). In `_listed_here`, a pending recording whose file can't be read should mark the pass `index_unavailable`. Add the case to `test_one_recording_that_cant_be_written_stops_none_after_it`.
3. **Minor: `plaud consent` ignores pending index updates (`commands.py:105-124`).** In a probe, `in_archive` was `[]` while the worker's pass tagged that same recording. The dry-run review (§12.1, §17.5) reads this list. Fix: reuse `_listed_here`'s read of pending recordings.
4. **Minor: `plaud consent` is a reader that holds no lock, but it writes (`commands.py:100`).** It calls `State.open`, which runs the schema script and the upgrade and makes the locks folder. `state.py` says readers use `State.read_only`.
5. **Minor: no spec section has `PRIVATE_UNWRITTEN_IO` (plan decision 58).** §13.2, §15.6 and §16 give only the reindex sentence, but 70545bb's commit body says §16's row "gains" it. Add it to §15.6 and the spec's Revisions.

No scope creep, and no doc made untrue (grepped the touched names and "not yet", "planned", "unverified").

## Scenarios

- C5.04: proved by `test_keep_here_clears_the_flag_and_changes_no_file`.
- C5.10: proved by `test_plaud_consent_lists_kept_lines_by_number_and_2a_misses_by_archive_id`.
- C5.13: proved by `test_an_already_flagged_or_gone_recording_gets_no_second_flag` and the racing-writer test.

## Security checklist

1. Secrets: fine. Nothing here handles tokens or URLs; logs, fixtures and commit text hold only IDs and class names.
2. Permissions: not applicable. Nothing is created or mounted; a probe confirmed no `index.db` or `state.db` was made.
3. Network: fine. `recordings.cli` loads no client, token or sync module (checked in `sys.modules`), and the tests never reach the network.
4. Failing closed: a finding (#2). The rest holds: during a stop, readable exclusions apply and nothing clears; an empty list is a stop; an index that can't answer clears nothing; Keep it here clears only `kept-out-late` and changes no file; `SetPlaudFields` only adds `private`; no title is shown.
5. Injection: fine. SQL goes through `PlaudDB`, with parameters and checked column names; IDs reach paths only through `parse_id` and `ID_RE`.
6. Logging: a finding (#1, how much is logged). What is logged is only IDs and class names: no `str(exc)` and no `log.exception`.

Verdict: changes needed (0 Critical, 2 Important)
