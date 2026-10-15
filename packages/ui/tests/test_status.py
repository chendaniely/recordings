import subprocess
import sys
from datetime import datetime, timedelta, timezone

from recordings_ui.status import StatusSettings, status_view

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def test_the_demo_shows_no_disk_figure(demo_archive):
    # why: demo mode has no settings. Its disk is a temporary copy, or on Pages, Pyodide's
    # in-memory file system: a free-space figure would be made up, on the public demo.
    view = status_view(demo_archive, None, now=T0)
    assert view == {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}


def test_without_a_state_folder_status_shows_disk_only(demo_archive):
    settings = StatusSettings(state_path=None, timezone_name="UTC", warn=20.0, stop=5.0,
                              jobs=("disk",))
    view = status_view(demo_archive, settings, now=T0)
    assert view["configured"] is False and view["jobs"] == []
    assert view["disk"]["marker"] is True and view["disk"]["level"] in ("ok", "warn", "stop")


def test_with_state_status_reads_the_runs(writer):
    writer.state.record_run("backup", T0 - timedelta(minutes=30), T0 - timedelta(minutes=30), True,
                            "snapshot x")
    settings = StatusSettings(state_path=writer.state.path, timezone_name="UTC", warn=20.0,
                              stop=5.0, jobs=("disk", "backup"))
    view = status_view(writer.root, settings, now=T0)
    (job,) = view["jobs"]
    assert view["configured"] and job["last_ok"] == "2026-10-09 07:30 (30 min ago)"
    assert job["last_ok_detail"] == "snapshot x" and not job["attention"]


def test_a_damaged_state_db_needs_attention_rather_than_breaking_the_page(writer):
    # why: Status is where Dan looks when something is wrong; a traceback there hides the rest.
    writer.state.db_path.write_bytes(b"not a database, " * 64)
    settings = StatusSettings(state_path=writer.state.path, timezone_name="UTC", warn=20.0,
                              stop=5.0, jobs=("disk", "backup"))
    view = status_view(writer.root, settings, now=T0)
    assert view["configured"] is True and view["jobs"] == []
    assert any("restore it from the backup" in a for a in view["attention"])
    assert view["disk"] is not None


def test_status_without_state_never_loads_sqlite(demo_archive):
    # why: the Pages demo runs on Pyodide, where sqlite3 is a separate module nobody has checked.
    # Nor may the reading path load the writer, the locks (flock) or state.db: the Pages demo has
    # no write path, so it needs no no-op lock (spec §6.8) as long as this holds. fcntl is not
    # tested for: a third-party import loads it.
    code = ("import sys; from pathlib import Path; from recordings.archive import Archive; "
            "import recordings_ui.settings, recordings_ui.runtime; "
            "import recordings_ui.views as v; "
            f"v.status_view(Archive(Path({str(demo_archive)!r})), None); "
            "print(sorted(m for m in ('sqlite3', 'recordings.writer', 'recordings.locks', "
            "'recordings.state') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
