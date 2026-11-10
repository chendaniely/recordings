# Phase end step 4, Tier 1 fixes: report

Six commits on `stage-2b`, in order, each after `uv run ruff format .` and `make check` (lint,
pytest, pytest under `TZ=UTC`, Vitest, gitleaks), each message checked by `scripts/commitmsg.py`.
Fix 3 changes the views' output, so `make test-js`, `make build` and `make e2e` ran for it too.
Nothing was amended, reset, rebased or pushed, and the plan's phase-end list is untouched.

| Fix | Commit | Subject |
|---|---|---|
| 1 (S1) | `6efdd2f` | the private patterns fold every title, at import too |
| 2 (keep_out_words row) | `7de8559` | a keep_out_words row that isn't a list never reads as no words |
| 3 (G1, S2, Task 13) | `366517a` | an unchecked recording holds the ping, and the pass retries each tick |
| 4 (R2) | `bd83317` | consent-add.sh syncs the new list before its rename, and after |
| 5 (S4) | `80939b0` | the backup refuses an extra path doctor reports, by one check |
| 6 (cmd_baseline) | `4718636` | the baseline command catches OSError and sqlite3 errors by class |

Final `make check`: 1987 pytest passed (twice, the second under `TZ=UTC`), 455 Vitest passed, no
leaks. `make e2e` (at Fix 3): 144 passed.

## Fix 1: S1, the import's private check folds (`6efdd2f`)

**Failing line** (each of the three probes):
`test_import_run.py:558: assert seen == {B: tags}`, with
`AssertionError: {'bbbb…': []} != {'bbbb…': [('private', 'auto')]}`.

**What changed.**
- `reconcile.matches_auto_private` tries each title as given and folded (`models.fold`), each with
  and without the "MM-DD " prefix. Every caller now folds: the audio-router import's create step,
  the marker's judge, and Phase 2's import.
- `privacy._hits` folded before calling it and tried exactly the same four forms. It is gone, and
  the judge calls `matches_auto_private([title], run)`; `privacy.py` no longer imports `fold`.
- `reconcile.py` already imported `recordings.models`, so its importers (privacy, writer,
  settings, audio_router, and the views through privacy, Pages included) gain no new import.

**Tests.** `test_a_title_is_tried_folded_when_the_recording_is_created`, parametrised over a
fullwidth `Ｐ`, a zero-width space and a soft hyphen (each must be in `Incoming.tags` as
`("private", "auto")` when `Writer.add` is called), and a plain title that stays untagged.

**Concern.** `scripts/plaud_spike.py` still folds before calling it. That is harmless now, and left
alone: Phase 3 deletes the script.

## Fix 2: a keep_out_words row that isn't a list (`7de8559`)

**Failing lines:**
- `test_plaud_baseline.py:283: with pytest.raises(KeepOutWordsError): writer.keep_out_words()`
  failed with `DID NOT RAISE KeepOutWordsError`.
- `test_plaud_cli.py:339: assert main(["plaud", "baseline", "--json"]) == 1` failed with
  `assert 0 == 1`.

**What changed.**
- `Writer.keep_out_words` reads the row through `baseline.enforced_words`, imported where it is
  used, since baseline imports the writer. So the Writer, `enforce_word` and `enforced_words` agree:
  each raises `KeepOutWordsError` and changes nothing. A reconcile then judges nothing and moves no
  marker, so the titles stay due until the row is mended.
- `baseline._sweep`, which the baseline and the patterns pass share, checks the row first, so a bad
  row gives one reason instead of every recording listed under the wrong fix.
- `recordings plaud baseline` prints the fixed `NOT_A_LIST` phrase and exits 1, as
  `keep-out-words` does.

**Concern.** While the row is bad, `recordings reindex` lists every Plaud recording as
"KeepOutWordsError: not reconciled", and the worker's patterns pass (when one is due) raises as a
whole. With Fix 3, that opens the private-unwritten blocker. Both fail closed, as intended. No
command repairs the row, so mending it takes a hand edit of `state.db`; it can only go bad that way
too.

## Fix 3: G1, S2 and Task 13's question (`366517a`)

**Failing lines:**
- `test_worker.py:926: (flag,) = w.db.open_flags((PRIVATE_UNWRITTEN,))` raised
  `ValueError: not enough values to unpack (expected 1, got 0)`: an unchecked recording got no
  flag.
- `test_worker.py:967: assert (record["error"], record["at"]) == …` raised
  `TypeError: 'NoneType' object is not subscriptable`: a pass that raised was not recorded.
- `test_worker.py:1019: assert reconciled == [busy]` failed with
  `AssertionError: assert [] == ['20261006T14…_037bfed0']`: a busy recording was not retried.

### The design, and why

**The flag.** A recording the patterns pass couldn't check opens the existing `private-unwritten`
flag, with detail `{"owed": "patterns", "cause": <class>}`. It adds no blocker (decision 10).
- The flag holds the ping through the existing blocker.
- It resolves once the pass no longer lists the recording, or the recording is private or gone.
  That check is `flags.private_owed`, a helper `still_owed` now shares.
- `busy` recordings are never flagged; they are retried.
- Folder keys that aren't recording IDs (`unnamed-folder`) are never flagged.
- One flag per recording: `_owed_private` now resolves flags before it adds new ones. So a
  recording that moves between "owed by consent" and "owed by patterns" keeps one flag, with no
  tick between without one.

**The retry: at every tick, but only what was left.** I chose this over a full pass at each
scheduled turn:
- A full pass costs about 21 ms per recording (the council measured 3.5 min at 10,000), and it
  blocks the tick, so late consent and requests would wait.
- Scheduled turns don't happen when `schedule_minutes` is 0.

How it works:
- `patterns_pass(…, only=[…])` re-checks just the recordings the last attempt of this version left
  (unwritten and busy). The others were checked by that attempt and their markers moved.
- The counts add up over one version's attempts.
- A retry that changed nothing writes nothing.
- A recording whose folder is gone since is dropped.
- A key that isn't an ID waits for a whole sweep.
- Once a pass is complete, the step returns at once, without touching `state.db`.

So a `recordings reindex` that lands the tag clears the flag, the blocker and the Needs attention
line at the next tick, which fixes G1.

**S2: a pass that raises as a whole.**
- `baseline.record_pass_error` adds `"error": <class>` and `"at"` (when it first failed so) to
  `K_PATTERNS_PASS`. It keeps `version` as the last complete pass's, and the next attempt that ends
  writes a record without `error`.
- The worker remembers the class, and the blocker opens with detail
  `{"patterns_error": <class>}` (late consent's stays `{"error": …}`; both can be present).
- `_step` logs it once by class.
- The next tick retries over the whole archive, since there is no list of what was checked.

**The heartbeat and `alive`.** Only the pass at start runs as `checking`, with "checking, N of M".
A retry calls `_bump_one`, which bumps progress once per recording and lets SIGTERM stop it between
recordings, without changing the phase. So no stale count and no `starting_since` appear, and
`alive`'s rules (decision 48) are untouched.

**The views.**
- The unchecked line now comes from the flag: `flag_sentence(…, owed="patterns")` returns
  `flags.PRIVATE_UNCHECKED`, the old `PATTERNS_UNCHECKED` text, verbatim. It shows in Status's
  Needs attention and summary, on the Sync screen and in Details.
- The views no longer read `K_PATTERNS_PASS`.
- A stopped pass reads "Checking titles against the private patterns stopped (`<Class>`): check the
  archive disk, then the worker's log", in the summary and in Needs attention.
- No frontend code changed: `FlagRow` already handles a sentence that names its recording.

**Docs.**
- The spec changed: §13.1 case 5 (the flag and the retry), §15.6 (the two sentences) and §16's
  private-unwritten row, with a Revisions line. No decision 1–12 and no scenario's expected outcome
  changed.
- The runbook's blocker row and CLAUDE.md's line on the patterns pass now say it is retried.

**Tests.**
- Worker:
  - an unchecked recording opens the flag and the blocker, and a good run sends no ping; after a
    reindex lands the tag, the next tick clears both, and the next turn pings;
  - a pass that raises is recorded, opens the blocker, logs once by class without the exception's
    text, and clears once a retry ends;
  - a busy recording gets no flag and no blocker, and the next tick retries only it, then nothing.
- Baseline:
  - the `only` retry (only what was left, the counts add up, nothing written when nothing changed,
    a gone recording dropped, a non-ID key kept);
  - `record_pass_error` and a whole-archive retry after it.
- Views: the line follows the flag and not `K_PATTERNS_PASS`; a stopped pass's line shows its class
  only.

### Concerns

- **Two retry costs.** A retry after a whole-pass failure (or after another process records a new
  pattern version) is a whole sweep inside one tick. It runs under the phase `idle`, without
  "checking, N of M", and at scale it holds the tick for minutes. That is rare, but Status shows
  `idle` meanwhile. Separately, a busy recording's retry can wait the Writer's 10-second lock
  timeout inside the tick, as the pass at start already does.
- **The non-ID folder key.** An `unnamed-folder` key (a folder whose name isn't an ID) keeps the
  pass incomplete until the next start's whole sweep. It opens no flag and no blocker, because there
  is no recording ID to flag (the phase-end row from Task 10 already covers keying by path).
- **Tests written after the code.** The baseline-level tests for `only` and `record_pass_error`
  came after the implementation. The failing-first tests are the three worker tests above.

## Fix 4: R2, sync around the rename (`bd83317`)

**Failing line:**
`test_make_targets.py:181: assert [line.split(" ", 1)[0] for line in lines] == ["sync", "mv", "sync"]`
failed with `AssertionError: assert ['mv'] == ['sync', 'mv', 'sync']`.

**What changed.** A bare `sync` before the `mv` and one after it, with a comment, and the header
comment updated to match.

**What I read.**
- POSIX.1-2024 (Issue 8) has no `sync` utility at all: its utilities index goes from `stty` to
  `tabs`.
- It has only the XSI `sync()` function. It "shall cause all information in memory that updates
  file systems to be scheduled for writing", and that writing "is not necessarily complete upon
  return".
- Linux's `sync(2)` "waits for I/O completions" (since 1.3.20).
- coreutils' `sync [OPTION] [FILE]...` flushes everything when given no operand.
- macOS's `sync(8)` has the synopsis `sync`, with no operands (read with `man 8 sync`).

So bare `sync` is the one form that runs on both. On the server (Linux) it waits for the writes; on
macOS it only runs the tests.

**Test.** A fake `sync` on PATH records its calls. A fake `mv` records its call, then execs the real
`mv`. The test checks the order (`sync`, `mv`, `sync`), that both syncs take no operand, and that
the list was written.

**Concern.** Every consent-add test now runs the real `sync`, which flushes the test machine's
whole cache. It is cheap in practice.

## Fix 5: S4, the backup refuses what doctor reports (`80939b0`)

**Failing line:**
`test_backup.py:1340: with pytest.raises(backup.BackupRefused, …): backup.run_backup(…)` failed
with `Failed: DID NOT RAISE BackupRefused`, for the configured state folder and the token store's
folder: both were backed up. `/state`, `/plaud` and `/run/secrets` failed only as missing.

**What changed.**
- `backup.never_backed_up(entry, token_folder=, state=)` is the one check. It moved from doctor with
  its path helpers (`normal_path`, `holds`) and returns fixed reasons.
- `run_backup` refuses first, before the missing-path check and before restic runs, with
  "refusing to back up: `<path>`, `<reason>`".
- `BackupConfig` gains `extra_paths` and `token_folder` (`backup.token_folder(cfg)`, read as
  doctor read it).
- Doctor's `_extra_paths` calls the same check, and its messages are unchanged. Its `PLAUD_MOUNT`
  and `SECRETS_DIR` now come from `backup`.

**Tests.**
- Backup: `/state`, the configured state folder, the configured token folder and a file in it,
  `/plaud`, `/run/secrets` and a secret inside it are each refused with their reason, and restic
  never runs. `/consent`, `/state/manifests`, `/state/backup/state.db` and `/run/other` are never
  refused.
- Doctor: three `/run/secrets` cases.

**Concern: a widening, said in the commit.**
- Doctor did not check `/run/secrets` before. The council's decision table names it, so the shared
  check adds it, both directions (`/run` and `/run/secrets/x`).
- §17.2's rule in the spec gains the secrets folder, with a Revisions line.
- Secret files kept outside `/run/secrets` (a Mac's `*_FILE` paths) are still not checked.

## Fix 6: cmd_baseline catches OSError and sqlite3 errors (`4718636`)

**Failing line** (for each class):
`test_plaud_cli.py:361: assert main(["plaud", "baseline", "--json"]) == commands.EXIT_FAILED`
raised `OSError: /Volumes/archive/recordings/2026/10/Synthetic 042: a title no command may
print/recording.json`. The exception escaped `main` with its text.

**What changed.**
- `cmd_baseline` catches `(OSError, sqlite3.Error)` as `_writer` does: "the baseline stopped
  (`<Class>`): check the archive disk and its permissions, then run the baseline again".
- Under `--json` that comes as `{"error": …}`, otherwise on stderr.
- `LockTimeout` is a `RuntimeError`, so the order of the `except`s doesn't matter.

**The exit code is 1**, §9.6's failure. The run started and then stopped, and `keep-out-words`
exits 1 for state.db faults too. `_writer`'s 78 stays for a Writer that can't open, which is
configuration.

**Test.** Both classes, in both modes. The class is shown; the made-up title and path are not.

## Fix round 1

These are new commits on top of `02d397c`, which holds Dan's answers. Items 1–3 share one commit,
because they are one state machine: which recordings the patterns pass still owes a check, and
when it retries. Items 4–7 have a commit each. Before each commit, `uv run ruff format .` and
`make check` passed. Items 1–3, 4 and 6 change what Status says, so `make test-js`, `make build`
and `make e2e` ran for them too. Nothing was amended or pushed, and the plan's phase-end list is
untouched.

| Items | Commit | Subject |
|---|---|---|
| 1–3 | `1d38238` | an unchecked recording keeps its flag while busy or failing, and a whole retry waits a turn |
| 4 | `0d04971` | a stopped patterns pass names the step its class needs |
| 5 | `f4479bb` | consent-add.sh's header wraps at 100 again |
| 6 | `44b2ad5` | a keep_out_words row that isn't a list reads as unreadable on Status |
| 7 | `94c2f91` | say when the worker retries the patterns pass |

The final `make check` passed: 1996 pytest (twice, the second under `TZ=UTC`), 455 Vitest, and no
leaks. The last `make e2e`, at item 6, passed 144.

### Items 1–3 (`1d38238`)

**The failing lines:**
- **Item 1:** `test_worker.py:1064: assert [e.recording_id for e in
  w.db.open_flags((PRIVATE_UNWRITTEN,))] == [rid]` failed with `assert [] == ['…']`. The flag was
  resolved when the recording turned busy.
- **Item 3:** `test_worker.py:1085: assert [(e.recording_id, e.detail["owed"]) for e in flags] ==
  [(rid, "patterns")]` failed with `assert [] == [('…', 'patterns')]`. Two runs got there:
  - The test's first version read `open_flags()` with its default kinds, which leave out
    `private-unwritten`. So it failed for a reason that had nothing to do with the fix. I fixed
    the test.
  - I then ran it with the item 3 rule switched off (`unknown = False`), and it failed for this
    reason. With the rule back, it passes.
- **Item 2:** `test_worker.py:1137: assert sweeps == ["starting"]` failed with
  `assert ['starting', 'idle', 'idle', 'idle', 'idle'] == ['starting']`: one whole sweep per tick,
  under `idle`.
- **Views:** `test_plaud_views.py:390` expected "(checking titles again, 12 of 40)" and got
  "(working)".

**What changed, by item:**

1. **A busy retry keeps the flag.** An open patterns flag stays while the last attempt that ended
   lists the recording unwritten **or busy**. Busy alone still never opens a flag. The test also
   holds the ping at the turn that is due.
2. **The retry.**
   - **Every tick:** `patterns_pass(retry=True)` re-checks only what the record's last ended
     attempt of this version left (unwritten and busy). That holds even after a later attempt
     raised, since the ended attempt checked the rest.
   - **No attempt of this version ended:** the retry raises `WholePassDue` and checks nothing.
     The worker then sweeps the whole archive at most once per turn: one `schedule_minutes`
     interval after the last whole attempt, and 20 minutes (the default) when the schedule is off.
     `WHOLE_RETRY_MINUTES_OFF` gives the reason in the code.
   - **Logging:** every retry is quiet, with no line per recording. The pass logs a failure once
     per class, and once when an attempt ends again. This happens in `_patterns`, not in `_step`:
     a retry that waits for its turn neither failed nor worked, yet `_step` logged "works again"
     for it and then "failed" again.
   - **The heartbeat during a retry:** a new phase, `rechecking`. Status shows it as "checking
     titles again", with "N of M" when the retry is whole.
     - `rechecking` is not in UNFINISHED, so it carries no `starting_since`, and `alive` reads it
       by the ordinary rules: a beat under 90 s, and progress within 15 minutes, bumped at each
       recording. It never reads STUCK for the start's time, and decision 48 is unchanged.
     - The phase goes back to `idle` when the retry ends, however it ends.
     - The test checks, during the retry, that the phase is `rechecking` and `alive` is
       `(True, None)`.
3. **A restart whose start pass raised.** The open patterns flags stay owed while the pass is
   failing or a whole sweep is due. The next retry re-checks the record's leftovers, and nothing
   else (the test counts the reconciles).

**Other changes in this commit.**
- The earlier S2 test now waits a turn for its whole retry.
- The baseline tests cover `retry=True` from the record, and `WholePassDue`.
- The spec changed in §13.1 case 5, §9.2 and §15.2, with a Revisions line. No decision and no
  scenario changed.
- The runbook row and `flags.py`'s docstring follow.

**Checks.** `make e2e` gave 143 passed and 1 setup error, at load averages of 58–78. The error was
`test_the_list_rows_text_stays_readable_in_every_state`: its demo server didn't answer `/healthz`
within 30 s, and its log was empty. Run alone it passed (2 passed), and the next two full e2e runs
passed 144.

### Item 4 (`0d04971`)

**The failing line**, for each class:
`test_plaud_views.py:475: assert panel["summary"]["sentence"] == stopped.removesuffix(".")`. Every
class got "check the archive disk, then the worker's log".

**What changed.** The sentence follows the class, as `flag_sentence` words a flag. Each is a fixed
phrase with the class only:

| Class | Step |
|---|---|
| `OSError` and Python's own kinds of it | the archive disk and its permissions, then the worker's log |
| a `sqlite3` error, by name, or `StateError` | state.db's disk, then the worker's log |
| `KeepOutWordsError` | the stored keep-out words aren't a list, as `recordings plaud keep-out-words` says |
| anything else | the worker's log |

The `sqlite3` errors are matched by name because the views import no `sqlite3` at module level.
§15.6 changed, with a Revisions line, and the runbook row points at the step Status names.

### Item 5 (`f4479bb`)

**The failing line:** `test_repo_tools.py:99: assert long == []` failed with
`AssertionError: assert ['consent-add.sh:6 (170)'] == []`.

**What changed.** The header paragraph is wrapped at 100 again, with its words unchanged. A test
now pins that every comment line in `docker/*.sh` is at most 100 characters; code lines may run on.

### Item 6 (`44b2ad5`)

**The failing line:** `test_plaud_views.py:792: assert view["sync"]["summary"]["sentence"] ==
UNREADABLE_SYNC` got "Scheduled sync waits for the first catch-up (runbook)". The panel had read
normally, and showed the string's letters.

**What changed.**
- `_Read.words` reads the row through `baseline.enforced_words`, as the Writer does. A row that
  isn't a list raises `KeepOutWordsError`.
- `_guarded` already turns that into "can't be read now (`KeepOutWordsError`)". That fails closed,
  and the backups section stays.
- The details list only valid words.

### Item 7 (`94c2f91`)

**What changed.** CLAUDE.md now says the worker "retries at each tick while the last attempt left
recordings unchecked or raised".

**No failing line.** This is a wording change in CLAUDE.md, which no test reads, so nothing that
runs changed. The commit says so.

### Concerns

- **Two tests failed under load, and they aren't this round's.** Two `make check` runs at item 4,
  at load averages of 35–70 on this Mac (40-odd sessions), each failed one test that passed in the
  other run. Neither touches these changes. Both have the load-sensitive shape of the council's
  R6, so they belong with it:
  - `test_plaud_requests.py::test_one_request_id_sent_by_two_sessions_at_once_is_one_row`. One
    session got "database is locked" at `BEGIN IMMEDIATE`, and the other went past its deadline
    (`NotSaved`). Run alone it passed 5 times out of 5, taking 0.3–6.3 s.
  - `test_plaud_session.py::test_arrivals_count_from_the_later_of_the_start_and_the_last_refresh`,
    under `TZ=UTC`. It reads `plaud_arrivals` before the first draw with no `await until(…)`, so
    `seen` is empty. Alone, at load 66, it failed 1 run in 3. The likely fix is the `until` its
    neighbours use.
- **A whole-archive retry still blocks its tick.** It now comes at most once per turn and says
  "checking titles again" on Status, but at 10,000 recordings it still holds that one tick for
  minutes. Phase 2's split could move it off the main thread.
- **Someone else's file.** `docs/superpowers/plans/2026-10-23-stage-2b-phase-1-qa.md` shows as
  modified in the worktree, by someone else. I left it unstaged.
- **Left for Phase 2's split**, as the round asked: the `_patterns(again=)` flag argument; moving
  the patterns-owed rule into `flags.py`; the bare `"consent"` literal; `writer.py`'s in-method
  import.
