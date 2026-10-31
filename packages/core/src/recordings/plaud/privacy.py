"""The auto-private marker (stage 2b spec §13): which titles the sync has judged, and against what.

- **The marker,** per recording here and per canonical Plaud ID: a plain SHA-256 of the newest
  checked title, exactly as Plaud gave it (so any change, case included, re-checks), and the version
  of the pattern list it passed. Only recordings in the archive have markers, whose titles
  `recording.json` and the merge-base revisions already hold, so the hash reveals nothing new.
- **Due,** and checked by `judge`:
  - an ID with no marker (case 4: 2a's imports, a hash merge, a lost `state.db`): every pattern and
    every keep-out word run over every title of every snapshot, under every Plaud ID;
  - a marker whose hash isn't the newest title's (case 2): every pattern and word on every title
    after the last one whose hash is the marker's (all of them if none is), since a reconcile
    that stopped part way, or two snapshots between reconciles, leaves more than one new title;
  - a marker of an older pattern version (case 5): only the patterns added since that version, over
    every title of that ID. A removed pattern re-checks nothing.
  Each title is tried as given and folded (`keepout.fold`). Nothing ever removes `private`.
- **A marker moves only once the result is written,** and only when it was due: the Writer records
  `Verdict.markers` after its `mutate` lands, and after the note and any late keep-out, so one
  `state.db` refuses leaves the title due. A marker that wasn't due keeps its version, so a
  pattern removed and later restored is not run again on a title that passed it. One limit: a
  marker moved by a later check takes that version, so a pattern removed and restored after it is
  run once more, erring private.
- **Pattern versions** live in `plaud_state` (`K_PATTERNS`): `{"versions": [{"version", "patterns",
  "at"}, …]}`, oldest first. Whichever process judges records a new version when the configured set
  differs from the newest (2b phase 1 plan, decision 27).

No `sqlite3` at import: `why_phrase` is read by the views, which the Pages demo imports. The
database module is imported where a marker is built or the state read.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from recordings.plaud.keepout import fold, keep_out_match
from recordings.plaud.reconcile import matches_auto_private

if TYPE_CHECKING:
    from recordings.plaud.checks import Marker, PlaudDB

Why = Literal["title changed", "first check", "new pattern", "kept out late"]


def title_sha256(title: str) -> str:
    """SHA-256 of the title's UTF-8 bytes, exactly as Plaud gave them."""
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatternSet:
    version: int
    patterns: tuple[str, ...]
    by_version: Mapping[int, tuple[str, ...]]

    def added_since(self, version: int) -> tuple[str, ...]:
        """The patterns in this set that `version` didn't have; all of them for an unknown one."""
        old = set(self.by_version.get(version, ()))
        return tuple(p for p in self.patterns if p not in old)


def current_patterns(db: PlaudDB, configured: Sequence[str], *, now: datetime) -> PatternSet:
    """The pattern list by version, recording a new version when `configured` differs from the
    newest one as a set (a reordering adds nothing)."""
    from recordings.plaud.checks import K_PATTERNS

    stored = db.get(K_PATTERNS)
    versions = [
        v
        for v in (stored.get("versions", []) if isinstance(stored, dict) else [])
        if isinstance(v, dict) and isinstance(v.get("version"), int)
    ]
    by_version = {v["version"]: tuple(v.get("patterns") or ()) for v in versions}
    newest = max(by_version, default=0)
    patterns = tuple(configured)
    if newest == 0 or set(by_version[newest]) != set(patterns):
        newest += 1
        by_version[newest] = patterns
        versions.append({"version": newest, "patterns": list(patterns), "at": now.isoformat()})
        db.put(K_PATTERNS, {"versions": versions}, now=now)
    return PatternSet(version=newest, patterns=patterns, by_version=by_version)


@dataclass(frozen=True)
class Verdict:
    add_private: bool
    why: Why | None  # set only with add_private
    kept_out_word: str | None  # the first enforced word a checked title matched, private or not
    markers: tuple[Marker, ...]  # every canonical ID's, to record once the result is written
    titles_checked: int


@dataclass(frozen=True)
class PrivacyInputs:
    markers: Mapping[str, Marker]
    patterns: PatternSet
    words: tuple[str, ...]


def _hits(title: str, patterns: Sequence[str]) -> bool:
    return bool(patterns) and matches_auto_private([title, fold(title)], patterns)


def judge(
    titles_by_id: Mapping[str, Sequence[str]],
    markers: Mapping[str, Marker],
    patterns: PatternSet,
    words: Sequence[str],
    *,
    private_now: bool,
) -> Verdict:
    """Which of these titles are due, and whether they make the recording private (§13.1).
    `titles_by_id` maps each canonical Plaud ID to its snapshots' titles in fetch order, newest
    last; `markers` are the recording's; `private_now` whether it is private already."""
    from recordings.plaud.checks import Marker

    checked: set[tuple[str, int]] = set()
    why: Why | None = None
    word: str | None = None

    def check(pid: str, i: int, title: str, run: Sequence[str], words_too: bool, case: Why) -> None:
        nonlocal why, word
        checked.add((pid, i))
        if why is None and _hits(title, run):
            why = case
        if words_too and word is None:
            word = keep_out_match(title, words)

    ids = [pid for pid, titles in titles_by_id.items() if titles]
    moved: set[str] = set()
    if any(pid not in markers for pid in ids):  # case 4: everything, everywhere
        for pid in ids:
            for i, title in enumerate(titles_by_id[pid]):
                check(pid, i, title, patterns.patterns, True, "first check")
        moved = set(ids)
    else:
        for pid in ids:
            titles, marker = titles_by_id[pid], markers[pid]
            hashes = [title_sha256(title) for title in titles]
            if hashes[-1] != marker.title_sha256:  # case 2
                # Every title after the last one the marker names, all of them if none does: a
                # reconcile that stopped part way leaves the marker behind more than one new title.
                named = [i for i, h in enumerate(hashes) if h == marker.title_sha256]
                for i in range(named[-1] + 1 if named else 0, len(titles)):
                    check(pid, i, titles[i], patterns.patterns, True, "title changed")
                moved.add(pid)
            added = patterns.added_since(marker.patterns_version)
            if marker.patterns_version != patterns.version and added:  # case 5
                for i, title in enumerate(titles):
                    check(pid, i, title, added, False, "new pattern")
                moved.add(pid)
    new_markers = tuple(
        Marker(pid, title_sha256(titles_by_id[pid][-1]), patterns.version)
        if pid in moved
        else markers[pid]
        for pid in ids
    )
    if why is None and word is not None:
        why = "kept out late"
    add = why is not None and not private_now
    return Verdict(
        add_private=add,
        why=why if add else None,
        kept_out_word=word,
        markers=new_markers,
        titles_checked=len(checked),
    )


def why_phrase(why: Why) -> str:
    """The fixed reason an automatic `private` records (2b phase 1 plan, decision 15). Phase 2's
    rebuild record adds "the sync's record was rebuilt" for case 4 after a rebuild."""
    if why == "first check":
        return "first check"
    if why == "new pattern":
        return "patterns changed"
    if why == "kept out late":
        return "kept out by title"
    return "title changed"
