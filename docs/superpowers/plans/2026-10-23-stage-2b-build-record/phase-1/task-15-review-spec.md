# Task 15 review: Spec axis (Tier 1), doctor's worker role

Base 236ae13, head 4ef74a6. `test_doctor.py`: 71 passed. Probe script:
`<scratch>/t15/probe.py`.

**What matches §17.2, each with a test.**
- Each role forbids the right secrets: `web` both sets plus `deadman_sync_url`; `worker` the backup set (`deadman_url` included), with the alert set allowed; `backup` `deadman_sync_url` alone.
- `web` and `backup` report a `/plaud` folder and `RECORDINGS_PLAUD_TOKEN`.
- `worker` skips `_backup`, `_mirror`, `_ssh` and `_alerts`, takes `deadman_set` from the sync's URL, and has no alerts-off warning.
- A consent stop names line numbers only, and kept lines are a note.
- The token is checked with `is_file` and `stat` only, and no value or hash is printed.
- No doc names `JOB_SECRETS`, and none is now untrue. The brief names no Scenario; C10.03 is Phase 2's.

## Findings

1. **Important.** doctor.py:681 (§17.2; plan decision 10). Yes, a Phase 1 deploy's doctor sends Dan to a command that doesn't exist. `make plaud-sign-in` answers "No rule to make target" (checked). The problem shows on every Phase 1 `doctor --role worker` (exit 78) until Phase 2. Plan council G6 explains this in the runbook only, and doctor's output gets pasted into chats on its own. Fix: keep "sign-in needed" and leave out the command, for example `"sign-in needed: the Plaud sign-in comes with stage 2b's next phase; until then the worker stays idle (runbook section 6)"`. Change the test at test_doctor.py:946, and Task 17's `WORKER_PROBLEMS` (a substring match), to `"sign-in needed"`. Put "restore §17.2's wording" on the phase-end list.
2. **Minor.** test_doctor.py:911 (§12.2 "doctor lists them"; §21 "a worker case for each check"). Every test expects `keep_out_words == []`, so an `_enforced_words` that always returned `[]` would pass. The probe shows the words are listed correctly. Add a test that stores `K_KEEP_OUT_WORDS` and expects them.
3. **Minor.** doctor.py:496-506. In `worker`, `_consent` still says "the services that stay up don't need it" or "not mounted here; the import reads it". It says this next to the consent-stop problem (probes P2 and P2b). Skip `_consent` for `worker`.
4. **Minor.** doctor.py:644-647. Every `from_config` error is blamed on the sync's URL. A bad `[alerts] ntfy_server` ends with "(RECORDINGS_DEADMAN_SYNC_URL_FILE names its URL's file)" (probe P4). Add the parenthetical only when the error names that variable.
5. **Minor.** doctor.py:615, 619. A `state.db` that can't be read reports `keep_out_words: []`, which looks like "none enforced". Report `None` instead.
6. **Minor (beyond the spec).** doctor.py:127 also checks `RECORDINGS_PLAUD_TOKEN_FILE`. That is sensible, but no test covers it: add it to the test at test_doctor.py:1025.

## Security checklist

1. **Secrets:** fine. The token is never opened. The sync's URL is reported as a bool. ConfigError texts name settings, not values. The tests and probe P11 find no value, hash or consent ID in the JSON.
2. **Permissions:** fine. The 0700 and 0600 modes and the effective UID are checked, naming the path and the `chmod`. `deadman_sync_url` is refused in `web` and `backup`, and `deadman_url` in `web` and `worker`. One Minor gap: :122 checks the fixed `/plaud`, not `token_store`'s folder.
3. **Network:** fine. Nothing listens. Doctor loads no client or token module (checked `sys.modules`), the UI doesn't import doctor, and a test blocks sockets.
4. **Failing closed:** fine. Every consent stop is a problem, and `BAD_WORD` doesn't name the word. Doctor writes nothing.
5. **Injection:** fine. The SQL is parameterised, nothing goes through a shell, and paths come from config.
6. **Logging:** fine. Doctor logs nothing. It prints fixed phrases, paths, line numbers and UIDs, plus the enforced keep-out words, which §12.2 and §17.2 require. It prints no title and no pattern text.

Verdict: changes needed (0 Critical, 1 Important)
