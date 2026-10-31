"""The Plaud worker (stage 2b spec §9.2, §16): how each scheduled turn ends.

`conclude_turn` is the one place the sync tells the outside how it is: it pings the sync's own
Healthchecks check only after a scheduled sync that is good and that nothing holds up. The check's
2-hour grace does the rest, so any lasting stop, a dead or wedged worker included, brings one email.
The answer is worked out afresh each turn from `state.db`, with no alert state of its own, so a
restart or a rebuilt `state.db` can't drop a ping or send one twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from recordings.alerts import AlertConfig, ping_url
from recordings.plaud.checks import PlaudDB
from recordings.plaud.health import RunOutcome, record_ping, record_run, sync_health
from recordings.state import State

log = logging.getLogger(__name__)


@dataclass
class TurnContext:
    state: State
    db: PlaudDB
    alerts: AlertConfig
    schedule_minutes: int
    clock: Callable[[], datetime]


def conclude_turn(ctx: TurnContext, ran: RunOutcome | None) -> bool:
    """End a scheduled turn, or a light turn inside a long run (§9.7, §16). True when it pinged and
    Healthchecks took it.

    `ran` is None when the turn ran no sync (the gate, a sign-in pause, a rate-limit wait, busy):
    nothing is sent. Otherwise the run is recorded (the failing streak, §11.7), then the sync's
    check is pinged at `alerts.deadman_sync_url`, the plain URL with an empty body, never `/start`
    or `/fail`, only when the run was good and `sync_health` says nothing holds it up. Any error
    while deciding sends nothing, and the 2-hour grace notices.

    This is where the sync's pushes go, when ntfy comes back for it (§16): one per blocker at
    onset, one when the streak turns 2 hours old, one when it works again. Nothing else in the
    worker tells the outside anything.
    """
    if ran is None:
        return False
    try:
        now = ctx.clock()
        good = record_run(ctx.db, ran, now=now)
        health = sync_health(ctx.state, schedule_minutes=ctx.schedule_minutes)
        url = ctx.alerts.deadman_sync_url
        if not (good and health.may_ping() and url):
            return False
    except Exception as exc:  # noqa: BLE001  any error while deciding sends nothing (§16)
        log.warning("conclude_turn: no ping (%s)", type(exc).__name__)
        return False
    status = ping_url(url)
    ok = status is not None and 200 <= status < 300
    try:
        record_ping(ctx.db, now=now, ok=ok, status=status)
    except Exception as exc:  # noqa: BLE001  the ping went; Status just won't show it
        log.warning("conclude_turn: the ping wasn't recorded (%s)", type(exc).__name__)
    return ok
