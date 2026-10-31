"""Requests from the UI (spec §9.3): rows in state.db's `plaud_requests`, the SQL that both `web`
and `worker` run on them (2b phase 1 plan, decision 20).

- **web inserts** a row with `insert`, on `asyncio.to_thread` (recordings_ui.plaud.submit), in one
  `BEGIN IMMEDIATE` transaction: a repeated `request_id` is that row, never a second one. Only the
  kinds in HANDLED reach it: recordings_ui.plaud refuses the others with NOT_YET before anything
  is inserted. Each later phase widens HANDLED as it adds a kind's handler, and §9.3's coalescing
  for that kind (a sync taking over a compare, a resync merge, Sync everything's cooldown).
- **the worker claims** the oldest queued row (`claim_next`), checks it again against fresh facts
  (worker.Worker.serve_request), and `finish`es or `refuse`s it. At start it fails rows left
  `running` ("interrupted") and expires rows queued more than an hour ago.
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
# The kinds the worker has a handler for. Phase 1 has Keep it here alone; each later phase adds its
# kinds with their handlers.
HANDLED: frozenset[str] = frozenset({"keep-here"})
NOT_YET = "Comes with the next part of stage 2b."
MAX_PLAUD_IDS = 20_000
MAX_RESYNC_IDS = 1_000
MAX_PLAUD_ID_LEN = 128
EXPIRE_AFTER = timedelta(hours=1)
KEEP_FINISHED = timedelta(days=7)
# How long an insert has, from the press, to get its row in (spec P6 answers within a second): the
# deadline covers every wait it makes, and a request that misses it is rolled back, never saved.
SAVE_WAIT = 0.8
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
QUEUED = "Queued"
INTERRUPTED = "interrupted"
UNREADABLE_ROW = "The request couldn't be read."


class NotSaved(TimeoutError):
    """The insert ran out of its time and rolled back: no row was saved."""


def queued_after(started: str) -> str:
    return f"Queued after the current sync (started {started})"


def worker_down(last: str) -> str:
    return (
        f"The Plaud worker isn't running (last reported {last}): your request runs when it's "
        "back, if that's within the hour."
    )


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


def _notice(conn: sqlite3.Connection, state: State, *, now: datetime, timezone_name: str) -> str:
    """What a queued request's reply says, read inside the insert's transaction (so within its
    deadline): the worker down (by its heartbeat), or a run holding `plaud`, or plain "Queued"."""
    from recordings.plaud.runlock import plaud_holder  # the lock probe: fcntl, server only
    from recordings.worker import HEARTBEAT_KEY, beat_alive, parse_heartbeat

    row = conn.execute("SELECT value FROM meta WHERE key = ?", (HEARTBEAT_KEY,)).fetchone()
    beat = parse_heartbeat(row[0] if row else None)
    holder = plaud_holder(state)
    if not beat_alive(beat, now=now)[0]:
        last = parse_stamp(beat.get("at")) if beat is not None else None
        return worker_down("never" if last is None else _hhmm(last, timezone_name))
    if holder is not None:
        return queued_after(_hhmm(holder[1], timezone_name))
    return QUEUED


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
        left()  # right before the INSERT
        conn.execute(
            "INSERT INTO plaud_requests (request_id, kind, plaud_ids, recording_ids, select_all, "
            "state, queued_at, message) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (
                req.request_id,
                req.kind,
                json.dumps(list(req.plaud_ids)),
                json.dumps(list(req.recording_ids)),
                int(req.select_all),
                utc_iso(now),
                notice,
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


def claim_next(state: State, *, now: datetime) -> Claimed | None:
    """The oldest queued row, now `running`. A row that can't be read is failed, not retried."""
    with state.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, request_id, kind, plaud_ids, recording_ids, select_all, queued_at "
            "FROM plaud_requests WHERE state = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            plaud_ids, recording_ids = json.loads(row[3]), json.loads(row[4])
            claimed = Claimed(
                id=row[0],
                request=PlaudRequest(
                    request_id=row[1],
                    kind=row[2],
                    plaud_ids=tuple(str(p) for p in plaud_ids),
                    recording_ids=tuple(str(r) for r in recording_ids),
                    select_all=bool(row[5]),
                ),
                queued_at=_stamp(row[6]),
            )
        except (ValueError, TypeError):  # an unreadable column or time
            conn.execute(
                "UPDATE plaud_requests SET state = 'failed', finished_at = ?, message = ? "
                "WHERE id = ?",
                (utc_iso(now), UNREADABLE_ROW, row[0]),
            )
            return None
        conn.execute(
            "UPDATE plaud_requests SET state = 'running', started_at = ? WHERE id = ?",
            (utc_iso(now), row[0]),
        )
    return claimed


def _stamp(raw: object) -> datetime:
    when = parse_stamp(raw)
    if when is None:
        raise ValueError("not a time")
    return when


def progress(state: State, request_id: str, *, done: int, total: int) -> None:
    with state.db() as conn:
        conn.execute(
            "UPDATE plaud_requests SET progress = ? WHERE request_id = ? AND state = 'running'",
            (json.dumps({"done": done, "total": total}), request_id),
        )


def _end(
    state: State,
    request_id: str,
    ended: ReplyState,
    message: str,
    result: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    with state.db() as conn:
        conn.execute(
            "UPDATE plaud_requests SET state = ?, finished_at = ?, message = ?, result = ? "
            "WHERE request_id = ?",
            (
                ended,
                utc_iso(now),
                message,
                json.dumps(dict(result)) if result else None,
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
    with state.db() as conn:
        return conn.execute(
            "UPDATE plaud_requests SET state = 'expired', finished_at = ? WHERE state = 'queued' "
            "AND datetime(queued_at) < datetime(?)",
            (utc_iso(now), utc_iso(now - EXPIRE_AFTER)),
        ).rowcount


def prune(state: State, *, now: datetime) -> int:
    with state.db() as conn:
        return conn.execute(
            "DELETE FROM plaud_requests WHERE state IN ('done', 'failed', 'expired', 'refused') "
            "AND datetime(finished_at) < datetime(?)",
            (utc_iso(now - KEEP_FINISHED),),
        ).rowcount
