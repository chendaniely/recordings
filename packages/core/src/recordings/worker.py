"""The Plaud worker (stage 2b spec §9.2, §9.7, §16): the process, its heartbeat and `--alive`, and
how each scheduled turn ends.

The process (Task 13): `recordings worker` is one process with a main thread that owns its Writer
and a heartbeat thread (spec §9.2). The main thread ticks every 2 seconds: it re-reads the consent
list when its file changed, applies late consent listings, keeps the consent and
private-unwritten blockers as the list and the archive say, and runs a scheduled turn when one is
due (§9.7). It imports no Plaud client, token store or sync at module level: Phase 2 passes its
turn in, so `web`, which reads `alive` from here, never pulls them in.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from recordings import alerts
from recordings.alerts import AlertConfig, ping_url
from recordings.config import Config, ConfigError, load_config
from recordings.locks import LockTimeout
from recordings.plaud import keepout, requests
from recordings.plaud.baseline import PatternsPass, enforce_word, patterns_pass
from recordings.plaud.checks import (
    K_CONSENT,
    K_LAST_ENDED,
    K_LAST_STARTED,
    PRIVATE_UNWRITTEN,
    PlaudDB,
)
from recordings.plaud.flags import (
    KEPT_HERE,
    NOTHING_TO_KEEP,
    LateReport,
    apply_late_consent,
    keep_here,
    still_owed,
)
from recordings.plaud.gate import GATE_ALLOWED, GATE_REFUSAL, Gate, gate_now
from recordings.plaud.health import (
    RunOutcome,
    TurnOutcome,
    clear_blocker,
    open_blocker,
    record_ping,
    record_run,
    record_turn,
    sync_health,
)
from recordings.plaud.keepout import ConsentList, FileSignature
from recordings.plaud.requests import Claimed
from recordings.plaud.runlock import plaud_holder, plaud_run
from recordings.plaud.settings import BAD_WORD, PlaudSettings, plaud_settings
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
    ok = status is not None and 200 <= status < 300
    try:
        record_ping(ctx.db, now=now, ok=ok, status=status)
    except Exception as exc:  # noqa: BLE001  the ping went; Status just won't show it
        log.warning("conclude_turn: the ping wasn't recorded (%s)", type(exc).__name__)
    return ok


# ---- the process (Task 13): a main thread that owns its Writer, and a heartbeat thread --------

TICK = 2.0  # seconds between the main thread's ticks (spec §9.2, §22)
HEARTBEAT = 30.0  # seconds between the heartbeat thread's writes
ALIVE_HEARTBEAT = timedelta(seconds=90)
ALIVE_PROGRESS = timedelta(minutes=15)
ALIVE_PROGRESS_STARTING = timedelta(minutes=30)
Phase = Literal["starting", "idle", "checking", "turn", "request", "consent", "stopping", "failed"]
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
# An index that can't answer is retried at every tick; for this long it stops every late consent
# listing, and is shown as a pass that failed.
INDEX_PATIENCE = timedelta(seconds=60)
INDEX_UNAVAILABLE = "IndexUnavailable"  # the private-unwritten blocker's `error` then


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

    def check(self, now: datetime, done: int, total: int) -> None:
        """One recording of the patterns pass at start: phase `checking`, `done` of `total`."""
        with self._lock:
            self.counter += 1
            self.at, self.phase, self.done, self.total = now, "checking", done, total

    def read(self) -> tuple[Phase, int, datetime]:
        with self._lock:
            return self.phase, self.counter, self.at

    def beat(self, now: datetime) -> dict[str, Any]:
        """The heartbeat's value: `starting_since` while the start is UNFINISHED (starting, its
        patterns pass, or failed), a failed start's `error`, and the patterns pass's `done` and
        `total` while `checking`."""
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
            if self.phase == "checking":
                value["done"], value["total"] = self.done, self.total
            return value


class SyncTurn(Protocol):
    def __call__(self, worker: Worker, *, light: bool) -> RunOutcome: ...


def stand_in_turn(worker: Worker, *, light: bool) -> RunOutcome:
    """Phase 1 has no sync: Phase 2 passes its own turn to Worker. The gate can't open in Phase 1
    (nothing can run the first catch-up yet), so scheduled_turn never reaches this."""
    raise NotImplementedError("the sync turn comes in stage 2b's Phase 2")


def heartbeat_once(state: State, progress: Progress, *, now: datetime) -> None:
    """meta.worker_heartbeat, on a short connection of its own (State.set_meta)."""
    state.set_meta(HEARTBEAT_KEY, json.dumps(progress.beat(now)))


def _heartbeat(state: State) -> dict | None:
    """The last heartbeat, or None when there is none or it can't be read."""
    try:
        raw = state.meta(HEARTBEAT_KEY)
        beat = json.loads(raw) if raw else None
    except (StateError, sqlite3.Error, ValueError):
        return None
    return beat if isinstance(beat, dict) else None


def _within(t: datetime | None, now: datetime, limit: timedelta) -> bool:
    """A stamp in the future (a clock put right after running ahead) counts as long ago, as in
    ops.due."""
    return t is not None and timedelta(0) <= now - t <= limit


def alive(state: State, *, now: datetime) -> tuple[bool, str | None]:
    """Spec §9.2: a heartbeat under 90 seconds old, and progress within 15 minutes. While the start
    is unfinished (`starting`, or its patterns pass, `checking`), within 30 minutes of
    `starting_since`, the first of the starts that never finished: a pass at start that takes
    longer reads STUCK, as a start does. While `starting`, that is all (Writer.open may rebuild the
    index for a long while); while `checking`, its progress counts too. A failed start is FAILED,
    however old. `recordings worker --alive` and Status read it."""
    beat = _heartbeat(state)
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
    beat = _heartbeat(state)
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


class _Stopped(Exception):
    """SIGTERM came during the patterns pass at start: it ends between recordings (§9.2)."""


class Worker:
    """`recordings worker` (spec §9.2, §9.7): one process, a main thread that owns its Writer, and
    a heartbeat thread. The main thread ticks every TICK seconds: it re-reads the consent list
    when its file changed and applies late consent listings, then runs a scheduled turn when one
    is due. It never imports the Plaud client, the token store or the sync at module level: Phase
    2 passes its turn in (`turn`), so `recordings_ui`, which reads `alive` from here, never
    pulls them in.

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
        turn: SyncTurn = stand_in_turn,
        heartbeat: float = HEARTBEAT,
    ) -> None:
        if cfg.state_path is None or cfg.archive_path is None:
            raise NotTheWriter("set [archive] path and [state] path in config.toml first")
        self.cfg, self.environ, self.clock = cfg, environ, clock
        self.turn, self.heartbeat = turn, heartbeat
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
        self._checked = 0  # the patterns pass at start: recordings checked so far
        self._to_check: int | None = None  # and how many it checks, counted once it begins
        self._not_before: datetime | None = None  # a turn that didn't run waits one interval
        self._held_by_gate = False  # the last turn found the gate closed
        self._pruned_at: datetime | None = None
        self._failing: dict[str, str] = {}  # step -> the class last logged
        self._beat_failed: str | None = None
        self._beating: threading.Thread | None = None
        self._ctx = TurnContext(
            self.state, self.db, self.alerts, self.settings.schedule_minutes, clock
        )

    # ---- start (spec §9.2's order) ------------------------------------------------------------
    def start(self) -> None:
        """1. the heartbeat thread, phase `starting`; 2. (Task 14) the requests left over; 3. the
        Writer; 4. the gate. (§9.2's sweep of `.tmp/plaud-dl/` comes with Phase 2's downloads.)
        5. the configured keep-out word recorded as enforced (decision 26), and a new pattern's
        pass (decision 27), under the phase `checking`, which SIGTERM stops between recordings.
        The pass is part of the start: a crash during it keeps the first start's time, as a crash
        before it does. The start is then done, phase `idle`, written to the heartbeat at once (a
        start that follows failed ones must not read STUCK until the next beat)."""
        self._start_heartbeat()
        self._requests_at_start()
        self.writer = self._open_writer()
        self.gate = self._open_gate()
        self._enforce()
        self.progress.bump(self.clock(), "idle")
        self._beat()

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

    def _patterns(self, now: datetime) -> PatternsPass | None:
        """Decision 27's pass, under the phase `checking`: each recording it checks bumps the
        progress and the heartbeat's `done` of `total`, so Status reads "(checking, 412 of 1,234)"
        (spec §15.2). SIGTERM stops it between recordings (§9.2); a pass stopped so isn't recorded
        as complete, and the next start runs it again (reconcile skips what it already judged)."""
        try:
            return patterns_pass(self.writer, self.db, now=now, progress=self._check_one)
        except _Stopped:
            log.info("the patterns pass stopped between recordings: the next start runs it again")
            return None

    def _check_one(self) -> None:
        """Called before each recording the pass checks: SIGTERM ends the pass here, between
        recordings."""
        if self.stopping.is_set():
            raise _Stopped
        if self._to_check is None:  # the pass has begun: count what it checks, once
            self._to_check = sum(
                any(s.kind == "plaud" for s in rec.sources)
                for rec in self.writer.archive.iter_recordings()
            )
        self._checked += 1
        self.progress.check(self.clock(), self._checked, self._to_check)

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
        self._step("late consent", self._late_consent)
        self._step("owed private", self._owed_private)
        self._step("request", self._serve_one)
        self._step("turn", self._turn_if_due)
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
        (`flags.still_owed`, against the list in force). The blocker is open while any flag is, or
        while the last pass failed as a whole (its detail names the error's class), and is written
        afresh at every tick."""
        if self.writer is None:
            return
        now = self.clock()
        unwritten = set(self._late.unwritten) if self._late is not None else set()
        causes = self._late.causes if self._late is not None else {}
        open_flags = {e.recording_id: e for e in self.db.open_flags((PRIVATE_UNWRITTEN,))}
        for rid in sorted(unwritten - open_flags.keys()):
            detail = {"owed": "consent", **({"cause": causes[rid]} if rid in causes else {})}
            with contextlib.suppress(sqlite3.IntegrityError):  # another writer just added it
                self.db.add_event(
                    PRIVATE_UNWRITTEN, now=now, recording_id=rid, detail=detail, open_flag=True
                )
        for rid, event in open_flags.items():
            if rid not in unwritten and not still_owed(self.writer.archive, event, self.consent):
                self.db.resolve(event.id, now=now)
        if self._late_failed is not None:
            open_blocker(self.db, PRIVATE_UNWRITTEN, now=now, detail={"error": self._late_failed})
        elif self.db.open_flags((PRIVATE_UNWRITTEN,)):
            open_blocker(self.db, PRIVATE_UNWRITTEN, now=now)
        else:
            clear_blocker(self.db, PRIVATE_UNWRITTEN)

    # ---- scheduled turns (spec §9.7) ----------------------------------------------------------
    def due(self) -> bool:
        """schedule_minutes after max(last_started, last_ended), which persist, so a worker that
        crashes and restarts doesn't start a sync at every start. A stamp in the future counts
        as long ago. A turn that didn't run (the gate, busy) waits one interval, in memory."""
        minutes = self.settings.schedule_minutes
        if minutes <= 0:
            return False
        now, interval = self.clock(), timedelta(minutes=minutes)
        if self._not_before is not None and now < self._not_before <= now + interval:
            return False
        stamps = (parse_stamp(self.db.get(K_LAST_STARTED)), parse_stamp(self.db.get(K_LAST_ENDED)))
        past = [t for t in stamps if t is not None and t <= now]
        return not past or now >= max(past) + interval

    def _turn_if_due(self) -> None:
        """The gate, read at every tick: while the last turn found it closed, the catch-up opening
        it ends the wait, so the first sync after the catch-up starts at the next tick, not an
        interval later. Then a due scheduled turn."""
        self.gate = gate_now(self.db)
        if self._held_by_gate and self.gate.open:
            self._not_before, self._held_by_gate = None, False
        if self.due():
            self.scheduled_turn()

    def scheduled_turn(self) -> None:
        """Spec §9.7: a bad keep_out_word, the gate closed or `plaud` held: each is recorded and
        pings nothing. Otherwise the turn runs under `plaud` (`plaud-sync`), and conclude_turn
        decides the ping. (Phase 2 adds the sign-in pause and the rate-limit wait here.)"""
        now = self.clock()
        outcome: TurnOutcome | None = None
        detail: Mapping[str, Any] | None = None
        self.gate = gate_now(self.db)
        if not self.settings.keep_out_word_ok:
            outcome = "idle-config"
        elif not self.gate.open:
            outcome, detail = "gate", {"reason": self.gate.reason}
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
        self._not_before = now + timedelta(minutes=self.settings.schedule_minutes)
        conclude_turn(self._ctx, None)  # no sync ran: no ping

    def _run_turn(self, now: datetime) -> RunOutcome | None:
        self.db.put(K_LAST_STARTED, now.isoformat(), now=now)
        self.progress.bump(now, "turn")
        ran: RunOutcome | None = None
        try:
            ran = self.turn(self, light=False)
        except Exception as exc:  # noqa: BLE001  a failed turn is recorded, never the loop's end
            record_turn(self.db, "error", now=self.clock(), detail={"error": type(exc).__name__})
            log.warning("the scheduled sync failed (%s)", type(exc).__name__)
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
        now = self.clock()
        requests.expire(self.state, now=now)
        claimed = requests.claim_next(self.state, now=now)
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
        except Exception as exc:  # noqa: BLE001  a failed request is answered, never the loop's end
            log.warning("a %s request failed (%s)", req.kind, type(exc).__name__)
            reply = requests.Reply(
                req.request_id, req.kind, "failed", f"error: `{type(exc).__name__}`"
            )
        if refused is not None:
            requests.refuse(self.state, req.request_id, message=refused, now=self.clock())
        else:
            requests.finish(
                self.state,
                req.request_id,
                ok=reply.state == "done",
                message=reply.message,
                result=reply.result,
                now=self.clock(),
            )
        self.progress.bump(self.clock(), "idle")


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


# ---- the worker's request handlers (Task 14): Phase 1 has Keep it here alone ---------------------
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


# What the worker serves. requests.HANDLED names the same kinds for web, which refuses the others
# before inserting; each later phase adds its handlers here and its kinds there.
HANDLERS: dict[str, Handler] = {"keep-here": _keep_here}
