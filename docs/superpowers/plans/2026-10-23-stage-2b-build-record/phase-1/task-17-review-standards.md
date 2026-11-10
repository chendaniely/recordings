# Task 17 review: Standards (f0b8c71..a6301d7, Tier 1)

Re-run: the three test files, 105 passed; ruff clean.

## Important

1. **The docs-check record is wrong** (CLAUDE.md: "Say in the commit or PR what you checked"). The commit says it checked the Compose file reference's `healthcheck`, `init` and `stop_grace_period`. The report says it didn't. The new `upstream-issues.md` entry says no doc covers a service's `healthcheck` replacing the image's, but the file reference does. Fix: the close commit's `Drift:` records this. Narrow the entry to the real gap: the `up` page doesn't say `--wait` fails on unhealthy.
   I read the pages on 2026-10-31. What the change relies on:
   - `init` forwards signals and reaps: holds.
   - `stop_grace_period` is the wait before SIGKILL: holds.
   - `healthcheck`: "can override the values set in the Dockerfile", with "the same default values" as the image's. `test`, `interval` and `start_period` hold. The unset `timeout` stays the image's **3 s** (`Dockerfile:80`, sized for a urllib probe), and `retries` stays 3. See Minor 3.
   - `up --wait` waits for "running|healthy": holds. CI's smoke proves it.
2. **`first-run.md:1282`, "Restoring for real", is now wrong.** It lists four snapshot paths and restores nothing from `/consent`. Section 6 step 3 adds `/consent` to every snapshot, but a restore leaves the list behind, so imports stop. Standard: preferences.md, "Wrong docs are fixed in the change that made them wrong." Fix: list `/consent`, and copy `$R/consent/.` back beside line 1336's `deploy.env` copy (mode 700).

## Minor

3. **`compose.yml`, the worker's health check.** `--alive` starts the whole CLI (0.2–0.45 s here) within an unstated, inherited 3 s. Fix: an explicit `timeout: 10s`, also in the test.
4. **Duplicated Code, `cli.py:548`.** `status is not None and 200 <= status < 300` is its third copy (also `alerts.py:240`, `worker.py:115`). Fix: one `alerts` helper.
5. **Duplicated Code, `test_deploy_template.py`.** `:698` repeats `:582`'s token test line for line: parametrize them. The new `mounts()` (`:610`) leaves inline copies at `:79`, `:94` and `:515`, and `:603` shadows it with a local: use the helper.
6. **"Rolling back across 2b".** A pre-2b `compose.yml` requires a non-empty `secrets/ntfy_topic` (main's `:146`): say so. Unlikely to bite, since any pre-2b image was deployed with it.

## Commits

One commit: the subject's format, the why, the failing line (`KeyError: 'worker'`) and `Plan:` are all right. The Sonnet `Co-Authored-By` matches Tasks 14–16.

Verdict: changes needed (0 Critical, 2 Important)
