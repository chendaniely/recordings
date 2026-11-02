"""Sync everything's record, and the retired sync (stage 2b spec §14.6, §17.5; area B2): the
readers and the few writes that need no Plaud call, so the web app's views, the worker's tick and
the CLI share them without importing the client (`recordings_ui` never does, test_plaud_imports).
The run itself is `plaud.full`.

`K_FULL` holds {"running": {...} while one is unfinished (its start, by whom, done and total),
"last": {...} the last that finished (its outcome, line, counts, the flagged IDs, the measured
pace, the manifest's name)}: counts, IDs and fixed phrases, never a title. `K_RETIRED` holds
{"at"} once DAN-15's last step has retired the sync.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from recordings.plaud.checks import K_CATCH_UP_DONE, K_FULL, K_RETIRED, PlaudDB
from recordings.plaud.when import local_hhmm, parse_stamp

EVERY = timedelta(days=30)  # the worker's own run (§14.6)
COOLDOWN = timedelta(hours=1)  # a press within an hour of the last finished one is refused (§9.3)
RECENT = "Sync everything ran less than an hour ago"  # §15.6
UNKNOWN = "unknown until the first run"  # the estimate before any run measured a pace


def running(db: PlaudDB) -> dict | None:
    """The unfinished run's record, or None."""
    value = db.get(K_FULL)
    found = value.get("running") if isinstance(value, dict) else None
    return found if isinstance(found, dict) and parse_stamp(found.get("started")) else None


def last(db: PlaudDB) -> dict | None:
    """The last finished run's record, or None."""
    value = db.get(K_FULL)
    found = value.get("last") if isinstance(value, dict) else None
    return found if isinstance(found, dict) and parse_stamp(found.get("finished")) else None


def retired(db: PlaudDB) -> datetime | None:
    """When DAN-15's last step retired the sync, or None."""
    value = db.get(K_RETIRED)
    return parse_stamp(value.get("at")) if isinstance(value, dict) else None


def retire(db: PlaudDB, *, now: datetime, undo: bool = False) -> bool:
    """DAN-15's end (§17.5): the sync retired (or back). True when it changed."""
    if undo:
        if retired(db) is None:
            return False
        db.delete(K_RETIRED)
        return True
    if retired(db) is not None:
        return False
    db.put(K_RETIRED, {"at": now.isoformat()}, now=now)
    return True


def due(db: PlaudDB, *, now: datetime, schedule_minutes: int) -> bool:
    """Whether the worker's tick runs Sync everything now: one unfinished resumes (unless the sync
    is retired); otherwise, with the schedule on and the sync not retired, 30 days after the last
    finished one, or after the first catch-up before any has run. A stamp in the future counts as
    long ago."""
    if retired(db) is not None:
        return False
    if running(db) is not None:
        return True
    if schedule_minutes <= 0:
        return False
    previous = last(db)
    since = parse_stamp(previous.get("finished")) if previous else None
    since = since or parse_stamp(db.get(K_CATCH_UP_DONE))
    if since is None:
        return False
    return since > now or now - since >= EVERY


def refusal(db: PlaudDB, *, now: datetime) -> str | None:
    """Why a press of Sync everything is refused now, at the worker (fresh facts, §9.3): within
    an hour of the last finished one. None while one is unfinished: the press resumes it."""
    if running(db) is not None:
        return None
    previous = last(db)
    finished = parse_stamp(previous.get("finished")) if previous else None
    if finished is not None and timedelta(0) <= now - finished < COOLDOWN:
        return RECENT
    return None


def estimate(db: PlaudDB) -> str | None:
    """ "about 40 minutes", from the last run's measured pace (seconds a check) and the recordings
    the sweep covers now (§14.6); None before any run measured one."""
    previous = last(db)
    pace = previous.get("pace") if previous else None
    if not isinstance(pace, int | float) or isinstance(pace, bool) or pace <= 0:
        return None
    covered = sum(
        1
        for r in db.check_rows()
        if r.gone_since is None and r.parked_at is None and r.kept_here_at is None
    )
    minutes = max(1, round(pace * max(covered, 1) / 60))
    if minutes < 90:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    return f"about {round(minutes / 60)} hours"


def running_line(db: PlaudDB, tz: str) -> str | None:
    """ "Sync everything is running (412 of 1,234)", from the unfinished run's progress."""
    record = running(db)
    if record is None:
        return None
    done, total = record.get("done"), record.get("total")
    if isinstance(done, int) and isinstance(total, int) and total:
        return f"Sync everything is running ({done:,} of {total:,})"
    started = parse_stamp(record.get("started"))
    return f"Sync everything is running (started {local_hhmm(started, ZoneInfo(tz))})"


__all__ = [
    "COOLDOWN",
    "EVERY",
    "RECENT",
    "UNKNOWN",
    "due",
    "estimate",
    "last",
    "refusal",
    "retire",
    "retired",
    "running",
    "running_line",
]
