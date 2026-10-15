import hashlib
import importlib.util
import inspect
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from recordings import backup, cli, ops
from recordings.backup import BackupError, Keep
from recordings.config import ConfigError, load_config, secret_path
from recordings.locks import Locks, LockTimeout
from recordings.ops import Context, JobResult, run_job
from recordings.selfdoc import validate
from recordings.sentinel import SENTINEL
from recordings.ssh import Nas, nas_free_bytes, ssh_options

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
# Read once, at import, so the fixture that clears RECORDINGS_* (Task 14) can never hide them.
REQUIRE_RESTIC = os.environ.get("RECORDINGS_REQUIRE_RESTIC") == "1"
TEST_RESTIC = os.environ.get("RECORDINGS_TEST_RESTIC")


def restic_or_skip() -> str:
    """The pinned restic; else a skip, or in CI (RECORDINGS_REQUIRE_RESTIC=1) a failure, because
    a skip there would hide a broken backup."""
    for candidate in (TEST_RESTIC, str(REPO / ".cache" / "bin" / "restic"), shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    if REQUIRE_RESTIC:
        pytest.fail("RECORDINGS_REQUIRE_RESTIC=1, but restic is not installed: run `make restic`")
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def config(tmp_path, writer, **backup_keys) -> tuple:
    deploy = tmp_path / "deploy.env"
    deploy.write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir(exist_ok=True)
    keys = {
        "repository": str(tmp_path / "repo"),
        "extra_paths": [str(deploy)],
        "restore_scratch": str(tmp_path / "scratch"),
        "cache_dir": str(tmp_path / "cache"),
        **backup_keys,
    }
    lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in keys.items())
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{writer.state.path}"\n[index]\npath = "{writer.index.path}"\n'
        f"[backup]\n{lines}\n",
        encoding="utf-8",
    )
    (tmp_path / "pw").write_text("a test password\n", encoding="utf-8")
    (tmp_path / "known_hosts").write_text("nas ssh-ed25519 AAAAexample\n", encoding="utf-8")
    environ = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "RECORDINGS_CONFIG": str(cfg_file),
        "RESTIC_PASSWORD_FILE": str(tmp_path / "pw"),
        "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(tmp_path / "known_hosts"),
    }
    return load_config(environ), environ


VERBS = ("init", "unlock", "backup", "forget", "check", "restore")


def verb(cmd: list[str]) -> str:
    return next(a for a in cmd[1:] if a in VERBS)


class FakeRestic:
    """Records each restic call and answers like restic's --json output does. With `only`, the
    return code and stderr apply to those restic commands, and the rest succeed."""

    def __init__(self, returncode=0, stdout="", stderr="", on_call=None, only=None):
        self.calls, self.returncode, self.stdout, self.stderr = [], returncode, stdout, stderr
        self.on_call, self.only = on_call, only

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs["env"]))
        if self.on_call:
            self.on_call(cmd)
        if self.only is not None and verb(cmd) not in self.only:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def restore_like_restic(writer, bc, cmd, *, state_copy=None) -> tuple[Path, Path]:
    """What `restic restore --target T` writes: every backed-up path under T at its absolute
    path, the state.db copy (`state_copy`, or one taken now) included. Returns (T, the archive's
    copy)."""
    target = Path(cmd[cmd.index("--target") + 1])
    copy = target / writer.root.relative_to(writer.root.anchor)
    shutil.copytree(writer.root, copy)
    for path in bc.paths:
        (target / path.relative_to(path.anchor)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / path.relative_to(path.anchor))
    dest = target / backup.state_copy_path(bc).relative_to(bc.state.anchor)
    if state_copy is None:
        writer.state.backup_to(dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_copy, dest)
    return target, copy


SUMMARY = json.dumps(
    {
        "message_type": "summary",
        "snapshot_id": "f" * 64,
        "total_files_processed": 12,
        "data_added": 4096,
    }
)


def test_the_backup_names_its_paths_and_never_the_password(tmp_path, writer, make_incoming):
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout='{"message_type":"status"}\n' + SUMMARY + "\n")
    result = backup.run_backup(bc, writer.state, environ, tags=("pre-import",), runner=fake)
    assert result == backup.BackupResult(snapshot_id="f" * 64, files=12, data_added=4096)
    (unlock, _), (cmd, env) = fake.calls
    assert unlock == [bc.restic, "unlock"]
    state_copy = writer.state.path / "backup" / "state.db"
    assert cmd[:3] == [bc.restic, "backup", "--json"]
    assert cmd[cmd.index("--host") + 1] == "test"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tag"] == ["recordings", "pre-import"]
    assert cmd[-4:] == [
        str(writer.root),
        str(cfg.path),
        str(tmp_path / "deploy.env"),
        str(state_copy),
    ]
    assert str(writer.root / ".tmp") in cmd and ".*.tmp" in cmd
    assert "a test password" not in " ".join(cmd)  # it reaches restic only through its environment
    assert env["RESTIC_PASSWORD_FILE"] == str(tmp_path / "pw")
    assert env["RESTIC_REPOSITORY"] == str(tmp_path / "repo")
    assert set(env) <= {
        "PATH",
        "HOME",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_PASSWORD",
        "RESTIC_CACHE_DIR",
        "RESTIC_REST_USERNAME",
        "RESTIC_REST_PASSWORD",
    }
    assert state_copy.is_file()
    assert "voice.db" not in " ".join(cmd) and "plaud" not in " ".join(
        cmd
    )  # §15.1: never backed up


def test_the_state_db_copy_is_a_consistent_sqlite_backup(writer):
    writer.state.set_meta("marker", "copied")
    copy = writer.state.backup_to(writer.state.path / "backup" / "state.db")
    import sqlite3

    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone() == ("copied",)


def test_no_backup_runs_without_the_sentinel_or_with_another_archive(tmp_path, writer):
    # why: §6.7, §15.1. An unmounted archive backed up hourly would age the real snapshots out.
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout=SUMMARY)
    writer.state.set_meta("archive_uuid", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(BackupError, match="UUID"):
        backup.run_backup(bc, writer.state, environ, runner=fake)
    (writer.root / SENTINEL).unlink()
    with pytest.raises(BackupError, match="mounted"):
        backup.run_backup(bc, writer.state, environ, runner=fake)
    assert fake.calls == []


def test_a_missing_path_or_password_is_refused_before_restic_runs(tmp_path, writer):
    cfg, environ = config(tmp_path, writer, extra_paths=[str(tmp_path / "nope.env")])
    bc = backup.from_config(cfg, environ)
    with pytest.raises(BackupError, match="nope.env"):
        backup.run_backup(bc, writer.state, environ, runner=FakeRestic(stdout=SUMMARY))
    cfg, environ = config(tmp_path, writer)
    del environ["RESTIC_PASSWORD_FILE"]
    with pytest.raises(BackupError, match="password"):
        backup.run_backup(
            backup.from_config(cfg, environ),
            writer.state,
            environ,
            runner=FakeRestic(stdout=SUMMARY),
        )


@pytest.mark.parametrize(
    "returncode, stderr",
    [
        (1, "Fatal: unable to open repository at rest:https://recordings:hunter2@nas:8000/r/\n"),
        (-9, ""),  # killed: §20 "a killed backup raises an alert" starts here
        (12, "Fatal: wrong password or no key found\n"),
    ],
)
def test_a_failed_restic_is_an_error_with_no_secret_in_it(tmp_path, writer, returncode, stderr):
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(returncode=returncode, stderr=stderr, only=("backup",))
    with pytest.raises(BackupError) as caught:
        backup.run_backup(backup.from_config(cfg, environ), writer.state, environ, runner=fake)
    assert "hunter2" not in str(caught.value) and f"exit {returncode}" in str(caught.value)
    assert caught.value.returncode == returncode


@pytest.mark.parametrize(
    "repository",
    [
        "rest:https://recordings:hunter2@nas:8000/recordings/",
        "sftp://backup:hunter2@nas//backups/recordings",
    ],
)
def test_a_repository_with_a_password_in_it_is_refused(tmp_path, writer, repository):
    # why: config.toml is not a secret (§5). The password goes in RESTIC_REST_PASSWORD_FILE.
    cfg, environ = config(tmp_path, writer, repository=repository)
    with pytest.raises(ConfigError, match="carries credentials") as caught:
        backup.from_config(cfg, environ)
    assert "hunter2" not in str(caught.value)


def test_retention_keeps_the_spec_counts_and_the_milestones(tmp_path, writer):
    cfg, environ = config(tmp_path, writer)
    cmd = backup.forget_command(backup.from_config(cfg, environ))
    for flag, value in (
        ("--keep-hourly", "24"),
        ("--keep-daily", "14"),
        ("--keep-weekly", "8"),
        ("--keep-monthly", "12"),
        ("--host", "test"),
        ("--tag", "recordings"),
    ):
        assert cmd[cmd.index(flag) + 1] == value, flag
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--keep-tag"] == [
        "pre-import",
        "post-import",
    ]
    assert "--prune" in cmd
    assert backup.from_config(cfg, environ).keep == Keep()


def test_unlock_runs_before_every_job_that_locks_the_repository(tmp_path, writer, make_incoming):
    # why: restic never treats another process's lock as stale on its own; only `restic unlock`
    # removes one. A redeploy mid-check would otherwise fail every later check and backup.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout=SUMMARY)
    backup.run_backup(bc, writer.state, environ, runner=fake)
    backup.run_check(bc, environ, now=NOW, runner=fake)
    backup.run_forget(bc, environ, runner=fake)
    assert [verb(cmd) for cmd, _ in fake.calls] == [
        "unlock",
        "backup",
        "unlock",
        "check",
        "unlock",
        "forget",
    ]
    check = fake.calls[3][0]
    assert f"--read-data-subset={backup.check_subset(NOW)}" in check


def test_the_weekly_check_reads_a_different_quarter_each_week():
    # why: a plain `restic check` never reads the pack files back; a quarter a week reads them all
    # every four weeks.
    assert {backup.check_subset(NOW + timedelta(weeks=w)) for w in range(4)} == {
        "1/4",
        "2/4",
        "3/4",
        "4/4",
    }


def test_a_failed_check_reads_the_same_quarter_again_until_it_passes(tmp_path, writer):
    # why: a retry that read another quarter would pass and look like a recovery, while the
    # damaged pack waited weeks for its quarter to come round again (ops council).
    cfg, environ = config(tmp_path, writer)
    now = [NOW]
    fake = FakeRestic(returncode=1, stderr="Fatal: repository contains errors", only=("check",))
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: now[0], runner=fake)
    assert run_job(ctx, "check").detail == "BackupError; exit 1"
    now[0] += timedelta(weeks=1)  # another calendar week: its quarter must wait its turn
    assert not run_job(ctx, "check").ok
    fake.returncode = 0
    assert run_job(ctx, "check").ok and run_job(ctx, "check").ok
    first = backup.check_subset(NOW)
    after = ops.QUARTERS[(ops.QUARTERS.index(first) + 1) % 4]
    assert [
        a.removeprefix("--read-data-subset=")
        for cmd, _ in fake.calls
        if verb(cmd) == "check"
        for a in cmd
        if a.startswith("--read-data-subset=")
    ] == [first, first, first, after]


def test_restics_whole_report_reaches_the_debug_log_and_nowhere_else(tmp_path, writer, caplog):
    # why: restic check names the damaged packs and the repair command on lines before its last,
    # which says only "Fatal: repository contains errors". `backup check --debug` must show them.
    cfg, environ = config(tmp_path, writer)
    report = (
        "pack f5261ce8 contains 2 errors\nrestic repair packs f5261ce8\n"
        "rest:https://recordings:hunter2@nas:8000/ answered 500\n"
        "Fatal: repository contains errors\n"
    )
    fake = FakeRestic(returncode=1, stderr=report, only=("check",))
    with caplog.at_level("DEBUG", logger="recordings.backup"), pytest.raises(BackupError) as caught:
        backup.run_check(backup.from_config(cfg, environ), environ, now=NOW, runner=fake)
    assert "restic repair packs f5261ce8" in caplog.text and "https://***@nas" in caplog.text
    assert "hunter2" not in caplog.text
    assert str(caught.value).endswith("Fatal: repository contains errors")


def test_retry_lock_and_the_rest_server_certificate_reach_restic(tmp_path, writer):
    cert = tmp_path / "rest-server.crt"
    cert.write_text("not a real certificate\n", encoding="utf-8")
    cfg, environ = config(
        tmp_path,
        writer,
        repository="rest:https://nas:8000/recordings/",
        rest_username="recordings",
        cacert=str(cert),
    )
    bc = backup.from_config(cfg, environ)
    base = backup.restic_base(replace(bc, retry_lock="5m"))
    assert base[base.index("--cacert") + 1] == str(cert)
    assert base[base.index("--retry-lock") + 1] == "5m"
    assert "--retry-lock" not in backup.restic_base(bc)  # the loop's: it tries again later
    cert.write_text("", encoding="utf-8")  # left empty: http over the tailnet (runbook 1b)
    assert "--cacert" not in backup.restic_base(backup.from_config(cfg, environ))


def test_the_runbook_downloads_the_restic_the_tests_pin():
    # why: two copies of one checksum drift apart. The runbook's spike fetches restic itself.
    spec = importlib.util.spec_from_file_location(
        "fetch_restic", REPO / "scripts" / "fetch_restic.py"
    )
    fetch_restic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_restic)
    pinned = dict(fetch_restic.PINNED.values())
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    url = f"github.com/restic/restic/releases/download/v{fetch_restic.VERSION}/"
    blocks = [b for b in runbook.split("```") if url in b]
    found = [pair for b in blocks for pair in re.findall(r"ARCH=(\w+) SUM=([0-9a-f]{64})", b)]
    assert blocks and found, "the runbook no longer downloads the pinned restic"
    for arch, sha in found:
        assert pinned.get(arch) == sha, arch


def test_sftp_takes_the_nas_key_and_host_key_from_secret_files(tmp_path, writer):
    (tmp_path / "key").write_text("not a real key\n", encoding="utf-8")
    cfg, environ = config(tmp_path, writer, repository="sftp:backup@nas:/backups/r")
    environ |= {"RECORDINGS_NAS_SSH_KEY_FILE": str(tmp_path / "key")}
    base = backup.restic_base(backup.from_config(cfg, environ))
    (option,) = [a for a in base if a.startswith("sftp.args=")]
    assert f"-i {tmp_path / 'key'}" in option and "StrictHostKeyChecking=yes" in option
    assert "not a real key" not in " ".join(base)


def test_the_port_is_an_ssh_option_so_sftp_and_rsync_read_it_too():
    # why: sftp's -p means "keep the times", and its port flag is -P. `-o Port=` means the port
    # to ssh, sftp and rsync -e alike.
    options = ssh_options(None, None, 2222)
    assert options[options.index("Port=2222") - 1] == "-o" and "-p" not in options


def test_a_nas_that_is_off_is_given_up_on_in_seconds():
    # why: without ConnectTimeout, ssh waits out TCP's own timeout (minutes) on a NAS that is off,
    # and doctor and every backup's free-space check wait with it, twice (ssh, then sftp).
    options = ssh_options(None, None, None)
    assert options[options.index("ConnectTimeout=10") - 1] == "-o"


def test_too_little_space_on_the_nas_stops_the_backup(tmp_path, writer):
    # why: §15.1. The NAS's free space is checked before backups start.
    cfg, environ = config(tmp_path, writer, nas_repo_path="/recordings-mirror", min_nas_free_gb=10)
    cfg.data["nas"] = {"ssh": "mirror@nas"}
    bc = backup.from_config(cfg, environ)

    def df(cmd, **kwargs):
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(
                cmd,
                0,
                "Filesystem 1024-blocks Used Available "
                "Capacity Mounted on\n"
                "/dev/md2 100 90 1048576 90% /volume1\n",
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, SUMMARY, "")

    with pytest.raises(backup.NasLowOnSpace, match="free on the NAS"):
        backup.run_backup(bc, writer.state, environ, runner=df)


SFTP_DF = (
    'sftp> df "/recordings-mirror"\n'
    "        Size         Used        Avail       (root)    %Capacity\n"
    "  1048576000   1047527424      1048576      1048576          99%\n"
)


def test_without_a_shell_the_free_space_comes_from_sftp_df():
    # why: §15.1. The NAS accounts are not admins, so they have no shell; OpenSSH's sftp still
    # answers `df`, through the statvfs@openssh.com extension.
    nas = Nas(ssh="mirror@nas", port=2222, key=None, known_hosts=None)
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, 1, "", "This account is not available\n")
        return subprocess.CompletedProcess(cmd, 0, SFTP_DF, "")

    assert nas_free_bytes(nas, "/recordings-mirror", runner=runner) == 1048576 * 1024
    sftp, stdin = calls[1]
    assert sftp[:3] == ["sftp", "-b", "-"] and sftp[-1] == "mirror@nas" and "Port=2222" in sftp
    assert stdin == 'df "/recordings-mirror"\n'


def test_the_free_space_is_unknown_when_neither_df_answers():
    nas = Nas(ssh="mirror@nas", port=None, key=None, known_hosts=None)
    failing = lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 255, "", "")
    assert nas_free_bytes(nas, "/recordings-mirror", runner=failing) is None
    calls = []
    assert nas_free_bytes(nas, "/x\nrm -r /", runner=lambda cmd, **kw: calls.append(cmd)) is None
    assert calls == []  # a newline would start another sftp command


def test_the_backup_says_how_much_the_nas_has_free_or_that_it_is_unknown(
    tmp_path, writer, make_incoming
):
    # why: §15.1. When neither ssh's df nor sftp's df answers, the NAS's free space is "unknown",
    # and the backup's recorded detail says so rather than nothing.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer, nas_repo_path="/recordings-mirror")
    cfg.data["nas"] = {"ssh": "mirror@nas"}

    def runner(answers):
        def run(cmd, **kwargs):
            if cmd[0] in ("ssh", "sftp"):
                return subprocess.CompletedProcess(
                    cmd, 0 if answers else 255, answers if cmd[0] == "ssh" else "", ""
                )
            return subprocess.CompletedProcess(cmd, 0, SUMMARY, "")

        return run

    df = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/md2 100 90 20971520 10% /volume1\n"
    )
    for answers, ending in (("", "; NAS free space unknown"), (df, "; 20 GB free on the NAS")):
        ctx = Context(
            cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=runner(answers)
        )
        result = run_job(ctx, "backup")
        assert result.ok and result.detail.endswith(ending), result.detail


def test_a_restored_copy_with_a_truncated_file_fails_the_restore_test(
    tmp_path, writer, make_incoming
):
    # Review Focus 5: a restore must be proven byte for byte, not by the files being there.
    rec = writer.add(make_incoming(b"ID3 " + b"x" * 4096)).recording
    cfg, environ = config(tmp_path, writer)

    def restore(cmd):
        if "restore" in cmd:
            _, copy = restore_like_restic(writer, backup.from_config(cfg, environ), cmd)
            media = copy / "recordings" / "2026" / "10" / rec.id / rec.media.file
            media.chmod(0o644)
            media.write_bytes(media.read_bytes()[:100])

    fake = FakeRestic(on_call=restore)
    result = backup.run_restore_test(
        backup.from_config(cfg, environ),
        environ,
        expected_uuid=writer.identity.uuid,
        now=NOW,
        runner=fake,
    )
    assert not result.ok and "SHA-256" in result.problems[0]["message"]
    assert [verb(cmd) for cmd, _ in fake.calls] == ["unlock", "restore"]
    assert "--verify" in fake.calls[1][0]  # restic reads each restored file back too
    assert not any((tmp_path / "scratch").iterdir())  # the scratch copy is always removed
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    job = run_job(ctx, "restore-test")
    assert not job.ok and job.detail == "RestoreTestFailed"
    assert writer.state.last_run("restore-test").ok is False


def test_the_restore_test_clears_old_copies_then_checks_the_space(
    tmp_path, writer, make_incoming, monkeypatch
):
    # why: a killed restore test leaves a full plaintext copy behind; and a restore onto the
    # archive's disk must not take it below the warning (or the stop) threshold.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    old = tmp_path / "scratch" / "restore-20261001T000000Z"
    (old / "archive").mkdir(parents=True)
    (tmp_path / "scratch" / "notes.txt").write_text("not ours\n", encoding="utf-8")
    gib = 1024**3
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=100 * gib, used=80 * gib - 1, free=20 * gib + 1),
    )  # just above 20% free
    fake = FakeRestic()
    with pytest.raises(backup.ScratchTooSmall, match="below 20% free"):
        backup.run_restore_test(
            backup.from_config(cfg, environ),
            environ,
            expected_uuid=writer.identity.uuid,
            now=NOW,
            runner=fake,
        )
    assert fake.calls == [] and not old.exists()
    assert (tmp_path / "scratch" / "notes.txt").is_file()
    # the archive outgrows the disk one day: Status and the alert must say that, not just
    # "BackupRefused", which is also a missing sentinel, file or password
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    assert run_job(ctx, "restore-test").detail == "ScratchTooSmall"


def test_a_restored_copy_left_behind_fails_the_restore_test(
    tmp_path, writer, make_incoming, monkeypatch
):
    # why: rmtree(ignore_errors=True) alone would hide a full plaintext copy left on the disk.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)

    def restore(cmd):
        if "restore" in cmd:
            target = Path(cmd[cmd.index("--target") + 1])
            shutil.copytree(writer.root, target / writer.root.relative_to(writer.root.anchor))

    monkeypatch.setattr(backup.shutil, "rmtree", lambda path, ignore_errors=False: None)
    with pytest.raises(backup.RestoreCopyLeftBehind):
        backup.run_restore_test(
            backup.from_config(cfg, environ),
            environ,
            expected_uuid=writer.identity.uuid,
            now=NOW,
            runner=FakeRestic(on_call=restore),
        )


def test_validate_deep_checks_every_hash(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    assert validate(writer.root, deep=True) == []
    folder = writer.archive.path_for(rec.id)
    (folder / rec.sources[0].raw).chmod(0o644)
    (folder / rec.sources[0].raw).write_bytes(b"tampered")
    assert validate(writer.root) == []  # the quick check sees the file there
    (problem,) = validate(writer.root, deep=True)
    assert "SHA-256" in problem["message"]


def test_run_job_records_each_run(tmp_path, writer, make_incoming):
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    ctx = Context(
        cfg=cfg,
        environ=environ,
        state=writer.state,
        clock=lambda: NOW,
        runner=FakeRestic(stdout=SUMMARY),
    )
    assert run_job(ctx, "backup").ok and writer.state.last_run("backup", ok=True).detail.startswith(
        "snapshot"
    )
    off, _ = config(tmp_path, writer, prune=False)
    assert (
        run_job(
            Context(
                cfg=off, environ=environ, state=writer.state, clock=lambda: NOW, runner=FakeRestic()
            ),
            "prune",
        ).detail
        == "prune is off: retention runs on the NAS"
    )


def test_a_failure_is_recorded_as_its_type_and_exit_code_only(tmp_path, writer, make_incoming):
    # why: Review Focus 4. restic's stderr can name a file, and a file name can carry a title.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(
        returncode=3,
        stderr="Fatal: /archive/会議メモ / Q&A.json: permission denied\n",
        only=("backup",),
    )
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    result = run_job(ctx, "backup")
    assert result.detail == "BackupError; exit 3" == writer.state.last_run("backup").detail


def test_a_job_holds_the_ops_lock_and_the_loop_skips_a_busy_turn(tmp_path, writer, make_incoming):
    # why: restic from the CLI and from the loop at once fails on restic's own lock, and `restic
    # unlock` in one container would remove the other's live lock: both containers have the same
    # host name. The loop skips a busy turn, which is not a failure and sends no alert.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(stdout=SUMMARY)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        busy = run_job(ctx, "backup", lock_timeout=0)  # the loop's: it never waits
        assert run_job(ctx, "backup", lock_timeout=0.2) == busy  # the CLI waits, then gives up
    assert busy == JobResult("backup", False, "skipped: busy")
    assert fake.calls == [] and writer.state.last_run("backup") is None
    assert run_job(ctx, "backup").ok  # free again


def test_a_job_cannot_take_the_ops_lock_again_so_none_may_call_the_import(
    tmp_path, writer, monkeypatch
):
    # why: run_job holds "ops", and Locks is re-entrant only within one instance. The import
    # builds its own, so a job that called it would wait lock_timeout (600 s) on this process's
    # own hold, then raise LockTimeout, which is not in FAILURES. Imports run from the CLI alone.
    cfg, environ = config(tmp_path, writer)
    seen = []

    def job(ctx, tags):
        with pytest.raises(LockTimeout), Locks(ctx.state.locks_dir, timeout=0).hold("ops"):
            pass
        seen.append(True)
        return "done"

    monkeypatch.setitem(ops.JOBS, "backup", job)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW)
    assert run_job(ctx, "backup").ok and seen == [True]
    assert "audio_router" not in inspect.getsource(ops)  # no job reaches the import


def test_the_cli_waits_for_the_loop_and_for_restic_locks(tmp_path, writer, monkeypatch):
    cfg, environ = config(tmp_path, writer)
    for name in [n for n in os.environ if n.startswith(("RECORDINGS_", "RESTIC_"))]:
        monkeypatch.delenv(name)  # nothing from this machine: its own secrets and settings included
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    ctx = cli._ops_context(False)
    assert ops.backup_config(ctx).retry_lock == "5m" and ctx.runner is ops.run_child
    assert inspect.signature(run_job).parameters["lock_timeout"].default == 600
    assert Context(cfg=cfg, environ=environ, state=writer.state).retry_lock is None


def test_sigterm_reaches_the_running_child_before_the_job_stops(tmp_path):
    # why: `docker stop` sends SIGTERM. restic must get it too, so it stops cleanly and removes
    # its lock; killed instead, its lock fails every later check and backup until an unlock.
    ready, got = tmp_path / "ready", tmp_path / "got-sigterm"
    child = (
        "import pathlib, signal, sys, time\n"
        f"def stop(*_):\n    pathlib.Path({str(got)!r}).touch()\n    sys.exit(3)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    main_thread = threading.main_thread().ident

    def send_sigterm():
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        signal.pthread_kill(main_thread, signal.SIGTERM)

    threading.Thread(target=send_sigterm, daemon=True).start()
    with pytest.raises(SystemExit) as stopped, ops.sigterm_forwarded():
        ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
    assert stopped.value.code == 128 + signal.SIGTERM
    assert got.exists()  # the child had the signal, and stopped on its own


def test_sigterm_still_reaches_the_handler_that_was_there_before():
    # why: a handler installed before ours (by a caller, or a test runner) must still run.
    earlier = []

    def handler(signum, frame):
        earlier.append(signum)

    before = signal.signal(signal.SIGTERM, handler)
    try:
        with ops.sigterm_forwarded():
            signal.raise_signal(signal.SIGTERM)  # no child running: the earlier handler decides
        assert earlier == [signal.SIGTERM]
        assert signal.getsignal(signal.SIGTERM) is handler  # the earlier one is put back
    finally:
        signal.signal(signal.SIGTERM, before)


def test_with_a_real_restic_backup_check_forget_and_restore(tmp_path, writer, make_incoming):
    exe = restic_or_skip()
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer, restic=exe)
    bc = replace(backup.from_config(cfg, environ), retry_lock="1m")  # as the CLI runs it
    backup.run_init(bc, environ)
    first = backup.run_backup(bc, writer.state, environ, tags=("pre-import",))
    assert len(first.snapshot_id) == 64
    backup.run_check(bc, environ, now=NOW)  # unlock, then a quarter of the data read back
    backup.run_forget(bc, environ)
    restored = backup.run_restore_test(bc, environ, expected_uuid=writer.identity.uuid, now=NOW)
    assert restored.ok and restored.recordings == 1
    assert not any((tmp_path / "scratch").iterdir())


CLEANS_UP = (
    "import pathlib, signal, sys, time\n"
    "def stop(*_):\n"
    "    time.sleep(0.5)  # restic's cleanup: it reports on stderr, then removes its lock\n"
    "    sys.stderr.write('cleaning up\\n'); sys.stderr.flush()  # BrokenPipeError if closed\n"
    "    pathlib.Path(%(cleaned)r).touch()\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "pathlib.Path(%(ready)r).touch()\n"
    "time.sleep(30)\n"
)


def sigterm_when_ready(ready: Path, signum: int = signal.SIGTERM) -> None:
    main_thread = threading.main_thread().ident

    def send():
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        signal.pthread_kill(main_thread, signum)

    threading.Thread(target=send, daemon=True).start()


def test_a_raising_earlier_handler_runs_only_after_the_child_has_cleaned_up(tmp_path):
    # why: an earlier handler that raises (sys.exit) used to run at once, which closed the child's
    # pipes mid-cleanup: restic died of SIGPIPE and left its repository lock behind.
    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    child = CLEANS_UP % {"ready": str(ready), "cleaned": str(cleaned)}
    seen = []

    def earlier(signum, frame):
        seen.append(cleaned.exists())
        raise RuntimeError("the earlier handler")

    before = signal.signal(signal.SIGTERM, earlier)
    try:
        sigterm_when_ready(ready)
        with pytest.raises(RuntimeError, match="earlier handler"), ops.sigterm_forwarded():
            ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
        assert signal.getsignal(signal.SIGTERM) is earlier
    finally:
        signal.signal(signal.SIGTERM, before)
    assert cleaned.exists() and seen == [True]  # the child finished first, then the handler ran


def test_a_sigterm_while_the_child_is_starting_still_reaches_it(monkeypatch):
    # why: a SIGTERM between Popen() and the assignment of the child used to stop this process
    # without ever signalling the child, which was left running.
    real, children = subprocess.Popen, []

    def popen_then_sigterm(*args, **kwargs):
        proc = real(*args, **kwargs)
        children.append(proc)
        signal.raise_signal(signal.SIGTERM)  # arrives before run_child has the child
        return proc

    monkeypatch.setattr(ops.subprocess, "Popen", popen_then_sigterm)
    with pytest.raises(SystemExit) as stopped, ops.sigterm_forwarded():
        ops.run_child(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert stopped.value.code == 128 + signal.SIGTERM
    try:
        assert children[0].wait(timeout=10) != 0  # the child was told to stop, not left running
    except subprocess.TimeoutExpired:
        children[0].kill()
        pytest.fail("the child was never signalled")


def test_ctrl_c_stops_the_child_with_sigterm_and_waits_for_its_cleanup(tmp_path):
    # why: at a terminal, Ctrl-C reaches restic as well. KeyboardInterrupt inside communicate()
    # closed the pipes before waiting, so restic could die of SIGPIPE in the middle of its
    # cleanup and leave its repository lock behind. Now the child is sent SIGTERM (restic handles
    # INT then TERM cleanly, exiting 130) and run_child waits for it before Ctrl-C goes on.
    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    sigterm_when_ready(ready, signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        ops.run_child(
            [sys.executable, "-c", CLEANS_UP % {"ready": str(ready), "cleaned": str(cleaned)}],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert cleaned.exists()  # run_child told it to stop, waited, and only then let Ctrl-C through


def test_a_second_ctrl_c_does_not_abandon_a_child_that_is_cleaning_up(tmp_path):
    # why: a second Ctrl-C used to leave the block, close the pipes and abandon restic mid-cleanup.
    # The child's TERM handler touches `stopping` first, so the second Ctrl-C always lands while
    # _stop_child waits (Popen.communicate has its own 0.25 s wait after the first one).
    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    stopping = tmp_path / "stopping"
    announced = CLEANS_UP.replace(
        "def stop(*_):\n", "def stop(*_):\n    pathlib.Path(%(stopping)r).touch()\n"
    )
    child = announced % {"ready": str(ready), "cleaned": str(cleaned), "stopping": str(stopping)}
    sigterm_when_ready(ready, signal.SIGINT)  # the first, as soon as the child is up
    main_thread = threading.main_thread().ident

    def second():
        deadline = time.monotonic() + 10
        while not stopping.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)  # the child's 0.5 s cleanup has begun
        signal.pthread_kill(main_thread, signal.SIGINT)

    threading.Thread(target=second, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
    assert cleaned.exists()


def test_ctrl_c_keeps_the_callers_timeout_and_kills_a_child_that_ignores_sigterm(tmp_path):
    # why: the wait after Ctrl-C was unbounded and dropped the caller's timeout, so a hung child
    # held the CLI for ever.
    ready = tmp_path / "ready"
    child = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(60)\n"
    )
    sigterm_when_ready(ready, signal.SIGINT)
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=2)
    assert time.monotonic() - began < 8  # about the 2 s timeout, not the child's 60


def test_ctrl_c_with_no_timeout_waits_only_interrupt_grace_for_a_child_that_ignores_sigterm(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ops, "INTERRUPT_GRACE", 1.0)
    ready = tmp_path / "ready"
    child = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(60)\n"
    )
    sigterm_when_ready(ready, signal.SIGINT)
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        ops.run_child([sys.executable, "-c", child], capture_output=True, text=True)
    assert time.monotonic() - began < 8  # about the 1 s grace, not the child's 60


def test_a_sigterm_in_the_spawn_window_is_honoured_when_popen_raises(monkeypatch):
    # why: the flag was only looked at after a child had been waited for, so on this path the CLI
    # exited 1 instead of 143 and the loop went on to its next turn.
    def popen_fails_after_sigterm(*args, **kwargs):
        signal.raise_signal(signal.SIGTERM)  # recorded only: no child yet
        raise OSError("spawn failed")

    monkeypatch.setattr(ops.subprocess, "Popen", popen_fails_after_sigterm)
    with pytest.raises(SystemExit) as stopped, ops.sigterm_forwarded():
        ops.run_child(["restic"], capture_output=True, text=True, timeout=60)
    assert stopped.value.code == 128 + signal.SIGTERM


def test_a_forwarded_sigterm_is_honoured_when_the_child_then_times_out(tmp_path):
    # why: communicate() raised TimeoutExpired past the check, so the earlier handler never ran
    # and the stop was held back until the next run_child or the end of the block.
    ready = tmp_path / "ready"
    child = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    seen = []
    before = signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
    try:
        sigterm_when_ready(ready)
        with pytest.raises(subprocess.TimeoutExpired), ops.sigterm_forwarded():
            ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=2)
    finally:
        signal.signal(signal.SIGTERM, before)
    assert seen == [signal.SIGTERM]  # the earlier handler ran, once, as the timeout went by


def test_sigterm_forwarded_can_nest_and_copes_with_a_handler_python_cannot_see(monkeypatch):
    # why: nested, it used to take its own handler for the "earlier" one and recurse on SIGTERM,
    # and getsignal() returns None for a handler not installed from Python.
    outer = signal.getsignal(signal.SIGTERM)
    with ops.sigterm_forwarded():
        with ops.sigterm_forwarded():
            assert ops._previous is not ops._on_sigterm
        assert signal.getsignal(signal.SIGTERM) is ops._on_sigterm  # the outer block's, still
    assert signal.getsignal(signal.SIGTERM) is outer
    monkeypatch.setattr(ops.signal, "getsignal", lambda signum: None)
    with ops.sigterm_forwarded():
        pass  # restoring None would raise TypeError
    monkeypatch.undo()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_errors_from_the_state_copy_or_the_ops_lock_are_recorded_not_raised(
    tmp_path, writer, make_incoming, monkeypatch
):
    # why: a full state disk (sqlite3.OperationalError) or a lock file that can't be opened
    # (OSError) used to escape run_job as a traceback, unrecorded.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    ctx = Context(
        cfg=cfg,
        environ=environ,
        state=writer.state,
        clock=lambda: NOW,
        runner=FakeRestic(stdout=SUMMARY),
    )

    def full(dest):
        raise sqlite3.OperationalError("database or disk is full: /some/place/Q&A.db")

    monkeypatch.setattr(writer.state, "backup_to", full)
    assert run_job(ctx, "backup") == JobResult("backup", False, "OperationalError")
    assert writer.state.last_run("backup").detail == "OperationalError"
    monkeypatch.undo()

    class Unopenable:
        def __init__(self, *args, **kwargs):
            pass

        def hold(self, key):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ops, "Locks", Unopenable)
    assert run_job(ctx, "check") == JobResult("check", False, "PermissionError")
    assert writer.state.last_run("check").detail == "PermissionError"


def test_host_keys_are_always_checked_and_a_nas_without_a_known_hosts_file_is_refused(
    tmp_path, writer
):
    # why: without StrictHostKeyChecking=yes, a host's ssh_config (accept-new) or ~/.ssh/known_hosts
    # decides, and the mirror sends the plaintext archive over these options.
    options = ssh_options(None, None, None)
    assert options[options.index("StrictHostKeyChecking=yes") - 1] == "-o"
    assert not any(o.startswith("UserKnownHostsFile=") for o in options)
    pinned = ssh_options(None, tmp_path / "kh", None)
    assert f"UserKnownHostsFile={tmp_path / 'kh'}" in pinned
    assert "GlobalKnownHostsFile=/dev/null" in pinned  # the system's known hosts are not trusted
    cfg, environ = config(tmp_path, writer, repository="sftp:backup@nas:/backups/r")
    del environ["RECORDINGS_NAS_KNOWN_HOSTS_FILE"]
    with pytest.raises(ConfigError, match="RECORDINGS_NAS_KNOWN_HOSTS_FILE"):
        backup.from_config(cfg, environ)  # an sftp: repository
    cfg, environ = config(tmp_path, writer)
    del environ["RECORDINGS_NAS_KNOWN_HOSTS_FILE"]
    cfg.data["nas"] = {"ssh": "mirror@nas"}
    with pytest.raises(ConfigError, match="RECORDINGS_NAS_KNOWN_HOSTS_FILE"):
        backup.from_config(cfg, environ)  # the mirror's and the free-space check's ssh
    cfg.data["nas"] = {}
    backup.from_config(cfg, environ)  # a rest-server on the tailnet needs no ssh at all


def test_ssh_shares_no_connection_or_agent_and_takes_no_option_for_a_host(tmp_path, writer):
    # why: outside Docker the user's ~/.ssh/config applies too. A shared connection (ControlPath)
    # skips the pinned host-key check, and ForwardAgent hands the NAS the user's agent. A
    # destination that starts with "-" is read as an option, ProxyCommand included (Task 16's
    # council).
    options = ssh_options(None, tmp_path / "kh", None)
    for option in ("ControlPath=none", "ForwardAgent=no", "ClearAllForwardings=yes"):
        assert options[options.index(option) - 1] == "-o", option
    cfg, environ = config(tmp_path, writer)
    cfg.data["nas"] = {"ssh": "-oProxyCommand=touch /tmp/x"}
    with pytest.raises(ConfigError, match=r"\[nas\] ssh"):
        backup.from_config(cfg, environ)


def test_a_key_path_with_a_space_survives_restics_sftp_args(tmp_path, writer):
    key = tmp_path / "my keys" / "nas key"
    key.parent.mkdir()
    key.write_text("not a real key\n", encoding="utf-8")
    cfg, environ = config(tmp_path, writer, repository="sftp:backup@nas:/backups/r")
    environ |= {"RECORDINGS_NAS_SSH_KEY_FILE": str(key)}
    base = backup.restic_base(backup.from_config(cfg, environ))
    (option,) = [a for a in base if a.startswith("sftp.args=")]
    assert (
        shlex.split(option.removeprefix("sftp.args="))
        == backup.from_config(cfg, environ).nas.options()
    )  # restic splits the value like a shell does


def test_a_direct_ssh_key_value_is_refused_and_a_stray_variable_never_reaches_restic(
    tmp_path, writer
):
    cfg, environ = config(tmp_path, writer)
    with pytest.raises(ConfigError, match="RECORDINGS_NAS_SSH_KEY_FILE"):
        secret_path("nas_ssh_key", environ | {"RECORDINGS_NAS_SSH_KEY": "-----BEGIN fixture-----"})
    env = backup.restic_env(
        backup.from_config(cfg, environ),
        environ
        | {
            "RESTIC_PASSWORD_COMMAND": "echo hunter2",
            "RESTIC_PROGRESS_FPS": "1",
            "RESTIC_REPOSITORY_FILE": "/elsewhere",
            "RECORDINGS_PLAUD_TOKEN": "token",
            "AWS_SECRET_ACCESS_KEY": "aws",
        },
    )
    assert set(env) <= {
        "PATH",
        "HOME",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_CACHE_DIR",
    }


def test_a_missing_sentinel_in_the_restored_copy_is_reported_once(tmp_path, writer, make_incoming):
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)

    def restore(cmd):
        if "restore" in cmd:
            _, copy = restore_like_restic(writer, backup.from_config(cfg, environ), cmd)
            (copy / SENTINEL).unlink()

    result = backup.run_restore_test(
        backup.from_config(cfg, environ),
        environ,
        expected_uuid=writer.identity.uuid,
        now=NOW,
        runner=FakeRestic(on_call=restore),
    )
    assert len(result.problems) == 1


def test_a_snapshot_missing_a_recording_or_a_backed_up_file_fails_the_restore_test(
    tmp_path, writer, make_incoming
):
    # why: --verify and the hashes prove what was restored, not that everything was: a snapshot
    # missing a whole recording folder passes both. The snapshot's own state.db copy, taken
    # before restic read the archive, names every recording.json it must hold.
    first = writer.add(make_incoming()).recording
    early = writer.state.backup_to(tmp_path / "early" / "state.db")  # before `later` existed
    later = writer.add(make_incoming()).recording
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)

    def run(change, state_copy=None):
        def restore(cmd):
            if "restore" in cmd:
                change(*restore_like_restic(writer, bc, cmd, state_copy=state_copy))

        return backup.run_restore_test(
            bc,
            environ,
            expected_uuid=writer.identity.uuid,
            now=NOW,
            runner=FakeRestic(on_call=restore),
        )

    def drop_first_and_the_config(target, copy):
        shutil.rmtree(copy / writer.archive.path_for(first.id).relative_to(writer.root))
        (target / cfg.path.relative_to(cfg.path.anchor)).unlink()

    result = run(drop_first_and_the_config)
    assert result.recordings == 1
    assert sorted((p["path"], p["message"]) for p in result.problems) == sorted(
        [
            (str(cfg.path), "is not in the snapshot"),
            (str(writer.archive.path_for(first.id) / "recording.json"), "is not in the snapshot"),
        ]
    )

    def drop_the_state_copy(target, copy):
        (target / backup.state_copy_path(bc).relative_to(bc.state.anchor)).unlink()

    result = run(drop_the_state_copy)
    assert [(p["path"], p["message"]) for p in result.problems] == [
        (str(backup.state_copy_path(bc)), "is not in the snapshot")
    ]

    # `later` was added after this snapshot's state copy: there or not, it is no problem
    result = run(lambda target, copy: None, state_copy=early)
    assert result.ok and result.recordings == 2

    def drop_later(target, copy):
        shutil.rmtree(copy / writer.archive.path_for(later.id).relative_to(writer.root))

    result = run(drop_later, state_copy=early)
    assert result.ok and result.recordings == 1
    assert not any((tmp_path / "scratch").iterdir())


def test_backup_init_prints_fixed_text_and_a_missing_restic_is_a_clean_error(
    tmp_path, writer, monkeypatch, capsys
):
    # why: restic's own words can carry a path or a server's reply; and a restic that is not
    # installed used to end in a traceback.
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(returncode=1, stderr="Fatal: /archive/会議メモ.json: permission denied\n")
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=fake)
    monkeypatch.setattr(cli, "_ops_context", lambda as_json: ctx)
    assert cli.main(["backup", "init", "--json"]) == 1
    out = capsys.readouterr().out
    assert "会議メモ" not in out and "BackupError; exit 1" in json.loads(out)["error"]
    missing, _ = config(tmp_path, writer, restic=str(tmp_path / "no-restic-here"))
    ctx = Context(cfg=missing, environ=environ, state=writer.state, runner=ops.run_child)
    assert cli.main(["backup", "init", "--json"]) == 1
    assert "FileNotFoundError" in json.loads(capsys.readouterr().out)["error"]


def test_fetch_restic_checks_the_binary_it_has_not_just_its_version(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "fetch_restic", REPO / "scripts" / "fetch_restic.py"
    )
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    assert set(fetch.BINARY_SHA256) == {name for name, _ in fetch.PINNED.values()}
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in fetch.BINARY_SHA256.values())
    monkeypatch.setattr(fetch, "TARGET", tmp_path / "restic")
    fake = tmp_path / "restic"
    fake.write_text("#!/bin/sh\necho 'restic 0.19.1 compiled with go'\n", encoding="utf-8")
    fake.chmod(0o755)
    name = next(iter(fetch.BINARY_SHA256))
    assert not fetch.installed(fetch.BINARY_SHA256[name])  # it prints the version, but isn't restic
    assert fetch.installed(hashlib.sha256(fake.read_bytes()).hexdigest())  # the hash is what counts
