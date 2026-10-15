"""The Status page's view (spec §12.5): backups and disk, read-only.

The backup records come from state.db, opened read-only. sqlite3 is imported only when a state.db
exists, so the Pages demo (Pyodide), which has none, never needs it. A state.db that can't be read
is shown as something needing attention, with the way back only if it is damaged, rather than
breaking the page. Demo mode has no settings, and shows no disk figure.
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


NO_STATE_DB = ("There is no state.db yet, so no backup can be recorded: run recordings init once, "
               "on the homelab server, and check that its state folder is mounted.")


def _without_state(archive_root: Path, settings: StatusSettings, attention: list[str], *,
                   configured: bool) -> dict:
    """Status with no backup records: the disk alone, and why (no state.db, or one that can't be
    read)."""
    from recordings.disk import disk_status, disk_view

    disk = disk_view(disk_status(archive_root, expected_uuid=None, warn=settings.warn,
                                 stop=settings.stop))
    return {"configured": configured, "jobs": [], "attention": attention, "flags": 0, "disk": disk}


def status_view(archive_root: Path, settings: StatusSettings | None, *,
                now: datetime | None = None) -> dict:
    if settings is None:
        # Demo mode: its disk is a temporary copy, or on Pages, Pyodide's in-memory file system,
        # so any free-space figure would be made up (§17).
        return {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}
    now = now or datetime.now(timezone.utc)
    state_db = settings.state_path / "state.db" if settings.state_path else None
    state = None
    if state_db is not None and state_db.is_file():
        from recordings.state import State  # imports sqlite3: only where a state.db exists

        state = State.read_only(settings.state_path)  # None if it went away since the check
    if state is None:
        # A laptop has no backups; a server whose config has a [backup] has lost its records.
        attention = [NO_STATE_DB] if "backup" in settings.jobs else []
        return _without_state(archive_root, settings, attention, configured=False)
    import sqlite3

    from recordings.ops import status_summary
    from recordings.state import unreadable

    try:
        return status_summary(state, archive_root=archive_root, now=now,
                              timezone_name=settings.timezone_name, warn=settings.warn,
                              stop=settings.stop, jobs=settings.jobs)
    except sqlite3.DatabaseError as exc:
        # Only damage gets the way back: restoring over a sound state.db that is merely busy,
        # unmounted or not writable would throw away its queue and merge bases.
        return _without_state(archive_root, settings, [unreadable(state_db, exc)], configured=True)
