# Task 14 report: Requests from the UI

Status: DONE

## Commits
- 7e14093 feat(plaud): request rows: inserted, claimed and re-checked by the worker (requests.py, worker.py, test_plaud_requests.py, test_worker.py)
- fadbfb9 feat(ui): recordings_ui.plaud checks a plaud_request and inserts it off the event loop (plaud.py, test_plaud_requests_ui.py)

Both carry `Plan:` in their trailers. Base recorded in task-14-base.

## TDD evidence
- Cycle 1 RED: `uv run pytest packages/core/tests/test_plaud_requests.py -q` gave `ImportError: cannot import name 'requests' from 'recordings.plaud'` (collection error). `test_start_runs_in_the_spec_order` gave `AttributeError: 'Worker' object has no attribute '_requests_at_start'`.
- Cycle 1 GREEN: `test_plaud_requests.py` + `test_worker.py`: `44 passed` (10 new, 34 in test_worker; the brief's "29" predates Task 13's additions).
- Cycle 2 RED: `ModuleNotFoundError: No module named 'recordings_ui.plaud'` (collection error).
- Cycle 2 GREEN: `test_plaud_requests_ui.py` + `test_plaud_imports.py`: `10 passed`.
- `make check`: `1814 passed, 141 deselected`, ruff clean, gitleaks no leaks. No flaky reruns needed.

## Adaptations
None of substance. The brief's code matched the code as built: `gate_now`, `GATE_ALLOWED`, `GATE_REFUSAL`, `flags.keep_here`/`KEPT_HERE`/`NOTHING_TO_KEEP`, `Phase` already had `"request"`, `plaud_holder`, `alive`, `HEARTBEAT_KEY`, `parse_stamp`, `local_hhmm` all exist as the brief names them. The imports were merged by hand into worker.py's existing import lines (`from recordings.plaud import keepout, requests`; multi-line flags import); ruff check/format made no further change. The brief's code and tests were used verbatim (the commit messages likewise, with Sonnet in the Co-Authored-By).

## Files changed
- packages/core/src/recordings/plaud/requests.py (new)
- packages/core/src/recordings/worker.py (start order, `_serve_one`, `serve_request`, prune, `HANDLERS`)
- packages/core/tests/test_plaud_requests.py (new), packages/core/tests/test_worker.py (start order)
- packages/ui/src/recordings_ui/plaud.py (new), packages/ui/tests/test_plaud_requests_ui.py (new)

## Self-review
- A request from the browser is refused before any row when its kind is not in HANDLED, when unknown, when the keys differ (no title), or when the demo/state reason holds; the reason comes before NOT_YET.
- The worker re-checks: no handler gives NOT_YET, then the gate (GATE_ALLOWED), then the handler; handler exceptions are answered with the class name only.
- `submit` answers within SUBMIT_WAIT (1 s) with NOT_SAVED for sqlite3.Error, OSError and timeout; the insert's own busy_timeout is 500 ms so no row lands after the answer. Tests pin the busy-lock and hung-insert cases.
- Pyodide import guard test passes: no sqlite3, recordings.state, locks or worker at import.

## Concerns
- `_notice` in `requests.insert` reads the heartbeat meta with `state.meta` without catching `sqlite3.Error`; that error propagates and `submit` turns it into NOT_SAVED, which is the intended behavior.
- Minor: `serve_request` bumps the progress phase to "request" and back to "idle" only on a normal path; an exception inside `requests.finish` itself is left to `_step`, as the brief writes it.

## Fix round (b0ac76d)

Rulings 1-9 of the Tier 1 review done in one commit; `make check` gave 1824 passed, ruff and gitleaks clean.

- **1 (Important):** `requests.insert(..., deadline=None)` (a `time.monotonic()` reading; default `SAVE_WAIT` = 0.8 s, replacing `SAVE_WAIT_MS`). One connection (`State.db(timeout=left)`, new `timeout` argument), `busy_timeout` set from the time left, the heartbeat read inside the transaction, the deadline checked before the INSERT and before the commit, `NotSaved(TimeoutError)` on a miss (rolls back). `submit` passes `time.monotonic() + SAVE_WAIT`; its `wait_for` stays as a backstop and its docstring says so (that one row may still land; a session follows the `request_id`, Task 20).
- RED lines: `TypeError: insert() got an unexpected keyword argument 'deadline'` (two core tests); UI lock test first run `assert 64.1 < 1.3` (that run's helper had a thread bug, `check_same_thread`, fixed afterwards; I did not re-run the new UI lock test against the old code, but the reviewer's own probe showed the row landing after NOT_SAVED). 128-char ID: `'Comes with the next part of stage 2b.' != "An ID isn't valid."`. Keep it here with a title in `plaud_ids`: `assert PlaudRequest(... plaud_ids=('Coffee-chat-about-grant-timelines',) ...) == Refusal(...)`.
- **2:** `_opaque` uses `<` (127 ok, 128 refused). **3:** keep-here refuses non-empty `plaud_ids` or `all=True` with UNREADABLE, after the ID checks (so BAD_ID still wins for a bad Plaud ID). The earlier tests that sent Plaud IDs on keep-here now send recording IDs alone.
- **4:** tests added for a handler that raises (failed, "error: `RuntimeError`", no text in log), a read-only state.db (NOT_SAVED at once), `claim_next` unreadable rows (bad JSON and a bad `queued_at`). These three passed on first run: they pin behaviour that already held.
- **5:** `when.utc_iso` is the public helper (moved from `checks._utc`, which now imports it); `requests` uses it and `parse_stamp` for `queued_at`. New `test_utc_iso_...` in test_plaud_when.py.
- **6 (adaptation):** `worker.last_heartbeat(state)` is public (renamed from `_heartbeat`), with `parse_heartbeat(raw)` and `beat_alive(beat, now=)`. Ruling 1 needs the heartbeat read on the insert's own connection, so `_notice` reads the row there and uses `parse_heartbeat` and `beat_alive`, not `last_heartbeat` itself (that opens its own connection with a 30 s timeout). Task 19 can call `last_heartbeat`.
- **7:** `UNREADABLE = UNREADABLE_ROW`; `NO_CONFIG_REASON` pinned equal to `app.NO_CONFIG` by a test; `_refused` helper in `submit`.
- **8:** worker.py module and `Worker` docstrings name the request step. **9:** the commit body says the asyncio docs were not read this round and the check is Task 20's.
- Other edits: test_ops.py's `_connect` spy now accepts the new `timeout` argument (`*rest`).
- Not done: standards finding 4 (plan decision 20's wording of `requests.py`'s imports) is the plan text, left to the controller; smell 10 (positional `row[...]` in `claim_next`) was not in the rulings.
