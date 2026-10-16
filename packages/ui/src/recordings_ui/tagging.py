"""The tag service (tagging spec §5): one write path for tags, with two stores.

- `ArchiveStore`, for the real app and `make demo`, writes through the process's one `Writer`,
  owned by one thread (`recordings_ui.writing`). Its reads go straight to the archive, on the
  event loop: recording.json is replaced atomically, so a read needs no lock, and reads never
  queue behind writes.
- `MemoryStore`, for the Pages demo, keeps the demo's recordings in memory. It applies the same
  operations and validates their results as `mutate` does, but never serialises a model, never
  writes a file and starts no thread. `pages_store()` chooses it, and only under Pyodide.

This module must import on Pyodide (Python 3.12, no sqlite3, fcntl or threads): everything that
needs them is imported inside `ArchiveStore.__init__`, as `status.py` does.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypeVar

from recordings.archive import Archive
from recordings.models import Recording
from recordings.tagops import Recorded

from recordings_ui import views

if TYPE_CHECKING:
    from recordings.writer import Writer

T = TypeVar("T")

# Fixed phrases (spec §8): why one recording failed, and why a batch stopped. Never exception
# text, which can carry a title.
GONE = "the recording is gone"
STALE = "edited outside the app: run `recordings reindex`"
INVALID = "the file doesn't validate"
BUSY = "busy: another job holds this recording; try again"
NOT_CONFIRMED = "not confirmed: would become visible"
STOP_NOT_WRITER = "Stopped: this app can't write the archive now"
STOP_FULL = "Stopped: the disk is full"
STOP_READ_ONLY = "Stopped: the disk is read-only"
STOP_IO = "Stopped: a disk error"
STOP_STATE = "Stopped: state.db can't be used"
STOP_BUSY = "Stopped: busy: other jobs hold these recordings; try again"
STOP_SHUTDOWN = "Stopped: the app is shutting down"
STOP_THREAD = "Stopped: the writer thread stopped; restart the app"
STOP_UNEXPECTED = "Stopped: an unexpected error (the server log has its class)"

LOCK_WAIT = 2.0  # seconds: the service's locks, not the Writer's default 30 (spec §5)


class Classified(NamedTuple):
    reason: str  # a fixed phrase
    stops: bool  # an archive-level error: the batch stops (spec §5)


def under_pyodide() -> bool:
    return sys.platform == "emscripten"


class Store(Protocol):
    """Where tags are kept, as the tag service sees it."""

    saves: bool  # False only on Pages, where the UI says "Demo: changes aren't saved"
    archive: Archive  # read-only: the library view and snippets

    def library(self) -> dict: ...

    def read(self, recording_id: str) -> Recording: ...

    def apply(self, recording_id: str, op: Recorded) -> Recording: ...

    async def write(self, fn: Callable[[], T]) -> T: ...

    async def read_off_loop(self, fn: Callable[[], T]) -> T: ...

    async def ready(self) -> None: ...

    def status(self, *, retry: bool = False) -> tuple[bool, str | None]: ...

    async def open_for_write(self) -> tuple[bool, str | None]: ...

    def classify(self, exc: BaseException) -> Classified | None: ...

    def close(self) -> None: ...


class MemoryStore:
    """The Pages demo's recordings, with this page's changes kept in memory (spec §5). It takes
    no lock at all, and a reload brings the demo back unchanged."""

    saves = False

    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self._changed: dict[str, Recording] = {}

    def changed(self) -> dict[str, Recording]:
        return dict(self._changed)

    def library(self) -> dict:
        recordings = [self._changed.get(r.id, r) for r in self.archive.iter_recordings()]
        return views.library_view_from(recordings, self.archive.problems)

    def read(self, recording_id: str) -> Recording:
        if recording_id in self._changed:
            return self._changed[recording_id]
        return self.archive.load(recording_id)

    def apply(self, recording_id: str, op: Recorded) -> Recording:
        rec = self.read(recording_id)
        new = op.apply(rec)
        if new == rec:
            return rec
        # As mutate does (spec §6.4): rev goes up, and the result must validate. model_dump and
        # model_validate only: never dump_json, whose output Pyodide's pydantic 2.10 gets wrong
        # (stage-2a decision 7).
        new = Recording.model_validate({**new.model_dump(by_alias=True), "rev": (rec.rev or 0) + 1})
        self._changed[recording_id] = new
        return new

    async def write(self, fn: Callable[[], T]) -> T:
        return fn()  # inline: Pyodide has no threads

    async def read_off_loop(self, fn: Callable[[], T]) -> T:
        return fn()

    async def ready(self) -> None:
        return None  # nothing to open: the demo's recordings are in memory

    def status(self, *, retry: bool = False) -> tuple[bool, str | None]:
        return True, None

    async def open_for_write(self) -> tuple[bool, str | None]:
        return True, None

    def classify(self, exc: BaseException) -> Classified | None:
        return None  # nothing of its own: the service's common rules cover it

    def close(self) -> None:
        pass


def pages_store(archive: Archive) -> MemoryStore:
    """The Pages demo's store. Only under Pyodide: outside it the app never falls back to memory,
    so `make demo` and the real app can't silently stop saving (spec §5)."""
    if not under_pyodide():
        raise RuntimeError("the in-memory store is for the Pages demo, under Pyodide only")
    return MemoryStore(archive)


class ArchiveStore:
    """The real archive and `make demo`'s copy (spec §5)."""

    saves = True

    def __init__(self, archive: Archive, open_writer: Callable[[], Writer]) -> None:
        # Here, never at the top: these bring sqlite3, fcntl and threads, which the Pages demo's
        # Pyodide lacks (stage-2a decision 6; spec §5).
        import sqlite3

        from recordings.locks import LockTimeout
        from recordings.writer import InvalidFile, NotTheWriter, StaleFile

        from recordings_ui.writing import OneThread, ThreadStopped, WriterThread

        self.archive = archive
        self._sqlite_error = sqlite3.Error
        self._lock_timeout = LockTimeout
        self._invalid, self._not_the_writer, self._stale = InvalidFile, NotTheWriter, StaleFile
        self._thread_stopped = ThreadStopped
        self._thread = WriterThread(open_writer)
        self._reader = OneThread("recordings-reader")

    def library(self) -> dict:
        return views.library_view(self.archive)

    def read(self, recording_id: str) -> Recording:
        return self.archive.load(recording_id)

    def apply(self, recording_id: str, op: Recorded) -> Recording:
        """On the writer thread only: call it through `write`."""
        writer = self._thread.writer
        if writer is None:
            raise self._not_the_writer(self._thread.reason or "the archive isn't open")
        return writer.mutate(recording_id, op)

    async def write(self, fn: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._thread.executor, fn)

    async def read_off_loop(self, fn: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._reader, fn)

    async def ready(self) -> None:
        """Wait for the thread's first open. It is the first job the thread runs, so this never
        waits behind a write; once it has run, it returns at once."""
        with contextlib.suppress(self._thread_stopped):
            await asyncio.wrap_future(self._thread.opened)

    def status(self, *, retry: bool = False) -> tuple[bool, str | None]:
        """`can_tag` and, when it is false, why, as last found. It never waits, so reads never
        queue behind writes (spec §5). retry=True, on Refresh, also queues another open without
        waiting for it, whatever the failure was; a retry that works shows on the next Refresh."""
        if retry:
            self._thread.retry()
        return self._thread.status()

    async def open_for_write(self) -> tuple[bool, str | None]:
        """Before a request's writes. A failed open is tried again here only after a transient
        failure, such as a busy index (spec §5); any other stands until a Refresh. The retry runs
        on the writer thread, in the queue this request's writes join anyway."""
        await self.ready()
        if self._thread.writer is None and self._thread.transient:
            with contextlib.suppress(self._thread_stopped):
                await self.write(self._thread.ensure_open)
        return self._thread.status()

    def wait_open(self, timeout: float) -> tuple[bool, str | None]:
        """Wait for the latest open, first or retried, then report (tests, and the docs check)."""
        self._thread.opening.result(timeout)
        return self._thread.status()

    def classify(self, exc: BaseException) -> Classified | None:
        """The Writer's own refusals, as fixed phrases (spec §8)."""
        if isinstance(exc, self._not_the_writer):
            return Classified(STOP_NOT_WRITER, True)
        if isinstance(exc, self._thread_stopped):
            return Classified(STOP_THREAD, True)
        if isinstance(exc, self._stale):
            return Classified(STALE, False)
        if isinstance(exc, self._invalid):
            return Classified(INVALID, False)
        if isinstance(exc, self._lock_timeout):
            return Classified(BUSY, False)
        if isinstance(exc, self._sqlite_error):
            busy = getattr(exc, "sqlite_errorname", "") in ("SQLITE_BUSY", "SQLITE_LOCKED")
            return Classified(BUSY, False) if busy else Classified(STOP_STATE, True)
        return None

    def close(self) -> None:
        self._thread.close()
        self._reader.shutdown(wait=False)
