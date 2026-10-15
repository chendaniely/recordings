"""The Status page's view (spec §12.5): backups and disk, read-only.

The backup records come from state.db, opened read-only. sqlite3 is imported only when a state.db
exists, so the Pages demo (Pyodide), which has none, never needs it. A state.db that can't be read
is shown as something needing attention, with the way back, rather than breaking the page. Demo
mode has no settings, and shows no disk figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class StatusSettings:
    state_path: Path | None
    timezone_name: str
    warn: float
    stop: float
    jobs: tuple[str, ...]


def status_settings(cfg) -> StatusSettings:
    """For the server only: it reads the schedule's jobs from config."""
    from recordings.config import ConfigError
    from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT, disk_thresholds
    from recordings.ops import enabled_jobs

    try:
        thresholds = disk_thresholds(cfg)
    except ConfigError:
        thresholds = {"warn": WARN_FREE_PERCENT, "stop": STOP_FREE_PERCENT}
    return StatusSettings(state_path=cfg.state_path, timezone_name=cfg.default_timezone,
                          warn=thresholds["warn"], stop=thresholds["stop"],
                          jobs=enabled_jobs(cfg))


def status_view(archive_root: Path, settings: StatusSettings | None, *,
                now: datetime | None = None) -> dict:
    if settings is None:
        # Demo mode: its disk is a temporary copy, or on Pages, Pyodide's in-memory file system,
        # so any free-space figure would be made up (§17).
        return {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}
    from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT, disk_status, disk_view

    now = now or datetime.now(timezone.utc)
    warn = settings.warn if settings else WARN_FREE_PERCENT
    stop = settings.stop if settings else STOP_FREE_PERCENT
    state_db = settings.state_path / "state.db" if settings and settings.state_path else None
    if state_db is None or not state_db.is_file():
        return {"configured": False, "jobs": [], "attention": [], "flags": 0,
                "disk": disk_view(disk_status(archive_root, expected_uuid=None, warn=warn,
                                              stop=stop))}
    import sqlite3

    from recordings.ops import status_summary
    from recordings.state import State

    try:
        return status_summary(State.read_only(settings.state_path), archive_root=archive_root,
                              now=now, timezone_name=settings.timezone_name, warn=warn,
                              stop=stop, jobs=settings.jobs)
    except sqlite3.DatabaseError:
        return {"configured": True, "jobs": [], "flags": 0,
                "attention": ["state.db can't be read: it is damaged (restore it from the backup, "
                              "runbook: Restoring for real), or this service can't write to the "
                              "state folder, which SQLite's WAL files need."],
                "disk": disk_view(disk_status(archive_root, expected_uuid=None, warn=warn,
                                              stop=stop))}
