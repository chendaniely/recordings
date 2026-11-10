# Task 9 report: the auto-private marker

Status: DONE. Commit 315c964 (one cycle, as the brief has one commit). Base recorded in task-9-base.

## Implemented
- `plaud/privacy.py` (new, verbatim from the brief): title_sha256, PatternSet, current_patterns, Verdict, judge, PrivacyInputs, why_phrase. No sqlite3 at import (tested).
- `plaud/reconcile.py`: `privacy` parameter, `_plaud_title`, `Reconciled.verdict`, docstring bullet rewritten; privacy imported inside `reconcile` (the one cycle).
- `writer.py`: `keep_out_word` on Writer/Writer.open (read directly, not via plaud_settings), `keep_out_words()`, `_record_privacy` (event and late keep-out first, markers last), `ReconcileReport.private_added`/`kept_out_word`.
- FORMAT.md and demo/archive/FORMAT.md updated (byte-identical copy).
- Tests: `test_plaud_marker.py` (new, 17 tests), two `test_reconcile.py` tests moved to markers.

## TDD evidence
- RED: `uv run pytest packages/core/tests/test_plaud_marker.py packages/core/tests/test_reconcile.py -q` -> both files "ModuleNotFoundError: No module named 'recordings.plaud.privacy'" at collection, "2 errors during collection".
- GREEN: same files plus test_writer, test_import_run, test_plaud_flags -> "207 passed in 15.30s".
- FORMAT step: test_selfdoc + test_demo_archive -> "22 passed".
- `uv run ruff format .` -> "136 files left unchanged".
- `make check` -> "1676 passed, 141 deselected, 1 warning" (the warning is a pre-existing fork() DeprecationWarning), gitleaks "no leaks found". No UI flake occurred.

## Files changed
packages/core/src/recordings/plaud/privacy.py, reconcile.py, writer.py, format/FORMAT.md, demo/archive/FORMAT.md, packages/core/tests/test_plaud_marker.py, test_reconcile.py.

## Self-review
Brief's code matched the real files and Tasks 1-8 as built (PlaudDB.markers/set_markers/add_event, record_late_keep_out, valid_word, keep_out_match signatures); no deviations. Verified `title_seen` still drives only the title role in reconcile. Only `private` is ever added; `judge` never removes it.

## Concerns
None blocking. Note: `_record_privacy` runs on the "unreadable output" return path too (markers move even though outputs are not written), exactly as the brief specifies; privacy does not depend on outputs.

## Fix round (commit 848eb74)
- RED: new tests failed first, e.g. `assert None == 'title changed'` for the A, P, A reconcile; the keep-out A, "NoArchive", A flag; judge `added_at` (TypeError); a reindex stopped by a refused state.db. The fold and Writer.open tests passed already (code was right); mutation check: removing `fold` from `_hits` fails the 3 pattern-fold tests.
- GREEN: test_plaud_marker 28 passed; `make check`: 1687 passed, vitest 378 passed, gitleaks clean.
- Important 1: Marker gains `checked_at`; `PlaudDB.markers` returns it; `Snapshot.added_at` (the Writer passes `source.added_at`); `judge(..., added_at=)` judges titles added at or after checked_at with another hash, and rewrites the marker so checked_at moves; tests with a controlled `_now` clock prove [A,P,A] ends private ("title changed"), [A, noarchive, A] is flagged, and after removal neither reconcile nor a web reindex re-adds. Spec §13.2 "Due" reworded, Revisions line added (dated 2026-10-31, the plan's date for today).
- Important 2: fullwidth, word joiner, BOM tests for patterns; fullwidth and word joiner for the keep-out word.
- Minor 3: `sqlite3.Error` caught in `reindex` (not in `reconcile_plaud`, whose brief test requires it to raise); one warning with the class, report problem "OperationalError: not reconciled", title stays due; tests for `set_markers` and `put` failing.
- Minor 4: docstrings in writer.py and privacy.py say the retry records no second note. 5: Writer.open test (configured word, invalid word, bad api_base/schedule_minutes). 6: FORMAT.md both copies, reconcile.py docs. 7: raw word passed to Writer, dead branch dropped, `_PHRASES` dict, `words_too`/`case` keywords.
- Concern: the new rule depends on `source.added_at` being the time the app added the snapshot; a merged snapshot carrying an old `added_at` (before the marker's checked_at) would not be judged by this rule.

## Fix round 2 (commit 3b67f6a)
- RED: the new tests failed first, e.g. `assert (None, None) == ('title changed', None)` (row 1 [A], row 2 merging [P, A], P = "Private: x" / "NoArchive: coffee" / fullwidth, real clock), `Marker has no attribute 'judged'`, `judge() got an unexpected keyword argument 'snapshot_shas'`.
- GREEN: test_plaud_marker 32 passed; `make check`: 1691 passed, vitest 378 passed, gitleaks clean.
- Content rule as ruled: `plaud_state` `judged:<recording_id>` = `{pid: [sha256, ...]}`, written by `set_markers` in its transaction; `Marker.judged` (replaces `checked_at`); `Snapshot.sha256`; `judge(snapshot_shas=)`; a title from an unjudged snapshot whose hash isn't the marker's is judged once and the set becomes every current snapshot's sha256; a marker with no key reads as empty; `State.forget` drops the key. `added_at` rule removed; `privacy.py` comments fixed.
- Tests (real clock): the three probes (private, flagged, fullwidth), an `added_at` far ahead (400 days) with 4 reconciles and a reindex re-adding nothing, no-key marker judges once, forget drops the key, judge-level test.
- Spec §13.2 Due reworded; the one Revisions line updated.
