"""Re-sync and parking a recording (stage 2b spec §14.6, decision 6; area B2).

**Re-sync** checks the recordings named now, ahead of the sweep: Details' **Re-sync from Plaud**
(one recording), the Library's selection bar (decision 6: one request for the selection, up to
1,000), Status's "fails every check" line, and `recordings plaud resync <id>…`. Each recording is
checked as the sweep checks it (`editsync.Checker`, B1's), with one difference: a parked recording
is tried too (`parked_ok`), and a success unparks it.

Before any Plaud call, like a person's sync (`plaud.sync`, §11.1): a rebuilt `state.db` looked
for first (`sync.rebuild_check`, §11.6; a check that raises ends it), the gate (a Re-sync writes
snapshots, so it waits for the first catch-up, §9.3), a hold ("Held: see Status": the newest
listing is suspect while one is on, so nothing is checked against it; a guess, failing closed),
a rate limit's wait, the consent list read afresh (a stop checks nothing), and the sign-in. Then
the newest complete listing (the worker's, while it is one schedule interval fresh, else a new
one), for the form each Plaud ID is listed in (§14.1 step 1). A listing that isn't complete ends
the run with its phrase, and is never recorded: a Re-sync moves no listing state.

**Its replies** (§14.6, §15.6): one recording's line ("Up to date", "Updated: speakers", "Not
checked: kept out", "Not checked: on the consent list", "Gone from Plaud", "Edit-sync paused: see
Status", "Failed: Plaud unavailable"), and a selection's ("25 checked: 7 updated (speakers 6,
title 1) · 18 up to date"), with recordings that have no Plaud source skipped and counted. S9, which
would measure how long Plaud takes to show an edit, was skipped, so "Up to date" carries no delay
note (the guesses file).

**Parking** (§14.6): a parked recording (`plaud_checks.parked_at`) isn't checked by the sweep and
doesn't count against "All here", Sync everything or §11.7 (`sweep.covered`, the checker, the
failing list all leave it out); Re-sync still tries it. `park` sets it for recordings here with a
Plaud source; `unpark` clears it and asks for a check at the next run.

No title anywhere: recordings by archive ID, parts by name, failures by fixed phrase or class.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from recordings.plaud import breaker, client, health
from recordings.plaud.checks import K_HOLD, PlaudDB
from recordings.plaud.editsync import (
    BUSY,
    DEGRADED,
    FAILED,
    GONE,
    LOGGED,
    NOT_CONSENT,
    NOT_KEPT_OUT,
    NOT_LISTED,
    PARKED,
    PARTS,
    SUPPRESSED,
    UP_TO_DATE,
    UPDATED,
    Checker,
    CheckResult,
    CheckStopped,
)
from recordings.plaud.gate import GATE_REFUSAL, gate_now
from recordings.plaud.importer import STOPPED_CONSENT, ImportStopped
from recordings.plaud.keepout import read_consent
from recordings.plaud.settings import BAD_WORD
from recordings.sources.plaud import Listing, PlaudSource

log = logging.getLogger(__name__)

RESYNCING = "Re-syncing"  # a running Re-sync's words: the screen adds "(12 of 25)"
NO_PLAUD = "no Plaud source"  # a recording named that didn't come from Plaud, or isn't here
NOTHING_NAMED = "Name the recordings to re-sync"
TOO_MANY = "For more, use Sync everything"  # over requests.MAX_RESYNC_IDS (§14.6)
HELD = "Held: see Status"
OK = "ok"
# A run longer than this many recordings serves the light turn and single-recording requests
# between two of them (§9.2's "a large Re-sync").
LONG = 20

# One recording's reply (§14.6, §15.6), by the check's result
LINES = {
    UP_TO_DATE: "Up to date",
    SUPPRESSED: "Edit-sync paused: see Status",
    NOT_KEPT_OUT: "Not checked: kept out",
    NOT_CONSENT: "Not checked: on the consent list",
    GONE: "Gone from Plaud",
    NOT_LISTED: "Not checked: Plaud doesn't list it now",
    BUSY: "Busy: it is checked again at the next sync",
    DEGRADED: "Degraded: Plaud's links failed, so it is checked again later",
    PARKED: "Parked",  # never with parked_ok; kept for completeness
    NO_PLAUD: "Not checked: it didn't come from Plaud",
}
# How a selection's end line names each skip
SKIPS = {
    NOT_KEPT_OUT: "kept out",
    NOT_CONSENT: "on the consent list",
    GONE: "gone from Plaud",
    NOT_LISTED: "not listed now",
    BUSY: "busy",
    NO_PLAUD: "no Plaud source",
}

EXIT_OK, EXIT_FAILED, EXIT_PARTIAL, EXIT_UNREACHABLE, EXIT_CONFIG = 0, 1, 2, 69, 78


def failure_phrase(name: str | None) -> str:
    """A failed check's cause as §15.6 says it: a Plaud error's own fixed message ("Plaud
    unavailable"), else its class ("error: `WriterError`"). Never an exception's text."""
    found = getattr(client, name or "", None)
    if isinstance(found, type) and issubclass(found, client.PlaudError):
        return found.message
    return f"error: `{name or 'Exception'}`"


def line_for(result: CheckResult) -> str:
    """One recording's reply (§14.6)."""
    if result.result == UPDATED:
        return f"Updated: {', '.join(result.parts)}" if result.parts else "Updated"
    if result.result == FAILED:
        return f"Failed: {failure_phrase(result.failure)}"
    return LINES.get(result.result, result.result)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


@dataclass
class ResyncReport:
    """How a Re-sync ended: counts, IDs and fixed phrases, never a title. `end` and `ended_by`
    are what `sync._check_sign_in` writes, as on a SyncReport."""

    run_id: str
    requested: tuple[str, ...]
    results: list[CheckResult] = field(default_factory=list)
    no_plaud: list[str] = field(default_factory=list)
    unparked: list[str] = field(default_factory=list)
    phrase: str = OK
    exit_code: int = EXIT_OK
    ended_by: str | None = None
    stopped: str | None = None
    left: int = 0  # named, and not reached: a stop

    def end(self, phrase: str, code: int) -> ResyncReport:
        self.phrase, self.exit_code = phrase, code
        return self

    @property
    def attempts(self) -> int:
        return sum(1 for r in self.results if r.attempt)

    @property
    def successes(self) -> int:
        return sum(1 for r in self.results if r.success)

    def by_result(self) -> Counter:
        found = Counter(r.result for r in self.results)
        if self.no_plaud:
            found[NO_PLAUD] += len(self.no_plaud)
        return found

    def parts(self) -> list[tuple[str, int]]:
        """The updated parts, each with how many recordings it changed in: the most first."""
        counted = Counter(p for r in self.results if r.result == UPDATED for p in r.parts)
        return sorted(counted.items(), key=lambda kv: (-kv[1], PARTS.index(kv[0])))

    @property
    def updated_ids(self) -> list[str]:
        return [r.recording_id for r in self.results if r.result == UPDATED]

    def each(self) -> dict[str, str]:
        """Each named recording's own line, for Details when its press joined a selection's."""
        lines = {r.recording_id: line_for(r) for r in self.results}
        lines.update(dict.fromkeys(self.no_plaud, LINES[NO_PLAUD]))
        return lines

    def line(self) -> str:
        """The reply: one recording's own line, or a selection's end line (§14.6); a run that
        stopped says why first ("Failed: Plaud unavailable" for one recording)."""
        if self.phrase == OK:
            if len(self.requested) == 1:
                return line_for(self.results[0]) if self.results else LINES[NO_PLAUD]
            return self._counts()
        if self.phrase in (HELD, GATE_REFUSAL, NOTHING_NAMED):
            return self.phrase
        if len(self.requested) == 1 and not self.results:
            return f"Failed: {self.phrase}"
        head = f"Stopped: {self.phrase} · See Status"
        counts = self._counts() if (self.results or self.no_plaud) else ""
        return f"{head} · {counts}" if counts else head

    def _counts(self) -> str:
        found = self.by_result()
        checked = sum(n for result, n in found.items() if result in LOGGED)
        parts: list[str] = []
        if found[UPDATED]:
            named = ", ".join(f"{p} {n:,}" for p, n in self.parts())
            parts.append(f"{found[UPDATED]:,} updated" + (f" ({named})" if named else ""))
        if found[UP_TO_DATE]:
            parts.append(f"{found[UP_TO_DATE]:,} up to date")
        if found[SUPPRESSED]:
            parts.append(f"{found[SUPPRESSED]:,} paused by edit-sync (see Status)")
        if found[DEGRADED]:
            parts.append(f"{found[DEGRADED]:,} degraded")
        if found[FAILED]:
            parts.append(f"{found[FAILED]:,} failed · See Status")
        skipped = [(SKIPS[k], found[k]) for k in SKIPS if found[k]]
        if skipped:
            total = sum(n for _, n in skipped)
            why = ", ".join(f"{what} {n:,}" for what, n in skipped)
            parts.append(f"{total:,} not checked ({why})")
        if self.left:
            parts.append(f"{self.left:,} not reached")
        if not parts:
            return f"{checked:,} checked"
        return f"{checked:,} checked: " + " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """A request's result and `--json`: counts and IDs, never a title."""
        found = self.by_result()
        return {
            "run": self.run_id,
            "phrase": self.phrase,
            "requested": len(self.requested),
            "checked": sum(n for result, n in found.items() if result in LOGGED),
            "results": dict(sorted(found.items())),
            "parts": dict(self.parts()),
            "updated_ids": self.updated_ids,
            "failed_ids": [r.recording_id for r in self.results if r.result == FAILED],
            "unparked": list(self.unparked),
            "no_plaud": list(self.no_plaud),
            "left": self.left,
            "each": self.each(),
        }

    def outcome(self) -> health.RunOutcome:
        return health.RunOutcome(
            listing="good" if self.ended_by is None else "bad",
            attempts=self.attempts,
            successes=self.successes,
            disk_refused=False,
            detail=self.phrase,
        )


def _plaud_sources(rec) -> bool:
    return any(s.kind == "plaud" for s in rec.sources)


def resync(
    run: Any,  # plaud.sync.Run
    recording_ids: Sequence[str],
    *,
    listing: Listing | None = None,
) -> ResyncReport:
    """Re-sync these recordings (the module's notes). `listing`: a complete listing fresh enough
    to use (the worker's newest), else one is made. Raises only `worker.Stopping` (SIGTERM between
    recordings) and errors that aren't Plaud's or the archive's (bugs)."""
    from recordings.plaud import sync
    from recordings.worker import Stopping

    named = tuple(dict.fromkeys(recording_ids))
    report = ResyncReport(run_id=run.run_id, requested=named)
    db: PlaudDB = run.db
    now = run.clock()
    if not named:
        return report.end(NOTHING_NAMED, EXIT_FAILED)
    if not run.settings.keep_out_word_ok:  # §12.2: an invalid word leaves every run idle
        return report.end(BAD_WORD, EXIT_CONFIG)
    _found, failed = sync.rebuild_check(run.writer, now=now)  # §11.6: before anything is listed
    if failed is not None:
        report.ended_by = failed
        return report.end(f"error: `{failed}`", EXIT_FAILED)
    if not gate_now(db).open:
        return report.end(GATE_REFUSAL, EXIT_FAILED)
    if db.get(K_HOLD) is not None:
        return report.end(HELD, EXIT_FAILED)
    if (until := health.rate_limit_wait(db, now=now)) is not None:
        return report.end(sync.rate_limited(until, run.timezone_name), EXIT_UNREACHABLE)
    if read_consent(run.settings.consent_list).stop is not None:
        return report.end(STOPPED_CONSENT, EXIT_CONFIG)  # nothing is fetched during a stop
    if not sync._check_sign_in(run, report, now=now):
        return report
    if listing is None or not listing.complete:
        listing = PlaudSource(run.client, clock=run.clock).list_all()
        if not listing.complete:
            report.ended_by = type(listing.error).__name__ if listing.error else "Listing"
            if listing.error is not None:
                sync._record_error(run, listing.error, now=now)
                return report.end(*sync._ended(listing.error, run.timezone_name))
            return report.end(f"bad listing: {listing.why or 'unreadable'}", EXIT_FAILED)
        if run.newest is not None:
            run.newest(listing)
    checker = Checker(
        writer=run.writer,
        db=db,
        client=run.client,
        consent=run.importer(catch_up=False).consent,
        timezone_name=run.timezone_name,
        run_id=run.run_id,
        schedule_minutes=run.settings.schedule_minutes,
        full_sweep_days=run.settings.full_sweep_days,
        breaker_open=breaker.is_open(db),
        clock=run.clock,
    )
    rows = {row.canonical_id: row for row in listing.rows}
    _check_each(run, report, checker, rows, named, stopping=Stopping)
    try:
        breaker.evaluate(db, now=run.clock())
    except (sqlite3.Error, OSError) as exc:
        log.warning("the breaker wasn't evaluated (%s)", type(exc).__name__)
    if report.phrase == OK and any(r.result in (FAILED, BUSY, DEGRADED) for r in report.results):
        report.exit_code = EXIT_PARTIAL
    log.info(
        "re-sync: %d named, %d checked, %d updated, %d failed, %d unparked",
        len(named),
        report.attempts,
        len(report.updated_ids),
        sum(1 for r in report.results if r.result == FAILED),
        len(report.unparked),
    )
    return report


def _check_each(
    run: Any,
    report: ResyncReport,
    checker: Checker,
    rows: dict,
    named: tuple[str, ...],
    *,
    stopping: type[BaseException],
) -> None:
    """Each named recording in turn, as the sweep checks (`sync._sweep`): `between` before each
    but the first, a stop ending the rest. A parked one is tried, and a success unparks it."""
    from recordings.plaud import sync
    from recordings.plaud.baseline import KeepOutWordsError

    db: PlaudDB = run.db
    total = len(named)
    for n, rid in enumerate(named):
        if n and run.between is not None:
            run.between()  # may raise worker.Stopping, between two recordings (§9.2)
        if run.progress is not None:
            run.progress(n, total)
        try:
            rec = run.writer.archive.load(rid)
        except (KeyError, OSError, ValueError):
            report.no_plaud.append(rid)  # not here, or can't be read now: nothing to check
            continue
        if not _plaud_sources(rec):
            report.no_plaud.append(rid)
            continue
        row = db.check_row(rid)
        parked = row is not None and row.parked_at is not None
        try:
            result = checker.check(rid, rows, parked_ok=True)
        except ImportStopped as exc:  # the consent list, read again before each check
            report.stopped = exc.phrase
            report.left += total - n
            report.end(exc.phrase, EXIT_CONFIG if exc.phrase == STOPPED_CONSENT else EXIT_FAILED)
            break
        except CheckStopped as exc:
            report.ended_by = exc.cause
            report.left += total - n
            report.end(f"error: `{exc.cause}`", EXIT_FAILED)
            break
        except KeepOutWordsError:
            report.ended_by = KeepOutWordsError.__name__
            report.left += total - n
            report.end("error: `KeepOutWordsError`", EXIT_FAILED)
            break
        except client.PlaudError as exc:  # run-ending: the checker raises no other
            report.ended_by = type(exc).__name__
            report.left += total - n
            sync._record_error(run, exc, now=run.clock())
            report.end(*sync._ended(exc, run.timezone_name))
            break
        except stopping:
            raise
        report.results.append(result)
        if parked and result.success:
            db.set_check(rid, parked_at=None)  # §14.6: a Re-sync's success unparks it
            report.unparked.append(rid)
    if run.progress is not None:
        run.progress(total - report.left, total)


# ---- Parking a recording (§14.6) -------------------------------------------------------------
PARKED_LINE = "Parked: it no longer counts against All here, and syncs leave it alone"
UNPARKED_LINE = "Unparked: it is checked at the next sync"
NOTHING_TO_PARK = "Nothing to park: it isn't a recording from Plaud here, or it is parked already"
NOTHING_TO_UNPARK = "Nothing to unpark"


@dataclass(frozen=True)
class Parking:
    done: tuple[str, ...]
    nothing: tuple[str, ...]


def park_recordings(
    archive: Any,
    db: PlaudDB,
    recording_ids: Iterable[str],
    *,
    park: bool,
    now: datetime,
) -> Parking:
    """Park (or unpark) recordings here that came from Plaud (§14.6): only `plaud_checks`
    changes, never an archive file. A recording that isn't here, has no Plaud source, is gone
    from Plaud, or is already so, is "nothing". Unparked, its next check is now, so the next run
    tries it; its failures stay as they were, so one that still fails is on the failing list
    again at once."""
    done, nothing = [], []
    for rid in dict.fromkeys(recording_ids):
        try:
            rec = archive.load(rid)
        except (KeyError, OSError, ValueError):
            nothing.append(rid)
            continue
        row = db.check_row(rid)
        is_parked = row is not None and row.parked_at is not None
        if not _plaud_sources(rec) or is_parked == park:
            nothing.append(rid)
            continue
        if park and row is not None and row.gone_since is not None:
            nothing.append(rid)  # gone from Plaud: nothing checks it anyway
            continue
        if park:
            db.set_check(rid, parked_at=now)
        else:
            db.set_check(rid, parked_at=None, next_check_at=now)
        done.append(rid)
    if done:
        log.info("%s %d recordings", "parked" if park else "unparked", len(done))
    return Parking(tuple(done), tuple(nothing))


def failing_ids(db: PlaudDB) -> frozenset[str]:
    """The recordings on the failing list now (FAILING_AFTER failed checks in a row)."""
    return frozenset(row.recording_id for row in db.failing_checks())


__all__ = [
    "LINES",
    "LONG",
    "NO_PLAUD",
    "RESYNCING",
    "Parking",
    "ResyncReport",
    "failure_phrase",
    "line_for",
    "park_recordings",
    "resync",
]
