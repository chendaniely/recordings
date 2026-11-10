# Task 19 report: the view model

Commit: fdcd848 (branch stage-2b). Status: DONE_WITH_CONCERNS (one departure for the controller to rule on, below).

## What I built
- `recordings_ui/plaud.py`: `PlaudViewSettings`, `plaud_view_settings`, `demo_view_settings`, `sync_view`, `sync_panel`, `arrivals`, `recording_plaud`, `demo_view`, and the constants the brief names. Every reader of state.db is imported inside the function that uses it.
- `status.py` (`status_view` gains `plaud=` and "sync"; backups logic moved to `_backups_view`), `views.py` (`recording_view(..., plaud=, now=)` gains "plaud"; `status_view(..., plaud=)`), `runtime.py` (`configure(plaud=)`, `plaud_settings()`), `app.py` (`plaud_views`, passes `plaud=`), `settings.py` (demo mode copies the canned view beside the copy).
- `demo/plaud-sync.json` (hand-written, synthetic IDs `of_` + small counter; the one real title is the demo archive's own JFK recording, checked present in `demo/archive`), `docker/Dockerfile` COPY line, `test_demo_guard.py` allowed path, `types.ts` (all the Plaud types, `RecordingView.plaud`, `StatusView.sync?`).
- Tests: `PlaudScene` fixture in conftest; `test_plaud_views.py` (23 tests); two new tests in `test_status.py`.

## Steps
- Step 1 conftest: done. Step 2 tests: written verbatim from the brief, plus one extra (see below).
- Step 3 failing lines (exact):
  - `E   ImportError: cannot import name 'DEMO_NOTE' from 'recordings_ui.plaud' (...packages/ui/src/recordings_ui/plaud.py)` (test_plaud_views.py:34)
  - `E   ImportError: cannot import name 'arrivals' from 'recordings_ui.plaud' (...)` (test_status.py:11)
- Steps 4-7: done. Step 8: `pytest` on the four files: 71 passed; `make test-js`: 34 files, 380 tests passed (TypeScript check included). Step 9: `ruff format .` unchanged, `make check` passed in full (1918 passed in the default and UTC runs, gitleaks: no leaks). Step 10: message checked with `commitmsg.py` (clean), committed as fdcd848.
- After `make check` I added one test (below) and re-ran ruff and test_plaud_views.py (23 passed); I did not rerun the full `make check`.

## Built differently
1. **Updated on Plaud hides more titles than the brief's listing (P13).** The brief hid a title only for a recording with an *open* flag. Pressing Keep it here resolves the flag, so the title of a recording kept out by title would then appear in Updated on Plaud, against P13 ("for one kept out by title ... only inside Release's own question"). I added `_untitled` / `_title_for` in `plaud.py`: a title is also hidden for a recording that has any `kept-out-late`, `consent-late`, `private-unwritten` or `kept-here` event (open or resolved), a `retagged-private` event whose `why` is "kept out late" / "kept out by title", or a Plaud ID whose listing row has `kept_out_at` set. Extra test `test_a_title_stays_hidden_after_keep_it_here_resolves_the_flag`. This tightens privacy only; no spec, decision or scenario outcome changes. Test expectations (C6.06, C6.14 included) are unchanged.
2. **Imports in `plaud.py`:** the brief's replacement block omitted `time`, `SAVE_WAIT` and `UNREADABLE_ROW`, which Task 14's `submit` and `UNREADABLE` use; I kept them (merged, as the brief itself says to keep Task 14's names). Also added `Path` (runtime use in the dataclass) and `Config`/`Event` under TYPE_CHECKING.
3. **`_plaud_refs(body)`** helper factored out of `recording_plaud` so `_title_for` shares it. No behaviour change.
4. Everything else matched the code as it stands (checks.K_*, BLOCKER_PREFIX, `flag_sentence`, `Progress`/heartbeat shape, `State.read_only`, `plaud_holder`, `worker.alive`, `requests.HANDLED/NOT_YET`, `when.*`). `flag_sentence` also words a `FileNotFoundError` cause (`PRIVATE_UNWRITTEN_MISSING`); the views pass the cause through, so it shows as a flag sentence and in Needs attention unchanged.

## Docs checked
None needed: no shinyreact, Shiny or pydantic API touched; `types.ts` additions are plain interfaces with no component reading them. Said so in the commit body. Nothing added to `docs/upstream-issues.md`.

## Self-review against the privacy rules
- Titles reach a view only in `sync_view["updated"]` (the recording's own title, from the last committed `recording.json`), and never for a flagged, kept-out, consent-listed or Keep-it-here recording. Tests: `test_flag_and_updated_rows_never_name_a_flagged_recording`, the new Keep-it-here test, `test_status_never_carries_a_title`; the panel and Status carry IDs only.
- The consent list's IDs are never read; only the worker's `count`, `read_at`, `file_changed`, `stop`. The canned `not_here` rows carry invented titles ("Synthetic 042"); live Phase 1 views never fill `not_here`.
- No logging added. Reply/failure text: only fixed phrases, IDs, counts, class names. Exception classes pass through `_class_name` (identifier-only, else "Exception"); `flag_sentence` shows "unknown" for non-identifiers. `str(exc)` is never used.
- `_last_result` shows the worker's stored `message` (a fixed reply phrase by contract); not a title in Phase 1's only handler (Keep it here).
- No transcript or notes read. `recordings_ui` imports no client/tokens/sync; settings via `plaud_settings` (network-free `endpoint`); tests assert none loads on read or call.
- Pyodide: nothing at module top loads sqlite3/fcntl/threads; pinned by `test_the_plaud_views_import_on_pyodide_without_sqlite`.

## Concerns
- Departure 1 above, for the controller's review (it is stricter than the brief; the Phase 2 Kept-out/Release listing will want its own title rule).
- Full `make check` was run before the one added test, not after (ruff and that file were re-run).
- The `_untitled` query runs one extra read of `plaud_events` per Sync-screen render; trivial at this scale.

## Fix round 1

Commit 2400765 (on top of fdcd848). make check run after the last change: 1929 passed, gitleaks clean. types.ts unchanged, so make test-js was not rerun.

- Important 1: _guarded, arrivals and recording_plaud treat ValueError and TypeError like DatabaseError. Sync screen and panel summary say UNREADABLE_SYNC with see_status true; the panel adds a Needs attention line UNREADABLE_LINE with the class name only. _Read now reads and sorts the recent events once, so a bad event time stops the panel as well as the Sync screen (otherwise only the Sync screen would break, with no Needs attention line). Tests for bad plaud_state JSON, bad event detail, bad event time: status_view keeps its backups, the sync part is unreadable, and Needs attention names the class.
- Important 2: _Read.health_cause (cause() builds on it) gives the private-unwritten summary the first open flag sentence whose cause needs the disk or missing-file remedy (flags.flag_sentence), minus its full stop. Tests: OSError and FileNotFoundError.
- Minor 3: summary kind is "problem" only for a sync_health cause; the gate reason and "No good listing yet" are "no-sync". Test 1 adjusted; demo json left alone (its summaries are demo kind).
- Minor 4: one test per title-filter path. Minor 5: test pins KEPT_HERE_EVENT and CONSENT_UNREADABLE. Minor 6: comments corrected (settings.py, views.py). Minor 7: _worker reads the heartbeat once (parse_heartbeat, beat_alive).
- Failing lines first: see the commit body. The title-filter and constant-pin tests passed at once (they pin existing behaviour).
- Concern: the unreadable summary does not point to a specific cause beyond the class name; sqlite DatabaseErrors such as a locked database also read as unreadable, as before.
