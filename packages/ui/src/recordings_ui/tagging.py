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
import errno
import logging
import re
import secrets
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypeVar

from pydantic import ValidationError
from recordings.archive import Archive
from recordings.ids import parse_id
from recordings.models import Recording, TagRef, is_private
from recordings.tagops import (
    LOOKS_PRIVATE,
    AddTags,
    Effect,
    Op,
    Recorded,
    RemoveTags,
    RestoreTags,
    WouldExpose,
    effect_of,
    is_private_lookalike,
    removes_last_private,
    validate_new_tag,
)

from recordings_ui import views

if TYPE_CHECKING:
    from recordings.writer import Writer

T = TypeVar("T")
log = logging.getLogger(__name__)

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
STOP_CANCELLED = "Stopped: the request was cancelled"  # its window went, or the app stops
STOP_THREAD = "Stopped: the writer thread stopped; restart the app"
STOP_UNEXPECTED = "Stopped: an unexpected error (the server log has its class)"

LOCK_WAIT = 2.0  # seconds: the service's locks, not the Writer's default 30 (spec §5)
CANCEL_WAIT = 5.0  # seconds a cancelled request waits for the write it had started


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

    def join(self, timeout: float) -> None: ...


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

    def join(self, timeout: float) -> None:
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
        """On the writer thread only: call it through `write`. Anywhere else it raises
        RuntimeError, so a path that forgets `write` fails at once (spec §5)."""
        self._thread.executor.require_thread("ArchiveStore.apply")
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
        waits behind a write; once it has run, it returns at once. Every session awaits the same
        future, so the wait is shielded: a cancelled wait never cancels the open itself."""
        with contextlib.suppress(self._thread_stopped):
            await asyncio.shield(asyncio.wrap_future(self._thread.opened))

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
            # By prefix, as index.py does: SQLITE_BUSY_SNAPSHOT and the like are busy too.
            name = getattr(exc, "sqlite_errorname", None) or ""
            busy = name.startswith(("SQLITE_BUSY", "SQLITE_LOCKED"))
            return Classified(BUSY, False) if busy else Classified(STOP_STATE, True)
        return None

    def close(self) -> None:
        self._thread.close()
        self._reader.shutdown(wait=False, cancel_futures=True)

    def join(self, timeout: float) -> None:
        """After close: wait, bounded, for the writer thread to finish the job it was running."""
        self._thread.join(timeout)


MAX_TAGS = 20
MAX_IDS = 20_000  # a sanity cap (spec §5)
UNDO_KEEP = 20  # actions each session keeps for Undo
MAX_BUSY = 5  # consecutive busy recordings before a batch stops
REQUEST_IDS_KEPT = 1_000  # recent request IDs, per process
USED_UNDO_KEPT = 1_000  # used Undo tokens, per session, so a second use says "already undone"
PREPASS_CHUNK = 200  # recordings per pre-pass job, so other windows' writes interleave
ACTIONS = ("add", "remove", "undo")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_FULL = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})

# A request refused whole, before anything is applied (spec §8).
UNREADABLE = "The request couldn't be read."
NO_RECORDINGS = "Choose at least one recording."
BAD_ID = "A recording ID isn't valid."
NO_TAGS = "Choose at least one tag."
TOO_MANY_IDS = f"At most {MAX_IDS:,} recordings at a time."
TOO_MANY_TAGS = f"At most {MAX_TAGS} tags at a time."
ALREADY_UNDONE = "already undone"
UNDO_GONE = "Undo is no longer available (the page was reloaded)"

Send = Callable[[dict], Awaitable[None]]


def classify_common(exc: BaseException) -> Classified:
    """The rules every store shares (spec §8), after the store's own. Anything unrecognised
    stops the batch (tagging plan, decision 14): a bug must not fail every recording in turn."""
    if isinstance(exc, WouldExpose):
        return Classified(NOT_CONFIRMED, False)
    if isinstance(exc, KeyError | FileNotFoundError):
        return Classified(GONE, False)
    if isinstance(exc, ValidationError | UnicodeDecodeError):
        return Classified(INVALID, False)
    if isinstance(exc, OSError):
        if exc.errno in _FULL:
            return Classified(STOP_FULL, True)
        if exc.errno == errno.EROFS:
            return Classified(STOP_READ_ONLY, True)
        return Classified(STOP_IO, True)
    return Classified(STOP_UNEXPECTED, True)


@dataclass(frozen=True)
class Job:
    """A request that passed the checks (spec §5)."""

    request_id: str
    action: str  # "add", "remove" or "undo"
    ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    undo: str | None = None
    confirm_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Refusal:
    """A request refused whole, before anything was applied (spec §8)."""

    request_id: str | None
    message: str


def _texts(value: object) -> bool:
    """A JSON array of strings. Shiny hands an array inside an input value over as a tuple, and
    tests as a list: both are taken. A lone string never is."""
    return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)


def _request_id(raw: dict) -> str | None:
    """The request's own ID, or None when it isn't one (spec §5). Tag and snippet requests."""
    value = raw.get("request_id")
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def _ids_ok(ids: tuple[str, ...]) -> bool:
    """Every ID is a recording ID: the pattern, and a date and time that exist, so it can name a
    folder (spec §5). An ID that passes and still isn't found fails alone, as "gone"; one with an
    impossible date, such as month 13, is refused here, with its whole request. Tag and snippet
    requests."""
    for rid in ids:
        try:
            parse_id(rid)
        except ValueError:
            return False
    return True


def _outcome_read(request_id: str, rid: str, write: asyncio.Future) -> None:
    """A write's outcome, always read. A write that outlasts its cancelled request is read by
    no one else, and asyncio would log an unread exception with its text and traceback (Global
    Constraints): only its class is logged, here."""
    if write.cancelled():
        return
    exc = write.exception()
    if exc is not None:
        log.info("tag %s: the write on %s ended in %s", request_id, rid, type(exc).__name__)


@dataclass
class _Tally:
    """What a batch has done so far: what its reply and its Undo are built from, however the
    batch ends (spec §5)."""

    total: int
    done: int = 0
    unchanged: int = 0
    now_visible: int = 0
    effects: dict[str, Effect] = field(default_factory=dict)
    afters: dict[str, Recording] = field(default_factory=dict)
    settled: set[str] = field(default_factory=set)  # nothing more for an Undo to do there
    failures: list[dict] = field(default_factory=list)
    stopped: str | None = None

    def landed(self, rid: str, before: Recording, after: Recording) -> None:
        effect = effect_of(before, after)
        self.settled.add(rid)
        if effect.empty:
            self.unchanged += 1
            return
        self.effects[rid], self.afters[rid] = effect, after
        if not before.tags and after.tags and not is_private(after):
            self.now_visible += 1

    def failed(self, rid: str, reason: str) -> None:
        self.failures.append({"id": rid, "reason": reason})
        if reason == GONE:
            self.settled.add(rid)


class TagService:
    """One per process: the store, the request IDs already handled, and shutdown (spec §5)."""

    def __init__(self, store: Store, *, progress_every: float = 0.25) -> None:
        self.store = store
        self.progress_every = progress_every  # seconds between progress replies (decision 10)
        self.stopping = False
        self._handled: OrderedDict[str, None] = OrderedDict()

    def session(self) -> TagSession:
        return TagSession(self)

    def stop(self) -> None:
        """At shutdown. A batch stops between recordings; writes are atomic, so that is safe."""
        self.stopping = True
        self.store.close()

    def claim(self, request_id: str) -> bool:
        """False for an ID already applied or failed: a repeat is ignored (spec §5)."""
        if request_id in self._handled:
            return False
        self._handled[request_id] = None
        while len(self._handled) > REQUEST_IDS_KEPT:
            self._handled.popitem(last=False)
        return True

    def release(self, request_id: str) -> None:
        """A gated request is not recorded (spec §5)."""
        self._handled.pop(request_id, None)


class TagSession:
    """One browser window: its view of the library and its Undo history (spec §5)."""

    def __init__(self, service: TagService) -> None:
        self.service = service
        self.store = service.store
        self.rows: dict[str, dict] = {}
        self._types: set[str] = set()  # note types the view lists, exclusion-only ones included
        self._undo: OrderedDict[str, dict[str, Effect]] = OrderedDict()
        self._used: OrderedDict[str, None] = OrderedDict()
        # The IDs the last `confirm` reply named: the only ones a resend can confirm.
        self._named: frozenset[str] = frozenset()

    # ---- the view ------------------------------------------------------------------------
    async def library(self, *, refresh: bool = False) -> dict:
        """The full view, on load and on Refresh (spec §5). It waits only for the store's first
        open, never behind a write: the view is read on the event loop, and `can_tag` is the
        store's status as last found. Refresh also queues a retry of a failed open, without
        waiting for it, so a retry that works shows on the next Refresh. Undo tokens survive it;
        only a reload, which makes a new session, ends them."""
        await self.store.ready()
        view = self.store.library()
        can_tag, reason = self.store.status(retry=refresh)
        self.rows = {r["id"]: r for r in view["recordings"]}
        self._types = {n["note_type"] for n in view["note_types"]}
        return {**view, "can_tag": can_tag, "cannot_tag_reason": reason, "saves": self.store.saves}

    def _known_tags(self) -> set[str]:
        return {t["tag"] for r in self.rows.values() for t in r["tags"]}

    def _note_types(self) -> set[str]:
        """The note types the archive already has (spec §4): a `notes/*` tag in this view, or a
        type the view lists from an exclusion (tagging plan, decision 18)."""
        tagged = {t.removeprefix("notes/") for t in self._known_tags() if t.startswith("notes/")}
        return tagged | self._types

    # ---- checks, on the event loop, before anything is applied -----------------------------
    def check(self, raw: object) -> Job | Refusal:
        """Never raises: a request this can't read is refused whole."""
        try:
            return self._check(raw)
        except Exception as exc:  # noqa: BLE001  a malformed request must never close the session
            log.warning("tag request refused (%s)", type(exc).__name__)
            return Refusal(None, UNREADABLE)

    def _check(self, raw: object) -> Job | Refusal:
        if not isinstance(raw, dict):
            return Refusal(None, UNREADABLE)
        request_id = _request_id(raw)
        if request_id is None:
            return Refusal(None, UNREADABLE)
        action, confirm = raw.get("action"), raw.get("confirm_ids") or []
        if action not in ACTIONS or not _texts(confirm):
            return Refusal(request_id, UNREADABLE)
        if len(confirm) > MAX_IDS:  # IDs in the request too: the same sanity cap
            return Refusal(request_id, TOO_MANY_IDS)
        if action == "undo":
            token = raw.get("undo")
            if not isinstance(token, str) or not token:
                return Refusal(request_id, UNREADABLE)
            return Job(request_id, "undo", undo=token, confirm_ids=frozenset(confirm))
        ids, tags = raw.get("ids"), raw.get("tags")
        if not _texts(ids) or not _texts(tags):
            return Refusal(request_id, UNREADABLE)
        ids, tags = tuple(dict.fromkeys(ids)), tuple(dict.fromkeys(tags))
        if not ids:
            return Refusal(request_id, NO_RECORDINGS)
        if len(ids) > MAX_IDS:
            return Refusal(request_id, TOO_MANY_IDS)
        if not _ids_ok(ids):
            return Refusal(request_id, BAD_ID)
        if not tags:
            # No clear-all: an empty removal is refused, so a bug can never become one.
            return Refusal(request_id, NO_TAGS)
        if len(tags) > MAX_TAGS:
            return Refusal(request_id, TOO_MANY_TAGS)
        if action == "add":
            known, note_types = self._known_tags(), self._note_types()
            for tag in tags:
                if tag in known:
                    # An existing tag is taken as it is, whatever its spelling, except a privacy
                    # lookalike: adding it would spread it to more recordings (Task 2's review,
                    # M2). A removal still accepts any spelling.
                    problem = LOOKS_PRIVATE if is_private_lookalike(tag) else None
                else:
                    problem = validate_new_tag(tag, note_types=note_types, existing=known)
                if problem is not None:
                    return Refusal(request_id, problem)
        return Job(request_id, action, ids, tags, confirm_ids=frozenset(confirm))

    # ---- running a request -----------------------------------------------------------------
    async def run(self, job: Job | Refusal, send: Send) -> None:
        """Apply one request and reply. It runs as the session's extended task, outside Shiny's
        reactive lock, and never raises: a tagging error never closes the session (spec §5). A
        cancellation alone goes on, once what landed is recorded and a reply tried."""
        request_id = job.request_id
        try:
            # Applied or failed, a request's ID is recorded, and a repeat is ignored (spec §5). A
            # refusal with no usable ID can't be recorded, so each one is answered.
            if request_id is not None and not self.service.claim(request_id):
                log.info("tag %s: a repeated request, ignored", request_id)
                return
            if isinstance(job, Refusal):
                reply = self._reply(request_id, "failed", message=job.message)
            else:
                reply = await self._apply(job, send)
                if reply["status"] == "confirm":
                    self.service.release(request_id)
        except Exception as exc:  # noqa: BLE001  the session must stay up (spec §5)
            log.warning("tag %s failed (%s)", request_id, type(exc).__name__)
            reply = self._reply(request_id, "failed", message=STOP_UNEXPECTED)
        # One send, which never raises: a reply that can't be sent is never followed by another
        # saying the request failed, when what it reported may have landed.
        await self._quietly(send, reply)

    async def _apply(self, job: Job, send: Send) -> dict:
        # Only the resend straight after a confirm reply can confirm, and only the IDs it named:
        # pre-filled confirm_ids, or a cancelled dialog's, are never consent (council ruling).
        named, self._named = self._named, frozenset()
        log.info(
            "tag %s: %s, %d recording(s), %d tag(s)",
            job.request_id,
            job.action,
            len(job.ids),
            len(job.tags),
        )
        can_tag, reason = await self.store.open_for_write()  # retries a transient failure (§5)
        if not can_tag:
            return self._reply(job.request_id, "failed", message=f"Stopped: {reason}")
        undoing: dict[str, Effect] | None = None  # the effects an Undo reverses
        if job.action == "undo":
            undoing = self._undo.get(job.undo or "")
            if undoing is None:
                message = ALREADY_UNDONE if job.undo in self._used else UNDO_GONE
                return self._reply(job.request_id, "failed", message=message)
            effects = undoing
            targets = tuple(effects)

            def make(rid: str, expose: frozenset[str]) -> Op:
                return RestoreTags(effects[rid], expose)

        elif job.action == "add":
            refs = tuple(TagRef(tag=t, by="you") for t in job.tags)
            targets = job.ids

            def make(rid: str, expose: frozenset[str]) -> Op:
                return AddTags(refs)

        else:
            targets = job.ids

            def make(rid: str, expose: frozenset[str]) -> Op:
                return RemoveTags(job.tags, expose)

        # The gate may expose only what this request's own pre-pass found and the window
        # confirmed: never the rest of the client's confirm_ids (spec §5).
        may_expose: frozenset[str] = frozenset()
        if job.action != "add":  # only a removal can expose a recording
            losing = await self._prepass(targets, make)
            if not set(losing) <= job.confirm_ids & named:
                self._named = frozenset(losing)
                visible = sum(losing.values())
                return self._reply(
                    job.request_id,
                    "confirm",
                    total=len(targets),
                    loses_private={
                        "visible": visible,
                        "untagged": len(losing) - visible,
                        "ids": list(losing),
                    },
                )
            may_expose = frozenset(losing)
        return await self._batch(job, targets, make, may_expose, send, undoing)

    def _settle_undo(self, token: str, effects: dict[str, Effect], settled: set[str]) -> None:
        """An Undo that was applied uses up its token; a gated one never does (spec §5). What it
        didn't settle, because it failed there (busy, say) or the batch stopped first, stays
        under the same token, in its place, so "try again" can. A recording gone since is
        settled: nothing more can be undone there."""
        left = {rid: e for rid, e in effects.items() if rid not in settled}
        if left:
            self._undo[token] = left
            return
        self._undo.pop(token, None)
        self._used[token] = None
        while len(self._used) > USED_UNDO_KEPT:
            self._used.popitem(last=False)

    def _keep(self, token: str, effects: dict[str, Effect]) -> None:
        """Keep an action for Undo; the session keeps the last UNDO_KEEP (spec §5)."""
        self._undo[token] = effects
        while len(self._undo) > UNDO_KEEP:
            self._undo.popitem(last=False)

    async def _prepass(
        self, targets: tuple[str, ...], make: Callable[[str, frozenset[str]], Op]
    ) -> dict[str, bool]:
        """The recordings this request would leave with no private tag: True for those that keep
        other tags (visible), False for those left with none (untagged). It reads the files, on
        the writer thread, and only counts: the operation's own gate decides, under the lock,
        on the state it writes (spec §5)."""
        losing: dict[str, bool] = {}
        for start in range(0, len(targets), PREPASS_CHUNK):
            chunk = targets[start : start + PREPASS_CHUNK]
            losing |= await self.store.write(partial(self._would_lose, chunk, make))
        return losing

    def _would_lose(
        self, chunk: tuple[str, ...], make: Callable[[str, frozenset[str]], Op]
    ) -> dict[str, bool]:
        found: dict[str, bool] = {}
        for rid in chunk:
            try:
                before = self.store.read(rid)
                after = make(rid, frozenset({rid})).apply(before)
            except Exception:  # noqa: BLE001  gone or unreadable: the batch reports it
                continue
            if removes_last_private(before, after):
                found[rid] = bool(after.tags)
        return found

    async def _batch(
        self,
        job: Job,
        targets: tuple[str, ...],
        make: Callable[[str, frozenset[str]], Op],
        may_expose: frozenset[str],
        send: Send,
        undoing: dict[str, Effect] | None,
    ) -> dict:
        """Recording by recording, not all-or-nothing: each gets its own write, wrapped for every
        exception (spec §5)."""
        tally = _Tally(total=len(targets))
        cancelled: asyncio.CancelledError | None = None
        busy = 0
        last = time.monotonic()
        try:
            for rid in targets:
                if self.service.stopping:
                    tally.stopped = STOP_SHUTDOWN
                    break
                op = Recorded(make(rid, may_expose), job.request_id)
                error: Exception | None = None
                after: Recording | None
                # Shielded: a write that has started can't be stopped, so a cancelled request
                # waits for it and records what landed (Task 4's carry). asyncio.wait never
                # cancels what it waits for; asyncio.shield would also shield it, but would log
                # the text of a write that failed after the cancel (CPython 3.13+).
                write = asyncio.ensure_future(self.store.write(partial(self.store.apply, rid, op)))
                write.add_done_callback(partial(_outcome_read, job.request_id, rid))
                try:
                    await asyncio.wait({write})
                    after = write.result()
                except asyncio.CancelledError as exc:
                    cancelled = exc
                    after = await self._after_cancel(write, rid, op)
                except Exception as exc:  # noqa: BLE001  each recording is wrapped (spec §5)
                    error, after = exc, await self._recheck(rid, op)
                    log.warning(
                        "tag %s: %s failed on %s (%s)",
                        job.request_id,
                        job.action,
                        rid,
                        type(exc).__name__,
                    )
                tally.done += 1
                if after is not None and op.before is not None:
                    busy = 0
                    tally.landed(rid, op.before, after)
                if cancelled is not None:
                    break
                if error is not None:
                    found = self.store.classify(error) or classify_common(error)
                    if found.stops:
                        tally.stopped = found.reason
                        break
                    if after is None:
                        tally.failed(rid, found.reason)
                        busy = busy + 1 if found.reason == BUSY else 0
                        if busy >= MAX_BUSY:
                            tally.stopped = STOP_BUSY
                            break
                if (
                    tally.done < tally.total
                    and time.monotonic() - last >= self.service.progress_every
                ):
                    last = time.monotonic()
                    progress = self._reply(
                        job.request_id, "progress", done_so_far=tally.done, total=tally.total
                    )
                    await self._quietly(send, progress)
        except Exception as exc:  # noqa: BLE001  a bug between writes stops the batch (decision 14)
            log.warning("tag %s: %s stopped (%s)", job.request_id, job.action, type(exc).__name__)
            tally.stopped = STOP_UNEXPECTED
        except asyncio.CancelledError as exc:  # while a progress reply, or a re-read, was awaited
            cancelled = exc
        if cancelled is None:
            return self._conclude(job, tally, undoing)
        # Cancelled: keep what landed, with its rows and Undo, try a reply, and let the
        # cancellation go on.
        tally.stopped = STOP_CANCELLED
        log.info("tag %s: cancelled after %d of %d", job.request_id, tally.done, tally.total)
        await self._quietly(send, self._conclude(job, tally, undoing))
        raise cancelled

    async def _after_cancel(
        self, write: asyncio.Future, rid: str, op: Recorded
    ) -> Recording | None:
        """The request was cancelled while this recording's write ran. Wait for the write, a
        while, and say what landed, so the reply and the Undo cover it."""
        finished, _ = await asyncio.wait({write}, timeout=CANCEL_WAIT)
        if not finished:
            log.warning("tag %s: a write on %s outlasted the cancel", op.request_id, rid)
            return None
        if not write.cancelled() and write.exception() is None:
            return write.result()
        return await self._recheck(rid, op)

    def _conclude(self, job: Job, tally: _Tally, undoing: dict[str, Effect] | None) -> dict:
        """The reply that ends a batch, with the rows and the Undo token for what it applied
        (spec §5). An Undo is settled before the new action is kept, so keeping its Redo token
        never pushes out another action's token."""
        if undoing is not None and job.undo is not None:
            self._settle_undo(job.undo, undoing, tally.settled)
        token = None
        if tally.effects:
            token = secrets.token_urlsafe(16)
            self._keep(token, tally.effects)
        # From here, a bug costs a row or the counts, never the token or the other rows.
        stopped, rows = tally.stopped, []
        summary: dict = {"counts": None, "tags": None, "note_types": None}
        for rid, rec in tally.afters.items():
            self._types.update(rec.excluded_note_types)  # a removed type stays known (decision 18)
            try:
                self.rows[rid] = views.row(rec)
            except Exception as exc:  # noqa: BLE001  the window's Refresh redraws it
                log.warning("tag %s: no row for %s (%s)", job.request_id, rid, type(exc).__name__)
                stopped = stopped or STOP_UNEXPECTED
                continue
            rows.append(self.rows[rid])
        try:
            summary = views.summarise(self.rows.values(), self._types)
        except Exception as exc:  # noqa: BLE001
            log.warning("tag %s: no counts (%s)", job.request_id, type(exc).__name__)
            stopped = stopped or STOP_UNEXPECTED
        return self._reply(
            job.request_id,
            "failed" if stopped else "done",
            changed=len(tally.effects),
            unchanged=tally.unchanged,
            done_so_far=tally.done,
            total=tally.total,
            now_visible=tally.now_visible,
            failures=tally.failures,
            undo=token,
            rows=rows,
            message=stopped,
            **summary,
        )

    async def _recheck(self, rid: str, op: Recorded) -> Recording | None:
        """After an exception, whether the change landed after all: read on the writer thread,
        in its order, never beside a write still queued there (spec §5)."""
        if op.before is None or op.after is None or op.after == op.before:
            return None  # nothing was about to change: no read needed
        try:
            return await self.store.write(partial(self._landed, rid, op))
        except Exception:  # noqa: BLE001  the thread has stopped: not counted as applied
            return None

    def _landed(self, rid: str, op: Recorded) -> Recording | None:
        """After an exception, read the file again. A change can land and then fail, when an
        fsync of the folder fails after the rename, and then it counts as applied (spec §5)."""
        if op.before is None or op.after is None or op.after == op.before:
            return None
        try:
            now = self.store.read(rid)
        except Exception:  # noqa: BLE001  unreadable now: not counted as applied
            return None
        same = now.tags == op.after.tags
        return now if same and now.excluded_note_types == op.after.excluded_note_types else None

    @staticmethod
    async def _quietly(send: Send, reply: dict) -> None:
        """A reply that can't be sent (the window closed) never stops the batch or raises: the
        batch finishes on the server, and a reload shows what landed (Review Focus 2)."""
        try:
            await send(reply)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "tag %s: %s reply not sent (%s)",
                reply["request_id"],
                reply["status"],
                type(exc).__name__,
            )

    @staticmethod
    def _reply(request_id: str | None, status: str, **fields: object) -> dict:
        reply: dict = {
            "request_id": request_id,
            "status": status,
            "changed": 0,
            "unchanged": 0,
            "done_so_far": 0,
            "total": 0,
            "loses_private": None,
            "now_visible": 0,
            "failures": [],
            "undo": None,
            "rows": [],
            "counts": None,
            "tags": None,
            "note_types": None,
            "message": None,
        }
        reply.update(fields)
        return reply
