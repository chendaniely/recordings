# Task 18 review: Standards (Tier 1)

Pages read: GNU make manual (§3.3, §5.1.2, §5.7.2, §6.5–6.7, §8.9, §8.11) and NEWS; POSIX.1-2024 XCU ch. 2 (§2.6.2, §2.14, §2.15 `trap`), XBD §9.3.5, the utilities index, `chmod`, `tail`, `ls`, `mv`; coreutils "mktemp invocation"; macOS mktemp(1).

## Important
1. **docs/upstream-issues.md:312, the new entry: wrong, and misfiled.** The file is for "Problems found in libraries this repo depends on"; CLAUDE.md files a gap only when "the docs don't cover it". These docs do: make §5.7.2 documents `export` (in the make.html the implementer read), and XCU §2.15 says "The condition can be EXIT, 0 (equivalent to EXIT)" (in the V3_chap02.html it read). `mktemp` isn't POSIX, even in 2024's index, but coreutils and mktemp(1) both document `mktemp template` making a 0600 file. The entry has no workaround ("Local note only"). Fix: delete it, and put this reading in the fix commit's body, since the commit body says these three went unread.

## Minor
2. **docker/consent-add.sh:17** `*[!0-9a-fA-F]*` uses ranges, which XBD §9.3.5 leaves unspecified outside the POSIX locale. I ran it: under `LC_ALL=en_US.UTF-8`, macOS's sh (bash 3.2) accepted `of_é000…1` and wrote it to the list. macOS ssh sends `LC_*` to the server. dash and make's canonical ID keep normal use safe. Fix: `LC_ALL=C; export LC_ALL`, or `[!0123456789abcdefABCDEF]`.
3. **Process:** the system `python3` (report) breaks CLAUDE.md's "Python only through uv … never the system Python". The commit doesn't depend on it. Record it in the close's Findings.
4. **Makefile:127** blames the ID for any `uv run` failure. Write "checking the ID (above)".
5. **first-run.md:1278** "any editor, which writes a new file and renames it": nano writes in place, and the list is 0400. Harmless (Task 13), but untrue.
6. **Smells (judgement):** *Mysterious Name*: `ops-busy.py` and `ops_lock_held(…, key=)` now handle any lock; the plan named them. *Duplicated Code*: `running_backup_guard`'s fake docker repeats `test_a_deploy_waits_while_a_job_runs_unless_forced`'s, `test_make_targets.py` repeats the `subprocess.run(["make", …])` call four times, and deploy-guard.sh:34 and :60 repeat the "could not ask…" text.

## Fine
- Make 3.81 has `-include`, `$(if)`, `$(error)` and `$(value)`; `override` and `export` sit on separate lines. A simple variable is "never expanded again", so `$(…)` in `ID` never runs. CI runs these tests under dash and make 4.3.
- POSIX covers `trap`, `${#}`, `tail -c`, `ls -l`'s mode, `chmod g=`, mv as rename().
- 63 tests pass; ruff, `make -n`, `make help` and the commit are clean.

Verdict: changes needed (0 Critical, 1 Important)
