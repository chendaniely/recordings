"""The derived index (spec §6.8): `index.db`, rebuilt from the archive by `recordings reindex`.

Dropping it loses nothing. Writers keep it current after every write, and it answers the lookups a
writer needs at scale: which recording holds these bytes, and which holds this source reference.
`rebuild` refuses to empty an index that holds recordings (§6.7): an archive that suddenly lists
none is far more likely unmounted than empty. It rebuilds inside one transaction on the live file,
so a failed rebuild leaves the old index as it was and nothing beside it, and readers see the old
index until the new one commits. An index SQLite can't read at all is renamed aside and rebuilt;
only a file whose header says it is an index (APPLICATION_ID) is ever moved.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from recordings.archive import Archive
from recordings.ids import relative_dir
from recordings.models import Recording
from recordings.refs import canonical_ref

log = logging.getLogger(__name__)
INDEX_VERSION = 1
# In the file's header (offset 68), so a file says it is an index before SQLite reads a page of it.
# Only a file that says so is ever renamed aside.
APPLICATION_ID = 0x52494458  # "RIDX"
_MARK = APPLICATION_ID.to_bytes(4, "big")
_TABLES = ("meta", "recordings", "source_refs")  # dropping a table drops its indexes too
# One statement each: they run inside rebuild's transaction, which executescript would commit.
_SCHEMA = (
    f"PRAGMA application_id = {APPLICATION_ID}",
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    (
        "CREATE TABLE recordings ("
        " id TEXT PRIMARY KEY, folder TEXT NOT NULL, sha256 TEXT NOT NULL, rev INTEGER NOT NULL)"
    ),
    "CREATE INDEX recordings_by_sha256 ON recordings (sha256)",
    (
        "CREATE TABLE source_refs ("
        " recording_id TEXT NOT NULL, kind TEXT NOT NULL,"
        " ref TEXT NOT NULL, canonical TEXT NOT NULL,"
        " PRIMARY KEY (recording_id, kind, ref))"
    ),
    "CREATE INDEX source_refs_by_canonical ON source_refs (kind, canonical)",
)
_UNREADABLE = ("SQLITE_NOTADB", "SQLITE_CORRUPT")
# A sound index that can't answer now: locked past the busy timeout, or an I/O error. A lookup
# that decides whether to make a new recording, or whether the import's plan may go ahead,
# passes strict=True, and these raise (§6.4, §9.3).
_NO_ANSWER = ("SQLITE_BUSY", "SQLITE_LOCKED", "SQLITE_IOERR", "SQLITE_NOMEM", "SQLITE_CANTOPEN")


class IndexRefused(RuntimeError):
    """A rebuild that would empty a non-empty index, or replace a database that isn't one."""


def _insert(db: sqlite3.Connection, rec: Recording) -> None:
    db.execute(
        "INSERT INTO recordings (id, folder, sha256, rev) VALUES (?, ?, ?, ?)",
        (rec.id, relative_dir(rec.id).as_posix(), rec.media.sha256, rec.rev or 0),
    )
    db.executemany(
        "INSERT OR IGNORE INTO source_refs (recording_id, kind, ref, canonical)"
        " VALUES (?, ?, ?, ?)",
        [(rec.id, s.kind, s.ref, canonical_ref(s.kind, s.ref)) for s in rec.sources],
    )


class Index:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn, conn:
            yield conn

    def exists(self) -> bool:
        return self.path.is_file()

    def _rows(self, sql: str, args: tuple = (), *, strict: bool = False) -> list[tuple]:
        if not self.exists():
            return []
        try:
            with self._db() as db:
                return db.execute(sql, args).fetchall()
        except sqlite3.DatabaseError as exc:
            # A damaged or foreign file answers nothing; rebuild it. A busy one is not "nothing":
            # a strict lookup raises rather than say no recording holds these bytes.
            if strict and (getattr(exc, "sqlite_errorname", None) or "").startswith(_NO_ANSWER):
                raise
            return []

    def _one(self, sql: str, args: tuple = (), *, strict: bool = False) -> object | None:
        rows = self._rows(sql, args, strict=strict)
        return rows[0][0] if rows else None

    def count(self) -> int:
        return int(self._one("SELECT count(*) FROM recordings") or 0)

    def archive_uuid(self) -> str | None:
        # state.db's meta holds the same archive_uuid: only a file that says it is an index (its
        # header) and is one (its meta) answers.
        if self._one("PRAGMA application_id") != APPLICATION_ID:
            return None
        return self._one(
            "SELECT value FROM meta WHERE key = 'archive_uuid' AND EXISTS "
            "(SELECT 1 FROM meta WHERE key = 'index_version')"
        )

    def rebuild(self, archive: Archive, *, archive_uuid: str, allow_empty: bool = False) -> int:
        recordings = list(archive.iter_recordings())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.exists():
            self._handle_unreadable_file()
        with self._db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")  # one writer; readers keep the old index until commit
                tables = self._check_ours(db)
                # Counted inside the lock, on this connection: outside it, a locked index read as 0.
                held = (
                    db.execute("SELECT count(*) FROM recordings").fetchone()[0]
                    if "recordings" in tables
                    else 0
                )
            except sqlite3.DatabaseError as exc:
                if exc.sqlite_errorname not in _UNREADABLE:
                    raise
                raise IndexRefused(
                    f"{self.path} can't be read ({exc.sqlite_errorname}) and doesn't say it is an "
                    "index, so rebuild leaves it alone. Check [index] path. If it is the index, "
                    "move it aside and run `recordings reindex`."
                ) from None
            if not recordings and held and not allow_empty:
                raise IndexRefused(
                    f"{archive.root} lists no recordings but the index holds {held}: refusing to "
                    "empty it. Is the archive mounted? Pass --allow-empty if it really is empty."
                )
            for table in _TABLES:
                db.execute(f"DROP TABLE IF EXISTS {table}")
            for statement in _SCHEMA:
                db.execute(statement)
            db.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [("index_version", str(INDEX_VERSION)), ("archive_uuid", archive_uuid)],
            )
            for rec in recordings:
                _insert(db, rec)
        self.wal()
        return len(recordings)

    def wal(self, *, timeout: float = 5.0) -> bool:
        """Switch the file to WAL mode (2b spec §9.5): web and the worker each hold a Writer, so one
        can upsert or rebuild while the other reads. The file keeps the mode, so once it is WAL this
        is a no-op. Called only on a file known to be this index (a rebuild just committed it, or a
        Writer found it this archive's), outside a transaction, where SQLite can change it.

        The switch needs the file to itself. While another process has it open (web reading as the
        worker starts), SQLite raises a DatabaseError (usually an OperationalError) or answers with
        another mode. WAL only speeds things up, so either way it is logged in a fixed phrase (the
        error's class, or ModeRefused), False is returned, and the next rebuild or Writer tries
        again."""
        try:
            with closing(sqlite3.connect(self.path, timeout=timeout)) as conn:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            log.warning("index.db stays in rollback-journal mode for now (%s)", type(exc).__name__)
            return False
        if mode != "wal":
            log.warning("index.db stays in rollback-journal mode for now (ModeRefused)")
            return False
        return True

    def _readable(self) -> bool:
        """False only when SQLite can't read the file at all; a busy file is still an error."""
        # Read-only and immutable: SQLite then ignores the -wal and -journal and never replays,
        # checkpoints or deletes them. Those siblings are the evidence the quarantine keeps. It
        # also skips locking, so a file written meanwhile can read as corrupt: see _stamp.
        uri = f"{self.path.resolve().as_uri()}?mode=ro&immutable=1"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError as exc:
            if exc.sqlite_errorname in _UNREADABLE:
                return False
            raise

    def _stamp(self) -> tuple:
        """Inode, size and mtime of the file and the siblings a writer changes, None if absent.
        Readers write the -shm too, so it is left out."""
        stamps = []
        for suffix in ("", "-journal", "-wal"):
            try:
                st = os.stat(f"{self.path}{suffix}")
            except FileNotFoundError:
                stamps.append(None)
            else:
                stamps.append((st.st_ino, st.st_size, st.st_mtime_ns))
        return tuple(stamps)

    def _handle_unreadable_file(self) -> None:
        """Refuse a file that isn't SQLite, and rename aside an index SQLite can't read. Never
        deletes, and never moves a file whose header doesn't say it is an index."""
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size == 0:
            return
        try:
            with self.path.open("rb") as f:
                header = f.read(100)
        except OSError as exc:
            raise IndexRefused(
                f"{self.path} can't be read ({type(exc).__name__}): check its "
                "owner and mode, and [index] path."
            ) from None
        if header[:16] != b"SQLite format 3\x00":
            raise IndexRefused(
                f"{self.path} is not an index (SQLite header not found). Check [index] path."
            )
        if header[68:72] != _MARK:
            return  # it doesn't say it is an index: never renamed; BEGIN IMMEDIATE decides
        # It says it is an index; can SQLite read it? A file written during the probe proves
        # nothing, so it goes on to BEGIN IMMEDIATE.
        before = self._stamp()
        if not self._readable() and self._stamp() == before:
            # Corrupted SQLite; rename aside with collision-safe timestamp
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            # Find a collision-free suffix number for all siblings
            suffix_num = ""
            while True:
                base_suffix = f".unreadable-{stamp}{suffix_num}"
                # Check all siblings before committing to the rename
                free = True
                for sibling_suffix in ("", "-journal", "-wal", "-shm"):
                    old_path = Path(f"{self.path}{sibling_suffix}")
                    if old_path.exists() and os.path.lexists(f"{old_path}{base_suffix}"):
                        free = False
                        break
                if free:
                    break
                # Increment and try again
                suffix_num = f"-{int(suffix_num[1:] if suffix_num else 1) + 1}"
            # The siblings first and the file last: a failure part-way leaves the file where it
            # was, and never a -wal or -journal alone, which SQLite deletes beside a new, empty
            # file.
            for sibling_suffix in ("-journal", "-wal", "-shm", ""):
                old_path = Path(f"{self.path}{sibling_suffix}")
                try:
                    old_path.rename(old_path.with_name(f"{old_path.name}{base_suffix}"))
                except FileNotFoundError:
                    if not sibling_suffix:
                        raise

    def _check_ours(self, db: sqlite3.Connection) -> set[str]:
        """The file's tables, once it is known to be empty or an index."""
        tables = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        ours = (
            "meta" in tables
            and db.execute("SELECT 1 FROM meta WHERE key = 'index_version'").fetchone() is not None
        )
        if tables and not ours:
            raise IndexRefused(
                f"{self.path} is a database but not an index, so rebuild won't replace it. "
                "Check [index] path."
            )
        return tables

    def upsert(self, rec: Recording) -> None:
        with self._db() as db:
            db.execute("DELETE FROM source_refs WHERE recording_id = ?", (rec.id,))
            db.execute("DELETE FROM recordings WHERE id = ?", (rec.id,))
            _insert(db, rec)

    def find_by_sha256(self, sha: str, *, strict: bool = False) -> str | None:
        """strict: an index that can't answer now (busy, an I/O error) raises, never says none."""
        return self._one(
            "SELECT id FROM recordings WHERE sha256 = ? ORDER BY id LIMIT 1", (sha,), strict=strict
        )

    def sha256_of(self, recording_id: str, *, strict: bool = False) -> str | None:
        """strict: an index that can't answer now (busy, an I/O error) raises, never says none."""
        return self._one(
            "SELECT sha256 FROM recordings WHERE id = ?", (recording_id,), strict=strict
        )

    def find_by_source(self, kind: str, ref: str, *, strict: bool = False) -> list[str]:
        """strict: an index that can't answer now (busy, an I/O error) raises, never says none."""
        rows = self._rows(
            "SELECT DISTINCT recording_id FROM source_refs WHERE kind = ? AND canonical = ? "
            "ORDER BY recording_id",
            (kind, canonical_ref(kind, ref)),
            strict=strict,
        )
        return [row[0] for row in rows]
