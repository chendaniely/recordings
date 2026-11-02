"""The sync run (stage 2b spec §11.1, §11.5's run rules, §11.6, §11.7, §9.7; area A5): one function,
`run_sync`, behind a scheduled turn, a light turn inside a long run, Compare, Sync now, Import
selected and Import all, and the CLI's `sync`, `sync --dry-run` and `sync --catch-up`.

**One run, step by step** (§11.1):
0. A rebuilt `state.db` (§11.6, `rebuild_check`), looked for before anything is listed, under
   the run's own hold: found, it is recorded (the gate opens, and what was made before it waits
   for review); a check that raises ends the run before it lists ("error: `OSError`").
1. The gate: a sync or an import waits for the first catch-up (a request is refused before it gets
   here, a scheduled turn too); the catch-up runs only while it is closed. A person's sync or import
   is refused while a hold is on ("Held: see Status", §11.4 step 3), and while a rate limit's wait
   runs ("rate limited until 14:40", §10.2): either would only meet the same answer again.
2. The consent list, read afresh, failing closed. During a consent stop the listing, privacy from
   the listing and the compare still run, but nothing is fetched or imported, no title of a
   recording not here is stored (the compare's own rule), and the listing never becomes the last
   good one (a guess, failing closed: a listing the consent list couldn't be checked against is
   never a baseline).
3. Late consent listings, in the CLI's syncs (the worker's tick applies them, and again between
   two recordings of a long run).
4. The sign-in (`tokens.check_sign_in`): refreshed if due; a refusal is recorded (the pause and
   the `sign-in` blocker) and ends the run, "sign-in needed".
5. The listing (`PlaudSource.list_all`). A partial one feeds privacy only; a bad or partial one
   ends the run with its error's phrase, recording a refused sign-in (`signin.record_refused`) or
   a rate limit's wait (`health.record_rate_limit`).
6. Privacy from the listing alone (§13.1 cases 2 and 3), for a complete or partial listing, in
   every run but a Compare: §11.1's Compare "stops after classify" and writes nothing to the
   archive, so neither this nor step 3 runs in one (the dry run is a Compare).
7. The compare (`compare.take_listing`): the buckets, the cache, the re-key match, and for a sync
   the hold's verdict (the last good listing, a hold, a small shrink). A Compare stops here.
8. The hold: a held listing imports nothing ("held: Plaud's IDs changed", "held: many gone from
   Plaud").
9. Imports (§11.5), through `importer.Importer`, each checked again before its first byte. A
   scheduled run (and a light turn) imports at most SCHEDULED_CAP, inside the throttle's 10-minute
   budget, and skips a recording whose failure backoff hasn't ended; a person's run has no cap, no
   budget and no backoff. Newest first, as Plaud lists them. Between two recordings `between` runs
   (the worker's privacy steps, its progress, SIGTERM). One recording's failure is counted and the
   run goes on; `ImportStopped` (a consent stop, the disk, a hold, the archive) stops the imports;
   `ListingStale` lists again; a run-ending Plaud error ends the run.
10. The record: `SyncReport`, whose `outcome()` is what `conclude_turn` judges (§11.7), and whose
    phrase, counts and exit code the request replies and the CLI print.

**The sweep** (§14.2, area B1) joins step 9's end, in a sync only (Sync now, a scheduled run;
never a light turn, a Compare, an import or the catch-up): the recordings a run checks
(`plaud.sweep.plan`: extras, the cold sweep up to its cap, the companion check), each through
`plaud.editsync.Checker`, the consent list read again before each, between two of them `between`,
and the budget, as for imports. A check is an attempt (a degraded one without success), so a
companion check's success keeps a lone failing recording from stopping the ping (§11.7). Then
**the breaker** (`plaud.breaker.evaluate`, §14.4), and the sweep's cap after a 429 (§10.2).

**What a person's run imports:** waiting recordings, and those missing only because the schedule
or auto-import is off (a person's press is the import). Import all adds those that failed 3 times;
named IDs (Import selected) add those that need review after a rebuild (`review_ok`). Parked,
held, consent-stopped and kept-out ones never.

**A good run** (§11.7): its listing was good (a small shrink counts) and, if it made any attempt,
one succeeded; the disk guard refused nothing. An import is an attempt (`ImportResult.attempt`),
and so is one a run-ending error stopped mid-way (an outage during a fetch is a fetch that failed);
`BudgetSpent` is no attempt (no call was made). A run that found the disk too full to import
before any download (waiting recordings, the disk at its stop margin) counts as refused.

No title is logged, stored by this module or put in a report: Plaud IDs appear as given in the
listing, recordings by archive ID, failures by class name, phrases from §15.6.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from recordings.disk import STOP_FREE_PERCENT, DiskStatus, disk_status
from recordings.locks import LockTimeout
from recordings.plaud import breaker, health, importer, signin, sweep
from recordings.plaud.baseline import KeepOutWordsError
from recordings.plaud.cache import Cache
from recordings.plaud.checks import K_CATCH_UP_DONE, K_HOLD, PlaudDB
from recordings.plaud.client import (
    BudgetSpent,
    PlaudAuthError,
    PlaudClient,
    PlaudError,
    PlaudForbidden,
    PlaudOffline,
    PlaudRateLimited,
    PlaudUnavailable,
)
from recordings.plaud.compare import (
    AUTO_IMPORT_OFF,
    DISK_FULL,
    FAILED,
    MISSING,
    REVIEW,
    SCHEDULE_OFF,
    WAITING,
    Classified,
    Compare,
    IndexUnavailable,
    after_imports,
    here_from_archive,
    take_listing,
)
from recordings.plaud.editsync import UPDATED, Checker, CheckResult, CheckStopped
from recordings.plaud.flags import apply_late_consent
from recordings.plaud.gate import GATE_REFUSAL, detect_rebuild, gate_now
from recordings.plaud.importer import (
    STOPPED_CONSENT,
    STOPPED_DISK,
    STOPPED_HELD,
    ConsentGuard,
    Importer,
    ImportResult,
    ImportStopped,
    ListingStale,
)
from recordings.plaud.keepout import read_consent
from recordings.plaud.listing_privacy import ListingPrivacy, privacy_from_listing
from recordings.plaud.settings import PlaudSettings
from recordings.plaud.when import local_hhmm
from recordings.refs import canonical_plaud_id
from recordings.sources.plaud import Listing, PlaudSource
from recordings.writer import Writer

if TYPE_CHECKING:
    from recordings.plaud.hold import Measure

log = logging.getLogger(__name__)

Mode = Literal["compare", "sync", "import", "catch-up"]
SCHEDULED_CAP = 20  # §11.5: a scheduled run imports at most 20; the CLI and the Sync screen, any
MAX_LISTINGS = 12  # a run lists again when its listing goes stale (§11.5 step 1), at most so often

# The run's phrases (§15.6), beside the importer's STOPPED_* and the hold's
OK = "ok"
SIGN_IN = "sign-in needed"
GATE = "gate closed"
HELD = STOPPED_HELD  # "held: see Status": a person's run refused while a hold is on
EVERY_FETCH_FAILED = "every fetch failed"
ALREADY_DONE = "the first catch-up is already done"
NOT_DONE = "the first catch-up hasn't run: `recordings plaud sync --catch-up` (runbook)"
STALE_AGAIN = "the listing kept going stale: the rest at the next sync"
LATE_CONSENT_FAILED = "late consent listings: not applied ({})"
REBUILT = (
    "the sync's records were rebuilt: what was made before now and isn't here waits for your "
    "review (the Sync screen's Not here yet)"
)
BREAKER_PHRASE = breaker.PHRASE  # "paused: unexpected changes", while the breaker is open

# Exit codes (§9.6), the CLI's and the report's
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2  # the listing was good, but some recordings failed or aren't here
EXIT_UNREACHABLE = 69  # Plaud unreachable or rate limited
EXIT_BUSY = 75
EXIT_SIGN_IN = 77
EXIT_CONFIG = 78  # configuration, or the consent list

# The missing states a person's press imports anyway: the press is the import.
_PERSON_STATES = (SCHEDULE_OFF, AUTO_IMPORT_OFF, DISK_FULL)


def partial_phrase(failed: int) -> str:
    return f"partial: {failed} recording{'s' if failed != 1 else ''} failed"


def rate_limited(until: datetime | None, timezone_name: str) -> str:
    """§15.6's "rate limited until 14:40", in the default zone."""
    if until is None:
        return "rate limited"
    return f"rate limited until {local_hhmm(until, ZoneInfo(timezone_name))}"


@dataclass
class Run:
    """What one run needs. `between` runs before each recording after the first (the worker's
    privacy steps and progress; it raises `worker.Stopping` on SIGTERM); `progress(done, total)`
    reports a request's progress; `newest(listing)` keeps a complete listing (Release reads it);
    `late_consent` applies late consent listings at the run's start (the CLI: the worker's tick
    does it itself). `disk` and `consent_settle` are the tests' (a disk to fill, a consent file
    trusted at once)."""

    writer: Writer
    client: PlaudClient
    settings: PlaudSettings
    timezone_name: str
    stop_free_percent: float = STOP_FREE_PERCENT
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    between: Callable[[], None] | None = None
    progress: Callable[[int, int], None] | None = None
    newest: Callable[[Listing], None] | None = None
    late_consent: bool = False
    disk: Callable[[], DiskStatus] | None = None
    consent_settle: float | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def db(self) -> PlaudDB:
        return PlaudDB(self.writer.state)

    def disk_now(self) -> DiskStatus:
        if self.disk is not None:
            return self.disk()
        return disk_status(
            self.writer.root,
            expected_uuid=self.writer.identity.uuid,
            stop=self.stop_free_percent,
            warn=max(self.stop_free_percent, 20.0),
        )

    def importer(self, *, catch_up: bool) -> Importer:
        settle = importer.SETTLE_SECONDS if self.consent_settle is None else self.consent_settle
        return Importer(
            writer=self.writer,
            db=self.db,
            client=self.client,
            consent=ConsentGuard(self.settings.consent_list, settle=settle),
            timezone_name=self.timezone_name,
            run_id=self.run_id,
            schedule_minutes=self.settings.schedule_minutes,
            stop_free_percent=self.stop_free_percent,
            catch_up=catch_up,
            clock=self.clock,
            disk=self.disk,
        )


@dataclass
class SyncReport:
    """How a run ended: counts, IDs and fixed phrases, never a title. `listing` is health's
    Listing ("good", "small-shrink", "held", "partial", "bad", "none")."""

    mode: Mode
    run_id: str
    started: datetime
    listing: health.Listing = "none"
    phrase: str = OK
    exit_code: int = EXIT_OK
    compare: Compare | None = None
    measured: Measure | None = None
    privacy: ListingPrivacy | None = None
    results: list[ImportResult] = field(default_factory=list)
    attempts: int = 0
    successes: int = 0
    disk_refused: bool = False
    candidates: int = 0  # recordings this run would import
    left: int = 0  # of those, not tried: the cap, a backoff, the budget, a stop
    stopped: str | None = None  # why imports stopped, a §15.6 phrase
    ended_by: str | None = None  # the run-ending error's class
    notes: list[str] = field(default_factory=list)
    kept_lines: tuple[int, ...] = ()  # the consent list's lines read as kept, by number (§12.1)
    catch_up_done: bool = False
    checks: list[CheckResult] = field(default_factory=list)  # the sweep's (§14.1)
    checks_left: int = 0  # planned and not checked: the budget, a stop
    breaker_open: bool = False  # the breaker is open at the run's end (§14.4)
    breaker_tripped: bool = False  # and this run tripped it
    plan: sweep.Plan | None = None  # the sweep's plan, when the run swept

    # ---- counts ------------------------------------------------------------------------------
    def _count(self, *outcomes: str) -> int:
        return sum(1 for r in self.results if r.outcome in outcomes)

    @property
    def imported(self) -> int:
        return self._count("imported", "merged")

    @property
    def private(self) -> int:
        return sum(1 for r in self.results if r.success and r.private)

    @property
    def failed(self) -> int:
        return self._count("failed")

    @property
    def busy(self) -> int:
        return self._count("busy")

    @property
    def excluded(self) -> int:
        return self._count("excluded")

    @property
    def recording_ids(self) -> list[str]:
        return [r.recording_id for r in self.results if r.success and r.recording_id]

    @property
    def not_here(self) -> int:
        """What this run would have imported and didn't: failed, busy or not tried."""
        return self.failed + self.busy + self.left

    def outcome(self) -> health.RunOutcome:
        """What conclude_turn judges (§11.7)."""
        return health.RunOutcome(
            listing=self.listing,
            attempts=self.attempts,
            successes=self.successes,
            disk_refused=self.disk_refused,
            detail=self.phrase,
        )

    def to_dict(self) -> dict[str, Any]:
        """For `--json` and a request's result: counts, IDs and phrases (§9.6), never a title."""
        out: dict[str, Any] = {
            "mode": self.mode,
            "run": self.run_id,
            "phrase": self.phrase,
            "listing": self.listing,
            "imported": self.imported,
            "private": self.private,
            "failed": self.failed,
            "busy": self.busy,
            "excluded": self.excluded,
            "left": self.left,
            "attempts": self.attempts,
            "successes": self.successes,
            "recording_ids": self.recording_ids,
            "failed_ids": [r.plaud_id for r in self.results if r.outcome == "failed"],
        }
        if self.compare is not None:
            out["equation"] = dict(self.compare.equation)
            out["archive"] = dict(self.compare.archive)
            out["consent"] = dict(self.compare.consent)
            out["titles_hidden"] = self.compare.titles_hidden
        if self.measured is not None and self.measured.held:
            out["hold"] = self.measured.sentence()
        if self.stopped is not None:
            out["stopped"] = self.stopped
        if self.notes:
            out["notes"] = list(self.notes)
        if self.mode == "catch-up":
            out["catch_up_done"] = self.catch_up_done
        if self.checks or self.checks_left:
            out["checks"] = self.check_counts()
            out["updated_ids"] = [c.recording_id for c in self.checks if c.result == UPDATED]
        if self.breaker_open:
            out["breaker"] = BREAKER_PHRASE
        return out

    def check_counts(self) -> dict[str, int]:
        """The sweep's results by §15.6's phrase ("up to date", "updated", …), with what it left."""
        counts: dict[str, int] = {}
        for c in self.checks:
            counts[c.result] = counts.get(c.result, 0) + 1
        if self.checks_left:
            counts["left"] = self.checks_left
        return counts

    def end(self, phrase: str, code: int) -> SyncReport:
        self.phrase, self.exit_code = phrase, code
        return self


def _ended(exc: BaseException, timezone_name: str) -> tuple[str, int]:
    """A run-ending error's §15.6 phrase and exit code."""
    if isinstance(exc, PlaudAuthError):
        return SIGN_IN, EXIT_SIGN_IN
    if isinstance(exc, PlaudRateLimited):
        return rate_limited(exc.until, timezone_name), EXIT_UNREACHABLE
    if isinstance(exc, (PlaudUnavailable, PlaudOffline)):
        return exc.message, EXIT_UNREACHABLE
    if isinstance(exc, PlaudForbidden):
        return exc.message, EXIT_FAILED
    if isinstance(exc, PlaudError):
        return exc.message, EXIT_FAILED
    return f"error: `{type(exc).__name__}`", EXIT_FAILED


def _record_error(run: Run, exc: BaseException, *, now: datetime) -> None:
    """What a run-ending error leaves for the next run (§10.2, §10.3): a refused sign-in's pause and
    blocker, a rate limit's wait."""
    db = run.db
    if isinstance(exc, PlaudAuthError):
        store = _store(run.client)
        if store is not None:
            signin.record_refused(db, store.path, now=now)
    elif isinstance(exc, PlaudRateLimited) and exc.until is not None:
        health.record_rate_limit(db, exc.until, now=now)


def _store(client: PlaudClient):
    """The client's TokenStore, when it has one (the tests' clients hold static tokens)."""
    from recordings.plaud.tokens import TokenStore

    return client.tokens if isinstance(client.tokens, TokenStore) else None


def _check_sign_in(run: Run, report: SyncReport, *, now: datetime) -> bool:
    """§11.1's "check the sign-in, refresh if due". False when it ended the run."""
    from recordings.plaud.tokens import TokenStoreUnsafe, check_sign_in

    store = _store(run.client)
    if store is None:
        return True
    try:
        check_sign_in(store, run.client, run.db, now=now)
    except PlaudAuthError:  # recorded by check_sign_in: the pause and the blocker
        report.end(SIGN_IN, EXIT_SIGN_IN)
        return False
    except TokenStoreUnsafe:
        report.end(
            "configuration: the Plaud sign-in's folder or file is open to others", EXIT_CONFIG
        )
        return False
    except LockTimeout:
        report.end("skipped: busy (`tokens.lock`)", EXIT_BUSY)
        return False
    except PlaudError as exc:  # the token endpoint's outage: nothing about the sign-in
        report.ended_by = type(exc).__name__
        report.end(*_ended(exc, run.timezone_name))
        _record_error(run, exc, now=now)
        return False
    return True


def rebuild_check(writer: Writer, *, now: datetime) -> tuple[bool, str | None]:
    """§11.6's rebuilt `state.db` (`gate.detect_rebuild`), looked for first by every run that
    lists, under that run's own hold: `run_sync` (the worker's turns and requests, the CLI's
    syncs, Sync everything), `resync`, and the CLI's `sync` and `--full` before their gate checks
    (the end-of-branch privacy review, 2026-11-02). So no listing on a rebuilt `state.db` writes
    `plaud_listing` rows, or imports, before `rebuilt_at` is recorded and the review marks apply.
    Returns (found, None), or (False, the exception's class) when the check raised: the run then
    lists nothing (fail closed)."""
    try:
        found = detect_rebuild(PlaudDB(writer.state), writer.archive, now=now)
    except Exception as exc:  # noqa: BLE001  its class only: the run ends before it lists
        cause = type(exc).__name__
        log.warning("the check for a rebuilt state.db failed (%s): nothing listed", cause)
        return False, cause
    if found:
        log.warning(
            "the sync's records were rebuilt: what was made before now and isn't here waits for "
            "review"
        )
    return found, None


def _importable(
    rows: Collection[Classified],
    *,
    person: bool,
    import_all: bool,
    named: frozenset[str],
    listing_rows: dict,
    now: datetime,
) -> list[Classified]:
    """What this run imports, in listing order (newest first): the module's notes."""
    picked = []
    for row in rows:
        pid = row.canonical
        if row.bucket == WAITING:
            ok = True
        elif row.bucket == MISSING and person:
            if pid in named:
                ok = row.state in (REVIEW, FAILED, *_PERSON_STATES)
            elif import_all:
                ok = row.state in (FAILED, *_PERSON_STATES)
            else:
                ok = row.state in _PERSON_STATES
        else:
            ok = False
        if not ok or (named and not import_all and pid not in named):
            continue
        before = listing_rows.get(pid)
        backing_off = before is not None and before.next_import_at is not None
        if not person and backing_off and before.next_import_at > now:
            continue  # §11.5: a failed import's next try waits for its backoff
        picked.append(row)
    return picked


def run_sync(
    run: Run,
    *,
    mode: Mode,
    scheduled: bool = False,
    named: Collection[str] = (),
    import_all: bool = False,
    sweep_checks: bool = True,
) -> SyncReport:
    """One run (the module's notes). `scheduled`: a scheduled or light turn (the cap, the backoff,
    the listing's own settings); otherwise a person's run (Sync now, the Sync screen's imports, the
    CLI). `named` and `import_all` are an import request's (Import selected, Import all).
    `sweep_checks` False: a light turn, which checks nothing (§9.2).
    Raises only `worker.Stopping` (SIGTERM between recordings) and errors that aren't Plaud's or
    the archive's (bugs): the worker records those as a failed turn."""
    from recordings.worker import Stopping  # the worker's SIGTERM, raised through `between`

    started = run.clock()
    report = SyncReport(mode=mode, run_id=run.run_id, started=started)
    db = run.db
    named_ids = frozenset(canonical_plaud_id(p) for p in named)
    person = not scheduled

    # 0. a rebuilt state.db, found before anything is listed (§11.6): it opens the gate
    found, failed = rebuild_check(run.writer, now=started)
    if failed is not None:
        report.ended_by = failed
        return report.end(f"error: `{failed}`", EXIT_FAILED)
    if found:
        report.notes.append(REBUILT)

    # 1. the gate, a hold, a rate limit's wait
    gate_open = gate_now(db).open
    if mode == "catch-up" and gate_open:
        return report.end(ALREADY_DONE, EXIT_FAILED)
    if mode in ("sync", "import") and not gate_open:
        return report.end(GATE_REFUSAL if person else GATE, EXIT_FAILED)
    if person and mode in ("sync", "import") and db.get(K_HOLD) is not None:
        return report.end(HELD, EXIT_FAILED)
    if person and (until := health.rate_limit_wait(db, now=started)) is not None:
        return report.end(rate_limited(until, run.timezone_name), EXIT_UNREACHABLE)

    # 2. the consent list, failing closed; 3. late consent listings (the CLI's syncs: a Compare
    # writes nothing to the archive, §11.1)
    consent = read_consent(run.settings.consent_list)
    stop = consent.stop is not None
    report.kept_lines = tuple(consent.review_lines)
    if run.late_consent and mode != "compare":
        try:
            apply_late_consent(run.writer, db, consent, now=started)
        except Exception as exc:  # noqa: BLE001  noted; the import checks the list again anyway
            report.notes.append(LATE_CONSENT_FAILED.format(type(exc).__name__))

    # 4. the sign-in
    if not _check_sign_in(run, report, now=started):
        return report

    done: set[str] = set()
    limits_before = run.client.throttle.rate_limits
    importer_ = run.importer(catch_up=mode == "catch-up")
    # Import all, and the catch-up (which retries what an interrupted one left failing), take
    # recordings that failed 3 times too; Sync now and a scheduled run don't.
    every = import_all if mode == "import" else mode == "catch-up"
    for _ in range(MAX_LISTINGS):
        relist = _one_listing(
            run,
            report,
            mode=mode,
            scheduled=scheduled,
            consent=consent,
            gate_open=gate_open or mode == "catch-up",
            named=named_ids,
            import_all=every,
            importer_=importer_,
            done=done,
            stopping=Stopping,
            sweep_checks=sweep_checks and mode == "sync",
        )
        if not relist:
            break
    else:
        report.notes.append(STALE_AGAIN)
        report.end(STALE_AGAIN, EXIT_PARTIAL)
    if mode == "catch-up" and _clean(report, stop=stop):
        db.put(K_CATCH_UP_DONE, run.clock().isoformat(), now=run.clock())
        report.catch_up_done = True
    if report.plan is not None:
        limited = run.client.throttle.rate_limits > limits_before
        _record_cap(run, report, rate_limited=limited)
    return report


def _clean(report: SyncReport, *, stop: bool) -> bool:
    """§11.6: the catch-up ends without a run-level failure. One recording's failure isn't one."""
    return (
        not stop
        and report.listing in ("good", "small-shrink")
        and report.stopped is None
        and report.ended_by is None
        and STALE_AGAIN not in report.notes
    )


def _one_listing(
    run: Run,
    report: SyncReport,
    *,
    mode: Mode,
    scheduled: bool,
    consent,
    gate_open: bool,
    named: frozenset[str],
    import_all: bool,
    importer_: Importer,
    done: set[str],
    stopping: type[BaseException],
    sweep_checks: bool = False,
) -> bool:
    """Steps 5 to 9 for one listing, then the sweep and the breaker (a sync's). True when the
    run must list again (`ListingStale`)."""
    db, now = run.db, run.clock()
    stop = consent.stop is not None
    person = not scheduled

    # 5. the listing
    listing = PlaudSource(run.client, clock=run.clock).list_all()
    try:
        here = here_from_archive(
            run.writer.index, run.writer.identity.uuid, run.writer.state, run.writer.archive, db
        )
    except IndexUnavailable:
        report.ended_by = IndexUnavailable.__name__
        report.end(f"error: `{IndexUnavailable.__name__}`", EXIT_FAILED)
        return False

    # 6. privacy from the listing alone, complete or partial; a Compare stops after classifying and
    # writes nothing to the archive (§11.1), so it leaves this to the next sync
    if listing.rows and mode != "compare":
        try:
            report.privacy = privacy_from_listing(run.writer, db, listing.rows, here, now=now)
        except KeepOutWordsError:
            report.ended_by = KeepOutWordsError.__name__
            report.end("error: `KeepOutWordsError`", EXIT_FAILED)
            return False
    if not listing.complete:
        report.listing = "partial" if listing.status == "partial" else "bad"
        if listing.error is not None:
            report.ended_by = type(listing.error).__name__
            report.end(*_ended(listing.error, run.timezone_name))
            _record_error(run, listing.error, now=now)
        else:
            report.end(f"bad listing: {listing.why or 'unreadable'}", EXIT_FAILED)
        return False

    # 7. the compare, and a sync's verdict on the listing
    try:
        words = run.writer.keep_out_words()
    except KeepOutWordsError:
        report.ended_by = KeepOutWordsError.__name__
        report.end("error: `KeepOutWordsError`", EXIT_FAILED)
        return False
    imports = mode != "compare" and not stop
    disk = run.disk_now() if imports else None
    disk_full = disk is not None and disk.level == "stop"
    result, measured, outcome = take_listing(
        db,
        Cache(run.writer.state.path),
        listing,
        here=here,
        consent=consent,
        words=words,
        patterns=run.writer.auto_private,
        gate_open=gate_open,
        schedule_on=run.settings.schedule_minutes > 0,
        auto_import=run.settings.auto_import,
        disk_full=disk_full,
        sync=mode in ("sync", "catch-up") and not stop,
        now=now,
    )
    report.compare, report.measured, report.listing = result, measured, outcome
    if run.newest is not None:
        run.newest(listing)

    # 8. the hold, and a consent stop (a Compare says them too: the dry run's exit code)
    if outcome == "held":
        report.end(measured.run_phrase() or HELD, EXIT_FAILED)
        return False
    if stop:
        report.stopped = STOPPED_CONSENT if mode != "compare" else None
        report.end(STOPPED_CONSENT, EXIT_CONFIG)
        return False
    if mode == "compare":
        report.end(measured.run_phrase() or OK, EXIT_OK)
        return False

    # 9. imports
    picked = [
        row
        for row in _importable(
            result.rows,
            person=person,
            import_all=import_all,
            named=named,
            listing_rows=db.listing_rows(),
            now=now,
        )
        if row.canonical not in done
    ]
    report.candidates = max(report.candidates, len(done) + len(picked))
    refused = [r for r in result.rows if r.state == DISK_FULL and r.canonical not in done]
    if disk_full and refused:
        # The guard refuses before any download (§11.5 step 5): the compare already says "the disk
        # is too full to import" for each, and the run isn't good (§11.7).
        report.disk_refused = True
        report.stopped = STOPPED_DISK
        report.left += len(refused)
        report.end(STOPPED_DISK, EXIT_PARTIAL)
        return False
    cap = SCHEDULED_CAP if scheduled else None
    relist = _import_rows(
        run,
        report,
        picked,
        listing=listing,
        importer_=importer_,
        named=named,
        cap=cap,
        done=done,
        stopping=stopping,
    )
    if relist:
        return True
    if any(r.canonical in done for r in result.rows):
        _settle(run, report, result, here=here, consent=consent)
    if sweep_checks and report.ended_by is None and report.stopped is None:
        _sweep(run, report, listing=listing, here=here, importer_=importer_, stopping=stopping)
    if sweep_checks:
        _breaker(run, report)
    _final_phrase(report, measured)
    return False


def _settle(run: Run, report: SyncReport, result: Compare, *, here, consent) -> None:
    """The stored compare after the imports (`compare.after_imports`), so the screens show what
    the run left rather than what it found (area A6). The report keeps the compare it ran on (its
    replies count from it). A state.db or cache that can't take it now leaves the compare as it
    was, to be put right by the next listing: the run's outcome doesn't change."""
    try:
        after_imports(
            run.db,
            Cache(run.writer.state.path),
            result,
            report.results,
            here=here,
            consent=consent,
            now=run.clock(),
        )
    except (sqlite3.Error, OSError) as exc:
        log.warning("the compare after the imports wasn't stored (%s)", type(exc).__name__)


def _sweep(
    run: Run,
    report: SyncReport,
    *,
    listing: Listing,
    here,
    importer_: Importer,
    stopping: type[BaseException],
) -> None:
    """§11.1's "a chunk of the sweep" (§14.2): each planned recording checked in turn
    (`editsync.Checker`), as imports are tried: `between` before each but the first, the budget,
    and a stop. A check is an attempt unless it was skipped or busy; a degraded one isn't a
    success (§11.7). One recording's failure is counted and the sweep goes on."""
    from recordings.plaud.baseline import KeepOutWordsError

    db, now = run.db, run.clock()
    try:
        consent = importer_.consent.current()
        plan = sweep.plan(
            db,
            here,
            consent_ids=consent.ids,
            now=now,
            marked=report.compare.marked if report.compare is not None else (),
            schedule_minutes=run.settings.schedule_minutes,
            full_sweep_days=run.settings.full_sweep_days,
            writer=run.writer,
        )
    except ImportStopped as exc:
        report.stopped = exc.phrase
        return
    report.plan = plan
    checker = Checker(
        writer=run.writer,
        db=db,
        client=run.client,
        consent=importer_.consent,
        timezone_name=run.timezone_name,
        run_id=run.run_id,
        schedule_minutes=run.settings.schedule_minutes,
        full_sweep_days=run.settings.full_sweep_days,
        breaker_open=breaker.is_open(db),
        clock=run.clock,
    )
    rows = {row.canonical_id: row for row in listing.rows}
    budget = run.client.throttle
    todo = plan.recordings
    for n, rid in enumerate(todo):
        if n and run.between is not None:
            run.between()  # may raise worker.Stopping, between two recordings (§9.2)
        if budget.spent():
            report.checks_left += len(todo) - n
            break
        try:
            result = checker.check(rid, rows)
        except ImportStopped as exc:  # the consent list, read again before each check
            report.stopped = exc.phrase
            report.checks_left += len(todo) - n
            break
        except CheckStopped as exc:
            report.stopped = f"error: `{exc.cause}`"
            report.ended_by = exc.cause
            report.checks_left += len(todo) - n
            break
        except KeepOutWordsError:
            report.ended_by = KeepOutWordsError.__name__
            report.end("error: `KeepOutWordsError`", EXIT_FAILED)
            report.checks_left += len(todo) - n
            break
        except BudgetSpent:
            report.checks_left += len(todo) - n
            break
        except PlaudError as exc:  # run-ending (the checker raises no other): an attempt failed
            report.attempts += 1
            report.ended_by = type(exc).__name__
            report.checks_left += len(todo) - n - 1
            _record_error(run, exc, now=run.clock())
            report.end(*_ended(exc, run.timezone_name))
            break
        except stopping:
            raise
        report.checks.append(result)
        report.attempts += int(result.attempt)
        report.successes += int(result.success)


def _breaker(run: Run, report: SyncReport) -> None:
    """§11.1's "the breaker": tripped now if the window says so (§14.4). Its state at the run's
    end goes in the report; a state.db that can't take it now is retried at the next run."""
    db = run.db
    try:
        report.breaker_tripped = breaker.evaluate(db, now=run.clock())
        report.breaker_open = breaker.is_open(db)
    except (sqlite3.Error, OSError) as exc:
        log.warning("the breaker wasn't evaluated (%s)", type(exc).__name__)


def _record_cap(run: Run, report: SyncReport, *, rate_limited: bool) -> None:
    """§10.2: the sweep's cap halves after a 429, and grows back by one per good run."""
    if report.plan is None:
        return
    rate_limited = rate_limited or report.ended_by == PlaudRateLimited.__name__
    try:
        sweep.record_cap(
            run.db,
            report.plan,
            rate_limited=rate_limited,
            good=health.judge_run(report.outcome()),
            now=run.clock(),
        )
    except (sqlite3.Error, OSError) as exc:
        log.warning("the sweep's cap wasn't recorded (%s)", type(exc).__name__)


def _import_rows(
    run: Run,
    report: SyncReport,
    rows: list[Classified],
    *,
    listing: Listing,
    importer_: Importer,
    named: frozenset[str],
    cap: int | None,
    done: set[str],
    stopping: type[BaseException],
) -> bool:
    """Step 9's loop. True when the listing went stale and the run must list again."""
    budget = run.client.throttle
    total = report.candidates
    for n, row in enumerate(rows):
        pid = row.canonical
        if cap is not None and len(done) >= cap:
            report.left += len(rows) - n
            break
        if n and run.between is not None:
            run.between()  # may raise worker.Stopping, between two recordings (§9.2)
        if budget.spent():
            report.left += len(rows) - n
            break
        if run.progress is not None:
            run.progress(len(done), total)
        try:
            result = importer_.import_one(row.listed, listing_at=listing.at, review_ok=pid in named)
        except ImportStopped as exc:
            report.stopped = exc.phrase
            report.disk_refused = report.disk_refused or exc.phrase == STOPPED_DISK
            report.left += len(rows) - n
            if exc.cause is not None:
                report.ended_by = exc.cause
            break
        except ListingStale:
            return True
        except BudgetSpent:
            report.left += len(rows) - n
            break
        except PlaudError as exc:  # run-ending (the importer raises no other): an attempt failed
            report.attempts += 1
            report.ended_by = type(exc).__name__
            report.left += len(rows) - n - 1
            _record_error(run, exc, now=run.clock())
            report.end(*_ended(exc, run.timezone_name))
            done.add(pid)
            return False
        except stopping:
            raise
        done.add(pid)
        report.results.append(result)
        report.attempts += int(result.attempt)
        report.successes += int(result.success)
    if run.progress is not None:
        run.progress(len(done), total)
    return False


def _final_phrase(report: SyncReport, measured: Measure | None) -> None:
    """The run's phrase and exit code once its imports are done, unless an error already set
    them: imports stopped, then failures, then a small shrink, then "ok"."""
    if report.ended_by is not None and report.stopped is None:
        return  # a run-ending error's phrase stands
    if report.stopped is not None:
        code = EXIT_PARTIAL if report.stopped == STOPPED_DISK else EXIT_FAILED
        if report.stopped == STOPPED_CONSENT:
            code = EXIT_CONFIG
        report.end(report.stopped, code)
        return
    if report.failed and not report.successes:
        report.end(EVERY_FETCH_FAILED, EXIT_PARTIAL)
        return
    if report.checks and report.attempts and not report.successes:
        report.end(EVERY_FETCH_FAILED, EXIT_PARTIAL)  # every check failed or was degraded (§11.7)
        return
    if report.failed:
        report.end(partial_phrase(report.failed), EXIT_PARTIAL)
        return
    shrink = measured.run_phrase() if measured is not None else None
    if shrink is None and report.breaker_open:
        shrink = BREAKER_PHRASE  # §15.6: the run's phrase while the breaker is open
    report.end(shrink or OK, EXIT_PARTIAL if report.not_here else EXIT_OK)


# ---- the worker's turn (§9.7) and its requests' runs ---------------------------------------
def worker_run(worker: Any, **overrides: Any) -> Run:
    """A Run from the worker: its Writer, its one client (one Throttle, whose waits bump the
    heartbeat and stop on SIGTERM), its settings, and its privacy steps between recordings."""
    from recordings.disk import disk_thresholds

    def newest(listing: Listing) -> None:
        worker.newest_listing = listing

    fields: dict[str, Any] = {
        "writer": worker.writer,
        "client": worker.plaud_client(),
        "settings": worker.settings,
        "timezone_name": worker.cfg.default_timezone,
        "stop_free_percent": disk_thresholds(worker.cfg)["stop"],
        "clock": worker.clock,
        "between": worker.between_recordings,
        "newest": newest,
        "late_consent": False,
    }
    return Run(**(fields | overrides))  # a long run's `between` replaces the plain one


def worker_turn(worker: Any, *, light: bool) -> health.RunOutcome:
    """A scheduled turn (§9.7 item 4), or the light turn of a due one inside a long run (§9.2):
    the run with the scheduled cap, inside the throttle's 10-minute budget, and a chunk of the
    sweep (area B1). The light turn checks nothing: the listing, privacy from it and up to 20
    imports (§9.2).
    Its §15.6 phrase goes to `ops_runs` (job `plaud-sync`) and the log, counts only."""
    run = worker_run(worker)
    with run.client.throttle.budget():
        report = run_sync(run, mode="sync", scheduled=True, sweep_checks=not light)
    record_ops_run(worker.state, "plaud-sync", report, finished=worker.clock())
    log.info("%s%s", "light turn: " if light else "sync: ", summary(report))
    return report.outcome()


def record_ops_run(state, job: str, report: SyncReport, *, finished: datetime) -> None:
    """The run in `ops_runs` (§9.5's reuse): its job, times, whether it was good, its phrase."""
    try:
        good = health.judge_run(report.outcome())
        state.record_run(job, report.started, finished, good, report.phrase)
    except (sqlite3.Error, OSError) as exc:
        log.warning("the run wasn't recorded in ops_runs (%s)", type(exc).__name__)


def summary(report: SyncReport) -> str:
    """One log line: the phrase and the counts, never a title."""
    parts = [report.phrase]
    if report.compare is not None:
        e = report.compare.equation
        parts.append(
            f"listed {e['listed']} = here {e['here']} + waiting {e['waiting']} + excluded "
            f"{e['excluded']} + missing {e['missing']}"
        )
    if report.results or report.left:
        parts.append(
            f"{report.imported} imported ({report.private} private), {report.failed} failed, "
            f"{report.busy} busy, {report.left} left"
        )
    return " · ".join(parts)


# ---- the request replies (§15.1, §11.5) ----------------------------------------------------
def synced_line(report: SyncReport) -> str:
    """Sync now's reply (§15.1): "Synced: 2 imported (1 private) · All here", "Synced: nothing
    new · All here", "Synced: 1 failed · See Status", or the run's own phrase when it stopped."""
    if report.listing not in ("good", "small-shrink") or report.stopped is not None:
        return _stopped_line(report)
    if report.ended_by is not None:
        return _stopped_line(report)
    head = (
        f"{report.imported} imported ({report.private} private)"
        if report.imported
        else "nothing new"
    )
    if report.failed:
        return f"Synced: {head} · {report.failed} failed · See Status"
    left = _still_not_here(report)
    tail = "All here" if not left else f"{left} not here yet · See Status"
    return f"Synced: {head} · {tail}"


def imported_line(report: SyncReport) -> str:
    """An import's end (§11.5): "31 imported · 4 arrived private", with what failed."""
    if report.listing not in ("good", "small-shrink") or report.ended_by is not None:
        return _stopped_line(report)
    parts = [f"{report.imported} imported"]
    if report.private:
        parts.append(f"{report.private} arrived private")
    if report.failed:
        parts.append(f"{report.failed} failed · See Status")
    if report.stopped is not None:
        parts.append(_cap(report.stopped))
    return " · ".join(parts)


def compared_line(report: SyncReport) -> str:
    """Compare's reply: the equation, or why there is none."""
    if report.compare is None:
        return _stopped_line(report)
    e = report.compare.equation
    return (
        f"Compared: listed {e['listed']:,} = here {e['here']:,} + waiting {e['waiting']:,} + "
        f"excluded {e['excluded']:,} + missing {e['missing']:,}"
    )


def _still_not_here(report: SyncReport) -> int:
    """Waiting and missing after the run, less what it imported (excluded rows never count)."""
    if report.compare is None:
        return 0
    e = report.compare.equation
    return max(0, e["waiting"] + e["missing"] - report.imported - report.excluded)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _stopped_line(report: SyncReport) -> str:
    if report.phrase == HELD or report.listing == "held":
        return "Held: see Status"
    return f"{_cap(report.phrase)} · See Status"


__all__ = [
    "EXIT_BUSY",
    "EXIT_CONFIG",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_SIGN_IN",
    "EXIT_UNREACHABLE",
    "SCHEDULED_CAP",
    "Run",
    "SyncReport",
    "compared_line",
    "imported_line",
    "run_sync",
    "synced_line",
    "worker_run",
    "worker_turn",
]
