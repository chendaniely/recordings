# A3 privacy fix: two fail-open paths in area A3's code (5d64755)

Read `BUILD-FAST.md` (same folder) first. An automated security review of 5d64755 flagged two
paths; the controller checked the first against the code and agrees it fails open. Fix both, test
first (each test fails before its fix, quote the failing line in the commit), one commit each.

## 1. A re-keyed, consent-listed recording can be imported after a consent stop (HIGH)

`packages/core/src/recordings/plaud/compare.py`, `rekey_matches` and `classify`.

`rekey_matches` only considers a listed ID that is `not in listing` (plaud_listing as it was
before this listing), and only absent keyed-out IDs that are `in excluded`. During a consent stop
(`consent` None or stopped) `excluded_ids` has no consent IDs, so `absent` is empty and no match
runs; `write_listing` then records the new ID in plaud_listing. Once the stop lifts, that ID is
`in listing`, so it is never a re-key candidate again: it lands in WAITING and is imported. The
same happens whenever a new ID's first sighting doesn't run the match for any reason (for example,
the old ID still listed alongside the new one that run, then gone the next).

Scenario to pin (spec §11.4 step 6, §12.1, C6.07's family): recording X is consent-listed and kept
out (its dates in the cache's keyed_out). The consent list becomes unreadable (a stop). Plaud
re-keys X to X'. A listing runs during the stop. The list reads cleanly again; the next listing
must classify X' as EXCLUDED by re-key (flagged for Dan), never WAITING, and nothing imports it.

The controller's suggested direction (choose your own if it is simpler and as safe): run the match
for every listed ID that is not here, not already excluded, and not released by Dan (a released
row must stay released, so the match must not catch it again), instead of only never-listed IDs.
That fails closed: a false match keeps a recording out and flags it for Dan, the safe direction.
Also consider keeping the last readable list's consent-listed keyed_out entries (the cache's
`kind='consent'`) in `absent`'s source during a stop. Keep §11.4 step 6's wording in the spec true,
with a Revisions line.

## 2. The title purge's stamp is taken after the purge (MEDIUM)

`packages/core/src/recordings/worker.py`: the tick purges cached titles that the consent list or a
keep-out word now catches (P1), then records `self._purged = (consent, words,
path.stat().st_mtime_ns)`, a stat taken *after* the purge. A cache write landing between the
purge and that stat is never purged until something else changes. Take the stamp before the purge,
so any write during or after it changes the mtime and triggers another purge. If readers of cached
titles (the views) can cheaply apply `title_allowed` again before showing a title, say whether
they already do; add it only if it is small and stays inside the UI rules (no Plaud client
import in `recordings_ui`).

## Report

`.superpowers/sdd/2026-10-23-stage-2b-phase-1/reports/a3-privacy-fix-report.md`. Commit trailer:
BUILD-FAST's. `make check` before each commit; `make e2e` too if `recordings_ui` changes.
