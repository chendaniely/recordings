# Task 12 review: Standards axis (cd683fb..f92c1c1)

Files: `plaud/gate.py` (new, 40 lines), `tests/test_plaud_gate.py` (new, 43 lines). Both match the brief.

## Documented standards
- **Commit format:** subject is `feat(plaud): 🤖 the gate: ...`, which is correct. The body says why (the gate stays closed because nothing in Phase 1 sets `catch_up_done_at`) and gives the failing line (`ModuleNotFoundError: No module named 'recordings.plaud.gate'`). `Plan:` is present, and `Co-Authored-By: Claude Sonnet 5.5` names the model that made the commit. The trailer paragraph is last with no blank line. No violation.
- **Archive, privacy and logs rules (CLAUDE.md):** the module touches no archive file, no title and no log. It reads one `plaud_state` key through `PlaudDB`. The phrases `GATE_FIRST` and `GATE_REFUSAL` are word for word as in the brief (spec §11.6 and §15.6). I could not check them against the spec text itself, only against the brief.
- **Pyodide and py312:** standard library only (`dataclasses`), and `from __future__ import annotations` is used. No problem.
- **Fixtures are synthetic:** the tests use `new_archive` and a fixed `NOW`, with no real data.
- **No upstream-issues entry** applies.

## Smells (judgement calls)
- **Possible Middle Man (Minor, suppress):** `gate_now` is a thin wrapper over `db.get(K_CATCH_UP_DONE)`. It is the single place that names the gate's open condition, and Tasks 13, 14 and 19 call it. Keep it.
- **Possible Primitive Obsession (Minor, no action):** `GATE_ALLOWED` holds request kinds as plain strings (`"compare"`, `"keep-here"`). The wider request-kind vocabulary lives in Task 14, so a shared constant or enum is Task 14's call. When that task lands, check that its kind names match these two strings exactly (`"keep-here"` versus `keep_here`).
- **Redundant state in `Gate` (Minor, no action):** `reason` is `None` exactly when `open` is true. This is documented in the field comment and pinned by the tests. A second field that always agrees with the first is mild, but the interface is the brief's, and Task 19 needs `reason`.

## Other observations
- `gate_now` fails closed on a stored JSON `null`, because `get` returns `None` for it. The implementer noted this. That is the right direction, and no test pins it (Minor, optional).
- The test `test_only_requests_that_write_no_snapshot_pass_the_closed_gate` only checks constants against literals. This is a pin test and the brief says so. It does guard against a kind being added to `GATE_ALLOWED` without a decision.
- I did not re-run the suite. The implementer reports `make check` as green.

No Critical or Important findings.

Verdict: approve
