# Task 14 review: Spec axis (Tier 1), ef08b02..fadbfb9

## Findings

**1. Important: a row can land after NOT_SAVED.** `requests.py:60, 150-176` and `plaud.py:157`. SAVE_WAIT_MS limits only the wait at `BEGIN IMMEDIATE`. `_notice`'s reads (`alive` → `state.meta`, opened with `State._connect`'s `timeout=30`) are not limited, and neither is the commit's fsync. I probed it. With another connection in `locking_mode=EXCLUSIVE` held for 3 s, the reply was NOT_SAVED at 1.00 s, and then row `exclusive-01` was `queued`, so the worker would run it. The same happened with a 0.8 s read before a lock released at 1.1 s. So the press hears "couldn't be saved" and the request runs anyway; in Phase 2 that could be a `release` or an `import`. P6 and §15.1 promise an answer, and this answer is wrong. It also contradicts the SAVE_WAIT_MS comment and the busy test's "no row lands later" (`test_plaud_requests_ui.py:153`). Fix: pass a deadline into `insert`. Do every read on one connection, with its timeout and `busy_timeout` set to the time left, and check the deadline before the INSERT, rolling back once it has passed. Task 19's poller should keep a request that timed out pending, so it reports a row that still lands. Test: an EXCLUSIVE holder released after 2 s gives NOT_SAVED, and the row count is 0 afterwards.

**2. Minor: a 128-character Plaud ID is accepted.** `plaud.py:70` accepts length ≤ 128, but §9.3 says "under 128 characters". Use `<` and test 128.

**3. Minor: keep-here keeps fields it never uses.** It stores `plaud_ids` and `all`. My crafted keep-here saved `["Coffee-chat-about-grant-timelines"]` (§9.3: "A request never carries a title"). Refuse non-empty `plaud_ids` and `all=True` for keep-here.

**4. Minor: untested paths.** No test covers a handler that raises (expected: `failed`, "error: `RuntimeError`", no message text in the log), a read-only state.db (Review Focus 5 names it; my probe got NOT_SAVED in 0.001 s), or `claim_next`'s unreadable row.

**5. Minor: docstrings miss the request step.** The `worker.py` module and `Worker` docstrings (lines 5 and 308) describe the tick without its request step.

## Scenarios
- **C5.18: proven.** `test_a_kind_with_no_handler_yet_is_refused_before_any_row` refuses at web with no row, and `test_a_kind_the_worker_cant_handle_yet_is_refused_never_run` refuses in the worker with the gate closed. Phase 1 answers with NOT_YET (decision 2).
- **C5.21: proven.** In `test_the_worker_checks_the_gate_again_before_a_handler_runs`, the stand-in import is refused with GATE_REFUSAL and its handler doesn't run; once the gate opens, it runs. The NOT_YET test also covers all 12 other kinds.

## Probes and docs
These all held: extra or title keys, a non-dict, a non-string kind or ID, nested tuples, a 10 MB ID (0.02 s), 20,000×128 Plaud IDs (0.12 s on the loop, NOT_YET), a month-13 ID, a reused `request_id` (returns that row), and a missing, read-only or `State.read_only` state.db (NOT_SAVED in ≤0.05 s).

Nothing was renamed or removed. No README, runbook or format doc covers requests. The Scenarios rows still say "planned", as every closed task's do.

## Security checklist
1. **Secrets: fine.** No token or URL appears. Replies, rows and results hold fixed phrases, IDs and class names.
2. **Permissions: not applicable.** The task creates no file or folder (it only writes rows), and web's `/state` mount is already read-write.
3. **Network exposure: fine.** Nothing new listens. After a real insert, no client, tokens or sync module was loaded; `recordings.worker` loads, but it imports none of them. Tests use only the fake ntfy. Minor: `test_plaud_imports` checks only what loads at import time. A check after a real `submit` would pin decision 31 once Phase 2 grows `worker.py`.
4. **Failing closed: fine, apart from finding 1.** A kind that is unknown or not in HANDLED is refused before any row, and refused again by the worker. A gate read that raises fails the row without running the handler. `keep_here` resolves only KEPT_OUT_LATE flags, writes no archive file and never touches `private`.
5. **Injection: fine.** All SQL values are parameters. The f-strings fill in constants only. No shell is used. Recording IDs are checked with `parse_id` at web and used only as parameters in the worker.
6. **Logging: fine.** Logs carry fixed phrases and class names. The logged `kind` is always a HANDLERS key. There is no `exc_info` and no `str(exc)`.

Verdict: changes needed (0 Critical, 1 Important)
