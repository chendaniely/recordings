"""A check of one recording here (stage 2b spec §14.1), with §14.4's double fetch: area B1.

**Never checked** (no Plaud call, no attempt, §11.7): a recording with any of its Plaud IDs on the
consent list (read again before each check) or kept out (`plaud_listing.kept_out_at`, a late
keep-out's or late consent listing's open flag, or Keep it here), one gone from Plaud, a parked one
(except through Re-sync, `parked_ok`), and one none of whose Plaud IDs the newest listing holds
("not listed": absent, but not yet gone). So C5.03's "nothing of it is fetched again, even when its
transcript appears" holds for checks too.

**A check** (§14.1):
1. The envelope, bare (`client.envelope(…, enrich=False)`), with a Plaud ID the newest listing
   holds, in the form listed. As soon as it answers: its ID must be this recording's, the consent
   list is read again, and its title goes through §13.1 as the listing's does
   (`listing_privacy.privacy_from_listing`, a no-op when the marker has seen it): a `private` it
   owes lands before any snapshot, and a keep-out word in it is a late keep-out, after which
   nothing more is fetched and nothing is written.
2. Its blocks (`enrich`). Link errors fetch the whole envelope again, once; if they persist the
   check is **degraded**: its snapshot is kept (unless an earlier one of this ID is the same,
   normalised) and never used as a baseline (reconcile skips it), and it is an attempt without
   success (§11.7).
3. Compared with the newest complete snapshot of that Plaud ID, both sides recomputed through
   `normalise.part_hashes` (main spec §9.1.1). Parts are named as §14.1 names them: title,
   transcript, speakers, notes, own notes (Dan's highlights and memos) and other.
4. **A change in a settled recording** fetches the envelope again at once (§14.4): a changed part
   whose hash differs between the two fetches is **volatile**. The second fetch is the one written.
   A second fetch that fails, or has link errors, says nothing about volatility.
5. **Counted** towards the breaker: a check whose only changed part is "other", or that found a
   part volatile; never a recording's first check after its baseline (`baseline_pending`: an
   import, a baseline, a rebaseline), and never a suppressed one. **Suppressed** while the breaker
   is open: a change only in "other" or in parts found volatile writes no snapshot and is
   scheduled as unchanged.
6. Otherwise a change is written: `Writer.add_snapshot` (the snapshot mark first, §11.6), its
   `_meta` the import's stamp (`client.stamp_meta`, `tool: "recordings"`).
7. **Always** `Writer.reconcile_plaud`, which writes nothing when the outputs are up to date: so a
   crash between a snapshot and its reconcile is repaired by the next check (§14.1). Removals it
   holds are kept in `plaud_checks.held_removals` (§14.7's count).
8. The record: a `plaud_check_log` row (its parts; an "other" change's field names as `other:<name>`
   entries, names only, never values), the schedule (`plaud.schedule.after_check`), and an
   `updated` event with the parts for the Sync screen's Updated list.

The fetch happens outside every lock; publishing and reconciling take the recording's (§14.1).
One recording's failure is tried once more, then counted (`failures`, `failing_since`: 3 in a
row puts it on Status's failing list, `checks.FAILING_AFTER`). A lock timeout is busy: neither a
failure nor an attempt. A run-ending Plaud error is raised as itself; an archive-level error is
`CheckStopped`.

No title in any log, result, event or exception: recordings by archive ID, Plaud IDs as
`id_hash`, failures by class name, parts and field names by name.
"""

from __future__ import annotations

import errno
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from recordings.archive import RawSource
from recordings.index import IndexRefused
from recordings.locks import LockTimeout
from recordings.models import Recording
from recordings.plaud import schedule
from recordings.plaud.checks import (
    CONSENT_LATE,
    K_HOLD,
    KEPT_OUT_LATE,
    CheckRow,
    PlaudDB,
)
from recordings.plaud.client import (
    Listed,
    PlaudBadResponse,
    PlaudClient,
    PlaudError,
    ends_the_run,
    id_hash,
    stamp_meta,
)
from recordings.plaud.compare import Here, _kept_out
from recordings.plaud.importer import STOPPED_HELD, ImportStopped
from recordings.plaud.normalise import (
    OWN_NOTE_TYPES,
    envelope_sha256,
    is_complete,
    link_errors,
    normalise,
    note_blocks,
    other_part,
    part_hashes,
    transcript_rows,
)
from recordings.refs import canonical_plaud_id
from recordings.writer import RECONCILE_ERRORS, IndexUnavailable, NotTheWriter, Writer, WriterError

log = logging.getLogger(__name__)

# A check's result (§14.1, §15.6's "A recording")
UP_TO_DATE = "up to date"
UPDATED = "updated"
DEGRADED = "degraded"
SUPPRESSED = "suppressed"
FAILED = "failed"
BUSY = "busy"
NOT_CONSENT = "not checked: on the consent list"
NOT_KEPT_OUT = "not checked: kept out"
GONE = "gone from Plaud"
PARKED = "parked"
NOT_LISTED = "not listed"  # absent from the newest listing, not yet gone: skipped (§14.1)
LOGGED = (UP_TO_DATE, UPDATED, DEGRADED, SUPPRESSED, FAILED)  # the results plaud_check_log holds
SUCCESSES = (UP_TO_DATE, UPDATED, SUPPRESSED)
# The parts, as §14.1 and the Sync screen name them
TITLE, TRANSCRIPT, SPEAKERS, NOTES, OWN_NOTES, OTHER = (
    "title",
    "transcript",
    "speakers",
    "notes",
    "own notes",
    "other",
)
PARTS = (TITLE, TRANSCRIPT, SPEAKERS, NOTES, OWN_NOTES, OTHER)
REMOVAL_HELD = "removal held"  # §15.1's Updated list: a removal reconcile holds for review
FIELD_PREFIX = "other:"  # a field name in plaud_check_log.parts_changed (never a value)
MAX_FIELDS = 12
_FIELD_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")
UNNAMED = "?"  # a key that isn't a plain name is never shown as it is


class CheckStopped(RuntimeError):
    """An archive-level error (the sentinel, state.db, the index, a full disk): going on would only
    fail every later check. `cause` is its class; never its text."""

    def __init__(self, cause: str) -> None:
        super().__init__(f"error: `{cause}`")
        self.cause = cause


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome (§14.1): counts, names and IDs, never a title."""

    recording_id: str
    result: str
    parts: tuple[str, ...] = ()  # changed, by §14.1's names
    volatile: tuple[str, ...] = ()  # changed parts that differed between two fetches
    fields: tuple[str, ...] = ()  # an "other" change's field names (never values)
    counted: bool = False  # towards the breaker (§14.4)
    failure: str | None = None  # the failure's class
    snapshot: str | None = None  # the snapshot written, relative to the recording's folder

    @property
    def attempt(self) -> bool:
        """§11.7: a check that fetched, or tried to; skipped and busy ones aren't."""
        return self.result in LOGGED

    @property
    def success(self) -> bool:
        """A degraded check is an attempt without success (§11.7)."""
        return self.result in SUCCESSES

    def phrase(self) -> str:
        """§15.6's "A recording": "updated: speakers, title", "failed: Plaud unavailable", …"""
        if self.result == UPDATED and self.parts:
            return f"updated: {', '.join(self.parts)}"
        if self.result == FAILED and self.failure:
            return f"failed: {self.failure}"
        return self.result


class _Skip(Exception):
    def __init__(self, result: str) -> None:
        super().__init__(result)
        self.result = result


class _Owed(Exception):
    """The envelope's title owes `private`, and it couldn't be written: nothing is published."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


_RETRIED = (
    WriterError,
    KeyError,
    OSError,
    ValueError,
    TypeError,
    RecursionError,
    ArithmeticError,
    sqlite3.Error,
    IndexRefused,
)


def _stops_the_run(exc: BaseException) -> bool:
    if isinstance(exc, OSError):
        return exc.errno in (errno.ENOSPC, errno.EDQUOT)
    return isinstance(exc, (sqlite3.Error, IndexUnavailable, IndexRefused, NotTheWriter))


def _title(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def plaud_ids(rec: Recording) -> list[str]:
    """The recording's canonical Plaud IDs, in `sources` order, each once."""
    seen: list[str] = []
    for source in rec.sources:
        if source.kind == "plaud" and (pid := canonical_plaud_id(source.ref)) not in seen:
            seen.append(pid)
    return seen


def own_types(*envelopes: dict | None) -> frozenset[str]:
    """The note types that are Dan's own notes (highlights, memos) in these envelopes."""
    own = set()
    for env in envelopes:
        if not isinstance(env, dict):
            continue
        for note_type, block, _raw in note_blocks(normalise(env)):
            if str(block.get("data_type") or "") in OWN_NOTE_TYPES:
                own.add(note_type)
    return frozenset(own)


def display(key: str, own: frozenset[str]) -> str:
    """A `part_hashes` key as §14.1 names it."""
    if key.startswith("notes:"):
        return OWN_NOTES if key.removeprefix("notes:") in own else NOTES
    return key if key in PARTS else OTHER


def part_names(keys: list[str], own: frozenset[str]) -> tuple[str, ...]:
    named = {display(k, own) for k in keys}
    return tuple(p for p in PARTS if p in named)


def other_fields(old: dict | None, new: dict) -> tuple[str, ...]:
    """The names of the fields that changed in "other" (`normalise.other_part`), never their
    values: a top-level key, or a block's or a segment row's key under its list ("source_list.
    share_state"). A key that isn't a plain name shows as "?"."""
    found: set[str] = set()

    def name(path: list[str]) -> str:
        clean = [p if _FIELD_NAME.fullmatch(p) else UNNAMED for p in path]
        return clean[0] if len(clean) == 1 else f"{clean[0]}.{clean[-1]}"

    def walk(a: Any, b: Any, path: list[str]) -> None:
        if a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in set(a) | set(b):
                if a.get(key) != b.get(key):
                    walk(a.get(key), b.get(key), [*path, str(key)])
            return
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for x, y in zip(a, b, strict=True):
                walk(x, y, path)
            return
        if isinstance(a, list) and isinstance(b, list):
            for item in (*a, *b):
                if isinstance(item, (dict, list)):
                    walk(item, None, path)
            if path:
                found.add(name(path))
            return
        if isinstance(a, dict) or isinstance(b, dict):
            keys = set(a) if isinstance(a, dict) else set(b)  # type: ignore[arg-type]
            for key in keys:
                found.add(name([*path, str(key)]))
            return
        if path:
            found.add(name(path))

    walk(other_part(old) if isinstance(old, dict) else {}, other_part(new), [])
    return tuple(sorted(found)[:MAX_FIELDS])


@dataclass
class Snapshots:
    """A recording's snapshots of one Plaud ID, read once (write-once files)."""

    envelopes: list[tuple[datetime, dict | None]] = field(default_factory=list)

    @property
    def newest_complete(self) -> dict | None:
        for _when, env in sorted(self.envelopes, key=lambda p: p[0], reverse=True):
            if isinstance(env, dict) and is_complete(env):
                return env
        return None

    def holds(self, env: dict) -> bool:
        """Whether one is the same as `env`, normalised (a degraded one isn't written twice)."""
        digest = envelope_sha256(env)
        return any(isinstance(e, dict) and envelope_sha256(e) == digest for _, e in self.envelopes)


def snapshots(writer: Writer, rec: Recording, pid: str) -> Snapshots:
    folder = writer.archive.path_for(rec.id)
    found = Snapshots()
    for source in rec.sources:
        if source.kind != "plaud" or not source.raw or canonical_plaud_id(source.ref) != pid:
            continue
        try:
            env = json.loads((folder / source.raw).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            env = None
        found.envelopes.append((source.fetched_at or source.added_at, env))
    return found


def complete_with_notes(env: dict | None) -> bool:
    """§14.2's settled test: a complete transcript with at least one Plaud note (not Dan's own)."""
    if not isinstance(env, dict) or not is_complete(env) or not transcript_rows(normalise(env)):
        return False
    return any(
        str(block.get("data_type") or "") not in OWN_NOTE_TYPES
        and str(block.get("data_content") or "").strip()
        for _type, block, _raw in note_blocks(normalise(env))
    )


@dataclass
class Checker:
    """What a run hands each check: its Writer, state.db, client, consent list (`.current()`,
    which raises the importer's ImportStopped on a stop), the default time zone, the run's ID, the
    schedule's settings, and whether the breaker is open (suppression, §14.4)."""

    writer: Writer
    db: PlaudDB
    client: PlaudClient
    consent: Any  # importer.ConsentGuard: .current() -> ConsentList, or ImportStopped
    timezone_name: str
    run_id: str
    schedule_minutes: int
    full_sweep_days: int
    breaker_open: bool = False
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    # ---- the one entry point ---------------------------------------------------------------
    def check(
        self, recording_id: str, listed: Mapping[str, Listed], *, parked_ok: bool = False
    ) -> CheckResult:
        """Check one recording (the module's notes). `listed`: the newest listing's rows, by
        canonical Plaud ID. Raises a run-ending PlaudError, CheckStopped, the importer's
        ImportStopped (a consent stop, or a hold recorded since the run listed: "held: see
        Status"), KeepOutWordsError; returns everything else."""
        now = self.clock()
        row = self.db.check_row(recording_id)
        try:
            rec, chosen = self._may_check(recording_id, row, listed, parked_ok=parked_ok)
        except _Skip as skip:
            return CheckResult(recording_id, skip.result)
        error: BaseException | None = None
        outcome: CheckResult | None = None
        for _ in range(2):  # §10.2: one recording's failure is tried once more, then counted
            try:
                outcome = self._fetch_and_compare(rec, chosen, row, now=now)
                break
            except _Skip as skip:
                return CheckResult(recording_id, skip.result)
            except LockTimeout:
                log.info("check of %s: busy, tried at the next run", recording_id)
                return CheckResult(recording_id, BUSY)
            except _Owed as owed:
                error = owed
                break
            except PlaudError as exc:
                if ends_the_run(exc):
                    raise
                error = exc
            except _RETRIED as exc:
                if _stops_the_run(exc):
                    raise CheckStopped(type(exc).__name__) from None
                error = exc
        if outcome is None:
            cause = error.cause if isinstance(error, _Owed) else type(error).__name__
            outcome = CheckResult(recording_id, FAILED, failure=cause)
        held = self._reconcile(recording_id)
        self._record(rec, chosen, row, outcome, held, now=now)
        return outcome

    # ---- before any call ---------------------------------------------------------------------
    def _may_check(
        self,
        recording_id: str,
        row: CheckRow | None,
        listed: Mapping[str, Listed],
        *,
        parked_ok: bool,
    ) -> tuple[Recording, Listed]:
        if self.db.get(K_HOLD) is not None:
            # §14.2: the sweep pauses under any hold. One recorded mid-run (a light turn between
            # two recordings) stops the run's checks before another fetch: its listing's IDs are
            # suspect (the end-of-branch privacy review, 2026-11-02).
            raise ImportStopped(STOPPED_HELD)
        try:
            rec = self.writer.archive.load(recording_id)
        except (KeyError, OSError, ValueError):
            raise _Skip(NOT_LISTED) from None  # not readable here now: the next run tries again
        ids = plaud_ids(rec)
        consent = self.consent.current()
        flags = {e.kind for e in self.db.open_flags(recording_id=recording_id)}
        if set(ids) & consent.ids or CONSENT_LATE in flags:
            raise _Skip(NOT_CONSENT)
        if (
            KEPT_OUT_LATE in flags
            or (row is not None and row.kept_here_at is not None)
            or any(_kept_out(self.db.listing(pid)) for pid in ids)
        ):
            raise _Skip(NOT_KEPT_OUT)
        if row is not None and row.gone_since is not None:
            raise _Skip(GONE)
        if row is not None and row.parked_at is not None and not parked_ok:
            raise _Skip(PARKED)
        chosen = next((listed[pid] for pid in ids if pid in listed), None)
        if chosen is None:
            raise _Skip(NOT_LISTED)
        return rec, chosen

    # ---- the fetch, outside every lock -------------------------------------------------------
    def _envelope(self, rec: Recording, chosen: Listed, *, now: datetime) -> dict:
        """The envelope, bare, checked as soon as it answers, then its blocks; link errors fetch
        it again, once (§10.2)."""
        env = self._bare(rec, chosen, now=now)
        self.client.enrich(env)
        if link_errors(env):
            env = self._bare(rec, chosen, now=now)
            self.client.enrich(env)
        meta = env.get("_meta") if isinstance(env.get("_meta"), dict) else {}
        env["_meta"] = {
            **stamp_meta(chosen, self.timezone_name),
            "enriched_from_links": list(meta.get("enriched_from_links") or []),
        }
        return env

    def _bare(self, rec: Recording, chosen: Listed, *, now: datetime) -> dict:
        from recordings.plaud.listing_privacy import privacy_from_listing

        pid = chosen.canonical_id
        env = self.client.envelope(chosen.listed_id, enrich=False)
        env_id = env.get("id")
        if isinstance(env_id, str) and env_id.strip() and canonical_plaud_id(env_id) != pid:
            raise PlaudBadResponse()  # never another recording's content under this ID
        ids = {pid} | ({canonical_plaud_id(env_id)} if isinstance(env_id, str) else set())
        if ids & self.consent.current().ids:
            raise _Skip(NOT_CONSENT)
        title = _title(env.get("name"))
        if title is not None:
            seen = Listed(
                chosen.listed_id,
                title,
                chosen.created_at,
                chosen.start_at,
                chosen.duration_ms,
                chosen.serial,
            )
            here = Here(by_id={pid: frozenset({rec.id})})
            judged = privacy_from_listing(self.writer, self.db, [seen], here, now=now)
            if rec.id in judged.kept_out_late:
                log.info("check of %s: kept out by the title Plaud's envelope gives", rec.id)
                raise _Skip(NOT_KEPT_OUT)
            if rec.id in judged.busy:
                raise LockTimeout(rec.id)
            if rec.id in judged.unwritten:
                raise _Owed(judged.causes.get(rec.id, "WriterError"))
        return env

    def _fetch_and_compare(
        self, rec: Recording, chosen: Listed, row: CheckRow | None, *, now: datetime
    ) -> CheckResult:
        pid, rid = chosen.canonical_id, rec.id
        env = self._envelope(rec, chosen, now=now)
        fetched = self.clock()
        have = snapshots(self.writer, self.writer.archive.load(rid), pid)
        old = have.newest_complete
        own = own_types(old, env)
        before = part_hashes(old) if old is not None else {}
        after = part_hashes(env)
        keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if not keys and old is not None and envelope_sha256(old) != envelope_sha256(env):
            keys = [OTHER]  # every field lands in a part; this is a backstop
        if link_errors(env):
            written = None if have.holds(env) else self._publish(rid, chosen, env, fetched)
            return CheckResult(rid, DEGRADED, parts=part_names(keys, own), snapshot=written)
        if not keys:
            return CheckResult(rid, UP_TO_DATE)
        pending = row is not None and row.baseline_pending
        volatile: tuple[str, ...] = ()
        if row is not None and row.settled and not pending:
            again = self._second(rec, chosen, now=now)
            if again is not None:
                twice = part_hashes(again)
                volatile = part_names([k for k in keys if after.get(k) != twice.get(k)], own)
                env, fetched = again, self.clock()
        parts = part_names(keys, own)
        fields = other_fields(old, env) if OTHER in parts else ()
        counted = not pending and (set(parts) == {OTHER} or bool(volatile))
        if self.breaker_open and set(parts) <= {OTHER, *volatile}:
            log.info("check of %s: suppressed (%s)", rid, ", ".join(parts))
            return CheckResult(rid, SUPPRESSED, parts, volatile, fields)
        written = self._publish(rid, chosen, env, fetched)
        return CheckResult(rid, UPDATED, parts, volatile, fields, counted, snapshot=written)

    def _second(self, rec: Recording, chosen: Listed, *, now: datetime) -> dict | None:
        """§14.4's second fetch, at once. One that fails, or has link errors, proves nothing."""
        try:
            again = self._envelope(rec, chosen, now=now)
        except PlaudError as exc:
            if ends_the_run(exc):
                raise
            return None
        return None if link_errors(again) else again

    def _publish(self, rid: str, chosen: Listed, env: dict, fetched: datetime) -> str:
        payload = json.dumps(env, indent=1, ensure_ascii=False).encode("utf-8")
        ref = self.writer.add_snapshot(
            rid,
            RawSource(
                kind="plaud",
                ref=chosen.listed_id,
                added_at=fetched,
                fetched_at=fetched,
                payload=payload,
            ),
            origin=f"plaud-check:{self.run_id}",
            now=fetched,
        )
        return ref.raw or ""

    # ---- always reconcile, then record -------------------------------------------------------
    def _reconcile(self, recording_id: str) -> list[dict] | None:
        """§14.1 step 4: idempotent; the removals it holds (None when it couldn't run now: the
        next check repairs it)."""
        try:
            report = self.writer.reconcile_plaud(recording_id)
        except RECONCILE_ERRORS as exc:
            log.info(
                "reconcile of %s after its check: not now (%s)", recording_id, type(exc).__name__
            )
            return None
        return [{"snapshot": h.snapshot, "slot": h.slot, "reason": h.reason} for h in report.held]

    def _record(
        self,
        rec: Recording,
        chosen: Listed,
        row: CheckRow | None,
        outcome: CheckResult,
        held: list[dict] | None,
        *,
        now: datetime,
    ) -> None:
        db, rid = self.db, rec.id
        db.log_check(
            rid,
            outcome.result,
            parts=[*outcome.parts, *(FIELD_PREFIX + f for f in outcome.fields)],
            volatile=outcome.volatile,
            counted=outcome.counted,
            failure=outcome.failure,
            now=now,
        )
        kind: schedule.Outcome = {
            UPDATED: "changed",
            FAILED: "failed",
            DEGRADED: "degraded",
        }.get(outcome.result, "unchanged")  # up to date, and suppressed (§14.4)
        try:
            current = self.writer.archive.load(rid)  # with the snapshot just written
        except (KeyError, OSError, ValueError):
            current = rec
        newest = snapshots(self.writer, current, chosen.canonical_id).newest_complete
        imported = [s.added_at for s in rec.sources if s.kind == "plaud"]
        fields = schedule.after_check(
            row,
            kind,
            now=now,
            settled=schedule.is_settled(
                recorded_at=rec.recorded_at,
                complete_with_notes=complete_with_notes(newest),
                now=now,
            ),
            imported_at=min(imported) if imported else None,
            schedule_minutes=self.schedule_minutes,
            full_sweep_days=self.full_sweep_days,
        )
        if outcome.result != FAILED:
            fields["baseline_pending"] = False  # the excused check is spent (§14.5)
        if held is not None:
            fields["held_removals"] = held
        db.set_check(rid, **fields)
        if outcome.result == UPDATED:
            before = {(h["snapshot"], h["slot"]) for h in (row.held_removals if row else ())}
            fresh = held is not None and any((h["snapshot"], h["slot"]) not in before for h in held)
            parts = [*outcome.parts, *([REMOVAL_HELD] if fresh else [])]
            db.add_event(
                "updated",
                now=now,
                recording_id=rid,
                plaud_id=chosen.canonical_id,
                detail={"parts": parts, "run": self.run_id},
            )
        log.info(
            "check of %s (%s): %s%s",
            rid,
            id_hash(chosen.canonical_id),
            outcome.phrase(),
            f" · volatile: {', '.join(outcome.volatile)}" if outcome.volatile else "",
        )


__all__ = [
    "DEGRADED",
    "FAILED",
    "NOT_CONSENT",
    "NOT_KEPT_OUT",
    "SUPPRESSED",
    "UPDATED",
    "UP_TO_DATE",
    "CheckResult",
    "CheckStopped",
    "Checker",
    "other_fields",
]
