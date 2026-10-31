"""The baseline's privacy part, the new-pattern pass, and the keep-out words enforced (stage 2b
spec §14.5, §13.1 case 5, §12.2).

- **The baseline** (`recordings plaud baseline`, before the catch-up) runs `Writer.reconcile_plaud`
  on every recording with a Plaud source, so every title the marker has never seen gets its first
  private and keep-out check (§13.1 case 4): 2a's imports, once. Seeding check state waits for the
  sweep (Phase 3). It counts, by the markers it moved, the titles it checked: a run after it checks
  none. A recording it can't write is listed, never skipped silently.
- **A new pattern** (case 5): `patterns_pass` runs the same reconcile over the archive when the
  pattern list has a version newer than the last pass, which the worker checks at start. Reconcile
  runs only the patterns each marker's version lacks, so a tag Dan removed stays removed for the
  patterns it already passed. A pass that couldn't write a recording isn't recorded as done, so
  the next start runs it again. It calls `progress` at every recording (§9.2), so a long pass at
  the worker's start reads as progress, never as stuck.
- **The keep-out words enforced** live in `plaud_state` (`K_KEEP_OUT_WORDS`, a JSON list, oldest
  first). The worker adds the configured word at every start, so changing the word releases
  nothing (2b phase 1 plan, decision 26); only `keep-out-words --drop` removes an old one, never the
  configured one. Dropping a word stops new keep-outs by it; what it kept out stays out until
  Release.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from recordings.locks import LockTimeout
from recordings.plaud.checks import K_KEEP_OUT_WORDS, K_PATTERNS_PASS, PlaudDB
from recordings.plaud.keepout import fold, valid_word
from recordings.plaud.privacy import current_patterns
from recordings.writer import ReconcileReport, Writer, WriterError

KEEP_CONFIGURED = "that is the configured keep_out_word: change [plaud] keep_out_word first"
NOT_ENFORCED = "that word isn't enforced: nothing to drop"
DROPPED = (
    "dropped: new titles with that word are no longer kept out; what it kept out stays out "
    "until released"
)
# What a reconcile of one recording can raise, as Writer.reindex catches it: a fixed record of the
# recording's ID, never the exception's text, which can carry a title.
RECONCILE_ERRORS = (
    WriterError,
    KeyError,
    LockTimeout,
    OSError,
    ValueError,
    TypeError,
    RecursionError,
    ArithmeticError,
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BaselineReport:
    titles_checked: int  # markers set or moved: each is one Plaud ID's newest title, checked
    private_added: int
    kept_out_late: int
    already_seen: int  # recordings every one of whose markers was there and not due
    unwritten: tuple[str, ...]  # recording IDs whose result couldn't be written

    def to_dict(self) -> dict:
        return {
            "titles_checked": self.titles_checked,
            "private_added": self.private_added,
            "kept_out_late": self.kept_out_late,
            "already_seen": self.already_seen,
            "unwritten": list(self.unwritten),
        }


def _plaud_recordings(writer: Writer) -> list[str]:
    return [
        rec.id
        for rec in writer.archive.iter_recordings()
        if any(s.kind == "plaud" for s in rec.sources)
    ]


def _reconcile(writer: Writer, db: PlaudDB, recording_id: str) -> tuple[ReconcileReport, int, bool]:
    """Reconcile one recording: (its report, how many markers moved, whether it had any before)."""
    before = db.markers(recording_id)
    report = writer.reconcile_plaud(recording_id)
    after = db.markers(recording_id)
    return report, sum(before.get(pid) != m for pid, m in after.items()), bool(before)


def baseline_privacy(writer: Writer, db: PlaudDB, *, now: datetime) -> BaselineReport:
    """§14.5's privacy part: every Plaud recording's titles checked once, by `reconcile_plaud`."""
    checked = added = kept_out = seen = 0
    unwritten: list[str] = []
    for rid in _plaud_recordings(writer):
        try:
            report, moved, had = _reconcile(writer, db, rid)
        except RECONCILE_ERRORS as exc:
            log.warning("baseline: %s not written (%s)", rid, type(exc).__name__)
            unwritten.append(rid)
            continue
        checked += moved
        added += report.private_added is not None
        kept_out += report.kept_out_word is not None
        seen += had and not moved
    return BaselineReport(checked, added, kept_out, seen, tuple(unwritten))


def enforced_words(db: PlaudDB) -> tuple[str, ...]:
    """The keep-out words the worker has enforced, oldest first."""
    stored = db.get(K_KEEP_OUT_WORDS)
    return tuple(w for w in (stored if isinstance(stored, list) else []) if valid_word(w))


def enforce_word(db: PlaudDB, word: str, *, now: datetime) -> tuple[str, ...]:
    """Record `word` as enforced (the worker, at every start), and return every enforced word."""
    words = list(enforced_words(db))
    if valid_word(word) and fold(word) not in {fold(w) for w in words}:
        words.append(word)
        db.put(K_KEEP_OUT_WORDS, words, now=now)
    return tuple(words)


def drop_word(db: PlaudDB, word: str, *, configured: str, now: datetime) -> str:
    """`keep-out-words --drop`: one of KEEP_CONFIGURED, NOT_ENFORCED or DROPPED."""
    if fold(word) == fold(configured):
        return KEEP_CONFIGURED
    words = enforced_words(db)
    kept = [w for w in words if fold(w) != fold(word)]
    if len(kept) == len(words):
        return NOT_ENFORCED
    db.put(K_KEEP_OUT_WORDS, kept, now=now)
    return DROPPED


@dataclass(frozen=True)
class PatternsPass:
    version: int
    checked: int  # markers moved, as the baseline counts them
    tagged: int

    def sentence(self) -> str:
        titles = "title" if self.checked == 1 else "titles"
        return (
            f"patterns changed: {self.checked:,} {titles} checked, {self.tagged:,} tagged private"
        )


def patterns_pass(
    writer: Writer,
    db: PlaudDB,
    *,
    now: datetime,
    progress: Callable[[], object] | None = None,
) -> PatternsPass | None:
    """§13.1 case 5 over the archive, when the pattern list has a version newer than the last
    pass's. The first version needs none: the baseline and each import check every title.
    `progress` is called at every recording (§9.2: the worker passes its heartbeat's bump)."""
    patterns = current_patterns(db, writer.auto_private, now=now)
    last = db.get(K_PATTERNS_PASS)
    done = last.get("version") if isinstance(last, dict) else None
    if patterns.version <= (done if isinstance(done, int) else 1):
        return None
    checked = tagged = failed = 0
    for rid in _plaud_recordings(writer):
        if progress is not None:
            progress()
        try:
            report, moved, _ = _reconcile(writer, db, rid)
        except RECONCILE_ERRORS as exc:
            log.warning("patterns pass: %s not written (%s)", rid, type(exc).__name__)
            failed += 1
            continue
        checked += moved
        tagged += report.private_added is not None
    result = PatternsPass(patterns.version, checked, tagged)
    if not failed:  # else the next start runs it again; reconcile skips what this one judged
        db.put(
            K_PATTERNS_PASS,
            {
                "version": result.version,
                "at": now.isoformat(),
                "checked": checked,
                "tagged": tagged,
            },
            now=now,
        )
    return result
