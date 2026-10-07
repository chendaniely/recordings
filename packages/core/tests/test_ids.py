from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from recordings.ids import make_id, parse_id, relative_dir

SHA = "3fa91c2e" + "0" * 56


def test_make_id_uses_iso_basic_local_time_with_offset():
    t = datetime(2026, 10, 6, 14, 0, 3, tzinfo=ZoneInfo("America/Vancouver"))
    assert make_id(t, SHA) == "20261006T140003-0700_3fa91c2e"


def test_utc_is_plus_zero_not_z():
    t = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert make_id(t, SHA) == "20260102T030405+0000_3fa91c2e"


def test_parse_round_trips_instant_and_offset():
    t = datetime(2026, 10, 5, 20, 5, 15, tzinfo=timezone(timedelta(hours=9)))
    stamp, short = parse_id(make_id(t, SHA))
    assert stamp == t
    assert stamp.utcoffset() == timedelta(hours=9)
    assert short == "3fa91c2e"


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_id(datetime(2026, 1, 1), SHA)


def test_offset_with_seconds_is_refused():
    # Old tzdata local-mean-time offsets have seconds; an ID carries ±HHMM only.
    t = datetime(1900, 1, 1, tzinfo=timezone(timedelta(hours=-5, seconds=-36)))
    with pytest.raises(ValueError, match="seconds"):
        make_id(t, SHA)


@pytest.mark.parametrize("bad", ["abc", SHA.upper(), SHA[:-1]])
def test_sha_must_be_64_lowercase_hex(bad):
    with pytest.raises(ValueError, match="64 lowercase hex"):
        make_id(datetime(2026, 1, 1, tzinfo=timezone.utc), bad)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-06T14:00:03-07:00_3fa91c2e",  # extended format
        "20261006T140003_3fa91c2e",  # no offset
        "20261006T140003-0700_3FA91C2E",  # upper-case hex
        "20261006T140003-0700_3fa91c2e/../x",  # path tricks
    ],
)
def test_parse_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        parse_id(bad)


def test_relative_dir_uses_the_local_date_in_the_id():
    # 23:30 on Dec 31 in Vancouver is already Jan 1 in UTC; the folder follows the ID.
    t = datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo("America/Vancouver"))
    rid = make_id(t, SHA)
    assert str(relative_dir(rid)) == f"recordings/2025/12/{rid}"
