import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from recordings import disk
from recordings.plaud.checks import NO_SYNC_YET
from recordings_ui.plaud import arrivals, recording_plaud, sync_view
from recordings_ui.status import StatusSettings, status_view

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


def test_the_demo_shows_no_disk_figure(demo_archive):
    # why: demo mode has no settings. Its disk is a temporary copy, or on Pages, Pyodide's
    # in-memory file system: a free-space figure would be made up, on the public demo.
    view = status_view(demo_archive, None, now=T0)
    assert view == {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}


def test_without_a_state_folder_status_shows_disk_only(demo_archive, monkeypatch):
    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=150, f_frsize=1024, f_blocks=1000)
    )
    settings = StatusSettings(
        state_path=None, timezone_name="UTC", warn=20.0, stop=5.0, jobs=("disk",)
    )
    view = status_view(demo_archive, settings, now=T0)
    assert view["configured"] is False and view["jobs"] == [] and view["attention"] == []
    assert view["disk"]["marker"] is True
    assert view["disk"]["level"] == "warn" and view["disk"]["free_percent"] == 15.0


def test_a_server_with_a_backup_but_no_state_db_needs_attention(demo_archive, tmp_path):
    # why: "Backups aren't set up here" is true of a laptop, not of a server whose config has a
    # [backup]: there the missing state.db means `recordings init` was never run (or the state
    # folder is not mounted), and every backup since has had nowhere to be recorded.
    settings = StatusSettings(
        state_path=tmp_path / "state",
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup"),
    )
    view = status_view(demo_archive, settings, now=T0)
    assert view["configured"] is False and view["disk"] is not None
    assert any("no state.db" in a for a in view["attention"])


def test_a_state_db_that_vanishes_after_the_check_is_treated_as_missing(writer, monkeypatch):
    # why: State.read_only returns None when state.db is gone, and Status must not raise.
    from recordings.state import State

    monkeypatch.setattr(State, "read_only", classmethod(lambda cls, path: None))
    settings = StatusSettings(
        state_path=writer.state.path,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup"),
    )
    view = status_view(writer.root, settings, now=T0)
    assert view["configured"] is False and view["disk"] is not None
    assert any("no state.db" in a for a in view["attention"])


def test_with_state_status_reads_the_runs(writer):
    writer.state.record_run(
        "backup", T0 - timedelta(minutes=30), T0 - timedelta(minutes=30), True, "snapshot x"
    )
    settings = StatusSettings(
        state_path=writer.state.path,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup"),
    )
    view = status_view(writer.root, settings, now=T0)
    (job,) = view["jobs"]
    assert view["configured"] and job["last_ok"] == "2026-10-09 07:30 (30 min ago)"
    assert job["last_ok_detail"] == "snapshot x" and not job["attention"]


def test_a_damaged_state_db_needs_attention_rather_than_breaking_the_page(writer):
    # why: Status is where Dan looks when something is wrong; a traceback there hides the rest.
    writer.state.db_path.write_bytes(b"not a database, " * 64)
    settings = StatusSettings(
        state_path=writer.state.path,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup"),
    )
    view = status_view(writer.root, settings, now=T0)
    assert view["configured"] is True and view["jobs"] == []
    assert any("restore state.db from the backup" in a for a in view["attention"])
    assert view["disk"] is not None


def test_a_state_db_that_is_sound_but_unreachable_gets_no_restore_advice(writer, monkeypatch):
    # why: state.unreadable() sorts the errors: moving a sound state.db aside, as the restore
    # advice says, throws away the queue and every merge base. A locked or unmounted one isn't
    # damage.
    import recordings.ops

    def busy(*args, **kwargs):
        exc = sqlite3.OperationalError("database is locked")
        exc.sqlite_errorname = "SQLITE_BUSY"
        raise exc

    monkeypatch.setattr(recordings.ops, "status_summary", busy)
    settings = StatusSettings(
        state_path=writer.state.path,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup"),
    )
    view = status_view(writer.root, settings, now=T0)
    (line,) = view["attention"]
    assert "SQLITE_BUSY" in line and "not damage" in line and "restore" not in line
    assert view["configured"] is True and view["disk"] is not None


def test_status_without_state_never_loads_sqlite(demo_archive):
    # why: the Pages demo runs on Pyodide, where sqlite3 is a separate module nobody has checked.
    # Nor may the reading path load the writer, the locks (flock) or state.db: the Pages demo has
    # no write path, so it needs no no-op lock (spec §6.8) as long as this holds. fcntl is not
    # tested for: a third-party import loads it.
    code = (
        "import sys; from pathlib import Path; from recordings.archive import Archive; "
        "import recordings_ui.settings, recordings_ui.runtime; "
        "import recordings_ui.views as v; "
        f"v.status_view(Archive(Path({str(demo_archive)!r})), None); "
        "print(sorted(m for m in ('sqlite3', 'recordings.writer', 'recordings.locks', "
        "'recordings.state') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


PLAUD_TABLES = (
    "plaud_requests",
    "plaud_listing",
    "plaud_checks",
    "plaud_check_log",
    "plaud_private_checked",
    "plaud_events",
    "plaud_state",
)
T0_SYNC = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)


def _status_settings(state_path) -> StatusSettings:
    return StatusSettings(
        state_path=state_path, timezone_name="UTC", warn=20.0, stop=5.0, jobs=("disk",)
    )


def test_status_never_carries_a_title(plaud_scene):
    # why: P13 and §15.2. Status names recordings by ID; the Sync screen alone may name one,
    # from the recording here, and never one kept out by title.
    s = plaud_scene
    s.open_gate()
    s.add(2, "Private synthetic title 2")
    flagged = s.kept_out_late(2, at=T0_SYNC)
    other = s.add(3, "Week 3 synthetic title")
    s.db.add_event(
        "retagged-private",
        now=T0_SYNC - timedelta(minutes=5),
        recording_id=other,
        plaud_id=s.plaud_id(3),
        detail={"why": "title changed"},
    )
    settings = _status_settings(s.writer.state.path)
    view = status_view(s.writer.root, settings, now=T0_SYNC, plaud=s.settings)
    assert view["sync"]["no_sync_yet"] is False
    assert any(flagged in line for line in view["attention"])  # by ID
    assert "synthetic title" not in json.dumps(view).lower()
    screen = sync_view(s.settings, now=T0_SYNC, can_request=None)
    titles = {row["recording_id"]: row["title"] for row in screen["updated"]}
    assert titles == {flagged: None, other: "Week 3 synthetic title"}


def test_without_the_2b_tables_it_says_no_sync_has_run_yet_never_the_restore_text(plaud_scene):
    # why: §9.5. A state.db from before 2b, or a release rolled back to, has no 2b tables. That
    # is "No sync has run yet", never a damaged state.db: restoring a sound one loses its queue.
    s = plaud_scene
    rid = s.add(1, "Week 1")
    with closing(sqlite3.connect(s.writer.state.db_path)) as conn, conn:
        for table in PLAUD_TABLES:
            conn.execute(f"DROP TABLE {table}")
    settings = _status_settings(s.writer.state.path)
    view = status_view(s.writer.root, settings, now=T0_SYNC, plaud=s.settings)
    assert view["sync"]["no_sync_yet"] is True
    assert view["sync"]["summary"]["sentence"] == NO_SYNC_YET
    assert not [line for line in view["attention"] if "restore" in line]
    assert sync_view(s.settings, now=T0_SYNC, can_request=None)["summary"]["kind"] == "no-sync"
    assert set(arrivals(s.settings, since=T0_SYNC, now=T0_SYNC).values()) == {0}
    # Details still names its Plaud ID, for `make consent-add`.
    lines = recording_plaud(s.settings, rid, now=T0_SYNC)
    assert lines["plaud_ids"] == [{"id": s.plaud_id(1), "copy_id": f"- `{s.plaud_id(1)}` —"}]
    assert lines["checked"] is None
