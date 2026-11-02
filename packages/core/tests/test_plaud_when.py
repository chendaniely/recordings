"""One time parser and one local "HH:MM" for the Plaud sync (`plaud/when.py`): every reader reads
a stored stamp the same way. Every time here is invented."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from recordings.plaud.when import local_hhmm, parse_stamp, utc_iso

VANCOUVER = ZoneInfo("America/Vancouver")
T = datetime(2026, 10, 23, 21, 20, tzinfo=UTC)


def test_an_iso_stamp_reads_as_an_aware_datetime_and_a_naive_one_as_utc():
    # why: the plan council's M6. Sync health read a naive stamp as UTC and the worker read it as
    # missing, so the schedule and Status could disagree about one last_started. One rule now.
    assert parse_stamp("2026-10-23T21:20:00+00:00") == T
    assert parse_stamp("2026-10-23T21:20:00") == T
    offset = parse_stamp("2026-10-23T14:20:00-07:00")
    assert offset == T and offset.utcoffset() == timedelta(hours=-7)
    assert parse_stamp(T.replace(tzinfo=None)) == T  # a stored datetime, naive
    assert parse_stamp(T) is T


@pytest.mark.parametrize(
    "junk", [None, "", "yesterday", "2026-13-40T00:00:00", 7, 1.5, True, [], {}]
)
def test_none_or_junk_reads_as_none(junk):
    assert parse_stamp(junk) is None


def test_local_hhmm_is_the_time_in_the_zone_given():
    assert local_hhmm(T, VANCOUVER) == "14:20"
    assert local_hhmm(T, ZoneInfo("Asia/Kolkata")) == "02:50"  # +05:30, the next day
    assert local_hhmm(T.replace(tzinfo=None), VANCOUVER) == "14:20"  # naive: UTC
    assert local_hhmm(T, UTC) == "21:20"


def test_it_needs_the_standard_library_only():
    # why: the Pages demo runs on Pyodide, and the views read their times through this module.
    code = "import sys, recordings.plaud.when; print('sqlite3' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_utc_iso_writes_utc_and_reads_a_naive_time_as_utc():
    # why: decision 47. `astimezone` alone would read a naive time as the machine's local time.
    naive = datetime(2026, 10, 23, 12, 0)  # noqa: DTZ001  the case under test
    assert utc_iso(naive) == "2026-10-23T12:00:00+00:00"
    local = datetime(2026, 10, 23, 5, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert utc_iso(local) == "2026-10-23T12:00:00+00:00"
    with pytest.raises(TypeError):
        utc_iso("2026-10-23T12:00:00")
