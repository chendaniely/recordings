# Task 1 report: Project tooling

Status: DONE. Base: ec9d5af (recorded in task-1-base).

## Commits
- a308af0 chore(repo): copy the commit-message check from the plan-driven-development skill
- 5c40268 build(make): make help, make check, test-utc and leaks

Both messages pass scripts/commitmsg.py (exit 0); `git log -1 --format='%(trailers)'` shows Plan: each time.

## Implemented
- scripts/commitmsg.py: copied from the skill, plus the origin paragraph, two noqa comments, and ruff format. `ruff check` passes. No personal path in it.
- packages/core/tests/test_repo_tools.py: six tests, verbatim from the brief.
- Makefile: replaced verbatim from the brief (help first, check, test-utc, leaks, `## ` on every target, .PHONY).
- CLAUDE.md: Toolchain gained the make help/check line and the hand-run commit-message check.

## TDD
- RED: `uv run pytest packages/core/tests/test_repo_tools.py -q` gave `2 failed, 4 passed`. The failure was `make: *** No rule to make target `check'.  Stop.` (and `help`).
- GREEN: same command, `6 passed`. `make help` lists `help` first and `restic` last.

## make check
- `uv run ruff format .` left 114 files unchanged.
- lint clean; pytest 1350 passed, 141 deselected (94s); the make target chain `test` (including Vitest) and `test-utc` completed without error; gitleaks: 336 commits scanned, no leaks found. I only looked at the tail of the output (pytest summary and gitleaks), so I did not read Vitest's own summary line. Make would have stopped on failure.

## Self-review
- Diff matches the brief. Tabs in recipes are confirmed (32 tab-led lines).
- Working tree clean after the commits.

## Concerns
- None blocking. The "refused" text in commitmsg.py still points to AGENTS.md, as the brief says.
- The Bash tool refused a compound command (heredoc plus a command naming git), so I wrote the commit message with the Write tool instead.
