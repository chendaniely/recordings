# Task 9 re-review, 315c964..848eb74

`test_plaud_marker` + `test_reconcile`: 78 passed. Ruff is clean, and the FORMAT.md copies are identical.

## Findings
1. **Partly addressed.** `privacy.py:159-172` judges a title whose snapshot's `added_at` is at or after `checked_at` (`reconcile.py:388-403`, `writer.py:858`, `checks.py:130,158`). Spec §13.2 is reworded (line 1626), with a Revisions line (3118). The tests pass only because the `clock` fixture puts reconciles before the snapshots' stamps. The rule fails open on Phase 1's import path (below).
2. **Addressed.** `test_plaud_marker.py:376,383`. Probe: with `privacy.fold` replaced by identity, all three titles stop matching.
3. **Addressed.** `writer.py:1006-1011` logs a fixed phrase and the class. The test at `:389` covers `set_markers` and `put`.
4. **Addressed.** `writer.py:929-933`, `privacy.py:20-24`.
5. **Addressed.** `test_plaud_marker.py:414`.
6. **Addressed.** FORMAT.md:66 (both copies), `reconcile.py:32-37,141`.
7. **Addressed.** The raw word is passed at `writer.py:397,406` and checked once (`:305`). The dead branch is gone, `_PHRASES` is at `privacy.py:197`, and `check` takes keywords.

## Design question

**Probe** (Writer level, synthetic archive, real `_now()`). The audio-router run stamps every `RawSource` with one `now`, taken at its start (`cli.py:387`, `audio_router.py:1128-1160`), and it reconciles after each row. Row 1 [A] is reconciled with a `checked_at` later than `now`. Row 2 (same audio) then merges [P, A] with `added_at = now`. Results:
- **P = "Private: x":** not private after `reconcile_plaud`, nor after a web `reindex`. The import's unfolded group check would tag this one at creation.
- **P = "NoArchive: coffee":** no flag and no tag. The import runs no keep-out word.
- **P = backdated "Ｐｒｉｖａｔｅ: x":** not private.
- **An `added_at` ahead of the clock:** a removed `private` comes back on every reconcile (4 of 4), which breaks decision 3.

So the rule fails open on the path the previous review named. **Important.**

**Proposed rule:** the marker remembers which snapshots it judged, by content, not by time.
- **Where:** the `plaud_state` key `judged:<recording_id>`, holding `{"<canonical pid>": ["<sources[].sha256>", …]}`. Those hashes are already in `recording.json`. `set_markers` writes the key in its own transaction (`PlaudDB._put(db, …)`), so the key and the markers move together, once the result is written.
- **Due:** case 2 as now, plus every title from a snapshot whose `sha256` isn't in its ID's set and whose hash isn't the marker's. This replaces the `checked_at` rule. Each such title is judged once, with every pattern and word. When a marker moves, its set becomes every current snapshot's `sha256`.
- **Not a count of `sources`:** an outside edit that `reindex` accepts can drop or reorder entries.
- **Lost `state.db`:** markers and key go together, and case 4 re-judges everything and rewrites both. A marker without a key (848eb74's rows, a partial restore) reads as an empty set: the other titles are judged once, erring private. No real `state.db` has markers yet.
- **Decision 3:** once P is judged, its `sha256` is in the set, so nothing re-adds the tag until a title changes.
- **Cleanup:** drop `Snapshot.added_at` and `judge(added_at=)`, reword §13.2's Due, and make the four probes tests without `clock`.

## Other new problems
- **Minor, `privacy.py:16`:** it cites "§13.2's older" Due wording, which this commit removed from the spec. Fix: drop the parenthetical.
- **Minor, `privacy.py:124-125`:** it says `added_at` is "when the app added each title's snapshot". For an import row, it is the run's start. The proposal removes the parameter.

Verdict: changes needed (0 Critical, 1 Important)
