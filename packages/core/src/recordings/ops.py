"""Operations (spec §14, §15.1). Each scheduled job runs through run_job, which records the run
in state.db, so Status can show it and the schedule knows what is due.

- **One job at a time.** run_job holds the ops lock, a flock in the state folder that the CLI,
  the backup service's loop and the import (Task 9) share. The CLI waits for it (CLI_WAIT, the
  default); the loop passes lock_timeout=0, and a busy turn is BUSY: not run, not recorded, not
  a failure. It is also what makes `restic unlock` safe (recordings.backup). A job runs while this
  process holds "ops", and Locks is re-entrant only within one instance, so no job may call the
  import: it takes "ops" through a Locks of its own, would wait out its 600 s on this process's
  hold, then raise LockTimeout, which run_job records and alerts as a failure. Imports run from
  the CLI alone; the backups around one are two CLI runs (`backup run --tag pre-import`, then
  `--tag post-import`).
- **Exit 11 is busy, not broken.** A restic exit 11 means another process holds the repository, as
  the NAS's weekly retention task does. run_job gives BUSY + " (repository locked)" for it: not
  recorded, not alerted, no /fail ping, and the CLI exits 75 like any busy turn. A backup that
  stays blocked leaves its /start ping unanswered, and the dead-man's switch alerts after its
  grace (spec §14). Only restic's exit 11 counts: rsync's 11 is a file I/O error and fails.
- **A failure is a result, not a crash.** It is recorded as the error's type and exit code only
  (failure_detail). The message, which can name paths or carry a server's words, goes only to
  the debug log.
- **SIGTERM reaches the child.** `docker stop` sends it to this process. Inside
  sigterm_forwarded, it goes on to the running restic or rsync, which stops cleanly and removes
  its lock. Then the handler that was there before runs, if any; with none, this process stops
  once the child has. The loop (Task 13) runs inside sigterm_forwarded and sets no handler of
  its own. Ctrl-C stops the child with SIGTERM and waits for it, within the caller's timeout, the
  same way, and a SIGTERM still pending when run_child ends or raises (Popen failing, a timeout)
  is honoured then.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from recordings import alerts, backup, mirror
from recordings.alerts import AlertConfig
from recordings.backup import BackupError, RestoreTestFailed
from recordings.config import Config, ConfigError
from recordings.disk import disk_status, disk_thresholds, disk_view
from recordings.locks import OPS_LOCK, Locks, LockTimeout, announce, running
from recordings.mirror import MirrorError
from recordings.sentinel import SentinelError
from recordings.state import State, StateError

log = logging.getLogger(__name__)
CLI_WAIT = 600.0  # seconds the CLI waits for a job of the loop's to finish
CLI_RETRY_LOCK = "5m"  # restic --retry-lock from the CLI: the NAS's retention task may hold it
BUSY = "skipped: busy"

_child: subprocess.Popen | None = None
_spawning = False  # between starting a child and holding it: run_child forwards a SIGTERM then
_stopping = False  # a SIGTERM arrived while a child ran: the earlier handler runs after it exits
_previous: Callable[[int, object], object] | None = None  # the SIGTERM handler before ours
_depth = 0  # how many sigterm_forwarded blocks are open: only the outermost sets the handler


def _now() -> datetime:
    return datetime.now(UTC)


INTERRUPT_GRACE = (
    120.0  # seconds a child gets to clean up after Ctrl-C when the caller set no timeout
)


def _stop_child(proc: subprocess.Popen, deadline: float | None) -> None:
    """After Ctrl-C: SIGTERM the child (restic and rsync stop cleanly on it, whatever signal the
    terminal sent them), then wait for it within what is left of the caller's timeout, or
    INTERRUPT_GRACE with none, and kill it when that runs out. A further Ctrl-C while it cleans up
    is ignored: abandoning it there is what leaves restic's repository lock behind."""
    proc.send_signal(signal.SIGTERM)
    wait = INTERRUPT_GRACE if deadline is None else max(deadline - time.monotonic(), 0.0)
    end = time.monotonic() + wait
    while True:
        try:
            proc.communicate(timeout=max(end - time.monotonic(), 0.0))
            return
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return
        except KeyboardInterrupt:
            continue  # the child is already stopping: let it finish


def run_child(
    cmd: list[str],
    *,
    input: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
    **popen_kwargs,
) -> subprocess.CompletedProcess:
    """subprocess.run, except for SIGTERM inside sigterm_forwarded: it goes on to the child, and
    this process stops only once the child has exited and its pipes are drained. Then the handler
    that was there before runs (it may raise), or, with none, this process exits. Calling that
    handler while the child still ran would close the pipes under it: restic would die of SIGPIPE
    in the middle of its cleanup and leave its repository lock behind. Ctrl-C (KeyboardInterrupt)
    sends the child SIGTERM and waits for it (within the timeout; _stop_child), and a SIGTERM
    still pending when this returns or raises, for whatever reason, is honoured then."""
    global _child, _spawning, _stopping
    if capture_output:
        popen_kwargs["stdout"] = popen_kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    # A SIGTERM handler runs between two bytecodes of this thread, so while _spawning is set it
    # only records the signal; it is sent below, once the child is held. (Blocking SIGTERM with
    # pthread_sigmask instead would hand the child a blocked signal across exec.)
    _spawning = True
    try:
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
            _child = proc
        finally:
            _spawning = False
        deadline = None if timeout is None else time.monotonic() + timeout
        with proc:
            try:
                if _stopping:
                    proc.send_signal(signal.SIGTERM)
                stdout, stderr = proc.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            except KeyboardInterrupt:
                # At a terminal, Ctrl-C reaches the child too. Leaving the block now would close
                # its pipes under it, as an early SIGTERM handler would, so stop it and wait.
                _stop_child(proc, deadline)
                raise
            finally:
                _child = None
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        # On every way out, including Popen raising and a timeout: a SIGTERM that arrived while a
        # child ran or was starting is honoured now, once the child is gone. If the earlier
        # handler returns, whatever was in flight (a result or an exception) carries on.
        if _stopping:
            _stopping = False
            if _previous is not None:
                _previous(
                    signal.SIGTERM, None
                )  # the child is done: now the caller's handler decides
            else:
                raise SystemExit(128 + signal.SIGTERM)  # no one else decides when to stop: stop now


def _on_sigterm(signum: int, frame) -> None:
    global _stopping
    if _child is not None or _spawning:
        _stopping = True  # restic and rsync stop cleanly on SIGTERM, dropping their locks
        if _child is not None:
            _child.send_signal(signal.SIGTERM)
        return  # run_child ends the job once the child has exited
    if _previous is not None:
        _previous(signum, frame)  # no child running: whatever Python handler was there decides
    else:
        raise SystemExit(128 + signum)


@contextmanager
def sigterm_forwarded() -> Iterator[None]:
    """While the CLI or the loop runs jobs, SIGTERM goes on to the running child (run_child), then
    to the handler that was there before, if it is a Python function. Without any handler, Python
    as a container's PID 1 ignores SIGTERM, and Docker kills both with SIGKILL after the grace
    period, leaving restic's lock behind. Only the main thread can set a handler, so elsewhere
    this does nothing; and it nests: only the outermost block sets and puts back the handler."""
    global _stopping, _previous, _depth
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    if _depth:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
        return
    before = signal.getsignal(signal.SIGTERM)  # None: a handler Python can't see or restore
    _stopping, _previous, _depth = False, before if callable(before) else None, 1
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, before if before is not None else signal.SIG_DFL)
        _stopping, _previous, _depth = False, None, 0


@dataclass
class Context:
    cfg: Config
    environ: Mapping[str, str]
    state: State
    clock: Callable[[], datetime] = field(default=_now)
    runner: Callable[..., subprocess.CompletedProcess] = field(default=run_child)
    # restic's --retry-lock: none by default, so restic fails at once on a held lock. The CLI's
    # Context (cli._ops_context) sets CLI_RETRY_LOCK.
    retry_lock: str | None = None
    # The mirror: the loop caps each run at mirror.TIMEOUT; `recordings mirror` sets None, and
    # may allow more deletions for its one run (--max-delete) than [mirror] max_delete.
    mirror_timeout: float | None = mirror.TIMEOUT
    mirror_max_delete: int | None = None
    # What this process remembers when state.db can't be written or read (a full disk), so a failing
    # job isn't retried, and an alert isn't sent again, at every tick: each job's last attempt
    # (finish time, ok), each alert key's (failing, last_sent), and when a job met a locked
    # repository.
    attempts: dict[str, tuple[datetime, bool]] = field(default_factory=dict)
    alerted: dict[str, tuple[bool, datetime | None]] = field(default_factory=dict)
    locked: dict[str, datetime] = field(default_factory=dict)
    warned: dict[str, datetime] = field(default_factory=dict)  # when _warn_hourly last spoke


@dataclass(frozen=True)
class JobResult:
    job: str
    ok: bool
    detail: str  # BUSY when the ops lock was held: not run, not recorded, not a failure


def failure_detail(exc: BaseException) -> str:
    """The fixed text a failed job is recorded and alerted with: the error's type, and its exit
    code when it carries one (`returncode`). Never str(exc), which can carry paths, titles or a
    server's words."""
    code = getattr(exc, "returncode", None)
    return f"{type(exc).__name__}; exit {code}" if isinstance(code, int) else type(exc).__name__


def backup_config(ctx: Context) -> backup.BackupConfig:
    return replace(backup.from_config(ctx.cfg, ctx.environ), retry_lock=ctx.retry_lock)


def _backup(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup_config(ctx)
    result = backup.run_backup(bc, ctx.state, ctx.environ, tags=tags, runner=ctx.runner)
    detail = (
        f"snapshot {result.snapshot_id[:12]}: {result.files} files, {result.data_added} bytes added"
    )
    if bc.nas_repo_path and bc.nas.ssh:  # §15.1: the NAS's free space, or "unknown"
        detail += (
            "; NAS free space unknown"
            if result.nas_free is None
            else f"; {result.nas_free / 1024**3:.0f} GB free on the NAS"
        )
    return detail


def _prune(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup_config(ctx)
    if not bc.prune:
        return "prune is off: retention runs on the NAS"
    return backup.run_forget(bc, ctx.environ, runner=ctx.runner)


CHECK_NEXT = "check_next_quarter"  # state.db meta: the quarter of the data the next check reads
QUARTERS = ("1/4", "2/4", "3/4", "4/4")


def _check(ctx: Context, tags: tuple[str, ...]) -> str:
    """Each check reads one quarter of the data, and only a check that passes moves on to the next.
    A failed check's retries, the loop's or the CLI's, read the same quarter again, so another
    quarter can't pass in its place and look like a recovery while the damage waits weeks to be
    read. Skipped weeks skip no quarter. The first check starts from the calendar week's."""
    saved = ctx.state.meta(CHECK_NEXT)
    subset = saved if saved in QUARTERS else backup.check_subset(ctx.clock())
    ctx.state.set_meta(CHECK_NEXT, subset)  # a failure below leaves it here for the retry
    detail = backup.run_check(backup_config(ctx), ctx.environ, subset=subset, runner=ctx.runner)
    ctx.state.set_meta(CHECK_NEXT, QUARTERS[(QUARTERS.index(subset) + 1) % len(QUARTERS)])
    return detail


def _restore_test(ctx: Context, tags: tuple[str, ...]) -> str:
    result = backup.run_restore_test(
        backup_config(ctx),
        ctx.environ,
        expected_uuid=ctx.state.meta("archive_uuid") or "",
        now=ctx.clock(),
        keep_free_percents=tuple(disk_thresholds(ctx.cfg).values()),
        runner=ctx.runner,
    )
    if not result.ok:
        first = result.problems[0]
        raise RestoreTestFailed(
            f"restore test failed: {len(result.problems)} problems; first: "
            f"{first['message']} ({first['path']})"
        )
    return f"restored and validated {result.recordings} recordings"


def _mirror(ctx: Context, tags: tuple[str, ...]) -> str:
    mc = mirror.from_config(ctx.cfg, ctx.environ)
    if ctx.mirror_max_delete is not None:
        mc = replace(mc, max_delete=ctx.mirror_max_delete)
    return mirror.run_mirror(
        mc,
        expected_uuid=ctx.state.meta("archive_uuid") or "",
        runner=ctx.runner,
        timeout=ctx.mirror_timeout,
    )


class DiskWarning(RuntimeError):
    """The archive's disk is below a threshold."""


class ArchiveMarkerError(DiskWarning):
    """The archive's marker is missing or changed: an unmounted or wrong disk, not a full one."""


class RestoreCopyLeft(DiskWarning):
    """A restore test's copy, the whole archive unencrypted, is in [backup] restore_scratch while
    no restore test runs: a SIGKILL, the OOM killer or a power cut left it behind (§15.1). The
    next restore test removes it, up to 30 days later; `recordings doctor` names it."""


def _restore_copy_left(ctx: Context) -> bool:
    """A restore-* folder in [backup] restore_scratch that no running restore test owns. The disk
    job runs holding "ops" (run_job), so no restore test can be running while it looks; the
    check is doctor's all the same, so a running test's own copy is never called left behind."""
    scratch = ctx.cfg.data.get("backup", {}).get("restore_scratch")
    if not isinstance(scratch, str) or not scratch.strip():
        return False
    held = running(ctx.state.locks_dir)
    if held is not None and held[0] == "restore-test":
        return False
    try:
        return any(Path(scratch).glob("restore-*"))
    except OSError:
        return False


def _disk(ctx: Context, tags: tuple[str, ...]) -> str:
    if ctx.cfg.archive_path is None:
        raise ConfigError("set [archive] path")
    status = disk_status(
        ctx.cfg.archive_path,
        expected_uuid=ctx.state.meta("archive_uuid"),
        **disk_thresholds(ctx.cfg),
    )
    if not status.marker:
        raise ArchiveMarkerError(status.message)
    if _restore_copy_left(ctx):  # before low space: Status shows that, and nothing shows this
        raise RestoreCopyLeft("a restore test's copy is still in [backup] restore_scratch")
    if status.level != "ok":
        raise DiskWarning(status.message)
    return status.message


JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]] = {
    "disk": _disk,
    "backup": _backup,
    "prune": _prune,
    "check": _check,
    "restore-test": _restore_test,
    "mirror": _mirror,
}
# The errors a job is expected to raise, and the ones a broken state.db is guarded against;
# run_job also contains any other Exception.
FAILURES: tuple[type[BaseException], ...] = (
    BackupError,
    MirrorError,
    DiskWarning,
    ConfigError,
    SentinelError,
    StateError,
    OSError,
    sqlite3.Error,
    subprocess.TimeoutExpired,
)


INTERVALS = {
    "disk": timedelta(minutes=10),
    "backup": timedelta(hours=1),
    "mirror": timedelta(hours=1),
    "prune": timedelta(days=1),
    "check": timedelta(days=7),
    "restore-test": timedelta(days=30),
}
ORDER = ("disk", "backup", "mirror", "prune", "check", "restore-test")
RETRY_AFTER = timedelta(minutes=15)
# A failed check reads a quarter of the repository again, and a failed restore test all of it,
# for hours in which the loop does nothing else: once a day is enough, and the alert has said
# it failed. (Task 11's ops council.) A failed mirror walks both trees twice to fail again, and
# one short of space first reads and rewrites all it staged: its own hour is soon enough.
# (Task 12's ops council)
RETRY_HEAVY_AFTER = {
    "check": timedelta(hours=24),
    "restore-test": timedelta(hours=24),
    "mirror": timedelta(hours=1),
}
REMIND_EVERY = timedelta(hours=24)
KEEP_RUNS = timedelta(days=90)  # ops_runs gets about 70,000 rows a year; older ones go, daily
PRUNE_RUNS_EVERY = timedelta(days=1)
REPO_LOCKED = (
    11  # restic's exit status for a repository another process holds (the NAS's retention)
)
LOCKED = f"{BUSY} (repository locked)"
TITLES = {
    "disk": "disk space low",
    "backup": "backup failed",
    "mirror": "mirror failed",
    "prune": "backup retention failed",
    "check": "backup check failed",
    "restore-test": "restore test failed",
}
# Failures of their own kind: each has its own title and its own alert state.
DETAIL_TITLES = {
    "ArchiveMarkerError": "archive disk missing or changed",
    "RestoreCopyLeft": "restore copy left behind",
}


def enabled_jobs(cfg: Config) -> tuple[str, ...]:
    jobs = {"disk"}
    if cfg.data.get("backup", {}).get("repository"):
        jobs |= {"backup", "prune", "check", "restore-test"}
    if cfg.data.get("mirror", {}).get("target"):
        jobs.add("mirror")
    return tuple(j for j in ORDER if j in jobs)


def due(
    state: State,
    now: datetime,
    jobs: tuple[str, ...],
    attempts: Mapping[str, tuple[datetime, bool]] | None = None,
) -> list[str]:
    """A run stamped in the future (a clock that ran ahead and was put right) counts as long
    ago: otherwise every job, backups included, would wait for the clock to catch up. `attempts`
    are the runs this process could not record (a full state disk): one newer than state.db's
    counts as the job's latest run, so a failing job waits its retry rather than running at
    every tick."""
    out = []
    for job in jobs:
        last, good = state.last_run(job), state.last_run(job, ok=True)
        last_at, last_ok = (last.finished_at, last.ok) if last is not None else (None, True)
        good_at = good.finished_at if good is not None else None
        tried = (attempts or {}).get(job)
        if tried is not None and (last_at is None or tried[0] > last_at or last_at > now):
            last_at, last_ok = tried
            if last_ok:
                good_at = last_at
        wait = RETRY_HEAVY_AFTER.get(job, RETRY_AFTER)
        if last_at is not None and not last_ok and timedelta(0) <= now - last_at < wait:
            continue
        if good_at is None or not timedelta(0) <= now - good_at < INTERVALS[job]:
            out.append(job)
    return out


def _alert_config(ctx: Context) -> AlertConfig | None:
    try:
        return alerts.from_config(ctx.cfg, ctx.environ)
    except ConfigError as exc:  # its text names a setting, never a secret's value
        log.warning("alerts are not configured: %s", exc)
        return None


def _alert(ctx: Context, ac: AlertConfig, job: str, ok: bool, detail: str) -> None:
    """One push when a job starts failing, a reminder every day while it fails, and one when it
    works again. The text is a fixed template: the job, the failure's fixed detail and "See
    Status", never a path, a title or a tool's own output (Review Focus 4). A state.db that
    can't be read or written never stops this, nor makes it say the same thing at every tick:
    the process keeps what it sent (ctx.alerted) for as long as it is newer than state.db's.
    A failure with a title of its own (DETAIL_TITLES) has an alert state of its own, so a flip to
    it is said at once, and one recovery clears them all."""
    now = ctx.clock()
    if ok:
        keys = [f"job:{job}", *(f"job:{job}:{kind}" for kind in DETAIL_TITLES)]
        failing = [key for key in keys if _known(ctx, key)[0]]
        if failing:
            alerts.send(
                ac,
                title=f"recordings: {job} recovered",
                message=f"{job} works again. See Status.",
                priority=3,
                tags=("white_check_mark",),
            )
        for key in failing:
            _remember(ctx, key, failing=False, last_sent_at=now)
        return
    key = f"job:{job}:{detail}" if detail in DETAIL_TITLES else f"job:{job}"
    failing, last_sent = _known(ctx, key)
    if not failing or last_sent is None or not timedelta(0) <= now - last_sent < REMIND_EVERY:
        delivered = alerts.send(
            ac,
            title=f"recordings: {DETAIL_TITLES.get(detail, TITLES[job])}",
            message=f"{job} failed ({detail}). See Status.",
        )
        last_sent = now if delivered else last_sent
    _remember(ctx, key, failing=True, last_sent_at=last_sent)


def _known(ctx: Context, key: str) -> tuple[bool, datetime | None]:
    """What is known of one alert: state.db's row, or this process's own memory of it when that is
    newer, or when state.db can't be read."""
    mine = ctx.alerted.get(key)
    try:
        saved = ctx.state.alert_state(key)
    except FAILURES:  # state.db unreadable: the process's memory, or nothing sent so far
        log.warning("%s: could not read the alert state", key)
        saved = None
    if saved is None:
        return mine or (False, None)
    if mine is None:
        return saved
    newer = (
        saved[1] is not None and saved[1] <= ctx.clock() and (mine[1] is None or saved[1] > mine[1])
    )
    return saved if newer else mine


def _remember(ctx: Context, key: str, *, failing: bool, last_sent_at: datetime | None) -> None:
    ctx.alerted[key] = (failing, last_sent_at)
    try:
        ctx.state.set_alert_state(key, failing=failing, last_sent_at=last_sent_at)
    except FAILURES:  # nothing to lose but the next reminder's timing
        log.warning("%s: could not save the alert state", key)


def _conclude(
    ctx: Context, ac: AlertConfig | None, job: str, started: datetime, ok: bool, detail: str
) -> JobResult:
    """Record a run, alert, and for a backup ping the dead-man's switch: the URL itself or /fail.
    A state.db that can't record it leaves the attempt in ctx.attempts, for due()."""
    try:
        ctx.state.record_run(job, started, ctx.clock(), ok, detail)
        ctx.warned.pop(f"record:{job}", None)
    except FAILURES:  # a broken state disk must not hide the alert below (which guards its own)
        _warn_hourly(ctx, f"record:{job}", f"{job}: could not record the run ({detail})")
        ctx.attempts[job] = (ctx.clock(), ok)
    if ac is not None:
        _alert(ctx, ac, job, ok, detail)
        if job == "backup":
            alerts.ping_deadman(
                ac,
                "success" if ok else "fail",
                message="" if ok else f"{job} failed ({detail}). See Status.",
            )
    return JobResult(job, ok, detail)


def _run_held(ctx: Context, job: str, tags: tuple[str, ...]) -> JobResult:
    ac = _alert_config(ctx)
    if ac is not None and job == "backup":
        alerts.ping_deadman(ac, "start")
    started = ctx.clock()
    try:
        detail, ok = JOBS[job](ctx, tags), True
    except Exception as exc:  # any error is the job's failure: it must not end the loop
        if isinstance(exc, BackupError) and exc.returncode == REPO_LOCKED:
            # The NAS's weekly forget --prune holds the repository for a while. Not a failure:
            # not recorded, not alerted, no /fail ping. A backup that stays blocked leaves its
            # /start unanswered, and the dead-man's switch alerts after its grace. The loop
            # leaves the job alone for RETRY_AFTER (run_due).
            log.info("%s: %s", job, LOCKED)
            ctx.locked[job] = ctx.clock()
            return JobResult(job, False, LOCKED)
        log.debug("%s failed", job, exc_info=True)  # the full error, for --debug only
        detail, ok = failure_detail(exc), False
    return _conclude(ctx, ac, job, started, ok, detail)


def run_job(
    ctx: Context, job: str, *, tags: tuple[str, ...] = (), lock_timeout: float = CLI_WAIT
) -> JobResult:
    """Run one job under the ops lock, record it, alert, and for a backup ping the dead-man's
    switch: /start first, then the URL itself or /fail. The CLI waits for the lock (the default);
    the loop passes 0. A lock still held means another run is busy: BUSY. A restic exit 11 (the
    repository is locked, as while the NAS's retention task runs) is busy too: LOCKED, which
    starts with BUSY. Neither is recorded or alerted, and neither sends /fail (a backup has
    sent /start before an exit 11, and its dead-man's switch decides)."""
    with ExitStack() as stack:
        try:
            stack.enter_context(Locks(ctx.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK))
            stack.enter_context(announce(ctx.state.locks_dir, job, ctx.clock()))
        except LockTimeout:
            log.info("%s: %s", job, BUSY)
            return JobResult(job, False, BUSY)
        except FAILURES as exc:  # the lock file can't be opened: a failure, said as any is
            log.debug("%s failed", job, exc_info=True)
            return _conclude(ctx, _alert_config(ctx), job, ctx.clock(), False, failure_detail(exc))
        return _run_held(ctx, job, tags)


WARN_EVERY = timedelta(hours=1)


def _warn_hourly(ctx: Context, key: str, message: str) -> None:
    """A full state disk fails the heartbeat and the trim at every tick: say so once an hour (the
    rest at debug), not 1,440 times a day. The alert says it too, once (Task 13)."""
    now = ctx.clock()
    last = ctx.warned.get(key)
    if last is None or not timedelta(0) <= now - last < WARN_EVERY:
        ctx.warned[key] = now
        log.warning(message)
    else:
        log.debug(message)


def _trim_runs(ctx: Context) -> None:
    """Once a day: drop runs older than KEEP_RUNS, but never a job's newest run or its newest good
    one, so Status never reads "never succeeded" after a long failing streak. A failure here is
    only logged."""
    now = ctx.clock()
    try:
        last = ctx.state.meta("ops_runs_trimmed")
        if last and timedelta(0) <= now - datetime.fromisoformat(last) < PRUNE_RUNS_EVERY:
            return
        gone = ctx.state.prune_runs(now - KEEP_RUNS)
        ctx.state.set_meta("ops_runs_trimmed", now.isoformat())
        ctx.warned.pop("trim", None)  # recovered: the next full disk warns at once
    except (*FAILURES, ValueError):
        _warn_hourly(ctx, "trim", "could not trim the old runs")
        return
    if gone:
        log.info("trimmed %d old runs", gone)


def run_due(ctx: Context) -> list[JobResult]:
    """One pass of the loop: each job that is due, in ORDER, never waiting for the ops lock. A
    busy job is BUSY, and the next pass tries it again."""
    try:
        ctx.state.set_meta("ops_heartbeat", ctx.clock().isoformat())
        ctx.warned.pop("heartbeat", None)  # recovered: the next full disk warns at once
    except FAILURES:  # a broken state disk must not stop the pass: the jobs still run
        _warn_hourly(ctx, "heartbeat", "could not write the heartbeat")
    _trim_runs(ctx)
    now = ctx.clock()
    return [
        run_job(ctx, job, lock_timeout=0)
        for job in due(ctx.state, now, enabled_jobs(ctx.cfg), ctx.attempts)
        if not (job in ctx.locked and timedelta(0) <= now - ctx.locked[job] < RETRY_AFTER)
    ]


def loop(ctx: Context, *, tick: float = 60.0, sleep=time.sleep, stop=lambda: False) -> None:
    """The backup service's main loop: what is due, then a minute's sleep, forever.
    `recordings ops --loop` runs it inside sigterm_forwarded: `docker stop` passes SIGTERM on to a
    running restic or rsync, and once that has stopped, the loop stops too."""
    while not stop():
        try:
            for result in run_due(ctx):
                log.info("%s: %s (%s)", result.job, "ok" if result.ok else "not ok", result.detail)
        except Exception as exc:  # due() reading a broken state.db, or a bug: wait a tick, retry
            log.warning("a pass of the loop failed (%s)", failure_detail(exc))
            log.debug("a pass of the loop failed", exc_info=True)  # the traceback, for --debug only
        sleep(tick)


SHOWN = (
    ("backup", "Backup", timedelta(hours=2)),
    ("restore-test", "Restore test", timedelta(days=31)),
    ("check", "Repository check", timedelta(days=8)),
    ("mirror", "Mirror for the Mac", timedelta(hours=2)),
    ("prune", "Retention", timedelta(days=2)),
)  # its alert says "See Status" too (§14)
RUNNING_LABELS = {
    **{job: label for job, label, _ in SHOWN},
    "disk": "Disk check",
    "import": "Import",
}
# The loop writes its heartbeat at each pass, about once a minute (Task 13). Between passes only a
# job holding "ops" keeps it quiet, and that job's run reports when it ends.
QUIET_AFTER = timedelta(minutes=15)
# A job holding "ops" excuses a late backup for this long at most. Past it, a hung restic or rsync,
# or a CLI job left running, needs attention: Status must not stay green for days while the
# dead-man's switch has alerted after its grace (§14).
HOLD_LIMIT = timedelta(hours=12)


def _stamp(text: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return t if t is not None and t.tzinfo is not None else None


def when_label(t: datetime, now: datetime, timezone_name: str) -> str:
    """`2026-10-08 14:05 (35 min ago)`: ISO 8601 dates and 24-hour times (spec §12)."""
    minutes = int((now - t).total_seconds() // 60)
    if t > now:  # a clock that ran ahead and was put right (§15.1): never "just now"
        ago = "clock ahead"
    elif minutes < 1:
        ago = "just now"
    elif minutes < 60:
        ago = f"{minutes} min ago"
    elif minutes < 48 * 60:
        ago = f"{minutes // 60} h ago"
    else:
        ago = f"{minutes // 1440} days ago"
    return f"{t.astimezone(ZoneInfo(timezone_name)):%Y-%m-%d %H:%M} ({ago})"


def status_summary(
    state: State,
    *,
    archive_root: Path,
    now: datetime,
    timezone_name: str,
    warn: float,
    stop: float,
    jobs: tuple[str, ...],
) -> dict:
    """Status's backup and disk panels (spec §12.5). The backup needs attention after 2 hours."""
    disk = disk_view(
        disk_status(archive_root, expected_uuid=state.meta("archive_uuid"), warn=warn, stop=stop)
    )
    flags = len(state.flags())
    held = running(state.locks_dir)  # (job, since) while a job holds the ops lock
    # A hold can excuse a late job for HOLD_LIMIT at most. A start in the future (a clock put
    # right) counts as long ago, as in due().
    hold_ok = held is not None and timedelta(0) <= now - held[1] <= HOLD_LIMIT
    out: dict = {
        "configured": True,
        "jobs": [],
        "attention": [],
        "flags": flags,
        "disk": disk,
        "running": None
        if held is None
        else {
            "job": held[0],
            "label": RUNNING_LABELS.get(held[0], held[0]),
            "since": when_label(held[1], now, timezone_name),
        },
    }
    reports = [_stamp(state.meta("ops_heartbeat"))]  # the loop's heartbeat, and each job's last run
    for job, label, stale_after in SHOWN:
        if job not in jobs:
            continue
        good, last = state.last_run(job, ok=True), state.last_run(job)
        reports.append(last.finished_at if last else None)
        failing = last is not None and not last.ok
        # A run stamped in the future counts as long ago, as in due().
        ago = None if good is None or good.finished_at > now else now - good.finished_at
        late = ago is None or ago > stale_after
        # §12.5: while a job holds the ops lock the backup's and the mirror's turns are skipped,
        # so one that is merely late is waiting, not an outage, for HOLD_LIMIT at most, and only
        # if the hold began before the job turned late: a hold that began after explains nothing.
        waiting = (
            late
            and job in ("backup", "mirror")
            and ago is not None
            and hold_ok
            and held[1] <= good.finished_at + stale_after
        )
        stale = late and not waiting
        last_ok = when_label(good.finished_at, now, timezone_name) if good else None
        out["jobs"].append(
            {
                "job": job,
                "label": label,
                "last_ok": last_ok,
                "last_ok_detail": good.detail
                if good
                else None,  # the NAS's free space, for a backup
                "failing": failing,
                "detail": last.detail if failing else None,
                "attention": failing or stale,
                "waiting": waiting,
            }
        )
        if job == "backup" and stale:
            out["attention"].append(
                "The last backup is more than 2 hours old."
                if good
                else "No backup has finished yet."
            )
        elif stale and not failing:  # a failing job has its line below
            out["attention"].append(
                f"{label} is overdue: it last worked {last_ok}."
                if good
                else f"{label} has not worked yet."
            )
        if failing:
            out["attention"].append(f"{label} failed: {last.detail}")
    if held is not None and not hold_ok:
        out["attention"].append(
            f"{out['running']['label']} has been running since {out['running']['since']}, and no "
            "backup runs until it ends: check that it isn't stuck."
        )
    # Silence with nothing holding "ops" means the backup service has stopped, whatever the
    # backup's own age says, and also where no backup is set up. A future stamp proves nothing.
    seen = max((t for t in reports if t is not None and t <= now), default=None)
    out["reported"] = when_label(seen, now, timezone_name) if seen else None
    if held is None and (seen is None or now - seen > QUIET_AFTER):
        out["attention"].append(
            f"The backup service last reported {out['reported']}: check that it is running."
            if seen
            else "The backup service has not reported yet: check that it is running."
        )
    if disk["level"] != "ok":
        out["attention"].append(disk["message"])
    if flags:
        out["attention"].append(
            f"{flags} file needs attention: run recordings reindex."
            if flags == 1
            else f"{flags} files need attention: run recordings reindex."
        )
    return out
