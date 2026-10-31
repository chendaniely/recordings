"""The baseline's privacy part, the new-pattern pass, and the keep-out words enforced (stage 2b
spec §14.5, §13.1 case 5, §12.2).

- **The baseline** (`recordings plaud baseline`, before the catch-up) runs `Writer.reconcile_plaud`
  on every recording with a Plaud source, so every title the marker has never seen gets its first
  private and keep-out check (§13.1 case 4): 2a's imports, once. Seeding check state waits for the
  sweep (Phase 3). It counts, by the markers it moved, the titles it checked: a run after it checks
  none. A recording it can't write (a busy or full state.db included), or whose recording.json
  doesn't validate, is listed with its cause, never skipped silently; one locked by someone else is
  "busy": neither a failure nor an attempt.
- **A new pattern** (case 5): `patterns_pass` runs the same reconcile over the archive when the
  pattern list has a version newer than the last complete pass's, which the worker checks at
  start. Reconcile runs only the patterns each marker's version lacks, so a tag Dan removed stays
  removed for the patterns it already passed. A pass that left any recording unwritten or busy
  isn't recorded as complete, so the next start runs it again. It calls `progress` at every
  recording (§9.2), so a long pass at the worker's start reads as progress, never as stuck.
- **The keep-out words enforced** live in `plaud_state` (`K_KEEP_OUT_WORDS`, a JSON list, oldest
  first). The worker adds the configured word at every start, so changing the word releases
  nothing (2b phase 1 plan, decision 26); only `keep-out-words --drop` removes an old one, never the
  configured one. Dropping a word stops new keep-outs by it; what it kept out stays out until
  Release. A row that isn't a list is refused and left alone (`KeepOutWordsError`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from recordings.ids import ID_RE
from recordings.locks import LockTimeout
from recordings.models import fold
from recordings.plaud.checks import K_KEEP_OUT_WORDS, K_PATTERNS_PASS, UNCHANGED, PlaudDB
from recordings.plaud.keepout import valid_word
from recordings.plaud.privacy import current_patterns
from recordings.writer import RECONCILE_ERRORS, ReconcileReport, Writer

KEEP_CONFIGURED = "that is the configured keep_out_word: change [plaud] keep_out_word first"
NOT_ENFORCED = "that word isn't enforced: nothing to drop"
DROPPED = (
    "dropped: new titles with that word are no longer kept out; what it kept out stays out "
    "until released"
)
NOT_A_LIST = (
    f"plaud_state key `{K_KEEP_OUT_WORDS}` isn't a list of words: left as it is, nothing changed"
)
INVALID_FILE = "InvalidFile"  # the cause of a recording.json that doesn't validate
# The pattern list's first version: the baseline and each import check every title against it,
# so only a later version needs a pass.
FIRST_PATTERN_VERSION = 1
log = logging.getLogger(__name__)


class KeepOutWordsError(ValueError):
    """The stored keep-out words aren't a list: refuse, never overwrite them."""


@dataclass(frozen=True)
class BaselineReport:
    titles_checked: int  # markers set or moved: each is one Plaud ID's newest title, checked
    private_added: int
    kept_out_late: int
    already_seen: int  # recordings every one of whose markers was there and not due
    unwritten: tuple[str, ...]  # recording IDs whose result couldn't be written
    busy: tuple[str, ...] = ()  # recording IDs locked by someone else: neither failed nor tried
    causes: Mapping[str, str] = field(default_factory=dict)  # each unwritten ID's class name

    def to_dict(self) -> dict:
        return {
            "titles_checked": self.titles_checked,
            "private_added": self.private_added,
            "kept_out_late": self.kept_out_late,
            "already_seen": self.already_seen,
            "unwritten": list(self.unwritten),
            "busy": list(self.busy),
            "causes": dict(self.causes),
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


@dataclass
class _Sweep:
    checked: int = 0
    private_added: int = 0
    kept_out: int = 0
    seen: int = 0
    unwritten: dict[str, str] = field(default_factory=dict)  # recording ID -> exception class
    busy: list[str] = field(default_factory=list)


def _sweep(
    writer: Writer, db: PlaudDB, label: str, progress: Callable[[], object] | None = None
) -> _Sweep:
    """Reconcile every Plaud recording, however one fails. A recording locked by someone else is
    busy; any other refusal (a busy or full state.db included) lists it with its class name; a
    recording.json that doesn't validate, which `iter_recordings` drops, is listed too, so
    nothing is skipped silently. Logs the ID and the class, never the exception's text."""
    ids = _plaud_recordings(writer)
    sweep = _Sweep()
    for problem in writer.archive.problems:
        folder = problem.path.parent.name
        sweep.unwritten[folder if ID_RE.fullmatch(folder) else "unnamed-folder"] = INVALID_FILE
    for rid in ids:
        if progress is not None:
            progress()
        try:
            report, moved, had = _reconcile(writer, db, rid)
        except LockTimeout:
            log.warning("%s: %s busy", label, rid)
            sweep.busy.append(rid)
            continue
        except RECONCILE_ERRORS as exc:
            log.warning("%s: %s not written (%s)", label, rid, type(exc).__name__)
            sweep.unwritten[rid] = type(exc).__name__
            continue
        sweep.checked += moved
        sweep.private_added += report.private_added is not None
        sweep.kept_out += report.kept_out_word is not None
        sweep.seen += had and not moved
    return sweep


def baseline_privacy(writer: Writer, db: PlaudDB, *, now: datetime) -> BaselineReport:
    """§14.5's privacy part: every Plaud recording's titles checked once, by `reconcile_plaud`."""
    sweep = _sweep(writer, db, "baseline")
    return BaselineReport(
        sweep.checked,
        sweep.private_added,
        sweep.kept_out,
        sweep.seen,
        tuple(sweep.unwritten),
        tuple(sweep.busy),
        sweep.unwritten,
    )


def _stored_words(stored: object) -> list:
    if stored is None:
        return []
    if not isinstance(stored, list):
        raise KeepOutWordsError(NOT_A_LIST)
    return stored


def enforced_words(db: PlaudDB) -> tuple[str, ...]:
    """The keep-out words the worker has enforced, oldest first. Raises KeepOutWordsError when
    the stored row isn't a list."""
    return tuple(w for w in _stored_words(db.get(K_KEEP_OUT_WORDS)) if valid_word(w))


def enforce_word(db: PlaudDB, word: str, *, now: datetime) -> tuple[str, ...]:
    """Record `word` as enforced (the worker, at every start), and return every enforced word.
    Reads and writes in one transaction. Raises KeepOutWordsError, changing nothing, when the
    stored row isn't a list."""

    def add(stored: object) -> object:
        words = _stored_words(stored)
        if valid_word(word) and fold(word) not in {fold(w) for w in words if valid_word(w)}:
            return [*words, word]
        return UNCHANGED

    stored = db.update(K_KEEP_OUT_WORDS, add, now=now)
    return tuple(w for w in _stored_words(stored) if valid_word(w))


def drop_word(db: PlaudDB, word: str, *, configured: str, now: datetime) -> str:
    """`keep-out-words --drop`: one of KEEP_CONFIGURED, NOT_ENFORCED or DROPPED. Reads and writes
    in one transaction. Raises KeepOutWordsError, changing nothing, when the row isn't a list."""
    if fold(word) == fold(configured):
        return KEEP_CONFIGURED
    dropped = False

    def remove(stored: object) -> object:
        nonlocal dropped
        words = _stored_words(stored)
        kept = [w for w in words if not (valid_word(w) and fold(w) == fold(word))]
        dropped = len(kept) != len(words)
        return kept if dropped else UNCHANGED

    db.update(K_KEEP_OUT_WORDS, remove, now=now)
    return DROPPED if dropped else NOT_ENFORCED


@dataclass(frozen=True)
class PatternsPass:
    version: int
    checked: int  # markers moved, as the baseline counts them
    tagged: int
    unwritten: Mapping[str, str] = field(default_factory=dict)  # recording ID -> exception class
    busy: tuple[str, ...] = ()  # locked by someone else: left for the next run

    @property
    def complete(self) -> bool:
        return not self.unwritten and not self.busy

    def sentence(self) -> str:
        titles = "title" if self.checked == 1 else "titles"
        text = (
            f"patterns changed: {self.checked:,} {titles} checked, {self.tagged:,} tagged private"
        )
        if self.unwritten:
            text += f", {len(self.unwritten):,} not written"
        if self.busy:
            text += f", {len(self.busy):,} busy"
        return text


def patterns_pass(
    writer: Writer,
    db: PlaudDB,
    *,
    now: datetime,
    progress: Callable[[], object] | None = None,
) -> PatternsPass | None:
    """§13.1 case 5 over the archive, when the pattern list has a version newer than the last
    complete pass's. The first version needs none: the baseline and each import check every title.
    `progress` is called at every recording (§9.2: the worker passes its heartbeat's bump).

    It records `K_PATTERNS_PASS` as `{"version": the last complete pass's, "attempted": this
    pass's, "at": iso, "checked": int, "tagged": int, "unwritten": [recording IDs], "busy":
    [recording IDs]}`. `version` moves to `attempted` only when nothing was unwritten or busy, so
    the next start runs the pass again; reconcile skips what this one already judged."""
    patterns = current_patterns(db, writer.auto_private, now=now)
    last = db.get(K_PATTERNS_PASS)
    done = last.get("version") if isinstance(last, dict) else None
    done = done if isinstance(done, int) and not isinstance(done, bool) else FIRST_PATTERN_VERSION
    if patterns.version <= done:
        return None
    sweep = _sweep(writer, db, "patterns pass", progress)
    result = PatternsPass(
        patterns.version,
        sweep.checked,
        sweep.private_added,
        dict(sweep.unwritten),
        tuple(sweep.busy),
    )
    db.put(
        K_PATTERNS_PASS,
        {
            "version": result.version if result.complete else done,
            "attempted": result.version,
            "at": now.isoformat(),
            "checked": result.checked,
            "tagged": result.tagged,
            "unwritten": list(result.unwritten),
            "busy": list(result.busy),
        },
        now=now,
    )
    return result
