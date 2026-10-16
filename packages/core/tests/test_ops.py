import json
import os
import sqlite3
import subprocess
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from recordings import disk, ops
from recordings.cli import main
from recordings.config import load_config
from recordings.locks import Locks
from recordings.ops import Context, due, enabled_jobs, loop, run_due, run_job

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
SUMMARY = json.dumps(
    {
        "message_type": "summary",
        "snapshot_id": "f" * 64,
        "total_files_processed": 1,
        "data_added": 1,
    }
)
TITLE = "会議メモ / Q&A"  # Review Focus 4: a synthetic non-ASCII title


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def restic(returncode=0, stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode, SUMMARY if returncode == 0 else "", stderr
        )

    return run


def raising(exc):
    def run(cmd, **kwargs):
        raise exc

    return run


def context(tmp_path, writer, ntfy, *, clock=None, runner=None, mirror=False):
    (tmp_path / "pw").write_text("pw\n", encoding="utf-8")
    deploy = tmp_path / "deploy.env"
    deploy.write_text("X=1\n", encoding="utf-8")
    mirror_section = f'[mirror]\ntarget = "{tmp_path / "mirror"}"\n' if mirror else ""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{writer.state.path}"\n'
        f'[backup]\nrepository = "{tmp_path / "repo"}"\nextra_paths = ["{deploy}"]\n'
        f'restore_scratch = "{tmp_path / "scratch"}"\n'
        f'[alerts]\nntfy_server = "{ntfy.url}"\n{mirror_section}',
        encoding="utf-8",
    )
    environ = {
        "RECORDINGS_CONFIG": str(cfg_file),
        "RESTIC_PASSWORD_FILE": str(tmp_path / "pw"),
        "RECORDINGS_NTFY_TOPIC": "alerts-topic",
        "RECORDINGS_DEADMAN_URL": f"{ntfy.url}/ping",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    return Context(
        cfg=load_config(environ),
        environ=environ,
        state=writer.state,
        clock=clock or Clock(),
        runner=runner or restic(),
    )


def own_environment(ctx, monkeypatch):
    """The process environment is the context's alone: a CLI command reads os.environ, and Dan's
    real RECORDINGS_* and RESTIC_* settings (a topic file, a token, a ping URL) must not get in."""
    for key in [k for k in os.environ if k.startswith(("RECORDINGS_", "RESTIC_"))]:
        monkeypatch.delenv(key)
    for key, value in ctx.environ.items():
        monkeypatch.setenv(key, value)


def pushes(ntfy):
    """The ntfy alerts: POSTs to the topic."""
    return [r for r in ntfy.requests if r["path"] == "/alerts-topic"]


def pings(ntfy):
    """The dead-man's switch pings, by path."""
    return [r["path"] for r in ntfy.requests if r["path"].startswith("/ping")]


def test_which_jobs_run_comes_from_config(tmp_path, writer, ntfy):
    assert enabled_jobs(context(tmp_path, writer, ntfy).cfg) == (
        "disk",
        "backup",
        "prune",
        "check",
        "restore-test",
    )
    assert "mirror" in enabled_jobs(context(tmp_path, writer, ntfy, mirror=True).cfg)


def test_a_job_is_due_after_its_interval_and_a_failure_is_retried_after_15_minutes(writer):
    state = writer.state
    assert due(state, T0, ("backup", "check")) == ["backup", "check"]
    state.record_run("backup", T0, T0, True, "ok")
    assert due(state, T0 + timedelta(minutes=59), ("backup",)) == []
    assert due(state, T0 + timedelta(hours=1), ("backup",)) == ["backup"]
    later = T0 + timedelta(hours=2)
    state.record_run("backup", later, later, False, "failed")
    assert due(state, later + timedelta(minutes=14), ("backup",)) == []
    assert due(state, later + timedelta(minutes=15), ("backup",)) == ["backup"]
    # why: a failed restore test restores the whole archive again, and a failed check reads a
    # quarter of the repository: once a day, not every 15 minutes (Task 11's ops council)
    state.record_run("restore-test", later, later, False, "RestoreTestFailed")
    assert due(state, later + timedelta(hours=23), ("restore-test",)) == []
    assert due(state, later + timedelta(hours=24), ("restore-test",)) == ["restore-test"]
    # why: a failed mirror walks both trees twice to fail again, and one short of space first
    # reads and rewrites all it staged: its own hour, not every 15 minutes (Task 12's ops council)
    state.record_run("mirror", later, later, False, "MirrorError; exit 11")
    assert due(state, later + timedelta(minutes=59), ("mirror",)) == []
    assert due(state, later + timedelta(hours=1), ("mirror",)) == ["mirror"]
    # why: one run stamped in the future, by a clock that ran ahead, must not hold every job
    # back until the clock catches up with it
    ahead = T0 + timedelta(days=400)
    state.record_run("backup", ahead, ahead, True, "ok")
    state.record_run("check", ahead, ahead, False, "BackupError; exit 1")
    assert due(state, later + timedelta(hours=1), ("backup", "check")) == ["backup", "check"]


def test_a_good_backup_pings_the_dead_mans_switch(tmp_path, writer, make_incoming, ntfy):
    writer.add(make_incoming())
    assert run_job(context(tmp_path, writer, ntfy), "backup").ok
    assert pings(ntfy) == ["/ping/start", "/ping"] and pushes(ntfy) == []


def test_a_killed_backup_alerts_once_reminds_daily_and_says_when_it_recovers(
    tmp_path, writer, make_incoming, ntfy
):
    # why: §20, "a killed backup raises an alert". A killed restic exits -9.
    writer.add(make_incoming())
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock, runner=restic(returncode=-9))
    result = run_job(ctx, "backup")
    assert (result.ok, result.detail) == (False, "BackupError; exit -9")
    clock.now += timedelta(hours=1)
    run_job(ctx, "backup")  # still failing: no second alert yet
    clock.now += timedelta(hours=24)
    run_job(ctx, "backup")  # a reminder
    ctx.runner = restic()
    clock.now += timedelta(hours=1)
    assert run_job(ctx, "backup").ok
    assert [(r["headers"]["Title"], r["body"]) for r in pushes(ntfy)] == [
        ("recordings: backup failed", "backup failed (BackupError; exit -9). See Status."),
        ("recordings: backup failed", "backup failed (BackupError; exit -9). See Status."),
        ("recordings: backup recovered", "backup works again. See Status."),
    ]
    assert pings(ntfy) == ["/ping/start", "/ping/fail"] * 3 + ["/ping/start", "/ping"]


@pytest.mark.parametrize(
    "runner, detail",
    [
        (
            restic(
                returncode=1,
                stderr=f"Fatal: open /archive/recordings/2026/10/x/{TITLE}.md: permission denied\n",
            ),
            "BackupError; exit 1",
        ),
        (
            raising(PermissionError(13, "Permission denied", f"/archive/{TITLE}.md")),
            "PermissionError",
        ),
    ],
    ids=["restic-stderr", "oserror-path"],
)
def test_no_alert_or_stored_run_carries_an_exceptions_text(
    tmp_path, writer, ntfy, caplog, runner, detail
):
    # why: Review Focus 4. An exception's text can hold a title, a path or a tool's own output.
    # Alerts go to third parties (ntfy.sh, Healthchecks.io), and Status shows the stored detail.
    # Only the container's debug log may carry the full text.
    ctx = context(tmp_path, writer, ntfy, runner=runner)
    with caplog.at_level("INFO"):
        result = run_job(ctx, "backup")
    assert (result.ok, result.detail) == (False, detail)
    assert len(pushes(ntfy)) == 1 and pings(ntfy) == ["/ping/start", "/ping/fail"]
    with closing(sqlite3.connect(writer.state.db_path)) as db:
        stored = "\n".join(db.iterdump())
    sent = json.dumps(ntfy.requests, ensure_ascii=False)
    for text in (stored, sent, caplog.text):
        assert "会議" not in text


def test_low_disk_space_raises_an_alert(tmp_path, writer, ntfy, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=150, f_frsize=1024, f_blocks=1000)
    )
    result = run_job(context(tmp_path, writer, ntfy), "disk")
    assert (result.ok, result.detail) == (False, "DiskWarning")
    (req,) = pushes(ntfy)
    assert req["headers"]["Title"] == "recordings: disk space low"
    assert req["body"] == "disk failed (DiskWarning). See Status."
    assert pings(ntfy) == []  # only a backup pings the dead-man's switch


def test_run_due_runs_what_is_due_and_beats_the_heart(tmp_path, writer, make_incoming, ntfy):
    writer.add(make_incoming())
    ctx = context(tmp_path, writer, ntfy)
    results = {r.job for r in run_due(ctx)}
    assert {"disk", "backup", "prune", "check"} <= results
    assert writer.state.meta("ops_heartbeat") == T0.isoformat()
    assert writer.state.meta("ops_runs_trimmed") == T0.isoformat()  # the daily trim ran too
    assert {r.job for r in run_due(ctx)} <= {"restore-test"}  # the rest ran a moment ago


def test_a_busy_ops_lock_skips_quietly(tmp_path, writer, make_incoming, ntfy):
    # why: the CLI and the loop share one flock in /state (ops review). The loop never waits, and
    # a skip is not a failure: nothing is recorded, alerted or pinged, and the next tick retries.
    writer.add(make_incoming())
    ctx = context(tmp_path, writer, ntfy)
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        results = run_due(ctx)
        assert results and all((r.ok, r.detail) == (False, "skipped: busy") for r in results)
        assert run_job(ctx, "backup", lock_timeout=0.2).detail == "skipped: busy"
    assert ntfy.requests == [] and writer.state.last_run("backup") is None
    assert "backup" in {r.job for r in run_due(ctx) if r.ok}  # free again: it runs


def test_an_unopenable_lock_file_or_state_is_a_recorded_failure_not_a_crash(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: run_job replaces Task 11's, whose lock-file handling must survive: an OSError opening
    # the lock (permissions, a full disk) escaping here would crash the backup service on a loop.
    ctx = context(tmp_path, writer, ntfy)

    class Unopenable:
        def __init__(self, *args, **kwargs):
            pass

        def hold(self, key):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ops, "Locks", Unopenable)
    assert run_job(ctx, "check") == ops.JobResult("check", False, "PermissionError")
    assert writer.state.last_run("check").detail == "PermissionError"
    assert all(not r.ok for r in run_due(ctx))  # and the loop's pass returns normally
    monkeypatch.undo()

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(writer.state, "record_run", broken)
    assert run_job(ctx, "check").ok  # a state disk that can't record must not turn into a crash


def test_a_state_disk_that_cannot_write_or_read_never_stops_a_job_an_alert_or_the_loop(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: a broken state.db reached the loop three more ways than record_run: the alert state's
    # read and write, the heartbeat, and due()'s own reads. None may end the backup service.
    ctx = context(tmp_path, writer, ntfy, runner=restic(returncode=1))

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    for name in ("record_run", "alert_state", "set_alert_state", "set_meta"):
        monkeypatch.setattr(writer.state, name, broken)
    result = run_job(ctx, "backup")
    assert (result.ok, result.detail) == (False, "BackupError; exit 1")
    assert len(pushes(ntfy)) == 1 and pings(ntfy) == ["/ping/start", "/ping/fail"]  # still said
    ctx.runner = restic()
    assert run_job(ctx, "backup").ok  # and a good run returns, with no recovery it can know of
    assert all(isinstance(r, ops.JobResult) for r in run_due(ctx))  # the heartbeat write fails too
    monkeypatch.setattr(writer.state, "last_run", broken)  # due() cannot read either
    ticks = []
    loop(ctx, tick=0, sleep=ticks.append, stop=lambda: len(ticks) >= 2)
    assert len(ticks) == 2


def test_old_runs_are_trimmed_daily_but_each_jobs_newest_good_run_stays(tmp_path, writer, ntfy):
    # why: ops_runs gets about 70,000 rows a year. Status must still know a job's last good run
    # after a long failing streak, so that row is kept however old it is.
    state = writer.state
    old = T0 - timedelta(days=200)
    state.record_run("backup", old, old, True, "snapshot a")  # newest good: kept
    state.record_run("check", old, old, True, "old good, superseded")  # dropped
    state.record_run("check", old + timedelta(days=1), old + timedelta(days=1), True, "kept")
    for day in range(2, 150):  # a failing streak
        at = old + timedelta(days=day)
        state.record_run("backup", at, at, False, "BackupError; exit 1")
    recent = T0 - timedelta(days=3)
    state.record_run("disk", recent, recent, True, "ok")
    ctx = context(tmp_path, writer, ntfy)
    ops._trim_runs(ctx)
    with closing(sqlite3.connect(state.db_path)) as db:
        kept = db.execute("SELECT job, ok, detail FROM ops_runs ORDER BY id").fetchall()
    assert ("backup", 1, "snapshot a") in kept and ("check", 1, "kept") in kept
    assert ("check", 1, "old good, superseded") not in kept
    # the streak's days 2 to 149 finished 198 to 51 days before T0: those inside 90 days stay
    assert sum(1 for r in kept if r[0] == "backup" and r[1] == 0) == 40
    assert state.last_run("backup", ok=True).detail == "snapshot a"
    assert state.meta("ops_runs_trimmed") == T0.isoformat()
    state.record_run("disk", old, old, True, "ancient")  # superseded below, so it may go
    state.record_run("disk", recent, recent, True, "ok")
    ctx.clock.now += timedelta(hours=1)
    ops._trim_runs(ctx)  # not again within a day
    with closing(sqlite3.connect(state.db_path)) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM ops_runs WHERE detail = 'ancient'").fetchone()[0] == 1
        )
    ctx.clock.now += timedelta(days=1)
    ops._trim_runs(ctx)
    with closing(sqlite3.connect(state.db_path)) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM ops_runs WHERE detail = 'ancient'").fetchone()[0] == 0
        )


def test_only_restics_exit_11_is_busy_rsyncs_is_a_failure(tmp_path, writer, ntfy, monkeypatch):
    # why: rsync's exit 11 is a file I/O error. Only a BackupError with restic's 11 is "locked".
    from recordings.mirror import MirrorError

    ctx = context(tmp_path, writer, ntfy, mirror=True)

    def rsync_io_error(ctx, tags):
        raise MirrorError("rsync failed", returncode=11)

    monkeypatch.setitem(ops.JOBS, "mirror", rsync_io_error)
    result = run_job(ctx, "mirror", lock_timeout=0)
    assert (result.ok, result.detail) == (False, "MirrorError; exit 11")
    assert writer.state.last_run("mirror").detail == "MirrorError; exit 11"
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == ["recordings: mirror failed"]


def test_a_locked_repository_is_busy_not_a_failure(tmp_path, writer, ntfy):
    # why: the NAS's weekly forget --prune holds restic's lock (exit 11). That is not an outage.
    ctx = context(
        tmp_path, writer, ntfy, runner=restic(11, "Fatal: repository is already locked exclusively")
    )
    result = run_job(ctx, "backup", lock_timeout=0)
    assert result.detail == "skipped: busy (repository locked)" and not result.ok
    assert writer.state.last_run("backup") is None and pushes(ntfy) == []
    assert pings(ntfy) == ["/ping/start"]  # started, never finished: the dead-man's switch decides


def test_the_loop_runs_until_told_to_stop(tmp_path, writer, ntfy):
    ctx = context(tmp_path, writer, ntfy)
    ticks = []
    loop(ctx, tick=0, sleep=ticks.append, stop=lambda: len(ticks) >= 3)
    assert len(ticks) == 3


def test_the_loop_runs_with_sigterm_forwarded(tmp_path, writer, ntfy, monkeypatch):
    # why: the loop is PID 1 in its container, where SIGTERM does nothing without a handler, and
    # Docker would wait out stop_grace_period, then kill restic mid-run and leave its lock behind.
    # Inside sigterm_forwarded (Task 11), restic gets the SIGTERM, then the loop stops.
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    events = []

    @contextmanager
    def forwarded():
        events.append("forwarding")
        yield
        events.append("restored")

    monkeypatch.setattr(ops, "sigterm_forwarded", forwarded)
    # the CLI waits for restic's lock (5m); the loop never does (§15.1): it is None by then
    monkeypatch.setattr(
        ops, "loop", lambda ctx, **kwargs: events.append(f"loop retry_lock={ctx.retry_lock}")
    )
    assert main(["ops", "--loop"]) == 0
    assert events == ["forwarding", "loop retry_lock=None", "restored"]


def test_the_loops_log_lines_carry_a_time_zone(tmp_path, writer, ntfy, monkeypatch):
    # why: the container's clock is UTC, and a line stamped "2026-10-14 19:30:00" read as local
    # time is 7 hours out for a reader in Vancouver. %z makes every stamp say which it is.
    import logging
    import re

    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    seen = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(ops, "loop", lambda ctx, **kwargs: None)
    assert main(["ops", "--loop"]) == 0
    record = logging.LogRecord("recordings.ops", logging.INFO, __file__, 1, "backup ok", None, None)
    line = logging.Formatter(seen["format"], seen["datefmt"]).format(record)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} recordings\.ops backup ok", line)


def test_alert_test_sends_one_logs_the_check_and_never_prints_the_topic(
    tmp_path, writer, ntfy, monkeypatch, capsys
):
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    assert main(["alert-test", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"ntfy": True, "deadman": True}
    assert "alerts-topic" not in out
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == ["recordings: test alert"]
    assert pings(ntfy) == ["/ping/log"]  # /log: the check's state stays as it is


def test_alert_test_can_test_the_dead_mans_switch_alone_and_fails_when_a_ping_fails(
    tmp_path, writer, ntfy, monkeypatch, capsys
):
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    monkeypatch.delenv("RECORDINGS_NTFY_TOPIC")
    assert main(["alert-test", "--json"]) == 0  # no topic: ntfy is skipped, not failed
    assert json.loads(capsys.readouterr().out) == {"ntfy": None, "deadman": True}
    assert pushes(ntfy) == [] and pings(ntfy) == ["/ping/log"]
    monkeypatch.setenv("RECORDINGS_NTFY_TOPIC", "alerts-topic")
    ntfy.status = 500
    assert main(["alert-test", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ntfy": False, "deadman": False}
    monkeypatch.delenv("RECORDINGS_NTFY_TOPIC")  # a failing ping alone fails the command
    assert main(["alert-test", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ntfy": None, "deadman": False}
    monkeypatch.delenv("RECORDINGS_DEADMAN_URL")
    assert main(["alert-test", "--json"]) == 78  # nothing to test


def test_one_pass_of_ops_exits_75_when_every_turn_was_busy(tmp_path, writer, ntfy, monkeypatch):
    # why: `recordings backup` exits 75 (try again) on a busy turn, and so does a pass of ops
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        assert main(["ops", "--json"]) == 75
    assert main(["ops", "--json"]) in (0, 1)  # free again: it ran


def test_a_full_state_disk_alerts_once_not_every_minute(tmp_path, writer, ntfy, monkeypatch):
    # why: a state.db that reads but can't write (a full disk) left every failing job due at every
    # tick and its alert unsaved: one push a minute until ntfy.sh refused them all (Task 13 review).
    from types import SimpleNamespace

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=0, f_frsize=1024, f_blocks=1000)
    )
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    for name in ("record_run", "set_alert_state", "set_meta", "prune_runs"):
        monkeypatch.setattr(writer.state, name, full)
    run_due(ctx)
    titles = [r["headers"]["Title"] for r in pushes(ntfy)]
    assert titles.count("recordings: disk space low") == 1
    sent, pinged = len(ntfy.requests), pings(ntfy)
    for _ in range(
        30
    ):  # past RETRY_AFTER (15 min), so the alert memory is what holds, not only the attempts
        clock.now += timedelta(minutes=1)
        run_due(ctx)
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == titles
    assert len(ntfy.requests) == sent and pings(ntfy) == pinged  # no job ran again either


def test_a_locked_repository_is_not_retried_every_minute(tmp_path, writer, ntfy):
    # why: a locked repository is not recorded, so every tick of the NAS's retention run brought
    # ssh, a state.db copy, restic unlock and restic backup again (Task 13 review).
    calls = []

    def locked(cmd, **kwargs):
        calls.append(cmd)
        return restic(11, "Fatal: repository is already locked exclusively")(cmd, **kwargs)

    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock, runner=locked)
    run_due(ctx)
    first = len(calls)
    assert first
    clock.now += timedelta(minutes=1)
    run_due(ctx)
    assert len(calls) == first
    clock.now += ops.RETRY_AFTER
    run_due(ctx)
    assert len(calls) > first


def test_a_job_that_raises_anything_is_a_failure_and_the_loop_carries_on(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: only FAILURES were caught, so a bug in one job ended the loop, and on restart the same
    # unrecorded job was due first again and the jobs after it never ran (Task 13 review).
    ctx = context(tmp_path, writer, ntfy)

    def bug(ctx, tags):
        raise KeyError(TITLE)

    monkeypatch.setitem(ops.JOBS, "check", bug)
    results = {r.job: r for r in run_due(ctx)}
    assert (results["check"].ok, results["check"].detail) == (False, "KeyError")
    assert "restore-test" in results  # the jobs after it still ran
    assert writer.state.last_run("check").detail == "KeyError"
    assert "recordings: backup check failed" in [r["headers"]["Title"] for r in pushes(ntfy)]
    assert "会議" not in json.dumps(ntfy.requests, ensure_ascii=False)


def test_the_loop_logs_a_fixed_line_and_keeps_the_traceback_for_debug(
    tmp_path, writer, ntfy, monkeypatch, caplog
):
    ctx = context(tmp_path, writer, ntfy)

    def broken(*args, **kwargs):
        raise ValueError(TITLE)

    monkeypatch.setattr(ops, "due", broken)
    ticks = []
    with caplog.at_level("INFO"):
        loop(ctx, tick=0, sleep=ticks.append, stop=lambda: len(ticks) >= 1)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and all(r.exc_info is None for r in warnings)
    assert "ValueError" in caplog.text and "会議" not in caplog.text


def test_an_unopenable_lock_file_alerts_and_a_backup_pings_fail(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: §14, a failed backup raises an alert. This failure was recorded but never said, so only
    # the dead-man's switch noticed, after its period and grace.
    class Unopenable:
        def __init__(self, *args, **kwargs):
            pass

        def hold(self, key):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ops, "Locks", Unopenable)
    ctx = context(tmp_path, writer, ntfy)
    assert run_job(ctx, "backup") == ops.JobResult("backup", False, "PermissionError")
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == ["recordings: backup failed"]
    assert pings(ntfy) == ["/ping/fail"]


def test_a_missing_or_changed_archive_marker_has_its_own_title(tmp_path, writer, ntfy):
    # why: an unmounted or wrong archive disk was reported as "disk space low"
    writer.state.set_meta("archive_uuid", "not-this-archive")
    result = run_job(context(tmp_path, writer, ntfy), "disk")
    assert (result.ok, result.detail) == (False, "ArchiveMarkerError")
    (req,) = pushes(ntfy)
    assert req["headers"]["Title"] == "recordings: archive disk missing or changed"
    assert req["body"] == "disk failed (ArchiveMarkerError). See Status."


def test_a_reminder_is_not_held_back_by_an_alert_stamped_in_the_future(tmp_path, writer, ntfy):
    writer.state.set_alert_state(
        "job:backup", failing=True, last_sent_at=T0 + timedelta(days=400)
    )  # a clock that ran ahead
    run_job(context(tmp_path, writer, ntfy, runner=restic(returncode=1)), "backup")
    assert len(pushes(ntfy)) == 1


def test_an_undelivered_alert_is_sent_again_at_the_next_failure(tmp_path, writer, ntfy):
    # why: a refused push must not count as sent, or nothing is said for the next 24 hours
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock, runner=restic(returncode=1))
    ntfy.status = 500
    run_job(ctx, "backup")
    ntfy.status = 200
    clock.now += timedelta(hours=1)
    run_job(ctx, "backup")
    assert len(pushes(ntfy)) == 2
    clock.now += timedelta(hours=1)
    run_job(ctx, "backup")  # delivered at last: now quiet until the daily reminder
    assert len(pushes(ntfy)) == 2


def test_alert_test_refuses_a_topic_with_no_server_but_not_a_server_with_no_topic(
    tmp_path, writer, ntfy, monkeypatch, capsys
):
    # why: a topic with no ntfy_server silently drops every alert; a server with no topic is the
    # template's normal dead-man-only setup and must keep working (Task 13 council)
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    assert main(["alert-test", "--json"]) == 0  # server and topic: both parts tested
    capsys.readouterr()
    monkeypatch.delenv("RECORDINGS_NTFY_TOPIC")
    assert main(["alert-test", "--json"]) == 0  # server, no topic: dead-man only
    assert json.loads(capsys.readouterr().out) == {"ntfy": None, "deadman": True}
    monkeypatch.setenv("RECORDINGS_NTFY_TOPIC", "alerts-topic")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        cfg_file.read_text(encoding="utf-8").replace(f'ntfy_server = "{ntfy.url}"\n', ""),
        encoding="utf-8",
    )
    assert main(["alert-test", "--json"]) == 78  # topic, no server
    out = capsys.readouterr().out
    assert "ntfy_server" in out and "alerts-topic" not in out
    assert len(pushes(ntfy)) == 1  # the first run's test push alone


def test_a_failure_with_a_title_of_its_own_is_said_at_once_and_one_recovery_clears_all(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: the disk job shared one alert state between a low disk and a missing marker, so the
    # flip from one to the other waited for the daily reminder (Task 13 re-review)
    from types import SimpleNamespace

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=0, f_frsize=1024, f_blocks=1000)
    )
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    run_job(ctx, "disk")
    real_uuid = writer.state.meta("archive_uuid")
    writer.state.set_meta("archive_uuid", "not-this-archive")
    clock.now += timedelta(minutes=20)
    run_job(ctx, "disk")
    clock.now += timedelta(minutes=20)
    run_job(ctx, "disk")  # still missing: quiet
    writer.state.set_meta("archive_uuid", real_uuid)
    monkeypatch.setattr(  # plenty free: recovery must not depend on the host's real disk
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=900, f_frsize=1024, f_blocks=1000)
    )
    clock.now += timedelta(minutes=20)
    run_job(ctx, "disk")
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == [
        "recordings: disk space low",
        "recordings: archive disk missing or changed",
        "recordings: disk recovered",
    ]


def test_a_future_stamp_and_a_full_state_disk_still_alert_once(tmp_path, writer, ntfy, monkeypatch):
    # why: a run and an alert row stamped in the future (a clock that ran ahead, then was put
    # right) were taken as newer than the process's own memory, so a full state disk brought back
    # a push a minute (Task 13 re-review)
    from types import SimpleNamespace

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=0, f_frsize=1024, f_blocks=1000)
    )
    future = T0 + timedelta(days=2)
    writer.state.record_run("disk", future - timedelta(minutes=1), future, False, "DiskWarning")
    writer.state.set_alert_state("job:disk", failing=True, last_sent_at=future)
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    for name in ("record_run", "set_alert_state", "set_meta", "prune_runs"):
        monkeypatch.setattr(writer.state, name, full)
    runs = 0
    for _ in range(31):
        runs += sum(r.job == "disk" for r in run_due(ctx))
        clock.now += timedelta(minutes=1)
    assert runs == 3  # minutes 0, 15 and 30: a future stamp must not make the job due every minute
    assert len([r for r in pushes(ntfy) if r["body"].startswith("disk failed")]) == 1


def test_a_marker_failure_that_recovers_alone_is_cleared_and_said_again_when_it_returns(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: one recovery must clear the marker failure's own alert key, not only job:disk's, or the
    # next marker failure within 24 hours is taken for the same one and not said (Task 13 re-review)
    from types import SimpleNamespace

    monkeypatch.setattr(  # 90% free, whatever the host's disk holds
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=900, f_frsize=1024, f_blocks=1000)
    )
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    real_uuid = writer.state.meta("archive_uuid")
    for uuid in ("not-this-archive", real_uuid, "not-this-archive"):
        writer.state.set_meta("archive_uuid", uuid)
        run_job(ctx, "disk")
        clock.now += timedelta(minutes=20)
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == [
        "recordings: archive disk missing or changed",
        "recordings: disk recovered",
        "recordings: archive disk missing or changed",
    ]


def test_the_status_summary_flags_an_old_backup_a_failing_job_and_low_disk(writer, monkeypatch):
    from types import SimpleNamespace

    from recordings.ops import status_summary, when_label

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=150, f_frsize=1024, f_blocks=1000)
    )
    state = writer.state
    state.record_run("backup", T0 - timedelta(hours=3), T0 - timedelta(hours=3), True, "snapshot x")
    # run_job's fixed failure template (Tasks 11 and 13): never the exception's own text
    state.record_run("restore-test", T0, T0, False, "BackupError; exit 1")
    summary = status_summary(
        state,
        archive_root=writer.root,
        now=T0,
        timezone_name="America/Vancouver",
        warn=20.0,
        stop=5.0,
        jobs=("disk", "backup", "restore-test"),
    )
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert set(jobs) == {"backup", "restore-test"}
    assert jobs["backup"]["last_ok"] == "2026-10-08 22:00 (3 h ago)"  # 05:00 UTC is 22:00 PDT
    assert jobs["backup"]["attention"] and not jobs["backup"]["failing"]
    assert jobs["restore-test"]["failing"] and jobs["restore-test"]["last_ok"] is None
    assert jobs["restore-test"]["detail"] == "BackupError; exit 1"
    assert summary["attention"][0] == "The last backup is more than 2 hours old."
    assert "Restore test failed: BackupError; exit 1" in summary["attention"]
    assert summary["disk"]["level"] == "warn" and summary["disk"]["free_percent"] == 15.0
    assert when_label(T0 - timedelta(seconds=20), T0, "UTC") == "2026-10-09 07:59 (just now)"
    assert when_label(T0 - timedelta(days=3), T0, "UTC") == "2026-10-06 08:00 (3 days ago)"


def test_status_shows_the_last_good_runs_detail_with_the_nas_free_space(writer):
    # why: §15.1. "NAS free space unknown" is in a good backup's detail (Task 11), and Status must
    # show it, not only a failing job's detail.
    from recordings.ops import status_summary

    detail = "snapshot 0123456789ab: 11 files, 4096 bytes added; NAS free space unknown"
    writer.state.record_run(
        "backup", T0 - timedelta(minutes=5), T0 - timedelta(minutes=5), True, detail
    )
    writer.state.record_run("restore-test", T0, T0, False, "BackupError; exit 1")
    summary = status_summary(
        writer.state,
        archive_root=writer.root,
        now=T0,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("backup", "restore-test"),
    )
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert jobs["backup"]["last_ok_detail"] == detail and jobs["backup"]["detail"] is None
    assert jobs["restore-test"]["last_ok_detail"] is None


def test_status_names_the_running_job_and_does_not_flag_the_backup_it_delays(writer):
    # why: §12.5. A restore test holds the ops lock for hours, so the hourly backup's turns are
    # skipped; that is not an outage, and Status says what is running and since when.
    from recordings.locks import OPS_LOCK, Locks, announce
    from recordings.ops import status_summary

    writer.state.record_run("backup", T0 - timedelta(hours=3), T0 - timedelta(hours=3), True, "x")
    args = {
        "archive_root": writer.root,
        "now": T0,
        "timezone_name": "UTC",
        "warn": 20.0,
        "stop": 5.0,
        "jobs": ("backup", "restore-test"),
    }
    idle = status_summary(writer.state, **args)
    assert idle["running"] is None
    assert "The last backup is more than 2 hours old." in idle["attention"]
    folder = writer.state.locks_dir
    # the backup turned late at T0 - 1 h; this hold began before that, so it explains the delay
    with (
        Locks(folder, timeout=0).hold(OPS_LOCK),
        announce(folder, "restore-test", T0 - timedelta(minutes=90)),
    ):
        busy = status_summary(writer.state, **args)
    assert busy["running"] == {
        "job": "restore-test",
        "label": "Restore test",
        "since": "2026-10-09 06:30 (1 h ago)",
    }
    assert "The last backup is more than 2 hours old." not in busy["attention"]


def test_a_read_only_state_never_creates_or_writes(writer, tmp_path):
    # why: Status and doctor read state.db while the writer uses it. A reader must never make an
    # empty state.db where none exists, nor change one.
    import sqlite3

    import pytest
    from recordings.state import State

    assert State.read_only(tmp_path / "nowhere") is None
    assert not (tmp_path / "nowhere").exists()
    state = State.read_only(writer.state.path)
    assert state.meta("archive_uuid") == writer.state.meta("archive_uuid")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        state.set_meta("x", "y")


def test_a_read_only_state_reads_what_a_live_writer_just_committed(writer):
    # why: the backup service keeps writing state.db (WAL) while the web service reads it. A
    # committed change in the -wal file, not yet checkpointed, must be visible to the reader.
    from recordings.state import State

    live = writer.state._connect("rw")  # holds the database open, so the WAL files exist
    try:
        live.execute("INSERT INTO meta (key, value) VALUES ('probe', 'wal')")
        live.commit()
        assert State.read_only(writer.state.path).meta("probe") == "wal"
    finally:
        live.close()


def test_run_job_announces_its_job_while_it_runs(tmp_path, writer, ntfy):
    # why: spec §12.5. Status reads the running job from the held ops lock, so run_job must name it.
    from recordings.locks import running

    seen = []

    def runner(cmd, **kwargs):
        seen.append(running(writer.state.locks_dir))
        return restic()(cmd, **kwargs)

    run_job(context(tmp_path, writer, ntfy, runner=runner), "backup")
    assert seen and set(seen) == {("backup", T0)}
    assert running(writer.state.locks_dir) is None  # and says nothing once it is done


def test_a_restore_copy_left_behind_alerts_once_but_a_running_tests_own_never(
    tmp_path, writer, ntfy, monkeypatch
):
    # why: §15.1 (the final review's minor 2). A SIGKILL, the OOM killer (backup's oom_score_adj
    # makes it the first choice, and a restore is its heaviest job) or a power cut leaves the
    # restore test's copy, the whole archive unencrypted, in restore_scratch. Only the next restore
    # test, up to 30 days later, removed it, and only doctor said so meanwhile.
    from types import SimpleNamespace

    from recordings.locks import OPS_LOCK, announce

    monkeypatch.setattr(
        disk.os, "statvfs", lambda p: SimpleNamespace(f_bavail=900, f_frsize=1024, f_blocks=1000)
    )  # 90% free: only the copy is wrong
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    left = tmp_path / "scratch" / "restore-20261009T060000Z"
    left.mkdir(parents=True)
    folder = writer.state.locks_dir
    with Locks(folder, timeout=0).hold(OPS_LOCK), announce(folder, "restore-test", T0):
        assert ops._disk(ctx, ())  # the copy of the restore test running now: nothing to say
    assert run_job(ctx, "disk") == ops.JobResult("disk", False, "RestoreCopyLeft")
    clock.now += timedelta(minutes=20)
    run_job(ctx, "disk")  # still there: quiet until the daily reminder
    left.rmdir()
    clock.now += timedelta(minutes=20)
    assert run_job(ctx, "disk").ok
    assert [(r["headers"]["Title"], r["body"]) for r in pushes(ntfy)] == [
        ("recordings: restore copy left behind", "disk failed (RestoreCopyLeft). See Status."),
        ("recordings: disk recovered", "disk works again. See Status."),
    ]


def test_status_says_when_the_backup_service_last_reported(writer):
    # why: the loop writes a heartbeat at each pass (Task 13), and the backup service has no Docker
    # healthcheck. A stopped loop must show on Status within minutes, not only once the backup is
    # 2 hours late, and also where no backup is set up.
    from recordings.ops import status_summary

    args = {
        "archive_root": writer.root,
        "now": T0,
        "timezone_name": "UTC",
        "warn": 20.0,
        "stop": 5.0,
        "jobs": ("disk", "backup", "restore-test"),
    }
    writer.state.record_run(
        "backup", T0 - timedelta(minutes=50), T0 - timedelta(minutes=50), True, "x"
    )
    writer.state.set_meta("ops_heartbeat", (T0 - timedelta(minutes=1)).isoformat())
    live = status_summary(writer.state, **args)
    assert live["reported"] == "2026-10-09 07:59 (1 min ago)"
    assert not any("backup service" in a for a in live["attention"])
    writer.state.set_meta("ops_heartbeat", (T0 - timedelta(minutes=50)).isoformat())
    quiet = status_summary(writer.state, **args)
    assert (
        "The backup service last reported 2026-10-09 07:10 (50 min ago): check that it is running."
    ) in quiet["attention"]
    assert "The last backup is more than 2 hours old." not in quiet["attention"]
    # A long job leaves the heartbeat old until the next pass, but its own run has just reported.
    writer.state.record_run(
        "restore-test",
        T0 - timedelta(hours=3),
        T0 - timedelta(minutes=1),
        True,
        "restored and validated 3 recordings",
    )
    after = status_summary(writer.state, **args)
    assert after["reported"] == "2026-10-09 07:59 (1 min ago)"
    assert not any("backup service" in a for a in after["attention"])
    # A heartbeat stamped in the future (a clock put right) proves nothing; nor is a loop that
    # never reported missed where no backup is set up.
    writer.state.set_meta("ops_heartbeat", (T0 + timedelta(days=1)).isoformat())
    alone = status_summary(writer.state, **{**args, "jobs": ("disk",)})
    assert alone["reported"] is None
    assert (
        "The backup service has not reported yet: check that it is running." in alone["attention"]
    )


def test_a_job_holding_ops_excuses_a_late_backup_for_12_hours_at_most(writer):
    # why: a hung restic or rsync, or a CLI job left running, holds "ops" with no end. The
    # dead-man's switch alerts after its grace (§14); Status must not stay green for days.
    from recordings.locks import OPS_LOCK, Locks, announce
    from recordings.ops import status_summary

    writer.state.record_run("backup", T0 - timedelta(hours=3), T0 - timedelta(hours=3), True, "x")
    args = {
        "archive_root": writer.root,
        "now": T0,
        "timezone_name": "UTC",
        "warn": 20.0,
        "stop": 5.0,
        "jobs": ("backup",),
    }
    folder = writer.state.locks_dir
    for held_for, excused in (
        (timedelta(hours=11), True),
        (timedelta(hours=13), False),
        (-timedelta(hours=1), False),
    ):  # a start in the future: long ago
        with Locks(folder, timeout=0).hold(OPS_LOCK), announce(folder, "check", T0 - held_for):
            summary = status_summary(writer.state, **args)
        (backup,) = summary["jobs"]
        assert backup["waiting"] is excused and backup["attention"] is not excused
        assert ("The last backup is more than 2 hours old." in summary["attention"]) is not excused
        stuck = [a for a in summary["attention"] if a.startswith("Repository check has been")]
        assert bool(stuck) is not excused
        assert not any("backup service" in a for a in summary["attention"])  # the hold explains it
    assert stuck == [
        (
            "Repository check has been running since 2026-10-09 09:00 (clock ahead), and "
            "no backup runs until it ends: check that it isn't stuck."
        )
    ]


def test_status_shows_retention_whose_alert_says_see_status(writer):
    # why: every alert ends "See Status" (§14), and a failed retention alerts.
    from recordings.ops import status_summary

    writer.state.record_run("prune", T0, T0, False, "BackupError; exit 1")
    summary = status_summary(
        writer.state,
        archive_root=writer.root,
        now=T0,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("backup", "prune"),
    )
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert jobs["prune"]["label"] == "Retention" and jobs["prune"]["failing"]
    assert "Retention failed: BackupError; exit 1" in summary["attention"]


def test_every_overdue_shown_job_says_so_in_needs_attention(writer):
    # why: a red "Overdue" row with nothing in the box above it leaves Dan to find it. The box
    # lists every row that is red (Task 15's council).
    from recordings.ops import status_summary

    old = T0 - timedelta(days=40)  # past 2 hours, 8 days and 31 days
    for job in ("restore-test", "check", "mirror"):
        writer.state.record_run(job, old, old, True, "x")
    summary = status_summary(
        writer.state,
        archive_root=writer.root,
        now=T0,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("mirror", "prune", "check", "restore-test"),
    )
    last = "2026-08-30 08:00 (40 days ago)"
    for label in ("Mirror for the Mac", "Repository check", "Restore test"):
        assert f"{label} is overdue: it last worked {last}." in summary["attention"]
    assert "Retention has not worked yet." in summary["attention"]
    fresh = status_summary(
        writer.state,
        archive_root=writer.root,
        now=old + timedelta(hours=1),
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("check", "mirror"),
    )
    assert not any("overdue" in a for a in fresh["attention"])


def test_a_hold_excuses_the_mirror_too_and_only_if_it_began_before_the_job_was_late(writer):
    # why: §12.5. The mirror's turns are skipped while a job holds ops, as the backup's are, so
    # neither is Overdue for that. But a hold that began after a job was already late explains
    # nothing: only the lateness that came first is excused (Task 15's council).
    from recordings.locks import OPS_LOCK, Locks, announce
    from recordings.ops import status_summary

    for job in ("backup", "mirror"):
        writer.state.record_run(job, T0 - timedelta(hours=3), T0 - timedelta(hours=3), True, "x")
    args = {
        "archive_root": writer.root,
        "now": T0,
        "timezone_name": "UTC",
        "warn": 20.0,
        "stop": 5.0,
        "jobs": ("backup", "mirror"),
    }
    folder = writer.state.locks_dir
    # Both turned late at T0 - 1 h. A hold from before that excuses them; one from after doesn't.
    for since, excused in ((T0 - timedelta(minutes=90), True), (T0 - timedelta(minutes=30), False)):
        with Locks(folder, timeout=0).hold(OPS_LOCK), announce(folder, "check", since):
            summary = status_summary(writer.state, **args)
        for row in summary["jobs"]:
            assert row["waiting"] is excused and row["attention"] is not excused, row["job"]
        assert ("The last backup is more than 2 hours old." in summary["attention"]) is not excused
        assert (
            any(a.startswith("Mirror for the Mac is overdue") for a in summary["attention"])
            is not excused
        )
        # a hold that is merely recent is not "stuck": only a long or future one is
        assert not any("check that it isn't stuck" in a for a in summary["attention"])


def test_a_future_stamp_counts_as_long_ago_and_is_not_just_now(writer):
    # why: due() and §15.1 treat a run stamped in the future (a clock put right) as long ago.
    # Status must too: otherwise it shows "OK, just now" for a loop that has stopped recording.
    from recordings.ops import status_summary, when_label

    writer.state.record_run("backup", T0 + timedelta(days=1), T0 + timedelta(days=1), True, "x")
    summary = status_summary(
        writer.state,
        archive_root=writer.root,
        now=T0,
        timezone_name="UTC",
        warn=20.0,
        stop=5.0,
        jobs=("backup",),
    )
    (backup,) = summary["jobs"]
    assert backup["attention"] and "just now" not in backup["last_ok"]
    assert "The last backup is more than 2 hours old." in summary["attention"]
    assert when_label(T0 + timedelta(minutes=5), T0, "UTC") == "2026-10-09 08:05 (clock ahead)"


def test_the_flags_line_agrees_in_number_and_has_no_markup(writer):
    # why: the line is shown as plain text, so a backtick would appear literally.
    from recordings.ops import status_summary

    args = {
        "archive_root": writer.root,
        "now": T0,
        "timezone_name": "UTC",
        "warn": 20.0,
        "stop": 5.0,
        "jobs": ("disk",),
    }
    writer.state.flag("recordings/a/recording.json", "outside-edit", "x")
    one = status_summary(writer.state, **args)
    assert "1 file needs attention: run recordings reindex." in one["attention"]
    writer.state.flag("recordings/b/recording.json", "outside-edit", "x")
    two = status_summary(writer.state, **args)
    assert "2 files need attention: run recordings reindex." in two["attention"]


def test_a_read_only_state_makes_every_connection_read_only(writer, tmp_path, monkeypatch):
    # why: State.read_only promises that no connection it makes can write, backup_to's too.
    from recordings.state import State

    state = State.read_only(writer.state.path)
    modes = []
    real = State._connect

    def spy(self, mode):
        modes.append(mode)
        return real(self, mode)

    monkeypatch.setattr(State, "_connect", spy)
    state.backup_to(tmp_path / "copy" / "state.db")
    state.meta("archive_uuid")
    assert modes == ["ro", "ro"]


def test_a_full_state_disk_warns_about_the_heartbeat_once_an_hour(
    tmp_path, writer, ntfy, monkeypatch, caplog
):
    # why: a full state disk fails the heartbeat and the trim at every tick; the log said so every
    # minute, which buries the one line that matters (Task 13's council).
    import logging

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    for name in ("record_run", "set_alert_state", "set_meta", "prune_runs"):
        monkeypatch.setattr(writer.state, name, full)
    with caplog.at_level(logging.DEBUG, logger="recordings.ops"):
        for _ in range(90):
            run_due(ctx)
            clock.now += timedelta(minutes=1)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    for text in ("could not write the heartbeat", "could not trim the old runs"):
        assert len([r for r in warned if r.getMessage() == text]) == 2, text  # minute 0 and 60


def test_the_hourly_warnings_start_again_once_the_state_disk_recovers(
    tmp_path, writer, ntfy, monkeypatch, caplog
):
    # why: a second full-disk episode within the hour of the first must still say so once.
    import logging

    broken = {"on": True}

    def flaky(real):
        def call(*args, **kwargs):
            if broken["on"]:
                raise sqlite3.OperationalError("database or disk is full")
            return real(*args, **kwargs)

        return call

    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    for name in ("record_run", "set_alert_state", "set_meta", "prune_runs"):
        monkeypatch.setattr(writer.state, name, flaky(getattr(writer.state, name)))
    texts = ("could not write the heartbeat", "could not trim the old runs")

    def warnings() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    with caplog.at_level(logging.DEBUG, logger="recordings.ops"):
        for _ in range(3):  # a full disk: one line each, then debug
            run_due(ctx)
            clock.now += timedelta(minutes=1)
        assert [w for w in warnings() if w in texts] == list(texts)
        broken["on"] = False  # recovered
        run_due(ctx)
        clock.now += timedelta(minutes=1)
        assert ctx.warned.keys().isdisjoint({"heartbeat", "trim"})
        broken["on"] = True  # full again, 5 minutes after the first line
        run_due(ctx)
    # The trim is a daily job: it isn't tried again after it succeeded, so only the heartbeat is.
    assert [w for w in warnings() if w in texts] == [*texts, texts[0]]


def test_a_full_state_disk_logs_each_jobs_unrecorded_run_once_an_hour(
    tmp_path, writer, ntfy, monkeypatch, caplog
):
    # why: _conclude's "could not record the run" repeated at every attempt (every 10 minutes for
    # the disk job); the runbook says these lines come once an hour.
    import logging

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock)
    for name in ("record_run", "set_alert_state", "set_meta", "prune_runs"):
        monkeypatch.setattr(writer.state, name, full)
    with caplog.at_level(logging.DEBUG, logger="recordings.ops"):
        for _ in range(90):
            run_due(ctx)
            clock.now += timedelta(minutes=1)
    unrecorded = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "could not record the run" in r.getMessage()
    ]
    attempts = [r for r in caplog.records if "could not record the run" in r.getMessage()]
    assert len(attempts) > len(unrecorded) >= 1  # the job was attempted more than once
    per_job = {}
    for r in unrecorded:
        per_job[r.getMessage().split(":")[0]] = per_job.get(r.getMessage().split(":")[0], 0) + 1
    assert max(per_job.values()) <= 2, per_job  # minute 0 and 60, never more
