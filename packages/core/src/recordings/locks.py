"""Locks for the archive's editable files (spec §6.4, §6.8): `flock` on files in `<state>/locks/`.

The kernel releases an flock when its process dies, so a crash never leaves a stale lock. The lock
files live in the state folder, never in the archive, so backups and the mirror never copy them.

Locks are taken in one fixed order, so two multi-file operations can never deadlock: the people
files, then tags.yaml, then `sha-<hash>` keys, then recordings by sorted ID, then anything else
(`index`, `docs`). Taking an earlier lock while holding a later one raises LockOrderError instead
of risking a deadlock. Within one thread a Locks object is re-entrant: holding a key again only
counts. Separate threads, and separate processes, exclude each other.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from recordings.ids import ID_RE

PEOPLE_FILES = ("people.yaml", "people.private.yaml")
OPS_LOCK = "ops"  # one bulk job at a time: the import (Task 9) and ops.run_job (Task 11)
_SAFE = re.compile(r"[^A-Za-z0-9._+-]")


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
        held = self._held()
        wanted = set(keys)
        new = sorted((k for k in wanted if k not in held), key=lock_order)
        if new and held and lock_order(new[0]) < max(lock_order(k) for k in held):
            raise LockOrderError(
                f"lock {new[0]!r} comes before one already held; take all of an operation's "
                "locks together, in one hold()")
        counted: list[str] = []
        try:
            for key in new:
                held[key] = [self._acquire(key), 0]
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

    def _acquire(self, key: str) -> int:
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / f"{_SAFE.sub('_', key)}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError as exc:
                # fcntl docs: LOCK_NB failing sets errno to EAGAIN or EACCES, by platform.
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        f"{key} is held by another writer; gave up after {self.timeout:g} s"
                        ) from None
                time.sleep(0.02)
