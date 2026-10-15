import json
import os
import sqlite3
import subprocess
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from recordings import disk, ops
from recordings.cli import main
from recordings.config import load_config
from recordings.locks import Locks
from recordings.ops import Context, due, enabled_jobs, loop, run_due, run_job

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
SUMMARY = json.dumps({"message_type": "summary", "snapshot_id": "f" * 64,
                      "total_files_processed": 1, "data_added": 1})
TITLE = "会議メモ / Q&A"  # Review Focus 4: a synthetic non-ASCII title


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def restic(returncode=0, stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, SUMMARY if returncode == 0 else "", stderr)
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
        f'[alerts]\nntfy_server = "{ntfy.url}"\n{mirror_section}', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RESTIC_PASSWORD_FILE": str(tmp_path / "pw"),
               "RECORDINGS_NTFY_TOPIC": "alerts-topic", "RECORDINGS_DEADMAN_URL": f"{ntfy.url}/ping",
               "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return Context(cfg=load_config(environ), environ=environ, state=writer.state,
                   clock=clock or Clock(), runner=runner or restic())


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
        "disk", "backup", "prune", "check", "restore-test")
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
        tmp_path, writer, make_incoming, ntfy):
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
        ("recordings: backup recovered", "backup works again. See Status.")]
    assert pings(ntfy) == ["/ping/start", "/ping/fail"] * 3 + ["/ping/start", "/ping"]


@pytest.mark.parametrize("runner, detail", [
    (restic(returncode=1, stderr=f"Fatal: open /archive/recordings/2026/10/x/{TITLE}.md: "
                                 "permission denied\n"), "BackupError; exit 1"),
    (raising(PermissionError(13, "Permission denied", f"/archive/{TITLE}.md")), "PermissionError"),
], ids=["restic-stderr", "oserror-path"])
def test_no_alert_or_stored_run_carries_an_exceptions_text(
        tmp_path, writer, ntfy, caplog, runner, detail):
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
    monkeypatch.setattr(disk.os, "statvfs", lambda p: SimpleNamespace(
        f_bavail=150, f_frsize=1024, f_blocks=1000))
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
        tmp_path, writer, ntfy, monkeypatch):
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
        tmp_path, writer, ntfy, monkeypatch):
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
    loop(ctx, tick=0, sleep=lambda s: ticks.append(s), stop=lambda: len(ticks) >= 2)
    assert len(ticks) == 2


def test_old_runs_are_trimmed_daily_but_each_jobs_newest_good_run_stays(tmp_path, writer, ntfy):
    # why: ops_runs gets about 70,000 rows a year. Status must still know a job's last good run
    # after a long failing streak, so that row is kept however old it is.
    state = writer.state
    old = T0 - timedelta(days=200)
    state.record_run("backup", old, old, True, "snapshot a")                   # newest good: kept
    state.record_run("check", old, old, True, "old good, superseded")           # dropped
    state.record_run("check", old + timedelta(days=1), old + timedelta(days=1), True, "kept")
    for day in range(2, 150):                                                  # a failing streak
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
        assert db.execute("SELECT COUNT(*) FROM ops_runs WHERE detail = 'ancient'").fetchone()[0] == 1
    ctx.clock.now += timedelta(days=1)
    ops._trim_runs(ctx)
    with closing(sqlite3.connect(state.db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM ops_runs WHERE detail = 'ancient'").fetchone()[0] == 0


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
    ctx = context(tmp_path, writer, ntfy,
                  runner=restic(11, "Fatal: repository is already locked exclusively"))
    result = run_job(ctx, "backup", lock_timeout=0)
    assert result.detail == "skipped: busy (repository locked)" and not result.ok
    assert writer.state.last_run("backup") is None and pushes(ntfy) == []
    assert pings(ntfy) == ["/ping/start"]  # started, never finished: the dead-man's switch decides


def test_the_loop_runs_until_told_to_stop(tmp_path, writer, ntfy):
    ctx = context(tmp_path, writer, ntfy)
    ticks = []
    loop(ctx, tick=0, sleep=lambda s: ticks.append(s), stop=lambda: len(ticks) >= 3)
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
    monkeypatch.setattr(ops, "loop", lambda ctx, **kwargs: events.append(
        f"loop retry_lock={ctx.retry_lock}"))
    assert main(["ops", "--loop"]) == 0
    assert events == ["forwarding", "loop retry_lock=None", "restored"]


def test_alert_test_sends_one_logs_the_check_and_never_prints_the_topic(
        tmp_path, writer, ntfy, monkeypatch, capsys):
    ctx = context(tmp_path, writer, ntfy)
    own_environment(ctx, monkeypatch)
    assert main(["alert-test", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"ntfy": True, "deadman": True}
    assert "alerts-topic" not in out
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == ["recordings: test alert"]
    assert pings(ntfy) == ["/ping/log"]  # /log: the check's state stays as it is


def test_alert_test_can_test_the_dead_mans_switch_alone_and_fails_when_a_ping_fails(
        tmp_path, writer, ntfy, monkeypatch, capsys):
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
    monkeypatch.delenv("RECORDINGS_NTFY_TOPIC")
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
    monkeypatch.setattr(disk.os, "statvfs", lambda p: SimpleNamespace(
        f_bavail=0, f_frsize=1024, f_blocks=1000))
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
    for _ in range(5):
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
        tmp_path, writer, ntfy, monkeypatch):
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
        tmp_path, writer, ntfy, monkeypatch, caplog):
    ctx = context(tmp_path, writer, ntfy)

    def broken(*args, **kwargs):
        raise ValueError(TITLE)

    monkeypatch.setattr(ops, "due", broken)
    ticks = []
    with caplog.at_level("INFO"):
        loop(ctx, tick=0, sleep=lambda s: ticks.append(s), stop=lambda: len(ticks) >= 1)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and all(r.exc_info is None for r in warnings)
    assert "ValueError" in caplog.text and "会議" not in caplog.text


def test_an_unopenable_lock_file_alerts_and_a_backup_pings_fail(
        tmp_path, writer, ntfy, monkeypatch):
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
    writer.state.set_alert_state("job:backup", failing=True,
                                 last_sent_at=T0 + timedelta(days=400))  # a clock that ran ahead
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
