# Task 20 report: the request input and its replies

Commit: ee6cf9e (base 7984119).

## Built
- `runtime.py`: `configure(plaud_state=, plaud_reason=)`, `plaud_state()`, `plaud_reason()`.
- `app.py`: `create_app` builds `State(cfg.state_path)` and passes `cannot_request_reason(settings, state)`.
- `plaud.py`: `POLL_SECONDS = 2.0`, `FINAL_STATES`, `UNSAVED_WINDOW = 60.0`, `clock()`; `UTC` import.
- `shiny_app.py`: `plaud_request` event effect -> asyncio task (`plaud_job`, `follow`, `follow_unsaved`), `plaud_result` message, `plaud_sync` and `plaud_arrivals` outputs, `refreshed_at`, `plaud_ended`, `session.on_ended` cancels tasks; `recording` and `status` outputs now pass `plaud=` (and depend on `plaud_ended`).
- `usePlaud.tsx` (+ test), `App.tsx` (two `useShinyOutputStatus`, `usePlaud` mounted once, `PlaudContext.Provider`), `CLAUDE.md` UI rules.

## Steps
- Step 1: docs read (below). Step 2: tests written verbatim. Step 3: failed as predicted. Steps 4-7: implemented. Step 8: test-js 391 passed; the 4 pytest files 36 passed. Step 9: `ruff format` unchanged, `make check` 1935 passed + gitleaks clean, `make build`, `make e2e` 132 passed. Step 10: committed; commitmsg.py passed.

## Step 3 failing lines
- `src/plaud/usePlaud.test.ts(32,60): error TS2307: Cannot find module './usePlaud' or its corresponding type declarations.`
- `TypeError: configure() got an unexpected keyword argument 'plaud_state'` (5 tests)
- `AttributeError: module 'recordings_ui.plaud' has no attribute 'POLL_SECONDS'` (1 test)

## Built differently
- `runtime.py`, `app.py`, `plaud.py`, `shiny_app.py` were merged, not replaced: runtime keeps Task 19's `TYPE_CHECKING` block (State import added), `plaud_settings()` etc.; shiny_app keeps Tasks 1-19's code (the listing's `recording`/`status` already matched Task 19 names `plaud=`, `now=`; `views.status_view` takes `plaud=` as built). `plaud.py` kept `sync_view(…, can_request=)` and `arrivals` as built (they matched the listing's calls). `follow_unsaved`/`UNSAVED_WINDOW` agree with `requests.py`/`plaud.NOT_SAVED` as built, no change needed.
- Test fixture: the brief's `ThreadState.db(self)` takes no arguments, but Task 14's `requests.insert` calls `state.db(timeout=…)`, so `submit` failed with `TypeError` ("a Plaud request failed (TypeError)", 3 tests). Changed to `db(self, *args, **kwargs)` forwarding to `super().db`. Test-only; no spec change.
- Commit trailer is Sonnet 5.5 (brief said Opus); commit body also names `_send_message.py` and quotes the three failing lines.

## Docs and sources read
- `.claude/skills/shinyreact-build-app/SKILL.md`: `send_message(session, id, data)` pushes to `useShinyMessageHandler` (line ~221); `useSetShinyInput` write-only, `priority: "event"`, `debounceMs: 0` (lines ~250-291); inline handlers safe (line ~369). Matches the brief.
- shiny 1.8.0: `reactive/_core.py` has `lock()` (line 302), `flush()` (261), `invalidate_later` (315); `session/_session.py` has `on_ended`, `send_custom_message`, and parses messages with `object_hook=_utils.lists_to_tuples` (line 1065). shinyreact `_send_message.py`: `resolve_id(id)` then `session.send_custom_message("shinyReactMessage", …)`. All as the brief says. `asyncio.to_thread` is Python 3.9+. No gaps; nothing added to `docs/upstream-issues.md`.
- Not read: the shinyreact online docs page (relied on the pinned skill and sources).

## Self-review
- UI rules: React stays external (no new imports of react bundles; build test passes); both new outputs subscribed in `App`; `plaud_request` input and `plaud_result` handler live in `usePlaud`, mounted once in `App`; nothing is sent on mount (a test pins it, the input is mounted with `null` and `@reactive.event` skips it); no `stopPropagation`; `www/ui.js` only rebuilt by `make build`.
- Privacy: requests carry no title; refusals write nothing (tests: not-yet kind and no-state both leave 0 rows); logs carry only fixed phrases and `type(exc).__name__`; `recordings_ui` imports no Plaud client/token store/sync (`test_plaud_imports` passes; `requests` is imported inside `follow*`, same module the existing `plaud.py` already imports).

## Concerns
- `plaud.py` imports `recordings.plaud.requests` at top (Task 14); shiny_app now imports `plaud`, so the Pages build relies on that module being sqlite-free at import. `make check` (incl. Pages tests) passed, so it holds.
- `follow` stops silently if the row is pruned or `plaud_state` raises (logged by class only).

## Fix round 1

Commit 19006c4 on ee6cf9e. make check 1942 passed (+ gitleaks), make test-js 394 passed, make build, make e2e 132 passed.

- I1: `requests.RUNNING` ("Running") written by `claim_next`; `requests.EXPIRED` written by `expire`. Core test, session test, Vitest test (expired toast text). The old core assertion that an expired row kept worker_down's text was updated.
- I2: `requests.worker_notice` (read-only, shares `_down` with `_notice`) and `is_worker_down`. `follow` sends the worker-down sentence for a queued row once per change, and "Queued" when the worker returns. Session test with a moving clock and heartbeats.
- I3: `poll` catches `sqlite3.Error`/`OSError`; `follow` logs the class once on failure and once on recovery. Session test (two failed reads, then the final reply; log lines pinned).
- I4: upstream-issues.md wording.
- 5: task leaves `following` before `reactive.lock()`, skips when `closed`; get/set under `reactive.isolate()`. Checked in shiny 1.8.0 `_core.py`: `isolate()` makes a fresh Context, `lock()` is the process lock.
- 6: the untrue comments are gone with the rewrite (now `plaud.requests`; `sqlite3` imported inside `poll`, since Pages has none).
- 7: `follow_unsaved` uses `poll` and is quiet on failed reads (with `plaud_state` None it was already skipped; the case was a state folder with no usable state.db).
- 8: FINAL comment corrected; a non-final reply after a toasted final one dismisses its toast (Vitest).
- 9: `usePlaud` listens for `shiny:disconnected` as `useTagging` does: `available` false, `reason`/`why` = `LOST` (exported from library.ts), nothing sent (`send` returns an id but sends no request). Vitest test.
- 10: session tests for decision 22's `since` and the `plaud_sync` redraw (both passed on the old code); Vitest for failed, expired and "Queued after…".

Note: the test server's `get_output` value stays stale after a message-less flush from another task, so the redraw test counts `sync_view` calls instead of reading the value.
