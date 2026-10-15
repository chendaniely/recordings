"""The disk guard (spec §12.5, §15.1), checked before anything writes in bulk, and by Status.

It checks two things. First the marker: the archive's sentinel, so the disk is mounted and it is
the right archive. Then the free space on the archive's disk: a warning below 20% free, and a stop
below 5%, where new imports stop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from recordings.config import Config, ConfigError
from recordings.sentinel import SentinelError, read_sentinel

WARN_FREE_PERCENT = 20.0
STOP_FREE_PERCENT = 5.0


@dataclass(frozen=True)
class DiskStatus:
    path: str
    marker: bool
    free_bytes: int | None
    total_bytes: int | None
    level: Literal["ok", "warn", "stop"]
    message: str

    @property
    def free_percent(self) -> float | None:
        if not self.total_bytes or self.free_bytes is None:
            return None
        return self.free_bytes * 100 / self.total_bytes


def disk_thresholds(cfg: Config) -> dict[str, float]:
    section = cfg.data.get("disk", {})
    warn = section.get("warn_free_percent", WARN_FREE_PERCENT)
    stop = section.get("stop_free_percent", STOP_FREE_PERCENT)
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (warn, stop))
    if not numbers or not 0 < stop < warn < 100:
        raise ConfigError("[disk] stop_free_percent and warn_free_percent must be numbers with "
                          "0 < stop_free_percent < warn_free_percent < 100")
    return {"warn": float(warn), "stop": float(stop)}


def disk_status(archive_root: Path, *, expected_uuid: str | None,
                warn: float = WARN_FREE_PERCENT, stop: float = STOP_FREE_PERCENT) -> DiskStatus:
    root = Path(archive_root)
    marker, message = True, ""
    try:
        identity = read_sentinel(root)
        if expected_uuid is not None and identity.uuid != expected_uuid:
            marker, message = False, "the archive's UUID changed: this is not the archive expected"
    except SentinelError as exc:
        marker, message = False, str(exc)
    free = total = None
    try:
        st = os.statvfs(root)
        free, total = st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
    except (OSError, AttributeError):  # missing folder, or no statvfs (the Pages demo)
        pass
    status = DiskStatus(path=str(root), marker=marker, free_bytes=free, total_bytes=total,
                        level="ok", message="")
    pct = status.free_percent
    if not marker:
        level = "stop"
    elif pct is not None and pct < stop:
        level, message = "stop", f"{pct:.1f}% free: below {stop:g}%, new imports stop"
    elif pct is not None and pct < warn:
        level, message = "warn", f"{pct:.1f}% free: below {warn:g}%"
    else:
        level, message = "ok", (f"{pct:.1f}% free" if pct is not None else "free space unknown")
    return DiskStatus(path=str(root), marker=marker, free_bytes=free, total_bytes=total,
                      level=level, message=message)


def _size(n: int) -> str:
    for unit, scale in (("TB", 10 ** 12), ("GB", 10 ** 9), ("MB", 10 ** 6)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} bytes"


def disk_view(status: DiskStatus) -> dict:
    """What the Status page shows (spec §12.5): the level, the reason, and free space."""
    pct = status.free_percent
    label = (f"{_size(status.free_bytes)} free of {_size(status.total_bytes)}"
             if status.free_bytes is not None and status.total_bytes else None)
    return {"level": status.level, "message": status.message, "marker": status.marker,
            "free_percent": round(pct, 1) if pct is not None else None, "free_label": label}
