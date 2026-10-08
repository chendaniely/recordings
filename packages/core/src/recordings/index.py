"""The derived index (spec §6.8): `index.db`, rebuilt from the archive by `recordings reindex`.

Dropping it loses nothing. Writers keep it current after every write, and it answers the lookups a
writer needs at scale: which recording holds these bytes, and which holds this source reference.
`rebuild` refuses to empty an index that holds recordings (§6.7): an archive that suddenly lists
none is far more likely unmounted than empty. It rebuilds inside one transaction on the live file,
so a failed rebuild leaves the old index as it was and nothing beside it, and readers see the old
index until the new one commits. A file SQLite can't read at all is renamed aside and rebuilt.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from recordings.archive import Archive
from recordings.ids import relative_dir
from recordings.models import Recording
from recordings.refs import canonical_ref

INDEX_VERSION = 1
_TABLES = ("meta", "recordings", "source_refs")  # dropping a table drops its indexes too
# One statement each: they run inside rebuild's transaction, which executescript would commit.
_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE recordings ("
    " id TEXT PRIMARY KEY, folder TEXT NOT NULL, sha256 TEXT NOT NULL, rev INTEGER NOT NULL)",
    "CREATE INDEX recordings_by_sha256 ON recordings (sha256)",
    "CREATE TABLE source_refs ("
    " recording_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, canonical TEXT NOT NULL,"
    " PRIMARY KEY (recording_id, kind, ref))",
    "CREATE INDEX source_refs_by_canonical ON source_refs (kind, canonical)",
)
_UNREADABLE = ("SQLITE_NOTADB", "SQLITE_CORRUPT")


class IndexRefused(RuntimeError):
    """A rebuild that would empty a non-empty index, or replace a database that isn't one."""


def _insert(db: sqlite3.Connection, rec: Recording) -> None:
    db.execute("INSERT INTO recordings (id, folder, sha256, rev) VALUES (?, ?, ?, ?)",
               (rec.id, relative_dir(rec.id).as_posix(), rec.media.sha256, rec.rev or 0))
    db.executemany(
        "INSERT OR IGNORE INTO source_refs (recording_id, kind, ref, canonical) VALUES (?, ?, ?, ?)",
        [(rec.id, s.kind, s.ref, canonical_ref(s.kind, s.ref)) for s in rec.sources])


class Index:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            with conn:
                yield conn

    def exists(self) -> bool:
        return self.path.is_file()

    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        if not self.exists():
            return []
        try:
            with self._db() as db:
                return db.execute(sql, args).fetchall()
        except sqlite3.DatabaseError:  # a damaged or foreign file answers nothing; rebuild it
            return []

    def _one(self, sql: str, args: tuple = ()) -> object | None:
        rows = self._rows(sql, args)
        return rows[0][0] if rows else None

    def count(self) -> int:
        return int(self._one("SELECT count(*) FROM recordings") or 0)

    def archive_uuid(self) -> str | None:
        return self._one("SELECT value FROM meta WHERE key = 'archive_uuid'")

    def rebuild(self, archive: Archive, *, archive_uuid: str, allow_empty: bool = False) -> int:
        recordings = list(archive.iter_recordings())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.exists():
            self._handle_unreadable_file()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # one writer; readers keep the old index until commit
            self._check_ours(db)
            # Check empty inside the lock, before dropping tables. The old code read the count
            # outside the transaction, where a locked index read as 0. This read on the same
            # connection is protected by BEGIN IMMEDIATE.
            held = 0
            try:
                held = int(db.execute("SELECT count(*) FROM recordings").fetchone()[0])
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
            except TypeError:
                pass
            if not recordings and held and not allow_empty:
                raise IndexRefused(
                    f"{archive.root} lists no recordings but the index holds {held}: refusing to "
                    "empty it. Is the archive mounted? Pass --allow-empty if it really is empty.")
            for table in _TABLES:
                db.execute(f"DROP TABLE IF EXISTS {table}")
            for statement in _SCHEMA:
                db.execute(statement)
            db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                           [("index_version", str(INDEX_VERSION)), ("archive_uuid", archive_uuid)])
            for rec in recordings:
                _insert(db, rec)
        return len(recordings)

    def _readable(self) -> bool:
        """False only when SQLite can't read the file at all; a busy file is still an error."""
        try:
            with self._db() as db:
                return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError as exc:
            if exc.sqlite_errorname in _UNREADABLE:
                return False
            raise

    def _handle_unreadable_file(self) -> None:
        """Check file type and handle corruption. Never deletes."""
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size == 0:
            return
        # Check for SQLite header
        try:
            with self.path.open("rb") as f:
                header = f.read(16)
        except OSError:
            raise IndexRefused(f"{self.path} is not an index (cannot read file). "
                             "Check [index] path.")
        if header != b"SQLite format 3\x00":
            raise IndexRefused(f"{self.path} is not an index (SQLite header not found). "
                             "Check [index] path.")
        # It's a SQLite file; check if it's readable
        if not self._readable():
            # Corrupted SQLite; rename aside with collision-safe timestamp
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
            # Now rename all siblings with the same base suffix
            for sibling_suffix in ("", "-journal", "-wal", "-shm"):
                old_path = Path(f"{self.path}{sibling_suffix}")
                if old_path.exists():
                    new_path = old_path.with_name(f"{old_path.name}{base_suffix}")
                    old_path.rename(new_path)

    def _check_ours(self, db: sqlite3.Connection) -> None:
        tables = {row[0] for row in
                  db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        ours = "meta" in tables and db.execute(
            "SELECT 1 FROM meta WHERE key = 'index_version'").fetchone() is not None
        if tables and not ours:
            raise IndexRefused(
                f"{self.path} is a database but not an index, so rebuild won't replace it. "
                "Check [index] path.")

    def upsert(self, rec: Recording) -> None:
        with self._db() as db:
            db.execute("DELETE FROM source_refs WHERE recording_id = ?", (rec.id,))
            db.execute("DELETE FROM recordings WHERE id = ?", (rec.id,))
            _insert(db, rec)

    def find_by_sha256(self, sha: str) -> str | None:
        return self._one("SELECT id FROM recordings WHERE sha256 = ? ORDER BY id LIMIT 1", (sha,))

    def sha256_of(self, recording_id: str) -> str | None:
        return self._one("SELECT sha256 FROM recordings WHERE id = ?", (recording_id,))

    def find_by_source(self, kind: str, ref: str) -> list[str]:
        rows = self._rows(
            "SELECT DISTINCT recording_id FROM source_refs WHERE kind = ? AND canonical = ? "
            "ORDER BY recording_id", (kind, canonical_ref(kind, ref)))
        return [row[0] for row in rows]
