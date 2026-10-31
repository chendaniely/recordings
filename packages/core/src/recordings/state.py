"""Operational state that can't be derived (spec §6.8): `state.db`, and the lock files beside it.

It lives in `[state] path` (`/srv/recordings/state`), outside the archive, so the archive's backups
and the mirror never copy it. It must be on a local disk: `flock` and SQLite's WAL both break over
NFS and SMB. The backup copies `state.db` on its own, with SQLite's backup command (§15.1). Reads
never need it: the Mac's read-only mirror has none.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from recordings.files import fsync_dir, temp_name

SCHEMA_VERSION = 1
# The version-1 tables: stage 2a's, then stage 2b's. Every statement is CREATE ... IF NOT EXISTS,
# so opening a state.db made by an earlier release adds what is missing.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT NOT NULL, rev INTEGER NOT NULL,
  sha256 TEXT NOT NULL, body BLOB NOT NULL, origin TEXT NOT NULL,
  committed INTEGER NOT NULL, written_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS revisions_by_file ON revisions (file, id);
CREATE TABLE IF NOT EXISTS flags (
  file TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (file, kind));
CREATE TABLE IF NOT EXISTS index_pending (recording_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS ops_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ops_runs_by_job ON ops_runs (job, id);
CREATE TABLE IF NOT EXISTS alerts (
  key TEXT PRIMARY KEY, failing INTEGER NOT NULL, last_sent_at TEXT);
-- Stage 2b's tables (its spec §9.5; recordings.plaud.checks reads and writes them). Tables only,
-- every column from the start and SCHEMA_VERSION still 1, so an older release's open ignores them
-- and `make rollback` keeps working (2b phase 1 plan, decision 3). Plaud IDs are canonical.
CREATE TABLE IF NOT EXISTS plaud_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  plaud_ids TEXT NOT NULL DEFAULT '[]', recording_ids TEXT NOT NULL DEFAULT '[]',
  select_all INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL,                     -- queued | running | done | failed | expired | refused
  queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  progress TEXT,                           -- JSON {"done", "total"}
  message TEXT,                            -- a §15.6 reply phrase
  result TEXT);                            -- JSON: counts and IDs, never a title
CREATE INDEX IF NOT EXISTS plaud_requests_by_state ON plaud_requests (state, id);
CREATE TABLE IF NOT EXISTS plaud_listing (
  canonical_plaud_id TEXT PRIMARY KEY, listed_id TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, absent_since TEXT,
  kept_out_at TEXT, kept_out_word TEXT,    -- by a word (§12.2); a late keep-out's too
  rekey_of TEXT,                           -- §11.4 step 6: the absent ID it matched; '*': several
  released_at TEXT,                        -- §12.2
  needs_review INTEGER NOT NULL DEFAULT 0, -- §11.6
  import_failures INTEGER NOT NULL DEFAULT 0, next_import_at TEXT, parked_at TEXT,
  title_sha256 TEXT, duration_ms INTEGER, change_marker TEXT);   -- only while its recording is here
CREATE TABLE IF NOT EXISTS plaud_checks (
  recording_id TEXT PRIMARY KEY, last_checked_at TEXT, last_changed_at TEXT, next_check_at TEXT,
  interval_seconds INTEGER, fast_checks_left INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0, failing_since TEXT,
  settled INTEGER NOT NULL DEFAULT 0, baseline_pending INTEGER NOT NULL DEFAULT 0,
  held_removals TEXT NOT NULL DEFAULT '[]', parked_at TEXT, degraded INTEGER NOT NULL DEFAULT 0,
  gone_since TEXT, kept_here_at TEXT);
CREATE TABLE IF NOT EXISTS plaud_check_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, recording_id TEXT NOT NULL,
  result TEXT NOT NULL, parts_changed TEXT NOT NULL DEFAULT '[]',
  parts_volatile TEXT NOT NULL DEFAULT '[]', counted INTEGER NOT NULL DEFAULT 0,
  failure_class TEXT);
CREATE INDEX IF NOT EXISTS plaud_check_log_by_at ON plaud_check_log (at);
CREATE TABLE IF NOT EXISTS plaud_private_checked (
  recording_id TEXT NOT NULL, canonical_plaud_id TEXT NOT NULL, title_sha256 TEXT NOT NULL,
  patterns_version INTEGER NOT NULL, checked_at TEXT NOT NULL,
  PRIMARY KEY (recording_id, canonical_plaud_id));
CREATE TABLE IF NOT EXISTS plaud_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
  recording_id TEXT, canonical_plaud_id TEXT,
  detail TEXT NOT NULL DEFAULT '{}',       -- JSON: parts, a fixed "why"; never a title
  open INTEGER NOT NULL DEFAULT 0, resolved_at TEXT);
CREATE INDEX IF NOT EXISTS plaud_events_by_at ON plaud_events (at);
CREATE UNIQUE INDEX IF NOT EXISTS plaud_events_one_flag ON plaud_events (recording_id)
  WHERE open = 1 AND kind IN ('kept-out-late', 'consent-late');
CREATE UNIQUE INDEX IF NOT EXISTS plaud_events_one_unwritten ON plaud_events (recording_id)
  WHERE open = 1 AND kind = 'private-unwritten';
CREATE TABLE IF NOT EXISTS plaud_state (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
# Every change after version 1, keyed by the version it makes: MIGRATIONS[2] takes a state.db
# from version 1 to 2. CREATE TABLE IF NOT EXISTS can't add a column, so a later stage that changes
# a table adds its statements here and raises SCHEMA_VERSION to match. Never edit a shipped one.
MIGRATIONS: dict[int, tuple[str, ...]] = {}

RESTORE_HINT = (
    "restore state.db from the backup (runbook: Restoring for real). If no backup copy is usable, "
    "stop the app, move the whole state folder aside (never state.db alone: its -wal file holds "
    "the latest commits), and run `recordings init --adopt`."
)
# SQLite's logs beside state.db hold commits not yet in it. SQLite replays a -wal over whatever
# state.db is next put beside it, and deletes one it finds beside a new, empty state.db.
LOGS = ("state.db-wal", "state.db-journal")
# SQLite's codes for a sound state.db that can't be reached now: a mode, an owner or a missing
# mount (CANTOPEN), a read-only folder, a lock held too long, a full disk, an I/O error.
_NOT_DAMAGE = (
    "SQLITE_CANTOPEN",
    "SQLITE_READONLY",
    "SQLITE_BUSY",
    "SQLITE_LOCKED",
    "SQLITE_FULL",
    "SQLITE_IOERR",
    "SQLITE_PERM",
    "SQLITE_NOMEM",
)


def unreadable(db_path: Path, exc: sqlite3.Error) -> str:
    """Why state.db can't be used, from a fixed template with SQLite's code, never its message.
    Only damage gets the way back: moving a sound state.db aside throws away the queue and every
    merge base."""
    code = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
    if code.startswith(_NOT_DAMAGE):
        return (
            f"{db_path} can't be opened now ({code}). That is not damage, so leave it where it "
            "is: check that the state folder is mounted, owned by this user, writable and not "
            "full, and that no other process holds it, then try again."
        )
    return f"{db_path} can't be read ({code}). If it is damaged, {RESTORE_HINT}"


class StateError(RuntimeError):
    pass


def _already(db_path: Path) -> str:
    return f"{db_path} already exists: `recordings init` runs once per archive"


KEEP_REVISIONS = 8  # merge bases kept per file (§6.4); stage 3's three-way merge reads them


@dataclass(frozen=True)
class Revision:
    id: int
    file: str
    rev: int
    sha256: str
    body: bytes
    origin: str  # the op's name ("create", "adopted", "merge-duplicate"), or <op>:<request_id>
    committed: bool


@dataclass(frozen=True)
class Run:
    job: str
    started_at: datetime
    finished_at: datetime
    ok: bool
    detail: str  # a fixed text (ops.failure_detail): never an error's message


def _now() -> str:
    return datetime.now(UTC).isoformat()


class State:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._read_only = False  # read_only() sets it: every connection is then mode=ro

    @property
    def _mode(self) -> str:
        return "ro" if self._read_only else "rw"

    @property
    def db_path(self) -> Path:
        return self.path / "state.db"

    @property
    def locks_dir(self) -> Path:
        return self.path / "locks"

    @classmethod
    def create(cls, path: Path, *, archive_uuid: str, writer_id: str) -> State:
        """Make state.db, once. Of two inits racing for one folder, one wins and one is refused:
        the meta rows go in as one transaction, and a second set breaks their primary key."""
        state = cls(path)
        if state.db_path.exists():
            raise StateError(_already(state.db_path))
        if logs := [name for name in LOGS if (state.path / name).exists()]:
            raise StateError(
                f"{state.path} still holds {', '.join(logs)}: an earlier state.db's latest "
                "commits, which a new state.db would destroy. Move the whole state folder aside "
                "first."
            )
        state.path.mkdir(parents=True, exist_ok=True)
        state.locks_dir.mkdir(exist_ok=True)
        try:
            with closing(state._connect("rwc")) as conn:  # the one place a state.db is made
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA journal_mode=WAL")
                with conn:
                    conn.executescript(SCHEMA)
                    conn.executemany(
                        "INSERT INTO meta (key, value) VALUES (?, ?)",
                        [
                            ("schema_version", "1"),
                            ("archive_uuid", archive_uuid),
                            ("writer_id", writer_id),
                        ],
                    )
                state._upgrade(conn)
        except sqlite3.IntegrityError:  # another init got there between the check and the insert
            raise StateError(_already(state.db_path)) from None
        except sqlite3.DatabaseError as exc:
            raise StateError(f"{state.db_path} could not be created ({exc})") from None
        return state

    @classmethod
    def open(cls, path: Path) -> State:
        state = cls(path)
        if not state.db_path.is_file():
            raise StateError(
                f"no state.db in {state.path}, so this machine is not the archive's writer. "
                "`recordings init` creates it, once, on the writer."
            )
        try:
            with state.db() as db:
                state._version(db)  # refuse a newer state.db before changing anything in it
                db.executescript(SCHEMA)
                state._upgrade(db)
        except sqlite3.DatabaseError as exc:
            raise StateError(unreadable(state.db_path, exc)) from None
        state.locks_dir.mkdir(exist_ok=True)
        return state

    @classmethod
    def read_only(cls, path: Path) -> State | None:
        """For readers (Status, doctor). It never creates, migrates or writes anything, and
        returns None when there is no state.db to read.

        state.db is in WAL mode, so a reader needs the -wal and -shm files: it finds them
        while a writer has the database open, and otherwise creates them, so the folder must
        be writable by the reader (Task 14 mounts /state read-write in `web`). In a read-only
        folder with no writer connected it fails with "attempt to write a readonly database".
        Never use immutable=1: it ignores the WAL and reads stale data."""
        state = cls(path)
        if not state.db_path.is_file():
            return None
        state._read_only = True
        return state

    def _connect(self, mode: str) -> sqlite3.Connection:
        """A URI with an explicit mode: `rw` never creates a missing file, as a plain path would."""
        uri = f"{self.db_path.absolute().as_uri()}?mode={mode}"
        return sqlite3.connect(uri, uri=True, timeout=30)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """One connection per use: committed on success, rolled back on error, always closed.

        It opens an existing state.db only: a missing one is an error, never a new, empty file.
        """
        with closing(self._connect(self._mode)) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn

    # ---- the schema version: refuse a newer state.db, migrate an older one ------------------
    def _version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        try:
            version = int(row[0])
        except (TypeError, ValueError):
            raise StateError(
                f"{self.db_path} has no readable schema_version, so it is damaged: {RESTORE_HINT}"
            ) from None
        if version < 1:
            raise StateError(
                f"{self.db_path} has schema version {version}, so it is damaged: {RESTORE_HINT}"
            )
        if version > SCHEMA_VERSION:
            raise StateError(
                f"{self.db_path} is at schema version {version}, newer than this release's "
                f"{SCHEMA_VERSION}: a newer release wrote it, and this one can't read it. Deploy "
                "that release again."
            )
        return version

    def _upgrade(self, conn: sqlite3.Connection) -> None:
        """Run the numbered migrations this state.db hasn't had, in order, in one transaction."""
        if self._version(conn) == SCHEMA_VERSION:
            return
        with conn:
            conn.execute("BEGIN IMMEDIATE")  # one process migrates; one that waited finds it done
            for n in range(self._version(conn) + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[n]:
                    conn.execute(statement)
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
            )

    def meta(self, key: str) -> str | None:
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db() as db:
            db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- revisions: the merge bases (§6.4), in two phases so a crash is recognisable --------
    def _revision(self, sql: str, args: tuple) -> Revision | None:
        with self.db() as db:
            row = db.execute(
                "SELECT id, file, rev, sha256, body, origin, committed FROM revisions " + sql, args
            ).fetchone()
        return Revision(*row[:6], committed=bool(row[6])) if row else None

    def last_committed(self, file: str) -> Revision | None:
        return self._revision("WHERE file = ? AND committed = 1 ORDER BY id DESC LIMIT 1", (file,))

    def pending(self, file: str) -> Revision | None:
        return self._revision("WHERE file = ? AND committed = 0 ORDER BY id DESC LIMIT 1", (file,))

    def begin(self, file: str, rev: int, sha256: str, body: bytes, origin: str) -> int:
        with self.db() as db:
            cur = db.execute(
                "INSERT INTO revisions (file, rev, sha256, body, origin, committed, written_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (file, rev, sha256, body, origin, _now()),
            )
            return int(cur.lastrowid)

    def commit(self, revision_id: int) -> None:
        with self.db() as db:
            (file,) = db.execute(
                "SELECT file FROM revisions WHERE id = ?", (revision_id,)
            ).fetchone()
            db.execute("UPDATE revisions SET committed = 1 WHERE id = ?", (revision_id,))
            db.execute(
                "DELETE FROM revisions WHERE file = ? AND committed = 1 AND id NOT IN "
                "(SELECT id FROM revisions WHERE file = ? AND committed = 1 "
                " ORDER BY id DESC LIMIT ?)",
                (file, file, KEEP_REVISIONS),
            )

    def discard(self, revision_id: int) -> None:
        with self.db() as db:
            db.execute("DELETE FROM revisions WHERE id = ? AND committed = 0", (revision_id,))

    def adopt(self, file: str, rev: int, sha256: str, body: bytes) -> int:
        revision_id = self.begin(file, rev, sha256, body, "adopted")
        self.commit(revision_id)
        return revision_id

    # ---- flags: files that need attention (until stage 3's UI shows them) -----------------
    def flag(self, file: str, kind: str, message: str) -> None:
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO flags (file, kind, message, at) VALUES (?, ?, ?, ?)",
                (file, kind, message, _now()),
            )

    def clear_flags(self, file: str) -> None:
        with self.db() as db:
            db.execute("DELETE FROM flags WHERE file = ?", (file,))

    def flags(self) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT file, kind, message, at FROM flags ORDER BY file, kind"
            ).fetchall()
        return [{"file": f, "kind": k, "message": m, "at": a} for f, k, m, a in rows]

    def committed_files(self) -> list[str]:
        """Every recording.json the app has committed a revision of."""
        with self.db() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT file FROM revisions WHERE committed = 1 ORDER BY file"
                )
            ]

    def forget(self, file: str) -> None:
        """Drop everything state.db remembers about a file that was removed on purpose: its
        revisions and flags, and its recording's Plaud rows (2b spec §9.5): its checks, markers
        and events, and the title hash, duration and change marker on the listing rows of its
        Plaud IDs, which are kept only while it is here. Its other listing state (kept out,
        released, parked) stays, keyed by the Plaud ID."""
        recording_id = PurePosixPath(file).parent.name
        with self.db() as db:
            db.execute("DELETE FROM revisions WHERE file = ?", (file,))
            db.execute("DELETE FROM flags WHERE file = ?", (file,))
            plaud_ids = db.execute(
                "SELECT canonical_plaud_id FROM plaud_private_checked WHERE recording_id = ?",
                (recording_id,),
            ).fetchall()
            db.executemany(
                "UPDATE plaud_listing SET title_sha256 = NULL, duration_ms = NULL, "
                "change_marker = NULL WHERE canonical_plaud_id = ?",
                plaud_ids,
            )
            for table in ("plaud_checks", "plaud_private_checked", "plaud_events"):
                db.execute(f"DELETE FROM {table} WHERE recording_id = ?", (recording_id,))

    # ---- index updates not yet made: a crash between a write and its index update ---------
    def mark_index_pending(self, recording_id: str) -> None:
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO index_pending (recording_id) VALUES (?)", (recording_id,)
            )

    def clear_index_pending(self, recording_id: str) -> None:
        with self.db() as db:
            db.execute("DELETE FROM index_pending WHERE recording_id = ?", (recording_id,))

    def index_pending(self) -> list[str]:
        with self.db() as db:
            return [
                r[0]
                for r in db.execute("SELECT recording_id FROM index_pending ORDER BY recording_id")
            ]

    # ---- operations: each run of a scheduled job (§14, §15.1); Status reads these ----------
    def record_run(
        self, job: str, started_at: datetime, finished_at: datetime, ok: bool, detail: str
    ) -> None:
        with self.db() as db:
            db.execute(
                "INSERT INTO ops_runs (job, started_at, finished_at, ok, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (job, started_at.isoformat(), finished_at.isoformat(), int(ok), detail),
            )

    def last_run(self, job: str, *, ok: bool | None = None) -> Run | None:
        sql = "SELECT job, started_at, finished_at, ok, detail FROM ops_runs WHERE job = ?"
        args: tuple = (job,)
        if ok is not None:
            sql, args = sql + " AND ok = ?", (job, int(ok))
        with self.db() as db:
            row = db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        return Run(
            row[0],
            datetime.fromisoformat(row[1]),
            datetime.fromisoformat(row[2]),
            bool(row[3]),
            row[4],
        )

    # ---- alert state: one alert per failure, a daily reminder, and "recovered" ---------------
    def alert_state(self, key: str) -> tuple[bool, datetime | None]:
        with self.db() as db:
            row = db.execute(
                "SELECT failing, last_sent_at FROM alerts WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return False, None
        return bool(row[0]), datetime.fromisoformat(row[1]) if row[1] else None

    def set_alert_state(self, key: str, *, failing: bool, last_sent_at: datetime | None) -> None:
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO alerts (key, failing, last_sent_at) VALUES (?, ?, ?)",
                (key, int(failing), last_sent_at.isoformat() if last_sent_at else None),
            )

    # ---- ops_runs retention: the loop trims it once a day (ops._trim_runs) ---------------------
    def prune_runs(self, before: datetime) -> int:
        """Delete the runs that finished before `before`, except each job's newest run and its
        newest good one (what Status and the schedule read, however long a job has been failing).
        Returns how many rows went."""
        with self.db() as db:
            return db.execute(
                "DELETE FROM ops_runs WHERE datetime(finished_at) < datetime(?) "
                "AND id NOT IN (SELECT MAX(id) FROM ops_runs WHERE ok = 1 GROUP BY job) "
                "AND id NOT IN (SELECT MAX(id) FROM ops_runs GROUP BY job)",
                (before.isoformat(),),
            ).rowcount

    def backup_to(self, dest: Path) -> Path:
        """A consistent copy of state.db, with SQLite's own backup command (§15.1), even while
        another process writes it. Replaced atomically, so a half copy is never backed up. The
        source opens with mode=rw, or ro through read_only() (_connect), so a missing state.db is
        never created here."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_name(dest)
        try:
            with closing(self._connect(self._mode)) as src, closing(sqlite3.connect(tmp)) as dst:
                src.backup(dst)
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        fsync_dir(dest.parent)
        return dest
