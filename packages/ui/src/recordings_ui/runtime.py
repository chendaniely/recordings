"""The archive the running app serves. Set once by create_app (or by tests)."""

from __future__ import annotations

from recordings.archive import Archive

_archive: Archive | None = None


def configure(archive: Archive | None) -> None:
    global _archive
    _archive = archive


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive
