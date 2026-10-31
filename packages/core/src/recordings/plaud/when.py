"""Times as the Plaud sync stores and shows them (stage 2b): one parser for the ISO stamps kept in
`state.db`, and one local "HH:MM". Sync health, the worker, the request replies, the CLI and the
views all read a stamp through here, so they never disagree about one. Standard library only, with
no sqlite3, so Pyodide (the Pages demo) imports it.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo


def _aware(when: datetime) -> datetime:
    """The one rule for a naive datetime: it is UTC."""
    return when if when.utcoffset() is not None else when.replace(tzinfo=UTC)


def parse_stamp(value: object) -> datetime | None:
    """An ISO 8601 stamp, or a datetime, as an aware datetime: a naive one is UTC. None for None,
    an empty string or anything else that isn't a stamp, never an exception."""
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return _aware(when)


def local_hhmm(dt: datetime, tz: tzinfo) -> str:
    """`14:20`: the time in `tz`. A naive datetime is UTC, as `parse_stamp` reads one."""
    return _aware(dt).astimezone(tz).strftime("%H:%M")
