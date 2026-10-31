"""Stage 2b's tables in state.db (2b spec §9.5): tables only, keyed by canonical Plaud ID, read
through PlaudDB. Every ID here is synthetic."""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from recordings.init import init_archive
from recordings.plaud.checks import (
    NO_SYNC_YET,
    Marker,
    NoSyncYet,
    PlaudDB,
    no_such_table,
)
from recordings.state import MIGRATIONS, SCHEMA, SCHEMA_VERSION, State

NOW = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)
HEX = f"{0x3FA9:032x}"
OTHER = f"{0xB0B:032x}"
REC = "20261006T140003-0700_3fa91c2e"
REC2 = "20261007T090000-0700_0badc0de"
PLAUD_TABLES = (
    "plaud_requests",
    "plaud_listing",
    "plaud_checks",
    "plaud_check_log",
    "plaud_private_checked",
    "plaud_events",
    "plaud_state",
)
TWO_A_SCHEMA = SCHEMA.split("-- Stage 2b's tables")[0]


@pytest.fixture
def state(tmp_path) -> State:
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    return State.open(tmp_path / "state")


def _tables(state: State) -> set[str]:
    with state.db() as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _as_2a(state: State) -> None:
    """Make this state.db what a 2a release left: its tables, none of 2b's."""
    with state.db() as db:
        for table in PLAUD_TABLES:
            db.execute(f"DROP TABLE {table}")


def test_schema_version_stays_1_and_migrations_stay_empty():
    # why: C8.01, §17.3. A version bump or a migration makes an older release refuse this
    # state.db, and `make rollback` to the 2a tag would stop working. 2b adds tables only.
    assert SCHEMA_VERSION == 1
    assert MIGRATIONS == {}


def test_a_2a_state_db_gains_the_2b_tables_on_open(state):
    _as_2a(state)
    started = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    state.record_run("backup", started, started, True, "ok")
    revision = state.adopt("recordings/2026/10/x/recording.json", 1, "a" * 64, b"{}")
    assert not _tables(state) & set(PLAUD_TABLES)
    reopened = State.open(state.path)
    assert set(PLAUD_TABLES) <= _tables(reopened)
    assert reopened.last_run("backup").detail == "ok"
    assert reopened.last_committed("recordings/2026/10/x/recording.json").id == revision
    assert reopened.meta("schema_version") == "1"


def test_a_state_db_with_the_2b_tables_still_reads_as_2a_did(state):
    # why: §17.3. An older release opens this file with its own SCHEMA, then reads 2a's tables;
    # the 2b tables must neither break that script nor its reads.
    started = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    state.record_run("mirror", started, started, False, "error: `OSError`")
    PlaudDB(state).put("catch_up_done_at", NOW.isoformat(), now=NOW)
    with state.db() as db:
        db.executescript(TWO_A_SCHEMA)  # what a 2a release runs at every open
    reader = State.read_only(state.path)
    assert reader.last_run("mirror", ok=False).detail == "error: `OSError`"
    assert reader.meta("writer_id") == "test" and reader.flags() == []
    assert PlaudDB(reader).get("catch_up_done_at") == NOW.isoformat()


def test_listing_rows_follow_the_canonical_id_across_a_form_flip(state):
    # why: C5.33. Plaud changed its IDs' form once already; a change of form must carry every
    # state over, or a kept-out recording would download and a review mark vanish.
    db = PlaudDB(state)
    db.set_listing("of_" + HEX, kept_out_at=NOW, kept_out_word="noarchive", needs_review=True)
    first = db.listing(HEX)
    assert (first.canonical_plaud_id, first.listed_id, first.first_seen) == (HEX, "of_" + HEX, NOW)
    flag = db.add_event(
        "consent-late", now=NOW, recording_id=REC, plaud_id="OF_" + HEX.upper(), open_flag=True
    )
    later = NOW + timedelta(hours=1)
    db.set_listing(HEX.upper(), listed_id=HEX.upper(), last_seen=later)  # now listed bare
    row = db.listing("of_" + HEX)
    assert row.canonical_plaud_id == HEX and row.listed_id == HEX.upper()
    assert (row.first_seen, row.last_seen) == (NOW, later)
    assert (row.kept_out_at, row.kept_out_word, row.needs_review) == (NOW, "noarchive", True)
    assert db.listing(HEX) == row
    (open_flag,) = db.open_flags()
    assert (open_flag.id, open_flag.canonical_plaud_id) == (flag, HEX)
    with state.db() as conn:
        assert conn.execute("SELECT count(*) FROM plaud_listing").fetchone()[0] == 1


def test_set_listing_and_set_check_take_column_names_only(state):
    db = PlaudDB(state)
    with pytest.raises(ValueError, match="not a settable column: title"):
        db.set_listing(HEX, title="Week 4")  # a listing row never holds a title
    with pytest.raises(ValueError, match="canonical_plaud_id"):
        db.set_listing(HEX, canonical_plaud_id=OTHER)
    with pytest.raises(ValueError, match="no time to date"):
        db.set_listing(HEX, kept_out_word="noarchive")
    db.set_listing("of_" + HEX, kept_out_at=NOW, kept_out_word="noarchive")  # never listed yet
    row = db.listing(HEX)
    assert (row.listed_id, row.first_seen, row.kept_out_word) == ("of_" + HEX, NOW, "noarchive")
    db.set_listing(OTHER, last_seen=NOW)  # a row dated by the listing that showed it
    assert (db.listing(OTHER).first_seen, db.listing(OTHER).last_seen) == (NOW, NOW)
    with pytest.raises(ValueError, match="not a settable column: oops"):
        db.set_check(REC, oops=1)
    db.set_check(REC, failures=2, held_removals=[{"part": "speakers"}], settled=True)
    db.set_check(REC, failures=3)
    check = db.check_row(REC)
    assert (check.failures, check.settled, check.held_removals) == (
        3,
        True,
        ({"part": "speakers"},),
    )
    assert db.check_row(REC2) is None
    with pytest.raises(ValueError, match="not an event kind"):
        db.add_event("renamed", now=NOW)


def test_one_open_privacy_flag_per_recording(state):
    # why: P15. A recording has one privacy flag, changed in place, never a second.
    db = PlaudDB(state)
    first = db.add_event("kept-out-late", now=NOW, recording_id=REC, open_flag=True)
    for kind in ("consent-late", "kept-out-late"):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_event(kind, now=NOW, recording_id=REC, open_flag=True)
    db.add_event("private-unwritten", now=NOW, recording_id=REC, open_flag=True)  # its own index
    with pytest.raises(sqlite3.IntegrityError):
        db.add_event("private-unwritten", now=NOW, recording_id=REC, open_flag=True)
    db.add_event("consent-late", now=NOW, recording_id=REC2, open_flag=True)  # another recording
    db.add_event("consent-late", now=NOW, recording_id=REC)  # closed events: any number
    db.change_kind(first, "consent-late")
    assert [e.kind for e in db.open_flags() if e.recording_id == REC] == ["consent-late"]
    db.resolve(first, now=NOW)
    db.add_event("kept-out-late", now=NOW, recording_id=REC, open_flag=True)
    assert len(db.open_flags()) == 2


def test_reading_a_missing_2b_table_raises_no_sync_yet(state):
    # why: §9.5. A 2a state.db read by a 2b Status says "No sync has run yet", never the
    # "restore state.db" text that any DatabaseError shows today.
    _as_2a(state)
    db = PlaudDB(State.read_only(state.path))
    for read in (
        lambda: db.get("last_turn"),
        db.open_flags,
        lambda: db.listing(HEX),
        lambda: db.check_row(REC),
        lambda: db.markers(REC),
        lambda: db.events_since(NOW, ["imported"]),
        lambda: db.with_prefix("blocker:"),
    ):
        with pytest.raises(NoSyncYet, match=NO_SYNC_YET):
            read()
    assert no_such_table(sqlite3.OperationalError("no such table: plaud_state"))
    assert not no_such_table(sqlite3.OperationalError("database is locked"))
    assert not no_such_table(ValueError("no such table"))


def test_forget_takes_the_recordings_plaud_rows_and_title_hash(state):
    # why: §9.5. A title hash is kept only while its recording is here; reindex --allow-missing
    # (State.forget) takes it, and the recording's own Plaud rows, with the recording.
    db = PlaudDB(state)
    for rec, ids in ((REC, (HEX, "of_" + OTHER)), (REC2, (f"{7:032x}",))):
        db.set_markers(rec, [Marker(i, "b" * 64, 1) for i in ids], now=NOW)
        for i in ids:
            db.set_listing(
                i, last_seen=NOW, title_sha256="b" * 64, duration_ms=2600, change_marker="m1"
            )
        db.set_check(rec, failures=1)
        db.add_event("imported", now=NOW, recording_id=rec, plaud_id=ids[0])
    db.set_listing(HEX, kept_out_at=NOW, kept_out_word="noarchive")
    state.forget(f"recordings/2026/10/{REC}/recording.json")
    assert db.markers(REC) == {} and db.check_row(REC) is None
    assert db.events_since(NOW - timedelta(days=1), ["imported"])[0].recording_id == REC2
    for i in (HEX, OTHER):
        row = db.listing(i)
        assert (row.title_sha256, row.duration_ms, row.change_marker) == (None, None, None)
    assert db.listing(HEX).kept_out_word == "noarchive"  # kept-out state stays, by Plaud ID
    assert db.listing(f"{7:032x}").title_sha256 == "b" * 64
    assert set(db.markers(REC2)) == {f"{7:032x}"} and db.check_row(REC2).failures == 1


def test_prune_keeps_open_flags_and_drops_old_rows(state):
    db = PlaudDB(state)
    with state.db() as conn:  # phase 3's checks write the log; here it is written by hand
        for days in (8, 6):
            conn.execute(
                "INSERT INTO plaud_check_log (at, recording_id, result) "
                "VALUES (?, ?, 'up to date')",
                ((NOW - timedelta(days=days)).isoformat(), REC),
            )
    old_closed = db.add_event("imported", now=NOW - timedelta(days=91), recording_id=REC)
    old_open = db.add_event(
        "consent-late", now=NOW - timedelta(days=120), recording_id=REC, open_flag=True
    )
    recent = db.add_event("updated", now=NOW - timedelta(days=89), recording_id=REC2)
    db.prune(now=NOW)
    with state.db() as conn:
        assert conn.execute("SELECT count(*) FROM plaud_check_log").fetchone()[0] == 1
        ids = {r[0] for r in conn.execute("SELECT id FROM plaud_events")}
    assert ids == {old_open, recent} and old_closed not in ids


def test_put_many_is_one_transaction(state):
    db = PlaudDB(state)
    with pytest.raises(TypeError):
        db.put_many({"catch_up_done_at": NOW.isoformat(), "last_turn": object()}, now=NOW)
    assert db.get("catch_up_done_at") is None and db.get("last_turn") is None
    db.put_many({"catch_up_done_at": "x", "blocker:consent": {"since": "y"}}, now=NOW)
    db.put("blocker:hold", {"since": "z"}, now=NOW)
    assert db.with_prefix("blocker:") == {
        "blocker:consent": {"since": "y"},
        "blocker:hold": {"since": "z"},
    }
    db.delete("blocker:hold")
    assert db.get("blocker:hold") is None and db.get("catch_up_done_at") == "x"


@pytest.fixture
def vancouver(monkeypatch):
    monkeypatch.setenv("TZ", "America/Vancouver")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_naive_time_is_stored_as_utc_whatever_the_machines_zone(state, vancouver):
    # why: decision 47. when.parse_stamp reads a naive time as UTC; storing it as local time
    # (naive.astimezone) wrote 14:20 as 21:20Z, so a reader and a writer disagreed by the offset.
    db = PlaudDB(state)
    naive = datetime(2026, 10, 23, 14, 20)  # noqa: DTZ001
    db.set_listing(HEX, last_seen=naive)
    db.put("last_turn", "x", now=naive)
    assert db.listing(HEX).last_seen == NOW
    with state.db() as conn:
        stored = conn.execute("SELECT updated_at FROM plaud_state").fetchone()[0]
    assert stored == NOW.isoformat()


def test_a_time_column_takes_a_datetime_only(state):
    db = PlaudDB(state)
    db.set_listing(HEX, last_seen=NOW)
    for bad in ("2026-10-23T14:20:00", 1761229200, "yesterday"):
        with pytest.raises(TypeError, match="last_seen"):
            db.set_listing(HEX, last_seen=bad)
        with pytest.raises(TypeError, match="next_check_at"):
            db.set_check(REC, next_check_at=bad)
    db.set_listing(HEX, absent_since=NOW)
    db.set_listing(HEX, absent_since=None)  # None clears a time
    assert db.listing(HEX).absent_since is None


def test_a_title_cannot_land_where_a_hash_belongs(state):
    # why: P1/C7.01. A title column is a hash or nothing; "Week 4 standup" must never be stored.
    db = PlaudDB(state)
    for bad in ("Week 4 standup", "B" * 64, "b" * 63, "g" * 64, ""):
        with pytest.raises(ValueError, match="title_sha256"):
            db.set_listing(HEX, last_seen=NOW, title_sha256=bad)
        with pytest.raises(ValueError, match="title_sha256"):
            db.set_markers(REC, [Marker(HEX, bad, 1)], now=NOW)
    assert db.listing(HEX) is None and db.markers(REC) == {}
    db.set_listing(HEX, last_seen=NOW, title_sha256="b" * 64)
    db.set_listing(HEX, title_sha256=None)
    assert db.listing(HEX).title_sha256 is None
