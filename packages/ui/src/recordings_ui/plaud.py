"""The Plaud sync's side of the web app (stage 2b spec §9.3, §15): the request checks and inserts,
and the views.

The browser's `plaud_request` input is checked here, then inserted as a row in state.db's
`plaud_requests` (recordings.plaud.requests.insert) on `asyncio.to_thread`: never on the event
loop, and never on the tag service's writer or reader thread, where it would wait behind a tag
batch. The worker claims the row and checks it again; nothing in it is taken on trust, and a
request never carries a title. A kind the worker has no handler for yet (not in
`requests.HANDLED`: Re-sync, Sync everything and Accept these changes until Phase 3) is refused
here with NOT_YET, so no row is ever inserted for it.

It imports no Plaud client, token store or sync module (test_plaud_imports.py asserts it). It
also imports on the Pages demo's Pyodide (Python 3.12, no sqlite3, fcntl or threads): state.db is
reached only through `requests.insert`, inside the thread, and sqlite3 is imported where a
request with a writable state.db needs it.

The views (Task 19; 2b spec §15, §22): the Sync screen's SyncView, Status's SyncPanel, the
Library's Arrivals and Details' RecordingPlaud, read-only from state.db, or from
demo/plaud-sync.json in the demos. They import what reads state.db inside each function,
never at the top, because the Pages demo imports this module on Pyodide, which has no sqlite3.
SyncPanel's `failing_checks` (Status's "N recordings fail every check · Re-sync · Park", §15.2;
Dan, Phase 1's Q&A §6) reads `plaud_checks` through PlaudDB.failing_checks: a recording with
FAILING_AFTER (3) or more failed checks in a row (`failures`), neither parked (`parked_at`) nor
gone (`gone_since`), dated by `failing_since`. Phase 3's checks write those columns; until then
the list is empty, and the panel draws no line.

Phase 2's readers (area A6) fill what the listing writes: the equations, the consent line's count
on Plaud and "All here" from `K_COMPARE` and `K_LAST_GOOD_LISTING`; Not here yet and Kept out
from the cache's compare rows (`Cache.read_only`), each title checked again before it leaves
(`_Read.shown_title`, P13); a hold from `K_HOLD`, a small shrink from `K_SMALL_SHRINK`; the
sign-in from `K_SIGN_IN` and its blocker; Gone and Parked from `plaud_checks` and
`plaud_listing`; the re-key flags and a rebuild's review rows; the last runs from `ops_runs`.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

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
from recordings.plaud.when import local_hhmm, parse_stamp
from recordings.sentinel import is_mirror

if TYPE_CHECKING:
    from recordings.config import Config
    from recordings.plaud.checks import Event
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
RELEASE_KINDS = frozenset({"release-preview", "release", "unrelease"})


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
    if kind in ("accept", "accept-changes", "compare", "sync") and (
        plaud_ids or recording_ids or select_all
    ):
        return Refusal(request_id, UNREADABLE)  # they act on the listing, and name nothing
    if kind in RELEASE_KINDS and (len(plaud_ids) != 1 or recording_ids or select_all):
        return Refusal(request_id, UNREADABLE)  # Release names one Plaud ID, and nothing else
    if kind == "import" and (recording_ids or not (plaud_ids or select_all)):
        return Refusal(request_id, UNREADABLE)  # Plaud IDs not here, or all of them
    if kind in ("park", "unpark") and (select_all or not (plaud_ids or recording_ids)):
        return Refusal(request_id, UNREADABLE)
    if kind in ("park", "unpark") and recording_ids:
        return Refusal(request_id, NOT_YET)  # a recording's parking (its checks) is area B2's
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


# ---- The views (Task 19; 2b spec §15, §22) -------------------------------------------------
# The views read state.db and the cache, read-only: the summary and what stops the sync
# (sync_health), the blockers, the privacy flags, what the worker read from the consent list (a
# count), the gate, the worker's heartbeat, the ping, the requests' last result, and Phase 2's
# listing, compare, hold and sign-in (area A6). What Phase 3 writes (the breaker, checks, Sync
# everything, held removals) is null or empty here until its reader comes, against types.ts and
# the canned view (2b phase 1 plan, Task 19). Every reader is imported inside the function that
# needs it, never at the top: the Pages demo runs this module on Pyodide, which has no sqlite3,
# and draws only the canned view (decision 23). Times and dates are formatted here, in the default
# zone; a title appears only in the Sync screen's Not here yet and Updated rows.

ALL_HERE_EXPLAIN = "Every recording on Plaud is in the archive or deliberately left out."
# Sync now's and the imports' reason while a listing is held (A5's handoff: the worker refuses
# them anyway, "Held: see Status").
HELD_BUTTON = "Held: Plaud's listing is held (see Held, above)"
HOLD_FALLBACK = "Plaud sync paused: its listing is held (runbook)."
HOLD_FALLBACK_REFUSED = "Accept refused: fix this first (runbook)"
# Copied from recordings.plaud.compare, which imports sqlite3 (the Pages demo's Pyodide has none):
# test_constants_copied_to_keep_sqlite_out_of_pyodide_match_their_sources pins each to its source.
TITLES_HIDDEN_CONSENT = "titles hidden until the consent list reads"
TITLES_HIDDEN_IDS = "titles hidden while Plaud's IDs look changed"
PARKED_STATE = "parked"
FAILED_STATE = "failed 3 times · Park"
REKEYED = "rekeyed"  # the re-key match's open flag (compare.REKEYED_EVENT)
CONSENT_KIND = "consent"  # the cache's keyed-out kind for a consent-listed ID (compare.BY_CONSENT)
RELEASE_BY = ("was-title", "rekeyed")  # Kept out rows Release can lift (release.kept_by)
# The re-key flag's sentence (§11.4 step 6), by what the new ID may be.
REKEY_OF = {
    "consent": "a consent-listed recording",
    "kept-out": "a recording kept out by title",
    "several": "one of several consent-listed or kept-out recordings",
}
REKEY_LISTED = "May be {of} back under a new Plaud ID: it stays out until you confirm (runbook)"
REKEY_AUDIO = "Its audio is {of}'s, already here: it stays out until you confirm (runbook)"
REBUILT_LINE = "The sync's records were rebuilt: {n} wait{s} for your review on the Sync screen."
SIGN_IN_FIX = "At the Mac: `make plaud-sign-in`."  # signin.SIGN_IN_FIX, as §15.2 words it
SIGN_IN_SOON = timedelta(days=7)  # Needs attention warns this long before the sign-in expires
DEMO_NOTE = "Demo: nothing is synced"
DEMO_STATUS = "Demo: Plaud sync isn't part of the demo."
DEMO_VIEW_NAME = "plaud-sync.json"
NO_SYNC = (
    "No sync has run yet"  # checks.NO_SYNC_YET, kept here: checks needs sqlite3 (a test pins it)
)
NO_GOOD_LISTING = "No good listing yet"
UNREADABLE_SYNC = "The sync's records in state.db can't be read now: see Needs attention"
# Needs attention's line for it, with the exception's class and never its text (§15.6).
UNREADABLE_LINE = "The sync's records in state.db can't be read now (`{}`): check the worker's log."
FULL_UNKNOWN = "unknown until the first run"
CONSENT_UNREADABLE = "the consent list can't be read"  # keepout.STOP_UNREADABLE
NO_WORKER_YET = "The Plaud worker hasn't reported yet: check that the worker container is running."
WORKER_FAILED = "The Plaud worker couldn't start (`{}`): see `rc logs worker` (runbook)."
PING_FAILING = "The sync's Healthchecks ping has failed for 2 hours."
# A recording the patterns pass couldn't check is a private-unwritten flag of its own (`"owed":
# "patterns"`), worded by flags.flag_sentence (PRIVATE_UNCHECKED), so its line follows the flag.
# A late consent pass that failed as a whole (Task 13's private-unwritten blocker, with the class it
# raised): the remedy that can help, never `recordings reindex`.
UNWRITTEN_STOPPED = (
    "late consent listings stopped ({}): check the archive disk, then the worker's log"
)
# A patterns pass that raised as a whole (the blocker's `patterns_error`), with the step its class
# needs, as flags.flag_sentence words a flag (§15.6). The worker tries it again, so what lasts is
# the archive disk, state.db, the stored keep-out words, or a bug its log names by class.
PATTERNS_STOPPED = "checking titles against the private patterns stopped ({}): {}"
PATTERNS_STOPPED_STEPS = {
    "disk": "check the archive disk and its permissions, then the worker's log",
    "state": "check state.db's disk, then the worker's log",
    "words": "the stored keep-out words aren't a list: `recordings plaud keep-out-words` says so",
    "other": "check the worker's log",
}
# By name only: the views import no sqlite3 at module level. OSError's kinds are Python's own.
_DISK_ERRORS = frozenset(
    name for name, v in vars(builtins).items() if isinstance(v, type) and issubclass(v, OSError)
)
_STATE_ERRORS = frozenset(
    {
        "DatabaseError",
        "OperationalError",
        "IntegrityError",
        "InternalError",
        "InterfaceError",
        "ProgrammingError",
        "NotSupportedError",
        "DataError",
        "StateError",
    }
)
# Spec §22's numbers.
PING_GRACE = timedelta(minutes=10)  # "one schedule interval plus 10 minutes"
FAILING_FOR = timedelta(hours=2)
PING_FAILING_FOR = timedelta(hours=2)
SCHEDULE_OFF_FOR = timedelta(hours=24)
STUCK_AFTER = timedelta(hours=2)
STUCK_AFTER_LONG = timedelta(hours=12)  # Sync everything and the catch-up
RECENT = timedelta(days=7)  # Updated on Plaud
FLAG_EVENTS = ("kept-out-late", "consent-late", "private-unwritten")
ARRIVAL_EVENTS = ("imported", "arrived-private", "updated", "retagged-private")
PRIVATE_EVENTS = ("retagged-private", "arrived-private")
LONG_JOBS = ("plaud-full", "plaud-catch-up")
# Events that mark a recording whose title must never reach a view (P13): a privacy flag, open or
# resolved, and Keep it here. A `retagged-private` whose reason is a late keep-out counts too.
TITLE_HIDING_EVENTS = (*FLAG_EVENTS, "kept-here")
KEPT_OUT_WHY = ("kept out late", "kept out by title")
# Decision 15: the `why` a retagged-private event records (privacy.why_phrase), as Details says it.
# Phase 2's rebuild record adds "the sync's record was rebuilt" with its wording.
WHY_DETAILS = {
    "title changed": "its Plaud title changed",
    "first check": "its title matched a private pattern",
    "patterns changed": "its title matched a private pattern",
    "kept out by title": "kept out by title",  # a keep-out word alone (Task 9's "kept out late")
    "the sync's record was rebuilt": "the sync's record was rebuilt",  # case 4 after a rebuild
}
ARRIVED_PRIVATE = "its title matched when it arrived"
CAUSES = {
    "listing": "no good listing",
    "disk": "the disk is too full to import",
    "work": "every fetch failed",
}
# The heartbeat's phase in plain words, for "last reported 14:21 (idle)" (§15.2).
PHASES = {
    "starting": "starting",
    "idle": "idle",
    "turn": "syncing",
    "request": "answering a request",
    "consent": "reading the consent list",
    "checking": "checking",  # the patterns pass at start, with its count (Task 13)
    "rechecking": "checking titles again",  # its retry after the start, counted if it is whole
    "stopping": "stopping",
}
KIND_LABELS = {
    "compare": "Compare",
    "sync": "Sync now",
    "import": "Import selected",
    "resync": "Re-sync",
    "full": "Sync everything",
    "accept": "Accept this listing",
    "accept-changes": "Accept these changes",
    "release-preview": "Release",
    "release": "Release",
    "unrelease": "Undo release",
    "park": "Park",
    "unpark": "Unpark",
    "keep-here": "Keep it here",
}
JOB_LABELS = {
    "plaud-sync": "A sync",
    "plaud-compare": "A compare",
    "plaud-import": "An import",
    "plaud-resync": "A re-sync",
    "plaud-full": "Sync everything",
    "plaud-baseline": "The baseline",
    "plaud-catch-up": "The catch-up",
    "plaud-accept": "Accept this listing",
    "plaud-review": "Marking for review",
    "plaud-breaker": "Accept these changes",  # or `breaker reset`: a moment's hold (§14.4)
    "plaud-volatility": "The volatility check",
    "import": "The audio-router import",  # it holds `plaud` beside `ops` (2b spec §9.4)
}


@dataclass(frozen=True)
class PlaudViewSettings:
    state_path: Path | None
    timezone_name: str
    schedule_minutes: int
    full_sweep_days: int
    auto_import: bool
    keep_out_word_ok: bool
    demo_view: Path | None  # demo mode and Pages: the canned view's file


def plaud_view_settings(cfg: Config) -> PlaudViewSettings:
    """For the server only: the state folder and the [plaud] keys the views read. A [plaud]
    section that won't read gives the spec's defaults here, so the app still starts; doctor and
    the worker name the fault."""
    from recordings.config import ConfigError
    from recordings.plaud.settings import (
        DEFAULT_FULL_SWEEP_DAYS,
        DEFAULT_SCHEDULE_MINUTES,
        plaud_settings,
    )

    try:
        ps = plaud_settings(cfg)
    except ConfigError:
        return PlaudViewSettings(
            state_path=cfg.state_path,
            timezone_name=cfg.default_timezone,
            schedule_minutes=DEFAULT_SCHEDULE_MINUTES,
            full_sweep_days=DEFAULT_FULL_SWEEP_DAYS,
            auto_import=True,
            keep_out_word_ok=True,
            demo_view=None,
        )
    return PlaudViewSettings(
        state_path=cfg.state_path,
        timezone_name=cfg.default_timezone,
        schedule_minutes=ps.schedule_minutes,
        full_sweep_days=ps.full_sweep_days,
        auto_import=ps.auto_import,
        keep_out_word_ok=ps.keep_out_word_ok,
        demo_view=None,
    )


def demo_view_settings(archive: Path) -> PlaudViewSettings:
    """Demo mode and Pages: no state folder, only the canned view that
    `settings.from_env(demo=True)` copied beside the archive's copy (decision 23)."""
    return PlaudViewSettings(
        state_path=None,
        timezone_name="UTC",
        schedule_minutes=0,
        full_sweep_days=7,
        auto_import=False,
        keep_out_word_ok=True,
        demo_view=archive.parent / DEMO_VIEW_NAME,
    )


def _handled() -> dict:
    """What the worker can do yet, which every view carries (G7): the UI disables every other
    request button with `not_yet` as its reason, as it shows the gate's and the demo's. Each area
    of stage 2b's build widens requests.HANDLED with its handlers (Keep it here, then Accept this
    listing)."""
    return {"handled": sorted(HANDLED), "not_yet": NOT_YET}


# ---- Formatting: the server formats every time, in the default zone ----------------------------


def _local(t: datetime, tz: str) -> datetime:
    return t.astimezone(ZoneInfo(tz))


def _hm(t: datetime | None, tz: str) -> str | None:
    return None if t is None else local_hhmm(t, ZoneInfo(tz))


def _date(t: datetime | None, tz: str) -> str | None:
    return None if t is None else f"{_local(t, tz):%Y-%m-%d}"


def _stamp(t: datetime | None, tz: str) -> str | None:
    return None if t is None else f"{_date(t, tz)} {_hm(t, tz)}"


def _when(t: datetime | None, now: datetime, tz: str) -> str | None:
    """`14:20` on the same local day as now, else `2026-10-22 14:20`."""
    if t is None:
        return None
    return _hm(t, tz) if _local(t, tz).date() == _local(now, tz).date() else _stamp(t, tz)


def _copy_form(plaud_id: str) -> str:
    """Copy ID: the whole ID in the consent list's own form (§15.1), which `make consent-add`
    and the list read as an exclusion."""
    return f"- `{plaud_id}` —"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _class_name(value: object) -> str | None:
    """An exception's class name as the worker recorded it; anything else is none (§15.6)."""
    return value if isinstance(value, str) and value.isidentifier() else None


def _patterns_stopped(error: str) -> str:
    """PATTERNS_STOPPED for a pass that raised `error` (a class name), with the step it needs."""
    if error == "KeepOutWordsError":
        step = PATTERNS_STOPPED_STEPS["words"]
    elif error in _DISK_ERRORS:
        step = PATTERNS_STOPPED_STEPS["disk"]
    elif error in _STATE_ERRORS:
        step = PATTERNS_STOPPED_STEPS["state"]
    else:
        step = PATTERNS_STOPPED_STEPS["other"]
    return PATTERNS_STOPPED.format(error, step)


def _sentence(ev: Event) -> str:
    """A flag's fixed sentence (Task 8's flag_sentence): a `private` that couldn't be written for
    a reason `recordings reindex` can't mend (a full or read-only disk, a folder it can't enter, a
    busy state.db) names the recording and its cause's class, recorded in the event's detail; a
    recording the patterns pass couldn't check (`"owed": "patterns"`) says so, by ID."""
    from recordings.plaud.flags import flag_sentence

    cause, owed = ev.detail.get("cause"), ev.detail.get("owed")
    return flag_sentence(
        ev.kind,
        ev.recording_id or "",
        cause if isinstance(cause, str) else None,
        owed if isinstance(owed, str) else None,
    )


# ---- Reading state.db, read-only ----------------------------------------------------------------


def _open(settings: PlaudViewSettings | None) -> State | None:
    if settings is None or settings.state_path is None:
        return None
    if not (settings.state_path / "state.db").is_file():
        return None
    from recordings.state import State  # imports sqlite3: only where a state.db exists

    return State.read_only(settings.state_path)


def _q(state: State, sql: str, args: tuple = ()) -> list[tuple]:
    """A read of state.db; a 2b table that isn't there yet is NoSyncYet (§9.5), never the
    "restore state.db" text."""
    import sqlite3

    from recordings.plaud.checks import NO_SYNC_YET, NoSyncYet, no_such_table

    try:
        with state.db() as db:
            return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if no_such_table(exc):
            raise NoSyncYet(NO_SYNC_YET) from None
        raise


def _body(state: State, recording_id: str) -> dict | None:
    """The recording.json the app last committed (state.db's merge base, main spec §6.4). The
    Sync screen names a recording here from it, as the Library names it from the file."""
    from recordings.ids import ID_RE, relative_dir

    if not ID_RE.fullmatch(recording_id or ""):
        return None
    rel = (relative_dir(recording_id) / "recording.json").as_posix()
    rows = _q(
        state,
        "SELECT body FROM revisions WHERE file = ? AND committed = 1 ORDER BY id DESC LIMIT 1",
        (rel,),
    )
    if not rows:
        return None
    try:
        body = json.loads(rows[0][0])
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _plaud_refs(body: dict | None) -> list[str]:
    return [
        s["ref"]
        for s in (body or {}).get("sources") or []
        if isinstance(s, dict) and s.get("kind") == "plaud" and isinstance(s.get("ref"), str)
    ]


def _next_sync(
    settings: PlaudViewSettings, gate_open: bool, last: datetime | None, now: datetime
) -> str | None:
    """When the next scheduled turn is due: schedule_minutes after the later of last_started and
    last_ended (§9.2); none while the schedule is off or the gate closed."""
    if settings.schedule_minutes <= 0 or not gate_open or last is None:
        return None
    due = max(last + timedelta(minutes=settings.schedule_minutes), now)
    return _when(due, now, settings.timezone_name)


def _rekey_sentence(ev: Event) -> str:
    """A possible re-keyed match's sentence (§11.4 step 6; area A3's listing match, `of`, and
    area A4's audio match, `by: audio`): what it may be, and that it stays out until Dan
    confirms. Never a title."""
    of = REKEY_OF.get(str(ev.detail.get("of")), REKEY_OF["several"])
    template = REKEY_AUDIO if ev.detail.get("by") == "audio" else REKEY_LISTED
    return _cap(template.format(of=of))


def _flag(db: Any, ev: Event, r: _Read | None = None) -> dict:
    """One privacy flag (P15), by recording ID with its next step; never a title. Its Plaud ID is
    shown as the listing row holds it (a late keep-out makes one), else in its canonical form. A
    `private` that couldn't be written says what its cause needs (_sentence). A possible re-keyed
    match is by Plaud ID alone, with its date and duration from the cache (`r`)."""
    from recordings.plaud.flags import KEPT_OUT_LATE

    listed = None
    if ev.canonical_plaud_id:
        row = r.listing.get(ev.canonical_plaud_id) if r else db.listing(ev.canonical_plaud_id)
        listed = row.listed_id if row is not None else ev.canonical_plaud_id
    date = duration = None
    if ev.kind == REKEYED and r is not None and ev.canonical_plaud_id:
        found = next(
            (c for c in r.cache_rows() if c.canonical_plaud_id == ev.canonical_plaud_id), None
        )
        if found is not None:
            date = _stamp(found.start_at, r.tz)
            duration = _minutes(found.duration_ms)
    return {
        "kind": ev.kind,
        "recording_id": ev.recording_id,
        "plaud_id": listed,
        "copy_id": _copy_form(listed) if listed else None,
        "sentence": _rekey_sentence(ev) if ev.kind == REKEYED else _sentence(ev),
        "keep_here": ev.kind == KEPT_OUT_LATE and ev.recording_id is not None,
        "date": date,
        "duration": duration,
    }


class _Read:
    """What one view reads, read once (spec §22). Raises NoSyncYet without the 2b tables."""

    def __init__(self, settings: PlaudViewSettings, state: State, now: datetime) -> None:
        from recordings.plaud import checks
        from recordings.plaud.baseline import enforced_words
        from recordings.plaud.gate import gate_now
        from recordings.plaud.health import sync_health

        self.settings, self.state, self.now = settings, state, now
        self.tz = settings.timezone_name
        self.db = checks.PlaudDB(state)
        self.health = sync_health(state, schedule_minutes=settings.schedule_minutes)
        self.gate = gate_now(self.db)
        get = self.db.get
        self.consent = _dict(get(checks.K_CONSENT))
        self.ping = _dict(get(checks.K_LAST_PING))
        # As the Writer reads them: a row that isn't a list raises KeepOutWordsError (a
        # ValueError), so the views say the records can't be read, never "none" or its letters.
        self.words = list(enforced_words(self.db))
        stamps = (parse_stamp(get(checks.K_LAST_STARTED)), parse_stamp(get(checks.K_LAST_ENDED)))
        self.last_turn = max((t for t in stamps if t is not None and t <= now), default=None)
        self.flags = self.db.open_flags(FLAG_EVENTS)
        # Read here, so a row that can't be read stops every view the same way (_guarded).
        recent = self.db.events_since(now - RECENT, ("updated", "retagged-private"))
        self.recent = sorted(recent, key=lambda e: e.at, reverse=True)
        unwritten = _dict(_dict(get(checks.BLOCKER_PREFIX + "private-unwritten")).get("detail"))
        self.unwritten_error = _class_name(unwritten.get("error"))
        self.patterns_error = _class_name(unwritten.get("patterns_error"))
        self.failing_checks = _failing_checks(self.db, self.tz)
        # Phase 2's data (area A6): the newest compare, the last good listing, a hold or a small
        # shrink, the sign-in's facts, a rebuild, the re-key flags and plaud_listing's rows.
        self.compare = _dict(get(checks.K_COMPARE))
        self.last_good = _dict(get(checks.K_LAST_GOOD_LISTING))
        hold = get(checks.K_HOLD)
        self.hold = hold if isinstance(hold, dict) else None
        self.small_shrink = _dict(get(checks.K_SMALL_SHRINK))
        self.sign_in = _dict(get(checks.K_SIGN_IN))
        self.rebuilt = get(checks.K_REBUILT) is not None
        self.rate_until = _rate_until(self.db, now)
        self.rekeyed = self.db.open_flags((REKEYED,))
        self.listing = self.db.listing_rows()
        self._cache: tuple[list, dict] | None = None

    def gate_reason(self) -> str | None:
        return None if self.gate.open else self.gate.reason

    def when(self, t: datetime | None) -> str | None:
        return _when(t, self.now, self.tz)

    def next_sync(self) -> str | None:
        return _next_sync(self.settings, self.gate.open, self.last_turn, self.now)

    def flag_rows(self) -> list[dict]:
        return [_flag(self.db, ev, self) for ev in (*self.flags, *self.rekeyed)]

    def health_cause(self) -> str | None:
        """What stops the sync, from sync_health (§16). A `private` that can't be written names
        the remedy that can help, never `recordings reindex` for a disk fault: a late consent pass
        or a patterns pass that failed as a whole says its class; otherwise the first open flag
        whose cause needs its own remedy says what its own sentence says (§15.6). The worker's
        last `gate` turn says nothing once the gate is open: the catch-up has opened it since, and
        the worker's next turn, a few seconds on, runs the first sync (area A6, found by
        `make e2e-live`)."""
        from recordings.plaud.flags import PRIVATE_UNWRITTEN, SENTENCES
        from recordings.plaud.health import BLOCKER_PHRASES

        if self.gate.open and self.held_by_gate():
            return None
        found = self.health.cause(tz=ZoneInfo(self.tz))
        if found != BLOCKER_PHRASES["private-unwritten"]:
            return found
        if self.unwritten_error:
            return UNWRITTEN_STOPPED.format(self.unwritten_error)
        if self.patterns_error:
            return _patterns_stopped(self.patterns_error)
        for ev in self.flags:
            said = _sentence(ev)
            if ev.kind == PRIVATE_UNWRITTEN and said != SENTENCES[PRIVATE_UNWRITTEN]:
                return said.removesuffix(".")
        return found

    def held_by_gate(self) -> bool:
        """Whether `health_cause` is the gate's reason, from the worker's recorded `gate` turn
        (health.py's `cause` reaches that outcome only when no blocker, no failing streak and no
        schedule-off comes first). The gate waiting for the first catch-up isn't a failure."""
        h = self.health
        return (
            not h.blockers
            and h.failing is None
            and h.schedule_minutes > 0
            and h.last_turn is not None
            and h.last_turn.outcome == "gate"
        )

    def off_before_catch_up(self, stopped: str | None) -> bool:
        """Whether `stopped` is the schedule being off, and the first catch-up isn't done (§15.2,
        §11.6): that reads plain, not as a failure. After the catch-up it stays a problem, which
        reaches Needs attention once it has lasted 24 hours (C6.14)."""
        from recordings.plaud.health import OFF

        return stopped == OFF and self.health.catch_up_done is None

    def cause(self) -> str | None:
        """What stops the sync, else the gate's reason."""
        return self.health_cause() or self.gate_reason()

    def summary_view(self) -> dict:
        """The one summary line the Sync screen and Status share (§15.1, §15.2): what stops the
        sync, or the gate's reason; else "All here · checked 14:20 · next sync 14:40" while the
        last good listing is under two schedule intervals old and nothing is waiting or missing
        (parked recordings aside), else what the newest compare left waiting or missing."""
        stopped = self.health_cause()
        problem = stopped or self.gate_reason()
        if problem is None:
            if (here := self.all_here()) is not None:
                return _summary_of("all-here", here, explain=ALL_HERE_EXPLAIN, see_status=False)
            return _summary_of("no-sync", self.waiting_line(), see_status=True)
        # The gate waiting for the first catch-up, and a listing not yet written, aren't failures
        # (§11.6, §15.2): only what sync_health says stops the sync is a problem.
        kind = "problem" if stopped and not self.held_by_gate() else "no-sync"
        if kind == "problem" and self.off_before_catch_up(stopped):
            kind = "no-sync"  # §15.2: "Scheduled sync is off since …" is plain before the catch-up
        return {"kind": kind, "sentence": _cap(problem), "explain": None, "see_status": True}

    # ---- Phase 2's readers (area A6): the listing, the compare, the hold, the sign-in ---------
    def equation(self) -> dict | None:
        """The newest compare's equation (§11.3), with the time of the listing behind it."""
        eq = _dict(self.compare.get("equation"))
        if not eq:
            return None
        keys = ("listed", "here", "waiting", "excluded", "missing", "arrive_private")
        return {
            **{k: _int(eq.get(k)) for k in keys},
            "listing_at": self.when(parse_stamp(self.compare.get("at"))),
        }

    def archive_equation(self) -> dict | None:
        a = _dict(self.compare.get("archive"))
        if not a:
            return None
        return {k: _int(a.get(k)) for k in ("from_plaud", "here", "gone", "absent")}

    def parked_count(self) -> int:
        return sum(1 for row in self.listing.values() if row.parked_at is not None)

    def all_here(self) -> str | None:
        """§15.2's "All here", or None: the last good listing under two schedule intervals old,
        and the newest compare leaving nothing waiting or missing but what is parked."""
        at = parse_stamp(self.last_good.get("at"))
        eq = self.equation()
        minutes = self.settings.schedule_minutes
        if at is None or eq is None or minutes <= 0:
            return None
        if self.now - at >= timedelta(minutes=2 * minutes):
            return None
        parked = sum(1 for row in self.cache_rows() if row.state == PARKED_STATE)
        if eq["waiting"] or eq["missing"] - parked > 0:
            return None
        nxt = self.next_sync()
        return f"All here · checked {self.when(at)}" + (f" · next sync {nxt}" if nxt else "")

    def waiting_line(self) -> str:
        """What the newest compare left to come, when nothing stops the sync: "2 waiting · 1
        missing · checked 14:20 · next sync 14:40"; before any listing, NO_GOOD_LISTING."""
        eq = self.equation()
        at = parse_stamp(self.compare.get("at"))
        if eq is None or at is None:
            return NO_GOOD_LISTING
        parts = [f"{eq[k]:,} {k}" for k in ("waiting", "missing") if eq[k]]
        parts.append(f"checked {self.when(at)}")
        if nxt := self.next_sync():
            parts.append(f"next sync {nxt}")
        return _cap(" · ".join(parts))

    def consent_stop(self) -> bool:
        """The worker hasn't read a list that reads: no title of a recording not here shows."""
        stop = self.consent.get("stop")
        return not self.consent or (isinstance(stop, str) and bool(stop))

    def titles_hidden(self) -> str | None:
        """Why Not here yet shows no titles now (§9.5, §15.1): a consent stop the tick has seen
        since the compare, a held listing that shows Plaud's IDs changed, or the compare's own."""
        if not self.compare:
            return None
        if self.consent_stop():
            return TITLES_HIDDEN_CONSENT
        if self.hold is not None and self.hold.get("ids_changed") is True:
            return TITLES_HIDDEN_IDS
        hidden = self.compare.get("titles_hidden")
        return hidden if hidden in (TITLES_HIDDEN_CONSENT, TITLES_HIDDEN_IDS) else None

    def cache_rows(self) -> list:
        return self._read_cache()[0]

    def keyed_out(self) -> dict:
        return self._read_cache()[1]

    def _read_cache(self) -> tuple[list, dict]:
        """The cache's compare rows and keyed-out dates (§9.5), read once, read-only. None there
        yet (no listing has run) reads as empty."""
        if self._cache is None:
            from recordings.plaud.cache import Cache

            cache = Cache.read_only(self.settings.state_path) if self.settings.state_path else None
            self._cache = ([], {}) if cache is None else (cache.compare_rows(), cache.keyed_out())
        return self._cache

    def shown_title(self, row: Any) -> str | None:
        """A not-here row's title, the cache's rule applied again before it leaves (P13):
        never for a review row, while titles are hidden, for an ID plaud_listing keeps out or
        re-keyed, an ID the cache last saw consent-listed, or a title a keep-out word matches."""
        from recordings.plaud.cache import title_allowed
        from recordings.plaud.keepout import ConsentList

        if row.title is None or row.review or self.titles_hidden() is not None:
            return None
        listed = self.listing.get(row.canonical_plaud_id)
        if listed is not None and (
            (listed.kept_out_at is not None and not _released(listed))
            or (listed.rekey_of is not None and not _released(listed))
        ):
            return None
        consented = frozenset(pid for pid, k in self.keyed_out().items() if k.kind == CONSENT_KIND)
        known = ConsentList(ids=consented, lines=(), stop=None)
        if not title_allowed(row.canonical_plaud_id, row.title, consent=known, words=self.words):
            return None
        return row.title

    def hold_view(self) -> dict | None:
        """SyncHold from K_HOLD (§11.4): its sentence, and Accept or why it is refused. With
        Plaud's IDs changed, Accept waits while the consent list can't be read (CONSENT_FIRST)."""
        from recordings.plaud.hold import CONSENT_FIRST

        h = self.hold
        if h is None:
            return None
        sentence = h.get("sentence")
        refused = h.get("refused") if isinstance(h.get("refused"), str) else None
        accept = h.get("accept") is True and refused is None
        if accept and h.get("ids_changed") is True and self.consent_stop():
            accept, refused = False, CONSENT_FIRST
        return {
            "sentence": sentence if isinstance(sentence, str) else HOLD_FALLBACK,
            "accept": accept,
            "refused": None if accept else _cap(refused or HOLD_FALLBACK_REFUSED),
        }

    def held_reason(self) -> str | None:
        """Why Sync now and the imports are off now, besides the gate and the app's own reasons:
        a hold (the worker answers "Held: see Status" anyway), or a rate limit's wait."""
        if self.hold is not None:
            return HELD_BUTTON
        if self.rate_until is not None:
            return f"Rate limited until {self.when(self.rate_until)}"
        return None

    def consent_view(self) -> dict | None:
        """What the worker read from the consent list (§12.1): its count, never its IDs (Task 13
        writes `count`), and how many the newest compare found on Plaud."""
        if not self.consent:
            return None
        stop = self.consent.get("stop")
        stopped = isinstance(stop, str) and bool(stop)
        on_plaud = _dict(self.compare.get("consent")).get("on_plaud")
        return {
            "ids": _int(self.consent.get("count")),
            "on_plaud": _int(on_plaud) if isinstance(on_plaud, int) and not stopped else None,
            "file_changed": _stamp(parse_stamp(self.consent.get("file_changed")), self.tz),
            "read_at": self.when(parse_stamp(self.consent.get("read_at"))),
            "stop": stop if stopped else None,
        }

    def schedule_off_since(self) -> datetime | None:
        """The schedule can't have been off before the last turn that ran ended, nor before the
        catch-up, while the gate kept every turn from running (§11.6). None: never ran."""
        stamps = (self.last_turn, self.health.catch_up_done)
        return max((t for t in stamps if t is not None), default=None)


def _summary_of(kind: str, sentence: str, *, explain: str | None = None, see_status: bool) -> dict:
    return {"kind": kind, "sentence": sentence, "explain": explain, "see_status": see_status}


def _rate_until(db: Any, now: datetime) -> datetime | None:
    """A rate limit's wait still running (§10.2; A2's K_RATE_LIMIT): a person's sync or import
    is refused until then (A5), so their buttons say so."""
    from recordings.plaud.health import rate_limit_wait

    return rate_limit_wait(db, now=now)


def _failing_checks(db: Any, tz: str) -> list[dict]:
    """The recordings here on the failing list (PlaudDB.failing_checks), oldest first, as
    FailingCheck rows: the archive ID and the date it began failing, never a title (P13). A row
    whose recording ID isn't an archive ID raises ValueError, so every view says its records
    can't be read (_guarded), never a shorter list (P8)."""
    from recordings.ids import ID_RE

    rows = []
    for row in db.failing_checks():
        if not isinstance(row.recording_id, str) or not ID_RE.fullmatch(row.recording_id):
            raise ValueError("a plaud_checks row's recording_id isn't an archive ID")
        rows.append({"recording_id": row.recording_id, "since": _date(row.failing_since, tz)})
    return rows


# ---- The Sync screen ----------------------------------------------------------------------------


def _summary(kind: str, sentence: str) -> dict:
    return {"kind": kind, "sentence": sentence, "explain": None, "see_status": False}


def _unreadable_summary() -> dict:
    return {"kind": "problem", "sentence": UNREADABLE_SYNC, "explain": None, "see_status": True}


def _empty_sync(reason: str | None) -> dict:
    """The Sync screen before any sync, or where there is nothing to read: every button off, and
    every section a listing fills (the equations, Not here yet, Kept out, Gone, Parked, a hold)
    empty; the breaker stays empty until Phase 3's reader."""
    why = reason or NO_SYNC
    return {
        "demo": False,
        "available": False,
        "reason": why,
        "summary": _summary("no-sync", NO_SYNC),
        "flags": [],
        "hold": None,
        "breaker": None,
        "buttons": {"sync": why, "compare": why, "full": why, "import": why},
        **_handled(),
        "gate": None,
        "listed": False,  # until a listing is compared: the screens then show no counts
        "full_estimate": FULL_UNKNOWN,
        "equation": None,
        "archive": None,
        "consent": None,
        "titles_hidden": None,
        "not_here": [],
        "import_all": {"count": 0, "review_more": 0},
        "kept_out": [],
        "updated": [],
        "gone": {"rows": [], "last_7_days": 0},
        "parked": [],
        "last_result": None,
        "next_sync": None,
    }


def _guarded(build, empty, unreadable):
    """A view from state.db: NoSyncYet without the 2b tables, and a state.db that can't be read,
    or holds a row that can't be (bad JSON, a bad time), gives its own line, never a traceback
    (Status has the restore advice, §9.5). `unreadable` is given the exception's class name."""
    import sqlite3

    from recordings.plaud.checks import NoSyncYet

    try:
        return build()
    except NoSyncYet:
        return empty()
    except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
        return unreadable(type(exc).__name__)


def sync_view(
    settings: PlaudViewSettings | None, *, now: datetime, can_request: str | None
) -> dict:
    """The Sync screen (§15.1), as SyncView. `can_request` is why this app can't make requests
    (cannot_request_reason: the demo, no config.toml, the mirror's copy), or None."""
    if settings is not None and settings.demo_view is not None:
        return demo_view(settings.demo_view)["sync"]
    state = _open(settings)
    if settings is None or state is None:
        return _empty_sync(can_request)

    def unreadable(_name: str) -> dict:
        return {**_empty_sync(UNREADABLE_SYNC), "summary": _unreadable_summary()}

    return _guarded(
        lambda: _sync_from(_Read(settings, state, now), can_request),
        lambda: _empty_sync(can_request),
        unreadable,
    )


def _sync_from(r: _Read, can_request: str | None) -> dict:
    from recordings.plaud.settings import BAD_WORD

    gate = r.gate_reason()
    word = None if r.settings.keep_out_word_ok else BAD_WORD
    # Compare runs whatever the gate says (§11.6). Under a hold, or a rate limit's wait, the worker
    # refuses Sync now and the imports, so they are off with the reason (§15.1).
    held_back = can_request or word or gate or r.held_reason()
    next_sync = r.next_sync()
    return {
        **_empty_sync(can_request),
        "available": can_request is None,
        "reason": can_request,
        "summary": r.summary_view(),
        "flags": r.flag_rows(),
        "hold": r.hold_view(),
        # Each button's own reason; the UI puts not_yet first for a kind not handled yet (G7).
        "buttons": {
            "sync": held_back,
            "compare": can_request,
            "full": held_back,
            "import": held_back,
        },
        "gate": gate,
        "listed": bool(r.compare),
        "equation": r.equation(),
        "archive": r.archive_equation(),
        "consent": r.consent_view(),
        "titles_hidden": r.titles_hidden(),
        "not_here": _not_here(r),
        "import_all": _import_all(r),
        "kept_out": _kept_out(r, next_sync),
        "updated": _updated(r),
        "gone": _gone(r),
        "parked": _parked(r),
        "last_result": _last_result(r),
        "next_sync": next_sync,
    }


def _row_when(r: _Read, row: Any) -> dict:
    """A cache row's date, time and duration, as the Sync screen shows them."""
    return {
        "date": _date(row.start_at, r.tz) or "",
        "time": _hm(row.start_at, r.tz) or "",
        "duration": _minutes(row.duration_ms),
    }


def _not_here(r: _Read) -> list[dict]:
    """Not here yet (§15.1): the newest compare's waiting and missing rows, newest first, each with
    its state; a title only as `shown_title` allows it (P13); Park on one that failed 3 times."""
    rows = []
    for row in r.cache_rows():
        if row.bucket not in ("waiting", "missing"):
            continue
        park = row.state == FAILED_STATE
        state = row.state or ""
        rows.append(
            {
                "plaud_id": row.listed_id,
                "copy_id": _copy_form(row.listed_id),
                **_row_when(r, row),
                "title": r.shown_title(row),
                "arrives_private": row.arrives_private,
                "state": state.removesuffix(" · Park") if park else state,
                "review": row.review,
                "park": park,
            }
        )
    return rows


def _import_all(r: _Read) -> dict:
    counts = _dict(r.compare.get("import_all"))
    return {"count": _int(counts.get("count")), "review_more": _int(counts.get("review_more"))}


def _kept_out(r: _Read, next_sync: str | None) -> list[dict]:
    """Kept out (§15.1): the newest compare's excluded rows, by Plaud ID, date, time and duration,
    never a title. Release on a row kept out by an earlier title or as a possible re-keyed match
    (`release.kept_by`); a row released since reads "Released · comes in at the next sync" with
    Undo (§15.6)."""
    rows = []
    for row in r.cache_rows():
        if row.bucket != "excluded":
            continue
        listed = r.listing.get(row.canonical_plaud_id)
        released = listed is not None and _released(listed) and row.excluded_by in RELEASE_BY
        by = row.excluded_by if row.excluded_by in ("consent", "title", *RELEASE_BY) else "title"
        rows.append(
            {
                "plaud_id": row.listed_id,
                "copy_id": _copy_form(row.listed_id),
                **_row_when(r, row),
                "by": by,
                "release": by in RELEASE_BY and not released,
                "released": _released_line(next_sync) if released else None,
            }
        )
    return rows


def _released_line(next_sync: str | None) -> str:
    """release.released_line, without its " · Undo": the row draws Undo as a button."""
    when = f" ({next_sync})" if next_sync else ""
    return f"Released · comes in at the next sync{when}"


def _gone(r: _Read) -> dict:
    """Gone from Plaud (§12.3, §15.1): newest first, with how many went in the last 7 days."""
    rows = r.db.gone_rows()
    week = r.now - RECENT
    return {
        "rows": [{"recording_id": rid, "since": _date(since, r.tz) or ""} for rid, since in rows],
        "last_7_days": sum(1 for _, since in rows if since is not None and since >= week),
    }


def _parked(r: _Read) -> list[dict]:
    """Parked (§15.1): Plaud IDs parked after failing to import (area A5), and recordings here
    parked from their checks (area B2)."""
    plaud = sorted(
        (row for row in r.listing.values() if row.parked_at is not None),
        key=lambda row: (row.parked_at, row.canonical_plaud_id),
    )
    return [{"id": row.listed_id, "kind": "plaud"} for row in plaud] + [
        {"id": rid, "kind": "recording"} for rid in r.db.parked_recordings()
    ]


def _untitled(r: _Read) -> set[str]:
    """The recordings whose title never reaches a view (P13): one with a privacy flag, open or
    resolved (consent-listed, or kept out by title, after it came in), one Keep it here was pressed
    for, and one the sync re-tagged private only because of a keep-out word."""

    marks = ", ".join("?" for _ in (*TITLE_HIDING_EVENTS, "retagged-private"))
    rows = _q(
        r.state,
        "SELECT recording_id, kind, detail FROM plaud_events WHERE recording_id IS NOT NULL "
        f"AND kind IN ({marks})",
        (*TITLE_HIDING_EVENTS, "retagged-private"),
    )
    hidden = {ev.recording_id for ev in r.flags if ev.recording_id}
    for recording_id, kind, raw in rows:
        if kind != "retagged-private":
            hidden.add(recording_id)
            continue
        try:
            why = _dict(json.loads(raw)).get("why")
        except (TypeError, ValueError):
            why = None
        if why in KEPT_OUT_WHY:
            hidden.add(recording_id)
    return hidden


def _title_for(r: _Read, recording_id: str, hidden: set[str]) -> str | None:
    """The recording's own title, from the file the app last committed, but never for one in
    `hidden`, nor one whose Plaud ID the listing keeps out (by a word or the consent list)."""
    if recording_id in hidden:
        return None
    body = _body(r.state, recording_id)
    if body is None:
        return None
    for ref in _plaud_refs(body):
        row = r.db.listing(ref)
        if row is not None and row.kept_out_at is not None and not _released(row):
            return None
    title = body.get("title")
    return title if isinstance(title, str) else None


def _released(row: Any) -> bool:
    """Release let it in (§12.2): released after it was last kept out, as the compare reads it
    (`compare._released`). A recording Dan released and the sync then imported shows its title
    like any other (Phase 1's phase-end list, Task 19)."""
    if row.released_at is None:
        return False
    return row.kept_out_at is None or row.released_at >= row.kept_out_at


def _updated(r: _Read) -> list[dict]:
    """Updated on Plaud, the last 7 days (§15.1): the marker's `retagged-private` (Phase 1), and
    edit-sync's `updated` (Phase 3). The title is the recording's own, from the file the app last
    committed, but never for a recording with a privacy flag or kept out: one kept out by title or
    consent-listed after it came in shows by ID alone, as everywhere else (P13)."""
    rows, titles = [], {}
    hidden = _untitled(r)
    for ev in r.recent:
        if ev.recording_id is None:
            continue
        if ev.recording_id not in titles:
            titles[ev.recording_id] = _title_for(r, ev.recording_id, hidden)
        parts = ["re-tagged private"]
        if ev.kind == "updated":
            parts = [p for p in ev.detail.get("parts") or [] if isinstance(p, str)] or ["updated"]
        rows.append(
            {
                "recording_id": ev.recording_id,
                "title": titles[ev.recording_id],
                "parts": parts,
                "at": r.when(ev.at),
            }
        )
    return rows


def _last_result(r: _Read) -> str | None:
    """The newest finished request's line, "Keep it here, 14:20: Kept here: …", so a phone that
    slept through the toast still sees it (§15.1). The UI shows this line, never one of its own.
    A preview's answer is its dialog, never a line."""
    rows = _q(
        r.state,
        "SELECT kind, select_all, finished_at, message FROM plaud_requests "
        "WHERE state IN ('done', 'failed') AND kind != 'release-preview' "
        "ORDER BY id DESC LIMIT 1",
    )
    if not rows:
        return None
    kind, select_all, finished, message = rows[0]
    label = "Import all" if kind == "import" and select_all else KIND_LABELS.get(kind, kind)
    return f"{label}, {r.when(parse_stamp(finished))}: {message}"


# ---- Release's question (§12.2; BD1, P6, P8) -------------------------------------------------


def release_preview(
    settings: PlaudViewSettings | None, request_id: str, *, now: datetime
) -> dict | None:
    """The preview for Release's question, for the session that sent the `release-preview`
    request (shiny_app's follow, when its row is done): read and deleted in one writable
    transaction of the cache, keyed by the request's own `request_id` (BD1); its Plaud ID is the
    one the worker wrote from the request's row, never anything the browser sends (P8). Shown once:
    a second read finds nothing. The only title any reply carries (P13). None in the demos, with
    no state folder, or when the preview has gone (10 minutes, or caught by the consent list or a
    keep-out word since)."""
    if settings is None or settings.state_path is None:
        return None
    import sqlite3  # here: the Pages demo's Pyodide has none, and never asks

    from recordings.plaud.cache import take_preview
    from recordings.plaud.checks import K_LAST_ENDED, K_LAST_STARTED, PlaudDB
    from recordings.plaud.gate import gate_now
    from recordings.state import State

    try:
        preview = take_preview(settings.state_path, request_id, now=now)
    except (sqlite3.Error, OSError) as exc:
        log.warning("Release's preview couldn't be read (%s)", type(exc).__name__)
        return None
    if preview is None:
        return None
    tz = settings.timezone_name
    next_sync = None
    try:
        db = PlaudDB(State.read_only(settings.state_path))
        stamps = [parse_stamp(db.get(k)) for k in (K_LAST_STARTED, K_LAST_ENDED)]
        last = max((t for t in stamps if t is not None), default=None)
        next_sync = _next_sync(settings, gate_now(db).open, last, now)
    except Exception as exc:  # noqa: BLE001  the question still shows, without the time
        log.warning("Release's next sync couldn't be read (%s)", type(exc).__name__)
    return {
        "plaud_id": preview.canonical_plaud_id,
        "title": preview.title,
        "date": _date(preview.start_at, tz) or "",
        "time": _hm(preview.start_at, tz) or "",
        "duration": _minutes(preview.duration_ms),
        "next_sync": next_sync,
    }


def _minutes(ms: int | None) -> str | None:
    """A duration as Release's question says it: "12 min", or "40 s" under a minute."""
    if ms is None:
        return None
    seconds = round(ms / 1000)
    return f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


# ---- Status's sync panel ------------------------------------------------------------------------


def _empty_panel() -> dict:
    """Status's panel before any sync. The numbers and details a later phase fills (the listing,
    the sweep, the sign-in, Sync everything, held, gone, failing, parked, the runs) keep these
    empty values until their readers come."""
    return {
        "demo": False,
        "no_sync_yet": True,
        "summary": _summary("no-sync", NO_SYNC),
        "failing": None,
        "failing_checks": [],  # Status's "fails every check" line (Phase 3's checks fill it)
        "not_pinged": None,
        "conditions": [],
        "hold": None,
        "breaker": None,
        **_handled(),
        "listed": False,  # until a listing is compared: Status then shows no zero counts
        "numbers": {
            "equation": None,
            "archive": None,
            "consent": None,
            "last_good_listing": None,
            "next_sync": None,
            "sweep": None,
            "sign_in": None,
            "full": None,
            "held": 0,
            "gone": 0,
            "failing": 0,
            "parked": 0,
            "worker": None,
            "healthchecks": None,
        },
        "details": {
            "normaliser": "",
            "fields": [],
            "census": {},
            "form_change": None,
            "changes_24h": {},
            "named_share": None,
            "runs": [],
            "keep_out_words": [],
            "malformed": 0,
        },
        "attention": [],
    }


def sync_panel(settings: PlaudViewSettings | None, *, now: datetime) -> dict:
    """Status's sync panel (§15.2), as SyncPanel: true sentences first, then the numbers, then
    the details. No title anywhere: recordings are named by ID (P13). `failing_checks` lists the
    recordings that fail every check, from plaud_checks (the module's docstring), and is empty
    when there are none, before any sync, and when the records can't be read."""
    if settings is not None and settings.demo_view is not None:
        return demo_view(settings.demo_view)["panel"]
    state = _open(settings)
    if settings is None or state is None:
        return _empty_panel()

    def unreadable(name: str) -> dict:
        return {
            **_empty_panel(),
            "no_sync_yet": False,
            "summary": _unreadable_summary(),
            "attention": [UNREADABLE_LINE.format(name)],
        }

    return _guarded(lambda: _panel_from(_Read(settings, state, now)), _empty_panel, unreadable)


def _worker(r: _Read) -> tuple[str | None, str | None, str | None]:
    """The worker's last report for the numbers, in plain words; its "silent", "stuck" or
    "couldn't start" reason; and its Needs attention line (§9.2). A start that failed writes the
    phase `failed` and its error's class (Task 13), shown as a class, never as text. The patterns
    pass at start runs as `checking`, its heartbeat carrying `done` and `total` (Task 13), and its
    retry after the start as `rechecking`, with them when it re-reads the whole archive."""
    from recordings.worker import HEARTBEAT_KEY, beat_alive, parse_heartbeat

    raw = r.state.meta(HEARTBEAT_KEY)  # read once: the same value judged alive and reported
    if raw is None:
        return None, "the Plaud worker hasn't reported yet", NO_WORKER_YET
    beat = parse_heartbeat(raw)
    data = beat or {}
    at = parse_stamp(data.get("at"))
    phase = data.get("phase")
    if phase == "failed":
        error = _class_name(data.get("error")) or "Exception"
        report = f"last reported {r.when(at)} (couldn't start: `{error}`)" if at else None
        return report, "the Plaud worker couldn't start", WORKER_FAILED.format(error)
    words = PHASES.get(phase, "working") if isinstance(phase, str) else "working"
    done, total = _int(data.get("done")), _int(data.get("total"))
    if phase in ("checking", "rechecking") and total > 0:
        words = f"{words}, {done:,} of {total:,}"  # §15.2's "(checking, 412 of 1,234)"
    report = f"last reported {r.when(at)} ({words})" if at else None
    ok, why = beat_alive(beat, now=r.now)
    if ok or why is None:
        return report, None, None
    since = f" (last reported {r.when(at)})" if at else ""
    return report, why, f"{_cap(why)}{since}: check that the worker container is running."


def _not_pinged(r: _Read, cause: str | None) -> str | None:
    """§15.2: whenever the last ping is older than one schedule interval plus 10 minutes."""
    stale_after = timedelta(minutes=max(r.settings.schedule_minutes, 0)) + PING_GRACE
    last = r.health.last_ping
    if last is None:
        done = r.health.catch_up_done
        if done is None or r.now - done <= stale_after:
            return None  # a check never pinged stays silent before the catch-up (§11.6)
        return (
            "The sync's Healthchecks check hasn't been pinged since the catch-up: "
            f"{cause or 'no good sync since then'}."
        )
    if r.now - last[0] <= stale_after:
        return None
    return (
        f"The sync's Healthchecks check isn't being pinged since {r.when(last[0])}: "
        f"{cause or 'no good sync since then'}. It emails you about 2 hours after that."
    )


def _panel_from(r: _Read) -> dict:
    from recordings.plaud.flags import CONSENT_LATE, KEPT_OUT_LATE, PRIVATE_UNWRITTEN, SENTENCES
    from recordings.plaud.normalise import NORMALISER_VERSION
    from recordings.plaud.runlock import plaud_holder
    from recordings.plaud.settings import BAD_WORD

    tz, now, health = r.tz, r.now, r.health
    report, silent, worker_line = _worker(r)
    summary = r.summary_view()
    gate = r.gate_reason()
    cause = r.cause() or silent
    said_since = "failing (since" in summary["sentence"]
    if said_since and (cause or "").startswith("the sync is failing (since"):
        cause = "the sync is failing"  # its time is the summary's (P12)
    failing = None
    if health.failing is not None:
        since, why = health.failing
        # A turn that ran records its run phrase as `result` (Task 13). Phase 2 adds "Last good
        # listing 11:40." with its reader of the listing (§15.2).
        detail = health.last_turn[2].get("result") if health.last_turn else None
        detail = detail if isinstance(detail, str) and detail else CAUSES.get(why, why)
        # The summary says "the sync is failing (since …)" when failing is its cause: the time is
        # said once, there (P12).
        failing = (
            f"Plaud sync failing ({detail})."
            if said_since
            else f"Plaud sync failing since {r.when(since)} ({detail})."
        )
        good_at = parse_stamp(r.last_good.get("at"))
        if good_at is not None:
            failing += f" Last good listing {r.when(good_at)}."  # §15.2
    disk = health.failing is not None and health.failing[1] == "disk"
    stop = r.consent.get("stop")
    consent_stop = (
        f"Plaud imports stopped: {stop if isinstance(stop, str) and stop else CONSENT_UNREADABLE}."
    )
    late = {
        CONSENT_LATE: "Recording {} is on the consent list, but in the archive. "
        "Take it out by hand (runbook).",
        KEPT_OUT_LATE: "Recording {} was kept out by title after it came in. "
        "Take it out by hand (runbook), or Keep it here.",
        PRIVATE_UNWRITTEN: "Recording {} should be private, but its file was edited outside "
        "the app: run `recordings reindex`.",
    }

    def flag_line(ev: Event) -> str:
        said = _sentence(ev)  # names the recording itself when its cause needs its own remedy
        return said if said != SENTENCES[ev.kind] else late[ev.kind].format(ev.recording_id)

    flag_lines = [flag_line(ev) for ev in r.flags if ev.kind in late]
    off_since = r.schedule_off_since() if r.settings.schedule_minutes <= 0 else None

    conditions: list[str] = []
    if "consent" in health.blockers:
        conditions.append(consent_stop)
    conditions += flag_lines
    if disk:
        conditions.append("Plaud imports stopped: the disk is too full to import.")
    if gate:
        conditions.append(f"{gate}.")
    if r.settings.schedule_minutes <= 0:
        conditions.append(
            f"Scheduled sync is off since {_stamp(off_since, tz)}."
            if off_since
            else "Scheduled sync is off."
        )
    if not r.settings.auto_import:
        conditions.append("Auto-import is off: new recordings wait for you on the Sync screen.")
    if not r.settings.keep_out_word_ok:
        conditions.append(f"Plaud sync is idle: {BAD_WORD}.")
    absent = r.small_shrink.get("absent")
    if r.hold is None and isinstance(absent, list) and absent:
        conditions.append(_shrink_line(len(absent)))  # hold.shrink_line (§11.4 step 5)

    attention: list[str] = []
    if health.failing is not None and now - health.failing[0] > FAILING_FOR:
        attention.append(f"Plaud sync has been failing since {r.when(health.failing[0])}.")
    attention += _sign_in_lines(r)
    if r.hold is not None:
        held_since = parse_stamp(r.hold.get("since"))
        since = f" since {r.when(held_since)}" if held_since else ""
        attention.append(
            f"Plaud sync is held{since}: see the Plaud sync panel to Accept this listing, "
            "or why it can't be."
        )
    for ev in r.rekeyed:
        row = r.listing.get(ev.canonical_plaud_id or "")
        listed = row.listed_id if row is not None else ev.canonical_plaud_id
        attention.append(f"Plaud ID {listed}: {_rekey_sentence(ev)}.")
    review = sum(1 for row in r.cache_rows() if row.review and row.bucket != "excluded")
    if r.rebuilt and review:
        attention.append(
            REBUILT_LINE.format(n=_plural(review, "recording", "recordings"), s="s" * (review == 1))
        )
    if disk:
        attention.append("Plaud imports stopped: the disk is too full to import.")
    if "consent" in health.blockers:
        attention.append(consent_stop)
    if r.unwritten_error:
        attention.append(_cap(UNWRITTEN_STOPPED.format(r.unwritten_error)) + ".")
    if r.patterns_error:
        attention.append(_cap(_patterns_stopped(r.patterns_error)) + ".")
    attention += flag_lines
    if worker_line:
        attention.append(worker_line)
    holder = plaud_holder(r.state)
    if holder is not None:
        job, since = holder
        limit = STUCK_AFTER_LONG if job in LONG_JOBS else STUCK_AFTER
        if now - since > limit:  # a start in the future (a clock put right) is never stuck
            label = JOB_LABELS.get(job, job)
            attention.append(f"{label} has run since {r.when(since)}: it may be stuck.")
    if (
        off_since is not None
        and health.catch_up_done is not None
        and now - off_since >= SCHEDULE_OFF_FOR
    ):
        attention.append(
            f"Scheduled sync has been off since {_stamp(off_since, tz)}: set [plaud] "
            "schedule_minutes to turn it back on, or pause the sync's Healthchecks check (runbook)."
        )
    failed_since = parse_stamp(r.ping.get("failed_since"))
    if (
        r.ping.get("ok") is False
        and failed_since is not None
        and now - failed_since >= PING_FAILING_FOR
    ):
        attention.append(PING_FAILING)
    # P12: a condition the summary line already says, or Needs attention already holds (Status
    # draws that first), isn't said again under it.
    conditions = [
        c for c in conditions if c.removesuffix(".") != summary["sentence"] and c not in attention
    ]

    healthchecks = None
    if health.last_ping is not None:
        at, ok, status = health.last_ping
        healthchecks = (
            f"last pinged {r.when(at)}"
            if ok
            else f"last ping failed {r.when(at)} "
            f"({f'HTTP {status}' if status is not None else 'unreachable'})"
        )
    panel = _empty_panel()
    good_at = parse_stamp(r.last_good.get("at"))
    last_good = (
        f"{r.when(good_at)} ({_int(r.last_good.get('count')):,} listed)" if good_at else None
    )
    not_imported = sum(1 for row in r.cache_rows() if row.state == FAILED_STATE)
    panel["numbers"].update(
        {
            "equation": r.equation(),
            "archive": r.archive_equation(),
            "consent": r.consent_view(),
            "last_good_listing": last_good,
            "next_sync": r.next_sync(),
            "sign_in": _sign_in_number(r),
            "gone": len(r.db.gone_rows()),
            "failing": len(r.failing_checks) + not_imported,
            "parked": r.parked_count() + len(r.db.parked_recordings()),
            "worker": report,
            "healthchecks": healthchecks,
        }
    )
    census = _dict(r.last_good.get("census")) or _dict(r.compare.get("census"))
    changed = parse_stamp(r.last_good.get("form_changed_at"))
    panel["details"].update(
        {
            "normaliser": str(NORMALISER_VERSION),
            "keep_out_words": r.words,
            "census": {str(k): _int(v) for k, v in census.items()},
            # §15.2: a change of form that held nothing, said once the listing is good again.
            "form_change": (
                f"Plaud's IDs changed form on {_date(changed, tz)}; every ID still matches"
                if changed is not None and r.hold is None
                else None
            ),
            "malformed": _int(r.compare.get("malformed")),
            "runs": _runs(r),
        }
    )
    panel.update(
        {
            "no_sync_yet": False,
            "listed": bool(r.compare),
            "summary": summary,
            "failing": failing,
            "failing_checks": r.failing_checks,
            "not_pinged": _not_pinged(r, cause),
            "conditions": conditions,
            "hold": r.hold_view(),
            "attention": attention,
        }
    )
    return panel


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _shrink_line(n: int) -> str:
    """hold.shrink_line, kept here: plaud.hold imports sqlite3 (a test pins the two)."""
    return f"Plaud now lists {n:,} fewer: waiting for the next listing to agree."


RUN_JOBS = ("plaud-sync", "plaud-catch-up")
RUNS_SHOWN = 20  # §15.2's "the last 20 runs with their results"


def _runs(r: _Read) -> list[dict]:
    """The last 20 runs (§15.2's details), from `ops_runs`: when each ended and its §15.6
    phrase, which never carries a title."""
    rows = _q(
        r.state,
        "SELECT finished_at, detail FROM ops_runs WHERE job IN (?, ?) ORDER BY id DESC LIMIT ?",
        (*RUN_JOBS, RUNS_SHOWN),
    )
    return [{"at": _stamp(parse_stamp(at), r.tz) or "", "result": str(d)} for at, d in rows]


def _sign_in_state(r: _Read) -> tuple[bool, datetime | None]:
    """Whether the sign-in is needed now, and since when: Plaud refused it (K_SIGN_IN's
    `refused_at`, with its `sign-in` blocker), or the last scheduled turn found none (§10.3)."""
    refused = parse_stamp(r.sign_in.get("refused_at"))
    blocker = r.health.blockers.get("sign-in")
    paused = r.health.last_turn is not None and r.health.last_turn.outcome == "paused"
    needed = refused is not None or blocker is not None or paused
    return needed, refused or blocker


def _sign_in_number(r: _Read) -> str | None:
    """Status's sign-in number (§15.2): needed, or works with its expiries (A1's K_SIGN_IN)."""
    needed, since = _sign_in_state(r)
    if needed:
        return f"needed{f' since {r.when(since)}' if since else ''}: {SIGN_IN_FIX}"
    ok_at = parse_stamp(r.sign_in.get("ok_at"))
    if ok_at is None:
        return None
    parts = [f"works (last used {r.when(ok_at)})"]
    if (expires := parse_stamp(r.sign_in.get("expires_at"))) is not None:
        parts.append(f"access token expires {r.when(expires)}")
    if r.sign_in.get("refresh_token") is True:
        later = parse_stamp(r.sign_in.get("refresh_expires_at"))
        parts.append(f"refresh token expires {_stamp(later, r.tz)}" if later else "refresh token")
    else:
        parts.append("no refresh token")
    return " · ".join(parts)


def _sign_in_lines(r: _Read) -> list[str]:
    """Needs attention's sign-in lines (§15.2): a sign-in needed, or one expiring within 7 days
    (BD3: the refresh token's own expiry when it has one, else the access token's)."""
    needed, since = _sign_in_state(r)
    if needed:
        at = f" since {r.when(since)}" if since else ""
        line = f"Plaud sign-in needed{at}. New recordings wait on Plaud: nothing is lost."
        return [f"{line} {SIGN_IN_FIX}"]
    if r.sign_in.get("refresh_token") is True:
        ends = parse_stamp(r.sign_in.get("refresh_expires_at"))
    else:
        ends = parse_stamp(r.sign_in.get("expires_at"))
    if ends is None or ends - r.now > SIGN_IN_SOON:
        return []
    verb = "expired" if ends <= r.now else "expires"
    return [f"The Plaud sign-in {verb} {_stamp(ends, r.tz)}. {SIGN_IN_FIX}"]


# ---- The Library's arrivals cue ---------------------------------------------------------------


def arrivals(settings: PlaudViewSettings | None, *, since: datetime, now: datetime) -> dict:
    """The Library's cue (§15.3, P10): what came in since `since` (the page's load or its last
    Refresh, decision 22), and the open privacy flags, by count only."""
    zero = {"new": 0, "private": 0, "updated": 0, "kept_out_late": 0, "consent_late": 0}
    if settings is not None and settings.demo_view is not None:
        return demo_view(settings.demo_view)["arrivals"]
    state = _open(settings)
    if state is None:
        return zero

    def build() -> dict:
        from recordings.plaud.checks import FLAG_KINDS, PlaudDB

        db = PlaudDB(state)
        seen = Counter(e.kind for e in db.events_since(since, ARRIVAL_EVENTS) if e.at <= now)
        flags = Counter(e.kind for e in db.open_flags(FLAG_KINDS))
        return {
            "new": seen["imported"],
            "private": seen["arrived-private"],
            "updated": seen["updated"] + seen["retagged-private"],
            "kept_out_late": flags["kept-out-late"],
            "consent_late": flags["consent-late"],
        }

    return _guarded(build, lambda: zero, lambda _name: zero)


# ---- Details' Plaud lines ---------------------------------------------------------------------


def recording_plaud(
    settings: PlaudViewSettings | None, recording_id: str, *, now: datetime
) -> dict | None:
    """Details' Plaud lines (§15.3), as RecordingPlaud; None for a recording with no Plaud
    source, and in the demos, whose canned view has no recordings of its own. When it was checked
    and when next, and gone from Plaud, come with the checks (Phase 3): None until then."""
    if settings is None or settings.demo_view is not None:
        return None
    state = _open(settings)
    if state is None:
        return None
    import sqlite3

    from recordings.plaud.checks import NoSyncYet

    try:
        body = _body(state, recording_id)
    except (sqlite3.DatabaseError, ValueError, TypeError):
        return None
    refs = _plaud_refs(body)
    if not refs:
        return None
    from recordings.refs import canonical_plaud_id

    seen: set[str] = set()
    unique = []
    for ref in refs:  # one line per recording on Plaud, whichever form its sources use
        if canonical_plaud_id(ref) not in seen:
            seen.add(canonical_plaud_id(ref))
            unique.append(ref)
    bare = {
        "plaud_ids": [{"id": ref, "copy_id": _copy_form(ref)} for ref in unique],
        "checked": None,
        "next_by": None,
        "gone_since": None,
        "tagged_private": None,
        "removed_private": None,
        "flags": [],
        "resync": None,
        **_handled(),
    }
    try:
        return {**bare, **_recording_lines(settings, state, recording_id, body or {}, unique)}
    except NoSyncYet:
        return bare  # before any sync: the IDs, for `make consent-add`
    except (sqlite3.DatabaseError, ValueError, TypeError):
        return bare


def _recording_lines(
    settings: PlaudViewSettings,
    state: State,
    recording_id: str,
    body: dict,
    refs: list[str],
) -> dict:
    from recordings.models import is_private_tag
    from recordings.plaud.checks import PlaudDB
    from recordings.plaud.flags import CONSENT_LATE
    from recordings.plaud.gate import gate_now

    tz = settings.timezone_name
    db = PlaudDB(state)
    plaud_ids, kept_out = [], False
    for ref in refs:
        row = db.listing(ref)
        listed = row.listed_id if row is not None else ref
        kept_out = kept_out or (row is not None and row.kept_out_at is not None)
        plaud_ids.append({"id": listed, "copy_id": _copy_form(listed)})
    tags = [
        t for t in body.get("tags") or [] if isinstance(t, dict) and isinstance(t.get("tag"), str)
    ]
    private_now = any(is_private_tag(t["tag"]) for t in tags)
    auto_private = any(is_private_tag(t["tag"]) and t.get("by") == "auto" for t in tags)
    events = _q(
        state,
        "SELECT at, kind, detail FROM plaud_events WHERE recording_id = ? AND kind IN (?, ?) "
        "ORDER BY id DESC LIMIT 1",
        (recording_id, *PRIVATE_EVENTS),
    )
    tagged = removed = None
    if events:
        raw_at, kind, raw_detail = events[0]
        try:
            detail = _dict(json.loads(raw_detail))
        except (TypeError, ValueError):
            detail = {}
        on = _date(parse_stamp(raw_at), tz)
        reason = (
            ARRIVED_PRIVATE if kind == "arrived-private" else WHY_DETAILS.get(detail.get("why"))
        )
        if private_now:
            tagged = f"Tagged private by the sync on {on}" + (f": {reason}" if reason else "")
        else:  # decision 3, made visible
            when = _removed_on(state, recording_id, tz)
            removed = (
                f"Tagged private by the sync on {on}. You removed it"
                f"{f' on {when}' if when else ''}. It comes back only if its Plaud title "
                "changes to a private one."
            )
    elif auto_private:
        tagged = "Tagged private by the sync"  # decision 15: its event aged out after 90 days
    flags = [_flag(db, ev) for ev in db.open_flags(FLAG_EVENTS) if ev.recording_id == recording_id]
    check = db.check_row(recording_id)
    gone = check.gone_since if check is not None else None
    gate = gate_now(db)
    if not gate.open:
        resync = gate.reason
    elif any(f["kind"] == CONSENT_LATE for f in flags):
        resync = "Not checked: on the consent list"
    elif kept_out:
        resync = "Not checked: kept out"
    else:
        resync = None
    return {
        "plaud_ids": plaud_ids,
        # §15.3's "Gone from Plaud since 2026-10-23 (kept)": the UI words it from the date.
        "gone_since": _date(gone, tz),
        "tagged_private": tagged,
        "removed_private": removed,
        "flags": flags,
        "resync": resync,
    }


def _removed_on(state: State, recording_id: str, tz: str) -> str | None:
    """The date of the revision that took the recording's `private` away (§15.3), from the merge
    bases state.db keeps (KEEP_REVISIONS); None once it has aged out of them."""
    from recordings.ids import relative_dir
    from recordings.models import is_private_tag

    rel = (relative_dir(recording_id) / "recording.json").as_posix()
    rows = _q(
        state,
        "SELECT body, written_at FROM revisions WHERE file = ? AND committed = 1 ORDER BY id",
        (rel,),
    )
    found, before = None, False
    for raw, written in rows:
        try:
            tags = _dict(json.loads(raw)).get("tags") or []
        except (TypeError, ValueError):
            continue
        private = any(isinstance(t, dict) and is_private_tag(str(t.get("tag", ""))) for t in tags)
        if before and not private:
            found = parse_stamp(written)
        before = private
    return _date(found, tz)


# ---- The canned view (decision 23) --------------------------------------------------------------


def demo_view(path: Path) -> dict:
    """The canned view, hand-written in demo/plaud-sync.json, as {"sync", "panel", "arrivals"}:
    the UI's contract for every section, Phase 2's and 3's included. Whatever it says, every
    action is off and the demo's notes show (§15.5). It never raises: a missing or broken file
    gives an empty canned view, so the demo still starts."""
    try:
        raw = _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raw = {}
    sync = _empty_sync(DEMO_NOTE)
    sync.update({k: v for k, v in _dict(raw.get("sync")).items() if k in sync})
    sync.update(
        {
            "demo": True,
            "available": False,
            "reason": DEMO_NOTE,
            "summary": _summary("demo", DEMO_NOTE),
            "buttons": dict.fromkeys(("sync", "compare", "full", "import"), DEMO_NOTE),
            **_handled(),
            "gate": None,
            "flags": [{**_dict(f), "keep_here": False} for f in sync["flags"] or []],
            "not_here": [{**_dict(row), "park": False} for row in sync["not_here"] or []],
            "kept_out": [{**_dict(row), "release": False} for row in sync["kept_out"] or []],
            "hold": {**sync["hold"], "accept": False, "refused": DEMO_NOTE}
            if isinstance(sync["hold"], dict)
            else None,
            "breaker": {**sync["breaker"], "accept": False}
            if isinstance(sync["breaker"], dict)
            else None,
        }
    )
    panel = _empty_panel()
    panel.update({k: v for k, v in _dict(raw.get("panel")).items() if k in panel})
    panel.update(
        {
            "demo": True,
            "no_sync_yet": False,
            "summary": _summary("demo", DEMO_STATUS),
            **_handled(),
            "failing_checks": [_dict(row) for row in panel["failing_checks"] or []],
            "hold": {**panel["hold"], "accept": False, "refused": DEMO_NOTE}
            if isinstance(panel["hold"], dict)
            else None,
            "breaker": {**panel["breaker"], "accept": False}
            if isinstance(panel["breaker"], dict)
            else None,
            "attention": [],  # the demo needs nothing of anyone
        }
    )
    counts = _dict(raw.get("arrivals"))
    keys = ("new", "private", "updated", "kept_out_late", "consent_late")
    return {"sync": sync, "panel": panel, "arrivals": {k: _int(counts.get(k)) for k in keys}}


# ---- The request input's replies (Task 20; 2b spec §9.3) ---------------------------------------

POLL_SECONDS = 2.0  # a pending row is read this often (§9.3); a test shortens it, never sleeps
FINAL_STATES = ("done", "failed", "expired", "refused")
# How long a session looks for a row after `submit`'s backstop answered NOT_SAVED: a commit that
# hung past the deadline may still land it (Task 14's review).
UNSAVED_WINDOW = 60.0


def clock() -> datetime:
    """Now, for the session's inserts and outputs; a test replaces it."""
    return datetime.now(UTC)
