"""The Plaud sync's tables in `state.db` (stage 2b spec §9.5), read and written through `PlaudDB`.

- **Keyed by canonical Plaud ID.** `plaud_listing` is keyed by `canonical_plaud_id`, and so is
  every Plaud ID another table holds. The ID as listed is a column that follows the newest form,
  so a flip between `of_` and bare hex carries every state over (C5.33).
- **Created in phase 1 with every column the spec names** (2b phase 1 plan, decision 3): `state.db`
  gains tables only, with no `SCHEMA_VERSION` bump, so `make rollback` keeps working, and `CREATE
  TABLE IF NOT EXISTS` can't add a column later.
- **No title.** A title hash only, and only while its recording is here; never a title (§9.5).
- **Times** are stored as UTC ISO 8601, so they compare as text.
- **Reading a table that isn't there** (a 2a `state.db` that only a reader has opened) raises
  `NoSyncYet`, which Status shows as "No sync has run yet", never as damage (§9.5).
- **`plaud_state`** holds one JSON value per key: the `K_*` keys below, and the blockers as
  `blocker:<name>` rows of `{"since", "detail"}` (decision 17).
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from recordings.plaud.when import parse_stamp, utc_iso
from recordings.refs import canonical_plaud_id
from recordings.state import State

NO_SYNC_YET = "No sync has run yet"
KEPT_OUT_LATE = "kept-out-late"
CONSENT_LATE = "consent-late"
PRIVATE_UNWRITTEN = "private-unwritten"  # an event kind and a blocker
KEPT_HERE_EVENT = "kept-here"
EVENT_KINDS = (
    "imported",
    "arrived-private",
    "updated",
    "retagged-private",
    KEPT_OUT_LATE,
    CONSENT_LATE,
    PRIVATE_UNWRITTEN,
    KEPT_HERE_EVENT,
    "gone",
    "released",
    "rekeyed",
)
FLAG_KINDS = (KEPT_OUT_LATE, CONSENT_LATE)
BLOCKERS = ("sign-in", "consent", "hold", "breaker", PRIVATE_UNWRITTEN)
BLOCKER_PREFIX = "blocker:"
CHECK_LOG_KEPT = timedelta(days=7)
# Failed checks in a row that put a recording on the failing list (§10.2, §14.6): Status's "fails
# every check" line (§15.2), and where Park appears.
FAILING_AFTER = 3
EVENTS_KEPT = timedelta(days=90)  # open flags are kept until resolved

# plaud_state keys (decision 17)
K_LAST_GOOD_LISTING = "last_good_listing"
K_HOLD = "hold"
K_BREAKER = "breaker"
K_SIGN_IN = "sign_in"
K_FAILING = "failing"
K_LAST_PING = "last_ping"
K_LAST_STARTED = "last_started"
K_LAST_ENDED = "last_ended"
K_LAST_TURN = "last_turn"
K_CATCH_UP_DONE = "catch_up_done_at"
K_KEEP_OUT_WORDS = "keep_out_words"
K_PATTERNS = "patterns"
K_PATTERNS_PASS = "patterns_pass"
K_CONSENT = "consent"  # {"read_at": iso, "count": int, ...}: the IDs' count, never the IDs
K_FULL = "full_sync"
# {"until": iso, "at": iso}: a 429 whose wait passed the run's budget ended the run at `at`, and
# scheduled turns make no Plaud call before `until` (§9.7 case 2, §10.2).
K_RATE_LIMIT = "rate_limit"
# The newest compare (§11.3; plaud.compare): the equations, the consent line and the census, with
# counts only, never a title or an ID list. Written by every complete listing, a Compare's too.
K_COMPARE = "compare"
# A small shrink waiting to settle (§11.4 step 5; plaud.hold): {"absent": [recording IDs], "since"}.
K_SMALL_SHRINK = "small_shrink"
# The snapshot mark (§11.6): committed in its own transaction before every 2b snapshot is
# published, by every path (an import's `Writer.add` included), and when a rebuild is recorded.
# {"first_at", "last_at", "count", "by"}: one row, so "mark rows exist" is "this key exists".
K_SNAPSHOT_MARK = "snapshot_mark"
# A rebuilt or restored state.db (§11.6): {"at": iso, "by": "detected" | "plaud review"}. Every
# listed ID not here nor excluded, made before `at`, needs Dan's review when it is first listed.
K_REBUILT = "rebuilt_at"
# plaud_listed's two listings (§11.4): the last good one, and a held one.
LISTED_GOOD = "good"
LISTED_HELD = "held"


def judged_key(recording_id: str) -> str:
    """The plaud_state key of the snapshots a recording's markers have judged (§13.2)."""
    return f"judged:{recording_id}"


UNCHANGED = object()  # what a PlaudDB.update callback returns to write nothing


class NoSyncYet(Exception):
    """A 2b table isn't in this state.db yet: no 2b process has opened it as a writer."""


def no_such_table(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


@dataclass(frozen=True)
class Marker:
    canonical_plaud_id: str
    title_sha256: str
    patterns_version: int
    # The sha256 of every snapshot of this ID the marker has judged (`sources[].sha256`, already in
    # recording.json), sorted; kept in plaud_state under `judged:<recording_id>` (§13.2).
    judged: tuple[str, ...] = ()


@dataclass(frozen=True)
class ListingRow:
    canonical_plaud_id: str
    listed_id: str
    first_seen: datetime
    last_seen: datetime
    absent_since: datetime | None
    kept_out_at: datetime | None
    kept_out_word: str | None
    rekey_of: str | None
    released_at: datetime | None
    needs_review: bool
    import_failures: int
    next_import_at: datetime | None
    parked_at: datetime | None
    title_sha256: str | None
    duration_ms: int | None
    change_marker: str | None


@dataclass(frozen=True)
class ListingEntry:
    """One listed ID, as a complete listing writes its plaud_listing row (§9.5, §11.3)."""

    canonical_plaud_id: str
    listed_id: str  # as this listing spelt it: the row follows the newest form
    title_sha256: str | None  # only for an ID here, never one kept out or consent-listed
    duration_ms: int | None  # likewise: only while its recording is here
    kept_out_word: str | None = None  # newly kept out by its title (§12.2): the word
    rekey_of: str | None = None  # newly matched to an absent excluded ID (§11.4 step 6), or "*"


@dataclass(frozen=True)
class CheckRow:
    recording_id: str
    last_checked_at: datetime | None
    last_changed_at: datetime | None
    next_check_at: datetime | None
    interval_seconds: int | None
    fast_checks_left: int
    failures: int
    failing_since: datetime | None
    settled: bool
    baseline_pending: bool
    held_removals: tuple
    parked_at: datetime | None
    degraded: bool
    gone_since: datetime | None
    kept_here_at: datetime | None


@dataclass(frozen=True)
class Event:
    id: int
    at: datetime
    kind: str
    recording_id: str | None
    canonical_plaud_id: str | None
    detail: dict
    open: bool
    resolved_at: datetime | None


_TIMES = frozenset(
    {
        "first_seen",
        "last_seen",
        "absent_since",
        "kept_out_at",
        "released_at",
        "next_import_at",
        "parked_at",
        "last_checked_at",
        "last_changed_at",
        "next_check_at",
        "failing_since",
        "gone_since",
        "kept_here_at",
        "at",
        "resolved_at",
    }
)
_BOOLS = frozenset({"needs_review", "settled", "baseline_pending", "degraded", "open"})
_JSON = frozenset({"held_removals", "detail"})
_LISTING = tuple(f.name for f in dataclasses.fields(ListingRow))
_CHECKS = tuple(f.name for f in dataclasses.fields(CheckRow))
_EVENT = tuple(f.name for f in dataclasses.fields(Event))
# A listing row a write makes (in phase 1, a late keep-out of a recording here, whose ID no
# listing has shown) is dated by the first of these times the write sets.
_DATING = (
    "first_seen",
    "last_seen",
    "kept_out_at",
    "parked_at",
    "released_at",
    "next_import_at",
    "absent_since",
)


# A listing keeps out (again) an ID its title's word catches now: one never kept out, or one
# Release had let in (released after it was last kept out). Kept out stays kept out otherwise:
# the first time and word stay (§12.2).
_KEEP_AGAIN = (
    "excluded.kept_out_at IS NOT NULL AND (plaud_listing.kept_out_at IS NULL OR "
    "(plaud_listing.released_at IS NOT NULL AND "
    "plaud_listing.released_at >= plaud_listing.kept_out_at))"
)


def _encode(name: str, value: Any) -> Any:
    if name in _TIMES:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"{name} takes a datetime")
        return utc_iso(value)
    if name.endswith("_sha256") and value is not None and not _is_sha256(value):
        raise ValueError(f"{name} must be 64 lowercase hex characters (a hash, never a title)")
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list | tuple | dict):
        return json.dumps(list(value) if isinstance(value, tuple) else value, ensure_ascii=False)
    return value


def _decode(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _TIMES:
        return parse_stamp(value)
    if name in _BOOLS:
        return bool(value)
    if name == "held_removals":
        return tuple(json.loads(value))
    if name in _JSON:
        return json.loads(value)
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _row(cls: type, cols: tuple[str, ...], row: tuple) -> Any:
    return cls(*(_decode(n, v) for n, v in zip(cols, row, strict=True)))


def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _refuse_unknown_columns(given: Mapping[str, Any], allowed: tuple[str, ...], key: str) -> None:
    unknown = sorted(set(given) - set(allowed) | ({key} & set(given)))
    if unknown:
        raise ValueError(f"not a settable column: {', '.join(unknown)}")


class PlaudDB:
    def __init__(self, state: State) -> None:
        self.state = state

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One state.db connection and transaction; a missing 2b table is NoSyncYet."""
        try:
            with self.state.db() as db:
                yield db
        except sqlite3.OperationalError as exc:
            if no_such_table(exc):
                raise NoSyncYet(NO_SYNC_YET) from None
            raise

    # ---- plaud_state (values are JSON) --------------------------------------------------------
    def get(self, key: str) -> Any | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM plaud_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _put(db: sqlite3.Connection, key: str, value: Any, now: datetime) -> None:
        db.execute(
            "INSERT INTO plaud_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), utc_iso(now)),
        )

    def put(self, key: str, value: Any, *, now: datetime) -> None:
        with self._db() as db:
            self._put(db, key, value, now)

    def update(self, key: str, change: Callable[[Any | None], Any], *, now: datetime) -> Any:
        """Read, change and write one key in one transaction, so two writers can't lose each
        other's change. `change(old)` returns the new value, or `UNCHANGED` to write nothing, or
        raises to roll back. Returns what is stored after."""
        with self._db() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM plaud_state WHERE key = ?", (key,)).fetchone()
            old = json.loads(row[0]) if row else None
            new = change(old)
            if new is UNCHANGED:
                return old
            self._put(db, key, new, now)
            return new

    def put_many(self, values: Mapping[str, Any], *, now: datetime) -> None:
        """Every key in one transaction: an error part-way writes none of them."""
        with self._db() as db:
            for key, value in values.items():
                self._put(db, key, value, now)

    def delete(self, key: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM plaud_state WHERE key = ?", (key,))

    def with_prefix(self, prefix: str) -> dict[str, Any]:
        with self._db() as db:
            rows = db.execute(
                "SELECT key, value FROM plaud_state WHERE substr(key, 1, ?) = ? ORDER BY key",
                (len(prefix), prefix),
            ).fetchall()
        return {key: json.loads(value) for key, value in rows}

    # ---- plaud_listing, by any form of the ID ------------------------------------------------
    def listing(self, plaud_id: str) -> ListingRow | None:
        with self._db() as db:
            row = db.execute(
                f"SELECT {', '.join(_LISTING)} FROM plaud_listing WHERE canonical_plaud_id = ?",
                (canonical_plaud_id(plaud_id),),
            ).fetchone()
        return _row(ListingRow, _LISTING, row) if row else None

    def set_listing(self, plaud_id: str, **fields: Any) -> None:
        """Set plaud_listing columns (their names only: ValueError otherwise), on the row of the
        ID's canonical form. A missing row is created, with the ID as given, and dated by the
        first of the times being set (first_seen, last_seen, kept_out_at, parked_at, released_at,
        next_import_at, absent_since); with none of them, ValueError (decision 36)."""
        _refuse_unknown_columns(fields, _LISTING, "canonical_plaud_id")
        encoded = {k: _encode(k, v) for k, v in fields.items()}
        canonical = canonical_plaud_id(plaud_id)
        with self._db() as db:
            exists = db.execute(
                "SELECT 1 FROM plaud_listing WHERE canonical_plaud_id = ?", (canonical,)
            ).fetchone()
            if not exists:
                dated = next((encoded[k] for k in _DATING if encoded.get(k) is not None), None)
                if dated is None:
                    raise ValueError("no listing row for this ID, and no time to date a new one by")
                db.execute(
                    "INSERT INTO plaud_listing (canonical_plaud_id, listed_id, first_seen, "
                    "last_seen) VALUES (?, ?, ?, ?)",
                    (canonical, plaud_id, dated, dated),
                )
            if fields:
                db.execute(
                    f"UPDATE plaud_listing SET {', '.join(f'{k} = ?' for k in fields)} "
                    "WHERE canonical_plaud_id = ?",
                    (*encoded.values(), canonical),
                )

    def listing_rows(self) -> dict[str, ListingRow]:
        """Every plaud_listing row, by canonical ID: one read for a whole listing's compare."""
        with self._db() as db:
            rows = db.execute(f"SELECT {', '.join(_LISTING)} FROM plaud_listing").fetchall()
        return {row[0]: _row(ListingRow, _LISTING, row) for row in rows}

    def write_listing(self, entries: Iterable[ListingEntry], *, now: datetime) -> None:
        """A complete listing's rows, in one transaction (§9.5, §11.3): each listed ID's row made
        or brought up to date (the ID as listed, `last_seen`, no longer absent, and its title
        hash and duration, which only an ID here has); a newly kept-out ID dated `now` with its
        word, and a re-keyed one's match, each kept once set (kept out stays kept out, §12.2);
        then every row this listing didn't list, and not already absent, absent since `now`."""
        stamp = utc_iso(now)
        values = []
        for e in entries:
            if e.title_sha256 is not None and not _is_sha256(e.title_sha256):
                raise ValueError("title_sha256 must be 64 lowercase hex characters")
            kept = stamp if e.kept_out_word is not None else None
            values.append(
                (
                    canonical_plaud_id(e.canonical_plaud_id),
                    e.listed_id,
                    stamp,
                    stamp,
                    kept,
                    e.kept_out_word,
                    e.rekey_of,
                    e.title_sha256,
                    e.duration_ms,
                )
            )
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(
                "INSERT INTO plaud_listing (canonical_plaud_id, listed_id, first_seen, last_seen, "
                "kept_out_at, kept_out_word, rekey_of, title_sha256, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (canonical_plaud_id) DO UPDATE SET "
                "listed_id = excluded.listed_id, last_seen = excluded.last_seen, "
                "absent_since = NULL, "
                f"kept_out_word = CASE WHEN {_KEEP_AGAIN} "
                "THEN excluded.kept_out_word ELSE plaud_listing.kept_out_word END, "
                f"kept_out_at = CASE WHEN {_KEEP_AGAIN} "
                "THEN excluded.kept_out_at ELSE plaud_listing.kept_out_at END, "
                "rekey_of = COALESCE(plaud_listing.rekey_of, excluded.rekey_of), "
                "title_sha256 = excluded.title_sha256, duration_ms = excluded.duration_ms",
                values,
            )
            db.execute(
                "UPDATE plaud_listing SET absent_since = ? "
                "WHERE absent_since IS NULL AND last_seen != ?",
                (stamp, stamp),
            )

    # ---- plaud_listed: the last good listing, and a held one (§11.4) ----------------------------
    def listed(self, which: str) -> dict[str, str]:
        """The `which` listing's canonical IDs and forms. An empty one is also what no listing
        reads as: whether there is one at all is its plaud_state record (K_LAST_GOOD_LISTING,
        K_HOLD), written in the same transaction (`set_listed`)."""
        with self._db() as db:
            rows = db.execute(
                "SELECT canonical_plaud_id, form FROM plaud_listed WHERE listing = ?", (which,)
            ).fetchall()
        return dict(rows)

    def set_listed(
        self,
        which: str,
        forms: Mapping[str, str],
        *,
        put: Mapping[str, Any] | None = None,
        delete: Iterable[str] = (),
        now: datetime,
    ) -> None:
        """Replace the `which` listing's IDs, and put and delete plaud_state keys, in one
        transaction: a listing and its record never disagree."""
        if which not in (LISTED_GOOD, LISTED_HELD):
            raise ValueError(f"not a listing: {which}")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM plaud_listed WHERE listing = ?", (which,))
            db.executemany(
                "INSERT INTO plaud_listed (listing, canonical_plaud_id, form) VALUES (?, ?, ?)",
                [(which, pid, form) for pid, form in forms.items()],
            )
            self._apply(db, put, delete, now)

    def promote_held(
        self,
        *,
        put: Mapping[str, Any] | None = None,
        delete: Iterable[str] = (),
        now: datetime,
    ) -> None:
        """The held listing becomes the last good one (Accept, §11.4 step 4), with puts and
        deletes, in one transaction."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM plaud_listed WHERE listing = ?", (LISTED_GOOD,))
            db.execute(
                "UPDATE plaud_listed SET listing = ? WHERE listing = ?", (LISTED_GOOD, LISTED_HELD)
            )
            self._apply(db, put, delete, now)

    def _apply(
        self,
        db: sqlite3.Connection,
        put: Mapping[str, Any] | None,
        delete: Iterable[str],
        now: datetime,
    ) -> None:
        for key, value in (put or {}).items():
            self._put(db, key, value, now)
        for key in delete:
            db.execute("DELETE FROM plaud_state WHERE key = ?", (key,))

    # ---- gone from Plaud (§12.3) ---------------------------------------------------------------
    def mark_gone(self, recording_ids: Iterable[str], *, now: datetime) -> list[str]:
        """Each recording here that isn't gone yet is gone from Plaud since `now`, with a `gone`
        event; returns those newly marked. Nothing is deleted, ever."""
        wanted = sorted(set(recording_ids))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            already = {
                row[0]
                for row in db.execute(
                    "SELECT recording_id FROM plaud_checks WHERE gone_since IS NOT NULL"
                )
            }
            marked = [rid for rid in wanted if rid not in already]
            db.executemany(
                "INSERT INTO plaud_checks (recording_id, gone_since) VALUES (?, ?) "
                "ON CONFLICT (recording_id) DO UPDATE SET gone_since = excluded.gone_since",
                [(rid, utc_iso(now)) for rid in marked],
            )
            db.executemany(
                "INSERT INTO plaud_events (at, kind, recording_id, detail, open) "
                "VALUES (?, 'gone', ?, '{}', 0)",
                [(utc_iso(now), rid) for rid in marked],
            )
        return marked

    def gone_recordings(self) -> frozenset[str]:
        """The recordings here that are gone from Plaud (`plaud_checks.gone_since`)."""
        with self._db() as db:
            rows = db.execute(
                "SELECT recording_id FROM plaud_checks WHERE gone_since IS NOT NULL"
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def back_from_gone(self, recording_ids: Iterable[str]) -> list[str]:
        """Each of these recordings that was gone from Plaud is listed again: it is here again
        (§12.3, "If it returns"). Returns those."""
        wanted = set(recording_ids)
        with self._db() as db:
            gone = [
                row[0]
                for row in db.execute(
                    "SELECT recording_id FROM plaud_checks WHERE gone_since IS NOT NULL "
                    "ORDER BY recording_id"
                )
                if row[0] in wanted
            ]
            db.executemany(
                "UPDATE plaud_checks SET gone_since = NULL WHERE recording_id = ?",
                [(rid,) for rid in gone],
            )
        return gone

    # ---- plaud_checks --------------------------------------------------------------------------
    def check_row(self, recording_id: str) -> CheckRow | None:
        with self._db() as db:
            row = db.execute(
                f"SELECT {', '.join(_CHECKS)} FROM plaud_checks WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return _row(CheckRow, _CHECKS, row) if row else None

    def failing_checks(self) -> list[CheckRow]:
        """The recordings here on the failing list (§10.2, §14.6): FAILING_AFTER or more failed
        checks in a row (`failures`), neither parked nor gone from Plaud, the oldest
        `failing_since` first and a row without one last. Phase 3's checks write these columns;
        Status's "fails every check" line reads them (§15.2; Dan, Phase 1's Q&A §6)."""
        with self._db() as db:
            rows = db.execute(
                f"SELECT {', '.join(_CHECKS)} FROM plaud_checks "
                "WHERE failures >= ? AND parked_at IS NULL AND gone_since IS NULL "
                "ORDER BY failing_since IS NULL, failing_since, recording_id",
                (FAILING_AFTER,),
            ).fetchall()
        return [_row(CheckRow, _CHECKS, row) for row in rows]

    def set_check(self, recording_id: str, **fields: Any) -> None:
        """Set plaud_checks columns (their names only), making the row if it is missing."""
        _refuse_unknown_columns(fields, _CHECKS, "recording_id")
        names = ["recording_id", *fields]
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields) or "recording_id = recording_id"
        with self._db() as db:
            db.execute(
                f"INSERT INTO plaud_checks ({', '.join(names)}) "
                f"VALUES ({_marks(len(names))}) "
                f"ON CONFLICT (recording_id) DO UPDATE SET {updates}",
                (recording_id, *(_encode(k, v) for k, v in fields.items())),
            )

    # ---- plaud_events --------------------------------------------------------------------------
    def add_event(
        self,
        kind: str,
        *,
        now: datetime,
        recording_id: str | None = None,
        plaud_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
        open_flag: bool = False,
    ) -> int:
        """One event; with open_flag, an open flag, which the partial unique indexes keep to one
        privacy flag and one unwritten `private` per recording (sqlite3.IntegrityError)."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"not an event kind: {kind}")
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO plaud_events (at, kind, recording_id, canonical_plaud_id, detail, "
                "open) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    utc_iso(now),
                    kind,
                    recording_id,
                    canonical_plaud_id(plaud_id) if plaud_id else None,
                    json.dumps(dict(detail or {}), ensure_ascii=False),
                    int(open_flag),
                ),
            )
            return int(cur.lastrowid)

    def _events(self, where: str, args: tuple) -> list[Event]:
        with self._db() as db:
            rows = db.execute(
                f"SELECT {', '.join(_EVENT)} FROM plaud_events WHERE {where} ORDER BY id", args
            ).fetchall()
        return [_row(Event, _EVENT, row) for row in rows]

    def open_flags(
        self, kinds: Iterable[str] = FLAG_KINDS, recording_id: str | None = None
    ) -> list[Event]:
        kinds = tuple(kinds)
        where, args = f"open = 1 AND kind IN ({_marks(len(kinds))})", kinds
        if recording_id is not None:
            where, args = where + " AND recording_id = ?", (*kinds, recording_id)
        return self._events(where, args)

    def resolve(self, event_id: int, *, now: datetime) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE plaud_events SET open = 0, resolved_at = ? WHERE id = ?",
                (utc_iso(now), event_id),
            )

    def change_kind(self, event_id: int, kind: str) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"not an event kind: {kind}")
        with self._db() as db:
            db.execute("UPDATE plaud_events SET kind = ? WHERE id = ?", (kind, event_id))

    def events_since(self, since: datetime, kinds: Iterable[str]) -> list[Event]:
        kinds = tuple(kinds)
        return self._events(f"at >= ? AND kind IN ({_marks(len(kinds))})", (utc_iso(since), *kinds))

    # ---- plaud_private_checked: the auto-private marker (§13.2) -------------------------------
    def markers(self, recording_id: str) -> dict[str, Marker]:
        with self._db() as db:
            rows = db.execute(
                "SELECT canonical_plaud_id, title_sha256, patterns_version "
                "FROM plaud_private_checked WHERE recording_id = ?",
                (recording_id,),
            ).fetchall()
        held = self.get(judged_key(recording_id))
        held = held if isinstance(held, dict) else {}  # no record: nothing judged yet
        return {
            row[0]: Marker(
                *row,
                judged=tuple(
                    h for h in held.get(row[0], ()) if isinstance(h, str) and _is_sha256(h)
                ),
            )
            for row in rows
        }

    def set_markers(self, recording_id: str, markers: Iterable[Marker], *, now: datetime) -> None:
        markers = list(markers)
        for m in markers:
            if not _is_sha256(m.title_sha256):
                raise ValueError("title_sha256 must be 64 lowercase hex characters")
            if not all(_is_sha256(h) for h in m.judged):
                raise ValueError("a judged snapshot's sha256 must be 64 lowercase hex characters")
        with self._db() as db:
            db.executemany(
                "INSERT INTO plaud_private_checked (recording_id, canonical_plaud_id, "
                "title_sha256, patterns_version, checked_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (recording_id, canonical_plaud_id) DO UPDATE SET "
                "title_sha256 = excluded.title_sha256, "
                "patterns_version = excluded.patterns_version, checked_at = excluded.checked_at",
                [
                    (
                        recording_id,
                        canonical_plaud_id(m.canonical_plaud_id),
                        m.title_sha256,
                        m.patterns_version,
                        utc_iso(now),
                    )
                    for m in markers
                ],
            )
            # The judged snapshots move with the markers, in this transaction (§13.2).
            row = db.execute(
                "SELECT value FROM plaud_state WHERE key = ?", (judged_key(recording_id),)
            ).fetchone()
            held = json.loads(row[0]) if row else {}
            held = held if isinstance(held, dict) else {}
            held.update({canonical_plaud_id(m.canonical_plaud_id): list(m.judged) for m in markers})
            self._put(db, judged_key(recording_id), held, now)

    def prune(self, *, now: datetime) -> None:
        """The check log after 7 days; events after 90, except open flags (§22)."""
        with self._db() as db:
            db.execute("DELETE FROM plaud_check_log WHERE at < ?", (utc_iso(now - CHECK_LOG_KEPT),))
            db.execute(
                "DELETE FROM plaud_events WHERE open = 0 AND at < ?", (utc_iso(now - EVENTS_KEPT),)
            )
