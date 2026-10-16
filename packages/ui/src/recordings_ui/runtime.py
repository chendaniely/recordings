"""The archive the running app serves, and where Status reads. Set once by create_app (or tests)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from recordings.archive import Archive

from recordings_ui.status import StatusSettings

if TYPE_CHECKING:
    from recordings_ui.tagging import TagService

_archive: Archive | None = None
_media_base: str | None = None
_status: StatusSettings | None = None
_tagging: TagService | None = None


def configure(
    archive: Archive | None,
    *,
    media_base: str | None = None,
    status: StatusSettings | None = None,
    tagging: TagService | None = None,
) -> None:
    """media_base: where the browser finds media files, as `<media_base><media.file>`.

    None (the server) links the FastAPI route /media/{id}. The static Pages demo, which has no
    FastAPI, sets "../media/" (spec §17.1). status: where Status reads its records; None in demo
    mode, which has no state folder. tagging: the process's tag service (tagging spec §5): the
    archive store from create_app, or the memory store from the Pages entry.
    """
    global _archive, _media_base, _status, _tagging
    _archive = archive
    _media_base = media_base
    _status = status
    _tagging = tagging


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive


def media_base() -> str | None:
    return _media_base


def status_settings() -> StatusSettings | None:
    return _status


def tagging() -> TagService:
    if _tagging is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not given a tag service")
    return _tagging
