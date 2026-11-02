"""Edit-sync's circuit breaker (stage 2b spec §14.4, decision 11; area B1).

- **What counts** (the check decides, `plaud.editsync`): a check whose only changed part is
  "other", and a check that found a part volatile (different between two fetches a moment apart).
  Never an import, a recording's first check after its baseline, a suppressed check, or a stable
  change to a named part.
- **The window** is the last 24 hours, from `max(now − 24 h, window_start)`. **It trips** when, in
  the window, at least 10 recordings counted, and they are more than 5% of the recordings checked
  (a check that failed checked nothing). `evaluate` runs at each sync's end (§11.1's "the breaker").
- **While it is open** a check whose change lies only in what counted is suppressed (the check
  does it), the `breaker` blocker holds the sync's ping (§16), and everything else goes on:
  listings, imports, title checks, `private` tags, snapshots with any other change.
- **Accept these changes** is offered only when every counted check's change was in "other" and
  stable (no part found volatile), and none of the fields was accepted before: a field that trips
  it again after an Accept keeps changing by itself, and needs a code fix. Pressed (`accept`), it
  does what `baseline --all` then `breaker reset` do: every recording's next check is excused and
  spread over `full_sweep_days`, so each gets one new snapshot over the next pass, and the window
  starts now. The fields accepted are kept, and the press goes to `ops_runs`.
- **A release that bumps `NORMALISER_VERSION` closes it by itself** at the worker's start
  (`close_on_release`): the trip records the version it saw. Status then says "closed by the
  release on 2026-11-02; watching for a re-trip" for a day.
- **`breaker reset`** closes it and starts the window now, excusing nothing: a tool no runbook step
  needs.
- **Stored** in `plaud_state` (`K_BREAKER`), so a restart doesn't clear it: `{"open", "since",
  "window_start", "recordings", "checked", "fields", "parts", "accept", "normaliser",
  "accepted", "closed": {"at", "by", "version"}}`. Field and part names only, never values.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from recordings.plaud.checks import K_BREAKER, UNCHANGED, PlaudDB
from recordings.plaud.when import local_hhmm, parse_stamp

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)
TRIP_AT = 10  # recordings counted, at least
TRIP_SHARE = 0.05  # and more than this share of the recordings checked
SHOW_CLOSED_FOR = WINDOW  # "closed by the release on …; watching for a re-trip"
CHECKED = ("up to date", "updated", "degraded", "suppressed")  # a failed check checked nothing
BLOCKER = "breaker"
PHRASE = "paused: unexpected changes"  # §15.6: the run's and the blocker's phrase
CODE_FIX = "This keeps changing by itself: it needs a code fix"
STILL_ARRIVE = "New recordings still arrive."
ACCEPT_NOTE = (
    "If Plaud added a feature, accept it: each recording gets one new snapshot. If a name looks "
    "like a link or a key, don't: it needs a code fix (a Claude session)."
)
OPEN_NOTE = (
    "While this is open, the sync's check stays down, so a new problem won't send another email: "
    "glance at Status until it's fixed."
)
ACCEPTED = "Accepted: each recording gets one new snapshot"
NOTHING_OPEN = "Nothing to accept: edit-sync isn't paused"
RESET = "The breaker is closed: the window starts now"
NOT_OPEN = "The breaker isn't open: nothing to reset"
# How a volatile part reads in Status's sentence: "12 recordings' transcripts change …"
PLURALS = {
    "title": "titles",
    "transcript": "transcripts",
    "speakers": "speaker names",
    "notes": "notes",
    "own notes": "own notes",
    "other": "other fields",
}


def record(db: PlaudDB) -> dict[str, Any]:
    """The breaker as stored; closed, with nothing accepted, when nothing is. A value that isn't
    an object reads as open, with no Accept: it fails closed (P8)."""
    value = db.get(K_BREAKER)
    if value is None:
        return {"open": False}
    if not isinstance(value, dict):
        return {"open": True, "accept": False, "unreadable": True}
    return dict(value)


def is_open(db: PlaudDB) -> bool:
    value = record(db)
    return value.get("open") is True or value.get("unreadable") is True


def window_from(value: Mapping[str, Any], now: datetime) -> datetime:
    start = parse_stamp(value.get("window_start"))
    return max(now - WINDOW, start) if start is not None else now - WINDOW


def _names(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


@dataclass(frozen=True)
class Measure:
    counted: int  # recordings that counted in the window
    checked: int  # recordings checked in the window
    fields: tuple[str, ...]  # the counted checks' "other" field names
    volatile: tuple[str, ...]  # the parts the counted checks found volatile

    @property
    def trips(self) -> bool:
        return self.counted >= TRIP_AT and self.counted > TRIP_SHARE * self.checked


def measure(db: PlaudDB, *, now: datetime) -> Measure:
    """The window's counts (the module's notes), from `plaud_check_log`. A window that starts at
    an Accept or a reset leaves out the checks logged up to that instant."""
    value = record(db)
    start = window_from(value, now)
    rows = db.check_log_since(start)
    if parse_stamp(value.get("window_start")) == start:
        rows = [r for r in rows if r.at is not None and r.at > start]
    counted = {r.recording_id for r in rows if r.counted}
    checked = {r.recording_id for r in rows if r.result in CHECKED}
    fields = sorted({f for r in rows if r.counted for f in r.fields})
    volatile = sorted({p for r in rows if r.counted for p in r.volatile})
    return Measure(len(counted), len(checked), tuple(fields), tuple(volatile))


def evaluate(db: PlaudDB, *, now: datetime, normaliser: int | None = None) -> bool:
    """§11.1's "the breaker", at a sync's end: trip it when the window says so. True when it
    tripped now. An open breaker stays as it is (suppressed checks don't count again)."""
    from recordings.plaud.health import open_blocker
    from recordings.plaud.normalise import NORMALISER_VERSION

    if is_open(db):
        return False
    m = measure(db, now=now)
    if not m.trips:
        return False
    version = NORMALISER_VERSION if normaliser is None else normaliser

    def trip(old: object) -> object:
        old = dict(old) if isinstance(old, dict) else {}
        if old.get("open") is True:
            return UNCHANGED
        accepted = _names(old.get("accepted"))
        again = bool(set(m.fields) & set(accepted))
        return {
            **old,
            "open": True,
            "since": now.isoformat(),
            "recordings": m.counted,
            "checked": m.checked,
            "fields": list(m.fields),
            "parts": list(m.volatile),
            "accept": not m.volatile and not again,
            "again": again,
            "normaliser": version,
            "accepted": accepted,
            "closed": None,
        }

    db.update(K_BREAKER, trip, now=now)
    open_blocker(db, BLOCKER, now=now, detail={"recordings": m.counted})
    log.warning(
        "edit-sync paused: %d recordings changed unexpectedly (%s)",
        m.counted,
        ", ".join(m.volatile or m.fields) or "other",
    )
    return True


def _close(db: PlaudDB, *, now: datetime, by: str, add: Iterable[str] = ()) -> bool:
    """Close it, start the window now, and clear the blocker. True when it was open."""
    from recordings.plaud.health import clear_blocker
    from recordings.plaud.normalise import NORMALISER_VERSION

    was = {"open": False}

    def close(stored: object) -> object:
        was["open"] = not isinstance(stored, dict) or stored.get("open") is True
        old = dict(stored) if isinstance(stored, dict) else {}
        accepted = _names(old.get("accepted"))
        accepted += [f for f in add if f not in accepted]
        return {
            **old,
            "open": False,
            "window_start": now.isoformat(),
            "accepted": accepted,
            "closed": {"at": now.isoformat(), "by": by, "version": NORMALISER_VERSION},
        }

    db.update(K_BREAKER, close, now=now)
    clear_blocker(db, BLOCKER)
    return was["open"]


def reset(db: PlaudDB, *, now: datetime) -> str:
    """`recordings plaud breaker reset`: close it, start the window now, excuse nothing."""
    if not is_open(db):
        return NOT_OPEN
    _close(db, now=now, by="reset")
    log.info("the breaker was reset")
    return RESET


@dataclass(frozen=True)
class Accepted:
    ok: bool
    message: str
    fields: tuple[str, ...] = ()
    rebaselined: int = 0


def accept_changes(db: PlaudDB, *, now: datetime, full_sweep_days: int) -> Accepted:
    """Accept these changes (decision 11): refused while it isn't open, and where no Accept is
    offered (a volatile part, or a field accepted before); otherwise every recording's next check
    is excused and spread over `full_sweep_days` (a rebaseline), then the breaker closes with its
    window starting now, and its fields are kept as accepted."""
    from recordings.plaud import sweep

    value = record(db)
    if not is_open(db):
        return Accepted(False, NOTHING_OPEN)
    if value.get("accept") is not True:
        return Accepted(False, CODE_FIX)
    fields = tuple(_names(value.get("fields")))
    count = sweep.rebaseline(db, now=now, full_sweep_days=full_sweep_days)
    _close(db, now=now, by="accept", add=fields)
    log.info("accepted these changes (%s): %d recordings rebaselined", ", ".join(fields), count)
    return Accepted(True, ACCEPTED, fields, count)


def close_on_release(db: PlaudDB, *, now: datetime, version: int | None = None) -> bool:
    """At the worker's start: a breaker the trip recorded under an older normaliser closes by
    itself (§14.4). True when it did."""
    from recordings.plaud.normalise import NORMALISER_VERSION

    current = NORMALISER_VERSION if version is None else version
    value = record(db)
    seen = value.get("normaliser")
    if value.get("open") is not True or not isinstance(seen, int) or seen >= current:
        return False
    _close(db, now=now, by="release")
    log.info("the breaker was closed by the release (normaliser %d)", current)
    return True


# ---- what Status and the Sync screen say (§14.4, §15.1, §15.2) ---------------------------------
def view(value: Mapping[str, Any], *, tz: tzinfo) -> dict | None:
    """SyncBreaker (types.ts), or None while it is closed: the sentence, the field names, Accept
    or why it needs a code fix, and what to know meanwhile. Names only, never values."""
    if value.get("open") is not True and value.get("unreadable") is not True:
        return None
    since = parse_stamp(value.get("since"))
    when = f" since {local_hhmm(since, tz)}" if since is not None else ""
    n = value.get("recordings")
    n = n if isinstance(n, int) and not isinstance(n, bool) else 0
    parts = _names(value.get("parts"))
    accept = value.get("accept") is True
    if parts:
        what = " and ".join(PLURALS.get(p, p) for p in parts)
        sentence = (
            f"Edit-sync paused{when}: {n:,} recordings' {what} change between two fetches a "
            "moment apart"
        )
        fields: list[str] = []
    else:
        sentence = (
            f"Edit-sync paused{when}: Plaud started sending something new on {n:,} recordings"
        )
        fields = _names(value.get("fields"))
    return {
        "sentence": sentence,
        "fields": fields,
        "accept": accept,
        "code_fix": None if accept else CODE_FIX,
        "note": f"{STILL_ARRIVE} {ACCEPT_NOTE}" if accept else STILL_ARRIVE,
    }


def closed_line(value: Mapping[str, Any], *, now: datetime, tz: tzinfo) -> str | None:
    """Status's line for a day after a release closed it: "Edit-sync's breaker was closed by the
    release on 2026-11-02; watching for a re-trip"."""
    closed = value.get("closed")
    if value.get("open") is True or not isinstance(closed, dict) or closed.get("by") != "release":
        return None
    at = parse_stamp(closed.get("at"))
    if at is None or not timedelta(0) <= now - at < SHOW_CLOSED_FOR:
        return None
    return (
        f"Edit-sync's breaker was closed by the release on {at.astimezone(tz):%Y-%m-%d}; "
        "watching for a re-trip."
    )


__all__ = [
    "ACCEPTED",
    "CODE_FIX",
    "PHRASE",
    "Accepted",
    "Measure",
    "accept_changes",
    "close_on_release",
    "closed_line",
    "evaluate",
    "is_open",
    "measure",
    "record",
    "reset",
    "view",
]
