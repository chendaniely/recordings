"""The first catch-up's gate (stage 2b spec §11.6), as Phase 1 needs it.

- **The gate** holds every scheduled sync, and every request that would write a snapshot, until
  `plaud_state.catch_up_done_at` is set: the first import is a supervised run on the server.
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

from recordings.plaud.checks import K_CATCH_UP_DONE, PlaudDB

GATE_FIRST = "Scheduled sync waits for the first catch-up (runbook)"
GATE_REFUSAL = "The first import is a supervised run on the server (runbook)"
# The request kinds the closed gate lets through (§11.6: "Only Compare runs"; Keep it here only
# resolves a flag). Every other kind would write a snapshot, and gets GATE_REFUSAL.
GATE_ALLOWED: frozenset[str] = frozenset({"compare", "keep-here"})


@dataclass(frozen=True)
class Gate:
    open: bool
    reason: str | None  # GATE_FIRST while closed; None when open


def gate_now(db: PlaudDB) -> Gate:
    """The gate now, from `plaud_state` alone: open exactly when the first catch-up is done.
    Raises NoSyncYet when `state.db` has no 2b tables."""
    if db.get(K_CATCH_UP_DONE) is not None:
        return Gate(open=True, reason=None)
    return Gate(open=False, reason=GATE_FIRST)
