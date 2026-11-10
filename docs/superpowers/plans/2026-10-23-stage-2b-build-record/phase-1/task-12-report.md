# Task 12 report: the gate

**Status:** DONE. Commit f92c1c1.

## Implemented
`packages/core/src/recordings/plaud/gate.py` (GATE_FIRST, GATE_REFUSAL, GATE_ALLOWED, Gate, gate_now) and
`packages/core/tests/test_plaud_gate.py`, both verbatim from the brief. No adaptation needed: `PlaudDB.get`,
`K_CATCH_UP_DONE`, `NoSyncYet` and `db.state.db()` exist as the brief assumes.

## TDD
- RED: `uv run pytest packages/core/tests/test_plaud_gate.py -q` -> `ModuleNotFoundError: No module named 'recordings.plaud.gate'`, 1 error during collection.
- GREEN: same command -> `3 passed in 4.00s`.

## make check
`uv run ruff format .` -> 144 files left unchanged. `make check`: 1747 passed, 141 deselected, 1 warning (fork DeprecationWarning, existing); gitleaks: no leaks. No test outside this task failed.

## Files
- packages/core/src/recordings/plaud/gate.py (new)
- packages/core/tests/test_plaud_gate.py (new)
Base SHA saved in task-12-base.

## Commit
commitmsg.py passed; `git log -1 --format='%(trailers)'` shows `Plan:` and the Sonnet Co-Authored-By. Working tree clean.

## Self-review
Diff matches the brief exactly. Gate reads only plaud_state (one key); closed unless the key is non-None. Note: `get` returns None for a missing key, so a stored JSON `null` would also read as closed (fail closed, fine).

## Concerns
None.

## Fix round
Rulings 1 and 2 applied. gate_now is open only when K_CATCH_UP_DONE is a str that when.parse_stamp reads (future stamps count); signature unchanged; docstrings updated.
Tests added: parametrised junk ("", False, 0, [], {}, "junk", 42, True) all closed; valid stamps (aware, naive, future) open; a damaged state.db makes gate_now raise sqlite3.DatabaseError.
RED (old `is not None` condition): `8 failed, 7 passed`. GREEN: `15 passed`. make check passed (gitleaks clean). Commit: see git log (fix(plaud): the gate opens only on a real timestamp).
