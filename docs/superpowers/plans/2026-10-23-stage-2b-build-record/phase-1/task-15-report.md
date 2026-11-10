# Task 15 report: Doctor's worker role

Status: DONE. Commit 4ef74a6 (base recorded in task-15-base).

## Implemented
Exactly the brief: ROLES with worker; BACKUP_SECRETS, ALERT_SECRETS, PLAUD_MOUNT; _forbid and _no_plaud_here replacing _no_job_secrets/JOB_SECRETS; _lines, _owner, _plaud_modes, _enforced_words, _worker; state.db missing is a problem for worker; role dispatch in doctor(); docstring; cli.py help names worker. Tests: the brief's imports, fixtures, three edited tests, and 16 new tests.

## TDD
- RED: `uv run pytest packages/core/tests/test_doctor.py -q` -> `ImportError: cannot import name 'ALERT_SECRETS' from 'recordings.doctor'`, 1 error during collection.
- GREEN: same command -> `71 passed in 5.56s` (first run after implementing).
- `uv run ruff format .` -> 149 files left unchanged.
- `make check` -> `1841 passed, 141 deselected, 1 warning`; gitleaks: no leaks. No test outside the task failed.

## Files
packages/core/src/recordings/doctor.py, packages/core/src/recordings/cli.py, packages/core/tests/test_doctor.py.

## Adaptations
None needed: keepout.read_consent (.stop, .ids, .review_lines), typo_phrase, plaud_settings (.keep_out_word_ok, .consent_list, .token_store), BAD_WORD, alerts.from_config's deadman_sync_url and checks.PlaudDB/K_KEEP_OUT_WORDS/NoSyncYet all exist as the brief's code uses them. No test changed what it proves.

## Self-review
- Diff matches the brief; no value, hash or token content is read or reported (token only stat'ed / is_file'd).
- Note: all new tests passed on the first run after the implementation, as the only RED was the collection ImportError the brief predicts.
- Note: a worker with a ConfigError from alerts.from_config leaves report["alerts"] as None (as the brief's code does); the problem text names the setting only.

## Concerns
None.

## Fix round (commit 2c9eb46)
Rulings 1-6 all applied, test-first. RED (`uv run pytest packages/core/tests/test_doctor.py -q`): 8 failed, 70 passed. Failing: the sign-in test (problem still held `make plaud-sign-in`), the consent-stop and missing-list tests (message now "stage 2b spec §12.1"), the worker consent-notes test, the token_store-folder test for web/backup, the non-list keep-out row test (keep_out_words was `[]`), the damaged state.db test (`[]` not None), and the sync-URL hint test (its first draft appended a duplicate `[alerts]` table; fixed to replace the line). Green at once (they pin existing behaviour, no RED): the enforced-words listing, and the `RECORDINGS_PLAUD_TOKEN_FILE` problem in web/backup. GREEN: 78 passed; `make check`: 1848 passed, vitest 380 passed, gitleaks clean.

Changes: sign-in text; `_consent(cfg, role, report)` drops both notes for worker; `_enforced_words` uses `baseline.enforced_words` (None when unreadable, a problem on KeepOutWordsError, `[]` on NoSyncYet); `_alert_config` shared by `_alerts` and `_worker`, hint only if the refusal names DEADMAN_SYNC_URL; `_no_plaud_folder_named` checks `[plaud] token_store`'s folder (only when configured); `FORBIDDEN` table; `PLAUD_MOUNT` and the token path from `DEFAULT_TOKEN_STORE`; "stage 2b spec §12.1" (the main-spec "§9.1" in the 2a note left as is); test renamed `test_every_secret_a_role_forbids_is_a_known_secret`; `--role` help built from ROLES; new `keepout.kept_lines_note` used by doctor and `plaud/commands.py` (its output unchanged).

For the controller: Task 17's `WORKER_PROBLEMS` substring must be "sign-in needed" (it does not exist yet). Phase-end: restore §17.2's wording with the make target in Phase 2.
