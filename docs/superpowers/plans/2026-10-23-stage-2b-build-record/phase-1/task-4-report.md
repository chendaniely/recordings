# Task 4 report: the Plaud client, copied

Status: DONE_WITH_CONCERNS (one flaky Vitest, unrelated).
Commit: 79f79d2 feat(plaud): copy audio-router's Plaud client, relicensed MIT ... (trailers: Plan:, Co-Authored-By Sonnet 5.5).
Base: `task-4-base` holds the prior HEAD (5e51267).

## Implemented
Everything in the brief, verbatim: `plaud/client.py`, `plaud/endpoint.py`, `plaud/__init__.py` docstring,
`tests/fake_plaud.py`, `plaud_server` fixture + `_helper` loader in `packages/core/tests/conftest.py`,
root `conftest.py` (`no_plaud_network`, `*_proxy` drop at import), `test_plaud_client.py`,
`packages/ui/tests/test_plaud_imports.py`, new CLAUDE.md bullet.

## TDD evidence
- RED 1: `uv run pytest packages/core/tests/test_plaud_client.py packages/ui/tests/test_plaud_imports.py -q`
  -> `ImportError: cannot import name 'client' from 'recordings.plaud'` (collection); the ui test alone
  failed with `ModuleNotFoundError: No module named 'recordings.plaud.client'` in the probe stderr.
- Copy: audio-router's `plaud.py` (282 lines) copied to `client.py` and to the git-ignored
  `task-4-client-as-copied.py`, then `client.py` replaced by the brief's edited text.
- RED 2: `https_proxy=http://127.0.0.1:9 uv run pytest ... -k proxy` -> `AssertionError: assert not ['https_proxy']`.
- GREEN: same two files -> `53 passed`. With `https_proxy`/`HTTP_PROXY` set, plus `test_alerts.py` -> `79 passed`.

## make check
`ruff format .` clean ("122 files left unchanged" after format); lint, pytest (1509 passed, 141 deselected),
pytest under TZ=UTC and gitleaks (no leaks) all pass on the final run. `make test-js` passed 378/378.
The first `make check` run had one Vitest failure, `TagPicker.test.ts > pointer movement over the list is a choice too`
(expected 'notes/lecture', got 'talks'), while the machine was busy; it passed on rerun. It is a frontend
test untouched by this task, likely timing-sensitive under load.

## Files changed
CLAUDE.md; conftest.py; packages/core/src/recordings/plaud/{__init__,client,endpoint}.py;
packages/core/tests/{conftest,fake_plaud,test_plaud_client}.py; packages/ui/tests/test_plaud_imports.py.

## Edits to the verbatim copy
Per the brief: the whole of the copied file is replaced by the brief's text (header, `of_` fix with no ID
pattern, GET-only `_get`, `TokenSource`, fixed `PlaudError` classes raised from None, redirect handlers,
https-only link check, stall rule, `stamp_meta` gains `"tool"`), with `API_BASE`, `check_api_base`, `is_loopback`,
`plain_url`, `split_url` moved to `endpoint.py`. Review with
`git diff --no-index .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-4-client-as-copied.py packages/core/src/recordings/plaud/client.py`
(435 insertions, 222 deletions).

## Self-review
- No personal path or audio-router path appears in any committed file; `test_the_client_holds_no_real_looking_id` passes.
- No Plaud host was contacted; all traffic is to 127.0.0.1 fakes; `.env`/secrets not read.
- I did not re-verify the audio-router checkout's commit hash (reading its `.git` is outside `src/` and `tests/`); the controller stated 64612df.
- Used the exact `\u`-free text; non-ASCII test strings (`stóráge`, `apí`) were written as literal characters per the brief.

## Concerns
- The flaky TagPicker Vitest above (not caused by this change).
- The Bash sandbox refuses any command containing the substring `git` in a path such as `<home>/git/hub/...` unless split; no impact on results.

## Fix round (commit b365ddc)
All 14 rulings applied, test-first (RED lines are in the commit body). Summary:
- 1: download refuses a missing/non-ASCII/zero Content-Length before writing; stops when a chunk would pass it (writes never exceed it). Tests: chunked with Content-Length 10, empty, "0", "²", with a spy on `.part` writes.
- 2: `_ascii_number` for Retry-After and Content-Length; tests with ², ³, ¹ through list_page, fetch_link, download, `_no_url_in`.
- 3: new `_guarded` helper wraps only the opener and body reads; file OSErrors go up unchanged (read-only folder test).
- 4: a read-loop timeout is IncompleteDownload (fake gained `Response.hold`/`link(hold=)` to go silent after the body).
- 5: `_MAX_BODY` = 64 MiB. 6: get_json raises outside its handler. 7: one helper `_guarded`. 8/9: docstrings. 10: `on_short_body`. 11: CLAUDE.md. 12: timeout test via a spy on `_HTTPConnection.connect` ((10.0, 60.0); passed at once, the values were right but unpinned). 13: redirect test carries an unredirected Authorization header. 14: guard installed at root conftest import (the autouse fixture `no_plaud_network` is gone; its test stays, plus a module-scoped fixture test).
- `make check`: 1519 pytest passed, Vitest, utc, gitleaks clean.
Notes: the RED run of the module-fixture test did one real DNS lookup of the Plaud host (the guard did not yet cover it); no connection was made. The https connection class's read timeout is not exercised locally (only the http one). The `Content-Length`-over-chunked case relies on the fake sending both headers.

## Fix round 2 (commit f8db422)
A: _ascii_number returns None past 15 digits (400 and 5000 digits tested via list_page, fetch_link, download). B: _get raises on_short_body when response.length is still above 0 (or past the cap); comment fixed. C: module-fixture test reads the guard qualname, no lookup. 12: https read timeout and download connect timeout pinned (passed at once). 5: RecursionError in get_json is PlaudBadResponse; the committed test makes json.loads raise it, because real nesting did not overflow in the full run (recursion limit raised by another test). make check: 1524 passed, gitleaks clean.
