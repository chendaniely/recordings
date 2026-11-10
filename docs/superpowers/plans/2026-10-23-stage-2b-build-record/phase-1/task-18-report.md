# Task 18 report

Commit: 1d8da42 (stage-2b). Status: DONE_WITH_CONCERNS.

## Built
ops-busy.py (lock name + jobs), deploy-guard.sh (asks ops, then plaud for the three long jobs), docker/consent-add.sh, scripts/consent_id.py, Makefile (-include local.mk, ID taken as written, consent-add), test_make_targets.py, test_deploy_template.py and test_runbook.py additions, runbook (section 3, section 4, two new sections), README (three edits). All from the brief verbatim.

## Steps
- 0 skipped. 1 tests written. 2 failed as expected. 3-7 built. 8 passed except the documented-line test, which needs the README/runbook docs (expected). 9 docs. 10 69 passed. 11 `ruff format` unchanged, `make check`: 1885 passed, gitleaks clean. 12 committed, commitmsg.py ok.

## Step 2 failing lines
- `FileNotFoundError: [Errno 2] No such file or directory: '.../docker/consent-add.sh'` (5 errors, test_make_targets fixture)
- `AssertionError: assert 0 == 2` (probe with no lock name); `assert (0 != 0) == True` for plaud-full (guard); `AssertionError: no heading '## Keep a recording out for good'`; `ValueError: list.index(x): x not in list` (re-import test); the stopped-backup guard test failed with exit 99 (fake docker's new argument form).

## Built differently
- Added a note to docs/upstream-issues.md (not in the brief's file list) for gaps in the docs I could read.
- I used system `python3` once (stdlib only, no packages) to splice the brief's blocks into the test files; the later splices used `uv run python`.
- Two stray fence lines from the splice were removed by hand (a trailing ``` in test_deploy_template.py).

## Docs checked
- GNU make manual (make.html, slice 200000-300000): override has higher priority than all assignments except another override (and sets a variable given on the command line); `value` returns the text "without any expansion"; origin returns undefined / command line / environment / file / override...; `-include` (section 3.3) ignores a missing file with no error.
- POSIX shell chapter: `${#x}`, `${x#w}`, `${x%w}`.
- Not covered: the `export` directive's section (per-node pages gave HTTP 429), `trap ... EXIT`, and `mktemp` (not POSIX). Recorded in docs/upstream-issues.md. Behaviour verified instead by running the target for real under macOS /usr/bin/make 3.81.

## Self-review
Brief followed line for line; the "before" texts all matched the current files. consent-add.sh relies on `mktemp`, `ls -ln` and `chmod u=,g=,o=`, which exist on Linux (server) and macOS (tests). Tests use synthetic IDs only; no ssh beyond the fake.

## Concerns
- Doc gaps above (export, trap EXIT, mktemp) are unread rather than known wrong.
- The WebFetch tool rate-limited the manual's per-node pages, so some quotes came from slices of the single-page manual.

## Fix round 1 (commit 8789f9e)

1. consent-add.sh refuses a missing, empty or blank-only list (exit 2, nothing written, no temp file), pointing to section 6 step 1 / "Restoring for real"; the new-list path is gone; the creation test now expects refusal; a test covers "", whitespace-only and missing. Failing line before: `assert 0 == 2` (the script exited 0 on a missing list).
2. consent_id.py refuses any second run of 24+ hex digits (two Copy IDs, an ID in the note). Tests: the two-Copy-ID line and a second ID in the note are refused; one Copy ID with an ordinary note passes. Failing line before: `assert (0 != 0)` (make exited 0).
3. docs/upstream-issues.md entry deleted. Read this round: POSIX XCU ch. 2 (EXIT condition: "shall occur when the shell terminates normally (exits)"); macOS `man mktemp` (mode 0600, trailing-X template). GNU make's `export` section and coreutils' mktemp page answered HTTP 429 and were NOT READ by me (the review's report quotes them); stated as such in the commit body, not filed as a gap.
4. Bracket expression lists digits one by one; test under en_US.UTF-8 with a non-ASCII character among 32 characters (skips with a reason if the locale is missing). Failing line before: `AssertionError: of_é000... assert (0 == 2)`.
5. Makefile: step 1 says "checking the ID (above)"; the remote command exits 3 (add failed) or 4 (readback failed); ssh 255 is "could not be reached"; each message tested against the fake ssh (fake ssh honours FAKE_SSH_EXIT; fake up-deployed.sh fails on a marker file). ID still read from the environment.
6. The target ends with the "within a few seconds ... run rc exec worker recordings plaud consent" line; the runbook says the same, and that an ID already on the list is added again (count unchanged).
7. deploy-guard.sh words each long job's cost (Sync everything resumes, spec 14.6; the first catch-up and the baseline are run again by hand) and has one `cannot_ask` function.
8. Runbook: no "any editor ... renames"; says the list is 0400 and that the watcher compares inode, size and mtime (keepout.signature), so an edit in place shows too; decision 12's `- 20261023 — asked` added. New runbook test pins these.
9. test_make_targets.py has `make()`, `run()` and `script()` helpers; `fake_running_backup` is shared by the two guard tests.

Tests: `make check` 1890 passed, gitleaks clean. Python only through uv this round.

Concerns: (a) a list that holds only comments (no ID) is also a consent stop (STOP_NO_IDS) but is not "empty" to the script, so consent-add still extends it, per the ruling's wording; say if that should refuse too. (b) The cost wording for Sync everything ("loses the one in progress") is my reading of spec 14.6 "continues with what isn't checked since". (c) GNU make `export` and coreutils mktemp pages not read by me.

## Fix round 2 (commit 8f3ec57)

consent-add.sh now drops comment lines (`grep -v '^[[:space:]]*#'`, the parser's `lstrip().startswith("#")`) before the blank check and refuses with the same message, now "missing, empty or only comments"; header, README, runbook and test strings updated. A list with kept/text lines but no exclusion is still allowed (no server-side parser), per the ruling. Tests: comments-only list (one indented) refused, nothing written, no temp file left (added to the existing refusal loop); comments plus a kept line is extended. Failing line before: `AssertionError: # only / # comments (indented) ... assert 0 == 2` (script exited 0). `make check`: 1891 passed, gitleaks clean.

## Fix round 3 (commit 407fc25)

N1: consent-add.sh counts only printable ASCII under LC_ALL=C (the re-review's rule); NFKC check with uv listed only U+0023, U+FE5F, U+FF03, so no extra pattern. The seven lists are in the refusal loop; a BOM before a real list is still extended; the header no longer claims the parser's rule and says the stricter case fails closed. N2: `-r` check ("cannot read ... owner and mode"), test skips as root. N3: 255 message reworded. N4: remote exit 5 for the readback's 78 ("the ID was added, but the list still stops imports"), via an escaped `\$$?` (the test now allows that one `$?`). N5: the closing line and the runbook name `(by the worker: N IDs)`. N7: consent_id.py applies `_ID_LIKE` to `visible(text)`; comment moved above; fullwidth and zero-width tests. Failing lines are in the commit body; the exit-78 assertions and the BOM-real-list test were not seen failing first (stated there). N6 left for the phase-end list. `make check`: 1894 passed, gitleaks clean.
