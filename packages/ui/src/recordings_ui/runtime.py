"""The archive the running app serves. Set once by create_app (or by tests)."""

from __future__ import annotations

from recordings.archive import Archive

_archive: Archive | None = None
_media_base: str | None = None


def configure(archive: Archive | None, *, media_base: str | None = None) -> None:
    """media_base: where the browser finds media files, as `<media_base><media.file>`.

    None (the server) links the FastAPI route /media/{id}. The static Pages demo, which has no
    FastAPI, sets "../media/" (spec §17.1).
    """
    global _archive, _media_base
    _archive = archive
    _media_base = media_base


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive


def media_base() -> str | None:
    return _media_base
