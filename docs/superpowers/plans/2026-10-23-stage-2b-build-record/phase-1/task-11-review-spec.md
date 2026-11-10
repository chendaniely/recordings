# Task 11 review: Spec axis (Tier 1), 4881a5f..7d4e0ca

The code matches the brief verbatim (I compared the files by script) and its 38 tests pass. Probed against synthetic state, pinging only FakeNtfy:
- **Blockers:** every blocker stops the ping. So do an unknown `blocker:` row and a junk blocker value.
- **R5:** fixed. Reverting `_ends` fails its test.
- **Errors while deciding:** a corrupt state.db, a clock that raises and a bad turn detail all send nothing.
- **`failed_since`:** kept across failures (also across turns that never pinged), dropped by a good ping.
- **Nothing else goes out:** no push, and no title, URL or `str(exc)`. `health` loads no client and no network module.

## Important
1. **C6.10's "not good, so no ping" is unproven.** `test_plaud_health.py:105`'s `DISK` has 0 successes, so it is never good whatever `disk_refused` says. I deleted `and not outcome.disk_refused` from `judge_run` (`health.py:71-76`) and all 25 tests still passed. That leaves the real case untested: some imports succeed, then the guard refuses one. That run would ping (spec §11.7, "A full disk"). No test goes from a disk streak straight to a ping either. **Fix:** add a table row `RunOutcome("good", 3, 2, True, …)` → no ping, and a test that runs `DISK` then `QUIET` → ping.
2. **The runbook has no row for `cause()`'s `error: \`<Class>\``** (`health.py:211-213`) or for `blocked: <name>`. Status can show either at 2 a.m., and the runbook gives no step. The test says "the runbook must know every phrase Status can show", but it checks only the blockers, `DISK` and `OFF`. **Fix:** add the rows (`rc logs worker` names the class; it needs a code fix), and assert them in the test, with `BAD_WORD`.

## Minor
3. No test proves that a small shrink counts as good (a mutation passed; §11.7, §16).
4. **Consent row:** "opens when" leaves out "can't be read" (`STOP_UNREADABLE`). "Do" covers only typo lines; a missing or unreadable list needs the file or its permissions fixed.
5. **Private-unwritten row:** it leaves out §16's "a backup" for a missing `recording.json` (`PRIVATE_UNWRITTEN_MISSING`).
6. **keep_out_word rule:** it leaves out "at least one letter or digit" (`valid_word`).
7. **"Never a cause: … failing … recordings"** overstates. §11.7's accepted limits let a recording's first failures start a work streak, and there is no companion check before the sweep.
8. **Commands that don't exist yet:** the runbook names `make plaud-sign-in` and a breaker reset, which come in later phases (plan decisions 5, 10), with no phase note.
9. **A non-dict `K_FAILING` pings** (probe), although an unknown cause reads as "work". A blocker row with an unknown name can never be cleared by `clear_blocker`. Both are reachable only by a hand edit or a rollback.
10. **Forward note for Task 13:** a turn that raised mid-sync must reach `conclude_turn` as a run that isn't good, never as `None`. Otherwise no streak starts, and a quiet run after it pings "up" (§11.7).

## Scenarios
| Scenario | Proven by this task's tests? |
|---|---|
| C5.08 | Only that the ping stops. Opening the blocker on a typo is Task 13's. |
| C6.01 | Yes. Busy, the gate, a pause and a wait share one `None` row; Task 13 drives each. |
| C6.02 | Yes. The rebuild's review list is Phase 2's. |
| C6.03 | Yes. |
| C6.09 | Yes. The UI half is Task 22's. |
| C6.10 | Partly (finding 1). |
| C6.12 | Yes. |

No renamed names. The Scenarios table is all "planned", the same as for other tasks.

## Security checklist
1. **Secrets: fine.** The ping URL is never logged or stored; `K_LAST_PING` holds the time, ok and status only.
2. **Permissions: not applicable.** This task creates no files and mounts nothing.
3. **Network exposure: fine.** Nothing listens. `health.py` loads no client, urllib, http or ssl module. The tests reach only FakeNtfy on the loopback.
4. **Failing closed: fine,** except Minor 9. An error while deciding, an unknown blocker and an unknown cause all stop the ping. No title is in any phrase.
5. **Injection: fine.** `PlaudDB` is parameterised. No shell. The URL is checked by `AlertConfig`.
6. **Logging: fine.** Only class names and HTTP statuses; no `log.exception` and no `exc_info`.

Verdict: changes needed (0 Critical, 2 Important)
