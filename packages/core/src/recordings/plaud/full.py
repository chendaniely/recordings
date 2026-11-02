"""Sync everything (stage 2b spec §14.6, main spec §9.1.1; area B2): `recordings plaud sync
--full`, the Sync screen's button under More (request `full`), and the worker's own run every 30
days.

**One run:**
1. A person's sync (`plaud.sync.run_sync`, mode "sync"): the gate, a hold and a rate limit's wait
   refuse it; the consent list, the sign-in, the listing, privacy from the listing, the compare
   (and its hold verdict), and the imports of what is waiting, with no cap (§11.1, §11.5). A
   listing that isn't good, a consent stop, the disk guard or a run-ending error ends it there,
   failed.
2. **Every recording the sweep covers** (`sweep.covered`: here, not gone, parked, flagged, kept out
   or consent-listed), oldest check first, through the check (`editsync.Checker`, B1's), then its
   failures once more. A resumed run skips what was checked since it started. A run-ending
   Plaud error ends it, failed; SIGTERM leaves it unfinished, to resume.
3. **The verdict:** exit 0 when the listing was good and every listed recording is excluded,
   parked, flagged (here, but consent-listed or kept out late, so never fetched; named by archive
   ID) or here and checked (degraded counts as checked); exit 2 otherwise, with counts. A
   recording that needs review after a rebuild (§11.6) is left out of the imports (C5.26) and
   counts as not here: Dan decides about it first (a guess, the guesses file).
4. **The manifest** (DAN-15): `<state>/manifests/plaud-<date>.json`, each Plaud ID with its
   archive ID, last snapshot, state and part hashes, but never the title or speakers parts, so
   Forget has nothing there to scrub; consent-listed and kept-out IDs not here by ID only. It is
   backed up through `[backup] extra_paths` (decision 9), and the worker makes the folder at its
   start, so a backup never meets it missing.
5. **The record,** `K_FULL`: `running` while unfinished (its start, progress and by whom), and
   `last` once finished (its outcome, line, counts, the flagged IDs, the measured pace, the
   manifest's name). Status's Sync everything line reads `last`: a failure shows there when it is
   run-level, or a recording not already on the failing list failed. It is never a blocker (§16).

**When:** a press (refused while one is queued or running, and within an hour of the last
finished one; "Resuming (started 14:20)" while one is unfinished), the CLI, and the worker's tick:
an unfinished one resumes; otherwise one runs 30 days after the last finished one (or after the
first catch-up, before any has run) while the schedule is on and the sync isn't retired. A failed
one waits for next month or a press, never the ops loop's 15-minute retry.

No title anywhere: Plaud IDs as listed, recordings by archive ID, phrases from §15.6.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from recordings.plaud import breaker, sweep
from recordings.plaud.checks import CONSENT_LATE, K_FULL, KEPT_OUT_LATE, PlaudDB
from recordings.plaud.client import PlaudError
from recordings.plaud.compare import EXCLUDED, HERE, MISSING, PARKED, REVIEW, WAITING, _kept_out
from recordings.plaud.editsync import (
    DEGRADED,
    FAILED,
    SUCCESSES,
    Checker,
    CheckResult,
    CheckStopped,
    snapshots,
)
from recordings.plaud.fullstate import (  # the network-free readers, re-exported
    COOLDOWN,
    EVERY,
    RECENT,
    due,
    estimate,
    last,
    refusal,
    retire,
    retired,
    running,
    running_line,
)
from recordings.plaud.importer import ImportStopped
from recordings.plaud.normalise import part_hashes
from recordings.plaud.when import parse_stamp
from recordings.refs import canonical_plaud_id

log = logging.getLogger(__name__)

MANIFESTS = "manifests"  # the folder in the state folder (`/state/manifests`, §14.6, decision 9)
MANIFEST_FORMAT = 1
LEFT_OUT_PARTS = ("title", "speakers")  # never in a manifest: Forget would have to scrub them
EXIT_OK, EXIT_FAILED, EXIT_PARTIAL = 0, 1, 2

# A listed recording's state in the verdict and the manifest (§14.6)
CHECKED, DEGRADED_STATE, PARKED_STATE, FLAGGED, EXCLUDED_STATE, GONE_STATE = (
    "checked",
    "degraded",
    "parked",
    "flagged",
    "excluded",
    "gone",
)
FAILED_STATE, NOT_HERE, NEEDS_REVIEW, NOT_CHECKED = (
    "failed",
    "not here",
    "needs review",
    "not checked",
)
OK_STATES = frozenset({CHECKED, DEGRADED_STATE, PARKED_STATE, FLAGGED, EXCLUDED_STATE, GONE_STATE})


# ---- the run ----------------------------------------------------------------------------------
@dataclass
class FullReport:
    """How a Sync everything ended: counts, IDs and fixed phrases, never a title."""

    run_id: str
    started: datetime
    resumed: bool = False
    phrase: str = "ok"
    exit_code: int = EXIT_OK
    run_failed: bool = False  # a run-level failure: the listing, a stop, a run-ending error
    sync: Any = None  # step 1's SyncReport
    states: dict[str, str] = field(default_factory=dict)  # listed canonical ID -> its state
    flagged: list[str] = field(default_factory=list)  # recording IDs, consent-listed or kept out
    checks: list[CheckResult] = field(default_factory=list)
    checked: int = 0  # covered recordings checked by this run, or since it started
    covered: int = 0
    newly_failing: list[str] = field(default_factory=list)
    manifest: str | None = None
    unfinished: bool = False  # SIGTERM: left to resume

    def counts(self) -> dict[str, int]:
        found = Counter(self.states.values())
        return dict(sorted(found.items()))

    @property
    def ok(self) -> bool:
        """What Status's Sync everything line says: no run-level failure, and no recording that
        wasn't already on the failing list failed (§14.6). The exit code is stricter."""
        return not self.run_failed and not self.newly_failing and self.exit_code == EXIT_OK

    def line(self) -> str:
        """ "all 1,234 checked (2 parked, 1 degraded)", or what is missing and why."""
        if self.run_failed:
            return f"failed ({self.phrase})"
        found = Counter(self.states.values())
        listed = sum(found.values())
        aside = [(PARKED_STATE, "parked"), (DEGRADED_STATE, "degraded"), (FLAGGED, "flagged")]
        notes = ", ".join(f"{found[s]:,} {w}" for s, w in aside if found[s])
        if self.exit_code == EXIT_OK:
            checked = found[CHECKED] + found[DEGRADED_STATE]
            text = f"all {checked:,} checked"
            return f"{text} ({notes})" if notes else text
        bad = [
            (FAILED_STATE, "failed"),
            (NOT_HERE, "not here"),
            (NEEDS_REVIEW, "need your review"),
            (NOT_CHECKED, "not checked"),
        ]
        missing = ", ".join(f"{found[s]:,} {w}" for s, w in bad if found[s])
        fine = sum(n for s, n in found.items() if s in OK_STATES)
        text = f"{fine:,} of {listed:,} fine: {missing}" if missing else f"{fine:,} of {listed:,}"
        return text + (f" ({notes})" if notes else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run_id,
            "started": self.started.isoformat(),
            "resumed": self.resumed,
            "phrase": self.phrase,
            "line": self.line(),
            "exit_code": self.exit_code,
            "ok": self.ok,
            "counts": self.counts(),
            "covered": self.covered,
            "checked": self.checked,
            "flagged": list(self.flagged),
            "failed_ids": sorted(
                {rid for rid, s in self._by_recording().items() if s == FAILED_STATE}
            ),
            "newly_failing": list(self.newly_failing),
            "manifest": self.manifest,
            "unfinished": self.unfinished,
        }

    def _by_recording(self) -> dict[str, str]:
        return {r.recording_id: _state_of(r) for r in self.checks}


def _state_of(result: CheckResult) -> str:
    if result.result == DEGRADED:
        return DEGRADED_STATE
    if result.result in SUCCESSES:
        return CHECKED
    if result.result == FAILED:
        return FAILED_STATE
    return NOT_CHECKED


def _progress(db: PlaudDB, record: dict, *, now: datetime) -> None:
    """The running record's progress, kept beside the last finished run's."""

    def change(old: Any) -> dict:
        old = old if isinstance(old, dict) else {}
        return {**old, "running": {**record, "at": now.isoformat()}}

    db.update(K_FULL, change, now=now)


def _finish(db: PlaudDB, report: FullReport, *, now: datetime, pace: float | None) -> None:
    previous = last(db)
    if pace is None and previous is not None:
        pace = previous.get("pace")  # nothing measured this time: the last pace stands
    record = {
        "started": report.started.isoformat(),
        "finished": now.isoformat(),
        "ok": report.ok,
        "exit_code": report.exit_code,
        "phrase": report.phrase,
        "line": report.line(),
        "counts": report.counts(),
        "flagged": list(report.flagged),
        "newly_failing": list(report.newly_failing),
        "covered": report.covered,
        "pace": pace,
        "manifest": report.manifest,
    }
    db.put(K_FULL, {"last": record}, now=now)


def run_full(run: Any, *, by: str) -> FullReport:
    """One Sync everything (the module's notes), under `plaud` (`plaud-full`), which the caller
    holds. `run` is a `plaud.sync.Run`; its `between` serves the light turn and single-recording
    requests in the worker's long runs (§9.2). Raises `worker.Stopping` on SIGTERM, leaving the
    run unfinished; any other error that isn't Plaud's or the archive's finishes it, failed, and
    is raised."""
    from recordings.worker import Stopping

    db: PlaudDB = run.db
    now = run.clock()
    unfinished = running(db)
    started = parse_stamp(unfinished.get("started")) if unfinished else None
    report = FullReport(run_id=run.run_id, started=started or now, resumed=started is not None)
    record = {"started": report.started.isoformat(), "by": by, "done": 0, "total": None}
    _progress(db, record, now=now)
    t0 = run.clock()
    try:
        pace = _run(run, report, record)
    except Stopping:
        report.unfinished = True
        log.info("Sync everything stopped between recordings: it resumes at the next start")
        raise
    except Exception as exc:
        report.run_failed, report.phrase = True, f"error: `{type(exc).__name__}`"
        report.exit_code = EXIT_FAILED
        _finish(db, report, now=run.clock(), pace=None)
        raise
    ended = run.clock()
    if pace is None and report.checked and ended > t0:
        pace = (ended - t0).total_seconds() / report.checked
    report.manifest = _write_manifest(run, report, now=ended)
    _finish(db, report, now=ended, pace=pace)
    log.info("Sync everything: %s (exit %d)", report.line(), report.exit_code)
    return report


def _run(run: Any, report: FullReport, record: dict) -> float | None:
    """Steps 1 to 3. Returns the measured pace (seconds a check), or None."""
    from recordings.plaud import sync
    from recordings.plaud.baseline import KeepOutWordsError
    from recordings.plaud.compare import here_from_archive

    db: PlaudDB = run.db
    newest: list = []
    keep = run.newest

    def remember(listing) -> None:
        newest.append(listing)
        if keep is not None:
            keep(listing)

    run.newest = remember
    try:
        first = sync.run_sync(run, mode="sync", sweep_checks=False)
    finally:
        run.newest = keep
    report.sync = first
    if (
        first.listing not in ("good", "small-shrink")
        or first.stopped is not None
        or first.ended_by is not None
        or not newest
    ):
        report.run_failed, report.phrase = True, first.phrase
        report.exit_code = first.exit_code if first.exit_code != EXIT_OK else EXIT_FAILED
        return None
    listing = newest[-1]
    failing_before = {row.recording_id for row in db.failing_checks()}
    try:
        here = here_from_archive(
            run.writer.index, run.writer.identity.uuid, run.writer.state, run.writer.archive, db
        )
        guard = run.importer(catch_up=False).consent
        consent = guard.current()
    except ImportStopped as exc:
        report.run_failed, report.phrase, report.exit_code = True, exc.phrase, EXIT_FAILED
        return None
    except Exception as exc:  # noqa: BLE001  the index: its class, never its text
        name = type(exc).__name__
        report.run_failed, report.phrase, report.exit_code = True, f"error: `{name}`", EXIT_FAILED
        return None
    rows = {r.recording_id: r for r in db.check_rows()}
    mine = sweep.covered(here, rows, db, consent_ids=consent.ids)
    if mine - set(rows):
        sweep.seed_missing(
            db,
            run.writer,
            mine,
            now=run.clock(),
            full_sweep_days=run.settings.full_sweep_days,
        )
        rows = {r.recording_id: r for r in db.check_rows()}
    report.covered = len(mine)

    def fresh(rid: str) -> bool:
        row = rows.get(rid)
        seen = row.last_checked_at if row is not None else None
        return seen is not None and seen >= report.started

    floor = datetime.min.replace(tzinfo=UTC)

    def oldest(rid: str) -> tuple[datetime, str]:
        row = rows.get(rid)
        return (row.last_checked_at if row is not None and row.last_checked_at else floor, rid)

    todo = sorted((rid for rid in mine if not fresh(rid)), key=oldest)
    done = len(mine) - len(todo)
    record.update({"done": done, "total": len(mine)})
    _progress(db, record, now=run.clock())
    checker = Checker(
        writer=run.writer,
        db=db,
        client=run.client,
        consent=guard,
        timezone_name=run.timezone_name,
        run_id=run.run_id,
        schedule_minutes=run.settings.schedule_minutes,
        full_sweep_days=run.settings.full_sweep_days,
        breaker_open=breaker.is_open(db),
        clock=run.clock,
    )
    listed = {row.canonical_id: row for row in listing.rows}
    results: dict[str, CheckResult] = {}
    t0, checked_now = run.clock(), 0

    def one(rid: str) -> bool:
        """Check one recording; False when the run must stop (its phrase set)."""
        nonlocal checked_now
        if run.between is not None:
            run.between()  # may raise worker.Stopping, between two recordings (§9.2)
        try:
            result = checker.check(rid, listed)
        except ImportStopped as exc:
            report.run_failed, report.phrase, report.exit_code = True, exc.phrase, EXIT_FAILED
            return False
        except CheckStopped as exc:
            report.run_failed, report.exit_code = True, EXIT_FAILED
            report.phrase = f"error: `{exc.cause}`"
            return False
        except KeepOutWordsError:
            report.run_failed, report.exit_code = True, EXIT_FAILED
            report.phrase = "error: `KeepOutWordsError`"
            return False
        except PlaudError as exc:  # run-ending: the checker raises no other
            sync._record_error(run, exc, now=run.clock())
            report.run_failed = True
            report.phrase, report.exit_code = sync._ended(exc, run.timezone_name)
            return False
        results[rid] = result
        if result.attempt:
            checked_now += 1
        return True

    for rid in todo:
        if not one(rid):
            break
        record["done"] = record.get("done", 0) + 1
        _progress(db, record, now=run.clock())
    if not report.run_failed:
        for rid in [rid for rid, r in results.items() if r.result == FAILED]:
            if not one(rid):  # §14.6: its failures once more
                break
    try:
        breaker.evaluate(db, now=run.clock())
    except (sqlite3.Error, OSError) as exc:
        log.warning("the breaker wasn't evaluated (%s)", type(exc).__name__)
    report.checks = list(results.values())
    after = {r.recording_id: r for r in db.check_rows()}
    report.checked = sum(
        1
        for rid in mine
        if (rid in results and _state_of(results[rid]) in (CHECKED, DEGRADED_STATE))
        or (rid not in results and rid in after and _fresh_row(after[rid], report.started))
    )
    report.newly_failing = sorted(
        rid for rid, r in results.items() if r.result == FAILED and rid not in failing_before
    )
    _verdict(run, report, first, here, consent.ids, results, after)
    elapsed = (run.clock() - t0).total_seconds()
    return elapsed / checked_now if checked_now and elapsed > 0 else None


def _fresh_row(row, started: datetime) -> bool:
    return row.last_checked_at is not None and row.last_checked_at >= started


def _verdict(run, report: FullReport, first, here, consent_ids, results, after) -> None:
    """Step 3: each listed recording's state, the flagged named, and the exit code."""
    db: PlaudDB = run.db
    flagged_ids = {
        e.recording_id
        for e in db.open_flags((KEPT_OUT_LATE, CONSENT_LATE))
        if e.recording_id is not None
    }
    listing_rows = db.listing_rows()
    imported = {canonical_plaud_id(r.plaud_id) for r in first.results if r.success}
    ids_of = here.ids_of()
    states: dict[str, str] = {}
    flagged: set[str] = set()
    for row in first.compare.rows if first.compare is not None else ():
        pid = row.canonical
        if row.bucket == EXCLUDED:
            states[pid] = EXCLUDED_STATE
        elif row.bucket in (WAITING, MISSING):
            if pid in imported:
                states[pid] = CHECKED  # just fetched: its import is its own baseline
            elif row.state == PARKED:
                states[pid] = PARKED_STATE
            elif row.state == REVIEW:
                states[pid] = NEEDS_REVIEW
            else:
                states[pid] = NOT_HERE
        elif row.bucket == HERE:
            worst = None
            for rid in row.recording_ids:
                state = _here_state(
                    rid,
                    pid,
                    results=results,
                    after=after,
                    started=report.started,
                    flagged=flagged_ids,
                    consent_ids=consent_ids,
                    listing=listing_rows,
                    ids=ids_of.get(rid, frozenset()),
                )
                if state == FLAGGED:
                    flagged.add(rid)
                if worst is None or (worst in OK_STATES and state not in OK_STATES):
                    worst = state
            states[pid] = worst or NOT_CHECKED
    report.states = states
    report.flagged = sorted(flagged)
    if report.run_failed:
        return  # a run-level stop keeps its own phrase and exit code; the states go in the manifest
    if any(s not in OK_STATES for s in states.values()):
        report.exit_code = EXIT_PARTIAL
        report.phrase = "partial: " + report.line()
    else:
        report.exit_code = EXIT_OK


def _here_state(rid, pid, *, results, after, started, flagged, consent_ids, listing, ids) -> str:
    row = after.get(rid)
    if (
        rid in flagged
        or (row is not None and row.kept_here_at is not None)
        or set(ids) & set(consent_ids)
        or any(_kept_out(listing.get(p)) for p in ids)
    ):
        return FLAGGED
    if row is not None and row.parked_at is not None:
        return PARKED_STATE
    if row is not None and row.gone_since is not None:
        return GONE_STATE
    if rid in results:
        return _state_of(results[rid])
    if row is not None and _fresh_row(row, started):
        return DEGRADED_STATE if row.degraded else CHECKED
    return NOT_CHECKED


# ---- the manifest (§14.6, DAN-15) ---------------------------------------------------------
def manifest_dir(state_path: Path) -> Path:
    return Path(state_path) / MANIFESTS


def ensure_manifest_dir(state_path: Path) -> Path:
    """The manifests folder, made 0700 if missing (the worker's start), so `[backup]
    extra_paths` can name it before the first Sync everything: a missing extra path refuses every
    backup (decision 9)."""
    folder = manifest_dir(state_path)
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def _write_manifest(run: Any, report: FullReport, *, now: datetime) -> str | None:
    """`plaud-<date>.json` in the manifests folder, by atomic replace. A run that failed at the
    listing writes none (it has nothing to say). Never a title or a speaker's name."""
    if report.run_failed and not report.states:
        return None
    try:
        zone = ZoneInfo(run.timezone_name)
    except Exception:  # noqa: BLE001  the default zone is checked elsewhere
        zone = now.tzinfo
    name = f"plaud-{now.astimezone(zone):%Y-%m-%d}.json"
    try:
        body = _manifest(run, report, now=now)
        folder = ensure_manifest_dir(run.writer.state.path)
        fd, tmp = tempfile.mkstemp(prefix=".plaud-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body, fh, indent=1, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, folder / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except (OSError, ValueError) as exc:
        log.warning("the Sync everything manifest wasn't written (%s)", type(exc).__name__)
        return None
    return name


def _manifest(run: Any, report: FullReport, *, now: datetime) -> dict:
    db: PlaudDB = run.db
    entries: list[dict] = []
    listed = dict(report.states)
    checks = {r.recording_id: r for r in db.check_rows()}
    by_result = {r.recording_id: _state_of(r) for r in report.checks}
    seen: set[str] = set()
    for rec in run.writer.archive.iter_recordings():
        pids = [canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud"]
        for pid in dict.fromkeys(pids):
            seen.add(pid)
            row = checks.get(rec.id)
            if pid in listed:
                state = listed[pid]
            elif row is not None and row.gone_since is not None:
                state = GONE_STATE
            elif row is not None and row.parked_at is not None:
                state = PARKED_STATE
            else:
                state = by_result.get(rec.id, NOT_CHECKED)
            snaps = snapshots(run.writer, rec, pid)
            newest = snaps.newest_complete
            raw = [
                s.raw
                for s in sorted(
                    (s for s in rec.sources if s.kind == "plaud" and s.raw),
                    key=lambda s: s.fetched_at or s.added_at,
                )
                if canonical_plaud_id(s.ref) == pid
            ]
            parts = part_hashes(newest) if newest is not None else {}
            entries.append(
                {
                    "plaud_id": pid,
                    "recording_id": rec.id,
                    "last_snapshot": raw[-1] if raw else None,
                    "state": state,
                    "parts": {k: v for k, v in sorted(parts.items()) if k not in LEFT_OUT_PARTS},
                }
            )
    for pid, state in sorted(listed.items()):
        if pid not in seen:
            entries.append({"plaud_id": pid, "state": state})  # by ID only
    return {
        "format": MANIFEST_FORMAT,
        "kind": "plaud sync everything",
        "started": report.started.isoformat(),
        "finished": now.isoformat(),
        "exit_code": report.exit_code,
        "result": report.line(),
        "counts": report.counts(),
        "flagged": list(report.flagged),
        "recordings": entries,
    }


__all__ = [
    "COOLDOWN",
    "EVERY",
    "RECENT",
    "FullReport",
    "due",
    "ensure_manifest_dir",
    "estimate",
    "last",
    "refusal",
    "retire",
    "retired",
    "run_full",
    "running",
    "running_line",
]
