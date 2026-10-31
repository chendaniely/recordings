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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from recordings.plaud.when import local_hhmm, parse_stamp

if TYPE_CHECKING:
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
# How long an insert waits for state.db's write lock: spec P6 answers a press within a second, so a
# busy state.db is "couldn't be saved" well inside it, never a row that lands after that answer.
SAVE_WAIT_MS = 500
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
QUEUED = "Queued"
INTERRUPTED = "interrupted"
UNREADABLE_ROW = "The request couldn't be read."


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


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat()


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


def _notice(state: State, *, now: datetime, timezone_name: str) -> str:
    """What a queued request's reply says, read before the write lock: the worker down (by its
    heartbeat), or a run holding `plaud`, or plain "Queued"."""
    from recordings.plaud.runlock import plaud_holder  # the lock probe: fcntl, server only
    from recordings.worker import HEARTBEAT_KEY, alive

    holder = plaud_holder(state)
    ok, _ = alive(state, now=now)
    if not ok:
        try:
            beat = json.loads(state.meta(HEARTBEAT_KEY) or "null")
        except ValueError:
            beat = None
        last = parse_stamp(beat.get("at")) if isinstance(beat, dict) else None
        return worker_down("never" if last is None else _hhmm(last, timezone_name))
    if holder is not None:
        return queued_after(_hhmm(holder[1], timezone_name))
    return QUEUED


def insert(state: State, req: PlaudRequest, *, now: datetime, timezone_name: str) -> Reply:
    """Spec §9.3's insert, in one BEGIN IMMEDIATE transaction, so a request_id sent twice at once
    is still one row. It waits at most SAVE_WAIT_MS for state.db's write lock; sqlite3.Error
    propagates (recordings_ui.plaud.submit answers "The request couldn't be saved")."""
    notice = _notice(state, now=now, timezone_name=timezone_name)
    with state.db() as conn:
        conn.execute(f"PRAGMA busy_timeout = {SAVE_WAIT_MS}")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT {_ROW} FROM plaud_requests WHERE request_id = ?", (req.request_id,)
        ).fetchone()
        if row is not None:
            return _reply(row)  # a repeated request_id is that row
        conn.execute(
            "INSERT INTO plaud_requests (request_id, kind, plaud_ids, recording_ids, select_all, "
            "state, queued_at, message) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (
                req.request_id,
                req.kind,
                json.dumps(list(req.plaud_ids)),
                json.dumps(list(req.recording_ids)),
                int(req.select_all),
                _iso(now),
                notice,
            ),
        )
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
                queued_at=datetime.fromisoformat(row[6]),
            )
        except (ValueError, TypeError):
            conn.execute(
                "UPDATE plaud_requests SET state = 'failed', finished_at = ?, message = ? "
                "WHERE id = ?",
                (_iso(now), UNREADABLE_ROW, row[0]),
            )
            return None
        conn.execute(
            "UPDATE plaud_requests SET state = 'running', started_at = ? WHERE id = ?",
            (_iso(now), row[0]),
        )
    return claimed


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
            (ended, _iso(now), message, json.dumps(dict(result)) if result else None, request_id),
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
            (_iso(now), INTERRUPTED),
        ).rowcount
    return interrupted, expire(state, now=now)


def expire(state: State, *, now: datetime) -> int:
    with state.db() as conn:
        return conn.execute(
            "UPDATE plaud_requests SET state = 'expired', finished_at = ? WHERE state = 'queued' "
            "AND datetime(queued_at) < datetime(?)",
            (_iso(now), _iso(now - EXPIRE_AFTER)),
        ).rowcount


def prune(state: State, *, now: datetime) -> int:
    with state.db() as conn:
        return conn.execute(
            "DELETE FROM plaud_requests WHERE state IN ('done', 'failed', 'expired', 'refused') "
            "AND datetime(finished_at) < datetime(?)",
            (_iso(now - KEEP_FINISHED),),
        ).rowcount
