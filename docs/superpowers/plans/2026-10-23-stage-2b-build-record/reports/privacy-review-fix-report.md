# Privacy review fixes: report

Status: **DONE_WITH_CONCERNS**. All seven findings are fixed, plus the controller's mid-task
follow-up to fix 1 (1b below), each in its own commit with a test that failed before it. No fix
needed a change to one of Dan's decisions or a scenario's outcome. The concerns at the end are
behaviour notes, not open leaks.

Branch `stage-2b`, from `33faecb`. Not pushed. Every commit's message passed
`scripts/commitmsg.py` and carries BUILD-FAST's trailer. `make check` was green before each commit;
`make e2e` before each that changed `recordings_ui` or the frontend; `make e2e-live` before each
that changed the run.

## Commits

| SHA | Item | Subject |
|---|---|---|
| `163d952` | 1, 2 | fix(plaud): 🤖 no Release preview for a re-keyed match, nor while Plaud's IDs look changed |
| `0d1dc12` | 3 | fix(plaud): 🤖 a rebuilt state.db is found before every run's first listing |
| `52479bb` | 1b | fix(plaud): 🤖 the re-keyed bar reads rekey_of itself, not what keeps the row out |
| `d9af194` | 4 | fix(plaud): 🤖 an invalid keep-out word refuses every request that lists or imports |
| `45b3adc` | 5 | fix(plaud): 🤖 the run's own consent read at its start is the guarded read |
| `51e5102` | 6 | fix(plaud): 🤖 Sync everything stops checking under a hold recorded mid-run |
| `7f40e0c` | 7 | fix(backup): 🤖 a backup and the mirror refuse while the Plaud sign-in is inside the archive, or can't be placed |

1b came in from the controller (a security review of `163d952`) while item 3 was mid-edit, so it
was committed after item 3, before item 4.

## 1 and 2. Release's preview: a re-keyed row, and an IDs-changed hold (High)

**What was wrong:** confirmed as the review traced it. `release.may_preview` accepted
`kept_by == "rekeyed"`, so `worker._release_preview` read the current title, wrote it to
`plaud-cache.db` and the dialog showed it; nothing on the preview path read `K_HOLD`.

**What changed:**
- `may_preview` refuses a re-keyed row with no listing read and nothing cached: "No title shown: it
  may be a consent-listed or kept-out recording back under a new ID". The shared early check of
  `release` and `recordings plaud release` is now `release.may_ask`, so a re-keyed row is still let
  in, and `may_release` still checks the consent list as it is now and the current title against
  every enforced word, the title never returned or stored.
- `release.titles_hidden`: the preview (and a review row's Show title, the same request) is refused
  while `K_HOLD` shows Plaud's IDs changed (a record that can't say reads as changed), or the newest
  Compare's `titles_hidden` says so: "No title shown while Plaud's IDs look changed: see Status". A
  large-shrink hold, which hides no titles, still previews.
- The Sync screen: Release on a re-keyed row sends no preview; its question is drawn from the row's
  date, time and duration ("Let this recording (…) in?"), and Let it in sends `release`
  (`ReleaseDialog` already asked about "this recording" when the title is empty). The preview
  buttons are off while the view's `titles_hidden` is set.
- Defence in depth (the review's below-Low note): the web app's `release_preview` drops the title
  when state.db says either bar came up after the worker wrote the preview.

**Failing lines before the fix:** `assert (reply.state, reply.message) == ("failed",
NO_TITLE_REKEYED)` → `('done', 'Release: see its question')`; the same with NO_TITLE_HELD; the web
app's `shown["title"] == ""` → 'Synthetic grant timelines'; SyncPage.test.ts `expect(sends)
.toEqual([])` → `[['release-preview', …]]` and the buttons-disabled line → `expected false to be
true`.

### 1b. The controller's follow-up: the bar on `rekey_of` itself

A security review of the first commit, sent by the controller mid-task: `kept_by` answers
"was-title" first, so a row carrying both `kept_out_at` and `rekey_of` (a re-keyed match whose
title took the keep-out word and then lost it) was still previewed. Fixed as asked, as its own
commit: `release.rekeyed(row)` (`rekey_of` set and not released) in `may_preview`, in the web
app's `_preview_barred`, and in the Kept out view, which now sends such a row as `by: "rekeyed"`, so
SyncPage asks with no title. Failing lines before: `('done', 'Release: see its question')`;
'Synthetic grant timelines'; `('was-title', True) == ('rekeyed', True)` (the screens test, through
the worker's turns on the fake: re-keyed, renamed with the word, renamed without it).

## 3. A rebuilt state.db detected at any run's start (Medium)

**What was wrong:** confirmed, and the catch-up-first test showed the leak: a recording kept out
only by the lost records was imported.

**What changed:** `sync.rebuild_check` runs `gate.detect_rebuild` first in every run that lists,
under that run's hold: `run_sync` (the worker's turns and requests, the CLI's syncs, Sync
everything) and `resync`; the CLI's `sync` and `--full` run it before their own gate checks, which
moved inside the hold. A check that raises lists nothing ("error: `OSError`", exit 1). The worker's
failed start check is retried at each tick. `record_rebuilt(unless_marked=True)` reads the mark
inside the transaction that writes it, so the rebuild is recorded once even when two runs check at
once; listing rows written before it with no title hash are marked for review in the same
transaction. A `--catch-up` that finds the rebuild is refused as not needed (the gate is open, as
§11.6 says); a dry run says so in its notes.

**Failing lines before:** dry run first: `(db.get(K_REBUILT) or {}).get("by") == "detected"` →
None; catch-up first: `kept.plaud_id not in live.here()` failed (imported); a failing check:
AttributeError (no `detect_rebuild` in `sync`; the run listed); the worker's retry: `w.gate.open`
False; `unless_marked`: TypeError; the defence-in-depth mark: `needs_review` False; Re-sync: exit 1,
GATE_REFUSAL.

## 4. An invalid keep-out word refuses the importing requests too (Low)

**What was wrong:** confirmed: with `keep_out_word = "two words"`, Sync now answered "Synced: 2
imported (0 private) · All here", and Compare, Import, Re-sync and Sync everything ran too.

**What changed:** `requests.NEEDS_WORD` (Compare, Sync now, Import, Re-sync, Sync everything,
Release and its preview): the worker refuses them before any Plaud call, as it refuses a kind the
gate holds, with the scheduled sync's own phrase ("configuration: `keep_out_word` is invalid").
Compare is refused too: it stores titles it couldn't judge against the word. Undo, Park, Unpark,
Keep it here and both Accepts still run. `run_sync` and `resync` refuse it themselves (exit 78), and
`recordings plaud release` now does (its Undo still runs); the CLI's `sync` and `resync` already
did. The Sync screen's Compare is off with it; SyncView gains `word` (and the canned view a
`"word": null`), so Release, Show title and every Re-sync button (Details, the selection bar,
Status's failing line) are off with it.

**Failing lines before:** each of the seven kinds' `assert (reply.state, reply.message) ==
("refused", BAD_WORD)` (Sync now `('done', 'Synced: 2 imported (0 private) · All here')`; Release
and its preview `('failed', "Nothing to release: it isn't kept out")`); the run in each mode `('ok',
0)`; the CLI's release exit 0; the views' Compare reason None; SyncPage.test.ts `expected false to
be true`; SelectionBar.test.ts `expected null to be 'configuration: …'`.

## 5. The run-start consent read uses the guarded read (Low)

**What was wrong:** confirmed: with the consent file changing at every look, the run read it bare,
classified the listing as readable and stored both titles in the cache (the import's own guard then
stopped the imports).

**What changed:** `ConsentGuard.read()`: the guard `current()` uses (settle, before-and-after), with
a stop returned rather than raised; a read that never settles is a stop of its own ("the consent list
keeps changing", no IDs). `current()` is now `read()` plus the same raises as before. `run_sync`
builds its importer first and reads the list through its guard, so a list caught mid-write is a
consent stop for the run: nothing fetched, no title of a recording not here stored, the run ending
"imports stopped: consent list". The import's first per-recording check reuses the guard's reading.

**Failing line before:** `assert live.db().get(K_COMPARE)["titles_hidden"] ==
TITLES_HIDDEN_CONSENT` → `None`.

## 6. Sync everything stops checking on a mid-run hold (Low)

**What was wrong:** confirmed: with a hold recorded after the second of four checks, the run went on
and answered "Sync everything: all 4 checked".

**What changed:** `editsync.Checker` reads `K_HOLD` before each check and stops the run with the
importer's ImportStopped("held: see Status"), as a consent stop does, before any fetch. That stops a
sweep in a sync, a long Re-sync and Sync everything alike (§14.2: "The sweep pauses under any
hold"). Sync everything stopped so (or whose own listing held, or whose imports a hold stopped) is
left unfinished, with no manifest and no finished record; `run_full` doesn't start while a hold is
on, leaving an unfinished run as it was; the worker's tick doesn't resume it while the hold is on
and does once it clears, checking only what it hadn't. The reply: "Sync everything: failed (held:
see Status) · See Status".

**Failing line before:** `assert (reply.state, reply.message) == ("failed", "Sync everything:
failed (held: see Status) · See Status")` → `('done', 'Sync everything: all 4 checked')`.

## 7. Tokens never in a backup (Low)

**What was wrong:** confirmed for both triggers: a `token_store` inside the archive was backed up
and mirrored (restic and rsync ran), and a bad `schedule_minutes` made `token_folder` fall back to
`/plaud`, so an extra path holding the configured sign-in's folder passed.

**What changed:** `backup.token_folder` reads `[plaud] token_store` alone (the default when
`[plaud]` or the key is absent) and answers None when it can't be read. `backup.sign_in_refusal`
gives the fixed reason, naming the setting and never the token: "[plaud] token_store lies inside the
archive …" (TOKEN_IN_ARCHIVE) or "[plaud] token_store can't be read …" (TOKEN_UNKNOWN). `run_backup`
refuses with it before restic runs, `run_mirror` before rsync does, and doctor reports it in every
role (`_extra_paths`); web and backup's "is mounted here" check reads the same folder.
`never_backed_up` takes an unknown folder (it still checks `/plaud`). An extra path holding the
token was already refused; it now is whatever else in `[plaud]` is wrong.

**Failing lines before:** `pytest.raises(backup.BackupRefused)` → DID NOT RAISE (restic ran) for
the archive (two spellings), an unreadable and a relative `token_store`, and a bad
`schedule_minutes` with the configured folder in `extra_paths`; the mirror's `MirrorRefused` → DID
NOT RAISE; doctor → AttributeError (no such phrase; nothing reported).

## Not built (the brief's "for Dan")

`check_api_base` / `check_token_url` allow-lists, decision 16's gap, a new keep-out word
re-checking titles already here, the re-key cache for late-listed recordings.

## Concerns and questions

No fix needed a change to one of Dan's decisions or a scenario's expected outcome. Things to know:

1. **The rebuild check's cost.** Every run that lists now calls `detect_rebuild` first. With a mark
   row there it is one read. With none (before the first 2b snapshot, including after a catch-up
   that imported nothing) it scans the archive's Plaud sources for a 2b snapshot at each run, every
   scheduled turn included, until the first 2b snapshot (with its mark) is written. Bounded, and
   it was already done at each worker start; worth knowing if a turn looks slow then.
2. **The CLI's gate checks moved inside the hold.** `recordings plaud sync` and `--full` read the
   gate after the rebuild check, under `plaud`. Before the first catch-up, with another run holding
   `plaud`, they now answer busy (75) instead of "the first catch-up hasn't run" (1). A catch-up on
   a rebuilt `state.db` is refused as not needed, with the rebuild's sentence, since the rebuild
   sets `catch_up_done_at` (§11.6).
3. **Sync everything pressed during a hold** is now refused without recording a finished, failed
   run (K_FULL is left as it was, so an unfinished one resumes once the hold clears); `ops_runs`
   still gets the press's line. Before, it finished failed and waited a month. No scenario pinned
   either.
4. **The IDs-changed bar also reads the newest Compare's `titles_hidden`,** not only a recorded hold
   (a Compare measures but never records a hold). Fail closed; it clears at the next listing that
   measures fine.
5. **Undo on a re-keyed match that a title also kept out** (both fields set) doesn't reopen the
   re-key flag (`unrelease` re-flags only a row with no `kept_out_at`); the row still reads as the
   re-keyed one and asks with no title. Left as is: it stays kept out.
6. A wording nit: the web app's warning when Release's question can't read state.db still says
   "Release's next sync couldn't be read", though the title is now dropped then too.

Tests: every commit had `make check` green (ruff; pytest twice, local and UTC; Vitest; gitleaks).
At `7f40e0c`: pytest 2537 passed (170 deselected) both runs, Vitest 465 passed, no leaks. `make
e2e` (146 passed) before `163d952`, `52479bb` and `d9af194`; `make e2e-live` (14 passed) before
`0d1dc12`, `45b3adc` and `51e5102`. Item 7 changes neither the run nor the screens, so it had
`make check` only. The run stopped once on a network error after that check had passed and before
the commit; the tree was unchanged, and was committed as checked.
