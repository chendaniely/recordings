"""Late consent listings and the privacy flags (stage 2b spec §12.1, §12.2, P15).

- **A late consent listing** is an exclusion on the consent list whose recording is already here,
  under any of its Plaud IDs. It is applied from the list and the index alone, with no Plaud call,
  whatever the sign-in, the listing, the schedule or the gate: the recording gets `private`
  (`by: auto`) through `mutate`, and a flag. During a consent stop the exclusions that do read are
  applied too (§12.1): adding `private` is always safe. A flag is cleared only while the list reads
  cleanly, because a line that turned into a typo must never read as "exclude fewer".
- **The index is asked strictly.** One that can't answer now (busy, an I/O error, gone or another
  archive's) is retried at the next tick, and shown (`LateReport.index_unavailable`), never read as
  "nothing here". Recordings whose index update is still pending are read from their files.
- **One recording never stops the others** (§12.1, "Every other recording keeps flowing"). A
  `private` that can't be written (an outside edit, a folder it can't enter, a full disk, a busy
  `state.db`, a file that doesn't decode: `UNWRITTEN`) is reported in `unwritten`, so the worker
  opens its blocker (§16), and the loop goes on; a flag `state.db` refuses is added at the next
  tick. Python reads a folder it can't enter as "no file there", so only `mutate`, which reads
  the file itself, decides that a recording is missing a file, and `gone` (a `stat`, where only
  "not found" counts) decides that its folder is.
- **A `private-unwritten` flag stays open while its `private` is owed** (`still_owed`, which the
  worker asks at every tick): until the recording is private or gone; one the consent list owed,
  also until a list that reads cleanly names none of its Plaud IDs. A stop can't say a line went.
- **An unwritten `private` says what stopped it.** `LateReport.causes` holds each one's exception
  class, which the worker keeps in its `private-unwritten` flag's detail as `"cause"`, and
  `flag_sentence` words the flag by it: an outside edit or an invalid file keeps §13.2's sentence
  (`recordings reindex`, or a fix by hand); any other class (a full disk, a folder's owner, a busy
  `state.db`) gets `PRIVATE_UNWRITTEN_IO`, which points at the disk, since reindex can't help.
- **One flag per recording (P15).** A recording with an open late keep-out flag that becomes
  consent-listed has that flag changed, never a second one. A recording gone from Plaud has no
  privacy flag of its own (it is listed under Gone from Plaud), so it gets this one, and only it.
  `plaud_events`' partial unique index enforces it across processes.
- **Keep it here** resolves a late keep-out's flag and changes no archive file. The recording stays
  `private`, nothing more is fetched from Plaud for it (`plaud_listing.kept_out_at` stays), and
  `plaud_checks.kept_here_at` keeps a later title with the word from flagging it again (2b phase 1
  plan, decision 16).
- **No title,** ever: flags and events carry IDs and fixed phrases only.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from recordings.archive import Archive
from recordings.index import Index
from recordings.locks import LockTimeout
from recordings.models import Recording, is_private
from recordings.plaud.checks import (
    CONSENT_LATE,
    FLAG_KINDS,
    KEPT_HERE_EVENT,
    KEPT_OUT_LATE,
    PRIVATE_UNWRITTEN,
    Event,
    PlaudDB,
)
from recordings.plaud.consent import LEGACY_ENTRY_RE
from recordings.plaud.keepout import ConsentList
from recordings.refs import canonical_plaud_id
from recordings.state import State
from recordings.writer import InvalidFile, NotTheWriter, SetPlaudFields, StaleFile, Writer

SENTENCES: dict[str, str] = {
    CONSENT_LATE: "On the consent list, but in the archive: take it out by hand (runbook)",
    KEPT_OUT_LATE: "Kept out by title, but already in the archive: take it out by hand (runbook)",
    PRIVATE_UNWRITTEN: (
        "Should be private, but its file was edited outside the app: run `recordings reindex`"
    ),
}
# A private-unwritten flag whose cause (its detail's "cause": the exception's class name) is
# neither an outside edit nor an invalid file: reindex can't mend a full disk or a folder's owner.
PRIVATE_UNWRITTEN_IO = (
    "Recording {id} should be private, but its file couldn't be written ({cause}): check the "
    "archive disk and its permissions."
)
# A recording.json that is missing (its folder is still here): reindex or a backup mends it.
PRIVATE_UNWRITTEN_MISSING = (
    "Recording {id} should be private, but its recording.json is missing: restore it from a "
    "backup, or run `recordings reindex`."
)
EDITED_CAUSES: frozenset[str] = frozenset({"StaleFile", "InvalidFile"})
KEPT_HERE = "Kept here: it stays private, and nothing more is fetched from Plaud for it"
NOTHING_TO_KEEP = "Nothing to keep: there is no open late keep-out for it"
# What writing one recording's `private` can raise that leaves it owed: the Writer's refusals, the
# file system's (a folder's owner, a full or read-only disk), state.db's, and a file that doesn't
# decode (UnicodeDecodeError is a ValueError).
UNWRITTEN = (StaleFile, InvalidFile, NotTheWriter, OSError, sqlite3.Error, ValueError)


@dataclass(frozen=True)
class LateReport:
    tagged: tuple[str, ...]  # recording IDs that got `private`
    flagged: tuple[str, ...]  # newly flagged, or changed to consent-late
    cleared: tuple[str, ...]  # consent-late flags cleared because the line went (`private` stays)
    unwritten: tuple[str, ...]  # a `private` owed but not written: the blocker opens (§13.2)
    busy: tuple[str, ...]  # LockTimeout: retried at the next tick
    index_unavailable: bool  # the strict lookup couldn't answer: retried at the next tick, shown
    # Each `unwritten` ID's exception class name: the worker keeps it in that recording's
    # private-unwritten flag as detail "cause", and flag_sentence words the flag by it.
    causes: Mapping[str, str] = field(default_factory=dict)


def flag_sentence(kind: str, recording_id: str, cause: str | None = None) -> str:
    """A flag's fixed sentence, as Status and the views show it. A private-unwritten flag whose
    cause is set and isn't an outside edit or an invalid file says its file couldn't be written,
    naming the class; anything but a class name in the detail reads as "unknown", so no other text
    is ever shown. A flag with no cause recorded keeps §13.2's sentence."""
    if kind == PRIVATE_UNWRITTEN and cause == "FileNotFoundError":
        return PRIVATE_UNWRITTEN_MISSING.format(id=recording_id)
    if kind == PRIVATE_UNWRITTEN and cause is not None and cause not in EDITED_CAUSES:
        shown = cause if cause.isidentifier() and len(cause) <= 64 else "unknown"
        return PRIVATE_UNWRITTEN_IO.format(id=recording_id, cause=shown)
    return SENTENCES[kind]


def flag(db: PlaudDB, kind: str, recording_id: str, *, plaud_id: str | None, now: datetime) -> bool:
    """Open `kind`'s flag on a recording, keeping one open privacy flag per recording (P15): an
    open late keep-out becomes consent-late, and a consent-late flag is never turned back. True
    when a flag was added or changed."""
    if kind not in FLAG_KINDS:
        raise ValueError(f"{kind!r} is not a privacy flag")
    pid = canonical_plaud_id(plaud_id) if plaud_id else None
    for _ in range(2):  # a second pass only after another process flagged it in between
        current = db.open_flags(recording_id=recording_id)
        if current:
            if current[0].kind == kind or kind == KEPT_OUT_LATE:
                return False
            db.change_kind(current[0].id, kind)
            return True
        try:
            db.add_event(kind, now=now, recording_id=recording_id, plaud_id=pid, open_flag=True)
        except sqlite3.IntegrityError:
            continue
        return True
    return False


def gone(path) -> bool:
    """Whether a folder is removed. Only "not found" says so: `Path.exists()` reads any OSError
    (a parent it can't enter, an I/O error) as "no", which would drop a recording still owed.
    The late consent pass and the worker's `private-unwritten` flags (`still_owed`) both ask it."""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def listed_here(
    index: Index, uuid: str, state: State | None, archive: Archive, ids: frozenset[str]
) -> tuple[list[tuple[str, str]], bool]:
    """Each recording here that one of `ids` names, with that ID: from the index, asked strictly,
    and from the files of recordings whose index update is pending. (recordings, unavailable).
    The worker's pass and `plaud consent` both ask it, so they see the same recordings. A pending
    recording whose file can't be read (and whose folder is still there) makes the answer
    unavailable: it might be one of them."""
    if not ids:
        return [], False
    if index.archive_uuid() != uuid:
        return [], True  # gone, unreadable or another archive's: it would answer "none"
    hits: dict[str, str] = {}
    try:
        for pid in sorted(ids):
            for rid in index.find_by_source("plaud", pid, strict=True):
                hits.setdefault(rid, pid)
        pending = state.index_pending() if state is not None else []
    except sqlite3.DatabaseError:
        return [], True
    unavailable = False
    for rid in pending:
        found = _plaud_ids(archive, rid)
        if found is None:
            unavailable = unavailable or not gone(archive.path_for(rid))
            continue
        for pid in sorted(found & ids):
            hits.setdefault(rid, pid)
            break
    return sorted(hits.items()), unavailable


def _ids_of(rec: Recording) -> frozenset[str]:
    return frozenset(canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud")


def _plaud_ids(archive: Archive, recording_id: str) -> frozenset[str] | None:
    """The recording's canonical Plaud IDs, or None when its file can't be read now."""
    try:
        rec = archive.load(recording_id)
    except (KeyError, OSError, ValueError):
        return None
    return _ids_of(rec)


def still_owed(archive: Archive, event: Event, consent: ConsentList | None) -> bool:
    """Whether a `private-unwritten` flag's `private` is still owed (§16): until its recording is
    private or gone. No file it can read is "gone" only when its folder is (`gone`: a folder it
    can't enter, or one under a parent it can't enter, hides a file that is there); a file it
    can't read or that doesn't validate keeps it owed. One the consent list owed (detail
    `"owed": "consent"`) is no longer owed once `consent`, the list in force, reads cleanly and
    names none of the recording's Plaud IDs: a line added by mistake, then taken out. A stop, or
    no list read yet, can't say a line went."""
    rid = event.recording_id
    if rid is None:
        return False
    try:
        rec = archive.load(rid)
    except KeyError:
        return not gone(archive.path_for(rid))
    except (ValueError, OSError):
        return True
    if is_private(rec):
        return False
    if event.detail.get("owed") != "consent" or consent is None or not consent.readable:
        return True
    return bool(_ids_of(rec) & consent.ids)


def _kept_here(db: PlaudDB, recording_id: str) -> bool:
    check = db.check_row(recording_id)
    return check is not None and check.kept_here_at is not None


def _kept_out_late_owed(db: PlaudDB, recording_id: str, ids: Iterable[str]) -> bool:
    if _kept_here(db, recording_id):
        return False
    for pid in ids:
        row = db.listing(pid)
        if row is not None and row.kept_out_at is not None:
            return True
    return False


def apply_late_consent(
    writer: Writer, db: PlaudDB, consent: ConsentList, *, now: datetime
) -> LateReport:
    """Tag and flag every recording here that an exclusion names; clear the flags whose line went.
    No Plaud call. Safe to run at every tick: a recording already `private` is only flagged. One
    recording that can't be written is reported in `unwritten`, its class in `causes`, and stops
    none after it."""
    tagged, flagged, unwritten, busy, cleared = [], [], [], [], []
    causes: dict[str, str] = {}
    found, unavailable = listed_here(
        writer.index, writer.identity.uuid, writer.state, writer.archive, consent.ids
    )
    for rid, pid in found:
        try:
            private = is_private(writer.archive.load(rid))
        except (KeyError, OSError, ValueError):
            # Gone, or a file it can't read or decode now. A folder it can't enter reads as "no
            # file" here too, so `mutate`, which reads the file itself, tells them apart.
            private = False
        if not private:
            try:
                writer.mutate(rid, SetPlaudFields(add_private=True))
            except KeyError:
                if gone(writer.archive.path_for(rid)):
                    continue  # removed since the index answered
                # The folder is still here without its recording.json: owed, never skipped.
                unwritten.append(rid)
                causes[rid] = FileNotFoundError.__name__
            except LockTimeout:
                busy.append(rid)  # retried at the next tick, flag and all
                continue
            except UNWRITTEN as exc:
                # Not logged: the pass runs every tick, and the report carries the ID and class.
                unwritten.append(rid)
                causes[rid] = type(exc).__name__  # a class name only: never the exception's text
            else:
                tagged.append(rid)
        try:
            if flag(db, CONSENT_LATE, rid, plaud_id=pid, now=now):
                flagged.append(rid)
        except sqlite3.Error:  # flag() reads the open flags afresh: the next tick adds it
            pass
    if consent.readable and not unavailable:
        for event in db.open_flags((CONSENT_LATE,)):
            rid = event.recording_id
            ids = _plaud_ids(writer.archive, rid) if rid is not None else None
            if ids is None or ids & consent.ids:
                continue  # still listed, or its file can't be read now: the flag stays
            if _kept_out_late_owed(db, rid, ids):
                db.change_kind(event.id, KEPT_OUT_LATE)  # its title kept it out too
            else:
                db.resolve(event.id, now=now)
            cleared.append(rid)
    return LateReport(
        tagged=tuple(tagged),
        flagged=tuple(flagged),
        cleared=tuple(cleared),
        unwritten=tuple(unwritten),
        busy=tuple(busy),
        index_unavailable=unavailable,
        causes=causes,
    )


def record_late_keep_out(
    db: PlaudDB, recording_id: str, plaud_ids: Iterable[str], word: str, *, now: datetime
) -> bool:
    """A recording here whose title has a keep-out word (§12.2, "Too late"): each of its Plaud IDs
    is kept out from now on (never fetched again), and the recording is flagged once, unless Dan
    pressed Keep it here. The caller has written `private`. True when a flag was added or changed.

    An ID no listing has shown yet (a 2a recording's, before Phase 2 lists) gets its
    `plaud_listing` row from `set_listing`, as the archive holds it and dated by the keep-out, so
    it never looks listed when no listing ran (2b phase 1 plan, decision 36)."""
    given: dict[str, str] = {}
    for pid in plaud_ids:
        given.setdefault(canonical_plaud_id(pid), pid)
    for canonical, as_given in sorted(given.items()):
        row = db.listing(canonical)
        if row is None or row.kept_out_at is None:
            db.set_listing(as_given, kept_out_at=now, kept_out_word=word)
    if _kept_here(db, recording_id):
        return False
    first = min(given) if given else None
    return flag(db, KEPT_OUT_LATE, recording_id, plaud_id=first, now=now)


def keep_here(db: PlaudDB, recording_id: str, *, now: datetime) -> str:
    """Keep it here (request `keep-here`): resolve the open late keep-out flag. Changes no archive
    file; `private` stays and nothing more is fetched. KEPT_HERE, or NOTHING_TO_KEEP."""
    open_flags = db.open_flags((KEPT_OUT_LATE,), recording_id=recording_id)
    if not open_flags:
        return NOTHING_TO_KEEP
    # kept_here_at first: a crash after it leaves a flag Dan can press again, never one that comes
    # back with the next title.
    db.set_check(recording_id, kept_here_at=now)
    db.add_event(
        KEPT_HERE_EVENT,
        now=now,
        recording_id=recording_id,
        plaud_id=open_flags[0].canonical_plaud_id,
    )
    for event in open_flags:
        db.resolve(event.id, now=now)
    return KEPT_HERE


def missed_by_2a(text: str, consent: ConsentList) -> frozenset[str]:
    """The exclusions stage 2a's parser didn't read (an `of_` or upper-case ID, §12.1): read here
    exactly as `load_consent_list` read them then."""
    legacy = {
        canonical_plaud_id(m.group(1))
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
        for m in [LEGACY_ENTRY_RE.match(line)]
        if m
    }
    return frozenset(consent.ids - legacy)
