"""Operations (spec §14, §15.1). Each scheduled job runs through run_job, which records the run
in state.db, so Status can show it and the schedule knows what is due.

- **One job at a time.** run_job holds the ops lock, a flock in the state folder that the CLI,
  the backup service's loop and the import (Task 9) share. The CLI waits for it (CLI_WAIT, the
  default); the loop passes lock_timeout=0, and a busy turn is BUSY: not run, not recorded, not
  a failure. It is also what makes `restic unlock` safe (recordings.backup). A job runs while this
  process holds "ops", and Locks is re-entrant only within one instance, so no job may call the
  import: it takes "ops" through a Locks of its own, would wait out its 600 s on this process's
  hold, then raise LockTimeout, which is not in FAILURES. Imports run from the CLI alone; the
  backups around one are two CLI runs (`backup run --tag pre-import`, then `--tag post-import`).
- **A failure is a result, not a crash.** It is recorded as the error's type and exit code only
  (failure_detail). The message, which can name paths or carry a server's words, goes only to
  the debug log.
- **SIGTERM reaches the child.** `docker stop` sends it to this process. Inside
  sigterm_forwarded, it goes on to the running restic or rsync, which stops cleanly and removes
  its lock. Then the handler that was there before runs, if any; with none, this process stops
  once the child has. The loop (Task 13) runs inside sigterm_forwarded and sets no handler of
  its own.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from recordings import backup
from recordings.backup import BackupError, RestoreTestFailed
from recordings.config import Config, ConfigError
from recordings.disk import disk_thresholds
from recordings.locks import OPS_LOCK, Locks, LockTimeout
from recordings.sentinel import SentinelError
from recordings.state import State, StateError

log = logging.getLogger(__name__)
CLI_WAIT = 600.0  # seconds the CLI waits for a job of the loop's to finish
CLI_RETRY_LOCK = "5m"  # restic --retry-lock from the CLI: the NAS's retention task may hold it
BUSY = "skipped: busy"

_child: subprocess.Popen | None = None
_stopping = False
_previous: Callable[[int, object], object] | None = None  # the SIGTERM handler before ours


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_child(cmd: list[str], *, input: str | bytes | None = None, timeout: float | None = None,
              capture_output: bool = False, **popen_kwargs) -> subprocess.CompletedProcess:
    """subprocess.run, except that a SIGTERM to this process, inside sigterm_forwarded, goes on to
    the child, and this process stops only once the child has."""
    global _child
    if capture_output:
        popen_kwargs["stdout"] = popen_kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    with subprocess.Popen(cmd, **popen_kwargs) as proc:
        _child = proc
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        finally:
            _child = None
    if _stopping and _previous is None:  # no one else decides when to stop: stop now
        raise SystemExit(128 + signal.SIGTERM)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _on_sigterm(signum: int, frame) -> None:
    global _stopping
    _stopping = True
    if _child is not None:
        _child.send_signal(signal.SIGTERM)  # restic and rsync stop cleanly, dropping their locks
    if _previous is not None:
        _previous(signum, frame)  # chain to whatever Python handler was there before
    elif _child is None:
        raise SystemExit(128 + signum)


@contextmanager
def sigterm_forwarded() -> Iterator[None]:
    """While the CLI or the loop runs jobs, SIGTERM goes on to the running child (run_child), then
    to the handler that was there before, if it is a Python function. Without any handler, Python
    as a container's PID 1 ignores SIGTERM, and Docker kills both with SIGKILL after the grace
    period, leaving restic's lock behind. Only the main thread can set a handler, so elsewhere
    this does nothing."""
    global _stopping, _previous
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = signal.getsignal(signal.SIGTERM)
    _stopping, _previous = False, before if callable(before) else None
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, before)
        _stopping, _previous = False, None


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
    detail = (f"snapshot {result.snapshot_id[:12]}: {result.files} files, "
              f"{result.data_added} bytes added")
    if bc.nas_repo_path and bc.nas.ssh:  # §15.1: the NAS's free space, or "unknown"
        detail += ("; NAS free space unknown" if result.nas_free is None
                   else f"; {result.nas_free / 1024 ** 3:.0f} GB free on the NAS")
    return detail


def _prune(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup_config(ctx)
    if not bc.prune:
        return "prune is off: retention runs on the NAS"
    return backup.run_forget(bc, ctx.environ, runner=ctx.runner)


def _check(ctx: Context, tags: tuple[str, ...]) -> str:
    return backup.run_check(backup_config(ctx), ctx.environ, now=ctx.clock(), runner=ctx.runner)


def _restore_test(ctx: Context, tags: tuple[str, ...]) -> str:
    result = backup.run_restore_test(
        backup_config(ctx), ctx.environ, expected_uuid=ctx.state.meta("archive_uuid") or "",
        now=ctx.clock(), keep_free_percents=tuple(disk_thresholds(ctx.cfg).values()),
        runner=ctx.runner)
    if not result.ok:
        first = result.problems[0]
        raise RestoreTestFailed(f"restore test failed: {len(result.problems)} problems; first: "
                                f"{first['message']} ({first['path']})")
    return f"restored and validated {result.recordings} recordings"


JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]] = {
    "backup": _backup, "prune": _prune, "check": _check, "restore-test": _restore_test,
}
FAILURES: tuple[type[BaseException], ...] = (
    BackupError, ConfigError, SentinelError, StateError, OSError, subprocess.TimeoutExpired)


def run_job(ctx: Context, job: str, *, tags: tuple[str, ...] = (),
            lock_timeout: float = CLI_WAIT) -> JobResult:
    """Run one job under the ops lock, and record it. The CLI waits for the lock (the default);
    the loop passes 0. A lock still held means another run is busy: BUSY, not recorded."""
    with ExitStack() as stack:
        try:
            stack.enter_context(Locks(ctx.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK))
        except LockTimeout:
            log.info("%s: %s", job, BUSY)
            return JobResult(job, False, BUSY)
        started = ctx.clock()
        try:
            detail, ok = JOBS[job](ctx, tags), True
        except FAILURES as exc:
            log.debug("%s failed", job, exc_info=True)  # the full error, for --debug only
            detail, ok = failure_detail(exc), False
        ctx.state.record_run(job, started, ctx.clock(), ok, detail)
    return JobResult(job, ok, detail)
