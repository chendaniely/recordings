"""The Plaud cache, `<state>/plaud-cache.db` (stage 2b spec §9.5): a separate SQLite file, derived
and rebuilt by the next listing, which no backup copies (the backup takes `state.db` and
`extra_paths` only, and `backup.never_backed_up` refuses an `extra_paths` entry that would carry
it, so doctor reports one and the backup refuses it).

It holds what the Sync screen shows of recordings not yet here, and nothing else:
- **the newest compare's rows** (`compare_rows`): for each listed ID not here, its bucket and
  state, its date, time and duration, and a title only for one waiting or missing that may show
  one (`title_allowed`, and the compare's own rules: none under review after a rebuild, none
  while a held listing shows Plaud's IDs changed);
- **the consent-listed and kept-out IDs' dates and durations** (`keyed_out`), a recording here that
  is consent-listed or kept out late among them unless Dan pressed Keep it here, kept until an ID
  has been absent from the listings for 30 days, for §11.4 step 6's re-key match.

**Never a title of a consent-listed recording**, and none at all during a consent stop: the
compare writes none (`plaud.compare`), and the worker's tick nulls any the consent list or a
keep-out word has caught since (`purge_titles`, the outline's P1). A reader can apply the same
rule again (`title_allowed`) before a title reaches a browser (P13). So no title of a recording Dan
then keeps out reaches any backup, the `pre-import` one included. Only the Sync screen reads it,
through `Cache.read_only`.

**Release's preview** (§12.2, area A4): the one title Release's question shows, in
`release_preview`, keyed by the `release-preview` request's own `request_id` (never by anything
the browser sends: the Plaud ID comes from the request's row, P8). The worker writes it; the web
app reads and deletes it in one writable transaction when it answers the asking session
(`take_preview`: a read-only cache can't delete, BD1), so it is shown once. One left unanswered
goes after PREVIEW_KEPT (10 minutes), and the worker's tick deletes any whose ID the consent list
names, or whose title a keep-out word matches, since it was written (`purge_titles`).

Not WAL: one writer (the worker or the CLI, under `plaud`), and readers that open it `mode=ro`.

**`secure_delete` is on for every writable connection** (Dan's answer to question 3, Phase 1's Q&A
§9), so a title nulled or deleted is overwritten with zeros, not left in the file's free space. The
setting belongs to the connection, so both places that open the file for writing set it, before
their first write. A read-only connection deletes nothing and needs none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from recordings.plaud.keepout import ConsentList, keep_out_match
from recordings.plaud.when import parse_stamp, utc_iso
from recordings.refs import canonical_plaud_id

CACHE_NAME = "plaud-cache.db"
KEYED_OUT_KEPT = timedelta(days=30)  # §9.5: until absent for 30 days
PREVIEW_KEPT = timedelta(minutes=10)  # §9.5: a Release preview no one answered goes after this
# ON, not FAST: SQLite's FAST leaves traces on freelist pages.
SECURE_DELETE = "PRAGMA secure_delete=ON"
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS compare_rows (
  canonical_plaud_id TEXT PRIMARY KEY, listed_id TEXT NOT NULL,
  bucket TEXT NOT NULL,                  -- waiting | missing | excluded
  state TEXT,                            -- waiting or missing: a §15.1 state phrase
  excluded_by TEXT,                      -- excluded: consent | title | was-title | rekeyed
  start_at TEXT, duration_ms INTEGER,
  title TEXT,                            -- waiting or missing only, and only when allowed
  arrives_private INTEGER NOT NULL DEFAULT 0, review INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS keyed_out (   -- §11.4 step 6: consent-listed and kept-out IDs, here too
  canonical_plaud_id TEXT PRIMARY KEY, kind TEXT NOT NULL, start_at TEXT, duration_ms INTEGER,
  last_listed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS release_preview (  -- §12.2: Release's question, shown once
  request_id TEXT PRIMARY KEY, canonical_plaud_id TEXT NOT NULL, title TEXT NOT NULL,
  start_at TEXT, duration_ms INTEGER, written_at TEXT NOT NULL);
"""
_COMPARE = (
    "canonical_plaud_id",
    "listed_id",
    "bucket",
    "state",
    "excluded_by",
    "start_at",
    "duration_ms",
    "title",
    "arrives_private",
    "review",
)


@dataclass(frozen=True)
class CompareRow:
    canonical_plaud_id: str
    listed_id: str
    bucket: str  # waiting | missing | excluded
    state: str | None
    excluded_by: str | None
    start_at: datetime | None
    duration_ms: int | None
    title: str | None
    arrives_private: bool
    review: bool


@dataclass(frozen=True)
class Preview:
    """Release's question (§12.2): the title, and the date and duration it is shown with."""

    canonical_plaud_id: str
    title: str
    start_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True)
class KeyedOut:
    canonical_plaud_id: str
    kind: str  # "consent" | "title" | "was-title" | "rekeyed"
    start_at: datetime | None
    duration_ms: int | None


def cache_path(state_dir: Path) -> Path:
    return Path(state_dir) / CACHE_NAME


def title_allowed(
    plaud_id: str,
    title: str | None,
    *,
    consent: ConsentList | None,
    words: Sequence[str] | None,
) -> bool:
    """Whether a title of a recording not here may be stored or shown now (§9.5, §12.1, §12.2):
    never during a consent stop (or before the list was read), never for a consent-listed ID,
    never one a keep-out word matches, and never while the keep-out words can't be read (None)."""
    if title is None or consent is None or consent.stop is not None or words is None:
        return False
    if canonical_plaud_id(plaud_id) in consent.ids:
        return False
    return keep_out_match(title, words) is None


def _preview_allowed(
    plaud_id: str, title: str | None, *, consent: ConsentList | None, words: Sequence[str] | None
) -> bool:
    """A Release preview stays while its ID isn't consent-listed (and the list reads) and its
    title carries no keep-out word."""
    return title_allowed(plaud_id, title, consent=consent, words=words)


def take_preview(state_dir: Path, request_id: str, *, now: datetime) -> Preview | None:
    """Release's preview for the session that asked (the web app, §12.2; BD1, P6, P8): read
    and delete in one writable transaction, keyed by the request's own `request_id`, so it is
    shown once. None when there is none, it is older than PREVIEW_KEPT, or no cache exists: the
    file is opened `mode=rw`, never created here."""
    path = cache_path(state_dir)
    if not path.is_file():
        return None
    uri = f"{path.absolute().as_uri()}?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn, conn:
        conn.execute(SECURE_DELETE).fetchone()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT canonical_plaud_id, title, start_at, duration_ms, written_at "
                "FROM release_preview WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        except sqlite3.OperationalError:  # no such table: no Release has been asked yet
            return None
        if row is None:
            return None
        conn.execute("DELETE FROM release_preview WHERE request_id = ?", (request_id,))
    written = parse_stamp(row[4])
    if written is None or now - written > PREVIEW_KEPT:
        return None
    return Preview(row[0], row[1], parse_stamp(row[2]), row[3])


def _row(r: tuple) -> CompareRow:
    return CompareRow(
        canonical_plaud_id=r[0],
        listed_id=r[1],
        bucket=r[2],
        state=r[3],
        excluded_by=r[4],
        start_at=parse_stamp(r[5]),
        duration_ms=r[6],
        title=r[7],
        arrives_private=bool(r[8]),
        review=bool(r[9]),
    )


class Cache:
    def __init__(self, state_dir: Path) -> None:
        self.path = cache_path(state_dir)
        self._read_only = False

    @classmethod
    def read_only(cls, state_dir: Path) -> Cache | None:
        """For the Sync screen's views: never creates or writes the file. None when it isn't there
        (no listing has run yet)."""
        cache = cls(state_dir)
        if not cache.path.is_file():
            return None
        cache._read_only = True
        return cache

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """One connection and transaction. A writable cache makes its file and schema on first
        use; a read-only one opens with mode=ro, and any write through it fails."""
        mode = "ro" if self._read_only else "rwc"
        uri = f"{self.path.absolute().as_uri()}?mode={mode}"
        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
            if not self._read_only:
                conn.execute(SECURE_DELETE).fetchone()
                conn.executescript(CACHE_SCHEMA)
            with conn:
                yield conn

    # ---- the newest compare's rows ---------------------------------------------------------
    def replace_compare(self, rows: Iterable[CompareRow], *, at: datetime) -> None:
        """The newest compare's rows replace the last ones, in one transaction."""
        values = [
            (
                r.canonical_plaud_id,
                r.listed_id,
                r.bucket,
                r.state,
                r.excluded_by,
                utc_iso(r.start_at) if r.start_at is not None else None,
                r.duration_ms,
                r.title,
                int(r.arrives_private),
                int(r.review),
            )
            for r in rows
        ]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM compare_rows")
            db.executemany(
                f"INSERT INTO compare_rows ({', '.join(_COMPARE)}) VALUES "
                f"({', '.join('?' for _ in _COMPARE)})",
                values,
            )
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('compare_at', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (utc_iso(at),),
            )

    def compare_rows(self) -> list[CompareRow]:
        """The newest compare's rows, newest recording first."""
        with self.db() as db:
            rows = db.execute(
                f"SELECT {', '.join(_COMPARE)} FROM compare_rows "
                "ORDER BY start_at IS NULL, start_at DESC, canonical_plaud_id"
            ).fetchall()
        return [_row(r) for r in rows]

    def compare_at(self) -> datetime | None:
        """When the listing behind the rows began."""
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'compare_at'").fetchone()
        return parse_stamp(row[0]) if row else None

    def purge_titles(self, *, consent: ConsentList | None, words: Sequence[str] | None) -> int:
        """The worker's tick (the outline's P1): null every title `title_allowed` no longer
        allows (a consent stop, an ID the list names since the compare, a keep-out word added
        since, words that can't be read), and delete every Release preview the consent list or a
        keep-out word now catches. Returns how many; writes nothing when there are none."""
        with self.db() as db:
            previews = db.execute(
                "SELECT request_id, canonical_plaud_id, title FROM release_preview"
            ).fetchall()
            caught = [
                (request_id,)
                for request_id, pid, title in previews
                if not _preview_allowed(pid, title, consent=consent, words=words)
            ]
            if caught:
                db.executemany("DELETE FROM release_preview WHERE request_id = ?", caught)
            titled = db.execute(
                "SELECT canonical_plaud_id, title FROM compare_rows WHERE title IS NOT NULL"
            ).fetchall()
            gone = [
                (pid,)
                for pid, title in titled
                if not title_allowed(pid, title, consent=consent, words=words)
            ]
            if gone:
                db.executemany(
                    "UPDATE compare_rows SET title = NULL WHERE canonical_plaud_id = ?", gone
                )
        return len(gone) + len(caught)

    # ---- Release's preview (§12.2) ----------------------------------------------------------
    def put_preview(self, request_id: str, preview: Preview, *, now: datetime) -> None:
        """The worker's answer to a `release-preview` request, keyed by its `request_id`; and
        any preview older than PREVIEW_KEPT goes."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM release_preview WHERE written_at < ?",
                (utc_iso(now - PREVIEW_KEPT),),
            )
            db.execute(
                "INSERT INTO release_preview (request_id, canonical_plaud_id, title, start_at, "
                "duration_ms, written_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (request_id) DO "
                "UPDATE SET canonical_plaud_id = excluded.canonical_plaud_id, title = "
                "excluded.title, start_at = excluded.start_at, duration_ms = "
                "excluded.duration_ms, written_at = excluded.written_at",
                (
                    request_id,
                    canonical_plaud_id(preview.canonical_plaud_id),
                    preview.title,
                    utc_iso(preview.start_at) if preview.start_at is not None else None,
                    preview.duration_ms,
                    utc_iso(now),
                ),
            )

    def expire_previews(self, *, now: datetime) -> int:
        """Delete the previews no one answered within PREVIEW_KEPT. Returns how many."""
        if not self.path.is_file():
            return 0
        with self.db() as db:
            return db.execute(
                "DELETE FROM release_preview WHERE written_at < ?", (utc_iso(now - PREVIEW_KEPT),)
            ).rowcount

    # ---- consent-listed and kept-out IDs, for the re-key match (§11.4 step 6) ---------------
    def keyed_out(self) -> dict[str, KeyedOut]:
        with self.db() as db:
            rows = db.execute(
                "SELECT canonical_plaud_id, kind, start_at, duration_ms FROM keyed_out"
            ).fetchall()
        return {r[0]: KeyedOut(r[0], r[1], parse_stamp(r[2]), r[3]) for r in rows}

    def remember_keyed_out(self, listed: Iterable[KeyedOut], *, now: datetime) -> None:
        """Each consent-listed or kept-out ID this listing listed, with its date and duration, seen
        `now`; then those absent for 30 days are forgotten."""
        values = [
            (
                k.canonical_plaud_id,
                k.kind,
                utc_iso(k.start_at) if k.start_at is not None else None,
                k.duration_ms,
                utc_iso(now),
            )
            for k in listed
        ]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(
                "INSERT INTO keyed_out (canonical_plaud_id, kind, start_at, duration_ms, "
                "last_listed_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (canonical_plaud_id) DO "
                "UPDATE SET kind = excluded.kind, start_at = excluded.start_at, "
                "duration_ms = excluded.duration_ms, last_listed_at = excluded.last_listed_at",
                values,
            )
            db.execute(
                "DELETE FROM keyed_out WHERE last_listed_at < ?", (utc_iso(now - KEYED_OUT_KEPT),)
            )
