"""The first catch-up's gate (stage 2b spec §11.6), as Phase 1 needs it.

- **The gate** holds every scheduled sync, and every request that would write a snapshot, until
  `plaud_state.catch_up_done_at` holds a real timestamp: the first import is a supervised run on
  the server.
  Late consent listings, which only add `private` or a flag, still apply (§12.1), and so do the
  kinds `GATE_ALLOWED` names, which write no snapshot.
- **It is read from `plaud_state` alone,** at the worker's start and at its ticks: no archive
  scan. Nothing in Phase 1 sets `catch_up_done_at`, so the gate stays closed until Phase 2's
  catch-up, and Status says "Scheduled sync waits for the first catch-up (runbook)", which isn't a
  failure: the sync's check, never pinged, stays silent.
- **The snapshot mark** (area A4): `mark_snapshot`, before every 2b snapshot, by every path.
- **An interrupted first catch-up:** mark rows, and no `catch_up_done_at`. The gate stays closed,
  and its reason says so (GATE_INTERRUPTED): `--catch-up` is run again.
- **A rebuilt `state.db`** (`detect_rebuild`, at the worker's start): the archive holds 2b
  snapshots (`_meta.tool` "recordings") but `plaud_state` has no mark row. `rebuilt_at`, the
  gate's opening and a mark are recorded in one transaction, so a restart doesn't find it again;
  every listed ID made before `rebuilt_at` that isn't here then needs Dan's review
  (`compare.review_marks`). A `state.db` restored from an older backup still has marks, so it
  passes: `recordings plaud review` records `rebuilt_at` as now instead (§11.6, the runbook).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from recordings.plaud.checks import K_CATCH_UP_DONE, K_SNAPSHOT_MARK, PlaudDB
from recordings.plaud.when import parse_stamp

if TYPE_CHECKING:
    from recordings.archive import Archive

GATE_FIRST = "Scheduled sync waits for the first catch-up (runbook)"
GATE_REFUSAL = "The first import is a supervised run on the server (runbook)"
GATE_INTERRUPTED = "The first catch-up didn't finish: run `--catch-up` again (runbook)"
# The request kinds the closed gate lets through (§11.6: "Only Compare runs"; Keep it here only
# resolves a flag). Every other kind would write a snapshot, and gets GATE_REFUSAL.
GATE_ALLOWED: frozenset[str] = frozenset({"compare", "keep-here"})


@dataclass(frozen=True)
class Gate:
    open: bool
    reason: str | None  # GATE_FIRST while closed; None when open


def mark_snapshot(db: PlaudDB, *, now: datetime, by: str) -> None:
    """The snapshot mark (§11.6), committed in its own transaction before a 2b snapshot is
    published, by every path (an import's `Writer.add`, a check's `add_snapshot`), and when a
    rebuild is recorded. One row, counted: a crash after it and before the snapshot leaves a mark
    with no snapshot, which is harmless. Mark rows with no `catch_up_done_at` say the first
    catch-up was interrupted; 2b snapshots with no mark row say `state.db` was rebuilt."""
    stamp = now.isoformat()

    def bump(old: object) -> dict:
        old = old if isinstance(old, dict) else {}
        count = old.get("count")
        return {
            "first_at": old.get("first_at") or stamp,
            "last_at": stamp,
            "count": (count if isinstance(count, int) and not isinstance(count, bool) else 0) + 1,
            "by": by,
        }

    db.update(K_SNAPSHOT_MARK, bump, now=now)


def gate_now(db: PlaudDB) -> Gate:
    """The gate now, from `plaud_state` alone: open exactly when the first catch-up is done.
    Only a string that reads as a timestamp opens it: any other value (empty, false, a number, a
    list, junk) leaves it closed, and a stamp in the future still counts (the clock may be behind).
    Raises NoSyncYet when `state.db` has no 2b tables, and any database error (locked, unreadable)
    as it is: the gate never opens on a read it couldn't make."""
    value = db.get(K_CATCH_UP_DONE)
    if isinstance(value, str) and parse_stamp(value) is not None:
        return Gate(open=True, reason=None)
    if db.get(K_SNAPSHOT_MARK) is not None:  # marks, no catch_up_done_at: it was interrupted
        return Gate(open=False, reason=GATE_INTERRUPTED)
    return Gate(open=False, reason=GATE_FIRST)


def has_2b_snapshot(archive: Archive) -> bool:
    """Whether any recording here holds a snapshot this app wrote (`_meta.tool` "recordings",
    `client.stamp_meta`). Read only when no mark row exists (the first start, or a rebuild), and
    it stops at the first one. A snapshot that can't be read counts for nothing."""
    import json

    for rec in archive.iter_recordings():
        folder = archive.path_for(rec.id)
        for source in rec.sources:
            if source.kind != "plaud" or not source.raw:
                continue
            try:
                payload = json.loads((folder / source.raw).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, RecursionError):
                continue
            meta = payload.get("_meta") if isinstance(payload, dict) else None
            if isinstance(meta, dict) and meta.get("tool") == "recordings":
                return True
    return False


def detect_rebuild(db: PlaudDB, archive: Archive, *, now: datetime) -> bool:
    """§11.6's rebuilt `state.db`, at the worker's start: 2b snapshots in the archive and no
    mark row. Records `rebuilt_at`, opens the gate and commits a mark, in one transaction; True
    when it did. Nothing stops: new recordings flow, and what was made before waits for review."""
    if db.get(K_SNAPSHOT_MARK) is not None or not has_2b_snapshot(archive):
        return False
    stamp = now.isoformat()
    db.record_rebuilt(
        at=now,
        by="detected",
        catch_up=not gate_now(db).open,
        mark={"first_at": stamp, "last_at": stamp, "count": 1, "by": "rebuild"},
        now=now,
    )
    return True
