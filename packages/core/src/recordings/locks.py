"""Locks for the archive's editable files (spec §6.4, §6.8): `flock` on files in `<state>/locks/`.

The kernel releases an flock when its process dies, so a crash never leaves a stale lock. The lock
files live in the state folder, never in the archive, so backups and the mirror never copy them.

Locks are taken in one fixed order, so two multi-file operations can never deadlock: the people
files, then tags.yaml, then `sha-<hash>` keys, then recordings by sorted ID, then anything else
(`index`, `catalog`). Taking an earlier lock while holding a later one raises LockOrderError instead
of risking a deadlock. Within one thread a Locks object is re-entrant: holding a key again only
counts. Separate threads, and separate processes, exclude each other. A hold() is synchronous:
never `await` inside one, because re-entrancy is per thread and other tasks on it would share the
lock. Keys become file names, so a key outside [A-Za-z0-9._+-] raises ValueError.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

from recordings.ids import ID_RE

PEOPLE_FILES = ("people.yaml", "people.private.yaml")
OPS_LOCK = "ops"  # one bulk job at a time: the import (Task 9) and ops.run_job (Task 11)
_KEY = re.compile(r"[A-Za-z0-9._+-]+")


class LockTimeout(RuntimeError):
    pass


class LockOrderError(RuntimeError):
    pass


def lock_order(key: str) -> tuple[int, int, str]:
    if key in PEOPLE_FILES:
        return (0, PEOPLE_FILES.index(key), "")
    if key == "tags.yaml":
        return (1, 0, "")
    if key.startswith("sha-"):
        return (2, 0, key)
    if ID_RE.fullmatch(key):
        return (3, 0, key)
    return (4, 0, key)


class Locks:
    def __init__(self, folder: Path, *, timeout: float = 30.0) -> None:
        self.folder = Path(folder)
        self.timeout = timeout
        self._local = threading.local()

    def _held(self) -> dict[str, list[int]]:
        """This thread's held keys: key -> [file descriptor, depth]."""
        held = getattr(self._local, "held", None)
        if held is None:
            held = self._local.held = {}
        return held

    @contextmanager
    def hold(self, *keys: str) -> Iterator[None]:
        for key in keys:
            if not _KEY.fullmatch(key):
                raise ValueError(f"lock key {key!r} must match [A-Za-z0-9._+-]+")
        held = self._held()
        wanted = set(keys)
        new = sorted((k for k in wanted if k not in held), key=lock_order)
        if new and held and lock_order(new[0]) < max(lock_order(k) for k in held):
            raise LockOrderError(
                f"lock {new[0]!r} comes before one already held; take all of an operation's "
                "locks together, in one hold()"
            )
        counted: list[str] = []
        deadline = time.monotonic() + self.timeout  # one for the whole hold, not one per key
        try:
            for key in new:
                held[key] = [self._acquire(key, deadline), 0]
            for key in wanted:
                held[key][1] += 1
                counted.append(key)
            yield
        finally:
            for key in counted:
                held[key][1] -= 1
            for key in [k for k in wanted if k in held and held[k][1] <= 0]:
                fd = held.pop(key)[0]
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _acquire(self, key: str, deadline: float) -> int:
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / f"{key}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except OSError as exc:
                    # fcntl docs: LOCK_NB failing sets errno to EAGAIN or EACCES, by platform.
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"{key} is held by another writer; gave up after {self.timeout:g} s"
                        ) from None
                    time.sleep(0.02)
        except BaseException:
            os.close(fd)  # on any exception, even KeyboardInterrupt; closing drops a flock too
            raise


@contextmanager
def announce(folder: Path, job: str, since: datetime, key: str = OPS_LOCK) -> Iterator[None]:
    """Call while holding `key`: writes which job runs, and since when, into the lock file, for
    Status (spec §12.5), and empties it again. Best effort: a full disk must not stop the job."""
    path = Path(folder) / f"{key}.lock"
    with suppress(OSError):
        _write(path, json.dumps({"job": job, "since": since.isoformat()}))
    try:
        yield
    finally:
        with suppress(OSError):
            _write(path, "")


def _write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def running(folder: Path, key: str = OPS_LOCK) -> tuple[str, datetime] | None:
    """Which job holds `key` now and since when, or None. It trusts the text only while the lock
    is held (a probe with LOCK_SH | LOCK_NB, released at once), so a crashed holder's leftover
    text means nothing. A reader in another container sees the same: flock is per host."""
    try:
        fd = os.open(Path(folder) / f"{key}.lock", os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                return None
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None  # nobody holds it
        try:
            data = json.loads(os.pread(fd, 4096, 0).decode("utf-8"))
            return str(data["job"]), datetime.fromisoformat(data["since"])
        except (ValueError, KeyError, TypeError, OSError):
            return None  # held, but not announced
    finally:
        os.close(fd)
