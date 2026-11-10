# Task 9 review: Spec axis, Tier 1 (fc0a45b..315c964)

The code is the brief's, verbatim (`privacy.py` diffed against it), so there is no scope creep. 67 tests pass and ruff is clean. The two FORMAT.md copies are identical. No doc became untrue: I checked `title_seen`, "stays removed", main spec §7.4 and CLAUDE.md.

**C5.16** is proven by `test_a_removed_private_stays_removed_through_a_reindex_in_a_web_writer`. It uses a second Writer on the same state.db, the way `web` opens one, and checks the "title changed" note.

## Findings

1. **Important: `privacy.py:142` (the coordinator's A→P→A).** Case 2 runs only when the newest hash differs. So titles `[A, "Private: x", A]` with the marker at A judge nothing.
   - I probed it at Writer level: P merged, no reconcile ran, then A merged. Neither `reconcile_plaud` nor `reindex` makes it private, and `[A, "noarchive x", A]` gets no flag.
   - The spec contradicts itself here. §13.2 ("Due when the newest title's hash differs") allows this. §13.1 case 2 ("on new snapshots' titles, so a title first seen in a fetch is checked too") and §13.3/CLAUDE.md ("judged on every title the app has seen") don't. The controller needs to rule.
   - Phase 1 reaches this only through a merge of several snapshots at once (an audio-router group, whose own check doesn't fold and doesn't run the keep-out words). Phase 2/3 merges widen it.
   - The fix the coordinator suggested doesn't work. The last position naming A's hash is the newest title, so nothing is after it. Anchoring at the first A instead re-judges P on every reconcile once A→P→A was fully judged, so a `private` Dan removed comes back every time (probed: True). That breaks decision 3. The hash alone can't say which A the marker names.
   - Smallest fix:
     - `PlaudDB.markers` also returns `checked_at`, which is already a column.
     - `reconcile_plaud` passes each snapshot's `added_at`.
     - Case 2 also judges every title whose hash differs from the marker's and whose snapshot was added after `checked_at`.
     - `_record_privacy` rewrites the markers whenever a case-2 title was checked, so `checked_at` moves forward.
     - Add an `[A, P, A]` test, and reword §13.2's "Due".

2. **Important: folding is untested.** §13.1 says each title is "tried as given and folded". The probes pass: fullwidth, zero-width, word-joiner and BOM titles all match. But if `fold` is removed from `_hits` (`privacy.py:102`), all 1290 core tests still pass. Fix: add a `judge` test with `"Ｐｒｉｖａｔｅ: x"` and `"Inter⁠view: x"`.

3. **Minor: `writer.py:927` and `privacy.py:17` claim more than the code does.** The docstrings say a note state.db refuses leaves the title due "so the next reconcile records it". I probed it with `add_event` raising once. The marker does stay where it was, but the retry finds the recording already private and records no note, so Details loses its reason (decision 15). Fix: `judge` returns the matched reason even when the recording is already private, and the Writer records the note when there is no `retagged-private` event since the marker's `checked_at`. Or correct both docstrings.

4. **Minor: `writer.py:405` has no test.** The probe passes: `Writer.open` opens with `api_base = "ftp://nope"` and `schedule_minutes = "x"`, and uses `keep_out_word = "skipme"`; an invalid word leaves only the enforced words. But a `Writer.open` that ignores the configured word still passes all 1676 tests. Fix: add a `test_writer` test that opens with those keys and checks `keep_out_words()`.

## Security checklist

1. **Secrets:** fine. No token, signed URL or secret value is in the diff, and every fixture is synthetic.
2. **Permissions:** not applicable. Nothing creates a file or folder; the change writes only state.db rows.
3. **Network:** fine. Nothing new listens. After `Writer.open`, no client, token, sync or worker module is loaded (probed). The tests use no network.
4. **Failing closed:** finding 1 (Important). Otherwise fine:
   - a failed `mutate`, or a refused keep-out, leaves the title due (tested);
   - an unknown pattern version runs every pattern;
   - `SetPlaudFields` and `judge` only ever add `private`;
   - an invalid configured word still leaves the enforced words.
5. **Injection:** fine. The change adds no SQL (Task 6's `markers` and `set_markers` are parameterised) and builds no shell command; IDs are canonicalised.
6. **Logging:** fine. There is no new log line. Events carry fixed `why` phrases. The `plaud_*` rows hold only SHA-256 hashes (probed: no synthetic title in any of them). Reports carry fixed phrases and the configured word.

Verdict: changes needed (0 Critical, 2 Important)
