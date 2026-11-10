# Task 13 review: the Spec axis (Tier 1), 5f30cb9..8ecb3d7

I ran `test_worker.py` (30 passed) and probed synthetic archives under `<scratch>/t13probe`.

## Findings

1. **Important.** `worker.py:397-407` and `_owed_private` (577). When the patterns pass at start can't write a §13.1 case 5 `private`, nothing opens: no flag and no `private-unwritten` blocker. The pass tries again only at the next start. Probed: a recording edited outside the app, plus a new pattern, leaves `K_PATTERNS_PASS.unwritten` set, no flag, and `blockers == []`. Spec §13.2 says "It opens the `private-unwritten` blocker (§16) until the tag lands", and §16's table agrees. Fix: pass the pass's `unwritten` IDs and their causes to `_owed_private` as flags `{"owed": "pattern", "cause"}`. `_still_owed` already keeps a non-consent flag open until the recording is private or gone. Or rule it Phase 2's and put it on the phase-end list.
2. **Important (the ruling asked for).** `worker.py:713-715`: `--alive` exits 78 on a config error (probed). §9.6's exit codes are for commands. Docker's health check defines 0 as healthy, 1 as unhealthy, and reserves 2. Moby reads any non-zero exit as unhealthy, so nothing breaks today. But the brief's interface ("--alive exits 0 or 1") and the help text at `cli.py:147` ("else 1") are untrue. Fix: under `--alive`, a `ConfigError` prints a fixed line and returns 1. Add a test.
3. **Minor.** `_starting_since` (258): a crash loop that dies after the start writes `idle` (an OOM kill during the patterns pass at start, say) gets a fresh 30 minutes at every restart, so it reads alive. Fix: keep `starting_since` through `checking`, or record this as decision 48's limit.
4. **Minor.** The patterns pass at start ignores SIGTERM. §9.2 says "it stops between recordings". The pass also holds back the first tick's late consent listings until it ends. Fix: end the pass from `_check_one` once `stopping` is set.
5. **Minor.** `test_worker.py:135-137`: the teardown sets `stopping` but never joins the heartbeat threads. Join them. (The `time.sleep(0.05)` at line 532 is the brief's, and needed: a file's change time can't be injected.)
6. **Minor.** `_open_writer`'s docstring says a busy index is retried; only `LockTimeout` is.
7. **Minor.** No worker test shows a typo stop still applying the exclusions that read (§12.1); a probe shows it does.
8. **Minor.** Adaptation 1 is right; make `flags._gone` public rather than import a private name.

## Scenarios

C5.08, C5.11, C5.14, C6.08 and C6.13 are each proved by a test in the diff. C5.18: the worker's half is proved (the turn makes no call and sends no ping). The refused import is Task 14's.

## Checked and fine

- A list that changes mid-read leaves the previous one in force; a stop (typo or missing) tags the exclusions that read and clears no flag (probed).
- Both blockers are worked out at every tick; each step's failure is caught and logged once per class.
- A wedged tick reads STUCK, a failed start FAILED; restarts keep `starting_since`.
- The gate is read at every tick; a turn it holds records "gate" and pings nothing.
- CLAUDE.md matches spec §19; no doc made untrue.

## Security checklist

1. Secrets: fine. Phase 1 has no token, and the sync's URL is never logged (class names only).
2. Permissions: not applicable. The worker creates no files, and compose is Task 17's.
3. Network exposure: fine. Nothing listens. Importing the worker loads no client, token store or sync module (tested), and the pings go to FakeNtfy.
4. Failing closed: fine for the consent list and the gate (a failed read runs no turn). The `private-unwritten` gap is finding 1.
5. Injection: fine. SQL is parameterised, no shell command is built, and recording IDs from flags go through `parse_id`.
6. Logging: fine. Logs carry fixed phrases, IDs, counts and class names, with no `exc_info`. `main` prints a `ConfigError`'s text, as the rest of the CLI does. That text names settings and paths, never values.

Verdict: changes needed (0 Critical, 2 Important)
