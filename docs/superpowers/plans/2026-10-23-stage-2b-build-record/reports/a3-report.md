# Area A3: the listing, the compare, and the hold: report

Status: **DONE_WITH_CONCERNS** (everything in A3's scope is built and tested; three readings the
skipped spikes would have settled are recorded as guesses, and two scenario rows are half A3's,
half a later area's. Nothing blocks A4 to A6.)

Branch `stage-2b`, from `26fa571`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `5d64755` | feat(plaud): 🤖 the listing, the compare, the cache and the hold, with Accept this listing |

One commit: the listing, the compare, the cache, the hold, Accept's two doors, the worker's tick
purge, the backup's refusal and the docs are coupled (the tests of each read the others, and the
spec, CLAUDE.md, the user guide and the guesses file describe the same code). The message passed
`scripts/commitmsg.py`; its trailer is the overnight build's.

## What was built, by spec section and scenario

### Main spec §9.0 and the Phase 1 plan's decision 4: `sources/base.py`, `sources/plaud.py`

- `sources/base.py`: `Candidate` (ref, title, recorded_at, duration_ms) and the `Source` protocol
  (`name`, `candidates()`). `fetch()` (A4's import) and `check()` (B1's) join it with their first
  implementation, as its docstring says; a protocol method with no implementation would be
  untested code (decision 4).
- `sources/plaud.py`: `PlaudSource(client, page_size=PAGE_SIZE, max_pages=500, clock=…)`.
  `list_all() -> Listing` and `candidates()` (raises `ListingIncomplete` for a bad or partial one).

### §11.2, the listing (`PlaudSource.list_all`, `Listing`)

- Pages until an empty page through `client.list_page` (so through A2's Throttle); a short page
  never ends it (the fake's `clamp`).
- Repeats: one row per canonical ID (the first seen), `repeats` counted.
- Malformed rows: `malformed` counts rows with no ID and rows with no title field (the client's
  count); `malformed_limit(rows) = max(2, int(rows × 1%))`, over every row served. More is bad
  (`why = TOO_MANY_MALFORMED`). A titleless row is still listed, with `title None`.
- An HTML page or the wrong shape (`PlaudBadResponse`), or any other one-call error: bad, with no
  rows. A run-ending error (`client.ends_the_run`) after a page with rows: partial, with its rows;
  on the first page: bad. `Listing.error` keeps the exception (compare=False, repr=False) so A5
  can record a refused sign-in or a rate limit's wait; `why` is its class name. The cap: bad
  (`why = CAP`).
- The census: `id_form(listed_id)` is `of_`, `hex` (each in any case) or `other`;
  `Listing.census` counts the kept rows; `Listing.forms()` maps canonical ID → form.

### §11.3, the buckets and both equations (`plaud/compare.py`)

- `classify(rows, here=, consent=, words=, listing=, keyed_out=, patterns=, conditions=)`:
  here (by canonical ID, `Here` from the index strictly plus pending files: `here_from_archive`,
  raising `IndexUnavailable`), then excluded (`consent`, `title` for a word now, `was-title` for an
  earlier keep-out not released, `rekeyed`), then waiting or missing with §15.1's state, the first
  of: the gate, a consent stop, a hold, review, parked, failed 3 times, the disk, the schedule off,
  auto-import off. A consent-listed ID's title is dropped (`replace(listed, title=None)`) before
  anything else reads the row. A row here keeps its title in memory and its `word` (for A4's late
  keep-out). Released = `released_at >= kept_out_at`.
- `compare(...) -> Compare`: `equation` {listed, here, waiting, excluded, missing,
  arrive_private}, `archive` {from_plaud, here, gone, absent} counted by recording, `consent`
  {ids, on_plaud}, `titles_hidden`, `import_all` {count, review_more} (review and parked rows left
  out), census, repeats, malformed. `Compare.record()` is K_COMPARE's value; `lines(result)` the
  dry run's lines.
- **C1.01, the property test** (`test_plaud_buckets_property.py`): 40 seeded random accounts
  through the fake server (both forms, an `other` ID now and then, repeats, clamps, consent and
  keep-out rows, late keep-outs and late consent listings already here, kept out before, released,
  review, parked, failing, a recording with two IDs, gone and absent recordings, a consent stop,
  and partial listings): every listed ID once, in the bucket an independent reading of §11.3
  gives, and both equations add up. A mutation of the release rule fails 3 seeds.

### §9.5's cache (`plaud/cache.py`) and P1

- `<state>/plaud-cache.db` (`CACHE_NAME`, `cache_path`), not WAL: `compare_rows` (the compare's
  rows for every listed ID not here: bucket, state, excluded_by, start, duration, title only when
  `title_shown`, arrives_private, review), `keyed_out` (each consent-listed or kept-out ID listed:
  its kind, start and duration, forgotten 30 days after it was last listed), `meta.compare_at`.
- `title_allowed(pid, title, consent=, words=)`: never with no title, during a consent stop or
  before the list was read, for a consent-listed ID, for a word match, or with words that can't
  be read. The compare also refuses titles while a held listing shows Plaud's IDs changed, and for
  review rows.
- **The tick purge (P1):** the worker's new step "cache titles" (`Worker._purge_cache`, after the
  consent blocker) calls `Cache.purge_titles(consent=self.consent, words=enforced_words)` when the
  list in force, the words or the file changed; a `KeepOutWordsError` purges every title.
- **C5.15, the cache half:** a consent stop, and a hold with an ID change, leave no title in the
  cache (`test_plaud_compare.py`, `test_plaud_hold.py`), and the tick nulls one the list names
  later (`test_plaud_accept.py`). The Sync screen half is A6's.
- `backup.never_backed_up` refuses an `extra_paths` entry that is `plaud-cache.db` in the state
  folder (`CARRIES_CACHE`), so doctor reports it in every role and the backup refuses it (rows in
  `test_backup.py` and `test_doctor.py`; §21's "Backups" line).

### What a complete listing writes (`record_compare`, `PlaudDB.write_listing`)

- `plaud_listing` in one transaction: each listed ID's row (the ID as listed, first/last seen,
  absent cleared), a title hash and duration only for an ID here that is neither consent-listed,
  nor kept out, nor caught by a word now; a newly kept-out ID dated with its word, kept once set;
  a re-key match; every other row not already absent, absent since now.
- Recordings gone from Plaud listed again: `gone_since` cleared (`PlaudDB.back_from_gone`).
- A `rekeyed` open flag per new match (detail `of`: consent, kept-out or several); resolved once
  the consent list names the new ID (Dan's `make consent-add`).
- The cache's rows and keyed-out dates, and `K_COMPARE`.
- The phase-end list's Task 6 row: `State.forget` now also reads the recording's Plaud IDs from its
  last committed revision, so a title hash the listing wrote before any marker goes with it
  (`test_forget_clears_a_title_hash_the_listing_wrote_without_a_marker`).

### §11.4, the hold (`plaud/hold.py`)

- `measure_against(forms, last_good=, here=, excluded=, waiting=) -> Measure` and `measure(forms,
  db=, here=, excluded=)`: newly absent recordings here (plus a waiting small shrink's still
  absent), excluded IDs the last good listing had and lacks now, census change (a new form, or a
  shared ID in another form; shares never compared), unreadable (`other`) count, overlap,
  looks_new, here_count. With no last good listing: against the archive's Plaud IDs, only overlap
  and unreadable can hold.
- `Measure.reasons` / `held` / `ids_changed` / `large` / `refused` / `sentence()` /
  `run_phrase()`; `verdict(m)` → "held" | "small-shrink" | "good" (health.Listing's values).
- `record_verdict(db, forms, m, at=, now=)` (a sync only): held → `K_HOLD` (since, at, reasons,
  sentence, accept, refused, ids_changed, unreadable, census_change, newly_absent, excluded_absent,
  looks_new, listing: the record Accept installs), `plaud_listed` 'held', and the `hold` blocker,
  in one transaction. Good → `K_LAST_GOOD_LISTING` (at, count, census, ID-set hash,
  form_changed_at), `plaud_listed` 'good', K_HOLD and the blocker deleted; a small shrink waits in
  `K_SMALL_SHRINK` {absent, since} and settles on a good listing at least an hour after it first
  showed with the same absent set (`PlaudDB.mark_gone`: `plaud_checks.gone_since` and a `gone`
  event); a changed set restarts the wait; a set that came back ends it.
- `accept(db, consent=, now=) -> Accepted(state, message, gone, why)`: nothing held → "Nothing to
  accept: no listing is held"; unreadable → "Accept refused: needs a code fix: …"; with Plaud's
  IDs changed, refused under a consent stop ("the consent list can't be read: fix it first") and
  when consent-listed or kept-out IDs (recomputed now: the consent list in force and plaud_listing's
  kept-out and re-keyed rows) in the last good listing are missing from the held one ("Accept
  refused: fix this first (runbook)", why "Fix this first: N consent-listed or kept-out IDs
  vanished while Plaud's IDs changed (runbook)"); else marks the newly absent gone and promotes
  the held listing, deleting K_HOLD, the blocker and K_SMALL_SHRINK.
- New table `plaud_listed (listing, canonical_plaud_id, form)` in state.py's SCHEMA (a table, so
  `SCHEMA_VERSION` stays 1; `test_plaud_tables.py`'s list has it).
- **C6.07, C5.34, C5.30, C5.31, C5.32, C5.29 (the match)**: `test_plaud_hold.py` against the
  fake's account (a small shrink settling at 61 minutes and not at 30; the wait restarting; a large
  shrink and Accept marking gone; a change of form with nothing absent holding nothing, with
  kept-out, review and consent holding; a census change with one absent holding and hiding
  titles; Accept refused when a consent-listed ID vanished with a form change, and under a stop;
  an excluded ID Plaud simply stops listing never blocking Accept; 10% re-keyed: large shrink,
  matched consent-listed and kept-out IDs excluded before and after Accept; a Compare never moving
  the last good listing or a hold) and `test_plaud_compare.py` (one match, none beyond a second,
  two candidates either way, no title for a matched ID).

### Accept this listing's two doors

- **The request `accept`**: `worker._accept`, in `HANDLERS`; `requests.HANDLED` = {"keep-here",
  "accept"}. The gate refuses it before the first catch-up (not in GATE_ALLOWED: "the UI's Accept
  stays refused before the first catch-up"). It holds `plaud` as the new job `plaud-accept`
  (`runlock.JOBS`, the UI's `JOB_LABELS`) for TURN_LOCK_WAIT; busy answers "skipped: busy
  (`plaud-catch-up`)". Reply: done with the message, or failed for a refusal; `result` {gone:
  [recording IDs], why}. Web's `check_request` refuses an `accept` that names any ID or `all`.
- **`recordings plaud accept-listing [--json]`**: holds `plaud` (CLI_WAIT), reads the consent list,
  works before the catch-up (§11.6). Exit 0 accepted or nothing held, 1 refused, 75 busy, 78
  refused for an unreadable consent list. Prints the message, the why, and gone recording IDs.
- `test_plaud_accept.py` (8): gate refusal, accept and gone, refusal with its why, busy, the CLI's
  accept, refusal, 78 and 75, the code fix; the tick purge.

### The fake server (§10.6 step 1, §21's listing faults)

`plaud_server.account(form="of_") -> FakeAccount` routes `GET LIST_PATH` (`{"data": [...]}`,
newest first, 400 under page size 10). `FakeRecording` (plaud_id, name or untitled, start_at,
duration_ms, seed, other_id; `.audio` a tiny WAV for A4). Faults: `form`/`flip()`, `clamp`,
`malformed`, `twice` (a repeat across pages), `remove` (a shrink), `rekey` (new hex, audio and
times unchanged), `other_id` (an unreadable form), an empty account, and drift through A2's
`after(k, hook)`. Still importable outside tests (stdlib only, plus the client's paths).

### Docs

CLAUDE.md (the fake's `account()`, the cache's rule, and the recipe "To add a Plaud request kind"
from the council's Simplicity 1, walked for `accept`); the spec's §9.4 (`plaud-accept`), §9.5
(`plaud_listed`), §11.4 step 5 (a waiting small shrink counts again), eight Scenarios rows and a
Revisions line; the user guide's "What the sync compares, and the equations" and "When the sync is
held: Accept this listing"; eight A3 rows in the guesses file.

## Tests

- New: `test_plaud_listing.py` (15), `test_plaud_compare.py` (27), `test_plaud_hold.py` (17),
  `test_plaud_buckets_property.py` (40 seeds), `test_plaud_accept.py` (8). Changed: the HANDLED
  pins (`test_plaud_requests.py`, `test_plaud_requests_ui.py` with a new accept-check test,
  `test_plaud_views.py`), `test_plaud_tables.py`'s table list, the cache rows in `test_backup.py`
  and `test_doctor.py`.
- `make check` before the commit: pytest 2,265 passed (and again under `TZ=UTC`), Vitest 458
  passed, ruff clean, gitleaks clean. Its one warning is the existing `test_demo_guard.py` fork
  warning. Because `recordings_ui/plaud.py` changed (`check_request`, `JOB_LABELS`, the handled
  list), `make e2e` too (it builds first): 145 passed. No flaky test seen.
- Requests leftovers for A5, from the phase-end list's Task 14 row ("requests.py gains each kind's
  handler and coalescing there"): the deadline-check test, `MAX_PLAUD_ID_LEN`'s comment and
  `claim_next`'s columns by name are not done here (they go with coalescing).

## From audio-router, and from the spec alone

- **From audio-router** (`src/audio_router/archive/sources/plaud.py`, `tests/test_transport.py`;
  read only): the listing's shape (a list, or one under `data`/`data_list`/`files`/`items`/
  `list`; rows with `id`, `name`, `duration` in ms, `start_at`, `created_at`, `serial_number`),
  "stop on an empty page, never a short one", and the API refusing page sizes under 10 (the fake's
  400 is a guess at the status).
- **From the spec alone, or guessed** (eight A3 rows in the guesses file): the listing's shape
  beyond audio-router; paging taken as stable (S5's list-twice-and-union rule for a drifting
  server not built); a titleless row malformed but listed, and the 1% over all rows; the census
  forms, case ignored; S7's "hex equal" (a different hex holds the catch-up, which is S7's stop);
  "5% of those here" as 5% of the recordings in the archive from Plaud; the re-key match's fields
  (start, else creation time; `duration` in ms); `plaud-accept` as its own job, and a busy
  answer rather than a queue.

## Questions for Dan

None that change a decision or a scenario. Two builder's readings worth a glance while testing:
1. **A drip of small shrinks** (spec §11.4 step 5, now one sentence longer): recordings in a
   small shrink still waiting count again in the next listing's measure, so several small
   deletions that add up past the large shrink's limit (more than 5, or 5%) hold the sync instead
   of each settling quietly. The spec's letter measures only against the last good listing, which
   has already moved past them; this reading fails closed.
2. **Accept while a long CLI run holds the sync** answers "skipped: busy (`plaud-catch-up`)" and
   asks for another press, rather than queueing behind it (§9.4's "a request waits its turn"). A5's
   coalescing may want to queue it instead.

## What's left, and what A4 to A6 need from me, by name

- **A4 (importing, keeping out, Release, gone, review):**
  - Late keep-outs and title changes from the listing: each `Classified` row `bucket == HERE`
    carries `word` (the keep-out word its listed title starts with now) and `listed.title`, in
    memory only; `record_late_keep_out` and the marker do the rest. `record_compare` writes no
    title hash for a here row with a word, or kept out.
  - Keep-out of an ID not here is recorded by `record_compare` (`kept_out_at`, `kept_out_word`, via
    `Classified.kept_out_now`); "was kept out by title" is `excluded_by == "was-title"`.
  - Release: `plaud_listing.released_at` is honoured when `released_at >= kept_out_at` (and for a
    re-keyed row); the preview's table goes into `cache.CACHE_SCHEMA` (none is there yet), with
    `title_allowed` to refuse a consent-listed or word-matching title.
  - Gone (§12.3): built here as the small shrink's settling and Accept (`PlaudDB.mark_gone`,
    `gone` events) and the return (`PlaudDB.back_from_gone`, in `record_compare`). Left: the sweep
    skipping gone recordings (B1) and their Status/Sync rows (A6).
  - Review marks (§11.6): `classify` reads `ListingRow.needs_review` from plaud_listing as it was
    *before* the listing (`take_listing`'s `before`), so a brand-new ID that needs review must be
    marked before classification (for instance in `take_listing`, between reading `before` and
    `classify`, a `replace` of the new rows with `needs_review=True` whose creation time is before
    `rebuilt_at`, and the same written by `write_listing`: `ListingEntry` has no `needs_review`
    field yet, so add one or a `set_listing` call).
  - Import failures and parking for a Plaud ID: `import_failures` (3 → `FAILED`), `parked_at`
    (→ `PARKED`), which `classify` reads; A4/A5 write them.
- **A5 (the run, the handlers, the catch-up):**
  - The run: `listing = PlaudSource(client).list_all()`; not complete → `listing.error` (a
    `ends_the_run` exception, or None for a bad shape/cap/malformed) decides the run's phrase and
    the sign-in/rate-limit records; partial rows feed A4's privacy only.
  - `here = here_from_archive(writer.index, writer.identity.uuid, writer.state, writer.archive,
    db)` (`IndexUnavailable` is an archive-level error); `words = enforced_words(db)`
    (`KeepOutWordsError` ends the run: nothing classified).
  - `result, measured, outcome = take_listing(db, Cache(state_path), listing, here=, consent=,
    words=, patterns=, gate_open=, schedule_on=, auto_import=, sync=True|False, now=)`; `outcome`
    is `RunOutcome.listing`; `measured.run_phrase()` the run's §15.6 phrase ("held: Plaud's IDs
    changed", "held: many gone from Plaud", "waiting: Plaud lists 3 fewer"). Import only while
    `outcome != "held"` and no `K_HOLD`; Sync now during a hold answers "Held: see Status".
  - The compare request's handler: `take_listing(sync=False)` (it never moves the last good
    listing or a hold); its reply from `result.equation`. HANDLED gains "compare" with it.
  - The dry run: `compare.lines(result)`, `measured.sentence()` and `refused`, the census, and
    `consent.review_lines`.
  - The catch-up: its listing is measured against the archive (no last good listing); held → stop
    and say so; `recordings plaud accept-listing` then `--catch-up` again.
- **A6 (views, runbook):**
  - Readers: `K_COMPARE` (equation, archive, consent, titles_hidden, import_all, census, repeats,
    malformed, at), `K_LAST_GOOD_LISTING` (at, count, census, form_changed_at for "Plaud's IDs
    changed form on …; every ID still matches"), `K_HOLD` (sentence, accept, refused → SyncHold),
    `K_SMALL_SHRINK` (`len(absent)` → `hold.shrink_line(n)`), the cache through
    `Cache.read_only(state_path)` with `title_allowed` applied again (P13), open `rekeyed` events
    (SyncFlag kind "rekeyed", `detail.of`), `plaud_checks.gone_since` for Gone rows.
    `CompareRow.excluded_by` maps to KeptOutRow.by as is; Release is offered for "was-title" and
    "rekeyed"; NotHereRow.park for `state == compare.FAILED`. Import `cache`, `compare` and `hold`
    inside the view functions only (sqlite3; the Pages demo's Pyodide).
  - The runbook: the hold and its recovery (the steps the hold's sentence points to, and
    `rc exec worker recordings plaud accept-listing`), and "Fix this first"'s recovery.
- **B1:** the sweep's "mark listing changes for a check now" can compare `plaud_listing.title_sha256`
  and `duration_ms` (written only for IDs here) before and after `write_listing`.
