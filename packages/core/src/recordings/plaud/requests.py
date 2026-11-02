"""Requests from the UI (spec §9.3): rows in state.db's `plaud_requests`, the SQL that both `web`
and `worker` run on them (2b phase 1 plan, decision 20).

- **web inserts** a row with `insert`, on `asyncio.to_thread` (recordings_ui.plaud.submit), in one
  `BEGIN IMMEDIATE` transaction: a repeated `request_id` is that row, never a second one. Only the
  kinds in HANDLED reach it: recordings_ui.plaud refuses the others with NOT_YET before anything
  is inserted. Each later phase widens HANDLED as it adds a kind's handler, and §9.3's coalescing
  for that kind.
- **Coalescing** (§9.3; area A5), in the same transaction: a press that a queued request already
  covers gets a row of its own that *follows* that one (`result` {"follows": its request_id}):
  the worker never claims it, and it takes its leader's state, progress, message and result, so
  every press sees how the one run ended. A `compare` or `sync` while a `sync` is queued, or a
  `compare` while a `compare` is, answers "Already queued"; a `sync` while a `compare` is queued
  upgrades that row to a sync ("Queued"); an `import` while an `import` is queued merges into it
  (its Plaud IDs added, `all` kept), "Already queued" when it adds nothing; a `resync` merges its
  recordings into a queued one likewise, up to MAX_RESYNC_IDS (area B2). A `full` doesn't
  coalesce: a second press is refused while one is queued or running, and within an hour of the
  last finished one, and answers "Resuming (started 14:20)" while one is unfinished.
- **the worker claims** the oldest queued row that follows none (`claim_next`), leaving the kinds
  it is told to skip queued (those needing `plaud` while another run holds it: a request waits
  its turn, §9.4), checks it again against fresh facts (worker.Worker.serve_request), and
  `finish`es or `refuse`s it, its followers with it. At start it fails rows left `running`
  ("interrupted") and expires rows queued more than an hour ago, and any follower whose leader
  has ended.
- Rows hold counts, IDs and fixed phrases (§15.6), never a title.

It imports no Plaud client, token store or sync module. It must also import on the Pages demo's
Pyodide (recordings_ui.plaud imports it there): only the standard library and
`recordings.plaud.when` at the top; the lock probe and the heartbeat are imported where `insert`
uses them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal

from recordings.plaud.when import local_hhmm, parse_stamp, utc_iso

if TYPE_CHECKING:
    import sqlite3

    from recordings.state import State

KINDS = (
    "compare",
    "sync",
    "import",
    "resync",
    "full",
    "accept",
    "accept-changes",
    "release-preview",
    "release",
    "unrelease",
    "park",
    "unpark",
    "keep-here",
)
# The kinds the worker has a handler for (worker.HANDLERS names the same): Phase 1's Keep it here,
# Accept this listing (Phase 2, area A3), Release's preview, Let it in and Undo (area A4),
# Compare, Sync now, the imports, Park and Unpark (area A5), Accept these changes (Phase 3, area
# B1), and Re-sync and Sync everything (area B2), with which every kind is handled. A later kind is
# added with its handler, never before (CLAUDE.md, "To add a Plaud request kind").
HANDLED: frozenset[str] = frozenset(
    {
        "keep-here",
        "accept",
        "release-preview",
        "release",
        "unrelease",
        "compare",
        "sync",
        "import",
        "park",
        "unpark",
        "accept-changes",
        "resync",
        "full",
    }
)
# The kinds a second press coalesces with while one is queued (§9.3): see `_coalesce`. Sync
# everything doesn't coalesce: a second press is refused (`_full_refusal`).
COALESCED: frozenset[str] = frozenset({"compare", "sync", "import", "resync"})
NOT_YET = "Comes with the next part of stage 2b."
MAX_PLAUD_IDS = 20_000
MAX_RESYNC_IDS = 1_000
# §9.3's "under 128 characters": this is the first length refused (recordings_ui.plaud's check
# takes `len(plaud_id) < MAX_PLAUD_ID_LEN`).
MAX_PLAUD_ID_LEN = 128
EXPIRE_AFTER = timedelta(hours=1)
KEEP_FINISHED = timedelta(days=7)
# How long an insert has, from the press, to get its row in (spec P6 answers within a second): the
# deadline covers every wait it makes, and a request that misses it is rolled back, never saved.
SAVE_WAIT = 0.8
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
QUEUED = "Queued"
ALREADY_QUEUED = "Already queued"
RUNNING = "Running"
EXPIRED = "Expired: the Plaud worker didn't run it within the hour. Press again once it's back."
INTERRUPTED = "interrupted"
UNREADABLE_ROW = "The request couldn't be read."


class NotSaved(TimeoutError):
    """The insert ran out of its time and rolled back: no row was saved."""


def queued_after(started: str) -> str:
    return f"Queued after the current sync (started {started})"


def waiting_for_full(done: int, total: int) -> str:
    """§15.6's "waiting for Sync everything (412 of 1,234)": a request queued while it runs."""
    return f"Waiting for Sync everything ({done:,} of {total:,})"


def resuming(started: str) -> str:
    """§9.3: a press of Sync everything while one is unfinished."""
    return f"Resuming (started {started})"


FULL_RECENT = "Sync everything ran less than an hour ago"  # plaud.full.RECENT (§15.6)
FULL_KEY = "full_sync"  # checks.K_FULL, kept here: Pyodide imports this module, not checks
FULL_COOLDOWN = timedelta(hours=1)  # plaud.full.COOLDOWN


WORKER_DOWN = "The Plaud worker isn't running"


def worker_down(last: str) -> str:
    return (
        f"{WORKER_DOWN} (last reported {last}): your request runs when it's "
        "back, if that's within the hour."
    )


def is_worker_down(message: str) -> bool:
    return message.startswith(WORKER_DOWN)


@dataclass(frozen=True)
class PlaudRequest:
    request_id: str
    kind: str
    plaud_ids: tuple[str, ...] = ()
    recording_ids: tuple[str, ...] = ()
    select_all: bool = False


ReplyState = Literal["queued", "running", "done", "failed", "expired", "refused"]


@dataclass(frozen=True)
class Reply:
    request_id: str
    kind: str | None
    state: ReplyState
    message: str
    progress: Mapping[str, int] | None = None  # {"done", "total"}
    result: Mapping[str, Any] | None = None  # counts and IDs, never a title

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "state": self.state,
            "message": self.message,
            "progress": dict(self.progress) if self.progress is not None else None,
            "result": dict(self.result) if self.result is not None else None,
        }


@dataclass(frozen=True)
class Claimed:
    id: int
    request: PlaudRequest
    queued_at: datetime


def _hhmm(t: datetime, timezone_name: str) -> str:
    from zoneinfo import ZoneInfo  # here: only insert's replies format a time

    return local_hhmm(t, ZoneInfo(timezone_name))


def _load(raw: str | None) -> dict:
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


_ROW = "request_id, kind, state, message, progress, result"


def _reply(row: tuple) -> Reply:
    request_id, kind, state, message, progress, result = row
    return Reply(
        request_id=request_id,
        kind=kind,
        state=state,
        message=message or "",
        progress=_load(progress) or None,
        result=_load(result) or None,
    )


def _down(conn: sqlite3.Connection, *, now: datetime, timezone_name: str) -> str | None:
    """worker_down's sentence when the heartbeat says the worker isn't running, else None."""
    from recordings.worker import HEARTBEAT_KEY, beat_alive, parse_heartbeat

    row = conn.execute("SELECT value FROM meta WHERE key = ?", (HEARTBEAT_KEY,)).fetchone()
    beat = parse_heartbeat(row[0] if row else None)
    if beat_alive(beat, now=now)[0]:
        return None
    last = parse_stamp(beat.get("at")) if beat is not None else None
    return worker_down("never" if last is None else _hhmm(last, timezone_name))


def worker_notice(state: State, *, now: datetime, timezone_name: str) -> str | None:
    """For a session following a queued row (spec §9.3): worker_down's sentence while the worker's
    heartbeat says it isn't running, else None. Read only; the same rule `insert` uses."""
    with state.db() as conn:
        return _down(conn, now=now, timezone_name=timezone_name)


def _notice(conn: sqlite3.Connection, state: State, *, now: datetime, timezone_name: str) -> str:
    """What a queued request's reply says, read inside the insert's transaction (so within its
    deadline): the worker down (by its heartbeat), or a run holding `plaud`, or plain "Queued"."""
    from recordings.plaud.runlock import plaud_holder  # the lock probe: fcntl, server only

    down = _down(conn, now=now, timezone_name=timezone_name)
    holder = plaud_holder(state)
    if down is not None:
        return down
    if holder is not None:
        if holder[0] == "plaud-full":
            done, total = _full_progress(_full(conn).get("running"))
            if total:
                return waiting_for_full(done, total)
        return queued_after(_hhmm(holder[1], timezone_name))
    return QUEUED


def _full(conn: sqlite3.Connection) -> dict:
    """Sync everything's record (checks.K_FULL), read in the insert's transaction; {} when there
    is none or it can't be read."""
    row = conn.execute("SELECT value FROM plaud_state WHERE key = ?", (FULL_KEY,)).fetchone()
    return _load(row[0]) if row else {}


def _full_progress(record: object) -> tuple[int, int]:
    record = record if isinstance(record, dict) else {}
    done, total = record.get("done"), record.get("total")
    ok = isinstance(done, int) and isinstance(total, int) and not isinstance(done, bool)
    return (done, total) if ok else (0, 0)


def _full_refusal(
    conn: sqlite3.Connection, state: State, *, now: datetime, timezone_name: str
) -> tuple[str | None, str | None]:
    """§9.3's rule for a press of Sync everything, in the insert's transaction: (why it is
    refused, or None; the reply when it isn't, or None for the usual one). Refused while one is
    queued or running, and within an hour of the last finished one; "Resuming (started 14:20)"
    while an interrupted one is unfinished (the worker resumes it)."""
    from recordings.plaud.runlock import plaud_holder  # the lock probe: fcntl, server only

    rows = conn.execute(
        "SELECT state FROM plaud_requests WHERE kind = 'full' AND state IN ('queued', 'running')"
    ).fetchall()
    if any(row[0] == "queued" for row in rows):
        return ALREADY_QUEUED, None
    record = _full(conn)
    unfinished = record.get("running") if isinstance(record.get("running"), dict) else None
    holder = plaud_holder(state)
    if rows or (holder is not None and holder[0] == "plaud-full"):
        done, total = _full_progress(unfinished)
        progress = f" ({done:,} of {total:,})" if total else ""
        return f"Sync everything is running{progress}", None
    started = parse_stamp(unfinished.get("started")) if unfinished else None
    if started is not None:
        return None, resuming(_hhmm(started, timezone_name))
    previous = record.get("last") if isinstance(record.get("last"), dict) else {}
    finished = parse_stamp(previous.get("finished"))
    if finished is not None and timedelta(0) <= now - finished < FULL_COOLDOWN:
        return FULL_RECENT, None
    return None, None


# A row that follows another (the module's notes on coalescing): its `result` names its leader
# while it waits. CASE, not OR: SQLite needn't short-circuit, and json_extract raises on text
# that isn't JSON.
_FOLLOWS = "(CASE WHEN json_valid(result) THEN json_extract(result, '$.follows') END)"
_LEADS = f"{_FOLLOWS} IS NULL"


def _coalesce(conn: sqlite3.Connection, req: PlaudRequest) -> tuple[str | None, bool]:
    """§9.3's coalescing, inside the insert's transaction: (the queued row this request follows,
    whether it adds nothing to it), or (None, False) for a row of its own (the module's notes)."""
    if req.kind not in COALESCED:
        return None, False
    if req.kind == "resync":
        return _merge_resync(conn, req)
    kinds = ("compare", "sync") if req.kind in ("compare", "sync") else ("import",)
    marks = ", ".join("?" for _ in kinds)
    found = conn.execute(
        "SELECT request_id, kind, plaud_ids, select_all FROM plaud_requests "
        f"WHERE state = 'queued' AND kind IN ({marks}) AND {_LEADS} ORDER BY id",
        kinds,
    ).fetchall()
    if not found:
        return None, False
    if req.kind == "import":
        leader, _, raw_ids, select_all = found[0]
        try:
            ids = [str(p) for p in json.loads(raw_ids)]
        except (ValueError, TypeError):
            return None, False  # a row that can't be read: this one stands on its own
        merged = list(dict.fromkeys([*ids, *req.plaud_ids]))
        every = bool(select_all) or req.select_all
        if merged == ids and every == bool(select_all):
            return leader, True
        conn.execute(
            "UPDATE plaud_requests SET plaud_ids = ?, select_all = ? WHERE request_id = ?",
            (json.dumps(merged), int(every), leader),
        )
        return leader, False
    syncs = [row[0] for row in found if row[1] == "sync"]
    if syncs:
        return syncs[0], True
    leader = found[0][0]
    if req.kind == "sync":  # the queued compare becomes this sync (the sync lists too)
        conn.execute("UPDATE plaud_requests SET kind = 'sync' WHERE request_id = ?", (leader,))
        return leader, False
    return leader, True


def _merge_resync(conn: sqlite3.Connection, req: PlaudRequest) -> tuple[str | None, bool]:
    """§9.3: a `resync` merges into a queued one (`UPDATE … WHERE state = 'queued'`), up to
    MAX_RESYNC_IDS; with none queued (the worker just claimed it), or too many, a row of its own."""
    found = conn.execute(
        "SELECT request_id, recording_ids FROM plaud_requests "
        f"WHERE state = 'queued' AND kind = 'resync' AND {_LEADS} ORDER BY id LIMIT 1"
    ).fetchone()
    if found is None:
        return None, False
    leader, raw_ids = found
    try:
        ids = [str(r) for r in json.loads(raw_ids)]
    except (ValueError, TypeError):
        return None, False  # a row that can't be read: this one stands on its own
    merged = list(dict.fromkeys([*ids, *req.recording_ids]))
    if merged == ids:
        return leader, True
    if len(merged) > MAX_RESYNC_IDS:
        return None, False
    updated = conn.execute(
        "UPDATE plaud_requests SET recording_ids = ? WHERE request_id = ? AND state = 'queued'",
        (json.dumps(merged), leader),
    ).rowcount
    return (leader, False) if updated else (None, False)


def insert(
    state: State,
    req: PlaudRequest,
    *,
    now: datetime,
    timezone_name: str,
    deadline: float | None = None,
) -> Reply:
    """Spec §9.3's insert, in one BEGIN IMMEDIATE transaction, so a request_id sent twice at once
    is still one row. `deadline` is a `time.monotonic()` reading (default: SAVE_WAIT from now).
    "The request couldn't be saved" means no row, so every wait here (the connection, the write
    lock, the heartbeat read) uses the time left, and the deadline is checked again right before
    the INSERT and before the commit: once it has passed the transaction rolls back and NotSaved
    is raised. sqlite3.Error propagates too (recordings_ui.plaud.submit answers both with
    NOT_SAVED). Only a commit that hangs past the deadline can still land a row; submit's own
    wait is a backstop for that, and a session follows its `request_id` to learn how it ended."""
    if deadline is None:
        deadline = monotonic() + SAVE_WAIT

    def left() -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NotSaved
        return remaining

    with state.db(timeout=left()) as conn:
        conn.execute(f"PRAGMA busy_timeout = {int(left() * 1000)}")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT {_ROW} FROM plaud_requests WHERE request_id = ?", (req.request_id,)
        ).fetchone()
        if row is not None:
            return _reply(row)  # a repeated request_id is that row
        notice = _notice(conn, state, now=now, timezone_name=timezone_name)
        if req.kind == "full":
            refused, said = _full_refusal(conn, state, now=now, timezone_name=timezone_name)
            if refused is not None:
                return Reply(req.request_id, req.kind, "refused", refused)  # no row
            if said is not None and not is_worker_down(notice):
                notice = said
        leader, nothing_new = _coalesce(conn, req)
        if nothing_new and not is_worker_down(notice):
            notice = ALREADY_QUEUED
        left()  # right before the INSERT
        conn.execute(
            "INSERT INTO plaud_requests (request_id, kind, plaud_ids, recording_ids, select_all, "
            "state, queued_at, message, result) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                req.request_id,
                req.kind,
                json.dumps(list(req.plaud_ids)),
                json.dumps(list(req.recording_ids)),
                int(req.select_all),
                utc_iso(now),
                notice,
                json.dumps({"follows": leader}) if leader is not None else None,
            ),
        )
        left()  # and before the commit
    return Reply(req.request_id, req.kind, "queued", notice)


def read(state: State, request_id: str) -> Reply | None:
    with state.db() as conn:
        row = conn.execute(
            f"SELECT {_ROW} FROM plaud_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
    return None if row is None else _reply(row)


_CLAIM = ("id", "request_id", "kind", "plaud_ids", "recording_ids", "select_all", "queued_at")


# A Compare, or a Re-sync of one recording: what a long run serves between two recordings (§9.2).
_INSIDE = (
    " AND (kind = 'compare' OR (kind = 'resync' AND json_valid(recording_ids) "
    "AND json_array_length(recording_ids) <= 1))"
)


def claim_next(
    state: State, *, now: datetime, skip: frozenset[str] = frozenset(), inside: bool = False
) -> Claimed | None:
    """The oldest queued row that follows none and isn't of a kind in `skip`, now `running`, its
    followers with it; with `inside`, only what a long run serves between two recordings (a
    Compare, a Re-sync of one recording). A row that can't be read is failed, not retried."""
    with state.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        skipped = sorted(skip)
        unless = f" AND kind NOT IN ({', '.join('?' for _ in skipped)})" if skipped else ""
        unless += _INSIDE if inside else ""
        cursor = conn.execute(
            f"SELECT {', '.join(_CLAIM)} FROM plaud_requests "
            f"WHERE state = 'queued' AND {_LEADS}{unless} ORDER BY id LIMIT 1",
            skipped,
        )
        found = cursor.fetchone()
        if found is None:
            return None
        row = dict(zip((d[0] for d in cursor.description), found, strict=True))
        try:
            plaud_ids, recording_ids = (
                json.loads(row["plaud_ids"]),
                json.loads(row["recording_ids"]),
            )
            claimed = Claimed(
                id=row["id"],
                request=PlaudRequest(
                    request_id=row["request_id"],
                    kind=row["kind"],
                    plaud_ids=tuple(str(p) for p in plaud_ids),
                    recording_ids=tuple(str(r) for r in recording_ids),
                    select_all=bool(row["select_all"]),
                ),
                queued_at=_stamp(row["queued_at"]),
            )
        except (ValueError, TypeError):  # an unreadable column or time
            conn.execute(
                "UPDATE plaud_requests SET state = 'failed', finished_at = ?, message = ? "
                f"WHERE id = ? OR (state = 'queued' AND {_FOLLOWS} = ?)",
                (utc_iso(now), UNREADABLE_ROW, row["id"], row["request_id"]),
            )
            return None
        conn.execute(
            "UPDATE plaud_requests SET state = 'running', started_at = ?, message = ? "
            f"WHERE id = ? OR (state = 'queued' AND {_FOLLOWS} = ?)",
            (utc_iso(now), RUNNING, row["id"], row["request_id"]),
        )
    return claimed


def _stamp(raw: object) -> datetime:
    when = parse_stamp(raw)
    if when is None:
        raise ValueError("not a time")
    return when


def progress(
    state: State, request_id: str, *, done: int, total: int, message: str | None = None
) -> None:
    """A running request's progress ({"done", "total"}), and its followers'; with `message`, its
    words too ("Importing": the screen adds "(5 of 31)")."""
    with state.db() as conn:
        conn.execute(
            "UPDATE plaud_requests SET progress = ?, message = COALESCE(?, message) "
            f"WHERE state = 'running' AND (request_id = ? OR {_FOLLOWS} = ?)",
            (json.dumps({"done": done, "total": total}), message, request_id, request_id),
        )


def _end(
    state: State,
    request_id: str,
    ended: ReplyState,
    message: str,
    result: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    """The row's end, and its followers' (still queued or running): the same answer for each."""
    with state.db() as conn:
        conn.execute(
            "UPDATE plaud_requests SET state = ?, finished_at = ?, message = ?, result = ? "
            f"WHERE request_id = ? OR (state IN ('queued', 'running') AND {_FOLLOWS} = ?)",
            (
                ended,
                utc_iso(now),
                message,
                json.dumps(dict(result)) if result else None,
                request_id,
                request_id,
            ),
        )


def finish(
    state: State,
    request_id: str,
    *,
    ok: bool,
    message: str,
    result: Mapping[str, Any] | None = None,
    now: datetime,
) -> None:
    _end(state, request_id, "done" if ok else "failed", message, result, now)


def refuse(state: State, request_id: str, *, message: str, now: datetime) -> None:
    """The worker's answer to a row it won't run: a kind it has no handler for yet, or the gate.
    `refused`, as web answers a request it never inserts."""
    _end(state, request_id, "refused", message, None, now)


def at_start(state: State, *, now: datetime) -> tuple[int, int]:
    """The worker's start (spec §9.2): (rows interrupted, rows expired)."""
    with state.db() as conn:
        interrupted = conn.execute(
            "UPDATE plaud_requests SET state = 'failed', finished_at = ?, message = ? "
            "WHERE state = 'running'",
            (utc_iso(now), INTERRUPTED),
        ).rowcount
    return interrupted, expire(state, now=now)


def expire(state: State, *, now: datetime) -> int:
    """Rows queued more than an hour ago expire; so does a follower whose leader has ended, or
    is gone, without taking it along (a crash between the two writes, a pruned row)."""
    with state.db() as conn:
        expired = conn.execute(
            "UPDATE plaud_requests SET state = 'expired', finished_at = ?, message = ? "
            "WHERE state = 'queued' AND datetime(queued_at) < datetime(?)",
            (utc_iso(now), EXPIRED, utc_iso(now - EXPIRE_AFTER)),
        ).rowcount
        orphans = conn.execute(
            "UPDATE plaud_requests SET state = 'expired', finished_at = ?, message = ? "
            f"WHERE state = 'queued' AND {_FOLLOWS} IS NOT NULL AND {_FOLLOWS} NOT IN "
            "(SELECT request_id FROM plaud_requests WHERE state IN ('queued', 'running'))",
            (utc_iso(now), EXPIRED),
        ).rowcount
        return expired + orphans


def prune(state: State, *, now: datetime) -> int:
    with state.db() as conn:
        return conn.execute(
            "DELETE FROM plaud_requests WHERE state IN ('done', 'failed', 'expired', 'refused') "
            "AND datetime(finished_at) < datetime(?)",
            (utc_iso(now - KEEP_FINISHED),),
        ).rowcount
