"""Privacy from the listing alone (stage 2b spec §13.1 cases 2 and 3, §12.2's "Too late"): every
run with a listing, complete or partial, checks the listed title of every recording here, whatever
else is paused (auto-import off, a hold, the breaker, a consent stop). Only no listing at all
delays it. A5's run calls `privacy_from_listing` right after the listing (§11.1's "privacy from the
listing alone"); the import never needs it, since it judges every title it has seen itself.

- **Due** when the listed title's hash isn't the recording's marker for that Plaud ID (§13.2),
  never `plaud_listing`'s hash: decision 3. A marker the listing already moved is never due again,
  so a `private` Dan removed stays removed until the title changes again, unless a keep-out word
  added since catches it (next).
- **A keep-out word added later** (§13.1 case 3; Dan's answer to question 5, Phase 1's Q&A §9): a
  judged title is due again when it starts with a word not yet applied to the recordings here
  (`K_WORDS_APPLIED`, folded) and with none that was, and takes the path below, why "kept out
  late". With no record yet, the words in force are seeded and nothing is re-checked. New words are
  marked applied by `mark_words_applied`, only after a good listing that left nothing busy or
  unwritten, so a failed tag is tried again and a recording a held or partial listing didn't show
  isn't skipped. The set only grows, so dropping a word and adding it back re-tags nothing Dan has
  dealt with. A recording Plaud no longer lists isn't checked. No extra Plaud call.
- **Checked** against `auto_private_patterns` (as given and folded, with Plaud's "MM-DD " prefix
  and without) and every keep-out word enforced. A match adds `private` through `mutate` at once,
  from the listing alone, so a fetch that keeps failing can't leave a private title untagged
  (decision 1). If the recording wasn't private it gets the note "re-tagged private: title
  changed" (`retagged-private`, why "title changed"; "kept out by title" when only a word matched;
  "first check" when the recording had no marker for that ID).
- **A keep-out word** on a recording here is a late keep-out (§12.2, decision 2): `private` as
  above, each of its Plaud IDs kept out from now on (never fetched again), and one flag per
  recording (P15), unless Dan pressed Keep it here (`flags.record_late_keep_out`).
- **The marker moves only once the result is written** (§13.2): no match, or `private`
  committed. Its pattern version and judged snapshots stay as they were (an older version still
  gets case 5's pass; a new marker starts at the current version with nothing judged, so
  reconcile still checks every title its snapshots carry). A `mutate` that fails leaves the title
  due: a lock timeout is "busy" and retried at the next run; anything else is reported
  (`unwritten`, with its class in `causes`) and opens the `private-unwritten` flag at once, which
  holds the sync's ping until the tag lands (§16), or until a later listed title, checked,
  matches nothing: then no `private` is owed, and the flag resolves.

No title is stored, logged or returned: hashes go to the marker, recording IDs and classes to the
report. A consent-listed recording here is the late consent pass's (`flags.apply_late_consent`);
checking its title here too only ever adds `private`.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from recordings.locks import LockTimeout
from recordings.models import fold, is_private
from recordings.plaud.checks import K_WORDS_APPLIED, PRIVATE_UNWRITTEN, Marker, PlaudDB
from recordings.plaud.client import Listed
from recordings.plaud.compare import Here
from recordings.plaud.flags import gone, record_late_keep_out
from recordings.plaud.keepout import keep_out_match
from recordings.plaud.privacy import (
    Why,
    current_patterns,
    rebuilt_why,
    title_sha256,
    why_phrase,
)
from recordings.plaud.reconcile import matches_auto_private
from recordings.writer import RECONCILE_ERRORS, SetPlaudFields, Writer

log = logging.getLogger(__name__)
OWED_TITLE = "title"  # a private-unwritten flag's `owed`: the listing's title owes it


@dataclass(frozen=True)
class ListingPrivacy:
    checked: int = 0  # listed titles that were due, and checked
    tagged: tuple[str, ...] = ()  # recordings that got `private` now
    kept_out_late: tuple[str, ...] = ()  # recordings a keep-out word caught now
    flagged: tuple[str, ...] = ()  # of those, newly flagged (or a flag changed)
    unwritten: tuple[str, ...] = ()  # a `private` owed and not written: flagged, the blocker opens
    busy: tuple[str, ...] = ()  # LockTimeout: the title stays due, checked at the next run
    causes: Mapping[str, str] = field(default_factory=dict)  # unwritten ID -> exception class
    new_words: tuple[str, ...] = ()  # keep-out words not yet applied: `mark_words_applied`'s


def privacy_from_listing(
    writer: Writer,
    db: PlaudDB,
    rows: Sequence[Listed],
    here: Here,
    *,
    now: datetime,
) -> ListingPrivacy:
    """§13.1 cases 2 and 3 over a listing's rows (the module's notes). `here` is the archive's
    Plaud recordings (`compare.here_from_archive`). Raises KeepOutWordsError when the enforced
    words can't be read (nothing is judged, and no marker moves), and a state.db error as itself:
    the run records it, and the titles stay due."""
    words = writer.keep_out_words()
    patterns = current_patterns(db, writer.auto_private, now=now)
    checked = 0
    tagged: list[str] = []
    late: list[str] = []
    flagged: list[str] = []
    unwritten: list[str] = []
    busy: list[str] = []
    causes: dict[str, str] = {}
    applied = _words_applied(db, words, now=now)
    new_words = tuple(w for w in words if fold(w) not in applied)
    for listed in rows:
        pid = listed.canonical_id
        title = listed.title
        if title is None or pid not in here.by_id:
            continue
        for rid in sorted(here.by_id[pid]):
            marker = db.markers(rid).get(pid)
            digest = title_sha256(title)
            same_title = marker is not None and marker.title_sha256 == digest
            if same_title and not _newly_caught(title, new_words, applied):
                continue  # judged: decision 3
            checked += 1
            private_match = matches_auto_private([title], patterns.patterns)
            word = keep_out_match(title, words)
            why: Why = "title changed" if marker is not None else "first check"
            if word is not None and (same_title or not private_match):
                why = "kept out late"
            if private_match or word is not None:
                outcome = _add_private(writer, db, rid, rebuilt_why(why, db), now=now)
                if outcome == "busy":
                    busy.append(rid)
                    continue
                if outcome is not None and outcome not in ("tagged", "private"):
                    unwritten.append(rid)
                    causes[rid] = outcome
                    _flag_unwritten(db, rid, outcome, now=now)
                    continue
                if outcome == "tagged":
                    tagged.append(rid)
                if word is not None:
                    late.append(rid)
                    ids = _plaud_ids(writer, rid) or [listed.listed_id]
                    if record_late_keep_out(db, rid, ids, word, now=now):
                        flagged.append(rid)
            else:
                _owed_no_more(db, rid, now=now)  # a later title that doesn't match: none owed
            moved = Marker(
                pid,
                digest,
                marker.patterns_version if marker is not None else patterns.version,
                marker.judged if marker is not None else (),
            )
            db.set_markers(rid, [moved], now=now)
    report = ListingPrivacy(
        checked=checked,
        tagged=tuple(tagged),
        kept_out_late=tuple(late),
        flagged=tuple(flagged),
        unwritten=tuple(unwritten),
        busy=tuple(busy),
        causes=causes,
        new_words=new_words,
    )
    if tagged or late or unwritten:
        log.info(
            "privacy from the listing: %d titles checked, %d tagged private, %d kept out late, "
            "%d not written",
            checked,
            len(tagged),
            len(late),
            len(unwritten),
        )
    return report


def _words_applied(db: PlaudDB, words: Sequence[str], *, now: datetime) -> frozenset[str]:
    """The folded keep-out words already applied to the recordings here. With no record yet, the
    words in force are seeded and nothing is re-checked: a title with no marker is judged against
    every word anyway. A record that isn't a list reads as no word applied, which can only add
    `private`."""
    stored = db.get(K_WORDS_APPLIED)
    if stored is None:
        seeded = sorted({fold(w) for w in words})
        db.put(K_WORDS_APPLIED, seeded, now=now)
        return frozenset(seeded)
    if not isinstance(stored, list):
        return frozenset()
    return frozenset(w for w in stored if isinstance(w, str))


def _newly_caught(title: str, new_words: Sequence[str], applied: frozenset[str]) -> bool:
    """Whether a title the marker has judged starts with a word not yet applied. One an applied
    word already caught isn't news: Dan has had that flag, and decided."""
    return (
        bool(new_words)
        and keep_out_match(title, new_words) is not None
        and keep_out_match(title, applied) is None
    )


def _plaud_ids(writer: Writer, recording_id: str) -> list[str]:
    try:
        rec = writer.archive.load(recording_id)
    except (KeyError, OSError, ValueError):
        return []
    return [s.ref for s in rec.sources if s.kind == "plaud"]


def _add_private(writer: Writer, db: PlaudDB, recording_id: str, why: Why, *, now: datetime):
    """`private` through `mutate`, and its note. "tagged" when added now, "private" when it
    already was, "busy" for a lock timeout, None when its folder is gone, else the class of what
    stopped it (the `private` is still owed)."""
    try:
        if is_private(writer.archive.load(recording_id)):
            return "private"
    except KeyError:
        if gone(writer.archive.path_for(recording_id)):
            return None
    except (OSError, ValueError):
        pass  # `mutate` reads the file itself, and says what stops it
    try:
        writer.mutate(recording_id, SetPlaudFields(add_private=True))
    except LockTimeout:
        return "busy"
    except KeyError:
        if gone(writer.archive.path_for(recording_id)):
            return None
        return FileNotFoundError.__name__
    except RECONCILE_ERRORS as exc:
        return type(exc).__name__
    db.add_event(
        "retagged-private", now=now, recording_id=recording_id, detail={"why": why_phrase(why)}
    )
    return "tagged"


def _owed_no_more(db: PlaudDB, recording_id: str, *, now: datetime) -> None:
    """The listing's own `private-unwritten` flag (detail `owed: title`) resolves once the
    recording's newest listed title, checked, matches nothing: the `private` it owed is owed no
    more (Dan renamed it back before the tag could land). Without this, the flag would hold the
    sync's ping until the recording were private, which nothing would make it."""
    for event in db.open_flags((PRIVATE_UNWRITTEN,), recording_id=recording_id):
        if event.detail.get("owed") == OWED_TITLE:
            db.resolve(event.id, now=now)


def _flag_unwritten(db: PlaudDB, recording_id: str, cause: str, *, now: datetime) -> None:
    """The `private-unwritten` flag (§13.2), at once: Needs attention names the recording, and
    the worker's tick keeps the blocker open while it is owed (`flags.still_owed`)."""
    if db.open_flags((PRIVATE_UNWRITTEN,), recording_id=recording_id):
        return
    with contextlib.suppress(sqlite3.IntegrityError):  # another writer just added it
        db.add_event(
            PRIVATE_UNWRITTEN,
            now=now,
            recording_id=recording_id,
            detail={"owed": OWED_TITLE, "cause": cause},
            open_flag=True,
        )


def mark_words_applied(db: PlaudDB, report: ListingPrivacy, *, now: datetime) -> None:
    """The words this pass caught up on are applied, unless a `private` is still owed (busy or
    unwritten): those are tried again at the next run. The sync calls it once its compare says the
    listing was good, so a recording a held or partial listing didn't show isn't skipped for good.
    The set only grows."""
    if not report.new_words or report.busy or report.unwritten:
        return

    def grow(stored: object) -> list[str]:
        held = {w for w in stored if isinstance(w, str)} if isinstance(stored, list) else set()
        return sorted(held | {fold(w) for w in report.new_words})

    db.update(K_WORDS_APPLIED, grow, now=now)


__all__ = ["OWED_TITLE", "ListingPrivacy", "mark_words_applied", "privacy_from_listing"]
