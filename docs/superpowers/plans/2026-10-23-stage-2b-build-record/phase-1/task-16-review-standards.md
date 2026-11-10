# Task 16 review: Standards axis (7894104..e139f58)

No hard violations. The checks: no new config key, so no settings row is needed; fixtures are synthetic (`of_{1:032x}`), with no personal paths; no title, token or consent ID reaches a report. ruff passes. The 21 related tests pass when I rerun them.

**Commit e139f58.** The subject follows `feat(backup): 🤖 …`. The body says why and quotes the line that showed the tests failing. `%(trailers)` shows `Plan: … Task 16`. `Co-Authored-By: Claude Sonnet 5.5` is right only if the implementer ran on Sonnet (the plan's commit format). The controller should confirm this.

## Judgement calls (all Minor)

1. **backup.py:7 and config.example.toml:63, the wording "doctor refuses".** Doctor reports a problem, but nothing stops `run_backup`. Outside Docker, a named token folder is still backed up. Fix: "doctor reports it as a problem".
2. **doctor.py:734, Speculative Generality.** The `isinstance` guard is redundant: `plaud_settings` already raises `ConfigError("[plaud] must be a table")` (settings.py:56–57), and the next `except` returns the same defaults. Its comment, "a crash line from _consent says so", is also hard to follow. Fix: delete the two lines.
3. **doctor.py:731–741 against 147–155, Duplicated Code.** Both functions resolve `plaud_settings(cfg).token_store.parent` and fall back on `ConfigError`. Fix: `_no_plaud_folder_named` uses `_plaud_folders(cfg)[0]`.
4. **doctor.py:757 and 776, Duplicated Code.** `Path(os.path.normpath(…))` is written twice. Fix: normalise the entries once.
5. **backup.py:356, 459–462 and 484, Duplicated Code.** Three places test "file or folder", each in its own way. Fix: one `_size(p)` helper for the estimate.
6. **test_doctor.py:46, a wrong comment.** The comment says config.example.toml has deploy.env and the consent folder, but the template's value holds only deploy.env. Runbook section 6 doesn't exist until Task 17. Fix: say that the template's comment asks for `/consent`, and that section 6 (Task 17) adds it.
7. **test_backup.py:1250–1254, dead state.** The outer `restored` and its `nonlocal` are never read outside `restore`. Fix: make it a local.
8. **backup.py:484, an edge case outside this axis.** `_came_back` checks against the live path's type. If a folder is gone when the restore test runs, it is expected back as a file, so the report says "is not in the snapshot" for a folder that did come back.

Two forward dependencies, which are not findings. The doctor warning and the template both say "runbook section 6", and the `CONSENT_MOUNT` comment says it is mounted in `backup`. Task 17 adds both the section and the mount, and must land in this phase.

Verdict: approve
