"""The archive the running app serves, and where Status reads. Set once by create_app (or tests)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from recordings.archive import Archive

from recordings_ui.status import StatusSettings

if TYPE_CHECKING:
    from recordings.state import State

    from recordings_ui.plaud import PlaudViewSettings
    from recordings_ui.tagging import TagService

_archive: Archive | None = None
_media_base: str | None = None
_status: StatusSettings | None = None
_tagging: TagService | None = None
_plaud: PlaudViewSettings | None = None
_plaud_state: State | None = None
_plaud_reason: str | None = None


def configure(
    archive: Archive | None,
    *,
    media_base: str | None = None,
    status: StatusSettings | None = None,
    tagging: TagService | None = None,
    plaud: PlaudViewSettings | None = None,
    plaud_state: State | None = None,
    plaud_reason: str | None = None,
) -> None:
    """media_base: where the browser finds media files, as `<media_base><media.file>`.

    None (the server) links the FastAPI route /media/{id}. The static Pages demo, which has no
    FastAPI, sets "../media/" (spec §17.1). status: where Status reads its records; None in demo
    mode, which has no state folder. tagging: the process's tag service (tagging spec §5): the
    archive store from create_app, or the memory store from the Pages entry. plaud: what the
    Plaud views read (2b spec §15): the server's state folder and [plaud] keys, or the demos'
    canned view (2b phase 1 plan, decision 23); None with no config.toml. plaud_state: the
    state.db Plaud requests are inserted into (§9.3); plaud_reason: why this app can't make
    requests (cannot_request_reason), or None. With a reason, no request is ever inserted.
    """
    global _archive, _media_base, _status, _tagging, _plaud, _plaud_state, _plaud_reason
    _archive = archive
    _media_base = media_base
    _status = status
    _tagging = tagging
    _plaud = plaud
    _plaud_state = plaud_state
    _plaud_reason = plaud_reason


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


def plaud_settings() -> PlaudViewSettings | None:
    return _plaud


def plaud_state() -> State | None:
    return _plaud_state


def plaud_reason() -> str | None:
    return _plaud_reason
