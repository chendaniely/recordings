# Task 17 review: Spec axis (f0b8c71..a6301d7, Tier 1)

**Evidence.** CI run 41211300653 passed, compose-smoke included: the worker went `Healthy` under `up --wait`, and `doctor --role worker` exited 78 naming all three problems. A `docker compose config` probe: with a 2a-era deploy.env, every command fails with "required variable RECORDINGS_CONSENT_HOST is missing a value: set … in docker/deploy.env". Section 6's "steps 1 to 5 in one sitting" covers the boot unit. With the topic unset, it renders `""` and `/dev/null`. The three test files: 105 passed.

**Scenarios.** C8.03 and C10.02: CI's compose-smoke. C10.01: `test_make_deploy_waits_for_every_health_check` and `test_a_deploy_waits…` (no service list), plus the smoke. C10.04: `test_each_dead_man_url_is_mounted_in_one_service`.

**These hold:**
- `/plaud` and `deadman_sync_url` are in `worker` alone, and `/consent` is read-only in `worker` and `backup`, never in `web`.
- The worker is hardened (the all-services test), publishes no port and has its own health check.
- The topic is optional, and the backup still pings without it.
- Section 6 checks out first. Its wording matches the code: doctor.py:719, gate.py:24 and `_emit`.
- The rollback order follows §17.3, and section 6 step 5 points to it.

## Important

1. **first-run.md:1262–1272: "Rolling back across 2b" fails in a fresh shell.** The top block never exports `RECORDINGS_IMAGE_TAG`, so `rc stop worker` fails (probe confirmed) and `&&` skips the `rm`. The pasted lines still run: `make rollback` starts 2a beside the running 2b worker, an orphan 2a's compose file leaves up. The go-forward `rc restart backup` needs the tag too. **Fix:** `sh docker/up-deployed.sh stop worker && sh docker/up-deployed.sh rm -f worker` (§17.5: "elsewhere, `sh docker/up-deployed.sh`"), or export the tag first as 2.4 does. Export it again after `make deploy`, and update the test's regex.
2. **From-scratch deploys at Phase 1's commit now fail.** compose.yml now needs the consent and plaud folders and `secrets/deadman_sync_url`. "Restoring for real" on a new server:
   - its prerequisite list (from 1291) makes none of them;
   - step 3 has no line that puts the snapshot's `/consent` back, which is the reason §12.1 backs it up;
   - its path list (1282) leaves out `/consent`;
   - so step 4's `rc up -d` fails.

   Section 0's "the commit you will deploy" also lets 2.4's `backup init` and `up -d` run at a 2b commit, where they fail the same way. **Fix:**
   - add section 6 steps 1, 2 and 4 to the new-server list;
   - add `mv "$R/consent" "$RECORDINGS_HOME/consent"` to step 3, and `/consent` to the path list;
   - in section 0 or 2, say that sections 2–5 deploy 2a's last commit before 2b.

## Minor

- The commit body says the Compose file reference was checked, but the report says it wasn't. Correct this in the close.
- Outside the diff: 1131 and 742 say doctor's "alerts are off" warning also shows in `web`, but `_alerts` runs only for `backup`/`all` (doctor.py:836).
- In README:129, the deploy.env row still says "stage 1". The two folders and the topic's file are 2b's.

## Security checklist

1. **Secrets:** fine. No values anywhere: the smoke uses placeholders, and alert-test prints booleans. No token code here.
2. **Permissions:** fine. `/plaud`, `deadman_sync_url` and `deadman_url` each sit in one service, tested and rendered. The folders are 0700 and the URL file 0600 (runbook and smoke). `/consent` is read-only.
3. **Network exposure:** fine. The worker has no `ports` or `expose`, and its check is local. The tests use FakeNtfy, and the UI is untouched.
4. **Failing closed:** fine. A bad or empty sync URL leaves the worker up and idle, with a doctor problem (worker.py:350). No list means a consent stop. A missing variable fails compose by name, with no default. No titles.
5. **Injection:** fine. The smoke runs subprocesses from argument lists, the runbook quotes its paths, and no shell is built from data.
6. **Logging:** fine. There are no new log lines. The smoke prints doctor's stderr only on failure.

Verdict: changes needed (0 Critical, 2 Important)
