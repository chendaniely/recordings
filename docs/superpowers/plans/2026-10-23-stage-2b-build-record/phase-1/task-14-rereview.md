# Task 14 re-review: fix round fadbfb9..b0ac76d

162 related tests pass; ruff is clean. Probes used a synthetic scratchpad archive.

## Findings

1. **Addressed** (`requests.py:186-219`, `plaud.py:170-181`).
   - **The earlier probe now holds.** I ran 39 trials:
     - an EXCLUSIVE holder for 2 s and for 3 s, and a writer for 2 s;
     - a 0.8 s slow read, without a lock and with one freed at 1.1 s;
     - a lock freed at 0.7 s, then a 0.2 s read;
     - release times swept from 600 to 1000 ms;
     - an executor kept busy for 0.9 s and for 1.5 s.

     Every NOT_SAVED came at 0.82–0.91 s, and none left a row.
   - **One case still lands a row.** After line 218 only the INSERT and the commit run, and in WAL mode they wait for no lock. A simulated 0.3 s commit got NOT_SAVED at 1.003 s and then a queued row; at 0.15 s, no row. The docstrings say so, and the ruling accepts it.
   - **The 2a callers keep 30 s:** `create` (state.py:203), `db()` (263/269) and `backup_to` (509).
   - **The `test_ops.py:1083` spy hides nothing.** It passes `timeout` through, and `["ro","ro"]` still pins the mode.
   - **The UI lock test fails on the old code.** I ran it against an exported copy of fadbfb9: `assert 1.5477 < 1.3`, because `asyncio.run` waited for the thread that inserted once the lock was freed.
2. **Addressed** (`plaud.py:73`, test at `test_plaud_requests_ui.py:227`; fails on the old code).
3. **Addressed** (`plaud.py:112`, test at `:234`; fails on the old code).
4. **Addressed:**
   - a handler that raises (`test_plaud_requests.py:335`);
   - a read-only state.db (`test_plaud_requests_ui.py:248`);
   - unreadable rows, both bad JSON and a bad time (`test_plaud_requests.py:350`).
5. **Addressed** (`when.py:32`, `checks.py:29`, `requests.py:251,267`, test at `test_plaud_when.py:48`).
6. **Addressed**, with the accepted departure (`requests.py:153-167`, `worker.py:237,246,272`).
7. **Addressed** (`plaud.py:50,140`, test at `test_plaud_requests_ui.py:242`).
8. **Addressed** (`worker.py:7,323`).
9. **Addressed.** The body says no docs were read and that the `to_thread` check is Task 20's. Its claim that the lock test failed first is borne out by the run above.

## New problems

- **Minor: the check before the commit is untested** (`requests.py:218`). Deleting it leaves every test green. Fix: patch `requests.utc_iso`, which runs after line 204's check, to move the fake clock past the deadline. Then assert NotSaved and no row.

## Out of scope

- `MAX_PLAUD_ID_LEN = 128` (`requests.py:58`) is now the first length refused, but its name reads as the longest allowed. A comment would help.
- SQLite's busy handler overshoots `busy_timeout` by about 0.1 s: a thin margin under load.
- Still open, outside the rulings: Standards finding 4 (plan decision 20's wording) and smell 10 (positional `row[...]`).

Verdict: approve
