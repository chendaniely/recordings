"""Operational state that can't be derived (spec §6.8): `state.db`, and the lock files beside it.

It lives in `[state] path` (`/srv/recordings/state`), outside the archive, so the archive's backups
and the mirror never copy it. It must be on a local disk: `flock` and SQLite's WAL both break over
NFS and SMB. The backup copies `state.db` on its own, with SQLite's backup command (§15.1). Reads
never need it: the Mac's read-only mirror has none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
# The version-1 tables. Later tasks in stage 2a append theirs here. Every statement is
# CREATE ... IF NOT EXISTS, so opening a state.db made earlier in 2a adds what is missing.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Every change after version 1, keyed by the version it makes: MIGRATIONS[2] takes a state.db
# from version 1 to 2. CREATE TABLE IF NOT EXISTS can't add a column, so a later stage that changes
# a table adds its statements here and raises SCHEMA_VERSION to match. Never edit a shipped one.
MIGRATIONS: dict[int, tuple[str, ...]] = {}

RESTORE_HINT = (
    "restore state.db from the backup (runbook: Restoring for real). If no backup copy is usable, "
    "stop the app, move the whole state folder aside (never state.db alone: its -wal file holds "
    "the latest commits), and run `recordings init --adopt`.")
# SQLite's logs beside state.db hold commits not yet in it. SQLite replays a -wal over whatever
# state.db is next put beside it, and deletes one it finds beside a new, empty state.db.
LOGS = ("state.db-wal", "state.db-journal")
# SQLite's codes for a sound state.db that can't be reached now: a mode, an owner or a missing
# mount (CANTOPEN), a read-only folder, a lock held too long, a full disk, an I/O error.
_NOT_DAMAGE = ("SQLITE_CANTOPEN", "SQLITE_READONLY", "SQLITE_BUSY", "SQLITE_LOCKED",
               "SQLITE_FULL", "SQLITE_IOERR", "SQLITE_PERM", "SQLITE_NOMEM")


def unreadable(db_path: Path, exc: sqlite3.Error) -> str:
    """Why state.db can't be used, from a fixed template with SQLite's code, never its message.
    Only damage gets the way back: moving a sound state.db aside throws away the queue and every
    merge base."""
    code = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
    if code.startswith(_NOT_DAMAGE):
        return (f"{db_path} can't be opened now ({code}). That is not damage, so leave it where it "
                "is: check that the state folder is mounted, owned by this user, writable and not "
                "full, and that no other process holds it, then try again.")
    return f"{db_path} can't be read ({code}). If it is damaged, {RESTORE_HINT}"


class StateError(RuntimeError):
    pass


def _already(db_path: Path) -> str:
    return f"{db_path} already exists: `recordings init` runs once per archive"


class State:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

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
                "first.")
        state.path.mkdir(parents=True, exist_ok=True)
        state.locks_dir.mkdir(exist_ok=True)
        try:
            with closing(state._connect("rwc")) as conn:  # the one place a state.db is made
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA journal_mode=WAL")
                with conn:
                    conn.executescript(SCHEMA)
                    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                        ("schema_version", "1"),
                        ("archive_uuid", archive_uuid),
                        ("writer_id", writer_id),
                    ])
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
                "`recordings init` creates it, once, on the writer.")
        try:
            with state.db() as db:
                state._version(db)  # refuse a newer state.db before changing anything in it
                db.executescript(SCHEMA)
                state._upgrade(db)
        except sqlite3.DatabaseError as exc:
            raise StateError(unreadable(state.db_path, exc)) from None
        state.locks_dir.mkdir(exist_ok=True)
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
        with closing(self._connect("rw")) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn

    # ---- the schema version: refuse a newer state.db, migrate an older one ------------------
    def _version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        try:
            version = int(row[0])
        except (TypeError, ValueError):
            raise StateError(f"{self.db_path} has no readable schema_version, so it is damaged: "
                             f"{RESTORE_HINT}") from None
        if version < 1:
            raise StateError(f"{self.db_path} has schema version {version}, so it is damaged: "
                             f"{RESTORE_HINT}")
        if version > SCHEMA_VERSION:
            raise StateError(
                f"{self.db_path} is at schema version {version}, newer than this release's "
                f"{SCHEMA_VERSION}: a newer release wrote it, and this one can't read it. Deploy "
                "that release again.")
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
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                         (str(SCHEMA_VERSION),))

    def meta(self, key: str) -> str | None:
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db() as db:
            db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
