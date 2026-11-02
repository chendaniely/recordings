"""The Plaud worker (stage 2b spec §9.2, §9.7, §16): the process, its heartbeat and `--alive`, and
how each scheduled turn ends.

The process (Task 13): `recordings worker` is one process with a main thread that owns its Writer
and a heartbeat thread (spec §9.2). The main thread ticks every 2 seconds: it re-reads the consent
list when its file changed, hides any title in the Plaud cache that the list or a keep-out word now
catches (§9.5), applies late consent listings, runs the new-pattern pass again for
what the last one left (§13.1 case 5), keeps the consent and private-unwritten blockers as the
list, the pass and the archive say, serves one request from the UI (§9.3: the kinds in HANDLERS,
at the end), and runs a scheduled turn when one is due (§9.7): `plaud.sync`'s run (area A5). It
imports no Plaud client, token store or sync at module level: the turn and the handlers import
them when they run, so `web`, which reads `alive` from here, never pulls them in.

`conclude_turn` is the one place the sync tells the outside how it is: it pings the sync's own
Healthchecks check only after a scheduled sync that is good and that nothing holds up. The check's
2-hour grace does the rest, so any lasting stop, a dead or wedged worker included, brings one email.
The answer is worked out afresh each turn from `state.db`, with no alert state of its own, so a
restart or a rebuilt `state.db` can't drop a ping or send one twice.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Literal, Protocol

from recordings import alerts
from recordings.alerts import AlertConfig, ping_url
from recordings.config import Config, ConfigError, load_config
from recordings.ids import ID_RE
from recordings.locks import LockTimeout
from recordings.plaud import keepout, requests, signin
from recordings.plaud.baseline import (
    KeepOutWordsError,
    PatternsPass,
    WholePassDue,
    enforce_word,
    enforced_words,
    patterns_pass,
    record_pass_error,
)
from recordings.plaud.cache import PREVIEW_KEPT, Cache, Preview, cache_path
from recordings.plaud.checks import (
    K_CONSENT,
    K_LAST_ENDED,
    K_LAST_STARTED,
    K_RETIRED,
    PRIVATE_UNWRITTEN,
    PlaudDB,
)
from recordings.plaud.flags import (
    KEPT_HERE,
    NOTHING_TO_KEEP,
    OWED_PATTERNS,
    LateReport,
    apply_late_consent,
    keep_here,
    private_owed,
    still_owed,
)
from recordings.plaud.gate import GATE_ALLOWED, GATE_REFUSAL, Gate, detect_rebuild, gate_now
from recordings.plaud.health import (
    RunOutcome,
    TurnOutcome,
    clear_blocker,
    open_blocker,
    rate_limit_wait,
    record_ping,
    record_run,
    record_turn,
    sync_health,
)
from recordings.plaud.hold import accept as accept_listing
from recordings.plaud.keepout import ConsentList, FileSignature
from recordings.plaud.requests import Claimed
from recordings.plaud.runlock import plaud_holder, plaud_run
from recordings.plaud.scratch import sweep_downloads
from recordings.plaud.settings import (
    BAD_WORD,
    DEFAULT_SCHEDULE_MINUTES,
    PlaudSettings,
    plaud_settings,
)
from recordings.plaud.when import parse_stamp
from recordings.state import State, StateError
from recordings.writer import NotTheWriter, Writer

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
    ok = alerts.accepted(status)
    try:
        record_ping(ctx.db, now=now, ok=ok, status=status)
    except Exception as exc:  # noqa: BLE001  the ping went; Status just won't show it
        log.warning("conclude_turn: the ping wasn't recorded (%s)", type(exc).__name__)
    return ok


# ---- the process (Task 13): a main thread that owns its Writer, and a heartbeat thread --------

TICK = 2.0  # seconds between the main thread's ticks (spec §9.2, §22)
# The Plaud cache's stamp is trusted only once its mtime is this old (nanoseconds): a file clock
# coarser than the time between two writes (a few milliseconds on ext4, a second on some others)
# could give a write just after the stamp the stamp's own mtime.
CACHE_SETTLED_NS = 3 * 10**9
HEARTBEAT = 30.0  # seconds between the heartbeat thread's writes
ALIVE_HEARTBEAT = timedelta(seconds=90)
ALIVE_PROGRESS = timedelta(minutes=15)
ALIVE_PROGRESS_STARTING = timedelta(minutes=30)
Phase = Literal[
    "starting",
    "idle",
    "checking",
    "rechecking",
    "turn",
    "request",
    "consent",
    "stopping",
    "failed",
]
# The patterns pass retried after the start (`rechecking`) is no part of a start: `alive` reads it
# by the ordinary rules, so a retry never inherits the start's `starting_since` (decision 48).
# A start that hasn't finished: starting, its patterns pass (`checking`), or a start that failed.
# Its heartbeat carries `starting_since`, which a restart keeps, so a crash loop at any point of
# the start is STUCK 30 minutes after its first start (decision 48).
UNFINISHED: tuple[Phase, ...] = ("starting", "checking", "failed")
SILENT = "the Plaud worker is silent"
STUCK = "the Plaud worker may be stuck"
FAILED = "the Plaud worker failed to start"
# `--alive` when config.toml can't be read: a fixed line, never the file's path or text.
NO_CONFIG = "the Plaud worker's config.toml can't be read: run `recordings doctor --role worker`"
HEARTBEAT_KEY = "worker_heartbeat"  # state.db's meta table (spec §9.5)
PRUNE_EVERY = timedelta(days=1)
WRITER_LOCK_WAIT = 10.0  # spec §9.2: Writer.open(cfg, docs=False, lock_timeout=10)
# A scheduled turn waits about a second for the `plaud` lock, so a probe at that instant (Status, a
# request's insert, the deploy guard) never makes it "busy" for a whole interval.
TURN_LOCK_WAIT = 1.0
# The request kinds whose run holds `plaud`: while another run holds it, they stay queued and wait
# their turn (§9.4), and the worker serves the other kinds meanwhile.
PLAUD_KINDS: frozenset[str] = frozenset(
    {"compare", "sync", "import", "accept", "accept-changes", "resync", "full"}
)
FINISH_TRIES = 3  # a request's end, written again a moment apart if state.db is busy
# An index that can't answer is retried at every tick; for this long it stops every late consent
# listing, and is shown as a pass that failed.
INDEX_PATIENCE = timedelta(seconds=60)
INDEX_UNAVAILABLE = "IndexUnavailable"  # the private-unwritten blocker's `error` then
# A whole-archive retry of the patterns pass (an attempt raised, and none of its pattern version
# has ended) runs at most once per scheduled turn, as the phase-end council worded it ("at each
# turn"): a sweep costs about 21 ms a recording (3.5 minutes at 10,000, the council measured) and
# holds the tick, so late consent listings and requests wait for it. With the schedule off, the
# default interval. Either way it is well inside the sync check's 2-hour grace, and the flags and
# the blocker hold meanwhile, so nothing possibly owed is let go.
WHOLE_RETRY_MINUTES_OFF = DEFAULT_SCHEDULE_MINUTES


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Progress:
    """What the main thread last did, shared with the heartbeat thread under a lock. The main
    thread bumps it at every tick, idle included (spec §9.2): a tick that never returns stops the
    counter, and `alive` then says STUCK, while the heartbeat thread keeps writing.

    `starting_since` is when this run of starts began: a worker whose previous start never finished
    (its last heartbeat was UNFINISHED: `starting`, `checking` or `failed`) keeps that start's time,
    so a worker that fails or dies and restarts over and over is STUCK after 30 minutes, never
    starting for ever. `error` is a failed start's exception class. `done` and `total` count the
    patterns pass at start, phase `checking`."""

    phase: Phase
    counter: int
    at: datetime
    starting_since: datetime | None = None  # None: `at`
    error: str | None = None
    done: int | None = None
    total: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.starting_since is None:
            self.starting_since = self.at

    def bump(self, now: datetime, phase: Phase | None = None) -> None:
        with self._lock:
            self.counter += 1
            self.at = now
            if phase is not None:
                self.phase = phase

    def fail(self, error: str) -> None:
        with self._lock:
            self.phase, self.error = "failed", error

    def check(
        self, now: datetime, done: int, total: int | None, *, phase: Phase = "checking"
    ) -> None:
        """One recording of the patterns pass: at start, phase `checking`, `done` of `total`; a
        retry after the start says `rechecking` (`total` None: not counted)."""
        with self._lock:
            self.counter += 1
            self.at, self.phase, self.done, self.total = now, phase, done, total

    def read(self) -> tuple[Phase, int, datetime]:
        with self._lock:
            return self.phase, self.counter, self.at

    def beat(self, now: datetime) -> dict[str, Any]:
        """The heartbeat's value: `starting_since` while the start is UNFINISHED (starting, its
        patterns pass, or failed), a failed start's `error`, and the patterns pass's `done` and
        `total` while `checking` or `rechecking`."""
        with self._lock:
            value: dict[str, Any] = {
                "at": now.isoformat(),
                "phase": self.phase,
                "progress_counter": self.counter,
                "progress_at": self.at.isoformat(),
            }
            if self.phase in UNFINISHED:
                value["starting_since"] = self.starting_since.isoformat()
            if self.phase == "failed":
                value["error"] = self.error
            if self.phase in ("checking", "rechecking"):
                value["done"], value["total"] = self.done, self.total
            return value


class SyncTurn(Protocol):
    def __call__(self, worker: Worker, *, light: bool) -> RunOutcome: ...


def sync_turn(worker: Worker, *, light: bool) -> RunOutcome:
    """The sync run as a scheduled turn, or the light turn of a due one inside a long run (§9.7,
    §9.2): `plaud.sync.worker_turn`, imported here, so that importing this module never loads the
    Plaud client, the token store or the sync (`web` reads `alive` from it)."""
    from recordings.plaud.sync import worker_turn

    return worker_turn(worker, light=light)


def raised(exc: BaseException) -> RunOutcome:
    """A turn that raised is a run that isn't good, never "no sync" (§11.7; Task 11's review): an
    attempt with no success, so a failing streak starts whose cause is the work's, which only a
    run with a success ends. A quiet run after it (good, no attempts) can't ping "up" while the
    failure may still be there."""
    return RunOutcome(
        listing="good",
        attempts=1,
        successes=0,
        disk_refused=False,
        detail=f"error: `{type(exc).__name__}`",
    )


def heartbeat_once(state: State, progress: Progress, *, now: datetime) -> None:
    """meta.worker_heartbeat, on a short connection of its own (State.set_meta)."""
    state.set_meta(HEARTBEAT_KEY, json.dumps(progress.beat(now)))


def parse_heartbeat(raw: str | None) -> dict | None:
    """A heartbeat row's value as a dict, or None when there is none or it isn't one."""
    try:
        beat = json.loads(raw) if raw else None
    except ValueError:
        return None
    return beat if isinstance(beat, dict) else None


def last_heartbeat(state: State) -> dict | None:
    """The last heartbeat, or None when there is none or it can't be read. Status and the request
    replies read it through here."""
    try:
        return parse_heartbeat(state.meta(HEARTBEAT_KEY))
    except (StateError, sqlite3.Error):
        return None


def _within(t: datetime | None, now: datetime, limit: timedelta) -> bool:
    """A stamp in the future (a clock put right after running ahead) counts as long ago, as in
    ops.due."""
    return t is not None and timedelta(0) <= now - t <= limit


def alive(state: State, *, now: datetime) -> tuple[bool, str | None]:
    """`beat_alive` on the last heartbeat. Spec §9.2: a heartbeat under 90 seconds old, and
    progress within 15 minutes. While the start is unfinished (`starting`, or its patterns pass,
    `checking`), within 30 minutes of `starting_since`, the first of the starts that never
    finished: a pass at start that takes longer reads STUCK, as a start does. While `starting`,
    that is all (Writer.open may rebuild the index for a long while); while `checking`, its
    progress counts too. A failed start is FAILED, however old. `recordings worker --alive` and
    Status read it."""
    return beat_alive(last_heartbeat(state), now=now)


def beat_alive(beat: dict | None, *, now: datetime) -> tuple[bool, str | None]:
    """`alive` for a heartbeat already read (the request insert reads it inside its transaction)."""
    if beat is None:
        return False, SILENT
    phase = beat.get("phase")
    if phase == "failed":
        return False, FAILED
    if not _within(parse_stamp(beat.get("at")), now, ALIVE_HEARTBEAT):
        return False, SILENT
    if phase in UNFINISHED:
        since = parse_stamp(beat.get("starting_since")) or parse_stamp(beat.get("progress_at"))
        if not _within(since, now, ALIVE_PROGRESS_STARTING):
            return False, STUCK
        if phase == "starting":
            return True, None
    if not _within(parse_stamp(beat.get("progress_at")), now, ALIVE_PROGRESS):
        return False, STUCK
    return True, None


def _starting_since(state: State, now: datetime) -> datetime:
    """The previous heartbeat's `starting_since` when its phase was UNFINISHED: that start never
    finished, so a restart doesn't renew the 30 minutes. Otherwise now."""
    beat = last_heartbeat(state)
    if beat is not None and beat.get("phase") in UNFINISHED:
        since = parse_stamp(beat.get("starting_since"))
        if since is not None and since <= now:
            return since
    return now


def _file_key(path: Path | None) -> tuple[FileSignature, int] | None:
    """What says the consent list changed: keepout.signature (inode, size and mtime) and the change
    time, which an edit that keeps the size and resets the mtime (`cp -p`, `touch -r`) still moves.
    None when it can't be stat'ed."""
    sig = keepout.signature(path)
    if sig is None or path is None:
        return None
    try:
        return sig, path.stat().st_ctime_ns
    except OSError:
        return None


class Stopping(Exception):
    """SIGTERM came during the patterns pass, a Plaud wait or a run: it ends between recordings
    (§9.2). A run stopped so is no failure: its turn records nothing and pings nothing."""


def _left(done: PatternsPass | None) -> tuple[frozenset[str], frozenset[str]]:
    """What a patterns pass left: its unwritten and its busy recordings."""
    if done is None:
        return frozenset(), frozenset()
    return frozenset(done.unwritten), frozenset(done.busy)


class Worker:
    """`recordings worker` (spec §9.2, §9.7): one process, a main thread that owns its Writer, and
    a heartbeat thread. The main thread ticks every TICK seconds: it re-reads the consent list
    when its file changed and applies late consent listings, retries the patterns pass for what
    the last one left, serves one request from the UI (a row in `plaud_requests`, checked again,
    §9.3), then runs a scheduled turn when one is due. It never imports the Plaud client, the
    token store or the sync at module level: its turn (`turn`, `sync_turn` unless a test hands
    in another) imports them when it runs, so `recordings_ui`, which reads `alive` from here,
    never pulls them in.

    No token, no consent list, or a bad keep_out_word leaves it up, healthy and idle (§9.2): the
    ticks go on, and Status and doctor say why. What each tick writes (K_CONSENT, the blockers) is
    worked out afresh from the list and state.db, and remembered only once written, so a failed
    write, or a row another process removed, is put right at the next tick. Logs carry fixed
    phrases, counts, IDs and exception class names only, never `exc_info` (§15.6)."""

    def __init__(
        self,
        cfg: Config,
        environ: Mapping[str, str],
        *,
        clock: Callable[[], datetime] = _utc_now,
        turn: SyncTurn = sync_turn,
        heartbeat: float = HEARTBEAT,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        if cfg.state_path is None or cfg.archive_path is None:
            raise NotTheWriter("set [archive] path and [state] path in config.toml first")
        self.cfg, self.environ, self.clock = cfg, environ, clock
        self.turn, self.heartbeat = turn, heartbeat
        self._client_factory = client_factory  # tests hand in the fake server's client
        self._client: Any | None = None
        # The run's newest complete listing, in memory only (titles never leave it): A5's run
        # sets it; Release's handlers read a title from it while it is one interval fresh.
        self.newest_listing: Any | None = None
        self._preview_at: datetime | None = None  # when the last Release preview was written
        self.state = State.open(cfg.state_path)
        self.db = PlaudDB(self.state)
        self.settings: PlaudSettings = plaud_settings(cfg)
        try:
            self.alerts = alerts.from_config(cfg, environ)
        except ConfigError as exc:  # doctor --role worker names it; the worker stays up
            log.warning(
                "the sync's ping URL can't be used (%s): no ping is sent", type(exc).__name__
            )
            self.alerts = AlertConfig(ntfy_server=None, topic=None, deadman_url=None)
        self.writer: Writer | None = None
        self.gate: Gate | None = None
        self.consent: ConsentList | None = None
        now = clock()
        self.progress = Progress("starting", 0, now, _starting_since(self.state, now))
        self.stopping = threading.Event()
        self._signature: tuple[FileSignature, int] | None = None  # the list as last read
        self._late: LateReport | None = None  # the last late consent pass, None if it failed
        self._late_failed: str | None = None  # that failure's class
        self._index_down: datetime | None = None  # since when the index couldn't answer
        self._logged: LateReport | None = None
        # The cache titles last checked: (list, words, the file's inode, size and mtime before it)
        self._purged: tuple | None = None
        self._checked = 0  # the patterns pass at start: recordings checked so far
        self._to_check: int | None = None  # and how many it checks, counted once it begins
        self._pass: PatternsPass | None = None  # the last patterns pass that ended; None: none due
        self._pass_failed: str | None = None  # the class it last raised as a whole, if it did
        self._whole_due = False  # no attempt of this pattern version has ended (WholePassDue)
        self._rebuild_owed = False  # the start's check for a rebuilt state.db raised: try again
        self._whole_at: datetime | None = None  # when the last whole-archive attempt began
        minutes = self.settings.schedule_minutes
        self._whole_every = timedelta(minutes=minutes if minutes > 0 else WHOLE_RETRY_MINUTES_OFF)
        self._not_before: datetime | None = None  # a turn that didn't run waits one interval
        self._held_by_gate = False  # the last turn found the gate closed
        self._held_by_pause = False  # the last turn found the sign-in paused
        self._pruned_at: datetime | None = None
        self._failing: dict[str, str] = {}  # step -> the class last logged
        self._beat_failed: str | None = None
        self._beating: threading.Thread | None = None
        # A long run of this process holds `plaud` (Sync everything, a large Re-sync, the CLI's):
        # what it serves between two recordings runs under that hold, never taking it again.
        self._holding = False
        self._serving = False  # serving between two recordings now: never again inside that
        self._cli = False  # a CLI run's stand-in (`for_cli`): no start, no patterns pass
        self._ctx = TurnContext(
            self.state, self.db, self.alerts, self.settings.schedule_minutes, clock
        )

    @classmethod
    def for_cli(cls, cfg: Config, environ: Mapping[str, str], *, writer: Writer, client: Any):
        """The worker's loop code for a CLI long run in the `worker` container (`recordings plaud
        sync`, `sync --full`, `resync`; §9.2: "This holds for the CLI's long runs too"): its
        `long_between` serves the light turn of a due scheduled sync (which can ping, through
        the same `deadman_sync_url`) and single-recording requests, under the CLI's hold. It is
        never started: no heartbeat, and no patterns pass, so it keeps no `private-unwritten`
        flag (the worker does). Its first light turn waits one schedule interval: a CLI run that
        has just begun never runs one at once."""
        w = cls(cfg, environ, client_factory=lambda: client)
        w.writer, w._cli, w._holding = writer, True, True
        minutes = w.settings.schedule_minutes or DEFAULT_SCHEDULE_MINUTES
        w._not_before = w.clock() + timedelta(minutes=minutes)
        return w

    # ---- start (spec §9.2's order) ------------------------------------------------------------
    def start(self) -> None:
        """1. the heartbeat thread, phase `starting`; 2. the leftovers of a download stopped with
        the worker, in `.tmp/plaud-dl/` (area A4), under a non-blocking try of `plaud`; 3. (Task
        14) the requests left over; 4. the Writer; 5. the gate, and a breaker that tripped under
        an older normaliser closed by this release (§14.4, area B1). 6. the configured keep-out word
        recorded as enforced (decision 26), and a new pattern's pass (decision 27), under the
        phase `checking`, which SIGTERM stops between recordings. The pass is part of the start:
        a crash during it keeps the first start's time, as a crash before it does. The start is
        then done, phase `idle`, written to the heartbeat at once (a start that follows failed
        ones must not read STUCK until the next beat)."""
        self._start_heartbeat()
        self._step("downloads", self._sweep_downloads)
        self._requests_at_start()
        self.writer = self._open_writer()
        self.gate = self._open_gate()
        found = self._step("rebuild", self._detect_rebuild)
        self._rebuild_owed = found is None  # it raised: each tick tries again (§11.6)
        if found:
            self.gate = self._open_gate()
        self._step("breaker", self._close_on_release)
        self._step("manifests", self._manifests)
        self._enforce()
        self.progress.bump(self.clock(), "idle")
        self._beat()

    def _sweep_downloads(self) -> None:
        """§9.2 step 2: a download in flight when the worker stopped is abandoned; its scratch
        file goes here. A CLI run holding `plaud` owns what is there, and keeps it."""
        if self.cfg.archive_path is None:
            return
        swept = sweep_downloads(self.cfg.archive_path, self.state)
        if swept is None:
            log.info("downloads: a run holds the sync, so its scratch files stay")
        elif swept:
            log.info("downloads: %d left by a stopped download removed", swept)

    def _start_heartbeat(self) -> None:
        self._beat()  # at once, so `--alive` passes from the first second
        self._beating = threading.Thread(
            target=self._heartbeat_loop, name="recordings-heartbeat", daemon=True
        )
        self._beating.start()

    def _heartbeat_loop(self) -> None:
        while not self.stopping.wait(self.heartbeat):
            self._beat()

    def _beat(self) -> None:
        try:
            heartbeat_once(self.state, self.progress, now=self.clock())
        except (StateError, sqlite3.Error, OSError) as exc:
            if self._beat_failed != type(exc).__name__:  # once per kind of failure, not every 30 s
                log.warning("could not write the heartbeat (%s)", type(exc).__name__)
            self._beat_failed = type(exc).__name__
        else:
            self._beat_failed = None

    def _open_writer(self) -> Writer:
        """Writer.open with the worker's 10-second lock wait and no docs (the archive's docs are
        the web app's and the CLI's to write). Only LockTimeout, a lock another process holds past
        those 10 seconds (web rebuilding the index, say), is tried again, each TICK, bumping the
        progress, so `--alive` stays green meanwhile. Any other refusal ends the start: `run`
        writes phase `failed`."""
        while True:
            self.progress.bump(self.clock())
            try:
                return Writer.open(self.cfg, docs=False, lock_timeout=WRITER_LOCK_WAIT)
            except LockTimeout:
                log.info("the archive is busy: opening it again shortly")
                if self.stopping.wait(TICK):
                    raise

    def _open_gate(self) -> Gate:
        gate = gate_now(self.db)
        if not gate.open:
            log.info("gate closed: %s", gate.reason)
        return gate

    def _detect_rebuild(self) -> bool:
        """§11.6: a rebuilt `state.db` (2b snapshots, no mark row) is recorded and passed at once:
        `rebuilt_at`, the gate open, a mark. Nothing stops. Every run that lists looks again
        first, under its own hold (`plaud.sync.rebuild_check`)."""
        found = detect_rebuild(self.db, self.writer.archive, now=self.clock())
        if found:
            log.warning(
                "the sync's records were rebuilt: what was made before now and isn't here waits "
                "for review"
            )
        return found

    def _rebuild_again(self) -> None:
        """The start's check for a rebuilt `state.db` raised: tried again at each tick until it
        answers (the end-of-branch privacy review, 2026-11-02), so the gate doesn't stay closed
        on a rebuild nothing found. A failure here is `_step`'s, logged once by its class."""
        if not self._rebuild_owed or self.writer is None:
            return
        found = self._detect_rebuild()
        self._rebuild_owed = False
        if found:
            self.gate = self._open_gate()

    def _close_on_release(self) -> bool:
        """§14.4: a release that bumps NORMALISER_VERSION closes the breaker by itself, at start
        (the trip recorded the version it saw). Status then watches for a re-trip."""
        from recordings.plaud import breaker

        return breaker.close_on_release(self.db, now=self.clock())

    def _manifests(self) -> None:
        """Sync everything's manifests folder, made if missing (§14.6, decision 9): `[backup]
        extra_paths` names it, and a missing extra path refuses every backup."""
        from recordings.plaud.full import ensure_manifest_dir

        if self.cfg.state_path is not None:
            ensure_manifest_dir(self.cfg.state_path)

    def _enforce(self) -> None:
        now = self.clock()
        if self.settings.keep_out_word_ok:
            self._step(
                "keep-out word", lambda: enforce_word(self.db, self.settings.keep_out_word, now=now)
            )
        else:
            log.warning("%s: scheduled syncs stay idle", BAD_WORD)
        done = self._step("patterns", lambda: self._patterns(now))
        if done is not None:
            log.info("%s", done.sentence())

    def _patterns(self, now: datetime, *, again: bool = False) -> PatternsPass | None:
        """Decision 27's pass. At start, the whole archive under the phase `checking`: each
        recording it checks bumps the progress and the heartbeat's `done` of `total`, so Status
        reads "(checking, 412 of 1,234)" (spec §15.2). `again` (a tick after the start): what the
        last attempt that ended left, from its record; or, once WholePassDue said none of this
        version has ended, the whole archive. A retry runs under the phase `rechecking` ("checking
        titles again", a whole one with its count), which `alive` reads by its ordinary rules, so
        a retry never claims the start's time (decision 48), and logs no line per recording.
        SIGTERM stops it between recordings (§9.2); a pass stopped so isn't recorded as complete,
        and the next start runs it again (reconcile skips what it already judged).

        What it ends with is kept for `_owed_private`: the recordings it couldn't check (never
        the busy ones) each open a private-unwritten flag. One that raises as a whole is recorded
        (`record_pass_error`) and remembered by its class, which opens that blocker (the
        phase-end council's G1 and S2). It logs that once per class, and once when an attempt
        ends again, here rather than by `_step`: a retry that waits for its turn, or finds a
        whole sweep due, has neither failed nor worked."""
        whole = not again or self._whole_due
        if whole:
            self._whole_at = now
        try:
            if not again:
                done = patterns_pass(self.writer, self.db, now=now, progress=self._check_one)
            else:
                self.progress.check(now, 0, None, phase="rechecking")
                done = patterns_pass(
                    self.writer,
                    self.db,
                    now=now,
                    progress=self._rechecker(whole=whole),
                    retry=not whole,
                    quiet=True,
                )
        except WholePassDue:
            self._whole_due = True  # nothing was checked: a whole sweep waits for its turn
            return None
        except Stopping:
            log.info("the patterns pass stopped between recordings: the next start runs it again")
            return None
        except Exception as exc:  # noqa: BLE001  remembered, recorded and logged once, by class
            if self._pass_failed != type(exc).__name__:
                log.warning("patterns failed (%s)", type(exc).__name__)
            self._pass_failed = type(exc).__name__
            with contextlib.suppress(Exception):  # state.db may be what failed: the blocker says it
                record_pass_error(self.db, self._pass_failed, now=now)
            return None
        if self._pass_failed is not None:
            log.info("patterns works again")
        if again and done is not None and _left(done) != _left(self._pass):
            log.info("patterns pass, again: %s", done.sentence())
        self._pass, self._pass_failed, self._whole_due = done, None, False
        return done

    def _patterns_again(self) -> None:
        """The patterns pass again, at every tick, while the last attempt left recordings
        unchecked or raised, so a `recordings reindex` that mends a recording clears its flag at
        the next tick, never only at a restart (§13.1 case 5). A whole-archive retry runs at most
        once per turn (WHOLE_RETRY_MINUTES_OFF's reason); nothing at all once a pass is complete.
        The phase is `idle` again once the retry ends, however it ends."""
        if self.writer is None:
            return
        if (
            not self._whole_due
            and self._pass_failed is None
            and (self._pass is None or self._pass.complete)
        ):
            return
        now = self.clock()
        if (
            self._whole_due
            and self._whole_at is not None
            and timedelta(0) <= now - self._whole_at < self._whole_every
        ):
            return  # a stamp in the future counts as long ago, as in `due`
        try:
            self._patterns(now, again=True)
        finally:
            self.progress.bump(self.clock(), "idle")

    def _rechecker(self, *, whole: bool) -> Callable[[], None]:
        """The progress of a retry: phase `rechecking` at each recording, counted against the
        archive's Plaud recordings when it is a whole one. SIGTERM ends it here, between
        recordings (§9.2)."""
        done, total = 0, None

        def one() -> None:
            nonlocal done, total
            if self.stopping.is_set():
                raise Stopping
            if whole and total is None:
                total = self._plaud_count()
            done += 1
            self.progress.check(self.clock(), done, total, phase="rechecking")

        return one

    def _check_one(self) -> None:
        """Called before each recording the pass checks: SIGTERM ends the pass here, between
        recordings."""
        if self.stopping.is_set():
            raise Stopping
        if self._to_check is None:  # the pass has begun: count what it checks, once
            self._to_check = self._plaud_count()
        self._checked += 1
        self.progress.check(self.clock(), self._checked, self._to_check)

    def _plaud_count(self) -> int:
        return sum(
            any(s.kind == "plaud" for s in rec.sources)
            for rec in self.writer.archive.iter_recordings()
        )

    def _start_failed(self, exc: BaseException) -> None:
        """A start that fails says so in the heartbeat (phase `failed`, the error's class), which
        `alive` reads as not alive, rather than leaving `starting` for the next restart to renew.
        The heartbeat thread stops first, so it can't write over it."""
        self.stopping.set()
        if self._beating is not None:
            self._beating.join(timeout=5)
        self.progress.fail(type(exc).__name__)
        self._beat()
        log.warning("the worker couldn't start (%s)", type(exc).__name__)

    # ---- the loop ----------------------------------------------------------------------------
    def run(self) -> int:
        """start, then a tick every TICK seconds until SIGTERM, which stops it between ticks."""
        previous = signal.signal(signal.SIGTERM, self._on_sigterm)
        try:
            try:
                self.start()
            except Exception as exc:
                if isinstance(exc, LockTimeout) and self.stopping.is_set():
                    return 0  # SIGTERM while the archive was busy
                self._start_failed(exc)
                raise
            while not self.stopping.is_set():
                self.tick()
                self.stopping.wait(TICK)
            return 0
        finally:
            self.progress.bump(self.clock(), "stopping")
            self.stopping.set()
            if self._beating is not None:
                self._beating.join(timeout=5)
            signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)

    def _on_sigterm(self, signum: int, frame: object) -> None:
        self.stopping.set()

    def tick(self) -> None:
        """One pass of the main thread (spec §9.2). Each step's failure is logged once by its class
        and left for the next tick: nothing here ends the loop."""
        now = self.clock()
        self.progress.bump(now, "idle")
        self._step("consent list", self._watch_consent)
        self._step("consent blocker", self._consent_blocker)
        self._step("rebuild", self._rebuild_again)
        self._step("cache titles", self._purge_cache)
        self._step("release previews", self._expire_previews)
        self._step("late consent", self._late_consent)
        self._step("patterns", self._patterns_again)
        self._step("owed private", self._owed_private)
        self._step("request", self._serve_one)
        self._step("turn", self._turn_if_due)
        self._step("sync everything", self._full_if_due)
        if self._pruned_at is None or not timedelta(0) <= now - self._pruned_at < PRUNE_EVERY:
            self._step("prune", self._prune)

    def _step(self, name: str, fn: Callable[[], Any]) -> Any:
        """One step. A failure is logged once per step and class, not at every tick, and the
        step's recovery once."""
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001  one step's failure must not end the loop
            if self._failing.get(name) != type(exc).__name__:
                log.warning("%s failed (%s)", name, type(exc).__name__)
                self._failing[name] = type(exc).__name__
            return None
        if self._failing.pop(name, None) is not None:
            log.info("%s works again", name)
        return result

    # ---- the consent list (spec §12.1, decision 13) -------------------------------------------
    def _watch_consent(self) -> None:
        """Read the list again when its file changed (inode, size, times), or while the last read
        was a stop. A read counts only when the file is the same before and after it (Review Focus
        2): a list caught mid-write keeps the previous one in force, and is read again next tick.
        What was read is remembered only once K_CONSENT holds it, so a write that fails is tried
        again at the next tick."""
        now = self.clock()
        path = self.settings.consent_list
        before = _file_key(path)
        if self.consent is not None and self.consent.readable and before == self._signature:
            return
        reading = keepout.read_consent(path)
        if _file_key(path) != before:
            return
        if reading != self.consent or before != self._signature:
            changed = None if before is None else before[0].mtime_ns / 1e9
            self.db.put(
                K_CONSENT,
                {
                    "count": len(reading.ids),
                    "file_changed": None
                    if changed is None
                    else datetime.fromtimestamp(changed, tz=UTC).isoformat(),
                    "read_at": now.isoformat(),
                    "stop": reading.stop,
                    "typo_lines": list(reading.typo_lines),
                    "review_lines": list(reading.review_lines),
                },
                now=now,
            )
        self.consent, self._signature = reading, before

    def _consent_blocker(self) -> None:
        """Decision 13: the `consent` blocker is open while the list in force is a stop, and
        cleared while it reads cleanly, written at every tick (both calls write nothing when the
        row is already so), so Status shows a typo within seconds."""
        if self.consent is None:
            return
        if self.consent.stop is not None:
            open_blocker(self.db, "consent", now=self.clock(), detail={"stop": self.consent.stop})
        else:
            clear_blocker(self.db, "consent")

    def _purge_cache(self) -> None:
        """The cache's titles of recordings not here (spec §9.5, the outline's P1): a title the
        consent list now names, or a keep-out word now matches, is nulled at the tick, never left
        for the next compare; during a consent stop, or with keep-out words that can't be read,
        every title goes. Asked again only when the list in force, the words or the file
        changed. The file's stamp is taken before the purge, so a write that lands during or
        after it (a CLI run's compare) reads as a change at the next tick; and a stamp younger
        than CACHE_SETTLED_NS is never kept, since a coarse file clock could give that write the
        same mtime."""
        if self.cfg.state_path is None:
            return
        path = cache_path(self.cfg.state_path)
        seen = time.time_ns()  # the file's clock, not self.clock: its mtime is compared with it
        try:
            st = path.stat()
        except FileNotFoundError:
            return  # no listing has run yet
        stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
        try:
            words: tuple[str, ...] | None = enforced_words(self.db)
        except KeepOutWordsError:
            words = None  # no title can be checked against them: none is kept
        if (self.consent, words, stamp) == self._purged:
            return
        hidden = Cache(self.cfg.state_path).purge_titles(consent=self.consent, words=words)
        if hidden:
            log.info("the Plaud cache: %d titles hidden by the consent list or a word", hidden)
        settled = seen - st.st_mtime_ns >= CACHE_SETTLED_NS
        self._purged = (self.consent, words, stamp) if settled else None

    def _expire_previews(self) -> None:
        """A Release preview no one answered goes after 10 minutes (§9.5): asked only while one
        this worker wrote may still be there."""
        if self._preview_at is None or self.cfg.state_path is None:
            return
        now = self.clock()
        Cache(self.cfg.state_path).expire_previews(now=now)
        if now - self._preview_at > PREVIEW_KEPT:
            self._preview_at = None

    # ---- the worker's one Plaud client (area A4's Release; A5's run shares it) ---------------
    def plaud_client(self) -> Any:
        """The worker's Plaud client: the token store and the API base from `[plaud]`, and one
        Throttle for the process (§10.2), whose waits bump the heartbeat's progress and stop on
        SIGTERM. Imported here: the worker never loads the client at module level."""
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                from recordings.plaud.client import PlaudClient, Throttle
                from recordings.plaud.tokens import TokenStore

                store = TokenStore(
                    self.settings.token_store, token_url=self.settings.token_url, clock=self.clock
                )
                self._client = PlaudClient(
                    store,
                    api_base=self.settings.api_base,
                    throttle=Throttle(progress=self._waiting),
                )
        return self._client

    def _waiting(self) -> None:
        """A slice of a Plaud wait, or a MiB of a download (§9.2): progress, and SIGTERM."""
        self.progress.bump(self.clock())
        if self.stopping.is_set():
            raise Stopping

    def between_recordings(self) -> None:
        """Between two recordings of a run (§9.2): progress, SIGTERM (`Stopping`), and the tick's
        privacy steps, so a long Sync now or import never holds back a consent edit: the list is
        read again when it changed, its blocker kept, the cache's titles purged, late consent
        listings applied and the `private-unwritten` flags kept, as at every tick. The phase
        stays the run's."""
        self._waiting()
        phase = self.progress.read()[0]
        self._step("consent list", self._watch_consent)
        self._step("consent blocker", self._consent_blocker)
        self._step("cache titles", self._purge_cache)
        self._step("late consent", self._late_consent)
        if not self._cli:  # the flags follow the worker's own patterns pass, which a CLI lacks
            self._step("owed private", self._owed_private)
        self.progress.bump(self.clock(), phase)

    def long_between(self) -> None:
        """Between two recordings of a long run that holds `plaud` (Sync everything, a large
        Re-sync, the CLI's syncs; §9.2): what `between_recordings` does, then the light turn of a
        due scheduled sync (`scheduled_turn(light=True)`), then one queued Compare or Re-sync of
        one recording. Other requests wait, and say so ("Waiting for Sync everything (412 of
        1,234)"). Never inside itself: what it serves runs with the plain `between`."""
        self.between_recordings()
        if self._serving:
            return
        phase = self.progress.read()[0]
        self._serving, held = True, self._holding
        self._holding = True  # whatever runs from here runs under the long run's hold
        try:
            self._step("light turn", self._light_turn_if_due)
            self._step("request", self._serve_inside)
        finally:
            self._serving, self._holding = False, held
            self.progress.bump(self.clock(), phase)

    def _light_turn_if_due(self) -> None:
        if self.due():
            self.scheduled_turn(light=True)

    def _serve_inside(self) -> None:
        now = self.clock()
        requests.expire(self.state, now=now)
        claimed = requests.claim_next(self.state, now=now, inside=True)
        if claimed is not None:
            self.serve_request(claimed)

    def _hold(self, job: str) -> contextlib.AbstractContextManager:
        """`plaud` for a run of `job`, waiting TURN_LOCK_WAIT, unless a long run of this process
        holds it already (`long_between` serves under that hold)."""
        if self._holding:
            return contextlib.nullcontext()
        return plaud_run(self.state, job, timeout=TURN_LOCK_WAIT, now=self.clock())

    def _full_if_due(self) -> None:
        """Sync everything from the tick (§14.6): an unfinished one resumes; otherwise one runs 30
        days after the last finished one while the schedule is on (`fullstate.due`). Never while
        another run holds `plaud`, the gate is closed, the sign-in is paused, a rate limit's wait
        runs or the keep-out word is bad: no Plaud call then, and the next tick asks again."""
        from recordings.plaud import fullstate  # network-free: the run itself loads the client

        now = self.clock()
        if not fullstate.due(self.db, now=now, schedule_minutes=self.settings.schedule_minutes):
            return
        if (
            not self.settings.keep_out_word_ok
            or plaud_holder(self.state) is not None
            or not gate_now(self.db).open
            or signin.paused(self.db, self.settings.token_store) is not None
            or rate_limit_wait(self.db, now=now) is not None
        ):
            return
        try:
            with plaud_run(self.state, "plaud-full", timeout=TURN_LOCK_WAIT, now=now):
                report = self._run_full(by="schedule")
        except LockTimeout:
            return
        except Stopping:
            return  # unfinished: it resumes at the next start
        log.info("Sync everything by itself: %s", report.line())

    def _run_full(self, *, by: str) -> Any:
        """Sync everything under the hold the caller took, its `between` the long run's."""
        from recordings.plaud import full, sync

        self.progress.bump(self.clock(), "turn")
        self._holding = True
        try:
            run = sync.worker_run(self, between=self.long_between)
            report = full.run_full(run, by=by)
            record_full(self.state, report, finished=self.clock())
            return report
        finally:
            self._holding = False
            self.progress.bump(self.clock(), "idle")

    def listed_now(self, plaud_id: str) -> Any | None:
        """This ID's row as Plaud lists it now: from the run's newest listing while it is one
        schedule interval fresh, else from a fresh listing (which then becomes the newest). None
        when Plaud doesn't list it. A listing that ends on an error raises it, unless the row was
        read before the error."""
        from recordings.refs import canonical_plaud_id
        from recordings.sources.plaud import PlaudSource

        pid, now = canonical_plaud_id(plaud_id), self.clock()
        minutes = self.settings.schedule_minutes or DEFAULT_SCHEDULE_MINUTES
        listing = self.newest_listing
        fresh = listing is not None and now - listing.at <= timedelta(minutes=minutes)
        if not fresh:
            listing = PlaudSource(self.plaud_client(), clock=self.clock).list_all()
            if listing.complete:
                self.newest_listing = listing
        row = next((r for r in listing.rows if r.canonical_id == pid), None)
        if row is None and not listing.complete and listing.error is not None:
            raise listing.error
        return row

    def next_sync_at(self) -> datetime | None:
        """When the next scheduled turn comes due (§9.2): None with the schedule off or the gate
        closed."""
        minutes = self.settings.schedule_minutes
        if minutes <= 0 or not gate_now(self.db).open:
            return None
        stamps = [parse_stamp(self.db.get(k)) for k in (K_LAST_STARTED, K_LAST_ENDED)]
        past = [t for t in stamps if t is not None]
        now = self.clock()
        return max(max(past) + timedelta(minutes=minutes), now) if past else now

    def _here(self, plaud_id: str) -> bool:
        from recordings.plaud.flags import listed_here
        from recordings.refs import canonical_plaud_id

        w = self.writer
        found, unavailable = listed_here(
            w.index, w.identity.uuid, w.state, w.archive, frozenset({canonical_plaud_id(plaud_id)})
        )
        if unavailable:
            raise LookupError("the index can't answer now")
        return bool(found)

    def _late_consent(self) -> None:
        """Late consent listings (spec §12.1), from the list in force and the index alone. A pass
        that fails as a whole is remembered by its class, and an index that can't answer for
        INDEX_PATIENCE as INDEX_UNAVAILABLE: `_owed_private` then opens the `private-unwritten`
        blocker with it, since every `private` the pass would have written is still owed."""
        if self.writer is None or self.consent is None:
            return
        now = self.clock()
        self.progress.bump(now, "consent")
        try:
            report = apply_late_consent(self.writer, self.db, self.consent, now=now)
        except Exception as exc:
            self._late, self._late_failed = None, type(exc).__name__
            raise
        self._late, self._late_failed = report, None
        if not report.index_unavailable:
            self._index_down = None
        elif self._index_down is None:
            self._index_down = now
        elif now - self._index_down >= INDEX_PATIENCE:
            self._late_failed = INDEX_UNAVAILABLE
        if report != self._logged:
            self._log_late(report)
        self._logged = report
        self.progress.bump(self.clock(), "idle")

    def _log_late(self, report: LateReport) -> None:
        if report.index_unavailable:
            log.warning("the index couldn't answer: late consent listings wait for the next tick")
        counts = {
            "tagged private": len(report.tagged),
            "flagged": len(report.flagged),
            "cleared": len(report.cleared),
            "busy": len(report.busy),
            "not written": len(report.unwritten),
        }
        if any(counts.values()):
            log.info(
                "late consent listings: %s",
                ", ".join(f"{n} {what}" for what, n in counts.items() if n),
            )

    def _owed_private(self) -> None:
        """Spec §16's `private-unwritten`, its own step, so it runs whatever the late consent pass
        did. A `private` the pass owed but couldn't write (a file edited outside the app, a full
        disk) opens one flag per recording, its detail naming the exception's class (`cause`, from
        Task 8's report); each flag resolves once the `private` isn't owed any more
        (`flags.still_owed`, against the list in force).

        A recording the patterns pass couldn't check (§13.1 case 5; never one only busy) may owe
        `private` too (P8), so it opens the same flag, with `"owed": "patterns"`. That flag stays
        while the last attempt that ended still lists the recording, unwritten or busy (busy
        checks nothing), and while the pass is failing or a whole sweep is due, when nothing says
        what was checked; it resolves once the recording is checked, private or gone
        (`flags.private_owed`). One flag per recording: flags that resolve go first, so a
        recording the consent list now owes gets that flag in the same tick, with no tick between
        without one.

        The blocker is open while any flag is, or while the last late consent pass or patterns
        pass failed as a whole (its detail names the error's class: `error` or `patterns_error`),
        and is written afresh at every tick."""
        if self.writer is None:
            return
        now, archive = self.clock(), self.writer.archive
        unwritten = set(self._late.unwritten) if self._late is not None else set()
        causes = self._late.causes if self._late is not None else {}
        unchecked = {
            rid: cause
            for rid, cause in (self._pass.unwritten.items() if self._pass is not None else ())
            if ID_RE.fullmatch(rid)  # a folder whose name isn't an ID is no recording to flag
        }
        left = {*unchecked, *(self._pass.busy if self._pass is not None else ())}
        unknown = self._pass_failed is not None or self._whole_due
        for event in self.db.open_flags((PRIVATE_UNWRITTEN,)):
            rid = event.recording_id
            if event.detail.get("owed") == OWED_PATTERNS:
                owed = (unknown or rid in left) and rid not in unwritten
                owed = owed and private_owed(archive, rid)
            else:
                owed = rid in unwritten or still_owed(archive, event, self.consent)
            if not owed:
                self.db.resolve(event.id, now=now)
        flagged = {e.recording_id for e in self.db.open_flags((PRIVATE_UNWRITTEN,))}
        for rid in sorted(unwritten - flagged):
            detail = {"owed": "consent", **({"cause": causes[rid]} if rid in causes else {})}
            self._flag_unwritten(rid, detail, now=now)
        for rid in sorted(unchecked.keys() - unwritten - flagged):
            if private_owed(archive, rid):
                detail = {"owed": OWED_PATTERNS, "cause": unchecked[rid]}
                self._flag_unwritten(rid, detail, now=now)
        failed = {
            **({"error": self._late_failed} if self._late_failed is not None else {}),
            **({"patterns_error": self._pass_failed} if self._pass_failed is not None else {}),
        }
        if failed:
            open_blocker(self.db, PRIVATE_UNWRITTEN, now=now, detail=failed)
        elif self.db.open_flags((PRIVATE_UNWRITTEN,)):
            open_blocker(self.db, PRIVATE_UNWRITTEN, now=now)
        else:
            clear_blocker(self.db, PRIVATE_UNWRITTEN)

    def _flag_unwritten(
        self, recording_id: str, detail: Mapping[str, str], *, now: datetime
    ) -> None:
        with contextlib.suppress(sqlite3.IntegrityError):  # another writer just added it
            self.db.add_event(
                PRIVATE_UNWRITTEN, now=now, recording_id=recording_id, detail=detail, open_flag=True
            )

    # ---- scheduled turns (spec §9.7) ----------------------------------------------------------
    def due(self) -> bool:
        """schedule_minutes after max(last_started, last_ended), which persist, so a worker that
        crashes and restarts doesn't start a sync at every start. A stamp in the future counts
        as long ago. A turn that didn't run (the gate, busy) waits one interval, in memory."""
        minutes = self.settings.schedule_minutes
        if minutes <= 0 or self.db.get(K_RETIRED) is not None:
            return False  # DAN-15's end retires the sync: no scheduled turn (§17.5)
        now, interval = self.clock(), timedelta(minutes=minutes)
        if self._not_before is not None and now < self._not_before <= now + interval:
            return False
        stamps = (parse_stamp(self.db.get(K_LAST_STARTED)), parse_stamp(self.db.get(K_LAST_ENDED)))
        past = [t for t in stamps if t is not None and t <= now]
        return not past or now >= max(past) + interval

    def _turn_if_due(self) -> None:
        """The gate, read at every tick: while the last turn found it closed, the catch-up opening
        it ends the wait, so the first sync after the catch-up starts at the next tick, not an
        interval later. Likewise the sign-in: while the last turn found it paused, a new token file
        (`make plaud-sign-in`) ends the wait. Then a due scheduled turn."""
        self.gate = gate_now(self.db)
        if self._held_by_gate and self.gate.open:
            self._not_before, self._held_by_gate = None, False
        if self._held_by_pause and signin.paused(self.db, self.settings.token_store) is None:
            self._not_before, self._held_by_pause = None, False
        if self.due():
            self.scheduled_turn()

    def scheduled_turn(self, *, light: bool = False) -> None:
        """Spec §9.7: a bad keep_out_word, the gate closed, the sign-in paused (no token file, or
        the one Plaud refused, §10.3), a rate limit's wait still running (§10.2: a run ended
        "rate limited until 14:40", `record_rate_limit`) or `plaud` held: each is recorded and
        pings nothing, with no Plaud call. A rate limit's turn comes back when its wait ends, or
        an interval later if that is sooner. Otherwise the turn runs under `plaud`
        (`plaud-sync`), and conclude_turn decides the ping.

        `light`: the light turn of a due scheduled sync, served between two recordings of a long
        run that already holds `plaud` (§9.2): the same checks, then the listing, title checks and
        up to 20 imports, under that hold, and conclude_turn as for any turn."""
        now = self.clock()
        outcome: TurnOutcome | None = None
        detail: Mapping[str, Any] | None = None
        until: datetime | None = None
        self.gate = gate_now(self.db)
        if not self.settings.keep_out_word_ok:
            outcome = "idle-config"
        elif not self.gate.open:
            outcome, detail = "gate", {"reason": self.gate.reason}
        elif (why := signin.paused(self.db, self.settings.token_store)) is not None:
            outcome, detail = "paused", {"why": why}
        elif (until := rate_limit_wait(self.db, now=now)) is not None:
            outcome, detail = "rate-limited", {"until": until.isoformat()}
        if outcome is None and light:
            conclude_turn(self._ctx, self._run_turn(now, light=True))
            return
        if outcome is None:
            try:
                with plaud_run(self.state, "plaud-sync", timeout=TURN_LOCK_WAIT, now=now):
                    ran = self._run_turn(now)
            except LockTimeout:
                holder = plaud_holder(self.state)
                outcome = "busy"
                detail = (
                    {"job": holder[0], "since": holder[1].isoformat()} if holder is not None else {}
                )
            else:
                conclude_turn(self._ctx, ran)
                return
        record_turn(self.db, outcome, now=now, detail=detail)
        self._held_by_gate = outcome == "gate"
        self._held_by_pause = outcome == "paused"
        self._not_before = now + timedelta(minutes=self.settings.schedule_minutes)
        if outcome == "rate-limited" and until is not None:
            self._not_before = min(self._not_before, until)
        conclude_turn(self._ctx, None)  # no sync ran: no ping

    def _run_turn(self, now: datetime, *, light: bool = False) -> RunOutcome | None:
        """The turn under `plaud`. One that raised is recorded as `error` and judged as a run that
        isn't good (`raised`); one SIGTERM stopped between recordings ran no whole sync, and
        records and pings nothing (None). A light turn's SIGTERM stops the long run around it
        too."""
        self.db.put(K_LAST_STARTED, now.isoformat(), now=now)
        self.progress.bump(now, "turn")
        ran: RunOutcome | None = None
        try:
            ran = self.turn(self, light=light)
        except Stopping:
            log.info("the scheduled sync stopped between recordings: the worker is stopping")
            if light:
                self.db.put(K_LAST_ENDED, self.clock().isoformat(), now=self.clock())
                raise
        except Exception as exc:  # noqa: BLE001  a failed turn is recorded, never the loop's end
            record_turn(self.db, "error", now=self.clock(), detail={"error": type(exc).__name__})
            log.warning("the scheduled sync failed (%s)", type(exc).__name__)
            ran = raised(exc)
        else:
            record_turn(self.db, "ran", now=self.clock(), detail={"result": ran.detail})
        ended = self.clock()
        self.db.put(K_LAST_ENDED, ended.isoformat(), now=ended)
        self.progress.bump(ended, "idle")
        return ran

    def _prune(self) -> None:
        now = self.clock()
        self.db.prune(now=now)
        requests.prune(self.state, now=now)
        self._pruned_at = now

    # ---- requests from the UI (Task 14, spec §9.3) --------------------------------------------
    def _requests_at_start(self) -> None:
        interrupted, expired = requests.at_start(self.state, now=self.clock())
        if interrupted or expired:
            log.info("requests at start: %d interrupted, %d expired", interrupted, expired)

    def _serve_one(self) -> None:
        """The oldest queued request. While another run holds `plaud` (the CLI's catch-up, say),
        the kinds that need it stay queued, and wait their turn (§9.4); the others are served."""
        now = self.clock()
        requests.expire(self.state, now=now)
        skip = PLAUD_KINDS if plaud_holder(self.state) is not None else frozenset()
        claimed = requests.claim_next(self.state, now=now, skip=skip)
        if claimed is not None:
            self.serve_request(claimed)

    def serve_request(self, claimed: Claimed) -> None:
        """The row is checked again against fresh facts, never taken on trust (spec §9.3). A kind
        with no handler yet is refused with NOT_YET (web refuses it before inserting, so this is a
        defence); then the gate (every kind but compare and keep-here waits for the first
        catch-up); then the kind's handler."""
        req = claimed.request
        self.progress.bump(self.clock(), "request")
        refused: str | None = None
        try:
            if (handler := HANDLERS.get(req.kind)) is None:
                refused = requests.NOT_YET
            elif req.kind not in GATE_ALLOWED and not gate_now(self.db).open:
                refused = GATE_REFUSAL
            else:
                reply = handler(self, claimed)
        except Stopping:  # SIGTERM between two recordings: what came in stays, the rest waits
            log.info("a %s request stopped between recordings: the worker is stopping", req.kind)
            reply = requests.Reply(req.request_id, req.kind, "failed", requests.INTERRUPTED)
        except Exception as exc:  # noqa: BLE001  a failed request is answered, never the loop's end
            log.warning("a %s request failed (%s)", req.kind, type(exc).__name__)
            reply = requests.Reply(
                req.request_id, req.kind, "failed", f"error: `{type(exc).__name__}`"
            )
        if refused is not None:
            end = partial(
                requests.refuse, self.state, req.request_id, message=refused, now=self.clock()
            )
        else:
            end = partial(
                requests.finish,
                self.state,
                req.request_id,
                ok=reply.state == "done",
                message=reply.message,
                result=reply.result,
                now=self.clock(),
            )
        self._answer(req.kind, end)
        self.progress.bump(self.clock(), "idle")

    def _answer(self, kind: str, end: Callable[[], None]) -> None:
        """Write a request's end, tried FINISH_TRIES times a moment apart (the phase-end council's
        R1): a row left `running` would be followed by its session until the next start."""
        for attempt in range(FINISH_TRIES):
            try:
                end()
                return
            except (StateError, sqlite3.Error, OSError) as exc:
                if attempt == FINISH_TRIES - 1:
                    log.warning(
                        "a %s request's end couldn't be written (%s)", kind, type(exc).__name__
                    )
                    return
                self.stopping.wait(TICK / 4)


def main(argv: list[str] | None = None) -> int:
    """`recordings worker [--alive]`: the daemon, or the compose health check. `--alive` exits 0 or
    1 and nothing else, 1 with a fixed line when config.toml can't be read too: Docker's health
    check gives a meaning to 0 and 1 only. The daemon exits 78 when it can't start as configured,
    1 when anything else stops it."""
    parser = argparse.ArgumentParser(prog="recordings worker")
    parser.add_argument("--alive", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(os.environ)
    except ConfigError as exc:
        if args.alive:
            print(NO_CONFIG)
            return 1
        print(f"recordings worker: {exc}", file=sys.stderr)
        return 78
    if args.alive:
        state = State.read_only(cfg.state_path) if cfg.state_path is not None else None
        ok, why = (False, SILENT) if state is None else alive(state, now=_utc_now())
        if not ok:
            print(why)
        return 0 if ok else 1
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )  # the container clock is UTC: say so, as the backup service's loop does
    try:
        return Worker(cfg, os.environ).run()
    except (NotTheWriter, StateError, ConfigError) as exc:
        # Fixed text: doctor names what is wrong (spec §15.6).
        print(
            f"recordings worker: can't start ({type(exc).__name__}): run "
            "`recordings doctor --role worker`",
            file=sys.stderr,
        )
        return 78
    except Exception as exc:  # noqa: BLE001  its class only: a traceback would carry its text
        print(f"recordings worker: stopped ({type(exc).__name__})", file=sys.stderr)
        return 1


# ---- the worker's request handlers (Task 14): Keep it here, and Accept this listing (area A3) ----
Handler = Callable[[Worker, Claimed], requests.Reply]


def _keep_here(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Keep it here (spec §12.2): each recording's open late keep-out resolves; no file changes."""
    req, now = claimed.request, worker.clock()
    kept, nothing = [], []
    for rid in req.recording_ids:
        (kept if keep_here(worker.db, rid, now=now) == KEPT_HERE else nothing).append(rid)
    return requests.Reply(
        req.request_id,
        req.kind,
        "done" if kept else "failed",
        KEPT_HERE if kept else NOTHING_TO_KEEP,
        result={"kept": kept, "nothing_to_keep": nothing},
    )


def _accept(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Accept this listing (spec §11.4 step 4): under `plaud`, checked again against the consent
    list in force and the kept-out records now (`hold.accept`). The gate has already refused it
    before the first catch-up (the CLI's `accept-listing` alone accepts the catch-up's listing).
    A run holding `plaud` (the CLI's) answers busy; the press can be made again."""
    req, now = claimed.request, worker.clock()
    try:
        with plaud_run(worker.state, "plaud-accept", timeout=TURN_LOCK_WAIT, now=now):
            done = accept_listing(worker.db, consent=worker.consent, now=now)
    except LockTimeout:
        holder = plaud_holder(worker.state)
        busy = f"skipped: busy (`{holder[0]}`)" if holder is not None else "skipped: busy"
        return requests.Reply(req.request_id, req.kind, "failed", busy)
    result: dict[str, Any] = {"gone": list(done.gone)}
    if done.why is not None:
        result["why"] = done.why
    if done.gone:
        log.info("accepted a held listing: %d marked gone from Plaud", len(done.gone))
    return requests.Reply(
        req.request_id,
        req.kind,
        "failed" if done.state == "refused" else "done",
        done.message,
        result=result,
    )


# ---- Release (area A4, spec §12.2): its preview, Let it in, and Undo -------------------------
def _one_id(claimed: Claimed) -> str | None:
    req = claimed.request
    if len(req.plaud_ids) != 1 or req.recording_ids or req.select_all:
        return None
    return req.plaud_ids[0]


def _failed(claimed: Claimed, message: str) -> requests.Reply:
    return requests.Reply(claimed.request.request_id, claimed.request.kind, "failed", message)


def _plaud_failure(exc: BaseException) -> str:
    """A Plaud error's fixed phrase (§15.6), or its class: never its text."""
    from recordings.plaud.client import PlaudError

    return (
        f"failed: {exc.message}"
        if isinstance(exc, PlaudError)
        else f"error: `{type(exc).__name__}`"
    )


def _release_preview(worker: Worker, claimed: Claimed) -> requests.Reply:
    """§12.2 step 1: the recording's current title, written to the cache only, keyed by this
    request's own ID; the web app shows it once in Release's question. Refused, with no title
    fetched, for a consent-listed ID or during a consent stop (the gate is the serve_request's),
    for a re-keyed match (§11.4 step 6) and while Plaud's IDs look changed (§11.4 step 3,
    `release.may_preview`); and when the title still carries a keep-out word, which is never
    shown."""
    from recordings.plaud import release
    from recordings.plaud.keepout import keep_out_match

    plaud_id = _one_id(claimed)
    if plaud_id is None:
        return _failed(claimed, release.ONE_ID)
    worker._watch_consent()  # the list as it is now
    verdict = release.may_preview(plaud_id, worker.consent, worker.db, here=worker._here(plaud_id))
    if not verdict.ok:
        return _failed(claimed, verdict.message)
    try:
        listed = worker.listed_now(plaud_id)
    except Exception as exc:  # noqa: BLE001  a Plaud error's phrase, or a class
        return _failed(claimed, _plaud_failure(exc))
    if listed is None:
        return _failed(claimed, release.NOT_LISTED)
    word = keep_out_match(listed.title, worker.writer.keep_out_words())
    if word is not None:
        return _failed(claimed, release.refused_word(word))
    now = worker.clock()
    Cache(worker.cfg.state_path).put_preview(
        claimed.request.request_id,
        Preview(listed.canonical_id, listed.title or "", listed.recorded_at, listed.duration_ms),
        now=now,
    )
    worker._preview_at = now
    return requests.Reply(
        claimed.request.request_id,
        claimed.request.kind,
        "done",
        release.ASK,
        result={"plaud_id": listed.canonical_id},
    )


def _release(worker: Worker, claimed: Claimed) -> requests.Reply:
    """§12.2 step 3: checked again, against the consent list as it is now and the current
    title against every enforced word, then released: it comes in at the next sync."""
    from zoneinfo import ZoneInfo

    from recordings.plaud import release
    from recordings.plaud.when import local_hhmm

    plaud_id = _one_id(claimed)
    if plaud_id is None:
        return _failed(claimed, release.ONE_ID)
    worker._watch_consent()
    here = worker._here(plaud_id)
    early = release.may_ask(plaud_id, worker.consent, worker.db, here=here)
    if not early.ok:  # no listing is read for one that can't be released anyway
        return _failed(claimed, early.message)
    try:
        listed = worker.listed_now(plaud_id)
    except Exception as exc:  # noqa: BLE001  a Plaud error's phrase, or a class
        return _failed(claimed, _plaud_failure(exc))
    if listed is None:
        return _failed(claimed, release.NOT_LISTED)
    verdict = release.may_release(
        plaud_id,
        listed.title,
        worker.consent,
        worker.writer.keep_out_words(),
        worker.db,
        here=here,
    )
    if not verdict.ok:
        return _failed(claimed, verdict.message)
    kind = release.release(worker.db, listed.listed_id, now=worker.clock())
    log.info("released a recording kept out (%s)", kind)
    at = worker.next_sync_at()
    zone = ZoneInfo(worker.cfg.default_timezone)
    line = release.released_line(local_hhmm(at, zone) if at is not None else None)
    return requests.Reply(
        claimed.request.request_id,
        claimed.request.kind,
        "done",
        line,
        result={"plaud_id": listed.canonical_id},
    )


def _unrelease(worker: Worker, claimed: Claimed) -> requests.Reply:
    """§12.2 step 4: Undo, until a sync has fetched it."""
    from recordings.plaud import release

    plaud_id = _one_id(claimed)
    if plaud_id is None:
        return _failed(claimed, release.ONE_ID)
    done = release.unrelease(worker.db, plaud_id, here=worker._here(plaud_id), now=worker.clock())
    state = "done" if done == release.UNDONE else "failed"
    return requests.Reply(claimed.request.request_id, claimed.request.kind, state, done)


# ---- the sync's requests (area A5, §9.3, §15.1): Compare, Sync now, the imports, Park ---------
IMPORTING = "Importing"  # a running import's words: the screen adds "(5 of 31)" (§11.5)
NOTHING_NAMED = "Name the recordings to import, or press Import all"
PARKED = "Parked: it no longer counts against All here, and syncs leave it alone"
UNPARKED = "Unparked"
NOTHING_TO_PARK = "Nothing to park: the sync hasn't listed it, or it is parked already"
NOTHING_TO_UNPARK = "Nothing to unpark"


def _sync_request(
    worker: Worker,
    claimed: Claimed,
    *,
    mode: str,
    job: str,
    line: Callable[[Any], str],
    named: tuple[str, ...] = (),
    import_all: bool = False,
) -> requests.Reply:
    """A person's run (§9.3), under `plaud` for its whole length: `plaud.sync.run_sync` checks the
    gate, a hold, a rate limit's wait, the consent list and every recording again itself. Its
    progress goes to the row ("Importing", done and total); its reply is `line`'s, its result
    the run's counts and IDs, never a title. It records no turn and pings nothing (§16)."""
    from recordings.plaud import sync

    req = claimed.request

    def progress(done: int, total: int) -> None:
        requests.progress(worker.state, req.request_id, done=done, total=total, message=IMPORTING)

    run = sync.worker_run(worker, progress=progress if mode != "compare" else None)
    try:
        with worker._hold(job):  # inside a long run (a Compare between two recordings), its hold
            report = sync.run_sync(run, mode=mode, named=named, import_all=import_all)
    except LockTimeout:
        holder = plaud_holder(worker.state)
        busy = f"skipped: busy (`{holder[0]}`)" if holder is not None else "skipped: busy"
        return requests.Reply(req.request_id, req.kind, "failed", busy)
    log.info("a %s request: %s", req.kind, sync.summary(report))
    ran = report.listing in ("good", "small-shrink") and report.exit_code in (
        sync.EXIT_OK,
        sync.EXIT_PARTIAL,
    )
    return requests.Reply(
        req.request_id, req.kind, "done" if ran else "failed", line(report), result=report.to_dict()
    )


def _compare(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Compare (§11.1): the listing and the buckets, nothing imported. It runs while the gate is
    closed, and never moves the last good listing or a hold."""
    from recordings.plaud import sync

    return _sync_request(
        worker, claimed, mode="compare", job="plaud-compare", line=sync.compared_line
    )


def _sync(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Sync now (§15.1): the scheduled sync's run, now, with no cap, budget or backoff."""
    from recordings.plaud import sync

    return _sync_request(worker, claimed, mode="sync", job="plaud-sync", line=sync.synced_line)


def _import(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Import selected (named Plaud IDs, which may bring in one that needs review, §11.6) and
    Import all (`all`: every recording not here that can come in, review rows left out), §11.5."""
    from recordings.plaud import sync

    req = claimed.request
    if not req.plaud_ids and not req.select_all:
        return _failed(claimed, NOTHING_NAMED)
    return _sync_request(
        worker,
        claimed,
        mode="import",
        job="plaud-import",
        line=sync.imported_line,
        named=req.plaud_ids,
        import_all=req.select_all,
    )


def _parking(worker: Worker, claimed: Claimed, *, park: bool) -> requests.Reply:
    """Park and Unpark (§14.6, §15.1). A Plaud ID that failed to import: parked, syncs and Import
    all leave it alone, and it no longer counts against "All here"; unparked, it is as it was
    (one that failed 3 times waits for Import selected). A recording here that fails every check
    (area B2, `resync.park_recordings`): parked, the sweep leaves it alone and it doesn't count
    against Sync everything or §11.7, and a Re-sync still tries it; unparked, it is checked at
    the next run. Checked again here: a recording that isn't here or didn't come from Plaud is
    "nothing"."""
    from recordings.plaud import resync

    req, now = claimed.request, worker.clock()
    if req.recording_ids:
        found = resync.park_recordings(
            worker.writer.archive, worker.db, req.recording_ids, park=park, now=now
        )
        message = (
            (resync.PARKED_LINE if found.done else resync.NOTHING_TO_PARK)
            if park
            else (resync.UNPARKED_LINE if found.done else resync.NOTHING_TO_UNPARK)
        )
        key = "parked" if park else "unparked"
        return requests.Reply(
            req.request_id,
            req.kind,
            "done" if found.done else "failed",
            message,
            result={key: list(found.done), "nothing": list(found.nothing)},
        )
    done, nothing = [], []
    for pid in req.plaud_ids:
        row = worker.db.listing(pid)
        if row is None or (row.parked_at is not None) == park:
            nothing.append(pid)
            continue
        worker.db.set_listing(pid, parked_at=now if park else None)
        done.append(pid)
    if done:
        log.info("%s %d Plaud IDs", "parked" if park else "unparked", len(done))
    message = (
        (PARKED if done else NOTHING_TO_PARK) if park else (UNPARKED if done else NOTHING_TO_UNPARK)
    )
    result = {"parked" if park else "unparked": done, "nothing": nothing}
    return requests.Reply(
        req.request_id, req.kind, "done" if done else "failed", message, result=result
    )


def _park(worker: Worker, claimed: Claimed) -> requests.Reply:
    return _parking(worker, claimed, park=True)


def _unpark(worker: Worker, claimed: Claimed) -> requests.Reply:
    return _parking(worker, claimed, park=False)


# ---- Accept these changes (area B1, §14.4, decision 11) -------------------------------------
def _accept_changes(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Accept these changes: under `plaud` (`plaud-breaker`), checked again against the breaker as
    stored now (`breaker.accept_changes`): refused while it isn't open, or when no Accept is
    offered (a part found volatile, or a field accepted before). Accepted, every recording's next
    check is excused and spread over `full_sweep_days`, the breaker closes with its window starting
    now, and the press goes to `ops_runs` (`plaud-accept-changes`), field names only."""
    from recordings.plaud import breaker

    req, now = claimed.request, worker.clock()
    try:
        with plaud_run(worker.state, "plaud-breaker", timeout=TURN_LOCK_WAIT, now=now):
            done = breaker.accept_changes(
                worker.db, now=now, full_sweep_days=worker.settings.full_sweep_days
            )
    except LockTimeout:
        holder = plaud_holder(worker.state)
        busy = f"skipped: busy (`{holder[0]}`)" if holder is not None else "skipped: busy"
        return requests.Reply(req.request_id, req.kind, "failed", busy)
    detail = done.message + (f" ({', '.join(done.fields)})" if done.fields else "")
    with contextlib.suppress(sqlite3.Error, OSError):  # the press is recorded when it can be
        worker.state.record_run("plaud-accept-changes", now, worker.clock(), done.ok, detail)
    return requests.Reply(
        req.request_id,
        req.kind,
        "done" if done.ok else "failed",
        done.message,
        result={"fields": list(done.fields), "rebaselined": done.rebaselined},
    )


# ---- Re-sync and Sync everything (area B2, §14.6) -------------------------------------------
def _resync(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Re-sync (§14.6, decision 6): the recordings named, checked now under `plaud`
    (`plaud-resync`), each as the sweep checks it, a parked one too (a success unparks it); its
    progress on the row ("Re-syncing", done and total); its reply one recording's line, or the
    selection's end line, and its result the counts, the updated IDs and each recording's own
    line, never a title. The newest listing is reused while one schedule interval fresh. More than
    `resync.LONG` recordings is a long run: between two of them it serves the light turn and a
    single-recording request (§9.2). Served between two recordings of another long run (one
    recording), it runs under that run's hold."""
    from recordings.plaud import resync, sync

    req, now = claimed.request, worker.clock()
    if not req.recording_ids:
        return _failed(claimed, resync.NOTHING_NAMED)
    if len(req.recording_ids) > requests.MAX_RESYNC_IDS:
        return _failed(claimed, resync.TOO_MANY)

    def progress(done: int, total: int) -> None:
        requests.progress(
            worker.state, req.request_id, done=done, total=total, message=resync.RESYNCING
        )

    long = len(req.recording_ids) > resync.LONG and not worker._holding
    between = worker.long_between if long else worker.between_recordings
    run = sync.worker_run(worker, progress=progress, between=between)
    minutes = worker.settings.schedule_minutes or DEFAULT_SCHEDULE_MINUTES
    listing = worker.newest_listing
    if listing is not None and now - listing.at > timedelta(minutes=minutes):
        listing = None
    held = worker._holding
    try:
        with worker._hold("plaud-resync"):
            worker._holding = held or long
            report = resync.resync(run, req.recording_ids, listing=listing)
    except LockTimeout:
        holder = plaud_holder(worker.state)
        busy = f"skipped: busy (`{holder[0]}`)" if holder is not None else "skipped: busy"
        return requests.Reply(req.request_id, req.kind, "failed", busy)
    finally:
        worker._holding = held
    state = "done" if report.phrase == resync.OK else "failed"
    return requests.Reply(req.request_id, req.kind, state, report.line(), result=report.to_dict())


def _full(worker: Worker, claimed: Claimed) -> requests.Reply:
    """Sync everything (§14.6), pressed: checked again against fresh facts (refused within an hour
    of the last finished one; an unfinished one resumes), then the whole run under `plaud`
    (`plaud-full`), serving the light turn and single-recording requests between two recordings
    (§9.2). Its reply: "Sync everything: all 1,234 checked (2 parked, 1 degraded)", or what is
    missing and why; its result the counts and IDs (the flagged by archive ID), never a title."""
    from recordings.plaud import full

    req, now = claimed.request, worker.clock()
    if (why := full.refusal(worker.db, now=now)) is not None:
        return _failed(claimed, why)
    try:
        with plaud_run(worker.state, "plaud-full", timeout=TURN_LOCK_WAIT, now=now):
            report = worker._run_full(by="request")
    except LockTimeout:
        holder = plaud_holder(worker.state)
        busy = f"skipped: busy (`{holder[0]}`)" if holder is not None else "skipped: busy"
        return requests.Reply(req.request_id, req.kind, "failed", busy)
    line = f"Sync everything: {report.line()}"
    if not report.ok:
        line += " · See Status"
    state = "failed" if report.run_failed else "done"
    return requests.Reply(req.request_id, req.kind, state, line, result=report.to_dict())


def record_full(state: State, report: Any, *, finished: datetime) -> None:
    """A Sync everything in `ops_runs` (§9.5's reuse, job `plaud-full`): its line, never a
    title."""
    try:
        state.record_run("plaud-full", report.started, finished, report.ok, report.line())
    except (sqlite3.Error, OSError) as exc:
        log.warning("Sync everything wasn't recorded in ops_runs (%s)", type(exc).__name__)


# What the worker serves. requests.HANDLED names the same kinds for web, which refuses the others
# before inserting; each later area adds its handlers here and its kinds there.
HANDLERS: dict[str, Handler] = {
    "keep-here": _keep_here,
    "accept": _accept,
    "release-preview": _release_preview,
    "release": _release,
    "unrelease": _unrelease,
    "compare": _compare,
    "sync": _sync,
    "import": _import,
    "park": _park,
    "unpark": _unpark,
    "accept-changes": _accept_changes,
    "resync": _resync,
    "full": _full,
}
