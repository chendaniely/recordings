"""The documents later work argues from agree with the approved stage 2b spec (its §19, and §20
criterion 11)."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DECISION_7_DOCS = (
    "README.md",
    "docs/runbooks/first-run.md",
    "docs/superpowers/specs/2026-10-15-tagging-a-design.md",
)


def _flat(rel: str) -> str:
    """The file's text with every run of whitespace as one space: the README's old sentence spans
    a line break, and a sentence rewrapped must still be found."""
    return " ".join((REPO / rel).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("rel", DECISION_7_DOCS)
def test_decision_7_is_worded_as_refined(rel):
    # why: C11.01. Dan's decision 7 (2026-10-23) refines his 2026-10-22 one: the first real tag
    # waits for stage 2b's first real Plaud import and the backup after it, not for the dry run,
    # which only reads. A document still saying the old one would invite a tag a rollback loses.
    text = _flat(rel)
    assert "until stage 2b's import dry run is done" not in text
    assert "refining 2026-10-22" in text


def test_claude_md_carries_decision_1s_exception():
    # why: 2b spec §13.3. Agents read CLAUDE.md's privacy rule; it must name the one deliberate
    # exception, or a correct sync would look like a bug, and a wrong one like the rule.
    text = _flat("CLAUDE.md")
    assert "One deliberate exception (stage 2b, Dan, 2026-10-23)" in text
    assert "judged on every title the app has seen at that moment" in text
