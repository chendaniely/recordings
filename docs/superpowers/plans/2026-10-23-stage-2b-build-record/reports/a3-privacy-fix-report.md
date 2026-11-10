# A3 privacy fix: report

Status: **DONE_WITH_CONCERNS**. Both fail-open paths are fixed. Each fix has a test that failed
before it. One related case is left for Dan, because closing it would widen decision 16 (question 1
below).

Branch `stage-2b`, from `425731e`. Not pushed.

## Commits

| SHA | Subject |
|---|---|
| `0dfeb5e` | fix(plaud): 🤖 the re-key match runs for every ID not here, and during a consent stop |
| `7e19224` | fix(worker): 🤖 the tick stamps the Plaud cache before it purges titles |

Both messages passed `scripts/commitmsg.py` and carry BUILD-FAST's trailer.

## 1. The re-key match (HIGH): `plaud/compare.py`

**What was wrong:** confirmed as the brief describes it. `rekey_matches` only compared IDs
`plaud_listing` had never seen, and only against absent IDs that were excluded at that moment. In
an unreadable-list stop, `consent.ids` is empty, so `absent` was empty. The re-keyed ID was then
written to `plaud_listing` unmatched (MISSING, "imports stopped: consent list"). Once the list read
again it was no longer new, so it was never a candidate and read as WAITING. The same thing
happened to a new ID first listed beside the old one.

**The fix:** both of the controller's directions, as two independent layers:
- **(a)** The match now compares every listed ID that is not here, not excluded and not released by
  Dan (`_released`), in every listing. A released row stays released and is never caught again.
- **(b)** During a consent stop (`consent` None or stopped), the cache's `keyed_out` entries of kind
  `consent` (from the last readable list) count as excluded for the match. So the stop's own listing
  already catches the new ID and flags it.
- The `rekeyed` flag's `detail.of` now also reads the cache's kind. Before this, a match made during
  a stop would have said "kept-out" for a consent-listed recording.

**Tests (scenario C5.35, new):**
- `test_plaud_hold.py::test_a_consent_listed_id_re_keyed_during_a_consent_stop_never_waits` is the
  brief's scenario, run through the fake account's `rekey` and `take_listing`. It checks the new ID
  is EXCLUDED/rekeyed in the stop's listing, with the flag `of: consent`, and still excluded once
  the list reads again: no waiting or missing rows, no title in the cache, one flag. Before the fix:
  `('missing', None, None) == ('excluded', ...)`. The brief's literal version (asserting only after
  the list reads again) failed with `('waiting', None) == ('excluded', 'rekeyed')`.
- `test_plaud_compare.py::test_a_new_id_listed_beside_the_old_is_matched_once_the_old_vanishes`.
  Before the fix: `('waiting', None, None) == ('excluded', ...)`.
- I turned each layer off in turn: (b) alone fails only the stop test, and (a) alone fails only the
  beside test, so each layer is pinned by its own test.
- The old test `…_already_in_plaud_listing_or_the_archive_is_never_a_rekey_candidate` pinned the
  fail-open rule. It is now `test_an_id_here_or_released_by_dan_is_never_a_rekey_candidate`, which
  covers a here ID, a released re-key match and a released kept-out ID.
- The property test passes `keyed_out={}`, so it is unaffected.

**Docs:**
- In the spec: §11.4 step 6's wording, §20's re-key bullet (one sentence), a new row C5.35 and a
  Revisions line.
- `compare.py`'s module notes and the `rekey_matches` docstring.
- The user guide's re-key line ("even if it first appeared while the consent list couldn't be
  read").
- No decision or scenario outcome changed.

## 2. The purge stamp (MEDIUM): `worker.py` `_purge_cache`

**The fix:**
- The stamp is now `(st_ino, st_size, st_mtime_ns)`, taken **before** the purge. A write during or
  after the purge reads as a change at the next tick.
- I also added a settling window, `CACHE_SETTLED_NS` = 3 s. A stamp whose mtime is younger than 3 s
  at the stat is not kept, so the next tick purges again. On a coarse file clock (ext4 stamps with a
  coarse clock, a few ms; some filesystems stamp to the second), a write landing right after the
  stamp can get the stamp's own mtime. `record_compare` writes the cache twice, ms apart, so the
  case is real. This is git's "racily clean" rule. The cost is a purge or two after each cache
  write.

**Tests (`test_plaud_accept.py`):**
- `test_a_compare_landing_just_after_the_tick_purge_is_purged_at_the_next_tick`: a compare that read
  the list before it named an ID lands right after the purge (Cache.purge_titles is monkeypatched).
  Before the fix: `{CONSENTED: 'Synthetic meeting 99'} != {CONSENTED: None}` (line 260).
- `test_a_write_the_file_clock_cant_tell_from_the_stamp_is_purged_too`: the same, with the file's
  mtime set back to the stamp's (checked: inode, size and mtime all equal). With the stamp taken
  first but no window, it failed with `'Synthetic meeting 99' is None` (line 286). The test widens
  the window to an hour, so a loaded machine can't outlast it.

**Views:** no view reads the cache yet (the Sync screen is A6). CLAUDE.md already requires a reader
to apply `cache.title_allowed` again before a title leaves the cache (P13), and A3's handoff to A6
says the same. Nothing was added to `recordings_ui`, so `make e2e` wasn't needed.

## Tests

`make check` before each commit:
- before `0dfeb5e`: pytest 2,267 passed, and again under `TZ=UTC`;
- before `7e19224`: pytest 2,269 passed, and again under `TZ=UTC`;
- both times: Vitest 458 passed, ruff clean, gitleaks clean.

The one warning is the existing `test_demo_guard.py` fork warning. No flaky test was seen.

## From audio-router, and from the spec alone

Nothing from audio-router. Both fixes rest on the spec (§11.4 step 6, §12.1, §9.5's P1), the
brief, and the stdlib. For the stdlib I checked `help()` for `os.stat_result`, `time.time_ns` and
`os.utime`.

## Questions for Dan

1. **A new ID listed beside a still-listed consent-listed (or kept-out) one, within 1 second on
   start time and duration:**
   - Decision 16 and §11.4 step 6 say the match applies to a *vanished* one. So in the listing where
     both IDs show, the new ID reads as waiting.
   - Once A4 imports waiting rows, it would be imported in that same run, before the old ID vanishes
     and the match (which now runs in every listing) could catch it.
   - This happens if Plaud lists both IDs mid-re-key, or with a second device recording the same
     meeting.
   - Should the match also compare against excluded IDs that are still listed? That fails closed: a
     false match is a flag and a Release.
   - Not built, since it widens decision 16.
2. **During a consent stop, the fallback uses the cache's last-known consent-listed IDs.** That can
   include an ID Dan removed from the list in the last 30 days. If its re-keyed twin appeared during
   the stop, it would be flagged and kept out until Dan releases it. That errs safe; I'm flagging it
   only so the behaviour isn't a surprise.

## What's left

- Question 1, if Dan wants it.
- For A6: read titles only through `Cache.read_only` with `title_allowed` applied again (P13), as
  CLAUDE.md says.
- Unchanged: `hold.measure`'s excluded set during a stop does not include the cache's
  consent-listed IDs. It only affects the hold's "excluded absent" count, and Accept is already
  refused under a stop when Plaud's IDs changed (CONSENT_FIRST).
