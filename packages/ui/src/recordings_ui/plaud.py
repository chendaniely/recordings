"""The Plaud sync's side of the web app (stage 2b spec §9.3, §15): the request checks and inserts;
Task 19 adds the views.

The browser's `plaud_request` input is checked here, then inserted as a row in state.db's
`plaud_requests` (recordings.plaud.requests.insert) on `asyncio.to_thread`: never on the event
loop, and never on the tag service's writer or reader thread, where it would wait behind a tag
batch. The worker claims the row and checks it again; nothing in it is taken on trust, and a
request never carries a title. A kind the worker has no handler for yet (anything but Keep it
here, in Phase 1) is refused here with NOT_YET, so no row is ever inserted for it.

It imports no Plaud client, token store or sync module (test_plaud_imports.py asserts it). It
also imports on the Pages demo's Pyodide (Python 3.12, no sqlite3, fcntl or threads): state.db is
reached only through `requests.insert`, inside the thread, and sqlite3 is imported where a
request with a writable state.db needs it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from recordings.ids import parse_id
from recordings.plaud import requests
from recordings.plaud.requests import (
    HANDLED,
    KINDS,
    MAX_PLAUD_ID_LEN,
    MAX_PLAUD_IDS,
    MAX_RESYNC_IDS,
    NOT_YET,
    REQUEST_ID_RE,
    SAVE_WAIT,
    UNREADABLE_ROW,
    PlaudRequest,
    Reply,
)
from recordings.sentinel import is_mirror

if TYPE_CHECKING:
    from recordings.state import State

    from recordings_ui.settings import Settings

log = logging.getLogger(__name__)

UNREADABLE = UNREADABLE_ROW  # the worker's phrase for a row it can't read is the same
UNKNOWN_KIND = "That isn't a Plaud request."
BAD_ID = "An ID isn't valid."
TOO_MANY_PLAUD = f"At most {MAX_PLAUD_IDS:,} Plaud recordings at a time."
TOO_MANY_RESYNC = "For more, use Sync everything"
NOT_SAVED = "The request couldn't be saved: state.db can't be used"
SUBMIT_WAIT = 1.0  # seconds: spec P6 answers a press within a second
# Why the Sync screen's buttons are off, in cannot_tag_reason's terms (fixed phrases, no title).
DEMO_REASON = "the demo never syncs with Plaud"
NO_CONFIG_REASON = "there's no config.toml, so this app only reads the archive"
MIRROR_REASON = "this is the NAS mirror's copy, which never syncs: open the archive's own app"
NO_STATE_REASON = "there's no state.db here, so this app can't ask the Plaud worker"
_KEYS = frozenset({"request_id", "kind", "plaud_ids", "recording_ids", "all"})


@dataclass(frozen=True)
class Refusal:
    request_id: str | None  # None when the request_id itself can't be read
    message: str


def _opaque(plaud_id: str) -> bool:
    """A Plaud ID as the listing gives it: visible ASCII, no space, so never room for a title."""
    return 0 < len(plaud_id) < MAX_PLAUD_ID_LEN and all("!" <= c <= "~" for c in plaud_id)


def check_request(raw: object) -> PlaudRequest | Refusal:
    """Spec §9.3's checks, before anything is inserted: the keys (and no other: a request never
    carries a title), a known kind, the caps, and IDs that parse; then a kind the worker handles
    (HANDLED), else NOT_YET. Duplicate IDs are dropped."""
    if not isinstance(raw, dict):
        return Refusal(None, UNREADABLE)
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        return Refusal(None, UNREADABLE)
    if set(raw) - _KEYS or "kind" not in raw:
        return Refusal(request_id, UNREADABLE)
    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        return Refusal(request_id, UNKNOWN_KIND)
    plaud_ids = raw.get("plaud_ids", [])
    recording_ids = raw.get("recording_ids", [])
    select_all = raw.get("all", False)
    # Shiny hands an array inside an input value over as a tuple, and tests send a list: both are
    # taken (as recordings_ui.tagging._texts does). A lone string never is.
    if (
        not isinstance(plaud_ids, list | tuple)
        or not isinstance(recording_ids, list | tuple)
        or not isinstance(select_all, bool)
    ):
        return Refusal(request_id, UNREADABLE)
    if kind == "resync" and len(recording_ids) > MAX_RESYNC_IDS:
        return Refusal(request_id, TOO_MANY_RESYNC)
    if len(plaud_ids) > MAX_PLAUD_IDS or len(recording_ids) > MAX_PLAUD_IDS:
        return Refusal(request_id, TOO_MANY_PLAUD)
    if not all(isinstance(p, str) and _opaque(p) for p in plaud_ids):
        return Refusal(request_id, BAD_ID)
    for recording_id in recording_ids:
        try:
            parse_id(recording_id)
        except (TypeError, ValueError):
            return Refusal(request_id, BAD_ID)
    if kind == "keep-here" and (plaud_ids or select_all):
        return Refusal(request_id, UNREADABLE)  # it uses recording IDs alone, and carries no more
    if kind not in HANDLED:
        return Refusal(request_id, NOT_YET)  # its handler comes with a later phase
    return PlaudRequest(
        request_id=request_id,
        kind=kind,
        plaud_ids=tuple(dict.fromkeys(plaud_ids)),
        recording_ids=tuple(dict.fromkeys(recording_ids)),
        select_all=select_all,
    )


def cannot_request_reason(settings: Settings, state: State | None) -> str | None:
    """Why this app can't ask the worker anything, or None: the demos, no config.toml, the
    mirror's copy, or no state.db (app.py's read-only cases). The buttons are disabled with it,
    and a crafted request is refused with it."""
    if settings.demo:
        return DEMO_REASON
    if settings.config is None:
        return NO_CONFIG_REASON
    if is_mirror(settings.archive):
        return MIRROR_REASON
    if state is None:
        return NO_STATE_REASON
    return None


def _refused(request_id: str, kind: str | None, message: str) -> dict:
    return Reply(request_id, kind, "refused", message).to_dict()


async def submit(
    state: State | None,
    raw: object,
    *,
    now: datetime,
    timezone_name: str,
    reason: str | None,
) -> dict:
    """One `plaud_request`, answered within a second (spec P6): a refusal, or the insert's reply.
    The demo's and the state's reasons come before NOT_YET, as the buttons show them.

    NOT_SAVED means no row. The insert is given a deadline (SAVE_WAIT, inside SUBMIT_WAIT) that
    every wait it makes uses, and it rolls back once it has passed; a state.db that can't be used
    answers NOT_SAVED at once: never a spinner or a traceback (Review Focus 5). `wait_for` here is
    only a backstop for a commit that hangs past that deadline: its reply stays NOT_SAVED, though
    that one row may still land, so a session follows the `request_id` it sent (Task 20)."""
    checked = check_request(raw)
    blocked = reason if reason is not None else NO_STATE_REASON if state is None else None
    if isinstance(checked, Refusal):
        shown = blocked if checked.message == NOT_YET and blocked is not None else checked.message
        return _refused(checked.request_id or "", None, shown)
    if blocked is not None:
        return _refused(checked.request_id, checked.kind, blocked)
    import sqlite3  # here: the Pages demo's Pyodide has none, and never gets this far

    try:
        reply = await asyncio.wait_for(
            asyncio.to_thread(
                requests.insert,
                state,
                checked,
                now=now,
                timezone_name=timezone_name,
                deadline=time.monotonic() + SAVE_WAIT,
            ),
            SUBMIT_WAIT,
        )
    except (sqlite3.Error, OSError) as exc:  # NotSaved and asyncio's TimeoutError are OSErrors
        log.warning("a Plaud request couldn't be saved (%s)", type(exc).__name__)
        return _refused(checked.request_id, checked.kind, NOT_SAVED)
    except Exception as exc:  # noqa: BLE001  a bug still answers, with its class only
        log.warning("a Plaud request failed (%s)", type(exc).__name__)
        return _refused(checked.request_id, checked.kind, f"error: `{type(exc).__name__}`")
    return reply.to_dict()
