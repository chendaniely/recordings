# Task 6 review: Spec axis (2f3f35c..7197dea, Tier 1)

The code, `checks.py`, `when.py` and both new test files match the brief exactly (checked with diff). 151 tests pass and ruff is clean. Probes on scratch files under `<scratch>/t6`:
- 2a's real `State` (from 2f3f35c) opens, writes and reads a 2b `state.db`, and 2b opens a 2a one. A read-only 2b reader on a 2a file gets `NoSyncYet`. `NoSyncYet` isn't a `DatabaseError`, so it can't trigger the "restore" text (§9.5).
- `wal()`: a busy file, a read-only folder and a read-only file each return False with the fixed log line. A Writer opens in 5.3 s while a reader holds the index, and the next one switches it. An `immutable=1` probe and 2a's `Index` both read a WAL file. The immutable read sees the last checkpoint.
- `parse_stamp`: a naive stamp is UTC. Junk returns None and never raises: bytes, a `date`, NaN, `+25:00`, a lone surrogate, a leading space.

**Scenarios:** C8.01 is proved by `test_schema_version_stays_1_and_migrations_stay_empty` and both open tests. C5.33 is proved by `test_listing_rows_follow_the_canonical_id_across_a_form_flip`, which covers the kept-out state, the review mark and the flag.

**Docs:** the change renames nothing. Main spec §6.8 already says WAL (Task 2), and that is now true. C8.01 and C5.33 stay "planned" in the table, as C7.02 did after Task 4 (the phase-end convention). No doc became untrue.

## Findings
1. **Minor:** `plaud/checks.py:172` `_utc` reads a naive datetime as local time. Decision 47's rule is UTC. Under `TZ=America/Vancouver`, a naive 14:20 is stored as 21:20Z, while `parse_stamp` reads it as 14:20Z. Also, `_encode` (176) stores a string time unchecked, and `_decode` (190) uses `fromisoformat` rather than `parse_stamp`. No caller does this today. Fix: treat a naive time as UTC (or refuse it), and accept only datetimes for `_TIMES` columns.
2. **Minor:** `index.py:169` catches only `OperationalError`. A `DatabaseError` (NOTADB/CORRUPT, probed) escapes, which can fail a Writer open or turn a committed rebuild into "rebuilding the index failed". It only happens in a race. Fix: catch `sqlite3.DatabaseError`.
3. **Minor:** §9.5 lists "flagged" for `plaud_checks`, but `state.py:63` has no such column. Decision 16 keeps flags in `plaud_events`. Say so in the SCHEMA comment, so that Phase 3 doesn't add a table for it.
4. **Minor (forward):** `State.forget` (`state.py:404`) finds the listing's title hash only through the markers. Phase 2 must write the hash and the marker together. Otherwise, read the Plaud refs from the last revision before deleting it.
5. **Minor:** there is no Writer-level test that a refused switch leaves the open working. The probe confirms it.

**Titles:** no column holds a title, and `set_listing(title=…)` is refused. Free-form fields can still hold a title: `plaud_events.detail`, `plaud_state` values, `change_marker`, `check_log.result`, and a non-hex "ID" (which `canonical_plaud_id` passes through unchanged). Nothing checks that `title_sha256` is a hash. Suggestion: require 64 hex characters in `set_listing` and `set_markers`. C7.01 (Phase 2) is the end-to-end proof.

## Security checklist
1. Secrets: **fine.** No token, URL or secret appears. The log line and the `NoSyncYet` text are fixed phrases.
2. Permissions: **fine.** No new folders. SQLite gives `-wal` and `-shm` the index file's mode, and the index volume is `web`'s alone.
3. Network: **fine.** Nothing listens. `checks.py` and `when.py` import no client, token or sync module, and the tests are offline.
4. Failing closed: **fine.** A missing table raises `NoSyncYet` and never reads as "no flags". `forget` keeps the kept-out state by Plaud ID, and the privacy gate is untouched.
5. Injection: **fine.** All values are parameterised. Column names come only from the dataclass whitelist (`_columns`), and `forget`'s table names are literals.
6. Logging: **fine.** There is one new line: a fixed phrase plus the class name, with no `exc_info`.

Verdict: approve
