"""The `plaud` lock (stage 2b spec §9.4): one Plaud sync at a time, held for one run.

It is taken through its own `Locks` instance, as `ops` is: in a Writer's instance it would make
every recording lock taken inside it raise `LockOrderError`. So a run never nests a second
`plaud_run` in one thread (a second instance's flock waits on the first, and times out). Each hold
announces its job in the lock file, so Status, the deploy guard and `plaud_holder` can say what
runs, and since when. The catch-up takes `ops` and `plaud` in one `hold()` (both sort as "anything
else", `ops` first) and announces itself on both, so the deploy guard, which asks `ops`, sees it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime

from recordings.locks import OPS_LOCK, PLAUD_LOCK, Locks, announce, running
from recordings.state import State

JOBS = (  # §9.4's list
    "plaud-sync",
    "plaud-compare",
    "plaud-import",
    "plaud-resync",
    "plaud-full",
    "plaud-baseline",
    "plaud-catch-up",
)
CLI_WAIT = 60.0  # the CLI waits this long for a held `plaud`, then exits 75 (§9.4)


@contextmanager
def plaud_run(
    state: State, job: str, *, timeout: float, now: datetime, with_ops: bool = False
) -> Iterator[None]:
    """Hold `plaud` (and `ops` first, with_ops) for one run, announcing `job` on each. A lock not
    free within `timeout` seconds raises LockTimeout, holding nothing."""
    if job not in JOBS:
        raise ValueError(f"{job!r} is not a Plaud job")
    keys = (OPS_LOCK, PLAUD_LOCK) if with_ops else (PLAUD_LOCK,)
    with Locks(state.locks_dir, timeout=timeout).hold(*keys), ExitStack() as announced:
        for key in keys:
            announced.enter_context(announce(state.locks_dir, job, now, key))
        yield


def plaud_holder(state: State) -> tuple[str, datetime] | None:
    """Which Plaud job holds `plaud` now, and since when; None when it is free."""
    return running(state.locks_dir, PLAUD_LOCK)
