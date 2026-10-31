"""Sync health (stage 2b spec §11.7, §16): is a run good, what stops the ping, and why.

- **A good run** (§11.7): its listing was good (a small shrink counts) and, if it made any import,
  check or fetch attempts, at least one succeeded; and the disk guard refused no import. Attempts
  include recordings on the failing list; a degraded check, or an import whose audio couldn't be
  fetched, is an attempt without success. The caller counts them.
- **The failing streak** (`K_FAILING`: `{"since", "cause"}`) starts at the first run that isn't
  good, and recovers by its cause: a listing streak at the next good listing (a small shrink
  counts); a disk streak at the first run the guard doesn't refuse; a work streak only at a good
  run with a success. A run that ends a streak by its cause, but isn't good for another reason
  (every fetch failing after an outage, say), carries the streak on, `since` kept, under the new
  cause, so a quiet run after it can't ping "up" in the middle of a systemic failure. A good run
  with no attempts leaves a work streak as it is.
- **Blockers,** a closed list (§16), are `plaud_state` rows `blocker:<name>` holding
  `{"since", "detail"}`; one clears by deleting its row (2b phase 1 plan, decision 17).
- **The last turn** (`K_LAST_TURN`: `{"outcome", "at", "detail"}`) says why a scheduled turn ran no
  sync. A turn repeating the last one's outcome and detail (the gate, busy, a bad keep-out word)
  isn't written again, so `at` reads as "since" and a closed gate writes nothing every tick. The
  schedule off isn't a turn: `cause()` reads it from `schedule_minutes`.
- **`sync_health`** reads `plaud_state` only, so what Status says is what stopped the ping (§16).
  Phrases are §15.6's; none carries a title, a token or a URL. Times are read and shown through
  `plaud/when.py`, as every reader of these stamps does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal

from recordings.plaud.checks import (
    BLOCKER_PREFIX,
    BLOCKERS,
    K_CATCH_UP_DONE,
    K_FAILING,
    K_LAST_PING,
    K_LAST_TURN,
    PlaudDB,
)
from recordings.plaud.settings import BAD_WORD
from recordings.plaud.when import local_hhmm, parse_stamp
from recordings.state import State

Cause = Literal["listing", "disk", "work"]
Listing = Literal["good", "small-shrink", "held", "partial", "bad", "none"]
TurnOutcome = Literal["ran", "gate", "paused", "rate-limited", "busy", "idle-config", "error"]
BLOCKER_PHRASES = {
    "sign-in": "sign-in needed",
    "consent": "imports stopped: consent list",
    "hold": "held: see Status",
    "breaker": "paused: unexpected changes",
    "private-unwritten": "should be private: run `recordings reindex`",
}
DISK = "imports stopped: the disk is too full to import"
OFF = "scheduled sync is off"
GATE = "gate closed"
_CAUSES = ("listing", "disk", "work")


@dataclass(frozen=True)
class RunOutcome:
    listing: Listing
    attempts: int  # imports, checks and fetches tried, failing-list recordings included
    successes: int  # a degraded check, or an import whose audio couldn't be fetched, is not one
    disk_refused: bool  # the disk guard refused an import
    detail: str  # a §15.6 run phrase


def judge_run(outcome: RunOutcome) -> bool:
    return (
        outcome.listing in ("good", "small-shrink")
        and (outcome.attempts == 0 or outcome.successes > 0)
        and not outcome.disk_refused
    )


def _cause_of(outcome: RunOutcome) -> Cause:
    if outcome.listing not in ("good", "small-shrink"):
        return "listing"
    return "disk" if outcome.disk_refused else "work"


def _ends(cause: Cause, outcome: RunOutcome, good: bool) -> bool:
    if cause == "listing":
        # A good listing ends it; record_run then clears the streak, or carries it on under the
        # run's own cause (every fetch failing), `since` kept: never "up" mid-failure (§11.7).
        return outcome.listing in ("good", "small-shrink")
    if cause == "disk":
        return not outcome.disk_refused
    return good and outcome.successes > 0


def record_run(db: PlaudDB, outcome: RunOutcome, *, now: datetime) -> bool:
    """Start, carry or end the failing streak by its cause (§11.7). Returns judge_run."""
    good = judge_run(outcome)
    failing = db.get(K_FAILING)
    if not isinstance(failing, dict):
        if not good:
            db.put(K_FAILING, {"since": now.isoformat(), "cause": _cause_of(outcome)}, now=now)
        return good
    cause = failing.get("cause") if failing.get("cause") in _CAUSES else "work"  # the strictest
    if _ends(cause, outcome, good):
        if good:
            db.delete(K_FAILING)
        else:
            since = failing.get("since") if isinstance(failing.get("since"), str) else None
            carried = {"since": since or now.isoformat(), "cause": _cause_of(outcome)}
            db.put(K_FAILING, carried, now=now)
    return good


def open_blocker(
    db: PlaudDB, name: str, *, now: datetime, detail: Mapping[str, Any] | None = None
) -> None:
    """Open a blocker, keeping the first `since`; written only when new or its detail changed."""
    if name not in BLOCKERS:
        raise ValueError(f"{name!r} is not a blocker")
    key, detail = BLOCKER_PREFIX + name, dict(detail or {})
    current = db.get(key)
    if isinstance(current, dict) and isinstance(current.get("since"), str):
        if current.get("detail") == detail:
            return
        db.put(key, {"since": current["since"], "detail": detail}, now=now)
        return
    db.put(key, {"since": now.isoformat(), "detail": detail}, now=now)


def clear_blocker(db: PlaudDB, name: str) -> None:
    if name not in BLOCKERS:
        raise ValueError(f"{name!r} is not a blocker")
    db.delete(BLOCKER_PREFIX + name)


def record_turn(
    db: PlaudDB, outcome: TurnOutcome, *, now: datetime, detail: Mapping[str, Any] | None = None
) -> None:
    """How a scheduled turn ended (§9.7). A repeat of the last outcome and detail isn't written
    again, unless the turn ran: `at` then reads as "since"."""
    detail = dict(detail or {})
    last = db.get(K_LAST_TURN)
    if (
        outcome != "ran"
        and isinstance(last, dict)
        and last.get("outcome") == outcome
        and last.get("detail") == detail
    ):
        return
    db.put(K_LAST_TURN, {"outcome": outcome, "at": now.isoformat(), "detail": detail}, now=now)


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class SyncHealth:
    blockers: Mapping[str, datetime]  # name -> since
    failing: tuple[datetime, Cause] | None
    last_turn: tuple[TurnOutcome, datetime, Mapping[str, Any]] | None
    last_ping: tuple[datetime, bool, int | None] | None  # at, ok, HTTP status
    catch_up_done: datetime | None
    schedule_minutes: int

    def may_ping(self) -> bool:
        """No blocker open and no failing streak (§16)."""
        return not self.blockers and self.failing is None

    def cause(self, *, tz: tzinfo = UTC) -> str | None:
        """The first reason that applies, as a fixed phrase (§15.2), with any time in `tz`; None
        when nothing stops the ping."""
        for name in (*BLOCKERS, *sorted(set(self.blockers) - set(BLOCKERS))):
            if name in self.blockers:
                return BLOCKER_PHRASES.get(name, f"blocked: {name}")
        if self.failing is not None:
            since, cause = self.failing
            if cause == "disk":
                return DISK
            return f"the sync is failing (since {local_hhmm(since, tz)})"
        if self.schedule_minutes <= 0:
            return OFF
        if self.last_turn is None:
            return None
        outcome, _, detail = self.last_turn
        if outcome == "idle-config":
            return BAD_WORD
        if outcome == "paused":
            return BLOCKER_PHRASES["sign-in"]
        if outcome == "rate-limited":
            until = parse_stamp(detail.get("until"))
            return f"rate limited until {local_hhmm(until, tz)}" if until else "rate limited"
        if outcome == "busy":
            job, since = detail.get("job"), parse_stamp(detail.get("since"))
            held = f" since {local_hhmm(since, tz)}" if since else ""
            return f"skipped: busy (`{job}`){held}" if job else "skipped: busy"
        if outcome == "gate":
            reason = detail.get("reason")
            return reason if isinstance(reason, str) else GATE
        if outcome == "error":
            name = detail.get("error")
            return f"error: `{name}`" if isinstance(name, str) else "error"
        return None


def sync_health(state: State, *, schedule_minutes: int) -> SyncHealth:
    """What `plaud_state` says, and nothing else. Raises NoSyncYet without the table."""
    db = PlaudDB(state)
    blockers = {
        key.removeprefix(BLOCKER_PREFIX): parse_stamp(
            value.get("since") if isinstance(value, dict) else None
        )
        or EPOCH
        for key, value in db.with_prefix(BLOCKER_PREFIX).items()
    }
    failing = db.get(K_FAILING)
    failing_at = parse_stamp(failing.get("since")) if isinstance(failing, dict) else None
    turn = db.get(K_LAST_TURN)
    turn_at = parse_stamp(turn.get("at")) if isinstance(turn, dict) else None
    ping = db.get(K_LAST_PING)
    ping_at = parse_stamp(ping.get("at")) if isinstance(ping, dict) else None
    status = ping.get("status") if isinstance(ping, dict) else None
    return SyncHealth(
        blockers=blockers,
        failing=(
            (failing_at or EPOCH, failing["cause"] if failing.get("cause") in _CAUSES else "work")
            if isinstance(failing, dict)
            else None
        ),
        last_turn=(
            (turn["outcome"], turn_at, dict(turn.get("detail") or {}))
            if turn_at is not None and isinstance(turn.get("outcome"), str)
            else None
        ),
        last_ping=(
            (ping_at, bool(ping.get("ok")), status if isinstance(status, int) else None)
            if ping_at is not None
            else None
        ),
        catch_up_done=parse_stamp(db.get(K_CATCH_UP_DONE)),
        schedule_minutes=schedule_minutes,
    )
