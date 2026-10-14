"""The locked write path (spec §6.4): the only code that changes an archive.

A Writer refuses to exist unless the archive's sentinel, this machine's state.db and the configured
writer ID all agree (§3, §6.7). Every change to a recording.json is an operation applied by
`mutate` under that recording's lock. `Locks` re-entrancy is per thread, so Writer methods are
synchronous: never `await` inside `hold()`, or other tasks on the thread share the lock. It checks
the file's content hash against the revision the app last wrote (a merge base in state.db), applies the operation, bumps `rev`, and writes the file
durably. A file edited outside the app is refused until `recordings reindex` accepts the edit
(three-way merges arrive in stage 3). New recordings are assembled under `.tmp/` and renamed into
place whole. Identical bytes arriving again merge into the existing recording, keeping the second
copy's sources, outputs, tags and notes. A private tag always lands before the content it
protects (§7.4).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from recordings.archive import Archive, RawSource, sha256_file, utc_stamp
from recordings.config import Config, ConfigError
from recordings.files import (
    copy_file_synced,
    fsync_dir,
    publish_exclusive,
    write_bytes_atomic,
    write_text_atomic,
)
from recordings.ids import ID_RE, make_id, relative_dir
from recordings.index import Index, IndexRefused
from recordings.locks import Locks, LockTimeout
from recordings.models import (
    SCHEMA_REF,
    MediaInfo,
    PlaudState,
    Recording,
    Rendition,
    SourceRef,
    TagRef,
    TimeSource,
    TitleBy,
    dump_json,
    is_private,
    is_private_tag,
)
from recordings.plaud.reconcile import (
    DEFAULT_AUTO_PRIVATE,
    Held,
    Snapshot,
    patterns_from_config,
    reconcile,
)
from recordings.refs import canonical_ref
from recordings.selfdoc import write_docs
from recordings.sentinel import MIRROR, SentinelError, is_mirror, read_sentinel
from recordings.state import State, StateError, unreadable

INDEX_LOCK = "index"
DOCS_LOCK = "docs"
_UNSAFE = re.compile(r"[^A-Za-z0-9.@+_-]+")
log = logging.getLogger(__name__)


class WriterError(RuntimeError):
    """Every refusal to write."""


class NotTheWriter(WriterError):
    """This process may not write this archive (§3, §6.7)."""


class StaleFile(WriterError):
    """The file changed outside the app since the app last wrote it."""


class InvalidFile(WriterError):
    """The file on disk doesn't validate, so nothing is applied to it (§11)."""


class MissingRecordings(IndexRefused):
    """state.db knows recordings the archive no longer lists: it is half mounted or restored."""


class IndexUnavailable(WriterError):
    """The index can't answer now (busy past its timeout, an I/O error), so nothing was added:
    "none" from it would make a second recording of bytes the archive holds (§6.4)."""


class Op(Protocol):
    name: str

    def apply(self, rec: Recording) -> Recording: ...


@dataclass(frozen=True)
class AddTags:
    tags: tuple[TagRef, ...]
    name: str = "add-tags"

    def apply(self, rec: Recording) -> Recording:
        tags, have = list(rec.tags), {t.tag for t in rec.tags}
        for tag in self.tags:
            if tag.tag not in have:
                tags.append(tag)
                have.add(tag.tag)
        return rec.model_copy(update={"tags": tags})


@dataclass(frozen=True)
class MergeIncoming:
    """The duplicate merge (§6.4): the second copy's sources, tags and excluded note types join
    the first. Its outputs and notes are written beside the first before this op runs."""

    sources: tuple[SourceRef, ...]
    tags: tuple[TagRef, ...] = ()
    excluded_note_types: tuple[str, ...] = ()
    name: str = "merge-duplicate"

    def apply(self, rec: Recording) -> Recording:
        rec = AddTags(self.tags).apply(rec)
        excluded = list(rec.excluded_note_types)
        excluded += [n for n in dict.fromkeys(self.excluded_note_types) if n not in excluded]
        return rec.model_copy(update={"sources": [*rec.sources, *self.sources],
                                      "excluded_note_types": excluded})

@dataclass(frozen=True)
class SetPlaudFields:
    """The fields Plaud owns (§9.1.1): the title while title_by is plaud or absent, the title
    last seen, and an auto-private tag, which is only ever added."""

    title: str | None = None
    title_seen: str | None = None
    add_private: bool = False
    name: str = "plaud-fields"

    def apply(self, rec: Recording) -> Recording:
        update: dict = {}
        if self.title is not None:
            update |= {"title": self.title, "title_by": "plaud"}
        if self.title_seen is not None:
            plaud = rec.plaud or PlaudState()
            update["plaud"] = plaud.model_copy(update={"title_seen": self.title_seen})
        rec = rec.model_copy(update=update)
        if self.add_private and not is_private(rec):
            rec = AddTags((TagRef(tag="private", by="auto"),)).apply(rec)
        return rec


@dataclass(frozen=True)
class ReconcileReport:
    recording_id: str
    written: int
    held: tuple[Held, ...]
    skipped: tuple[str, ...]
    title_difference: bool  # whether Plaud's title differs from yours; never the title itself
    problem: str | None = None  # a fixed phrase: never exception text, which can carry a title

    def to_dict(self) -> dict:
        return {"recording_id": self.recording_id, "written": self.written,
                "held": [{"snapshot": h.snapshot, "slot": h.slot, "reason": h.reason}
                         for h in self.held],
                "skipped": list(self.skipped), "title_difference": self.title_difference,
                "problem": self.problem}


@dataclass(frozen=True)
class Incoming:
    """One recording, as a source hands it to the writer (spec §9.0)."""

    media: Path
    recorded_at: datetime
    timezone_name: str
    time_source: TimeSource
    title: str
    kind: Literal["audio", "video"]
    sources: tuple[RawSource, ...]
    title_by: TitleBy | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    # A private tag goes here, never in a later write, so the recording is private from the
    # moment it exists (§7.4).
    tags: tuple[TagRef, ...] = ()
    renditions: tuple[Rendition, ...] = ()
    my_notes: str | None = None
    excluded_note_types: tuple[str, ...] = ()
    sha256: str | None = None  # what the caller hashed (the import's plan); add refuses others


@dataclass(frozen=True)
class Added:
    recording: Recording
    created: bool  # False: identical bytes were already there, and this merged into them
    sources_added: int
    renditions_added: int


@dataclass(frozen=True)
class ReindexReport:
    recordings: int
    adopted: tuple[str, ...]  # outside edits accepted as the new merge base
    invalid: tuple[str, ...]  # files that don't validate, flagged in state.db
    problems: tuple[dict, ...]
    forgotten: tuple[str, ...] = ()  # --allow-missing: recordings state.db no longer remembers
    reconciled: tuple[ReconcileReport, ...] = ()

    def to_dict(self) -> dict:
        return {"recordings": self.recordings, "adopted": list(self.adopted),
                "invalid": list(self.invalid), "problems": list(self.problems),
                "forgotten": list(self.forgotten),
                "plaud": {"written": sum(r.written for r in self.reconciled),
                          "held": sum(len(r.held) for r in self.reconciled),
                          "skipped": sum(len(r.skipped) for r in self.reconciled),
                          "problems": sum(r.problem is not None for r in self.reconciled)}}


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value).strip("_") or "x"


def rendition_filename(rendition: Rendition) -> str:
    """`<kind>-<engine>-<version>-<UTC stamp>.json`; notes' kind part is `notes-<note type>`."""
    kind = f"notes-{rendition.note_type}" if rendition.kind == "notes" else rendition.kind
    parts = [_slug(kind), _slug(rendition.engine), _slug(rendition.version),
             utc_stamp(rendition.created_at)]
    return "-".join(parts) + ".json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rev_of(raw: bytes) -> int:
    with contextlib.suppress(ValueError, AttributeError, TypeError):
        value = json.loads(raw).get("rev")
        if isinstance(value, int) and value >= 0:
            return value
    return 0


class Writer:
    def __init__(self, root: Path, state: State, index: Index, *, writer_id: str,
                 rebuild: bool = True,
                 auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> None:
        self.auto_private = tuple(auto_private)
        self.root = Path(root)
        try:
            self.identity = read_sentinel(self.root)
        except SentinelError as exc:
            raise NotTheWriter(str(exc)) from None
        if is_mirror(self.root):  # §3: the mirror's copy carries archive.json, but is never written
            raise NotTheWriter(f"{self.root} holds {MIRROR}: it is the NAS mirror's copy, not the "
                               "archive. Only the archive's writer writes (spec §3).")
        try:
            archive_uuid, recorded = state.meta("archive_uuid"), state.meta("writer_id")
        except StateError as exc:
            raise NotTheWriter(str(exc)) from None
        except sqlite3.DatabaseError as exc:
            raise NotTheWriter(unreadable(state.db_path, exc)) from None
        if archive_uuid != self.identity.uuid:
            raise NotTheWriter(
                f"{state.db_path} belongs to another archive: the archive's UUID changed. "
                "Refusing to write.")
        if recorded != writer_id:
            raise NotTheWriter(
                f"this process's writer_id {writer_id!r} is not the archive's writer "
                f"({recorded!r}); only the writer writes (spec §3)")
        self.state, self.index, self.writer_id = state, index, writer_id
        self.archive = Archive(self.root)  # its own reader, never the UI's
        self.locks = Locks(state.locks_dir)
        self._sweep_tmp()
        foreign = index.archive_uuid() != self.identity.uuid  # new, damaged or another archive's
        # rebuild=False is for `reindex`, which rebuilds on its own terms (--allow-missing).
        if rebuild and (foreign or state.index_pending()):
            try:
                # §6.7: only an index that isn't this archive's may start out empty.
                self.rebuild_index(allow_empty=foreign)
            except IndexRefused as exc:
                hint = "" if foreign or isinstance(exc, MissingRecordings) else (
                    f" A writer can't start with --allow-empty: if the archive really is empty, "
                    f"delete {index.path} (it is derived) and start again.")
                raise NotTheWriter(f"{exc}{hint}") from None
            except (sqlite3.DatabaseError, OSError) as exc:  # a mode, an owner, a full disk
                code = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
                raise NotTheWriter(
                    f"rebuilding the index at {index.path} failed ({code}). Check that the archive "
                    "and the index's folder are mounted and belong to this user; the index is "
                    "derived, so an empty folder for it will do.") from None

    @classmethod
    def open(cls, cfg: Config, *, docs: bool = True, rebuild: bool = True) -> Writer:
        missing = [name for name, value in (("[archive] path", cfg.archive_path),
                                            ("[state] path", cfg.state_path),
                                            ("[index] path", cfg.index_path),
                                            ("[archive] writer_id", cfg.writer_id))
                   if value is None]
        if missing:
            raise NotTheWriter("set " + ", ".join(missing) + " in config.toml to write the archive")
        try:
            state = State.open(cfg.state_path)
        except StateError as exc:
            raise NotTheWriter(str(exc)) from None
        try:
            patterns = patterns_from_config(cfg.data)
        except ConfigError as exc:  # a bad regex must not crash the CLI or stop the web app
            raise NotTheWriter(str(exc)) from None
        writer = cls(cfg.archive_path, state, Index(cfg.index_path), writer_id=cfg.writer_id,
                     rebuild=rebuild, auto_private=patterns)
        if docs:
            writer.write_docs()  # §6.7: at startup, and only into an archive with its sentinel
        return writer

    # ---- docs and the index ----------------------------------------------------------------
    def write_docs(self) -> list[str]:
        with self.locks.hold(DOCS_LOCK):
            return write_docs(self.root)

    def _missing(self) -> list[str]:
        """The recording.json files state.db committed that are no longer in the archive. Only a
        file that is not there counts: one that can't be read (a folder without permission, a
        half-mounted disk) is refused, even with --allow-missing, never forgotten."""
        gone, unreadable = [], []
        for f in self.state.committed_files():
            try:
                os.stat(self.root / f)
            except FileNotFoundError:
                gone.append(f)
            except OSError:
                unreadable.append(f)
        if unreadable:
            raise MissingRecordings(
                f"{len(unreadable)} recording file(s) state.db knows can't be read "
                f"({self._shown(unreadable)}). Is the archive fully mounted and readable? "
                "Nothing was changed.")
        return gone

    @staticmethod
    def _shown(files: list[str]) -> str:
        ids = [Path(f).parent.name for f in files]
        return ", ".join(ids[:5]) + (f" and {len(ids) - 5} more" if len(ids) > 5 else "")

    def _refuse_missing(self, gone: list[str]) -> None:
        if gone:
            raise MissingRecordings(
                f"{len(gone)} recording(s) state.db knows are not in the archive now "
                f"({self._shown(gone)}). Is the archive fully mounted and readable? The index was "
                "not rebuilt. If they were removed on purpose, run `recordings reindex "
                "--allow-missing`.")

    def rebuild_index(self, *, allow_empty: bool = False, allow_missing: bool = False) -> int:
        # state.db knows every file the app committed. One that is no longer a file means a folder
        # is unmounted, unreadable or deleted; rebuilding now would drop it from the index and let
        # an import make a second copy of its bytes (§6.4). Every rebuild is guarded, not only
        # `reindex`. For a deletion Dan means: `recordings reindex --allow-missing`.
        if not allow_missing:
            self._refuse_missing(self._missing())
        with self.locks.hold(INDEX_LOCK):
            count = self.index.rebuild(self.archive, archive_uuid=self.identity.uuid,
                                       allow_empty=allow_empty)
        # Not a bulk clear: a recording that landed after the rebuild listed the archive still has
        # its mark. Each mark is finished, and cleared, under that recording's own lock, with
        # INDEX_LOCK released first (lock order: recordings come before the index).
        self._catch_up_index()
        return count

    def _index(self, rec: Recording) -> bool:
        """Bring the index up to date after a committed write. The write has landed, so a busy or
        damaged index never fails it: the pending mark stays, and the next add, or the next
        Writer, finishes the update. True when the index took it."""
        try:
            with self.locks.hold(INDEX_LOCK):
                self.index.upsert(rec)
                self.state.clear_index_pending(rec.id)
        except (LockTimeout, sqlite3.DatabaseError) as exc:
            log.warning("index update for %s left pending (%s)", rec.id, type(exc).__name__)
            return False
        return True

    def _catch_up_index(self) -> None:
        """Finish the index updates a writer left pending: it crashed, or the index was busy.
        `add` runs this first, so a long-running Writer sees another process's crash before it
        looks for a duplicate. Each recording's lock waits out a write still in progress."""
        for rid in self.state.index_pending():
            with self.locks.hold(rid):
                try:
                    raw = (self.root / self._rel(rid)).read_bytes()
                except FileNotFoundError:  # its assembly never landed: nothing to index
                    self.state.clear_index_pending(rid)
                    continue
                try:
                    rec = Recording.model_validate_json(raw)
                except ValidationError:
                    # edited into an invalid file: `recordings reindex` reports it. The mark goes,
                    # or every Writer start would rebuild for it again.
                    self.state.clear_index_pending(rid)
                    continue
                if not self._index(rec):
                    return  # the index can't take updates now; one wait per add, not one per mark

    # ---- mutate: every change to a recording.json -------------------------------------------
    @staticmethod
    def _rel(recording_id: str) -> str:
        return (relative_dir(recording_id) / "recording.json").as_posix()

    def _check_sentinel(self) -> None:
        """Re-read archive.json before a write: a long-lived writer outlives its mount (§6.7)."""
        try:
            found = read_sentinel(self.root)
        except SentinelError:
            raise NotTheWriter("archive.json is gone or unreadable: the archive is not mounted "
                               "now. Refusing to write.") from None
        if found.uuid != self.identity.uuid:
            raise NotTheWriter("the archive's UUID changed since this writer opened it. "
                               "Refusing to write.")
        if is_mirror(self.root):
            raise NotTheWriter("this folder is now the NAS mirror's copy, not the archive. "
                               "Refusing to write.")

    def mutate(self, recording_id: str, op: Op) -> Recording:
        self._check_sentinel()
        if not ID_RE.fullmatch(recording_id):
            raise KeyError(recording_id)
        rel = self._rel(recording_id)
        with self.locks.hold(recording_id):
            rec = self._load_fresh(rel)
            new = op.apply(rec)
            if new == rec:
                return rec
            # rev never goes backwards, even after an adopted hand edit lowered it (§6.4)
            last = self.state.last_committed(rel)
            rev = max(rec.rev or 0, last.rev if last is not None else 0) + 1
            try:
                new = Recording.model_validate({**new.model_dump(by_alias=True), "rev": rev})
            except ValidationError as exc:
                raise InvalidFile(
                    f"{op.name} would make {rel} invalid: {str(exc).splitlines()[0]}") from None
            self._write_recording(rel, new, origin=op.name)
            return new

    def _load_fresh(self, rel: str) -> Recording:
        """The file as it is now, once it validates and is the one the app last wrote."""
        try:
            raw = (self.root / rel).read_bytes()
        except FileNotFoundError:
            raise KeyError(rel) from None
        try:
            rec = Recording.model_validate_json(raw)
        except ValidationError as exc:
            # Checked before freshness: `reindex` can't help a file that doesn't validate.
            message = str(exc).splitlines()[0]
            self.state.flag(rel, "invalid", message)
            raise InvalidFile(
                f"{rel} does not validate ({message}). Fix it by hand: the app changes nothing "
                "in a file that doesn't validate (§11).") from None
        self._check_fresh(rel, raw)
        return rec

    def _check_fresh(self, rel: str, raw: bytes) -> None:
        sha = _sha(raw)
        pending = self.state.pending(rel)
        if pending is not None:
            if pending.sha256 == sha:  # the file landed; its revision's commit didn't
                self.state.commit(pending.id)
                return
            self.state.discard(pending.id)  # the replace never happened
        last = self.state.last_committed(rel)
        if last is None:  # no history yet (a new state.db, or a file from before the writer)
            self.state.adopt(rel, _rev_of(raw), sha, raw)
            return
        if last.sha256 != sha:
            self.state.flag(rel, "stale", "edited outside the app since the app last wrote it; "
                                          "run `recordings reindex` to accept the edit")
            raise StaleFile(
                f"{rel} was edited outside the app since the app last wrote it. Run "
                "`recordings reindex` to accept the edit, then try again.")

    def _write_recording(self, rel: str, rec: Recording, *, origin: str) -> None:
        data = dump_json(rec).encode("utf-8")
        self.state.mark_index_pending(rec.id)
        revision = self.state.begin(rel, rec.rev or 0, _sha(data), data, origin)
        write_bytes_atomic(self.root / rel, data)
        self.state.commit(revision)
        self._index(rec)

    # ---- adding recordings -------------------------------------------------------------------
    def add(self, incoming: Incoming) -> Added:
        self._check_sentinel()
        self._catch_up_index()
        sha = sha256_file(incoming.media)
        if incoming.sha256 is not None and sha != incoming.sha256:
            raise WriterError("the media changed after it was planned: its SHA-256 differs")
        rid = make_id(incoming.recorded_at, sha)
        with self.locks.hold(f"sha-{sha}"):
            existing = self._holding(sha, rid)
            if existing is not None:
                return self._merge(existing, sha, incoming)
            return self._create(rid, sha, incoming)

    def _holding(self, sha: str, rid: str) -> str | None:
        """The recording that already holds these bytes, if any."""
        # Read the marks before the first index query: a mark is cleared only after its upsert
        # commits, so a recording missing from this snapshot is already in the index, and one in
        # it leads to its file. Another process's catch-up can't slip between the two reads.
        pending = self.state.index_pending()
        existing = self._find(sha)
        if existing is not None and not (self.root / self._rel(existing)).is_file():
            # The index names a folder that is gone (removed by hand): rebuild, then ask again.
            self._rebuild_for_add()
            existing = self._find(sha)
        if existing is None and self.index.archive_uuid() != self.identity.uuid:
            # An index that can't answer (gone, unreadable, not this archive's) says "none" to
            # everything: rebuild it before believing that no recording holds these bytes.
            self._rebuild_for_add()
            existing = self._find(sha)
        if existing is None:
            # An update the index couldn't take (busy, or read-only) leaves a pending mark and an
            # index that says "none" with a matching UUID. The marks name those recordings.
            existing = self._pending_holding(sha, pending)
        if existing is None and (self.root / self._rel(rid)).is_file():
            existing = rid  # the index is behind (a crash after the rename): the folder wins
        return existing

    def _rebuild_for_add(self) -> None:
        try:
            self.rebuild_index()
        except MissingRecordings as exc:  # nothing was written: a second copy would be wrong
            raise IndexUnavailable(f"{exc} Nothing was written.") from None

    def _find(self, sha: str) -> str | None:
        """The index's answer. A sound index that can't answer now (busy past its timeout, an I/O
        error) refuses the add instead of answering "none", which would make a second recording
        of bytes the archive already holds (§6.4)."""
        try:
            return self.index.find_by_sha256(sha, strict=True)
        except sqlite3.DatabaseError as exc:
            code = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
            raise IndexUnavailable(f"the index at {self.index.path} can't answer now ({code}), "
                                   "so nothing was written: try again") from None

    def _pending_holding(self, sha: str, pending: list[str]) -> str | None:
        """A recording among `pending` that holds these bytes. recording.json is replaced
        atomically, so it is read without the recording's lock."""
        for rid in pending:
            try:
                rec = Recording.model_validate_json((self.root / self._rel(rid)).read_bytes())
            except (FileNotFoundError, ValidationError):
                continue  # gone or invalid: nothing to merge into. Any other OSError raises: a
                # file that can't be read now never means no recording holds these bytes.
            if rec.media.sha256 == sha:
                return rid
        return None

    def _create(self, rid: str, sha: str, incoming: Incoming) -> Added:
        final = self.root / relative_dir(rid)
        with self.locks.hold(rid):
            if final.exists():
                raise WriterError(f"{final} exists but holds different media")
            tmp = self.root / ".tmp" / f"{rid}.{uuid.uuid4().hex}"
            revision = None
            try:
                (tmp / "source").mkdir(parents=True)
                (tmp / "renditions").mkdir()
                media_name = f"{rid}{incoming.media.suffix.lower()}"
                copy_file_synced(incoming.media, tmp / media_name)
                if sha256_file(tmp / media_name) != sha:  # hash the copy, not only the source
                    raise WriterError(f"{incoming.media} changed while it was being copied")
                sources = [self._publish_raw(tmp, s) for s in incoming.sources]
                for rendition in incoming.renditions:
                    self._publish_rendition(tmp, rendition)
                if incoming.my_notes is not None:
                    write_text_atomic(tmp / "my-notes.md", incoming.my_notes)
                rec = Recording(
                    schema_ref=SCHEMA_REF, id=rid, rev=1, title=incoming.title,
                    title_by=incoming.title_by, recorded_at=incoming.recorded_at,
                    timezone=incoming.timezone_name, time_source=incoming.time_source,
                    media=MediaInfo(file=media_name, sha256=sha, kind=incoming.kind,
                                    duration_ms=incoming.duration_ms,
                                    sample_rate=incoming.sample_rate, channels=incoming.channels),
                    sources=sources, tags=list(incoming.tags),
                    excluded_note_types=list(dict.fromkeys(incoming.excluded_note_types)))
                data = dump_json(rec).encode("utf-8")
                write_bytes_atomic(tmp / "recording.json", data)
                rel = self._rel(rid)
                self.state.mark_index_pending(rid)
                revision = self.state.begin(rel, 1, _sha(data), data, "create")
                self._check_sentinel()  # the mount may have dropped while the media was hashed
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                if revision is not None:
                    self.state.discard(revision)
                self._drop_empty_tmp()
                raise
            # The recording's entry, and the month and year folders' entries when they are new:
            # a new year folder is an entry in recordings/ (§6.4).
            for folder in (final.parent, final.parent.parent, final.parent.parent.parent):
                fsync_dir(folder)
            self.state.commit(revision)
            self._drop_empty_tmp()
            self._index(rec)
            return Added(rec, created=True, sources_added=len(sources),
                         renditions_added=len(incoming.renditions))

    def _merge(self, rid: str, sha: str, incoming: Incoming) -> Added:
        with self.locks.hold(rid):
            rec = self._load_fresh(self._rel(rid))
            if rec.media.sha256 != sha:
                raise WriterError(f"{rid} exists but holds different media")
            private = tuple(t for t in incoming.tags if is_private_tag(t.tag))
            if private:  # §7.4: the private tag lands first, before any content it protects
                self.mutate(rid, AddTags(private))
            folder = self.archive.path_for(rid)
            have = {(s.kind, canonical_ref(s.kind, s.ref), s.sha256) for s in rec.sources}
            new_sources = []
            for source in incoming.sources:
                digest = _sha(source.payload) if source.payload is not None else None
                key = (source.kind, canonical_ref(source.kind, source.ref), digest)
                if key not in have:
                    have.add(key)
                    new_sources.append(self._publish_raw(folder, source))
            written = sum(self._write_rendition_locked(folder, r) is not None
                          for r in incoming.renditions)
            self._merge_notes(folder, incoming.my_notes)
            merged = self.mutate(rid, MergeIncoming(tuple(new_sources), tuple(incoming.tags),
                                                    tuple(incoming.excluded_note_types)))
            return Added(merged, created=False, sources_added=len(new_sources),
                         renditions_added=written)

    @staticmethod
    def _merge_notes(folder: Path, notes: str | None) -> None:
        if not notes or not notes.strip():
            return
        path = folder / "my-notes.md"
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current is None:
            write_text_atomic(path, notes)
        elif notes.strip() not in current:
            write_text_atomic(path, current.rstrip("\n") + "\n\n---\n\n" + notes)

    # ---- outputs and raw payloads (write-once) -------------------------------------------------
    def write_rendition(self, recording_id: str, rendition: Rendition) -> str | None:
        """Publish an output beside a recording. None when an identical file is already there."""
        self._check_sentinel()
        folder = self.archive.path_for(recording_id)
        with self.locks.hold(recording_id):
            if not (folder / "recording.json").is_file():
                raise KeyError(recording_id)
            return self._write_rendition_locked(folder, rendition)

    def _write_rendition_locked(self, folder: Path, rendition: Rendition) -> str | None:
        data = dump_json(rendition).encode("utf-8")
        digest = _sha(data)
        for path in (folder / "renditions").glob("*.json"):
            with contextlib.suppress(OSError):
                if _sha(path.read_bytes()) == digest:
                    return None
        return self._publish_rendition(folder, rendition, data)

    @staticmethod
    def _publish_rendition(folder: Path, rendition: Rendition, data: bytes | None = None) -> str:
        (folder / "renditions").mkdir(exist_ok=True)
        data = data if data is not None else dump_json(rendition).encode("utf-8")
        path = publish_exclusive(data, folder / "renditions" / rendition_filename(rendition))
        return path.relative_to(folder).as_posix()

    @staticmethod
    def _publish_raw(folder: Path, source: RawSource) -> SourceRef:
        raw = digest = None
        if source.payload is not None:
            when = source.fetched_at or source.added_at
            (folder / "source").mkdir(exist_ok=True)
            target = folder / "source" / f"{_slug(source.kind)}-{utc_stamp(when)}{source.suffix}"
            digest = _sha(source.payload)
            # A file with these bytes already there is reused: a merge killed between publishing
            # and recording its snapshot never leaves a duplicate (Review Focus 2).
            same = next((p for p in sorted(target.parent.glob(f"{target.stem}*{target.suffix}"))
                         if _sha(p.read_bytes()) == digest), None)
            path = same or publish_exclusive(source.payload, target)
            raw = path.relative_to(folder).as_posix()
        return SourceRef(kind=source.kind, ref=source.ref, added_at=source.added_at,
                         fetched_at=source.fetched_at, raw=raw, sha256=digest)

    def reconcile_plaud(self, recording_id: str) -> ReconcileReport:
        """Build any Plaud outputs this recording's snapshots imply, and apply the fields Plaud
        owns (§9.1.1). Writes nothing when there is nothing new.

        Privacy first: Plaud's fields, an auto-private tag among them, land in one `mutate`
        before any output is published. A crash in between leaves a tagged recording whose
        outputs the next reconcile writes."""
        self._check_sentinel()  # outputs are written even when no field changes, so check here
        with self.locks.hold(recording_id):
            rec = self._load_fresh(self._rel(recording_id))
            folder = self.archive.path_for(recording_id)
            snapshots = []
            for source in rec.sources:
                if source.kind != "plaud" or not source.raw:
                    continue
                try:
                    envelope = json.loads((folder / source.raw).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError, RecursionError):
                    envelope = None
                snapshots.append(Snapshot(path=source.raw, ref=source.ref,
                                          fetched_at=source.fetched_at or source.added_at,
                                          envelope=envelope))
            if not snapshots:
                return ReconcileReport(recording_id, 0, (), (), False)
            unreadable: list = []
            existing = [r for _, r in self.archive.renditions(recording_id, problems=unreadable)]
            result = reconcile(rec, snapshots, existing, auto_private=self.auto_private)
            if result.title is not None or result.title_seen is not None or result.add_private:
                self.mutate(recording_id, SetPlaudFields(result.title, result.title_seen,
                                                         result.add_private))
            if unreadable:
                # An output that can't be read now (damaged, or an I/O error) isn't a missing
                # one: writing it again would leave a second, permanent copy beside it.
                return ReconcileReport(recording_id, 0, result.held, result.skipped,
                                       result.title_difference is not None,
                                       problem="an output can't be read: not reconciled")
            written = sum(self._write_rendition_locked(folder, r) is not None
                          for r in result.outputs)
            return ReconcileReport(recording_id, written, result.held, result.skipped,
                                   result.title_difference is not None)

    # ---- housekeeping ------------------------------------------------------------------------
    def _sweep_tmp(self) -> None:
        """Remove assemblies a killed writer left in .tmp/ (Review Focus 2). Each is named
        `<recording id>.<hex>`, and is removed only while that recording's lock is free."""
        tmp_root = self.root / ".tmp"
        if not tmp_root.is_dir():
            return
        probe = Locks(self.state.locks_dir, timeout=0)
        for entry in sorted(tmp_root.iterdir()):
            rid = entry.name.split(".", 1)[0]
            if not ID_RE.fullmatch(rid):
                continue  # not one of ours; leave it alone
            try:
                with probe.hold(rid):
                    shutil.rmtree(entry, ignore_errors=True)
            except LockTimeout:
                continue  # another writer is assembling it right now
        self._drop_empty_tmp()

    def _drop_empty_tmp(self) -> None:
        with contextlib.suppress(OSError):
            (self.root / ".tmp").rmdir()

    # ---- reindex: accept outside edits, rebuild the index (§6.4, §6.7, §11) -----------------
    def reindex(self, *, allow_empty: bool = False,
                allow_missing: bool = False) -> ReindexReport:
        self._check_sentinel()
        # Before anything is adopted or reconciled: a refused reindex writes nothing.
        gone = self._missing()
        if not allow_missing:
            self._refuse_missing(gone)
        adopted, invalid = [], []
        for folder in self.archive.recording_dirs():
            if not ID_RE.fullmatch(folder.name):
                continue  # reported by iter_recordings as a mismatched folder
            try:
                rel = self._rel(folder.name)
            except ValueError:
                continue
            if (folder / "recording.json").relative_to(self.root).as_posix() != rel:
                continue  # a folder in the wrong month: iter_recordings reports it
            with self.locks.hold(folder.name):
                try:
                    raw = (folder / "recording.json").read_bytes()
                except OSError:
                    continue  # unreadable or gone now: the missing check names it
                outcome = self._accept(rel, raw)
            if outcome == "adopted":
                adopted.append(rel)
            elif outcome == "invalid":
                invalid.append(rel)
        count = self.rebuild_index(allow_empty=allow_empty, allow_missing=allow_missing)
        reconciled = []
        for rec in list(self.archive.iter_recordings()):
            if not any(s.kind == "plaud" for s in rec.sources):
                continue
            try:
                reconciled.append(self.reconcile_plaud(rec.id))
            except (WriterError, KeyError, LockTimeout, OSError, ValueError, TypeError,
                    RecursionError, ArithmeticError) as exc:  # ValueError: a payload the
                # renditions can't encode; RecursionError: a payload nested too deep to parse
                log.debug("reconcile of %s failed", rec.id, exc_info=True)
                # A fixed phrase, never the exception's text, which can carry a title.
                problem = f"{type(exc).__name__}: not reconciled"
                reconciled.append(ReconcileReport(rec.id, 0, (), (), False, problem=problem))
        for rel in gone:  # --allow-missing means "removed on purpose": state.db forgets them
            self.state.forget(rel)
        problems = tuple({"path": str(p.path), "message": p.message}
                         for p in self.archive.problems)
        return ReindexReport(recordings=count, adopted=tuple(adopted), invalid=tuple(invalid),
                             problems=problems, forgotten=tuple(Path(f).parent.name for f in gone),
                             reconciled=tuple(reconciled))

    def _accept(self, rel: str, raw: bytes) -> str:
        sha = _sha(raw)
        pending = self.state.pending(rel)
        if pending is not None:
            if pending.sha256 == sha:
                self.state.commit(pending.id)
                self.state.clear_flags(rel)
                return "unchanged"
            self.state.discard(pending.id)
        last = self.state.last_committed(rel)
        if last is not None and last.sha256 == sha:
            self.state.clear_flags(rel)  # an edit undone by hand leaves no stale flag behind
            return "unchanged"
        try:
            rec = Recording.model_validate_json(raw)
        except ValidationError as exc:
            self.state.flag(rel, "invalid", str(exc).splitlines()[0])
            return "invalid"
        if rec.id != Path(rel).parent.name:
            return "skipped"  # a copied folder: iter_recordings reports it, nothing is adopted
        # The base keeps the highest rev seen, so a hand-lowered rev never makes rev go back.
        self.state.adopt(rel, max(rec.rev or 0, last.rev if last is not None else 0), sha, raw)
        self.state.clear_flags(rel)
        return "adopted"
