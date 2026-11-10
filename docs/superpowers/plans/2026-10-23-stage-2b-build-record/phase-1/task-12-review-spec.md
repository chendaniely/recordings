# Task 12 review, Spec axis + security checklist (Tier 1), base cd683fb, head f92c1c1

The diff is the brief verbatim: `plaud/gate.py` and `test_plaud_gate.py`. The brief names no Scenarios (the spec's C5.19, an interrupted catch-up, moved to Phase 2 by decision 44), so there is nothing to prove.

## Findings

1. **Important** (Tier 1, fail closed). `gate.py` `gate_now`: `if db.get(K_CATCH_UP_DONE) is not None` opens the gate on ANY stored JSON value other than null. Traced against `PlaudDB.get` (`json.loads`): `""`, `false`, `0`, `[]`, `{}`, `"junk"`, `42` and a far-future timestamp all give `Gate(open=True)`. Spec §11.6 (line ~1263): "Until `plaud_state.catch_up_done_at` is set ... Only Compare runs", and "The gate is the only thing between a fresh deploy and an import, and §20 criterion 5's tests guard it." The brief says "open exactly when set", so the code matches the brief literally. But a malformed or hand-edited row opening the gate is the failure this task exists to prevent. Fix: open only when the value is a non-empty `str` that `datetime.fromisoformat` parses, is tz-aware and is not after `now` (inject `now` as a parameter, default the clock); otherwise closed with `GATE_FIRST`. Phase 2's writer stores an ISO time, so this costs nothing.
2. **Important** `test_plaud_gate.py`: the only open case is a valid ISO string, so none of the junk values in finding 1 are tested, and the tests pass with the gate broken for them. Add parametrised tests: `""`, `False`, `0`, `"junk"`, `123`, `{}`, a future date, and JSON null all give closed.
3. **Minor** No test for an unreadable `state.db` (locked, an `sqlite3.OperationalError` other than "no such table"). By reading, it propagates (raises, never opens), which is acceptable; a short test would pin it. Corrupt JSON in the value likewise raises `JSONDecodeError`. Tasks 13 and 14 must treat any exception from `gate_now` as closed, not catch it and carry on.
4. **Minor** The report's "fail closed, fine" covers only the null case.

Missing table: `NoSyncYet`, tested. `GATE_ALLOWED == {compare, keep-here}`, `GATE_FIRST` and `GATE_REFUSAL` match §11.6 and §15.6, and the test pins them. No scope creep. Doc check: nothing outside the new files mentions the gate's names, so no doc was made untrue.

## Security checklist
1. Secrets: not applicable. No token, URL or log; the module only reads a timestamp key.
2. Permissions: not applicable. It creates no files or folders.
3. Network exposure: fine. It imports only stdlib and `recordings.plaud.checks`; nothing listens; tests use a local state.db.
4. Failing closed: a finding (1, 2). Junk, empty, non-string and future values open the gate; null, a missing table (NoSyncYet) and an unreadable db (raises) do not.
5. Injection: fine. The only SQL is `PlaudDB.get`, parameterised.
6. Logging: fine. gate.py logs nothing and returns fixed phrases.

Verdict: changes needed (0 Critical, 2 Important)
