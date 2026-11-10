# Task 11 review: Standards (Tier 1)

Code and tests match the brief byte for byte (diffed). ruff check and format are clean. No title, token, URL or `str(exc)` reaches a log or phrase. Commit 7d4e0ca: subject `feat(worker): 🤖 …`, body gives the why and the failing line, `Plan:` trailer parsed. `Co-Authored-By: Claude Sonnet 5.5` is allowed by the plan's commit format (the implementer's model).

## Standards (all Minor)

1. **health.py:21** "Phrases are §15.6's" overclaims, against Global Constraints ("§15.6's, word for word"). "the sync is failing (since …)", "scheduled sync is off", busy's " since 14:20", "blocked: <name>" and the bare fallbacks ("rate limited", "skipped: busy", "error") are not in §15.6. The plan prescribes them and none leaks anything. Fix: say "§15.6's and §15.2's", or record a ruling.
2. **worker.py:78–79** logs a failed ping a second time (`ping_url` already logs it) and writes "HTTP None" when the check can't be reached. Fix: drop the line, or log "unreachable" when the status is None.
3. **test_runbook.py:254** says the runbook knows "every phrase Status can show". The runbook has no row for `error: \`<Class>\``, which Task 13 records, and the test doesn't pin BAD_WORD or GATE. Missing docs may wait for the phase end (preferences.md, "Docs"). Put it on the phase-end list, or add the row.

## Smells (judgement calls, Minor)

- **Duplicated Code:** `listing in ("good", "small-shrink")` appears 3 times (L72, 79, 88). K_FAILING's cause normalisation appears twice (L102, L224). `status is not None and 200 <= status < 300` (worker.py:72) repeats `alerts.ping_deadman` (alerts.py:240). Extract `_GOOD_LISTINGS`, `_read_failing()`, and an `alerts` helper.
- **Shotgun Surgery / Feature Envy:** `health.py` owns every other `plaud_state` record, but K_LAST_PING is written in `worker._ping_record` and read in `sync_health` (Task 19 reads it raw too). Move it to `health.record_ping`.
- **Primitive Obsession:** `SyncHealth.failing`, `last_turn` and `last_ping` are positional tuples that callers unpack by position (L175, L183). A NamedTuple adds names and still compares equal to the tests' tuples.
- **Mysterious Name:** `_ends` → `_cause_ended`. `EPOCH` is public and sits mid-module (L152), while `_CAUSES` is private and at the top.
- **Data Clump:** `TurnContext` holds both `state` and `db` (derived from it). `conclude_turn` uses each for a different call.
- **Fixed phrases by trust:** the gate's `reason` (L197) and `error`/`job` are shown verbatim from a free-form `detail`. Only the callers keep them fixed.

Verdict: approve
