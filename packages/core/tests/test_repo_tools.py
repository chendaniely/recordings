"""The repo's own tools: the commit-message check (`scripts/commitmsg.py`, copied from Dan's
plan-driven-development skill) and the Makefile's front door (`make help`, `make check`)."""

import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "commitmsg.py"
PLAN = "Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Task 3"
COAUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _problems(message: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("commitmsg", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.problems(message)


def _make(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def test_commitmsg_accepts_a_task_commit():
    message = (
        "feat(plaud): 🤖 the consent grammar reads of_ and upper-case IDs\n\n"
        "What changed and why.\n\n"
        f"{PLAN}\n{COAUTHOR}\n"
    )
    assert _problems(message) == []


def test_commitmsg_refuses_a_plan_line_after_a_blank_line():
    # why: git reads trailers from the last paragraph only, so a blank line between Plan: and
    # Co-Authored-By: silently drops the Plan: line, and the task's commits can't be found by it.
    message = f"feat(plaud): 🤖 the consent grammar\n\nWhy.\n\n{PLAN}\n\n{COAUTHOR}\n"
    assert any("isn't parsed as a trailer" in p for p in _problems(message))


def test_commitmsg_refuses_a_close_without_its_headings():
    headings = "Drift: none\nRulings: none\nPhase-end: none\nTier: 3\nRounds: 1\nFindings: none\n"
    message = f"docs(plan): 🤖 close Task 1: Project tooling\n\n{headings}\n{PLAN}\n{COAUTHOR}\n"
    assert _problems(message) == ["a closing commit needs a 'Forward: …' line"]


def test_commitmsg_names_its_origin_and_no_path():
    # why: the repo is public. The copy says where it came from, and never where that is on disk.
    text = SCRIPT.read_text(encoding="utf-8")
    header = text.split('"""')[1]
    assert "copied from Dan's plan-driven-development skill" in header
    assert "/Users/" not in text and "~/" not in text


def _phony() -> list[str]:
    names = []
    for line in (REPO / "Makefile").read_text(encoding="utf-8").splitlines():
        if line.startswith(".PHONY:"):
            names += line.removeprefix(".PHONY:").split()
    return names


def test_make_help_lists_every_phony_target():
    # why: Dan's standing preferences: the Makefile is the front door, and `make help` lists every
    # target with what it does. A target without its `## ` line would be missing from the list.
    result = _make("help")
    assert result.returncode == 0, result.stderr
    listed = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    phony = _phony()
    assert {"help", "check", "test-utc", "leaks"} <= set(phony)
    assert set(phony) <= listed, sorted(set(phony) - listed)


def test_make_check_runs_lint_the_suites_utc_and_leaks():
    result = _make("-n", "check")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "uv run ruff check ." in out and "uv run ruff format --check ." in out
    assert "uv run pytest" in out and "npm test" in out
    assert "TZ=UTC uv run pytest" in out
    assert "gitleaks git --redact --no-banner ." in out
