# Task 18 review: Spec axis (Tier 1)

Tests: 63 passed. **Scenarios:** none named. The tests prove §21's deploy and shell-target items.

## Important
1. **docker/consent-add.sh:33-34 creates or extends a missing or empty list.** §12.1 makes that list a consent stop and says consent-add "appends". If the list is lost, or `RECORDINGS_CONSENT_HOST` names an empty folder, one add lifts the stop with a single exclusion. The next sync can then import the real list's recordings. The brief asked for this, but the spec wins. Fix: refuse, and point to runbook §6 step 1. test_make_targets.py:142 pins the current behaviour.
2. **scripts/consent_id.py:25 silently drops a second ID after a dash.** `ID='- \`of_A\` —- \`of_B\` —'` (two Copy IDs pasted) exits 0 and adds only A. Fix: refuse when another run of 24 or more hex digits follows, and add a test.

## Minor
- An ID already on the list is appended again and reported as "added". The count stays the same, which the runbook (line 1282) reads as "the worker didn't see it".
- The readback runs before the worker's next 2-second tick, so the worker's count shown is still the old one.
- Makefile:129 gives one message for an unreachable server, a failed add, and a failed readback after a good add.
- deploy-guard.sh:58 says "start again from the beginning", which is wrong for Sync everything (it resumes, §14.6).
- Runbook line 1278 says "any editor … renames", which isn't true of nano. Line 1286 leaves out decision 12's `- 20261023 — asked`.
- docs/upstream-issues.md:312 is not an upstream gap.

Docs grep: nothing stale, and no "not yet", "planned" or "unverified" near the change.

## Security checklist
1. Secrets: fine. Nothing reads or prints a secret.
2. Permissions: fine. The temporary file is 0600, and the list keeps its old mode.
3. Network: fine. Nothing new listens, `keepout` is network-free, and the tests use only the fakes.
4. Failing closed: findings (Important 1 and 2). The guard refuses an unreadable holder and a probe error, and no title is echoed.
5. Injection: fine. The ID is taken unexpanded, passed through the environment, and checked as 32 hex digits before ssh and again on the server.
6. Logging: fine. Messages carry fixed phrases, IDs and job names only.

Verdict: changes needed (0 Critical, 2 Important)
