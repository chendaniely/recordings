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
- **Phase 2's catch-up adds** the snapshot mark before every 2b snapshot, the interrupted state,
  and a rebuilt or restored `state.db` (`rebuilt_at`, `recordings plaud review`), with the code
  that writes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from recordings.plaud.checks import K_CATCH_UP_DONE, K_SNAPSHOT_MARK, PlaudDB
from recordings.plaud.when import parse_stamp

GATE_FIRST = "Scheduled sync waits for the first catch-up (runbook)"
GATE_REFUSAL = "The first import is a supervised run on the server (runbook)"
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
    return Gate(open=False, reason=GATE_FIRST)
