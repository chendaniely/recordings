"""The process's one writer thread (tagging spec §5).

Why one thread owns the process's one Writer:
- Lock re-entrancy is per `Locks` instance *and* per thread, so two Writer instances nested on
  one thread deadlock until the timeout.
- A lock must never be held across an `await`.
- The event loop must never block on a file write.
- Every window's writes are serialised.

So every tag write runs here, one recording at a time, in order, and the event loop awaits it.
A second one-thread executor, the reader, serves the Tags page's snippets, so a snippet never
waits behind a write.

Both threads are daemons: the interpreter doesn't wait for them at exit, and a batch stops
between recordings (TagService.stop). Writes are atomic, so stopping mid-batch is safe.

Imported only by `ArchiveStore.__init__`: the Pages demo's Pyodide has no threads, sqlite3 or
fcntl (stage-2a decisions 6 and 7).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future
from typing import Any

from recordings.init import InitError
from recordings.locks import LockTimeout
from recordings.state import StateError
from recordings.writer import NotTheWriter, Writer

log = logging.getLogger(__name__)

OPENING = "the archive is being opened"
STOPPED = "the writer thread stopped; restart the app"  # tagging.STOP_THREAD is "Stopped: " + it
TRANSIENT = "the archive's index or state.db was busy ({}): press Refresh to try again"


class ThreadStopped(RuntimeError):
    """The thread has exited, so nothing more runs on it."""


class OneThread(Executor):
    """An executor with one daemon thread. Jobs run in the order they were submitted. A job's
    exception goes to its future, and the thread carries on. When it stops, anything still queued
    fails with ThreadStopped, and so does any later submit."""

    def __init__(self, name: str) -> None:
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._cancel = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        with self._lock:
            if self._closed or not self._thread.is_alive():
                raise ThreadStopped(STOPPED)
            future: Future = Future()
            self._jobs.put((future, fn, args, kwargs))
            return future

    def alive(self) -> bool:
        return self._thread.is_alive() and not self._closed

    def require_thread(self, what: str) -> None:
        """Fail loudly when `what` runs anywhere but this executor's own thread."""
        if threading.current_thread() is not self._thread:
            raise RuntimeError(f"{what} runs on the {self._thread.name} thread only")

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._cancel = self._cancel or cancel_futures
            if not self._closed:
                self._closed = True
                self._jobs.put(None)  # after every job already queued
        if wait:
            self._thread.join()

    def _run(self) -> None:
        try:
            while (job := self._jobs.get()) is not None:
                future, fn, args, kwargs = job
                if not future.set_running_or_notify_cancel():
                    continue
                if self._cancel:  # shutdown(cancel_futures=True): the running job finished, no more
                    future.set_exception(ThreadStopped(STOPPED))
                    continue
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:  # noqa: BLE001  the job's failure goes to its caller
                    future.set_exception(exc)
        finally:
            with self._lock:
                self._closed = True
            self._fail_queued()

    def _fail_queued(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job is not None and job[0].set_running_or_notify_cancel():
                job[0].set_exception(ThreadStopped(STOPPED))


class WriterThread:
    """The process's one Writer, on its one thread. Opening it writes the archive's docs (§6.7),
    as `write_docs_at_startup` did. A failed open is tried again (spec §5): by `retry`, on
    Refresh, whatever the failure; and by `ensure_open` before a request's writes, only after a
    transient failure."""

    def __init__(self, open_writer: Callable[[], Writer]) -> None:
        self.executor = OneThread("recordings-writer")
        self._open_writer = open_writer
        self.writer: Writer | None = None
        self.reason: str | None = OPENING
        self.transient = False  # the last open failed for a reason that can pass (a busy index)
        self.opened: Future = self.executor.submit(self.ensure_open)  # the first open
        self.opening: Future = self.opened  # the latest open, first or retried

    def ensure_open(self) -> bool:
        """On the thread. True once the Writer is open."""
        self.executor.require_thread("WriterThread.ensure_open")
        if self.writer is not None:
            return True
        try:
            self.writer = self._open_writer()
        except (NotTheWriter, StateError, InitError) as exc:
            # Written for display: they name paths and IDs, never a title (spec §5). The text is
            # cannot_tag_reason only; the log carries the class (Global Constraints).
            self._failed(exc, str(exc), transient=False)
        except (LockTimeout, sqlite3.Error, OSError) as exc:
            self._failed(exc, TRANSIENT.format(type(exc).__name__), transient=True)
        except Exception as exc:  # noqa: BLE001  any other failure must leave the app serving
            reason = f"the archive couldn't be opened ({type(exc).__name__})"
            self._failed(exc, reason, transient=False)
        else:
            self.reason, self.transient = None, False
            return True
        return False

    def _failed(self, exc: BaseException, reason: str, *, transient: bool) -> None:
        self.reason, self.transient = reason, transient
        log.warning("not writing the archive's docs, and tagging is off (%s)", type(exc).__name__)

    def retry(self) -> None:
        """Queue another open without waiting for it (Refresh, spec §5). Nothing when the Writer
        is open, an open is still queued, or the thread has stopped."""
        if self.writer is not None or not self.opening.done() or not self.executor.alive():
            return
        with contextlib.suppress(ThreadStopped):
            self.opening = self.executor.submit(self.ensure_open)

    def status(self) -> tuple[bool, str | None]:
        if not self.executor.alive():
            return False, STOPPED
        if self.writer is None:
            return False, self.reason
        return True, None

    def close(self) -> None:
        """At shutdown: what is queued fails with ThreadStopped, and only the job already running
        finishes, so nothing re-creates files after the demo's folder is removed."""
        self.executor.shutdown(wait=False, cancel_futures=True)

    def join(self, timeout: float) -> None:
        """Wait, bounded, for the thread to exit after close."""
        self.executor._thread.join(timeout)
