# Task 8 report

Status: DONE. Brief followed verbatim (code extracted from the brief by line range, not retyped).

Commits
- 70545bb feat(plaud): late consent listings are tagged private and flagged once ... Keep it here (flags.py, test_plaud_flags.py)
- e048326 feat(plaud): recordings plaud consent; the runbook ... (commands.py, cli.py, test_plaud_cli.py, test_runbook.py, first-run.md, CLAUDE.md)

TDD
- Cycle 1 RED: `uv run pytest packages/core/tests/test_plaud_flags.py -q` with flags.py absent: collection ImportError "cannot import name 'flags' from 'recordings.plaud'" (the brief expects ModuleNotFoundError; same cause, the package-level import form). GREEN: 23 passed.
- Cycle 2 RED: test_plaud_cli + test_runbook with commands.py moved aside and cli.py unedited: 6 failed (5 CLI: argparse invalid choice 'plaud'; 1 runbook: no heading). GREEN after cli.py, runbook, CLAUDE.md: test_plaud_cli + test_runbook + test_cli 29 passed.
- Note: I first wrote the source files together with the tests, then moved them out to observe RED; RED evidence is genuine.

make check (before the last commit, after ruff format): 1653 passed, 141 deselected; gitleaks no leaks. No flaky tests hit. (An earlier mid-task run had 5 failures only because the CLI tests existed before cli.py was wired; expected.)

Self-review: diff is the brief's code verbatim; ruff format changed nothing; runbook section inserted before "When the data disk fills"; git status clean; no background processes.

Concerns: none. First commit's message says ImportError instead of the brief's ModuleNotFoundError, to match the observed failure.

## Fix round (commit 8b0115e)

All nine rulings done in one commit, test-first (new tests failed on the old src: patch reverted to confirm; RED lines are in the commit body).
1. Per-recording warnings removed (flags.py no longer logs). State.flag now writes nothing when the row has the same message, so the first time is kept (no 2a test relied on the restamp). Test: three passes over a StaleFile recording: one flag time, state.db dump identical after pass 1, no WARNING/ERROR.
2. KeyError with the folder still present: flagged, unwritten, cause FileNotFoundError (new param case in the cant_be_written test); pending recording with unreadable file and existing folder: index_unavailable (test).
3. Shared `flags.listed_here(index, uuid, state, archive, ids)`, used by apply_late_consent and `commands._here` (state None = nothing pending); test for pending in `plaud consent`.
4. State.read_only; test that no `locks` dir/files are created (WAL -wal/-shm excluded, as State.read_only documents).
5. `keepout.read_consent_text` returns (ConsentList, text); cmd_consent reads once; read_consent delegates.
6. checks.py holds KEPT_OUT_LATE, CONSENT_LATE, PRIVATE_UNWRITTEN, KEPT_HERE_EVENT; flags.py imports them (names still importable from flags); `_plaud_ids(archive, rid)` and `_kept_here` reused.
7. PlaudDB.open_flags(kinds, recording_id=None), used by flag() and keep_here(); racing-writer test's monkeypatch signature updated.
8. `on_plaud`/`in_archive` are int/list or null, with `on_plaud_note`/`in_archive_note`.
9. Spec §15.6 sentence + Revisions line (2026-10-31).
make check: 1658 passed, gitleaks clean. Not changed (ruled): sharing _fail/_config with cli.py.

## Fix round 2 (commit 3f2ff99)

1. `flags._gone(path)` (os.stat; only FileNotFoundError/NotADirectoryError = gone) used at both places; module docstring updated. New test `test_a_folder_under_a_parent_it_cant_enter_is_owed_not_gone` (skipped as root; its own test, not a param of the unwritten test, because two recordings in one month share a parent). RED: `assert False is True` (index_unavailable) with the old exists() semantics; the first-pass part (flag + unwritten with cause) passes either way since mutate raises KeyError.
2. `commands._here` docstring mended.
3. `PRIVATE_UNWRITTEN_MISSING` via `flag_sentence` for cause FileNotFoundError; test added to the sentence-by-cause test (RED: got the disk sentence). Spec §15.6 sentence, §16 row ("follows the cause"), Revisions line.
make check: 1659 passed, gitleaks clean.
Note: a locked parent also yields cause FileNotFoundError (mutate's KeyError), so its flag says "recording.json is missing"; that is the ruled wording, only slightly imprecise for that case.
