"""Importing one recording from Plaud (stage 2b spec §11.5): the function the sync run calls for
each waiting recording, the CLI's catch-up and the Sync screen's imports included (area A5 wires
it into the run).

**Before any byte** (step 1), checked again here whatever the run checked, because an import
causes downloads and archive writes (§9.3):
- the gate (only the catch-up imports while it is closed) and the listing hold (§11.4: nothing is
  imported while one is on): `ImportStopped`, for the whole run;
- the listing it came from is no older than one schedule interval, else `ListingStale`: the run
  lists again;
- the consent list as it is now (`ConsentGuard.current`): read again when its file changed, and
  trusted only once its file is at least one tick old, so a list written in place in two flushes
  is never read in the pause; a stop, or a file that keeps changing, is `ImportStopped`;
- the keep-out words (the configured one and every word enforced) on the listing's title; words
  that can't be read are `ImportStopped`;
- the ID's own record: kept out by a title and not released, a re-keyed match not released, under
  review after a rebuild (unless Dan named it), or parked: excluded, nothing fetched. An ID already
  here is skipped.

**The envelope** (step 2) is fetched bare (`client.envelope(…, enrich=False)`, Phase 1 plan
decision 37). As soon as it answers its title is checked against the keep-out words and its ID
against the consent list; either match writes nothing of it anywhere (a title match records the
ID as kept out, by its word, so it stays out). Only then are its blocks fetched (`enrich`). A
block whose link expired fetches the envelope again, once, with the same checks (§10.2).

**The snapshot** (step 3) is the envelope with `_meta` replaced by `client.stamp_meta`'s time
fields and `tool: "recordings"` (the gate's test, §11.6), and the blocks fetched from links; never
`data_link_errors`. **Tags** (step 4): `private` when any title, the listing's or the envelope's,
matches `auto_private_patterns`, in `Incoming.tags`, so it lands in the same rename as
`recording.json`, before any source or output.

**The disk** (step 5): below `stop_free_percent`, or when twice the audio's Content-Length would
leave less than that margin (`Writer.add` copies the download and hashes it again), imports stop
for the run (`ImportStopped`, "the disk is too full to import"). **The audio** (step 6) streams
into `.tmp/plaud-dl/` (`plaud.scratch`), never into memory or `/tmp`, with the client's stall rule,
Range resume and one fresh link (the envelope fetched again, with its checks). **Measured** (step
7) as the 2a import measures (`media.probe`: `wave` for a WAV, else ffprobe and a decode).

**A hash merge into a flagged recording** (consent-listed, kept out, or with an open privacy flag)
writes nothing: the download is discarded, and the new ID is recorded as a possible re-keyed copy
of it (excluded, flagged for Dan, as §11.4 step 6's match is), since the consent list excludes the
recording, not only its ID.

**Add, reconcile, record** (steps 8 to 10): the snapshot mark first, in its own transaction
(`gate.mark_snapshot`, §11.6), then `Writer.add` with a `plaud` RawSource (its ref the ID as
listed, its payload the snapshot) and the origin `plaud-import:<run ID>`, then
`Writer.reconcile_plaud` (Plaud's outputs, and the marker), then a `plaud_checks` row (unsettled),
the listing row's title hash and duration, and an `imported` or `arrived-private` event.

**Failures.** One recording's failure (a Plaud error that isn't the run's, a short download, media
that can't be measured, a file the Writer refuses) is tried once more, then counted
(`plaud_listing.import_failures`, `next_import_at`); after 3 it is missing ("failed 3 times").
`LockTimeout` is busy: neither a failure nor an attempt (§9.2). A run-ending Plaud error
(`client.ends_the_run`) is raised as itself, for the run to record. An archive-level error (the
sentinel, state.db, the index, a full disk) is `ImportStopped`.

**No title** in any log, result, event or exception: Plaud IDs are logged as `id_hash`, failures
by class name. No recording lock is held across a network call: `Writer.add` and reconcile take
their own, after the download.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from recordings.archive import RawSource
from recordings.disk import STOP_FREE_PERCENT, DiskStatus, disk_status
from recordings.index import IndexRefused
from recordings.locks import LockTimeout
from recordings.media import AUDIO_SUFFIXES, media_kind, probe
from recordings.models import TagRef, is_private
from recordings.plaud import keepout
from recordings.plaud.baseline import KeepOutWordsError
from recordings.plaud.checks import FLAG_KINDS, K_HOLD, PlaudDB
from recordings.plaud.client import (
    Listed,
    PlaudBadResponse,
    PlaudClient,
    PlaudError,
    PlaudLinkExpired,
    ends_the_run,
    id_hash,
    stamp_meta,
)
from recordings.plaud.compare import (
    BY_CONSENT,
    BY_REKEY,
    BY_TITLE,
    BY_WAS_TITLE,
    REKEYED_EVENT,
    _kept_out,
    _rekeyed,
    title_hash,
)
from recordings.plaud.compare import (
    _released as released,
)
from recordings.plaud.flags import listed_here
from recordings.plaud.gate import GATE_REFUSAL, gate_now, mark_snapshot
from recordings.plaud.keepout import ConsentList, keep_out_match
from recordings.plaud.reconcile import matches_auto_private
from recordings.plaud.scratch import download_dir, drop_download_dir
from recordings.plaud.settings import DEFAULT_SCHEDULE_MINUTES
from recordings.refs import canonical_plaud_id
from recordings.writer import (
    RECONCILE_ERRORS,
    Incoming,
    IndexUnavailable,
    NotTheWriter,
    Writer,
    WriterError,
)

log = logging.getLogger(__name__)

# Why imports stop for the run (§15.6's run phrases)
STOPPED_CONSENT = "imports stopped: consent list"
STOPPED_DISK = "imports stopped: the disk is too full to import"
STOPPED_HELD = "held: see Status"
STOPPED_WORDS = "imports stopped: the keep-out words can't be read"
CONSENT_UNSETTLED = "the consent list keeps changing"
# Excluded, with nothing fetched or written (a fixed kind, never a title)
EXCLUDED_REVIEW = "review"  # needs Dan's review after a rebuild (§11.6), and he didn't name it
EXCLUDED_PARKED = "parked"
EXCLUDED_FLAGGED = "flagged"  # its audio is a consent-listed or kept-out recording's
SETTLE_SECONDS = 2.0  # the worker's tick: a consent file younger than this isn't trusted yet
SETTLE_TRIES = 5
PLACEHOLDER_TITLE = "Untitled recording"  # Plaud gave no title at all (a malformed row)
DEFAULT_SUFFIX = ".mp3"  # audio-router stored Plaud's audio as .mp3 (store.audio_path)
RETRY_BACKOFF = timedelta(minutes=20)  # a failed import's next try, doubling per failure
RETRY_BACKOFF_CAP = timedelta(hours=24)
MEDIA_UNMEASURED = "its media can't be measured (spec §9.1.1)"
NO_AUDIO = "Plaud gave no audio link"
OTHER_RECORDING = "Plaud's envelope names another recording"

Outcome = Literal["imported", "merged", "excluded", "here", "busy", "failed"]


class ImportStopped(RuntimeError):
    """Imports stop for this run (the rest become missing): the gate, a hold, a consent stop, the
    keep-out words, the disk, or the archive itself. `phrase` is §15.6's fixed phrase; `cause`
    the archive-level error's class, when it was one. Never a title, a path or a URL."""

    def __init__(self, phrase: str, cause: str | None = None) -> None:
        super().__init__(phrase)
        self.phrase = phrase
        self.cause = cause


class ListingStale(RuntimeError):
    """The listing the ID came from is older than one schedule interval: the run lists again
    (§11.5 step 1)."""


@dataclass(frozen=True)
class ImportResult:
    plaud_id: str  # canonical
    outcome: Outcome
    recording_id: str | None = None
    private: bool = False  # arrived private
    why: str | None = None  # excluded: its kind; failed: the error's class; never a title
    reconciled: bool = True  # False: added, but its outputs wait for the next reconcile

    @property
    def attempt(self) -> bool:
        """§11.7: an import tried counts; excluded, here and busy don't."""
        return self.outcome in ("imported", "merged", "failed")

    @property
    def success(self) -> bool:
        return self.outcome in ("imported", "merged")


class _Excluded(Exception):
    """Raised inside a fetch (a fresh link's envelope) when the recording turned out kept out."""

    def __init__(self, result: ImportResult) -> None:
        super().__init__(result.why)
        self.result = result


class _DiskRefused(Exception):
    """The disk guard refused a download before its first byte (§11.5 step 5)."""


def _consent_key(path: Path | None) -> tuple[keepout.FileSignature, int] | None:
    """The consent file's inode, size and mtime, and its ctime, which an edit that keeps the mtime
    (`cp -p`, `touch -r`) still moves; None when it can't be stat'ed (as the worker's tick)."""
    sig = keepout.signature(path)
    if sig is None or path is None:
        return None
    try:
        return sig, Path(path).stat().st_ctime_ns
    except OSError:
        return None


class ConsentGuard:
    """The consent list as it is now, before each recording's first byte (§11.5 step 1). Read
    again when its file changed (inode, size, mtime, ctime). A file changed less than `settle`
    seconds ago isn't trusted yet: the guard waits until it is that old, so a list written in
    place in two flushes is never read in the pause (Phase 1's phase-end list, Task 13; S11). A
    read counts only when the file is the same before and after it. A stop, or a file that keeps
    changing for `tries` reads, raises ImportStopped: imports fail closed (P8)."""

    def __init__(
        self,
        path: Path | None,
        *,
        settle: float = SETTLE_SECONDS,
        tries: int = SETTLE_TRIES,
        sleep: Callable[[float], None] = time.sleep,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.settle, self.tries, self.sleep, self.clock_ns = settle, tries, sleep, clock_ns
        self._list: ConsentList | None = None
        self._key: tuple[keepout.FileSignature, int] | None = None

    def current(self) -> ConsentList:
        for _ in range(self.tries):
            key = _consent_key(self.path)
            if key is None:
                raise ImportStopped(STOPPED_CONSENT)  # missing, or can't be stat'ed: a stop
            age = (self.clock_ns() - max(key[0].mtime_ns, key[1])) / 1e9
            if age < self.settle:
                self.sleep(self.settle - max(age, 0.0))
                continue
            if self._list is not None and key == self._key:
                reading = self._list
            else:
                reading = keepout.read_consent(self.path)
                if _consent_key(self.path) != key:
                    continue  # written while it was read: read it again
                self._list, self._key = reading, key
            if reading.stop is not None:
                raise ImportStopped(STOPPED_CONSENT)
            return reading
        raise ImportStopped(STOPPED_CONSENT, CONSENT_UNSETTLED)


def _suffix(url: object) -> str:
    """The audio's file suffix, from the link's path when it names an audio type, else `.mp3`
    (audio-router's: Plaud serves mp3). Only the name changes with it: `media.probe` reads the
    bytes, and the duration is always the decoded one."""
    try:
        suffix = PurePosixPath(urlsplit(str(url)).path).suffix.lower()
    except ValueError:
        return DEFAULT_SUFFIX
    return suffix if suffix in AUDIO_SUFFIXES else DEFAULT_SUFFIX


def _title(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _blocks_expired(env: dict) -> bool:
    for section in ("source_list", "note_list"):
        blocks = env.get(section)
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("_data_link_error") == "PlaudLinkExpired":
                return True
    return False


def _stops_the_run(exc: BaseException) -> bool:
    """An archive-level error (§11.5): the sentinel, state.db, the index, a full disk. Going on
    would only fail every later recording."""
    if isinstance(exc, OSError):
        return exc.errno in (errno.ENOSPC, errno.EDQUOT)
    return isinstance(exc, (sqlite3.Error, IndexUnavailable, IndexRefused, NotTheWriter))


def record_kept_out(db: PlaudDB, listed_id: str, word: str, *, now: datetime) -> None:
    """An ID kept out by a title that starts with `word` (§12.2): when and by which word, under
    its canonical ID, never its title. Kept once set, unless Release had let it in: a title that
    takes the word again keeps it out again."""
    row = db.listing(listed_id)
    if row is None or row.kept_out_at is None or released(row):
        db.set_listing(listed_id, kept_out_at=now, kept_out_word=word, last_seen=now)


def record_import_failure(db: PlaudDB, listed_id: str, *, now: datetime) -> int:
    """One more failed import of this ID: counted, and its next try backs off (doubling from
    RETRY_BACKOFF, at most RETRY_BACKOFF_CAP). Returns the count; at IMPORT_FAILURES (3) it is
    missing, "failed 3 times · Park" (§11.5)."""
    row = db.listing(listed_id)
    failures = (row.import_failures if row is not None else 0) + 1
    wait = min(RETRY_BACKOFF * 2 ** min(failures - 1, 10), RETRY_BACKOFF_CAP)
    db.set_listing(listed_id, import_failures=failures, next_import_at=now + wait, last_seen=now)
    return failures


@dataclass
class Importer:
    """What a run hands each import: its Writer, state.db, client and consent guard, the default
    time zone, the run's ID, the schedule (for the listing's freshness), and the disk's stop
    margin. `catch_up` lets the first catch-up import while the gate is closed (§11.6)."""

    writer: Writer
    db: PlaudDB
    client: PlaudClient
    consent: ConsentGuard
    timezone_name: str
    run_id: str
    schedule_minutes: int = DEFAULT_SCHEDULE_MINUTES
    stop_free_percent: float = STOP_FREE_PERCENT
    catch_up: bool = False
    clock: Callable[[], datetime] | None = None
    disk: Callable[[], DiskStatus] | None = None

    # ---- the one entry point ---------------------------------------------------------------
    def import_one(
        self, listed: Listed, *, listing_at: datetime, review_ok: bool = False
    ) -> ImportResult:
        """Import one listed recording (the module's notes). Raises ImportStopped, ListingStale,
        or a run-ending PlaudError; returns everything else as an ImportResult."""
        pid = listed.canonical_id
        now = self._now()
        self._may_import(now, listing_at)
        consent = self.consent.current()
        words = self._words()
        early = self._before_any_byte(listed, consent, words, review_ok=review_ok, now=now)
        if early is not None:
            return early
        error: BaseException | None = None
        for _ in range(2):  # §10.2: one recording's failure is tried once more, then counted
            try:
                return self._fetch_and_add(listed, words, now=now)
            except _Excluded as exc:
                return exc.result
            except LockTimeout:
                log.info("import of %s: busy, tried at the next run", id_hash(pid))
                return ImportResult(pid, "busy")
            except _DiskRefused:
                raise ImportStopped(STOPPED_DISK) from None
            except PlaudError as exc:
                if ends_the_run(exc):
                    raise
                error = exc
            except (
                WriterError,
                KeyError,
                OSError,
                ValueError,
                TypeError,
                RecursionError,
                ArithmeticError,
                sqlite3.Error,
                IndexRefused,
            ) as exc:
                if _stops_the_run(exc):
                    name = type(exc).__name__
                    raise ImportStopped(f"error: `{name}`", name) from None
                error = exc
            consent = self.consent.current()  # the retry is a new first byte: checked again
            words = self._words()
            again = self._before_any_byte(listed, consent, words, review_ok=review_ok, now=now)
            if again is not None:
                return again
        cause = type(error).__name__
        failures = record_import_failure(self.db, listed.listed_id, now=self._now())
        log.info("import of %s failed (%s), %d in a row", id_hash(pid), cause, failures)
        return ImportResult(pid, "failed", why=cause)

    # ---- step 1: before any byte -----------------------------------------------------------
    def _now(self) -> datetime:
        return self.clock() if self.clock is not None else datetime.now(UTC)

    def _may_import(self, now: datetime, listing_at: datetime) -> None:
        if not self.catch_up and not gate_now(self.db).open:
            raise ImportStopped(GATE_REFUSAL)
        if self.db.get(K_HOLD) is not None:
            raise ImportStopped(STOPPED_HELD)
        minutes = self.schedule_minutes if self.schedule_minutes > 0 else DEFAULT_SCHEDULE_MINUTES
        if now - listing_at > timedelta(minutes=minutes):
            raise ListingStale("the listing is older than one schedule interval: list again")

    def _words(self) -> tuple[str, ...]:
        try:
            return self.writer.keep_out_words()
        except KeepOutWordsError:
            raise ImportStopped(STOPPED_WORDS) from None

    def _before_any_byte(
        self,
        listed: Listed,
        consent: ConsentList,
        words: tuple[str, ...],
        *,
        review_ok: bool,
        now: datetime,
    ) -> ImportResult | None:
        """The ID's own checks, with no Plaud call: an ImportResult when it isn't imported."""
        pid = listed.canonical_id
        if pid in consent.ids:
            return ImportResult(pid, "excluded", why=BY_CONSENT)
        word = keep_out_match(listed.title, words)
        if word is not None:
            record_kept_out(self.db, listed.listed_id, word, now=now)
            return ImportResult(pid, "excluded", why=BY_TITLE)
        row = self.db.listing(pid)
        if _kept_out(row):
            return ImportResult(pid, "excluded", why=BY_WAS_TITLE)
        if _rekeyed(row):
            return ImportResult(pid, "excluded", why=BY_REKEY)
        if row is not None and row.needs_review and not review_ok:
            return ImportResult(pid, "excluded", why=EXCLUDED_REVIEW)
        if row is not None and row.parked_at is not None:
            return ImportResult(pid, "excluded", why=EXCLUDED_PARKED)
        w = self.writer
        found, unavailable = listed_here(w.index, w.identity.uuid, w.state, w.archive, {pid})
        if unavailable:
            raise ImportStopped("error: `IndexUnavailable`", "IndexUnavailable")
        if found:
            return ImportResult(pid, "here", recording_id=found[0][0])
        return None

    # ---- steps 2 to 10 ---------------------------------------------------------------------
    def _envelope(self, listed: Listed, words: tuple[str, ...], *, now: datetime) -> dict:
        """`files/{id}` bare, checked as soon as it answers (step 2): its title against the
        keep-out words, its ID against the consent list as it reads now. Raises _Excluded."""
        pid = listed.canonical_id
        env = self.client.envelope(listed.listed_id, enrich=False)
        env_id = env.get("id")
        if isinstance(env_id, str) and env_id.strip() and canonical_plaud_id(env_id) != pid:
            raise PlaudBadResponse()  # never another recording's content under this ID
        consent = self.consent.current()
        ids = {pid} | ({canonical_plaud_id(env_id)} if isinstance(env_id, str) else set())
        if ids & consent.ids:
            raise _Excluded(ImportResult(pid, "excluded", why=BY_CONSENT))
        word = keep_out_match(_title(env.get("name")), words)
        if word is not None:
            record_kept_out(self.db, listed.listed_id, word, now=now)
            log.info("%s: kept out by the title Plaud's envelope gives", id_hash(pid))
            raise _Excluded(ImportResult(pid, "excluded", why=BY_TITLE))
        return env

    def _fetch_and_add(self, listed: Listed, words: tuple[str, ...], *, now: datetime):
        pid = listed.canonical_id
        env = self._envelope(listed, words, now=now)
        self.client.enrich(env)
        if _blocks_expired(env):  # §10.2: a link that expired fetches the envelope again, once
            env = self._envelope(listed, words, now=now)
            self.client.enrich(env)
        fetched = self._now()
        meta = env.get("_meta") if isinstance(env.get("_meta"), dict) else {}
        env["_meta"] = {
            **stamp_meta(listed, self.timezone_name),
            "enriched_from_links": list(meta.get("enriched_from_links") or []),
        }
        titles = [t for t in (_title(listed.title), _title(env.get("name"))) if t is not None]
        private = matches_auto_private(titles, self.writer.auto_private)
        recorded_at = self._recorded_at(listed)
        url = env.get("presigned_url")
        if not isinstance(url, str) or not url:
            raise PlaudBadResponse()  # NO_AUDIO: that recording's failure
        status = self._disk()
        if status.level == "stop":
            raise _DiskRefused()
        root = self.writer.root
        dest = download_dir(root) / f"{id_hash(pid)}-{uuid.uuid4().hex}{_suffix(url)}"
        try:
            self.client.download(
                url,
                dest,
                fresh_link=lambda: self._fresh_link(listed, words, now=now),
                admit=self._admit,
            )
            probed = probe(dest)
            if probed is None:
                raise WriterError(MEDIA_UNMEASURED)
            holder = self.writer.holding(dest, recorded_at)
            if holder is not None and self._flagged(holder):
                return self._discard_into_flagged(listed, holder, now=now)
            payload = json.dumps(env, indent=1, ensure_ascii=False).encode("utf-8")
            mark_snapshot(self.db, now=self._now(), by="import")  # §11.6: before the snapshot
            added = self.writer.add(
                Incoming(
                    media=dest,
                    recorded_at=recorded_at,
                    timezone_name=self.timezone_name,
                    time_source="plaud",
                    title=_title(env.get("name")) or _title(listed.title) or PLACEHOLDER_TITLE,
                    title_by="plaud" if titles else None,
                    kind=media_kind(dest, probed) or "audio",
                    sources=(
                        RawSource(
                            kind="plaud",
                            ref=listed.listed_id,
                            added_at=fetched,
                            fetched_at=fetched,
                            payload=payload,
                        ),
                    ),
                    duration_ms=probed.duration_ms,
                    sample_rate=probed.sample_rate,
                    channels=probed.channels,
                    tags=(TagRef(tag="private", by="auto"),) if private else (),
                    origin=f"plaud-import:{self.run_id}",
                )
            )
        finally:
            with contextlib.suppress(OSError):
                dest.unlink(missing_ok=True)
            drop_download_dir(root)
        return self._record(listed, added.recording.id, added.created, private, now=fetched)

    def _recorded_at(self, listed: Listed) -> datetime:
        """Plaud's start time (else its creation time) in the default zone (main spec §6.2; S12
        skipped: the guesses file). With neither, that recording fails: no ID can be made."""
        when = listed.recorded_at
        if when is None:
            raise ValueError("Plaud gave no recording time")
        return when.astimezone(ZoneInfo(self.timezone_name))

    def _disk(self) -> DiskStatus:
        if self.disk is not None:
            return self.disk()
        return disk_status(
            self.writer.root,
            expected_uuid=self.writer.identity.uuid,
            stop=self.stop_free_percent,
            warn=max(self.stop_free_percent, 20.0),
        )

    def _admit(self, length: int) -> None:
        """The disk guard (§11.5 step 5), with the audio's Content-Length, before its first byte:
        twice the length (the download, then `Writer.add`'s copy) must leave the stop margin."""
        status = self._disk()
        if status.level == "stop":
            raise _DiskRefused()
        if status.free_bytes is None or not status.total_bytes:
            return  # free space unknown (no statvfs): the level above already said "stop" if bad
        left = (status.free_bytes - 2 * length) * 100 / status.total_bytes
        if left < self.stop_free_percent:
            raise _DiskRefused()

    def _fresh_link(self, listed: Listed, words: tuple[str, ...], *, now: datetime) -> str:
        """A new link to the same audio, once (§10.1): the envelope again, with its checks."""
        env = self._envelope(listed, words, now=now)
        url = env.get("presigned_url")
        if not isinstance(url, str) or not url:
            raise PlaudLinkExpired()
        return url

    def _flagged(self, recording_id: str) -> bool:
        """Whether the recording these bytes already belong to is kept out: one of its Plaud IDs
        consent-listed or kept out (Keep it here included: nothing more is fetched for it), or an
        open privacy flag (§11.5's last rule)."""
        if self.db.open_flags(FLAG_KINDS, recording_id=recording_id):
            return True
        try:
            rec = self.writer.archive.load(recording_id)
        except (KeyError, OSError, ValueError):
            return True  # can't be read now: never merged into blind (P8)
        ids = {canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud"}
        consent = self.consent.current()
        if ids & consent.ids:
            return True
        return any(_kept_out(self.db.listing(i)) for i in ids)

    def _discard_into_flagged(self, listed: Listed, holder: str, *, now: datetime) -> ImportResult:
        """The download is discarded (the caller's `finally`); the new ID is recorded as a
        possible re-keyed copy of the flagged recording, excluded and flagged for Dan."""
        pid = listed.canonical_id
        rec = self.writer.archive.load(holder)
        ids = sorted({canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud"})
        consent = self.consent.current()
        of = BY_CONSENT if set(ids) & consent.ids else "kept-out"
        self.db.set_listing(listed.listed_id, rekey_of=ids[0] if ids else "*", last_seen=now)
        self.db.add_event(
            REKEYED_EVENT,
            now=now,
            plaud_id=pid,
            detail={"of": of, "by": "audio"},
            open_flag=True,
        )
        log.info("%s: its audio is a kept-out recording's: discarded", id_hash(pid))
        return ImportResult(pid, "excluded", why=EXCLUDED_FLAGGED)

    def _record(
        self, listed: Listed, recording_id: str, created: bool, private: bool, *, now: datetime
    ) -> ImportResult:
        """Steps 9 and 10: reconcile, then the records. A reconcile that fails leaves the
        recording here (private already, if it should be): its outputs come with the next
        reconcile, which every check ends with (§14.1)."""
        pid = listed.canonical_id
        reconciled = True
        try:
            self.writer.reconcile_plaud(recording_id)
        except RECONCILE_ERRORS as exc:
            reconciled = False
            log.warning(
                "reconcile of %s after its import failed (%s)", recording_id, type(exc).__name__
            )
        try:
            arrived_private = is_private(self.writer.archive.load(recording_id))
        except (KeyError, OSError, ValueError):
            arrived_private = private
        db = self.db
        db.set_check(
            recording_id,
            last_checked_at=now,
            next_check_at=now + timedelta(minutes=max(self.schedule_minutes, 1)),
            interval_seconds=max(self.schedule_minutes, 1) * 60,
            settled=False,
        )
        title = _title(listed.title)
        db.set_listing(
            listed.listed_id,
            last_seen=now,
            title_sha256=title_hash(title) if title is not None else None,
            duration_ms=listed.duration_ms,
            import_failures=0,
            next_import_at=None,
            needs_review=False,  # Import selected brought in a review row (§11.6): done
        )
        db.add_event(
            "arrived-private" if arrived_private else "imported",
            now=now,
            recording_id=recording_id,
            plaud_id=pid,
            detail={"run": self.run_id, "merged": not created},
        )
        log.info(
            "imported %s as %s%s%s",
            id_hash(pid),
            recording_id,
            " (private)" if arrived_private else "",
            "" if created else " (merged: same audio)",
        )
        return ImportResult(
            pid,
            "imported" if created else "merged",
            recording_id,
            private=arrived_private,
            reconciled=reconciled,
        )


def importer_for(
    writer: Writer,
    client: PlaudClient,
    *,
    consent_list: Path | None,
    timezone_name: str,
    run_id: str | None = None,
    schedule_minutes: int = DEFAULT_SCHEDULE_MINUTES,
    stop_free_percent: float = STOP_FREE_PERCENT,
    catch_up: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Importer:
    """An Importer for one run, from the settings A5's run reads (`plaud_settings`,
    `disk_thresholds`, `[archive] default_timezone`)."""
    return Importer(
        writer=writer,
        db=PlaudDB(writer.state),
        client=client,
        consent=ConsentGuard(consent_list),
        timezone_name=timezone_name,
        run_id=run_id or uuid.uuid4().hex[:12],
        schedule_minutes=schedule_minutes,
        stop_free_percent=stop_free_percent,
        catch_up=catch_up,
        clock=clock,
    )


__all__ = [
    "ConsentGuard",
    "ImportResult",
    "ImportStopped",
    "Importer",
    "ListingStale",
    "importer_for",
    "record_import_failure",
    "record_kept_out",
    "released",
]
