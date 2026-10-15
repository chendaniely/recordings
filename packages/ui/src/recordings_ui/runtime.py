"""The archive the running app serves, and where Status reads. Set once by create_app (or tests)."""

from __future__ import annotations

from recordings.archive import Archive

from recordings_ui.status import StatusSettings

_archive: Archive | None = None
_media_base: str | None = None
_status: StatusSettings | None = None


def configure(
    archive: Archive | None, *, media_base: str | None = None, status: StatusSettings | None = None
) -> None:
    """media_base: where the browser finds media files, as `<media_base><media.file>`.

    None (the server) links the FastAPI route /media/{id}. The static Pages demo, which has no
    FastAPI, sets "../media/" (spec §17.1). status: where Status reads its records; None in demo
    mode, which has no state folder.
    """
    global _archive, _media_base, _status
    _archive = archive
    _media_base = media_base
    _status = status


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive


def media_base() -> str | None:
    return _media_base


def status_settings() -> StatusSettings | None:
    return _status
