# Area A4: importing, keeping out, Release, and gone: report

Status: **DONE_WITH_CONCERNS** (everything in A4's scope is built and tested, with the run itself
left to A5 as the dispatch said; four readings past the spec's letter, all failing closed, are
questions below; two small gaps are listed under "What's left").

Branch `stage-2b`, from `7e19224`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `fa3cce5` | feat(plaud): 🤖 importing one recording, and privacy from the listing alone |
| `f289662` | feat(plaud): 🤖 Release: its preview, Let it in, Undo and the CLI |
| `805ae6f` | feat(plaud): 🤖 the review after a rebuild, plaud review, and the gate's later states |
| `d8735a7` | fix(plaud): 🤖 a private the listing owed resolves its flag when the title changes back |

Every message passed `scripts/commitmsg.py` and carries BUILD-FAST's trailer. `make check` was
green before each commit; `make e2e` (145 passed) before `f289662`, which changed
`recordings_ui/plaud.py` and `shiny_app.py`, and again before `d8735a7` (covering `805ae6f`'s
two label entries there).

## What was built, by spec section and scenario

### §11.5, importing one recording: `plaud/importer.py`

`Importer.import_one(listed, *, listing_at, review_ok=False) -> ImportResult` is the function A5's
run calls per waiting recording (`importer_for(...)` builds one from the settings).

- **Before any byte**, whatever the run checked: the gate (only `catch_up=True` imports while it is
  closed), a hold (`K_HOLD`), the listing's age (`ListingStale` past one schedule interval, 20
  minutes with the schedule off), the consent list as it is now (`ConsentGuard`: read again when
  its file's inode, size, mtime or ctime changed, and trusted only once the file is a tick (2 s)
  old, the phase-end list's Task 13 row; a stop, or a file still changing after 5 waits, is
  `ImportStopped`), the keep-out words (configured plus enforced, `Writer.keep_out_words`; words
  that can't be read are `ImportStopped`) on the listing's title (a match records the ID kept out,
  by its word), and the ID's record: kept out and not released, re-keyed and not released, under
  review (unless `review_ok`), parked, or already here (`flags.listed_here`, strict).
- **The envelope** bare (`client.envelope(..., enrich=False)`, decision 37; the client's new
  `enrich(env)` fetches the blocks). As soon as it answers: its `id` must be this recording's
  (else `PlaudBadResponse`), the consent list is read again, and its title is checked against the
  words. Either match writes nothing of it (a title match records the kept-out word). Only then
  `enrich`. A block whose link expired fetches the envelope again, once, with the same checks.
- **The snapshot:** `_meta` replaced by `client.stamp_meta`'s time fields and `tool:
  "recordings"`, plus `enriched_from_links`; never `data_link_errors`. Stored as
  `json.dumps(env, indent=1)`, as audio-router did.
- **Tags:** `private` (`by: auto`) in `Incoming.tags` when the listing's or the envelope's title
  matches, as given and folded (`matches_auto_private`). C5.01's import half.
- **The disk guard:** a disk already at "stop" before the download, and `admit(Content-Length)`
  refusing when twice the length would leave less than `stop_free_percent`: `ImportStopped("imports
  stopped: the disk is too full to import")`, nothing written, the scratch file gone.
- **The audio:** into `.tmp/plaud-dl/<id hash>-<uuid><suffix>` (`plaud/scratch.py`:
  `download_dir` retries `mkdir` once; the suffix is the link's when it names an audio type, else
  `.mp3`), with the client's stall rule, Range resume and one fresh link (the envelope again, with
  its checks: a fresh envelope that took the keep-out word stops the download). Measured with
  `media.probe` as the 2a import does (`wave` for a WAV, ffprobe and a decode otherwise; the tests'
  audio is WAV, so no test needs ffprobe). Unmeasurable media is that recording's failure.
- **A hash merge into a flagged recording** (one of its Plaud IDs consent-listed or kept out, Keep
  it here included, or an open privacy flag): asked before `add` (`Writer.holding`, new), the
  download is discarded, and the new ID is recorded `rekey_of` the flagged recording's ID with an
  open `rekeyed` flag (`by: audio`): excluded until Dan confirms, as §11.4 step 6's match is.
- **Add:** `gate.mark_snapshot` first, in its own transaction, then `Writer.add` with a `plaud`
  RawSource (ref the ID as listed) and `Incoming.origin = "plaud-import:<run ID>"` (new: the
  revision's origin in state.db).
- **Reconcile and record:** `Writer.reconcile_plaud` (a failure there leaves the recording here,
  `reconciled=False`), a `plaud_checks` row (unsettled), the listing row's title hash and duration
  with `import_failures` and `needs_review` cleared, and an `imported` or `arrived-private` event
  (detail: run, merged).
- **Failures:** a recording-level error is tried once more, then counted
  (`record_import_failure`: `import_failures`, `next_import_at` 20 minutes doubling, at most a
  day); `LockTimeout` is `busy`; a run-ending Plaud error is raised as itself; an archive-level
  error (sentinel, state.db, index, ENOSPC) is `ImportStopped("error: `<Class>`")`.
  `ImportResult.attempt` and `.success` follow §11.7.
- **The sweep at the worker's start** (§9.2 step 2): `Worker._sweep_downloads` →
  `scratch.sweep_downloads`, under a non-blocking try of `plaud`.
- C5.02 (never requested, the request log), C5.09 and C5.12 (the import's half), C5.29 (never
  requested), C8.02 (a 2b import validates, `selfdoc.validate(deep=True)`).

### §13.1 cases 2 and 3 from the listing: `plaud/listing_privacy.py`

`privacy_from_listing(writer, db, rows, here, *, now) -> ListingPrivacy`, for every listing,
complete or partial. Each listed title of a recording here whose hash isn't its marker (never
`plaud_listing`) is checked against the patterns and words; a match adds `private` through
`mutate` with its note (`title changed`; `kept out by title` for a word alone; `first check` with
no marker; `the sync's record was rebuilt` after a rebuild), and a word records a late keep-out
(`flags.record_late_keep_out`: its IDs kept out, one flag, none after Keep it here). The marker
moves only once the result is written. A `private` it can't write opens a `private-unwritten`
flag at once (detail `owed: title`, the class as `cause`); busy is retried at the next run.
`d8735a7`: that flag resolves when a later title, checked, matches nothing.

`privacy.judge` gains one rule (spec §13.2's new sentence): a title whose snapshot the marker has
judged is never due again under case 2. Without it, a marker the listing moved to a title no
snapshot carries yet would make every older title due at the next reconcile (a reindex, the
patterns pass), and put back a `private` Dan removed. C5.01, C5.03, C5.17 (100 runs with
reconciles between them).

### §12.2's Release: `plaud/release.py`, the worker's handlers, the cache, web, the CLI

- `release-preview`: refused with no title fetched for a consent-listed ID, during a consent stop,
  for one already here, for one nothing keeps out (review rows may be previewed: §11.6's "title on
  demand"), and while its current title still carries a keep-out word. Otherwise the title from
  `Worker.listed_now` (the run's newest listing in memory if fresh, else a fresh listing, never
  the envelope) goes to the cache's new `release_preview` table only, keyed by the request's own
  `request_id`; the reply is "Release: see its question" with the Plaud ID.
- The web app's `follow` (shiny_app.py) attaches `preview` to the done reply of the asking
  session: `recordings_ui.plaud.release_preview` → `cache.take_preview`, read and deleted in one
  writable transaction (`mode=rw`, never creating the file): BD1, P6, P8. Shown once; gone after 10
  minutes (`Worker._expire_previews`) or as soon as the consent list or a word catches it
  (`Cache.purge_titles`).
- `release`: checked again against the consent list and the current title; `released_at` now, a
  `released` event, a re-keyed match's flag resolved; reply "Released · comes in at the next sync
  (HH:MM) · Undo". `unrelease`: kept out again until it came in (a re-keyed match's flag back).
- `recordings plaud release <plaud-id> [--undo]`: ID, date and duration (from the cache's
  keyed-out dates), y/N, the same checks against a fresh listing. Exit 0, 1, 69, 77, 78.
- Kept out stays kept out after a Release: a listing whose title takes the word again keeps the ID
  out again (`write_listing`'s `_KEEP_AGAIN`, `classify`'s `kept_out_now`; the import's
  `record_kept_out` likewise).
- The views show the title of a recording Release let in and the sync then imported (phase-end
  list, Task 19's row).
- `requests.HANDLED` and `worker.HANDLERS` gain the three kinds (CLAUDE.md's recipe walked, the
  pins updated). C5.05, C5.07 (and C5.06's server half).

### §11.6: the mark, the interrupted catch-up, a rebuilt state.db, the review marks, `plaud review`

- `gate.mark_snapshot` (one counted `plaud_state` row, `snapshot_mark`) before every 2b snapshot:
  the import's `Writer.add` (B1's `add_snapshot` must call it too).
- `gate_now`: marks and no `catch_up_done_at` is closed with `GATE_INTERRUPTED` ("The first
  catch-up didn't finish: run `--catch-up` again (runbook)"). C5.19.
- `gate.detect_rebuild` at the worker's start (`Worker._detect_rebuild`, after the gate): only while
  no mark row exists, a pass over the snapshots for `_meta.tool == "recordings"`; found, it records
  `rebuilt_at`, the gate's opening and a mark in one transaction (`PlaudDB.record_rebuilt`). C5.22,
  C5.24.
- `compare.review_marks` / `with_review_marks` in `take_listing`, before `classify`: every listed ID
  not here, with no `plaud_listing` row, made (creation, else start time) before `rebuilt_at`; one
  with no time, or every one while the record can't be read, too. Written with the listing
  (`ListingEntry.needs_review`, kept once set). Review rows show no title in the cache (A3's rule).
  C5.25 (A5 passes `review_ok` only for IDs Dan named).
- `recordings plaud review [--json]` (job `plaud-review`): `rebuilt_at` now, and every listing row
  not here marked at once; the runbook's "Restoring for real" runs it between `reindex` and `rc up
  -d` (BB1). C5.27, C5.28.
- Decision 15: case 4 after a rebuild records "the sync's record was rebuilt" (`privacy.rebuilt_why`),
  with Details' wording.

### §12.3, gone from Plaud

Built by A3 (the small shrink's settling and Accept mark recordings gone; a listing that shows one
again clears it). A4's import adds only "nothing is imported twice": an ID listed again is here,
so `import_one` returns `here`. The sweep's skip is B1's; the Status and Sync screen rows A6's.

### The fake server (§10.6 step 2)

`FakeAccount.envelope`: each recording's `files/{id}` in either form, with an inline transcript, a
note behind a `data_link` and the audio behind `presigned_url`, both on the storage host
(`notes/<id>.md`, `audio/<id>.wav`). Dan's edits: `rename`, and `envelope_name` /
`envelope_untitled` (a title that differs between the listing and the fetch). Still importable
outside tests. `packages/core/tests/plaud_scene.py` (the `scene` fixture): an archive, its Writer,
the gate open, the fake account, a consent list and an Importer, for A5 and A6 to reuse.

### Docs

CLAUDE.md (the scratch-download sentence, the fake's envelopes, the cache's preview), FORMAT.md's
`source/` line and its demo copy (spec §19), the runbook's restore step, the user guide (how a
recording comes in; renaming after it came in; keeping out and Release; after a rebuild), spec
§13.2's two sentences, 16 scenario rows and five Revisions lines, eight rows in the guesses file.

## Tests

New: `test_plaud_import.py` (28), `test_plaud_listing_privacy.py` (7), `test_plaud_release.py`
(17), `test_plaud_release_ui.py` (5), `test_plaud_rebuild.py` (13); `test_worker.py` (+1, and the
start order), `test_plaud_views.py` (+1); the HANDLED pins. Mutation checks (each guard removed,
its test failing): the judged-snapshot rule, `_KEEP_AGAIN`, the review marks reaching `classify`.
`make check` before each commit: the last run, before `d8735a7`, pytest 2,341 passed (and again
under `TZ=UTC`; 2,304 at `fa3cce5`, 2,327 at `f289662`, 2,340 at `805ae6f`), Vitest 458, ruff
clean, gitleaks clean; the one warning is the existing `test_demo_guard.py` fork warning.
No flaky test seen.

## From audio-router, and from the spec alone

- **From audio-router** (`src/audio_router/archive/sync.py`, `sources/plaud.py`, `store.py`,
  `schemas.py`; read only): the audio is the envelope's `presigned_url`; audio stored as `.mp3`;
  `stamp_meta` from the listing's row; the snapshot as `json.dumps(env, indent=1)`; `_meta`'s keys.
- **From the spec alone, or guessed** (the guesses file's eight A4 rows): the suffix; the time from
  `start_at` else `created_at` (S12); the consent file's tick-old trust (S11); the listing's
  freshness; the failure backoff; an envelope naming another ID refused; the audio-hash rule's
  re-keyed flag; the mark as one counted row.

## Questions for Dan

1. **The re-key gap you already know** (a new ID listed beside a still-listed consent-listed or
   kept-out ID with the same start time and duration). The import can narrow it cheaply without
   touching decision 16's match: in `Importer._before_any_byte`, skip for this run (fetch nothing,
   count no attempt) any ID whose listed start time and duration are within 1 s of a cache
   `keyed_out` entry of kind consent or title, so it waits until the old ID vanishes and the match
   catches it. One cache read per import. Not built. The audio-hash rule doesn't help here: a
   consent-listed recording was never imported, so nothing in the archive holds its bytes.
2. **Release's preview is refused while the title still carries the keep-out word** (§12.2 refuses
   only Let it in for that). It saves a press that would be refused, and never shows that title.
   Keep it, or show the question and refuse at Let it in?
3. **A download whose audio a consent-listed or kept-out recording here already holds** is kept
   out (as §11.5 says) and also flagged "may be … back under a new ID: confirm", so Dan sees it;
   the spec says only "counts as excluded". Keep the flag?
4. **"If that listing is older than `schedule_minutes` (always, with the schedule off), the run
   lists again":** read as the run's own listing in memory, refused (`ListingStale`) once older
   than one interval, 20 minutes with the schedule off. A5's request runs list first anyway.

## What's left

- **The run** (A5): the import is a function; nothing calls it from the worker's turn, the
  requests or the CLI yet.
- **B1:** `Writer.add_snapshot` must call `gate.mark_snapshot` first; the sweep skips kept-out,
  consent-listed and gone recordings (C5.03's "nothing fetched again, even when its transcript
  appears" is then whole).
- **Not built:** seeding markers from `plaud.title_seen` after a rebuild (§13.2's last bullet):
  every recording gets case 4 instead, so a removed `private` may come back once with "the sync's
  record was rebuilt" (call 11 accepts that).
- The review marks for rows already in `plaud_listing` happen only through `plaud review`; a
  detected rebuild starts with an empty `plaud_listing`, so every ID is new then.

## What A5 and A6 need from me, by name

- **A5, per run:** `importer_for(writer, worker.plaud_client(), consent_list=settings.consent_list,
  timezone_name=cfg.default_timezone, run_id=…, schedule_minutes=settings.schedule_minutes,
  stop_free_percent=disk_thresholds(cfg)["stop"], catch_up=…)`. After each listing (complete or
  partial): `privacy_from_listing(writer, db, listing.rows, here, now=)` (KeepOutWordsError: nothing
  judged). Then, for WAITING rows (and REVIEW rows only when named, `review_ok=True`), skipping
  rows whose `plaud_listing.next_import_at` is in the future unless named, up to 20 in a scheduled
  run: `importer.import_one(listed, listing_at=listing.at)`. Catch `ImportStopped` (its `phrase`
  is the run's §15.6 phrase; `STOPPED_DISK` is the disk cause), `ListingStale` (list again), and the
  client's run-ending errors. `ImportResult.attempt` / `.success` feed §11.7. Set
  `worker.newest_listing = listing` after a complete listing (Release reads it). The catch-up:
  `catch_up=True`, then `K_CATCH_UP_DONE` at its clean end. `Worker.plaud_client()` is the one
  client (one Throttle; its waits bump the heartbeat and stop on SIGTERM).
- **A6, the views:** Kept out rows: `release.kept_by(row)` ("was-title" / "rekeyed") for
  `release`, `compare._released(row)` and `release.released_line(next_sync)` for `released`. Review
  rows: `CompareRow.review`; their "Show title" sends `release-preview` (the handler allows review
  rows). The rebuild line: `K_REBUILT` and the count of `needs_review` rows not here. Flags:
  `rekeyed` with detail `by: "audio"`; `private-unwritten` with `owed: "title"` (worded by its
  `cause` as any other). Events: `imported` / `arrived-private` (detail `run`, `merged`), `released`.
  The gate's `GATE_INTERRUPTED` reason already reaches the views through `gate_now`.
  `release_preview` is wired in `shiny_app.follow`; the frontend's ReleaseDialog takes `preview`.
