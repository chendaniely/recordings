import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from recordings import cli, mirror
from recordings.config import ConfigError, load_config
from recordings.mirror import MirrorError, MirrorRefused, is_remote, rsync_command, run_mirror
from recordings.ops import Context, run_job
from recordings.sentinel import MIRROR, SENTINEL

OTHER_UUID = "00000000-0000-4000-8000-000000000000"


def setup(tmp_path, writer, target, extra="", known_hosts=True):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{writer.state.path}"\n'
        f'[mirror]\ntarget = "{target}"\n{extra}',
        encoding="utf-8",
    )
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "PATH": os.environ["PATH"]}
    if known_hosts:  # fixtures only: the real RECORDINGS_* and RESTIC_* never reach this module
        (tmp_path / "known_hosts").write_text("nas ssh-ed25519 AAAAexample\n", encoding="utf-8")
        environ["RECORDINGS_NAS_KNOWN_HOSTS_FILE"] = str(tmp_path / "known_hosts")
    return load_config(environ), environ


def mirror_config(tmp_path, writer, target, extra=""):
    cfg, environ = setup(tmp_path, writer, target, extra)
    return mirror.from_config(cfg, environ)


class FakeRsync:
    """rsync against a local folder standing in for the target: it answers --list-only and the
    MIRROR.md fetch from that folder, answers a dry run with no changes, and records the mirror
    runs themselves (and whether the mark was on the target by then)."""

    def __init__(self, target: Path, returncode=0, stderr=""):
        self.target, self.returncode, self.stderr = target, returncode, stderr
        self.mirrored, self.marked_before_run, self.calls = [], [], []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if "--list-only" in cmd:
            if not self.target.is_dir():
                return subprocess.CompletedProcess(cmd, 23, "", "change_dir failed\n")
            names = [".", *sorted(p.name for p in self.target.iterdir())]
            listing = "".join(f"drwxr-xr-x          4,096 2026/10/09 08:00:00 {n}\n" for n in names)
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        if cmd[-2] == f"{self.target}/{MIRROR}":  # the target's mark, fetched
            shutil.copy(self.target / MIRROR, cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[-2].endswith("/" + MIRROR):  # the mark, which goes on before anything else
            shutil.copy(cmd[-2], self.target / MIRROR)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "--dry-run" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.marked_before_run.append((self.target / MIRROR).is_file())
        self.mirrored.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def test_the_command_deletes_late_and_little_puts_updates_in_place_together_and_skips_work_in_progress(
    tmp_path, writer
):
    mc = mirror_config(tmp_path, writer, "recordings-mirror@nas:/volume1/recordings-mirror/")
    cmd = rsync_command(mc)
    for flag in (
        "-a",
        "--delete",
        "--delete-delay",
        "--delay-updates",
        "--max-delete=50",
        "--exclude=/.tmp/",
        "--exclude=.*.tmp",
        "--filter=-p .~tmp~/",
        "--exclude=/#recycle/",
        "--filter=-p @eaDir/",
        f"--exclude=/{MIRROR}",
        "--timeout=600",
    ):  # a stalled NAS ends the run (exit 30), not the loop's cap
        assert flag in cmd, flag
    assert "--no-perms" not in cmd
    assert cmd[-2:] == [f"{writer.root}/", "recordings-mirror@nas:/volume1/recordings-mirror/"]
    ssh = cmd[cmd.index("-e") + 1]
    assert ssh.startswith("ssh -o BatchMode=yes") and "StrictHostKeyChecking=yes" in ssh
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in ssh
    assert "GlobalKnownHostsFile=/dev/null" in ssh  # only the pinned file is trusted
    dry = rsync_command(mc, dry_run=True)  # counts the deletions: --max-delete would cap the count
    assert "--dry-run" in dry and "--itemize-changes" in dry and "--max-delete=50" not in dry
    assert dry[-2:] == cmd[-2:] and "--delete-delay" in dry and "--timeout=600" in dry
    local = rsync_command(mirror_config(tmp_path, writer, str(tmp_path / "mirror")))
    assert "-e" not in local


def test_the_deletion_limit_and_no_perms_come_from_config(tmp_path, writer):
    mc = mirror_config(
        tmp_path, writer, str(tmp_path / "mirror"), "max_delete = 500\nno_perms = true\n"
    )
    cmd = rsync_command(mc)
    assert "--max-delete=500" in cmd and "--no-perms" in cmd  # a share with Windows ACLs
    for bad in ("max_delete = -1\n", 'max_delete = "50"\n', 'no_perms = "yes"\n'):
        with pytest.raises(ConfigError, match=r"\[mirror\]"):
            mirror_config(tmp_path, writer, str(tmp_path / "mirror"), bad)


def test_a_remote_target_needs_the_known_hosts_file_even_without_nas_ssh(tmp_path, writer):
    # why: the mirror sends the plaintext archive over these options, [nas] ssh set or not.
    cfg, environ = setup(tmp_path, writer, "recordings-mirror@nas:/m/", known_hosts=False)
    assert "ssh" not in cfg.data.get("nas", {})
    with pytest.raises(ConfigError, match="RECORDINGS_NAS_KNOWN_HOSTS_FILE"):
        mirror.from_config(cfg, environ)
    cfg, environ = setup(tmp_path, writer, str(tmp_path / "mirror"), known_hosts=False)
    assert mirror.from_config(cfg, environ).target  # a local target needs none


@pytest.mark.parametrize(
    "target, remote",
    [("nas:/x", True), ("backup@nas:/x/", True), ("/mnt/mirror", False), ("./x:y", False)],
)
def test_is_remote(target, remote):
    assert is_remote(target) is remote


def test_the_mirror_refuses_without_the_sentinel_or_with_another_archive(tmp_path, writer):
    # why: §6.7, §15.1. rsync --delete from an unmounted, empty folder would empty the mirror.
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"))
    calls = []
    with pytest.raises(MirrorRefused, match="UUID"):
        run_mirror(mc, expected_uuid=OTHER_UUID, runner=lambda *a, **k: calls.append(a))
    (writer.root / SENTINEL).unlink()
    with pytest.raises(MirrorRefused, match="mounted"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=lambda *a, **k: calls.append(a))
    assert calls == []


def test_the_mirror_runs_only_into_an_empty_target_or_its_own_mirror(tmp_path, writer):
    # why: §15.1. --delete would empty a wrong folder, or a mirror made before a rollback to a fresh
    # `init` (a new UUID), and Synology Drive would then delete it on the Mac too. The mark says
    # whose mirror it is: archive.json arrives last, and a copy of the archive carries it too.
    target = tmp_path / "mirror"
    mc = mirror_config(tmp_path, writer, str(target))
    fake = FakeRsync(target)
    with pytest.raises(MirrorRefused, match="can't be listed"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    (target / "#recycle").mkdir(parents=True)  # DSM's own folders don't count
    (target / "@eaDir").mkdir()
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert fake.marked_before_run == [True]  # the mark went on before the main run
    assert any("-c" in call for call in fake.calls if call[-2].endswith("/" + MIRROR))
    (target / MIRROR).unlink()
    (target / "photos").mkdir()
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    sentinel = (writer.root / SENTINEL).read_text(encoding="utf-8")
    (target / SENTINEL).write_text(sentinel, encoding="utf-8")  # an archive.json is not a mark
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    (target / MIRROR).write_text(mirror.mark_text(OTHER_UUID), encoding="utf-8")
    with pytest.raises(MirrorRefused, match="another archive's mirror"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    for bad in ("hello\n", f"archive-uuid: {OTHER_UUID}\narchive-uuid: {OTHER_UUID}\n"):
        (target / MIRROR).write_text(bad, encoding="utf-8")
        with pytest.raises(MirrorRefused, match="not valid"):
            run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    assert len(fake.mirrored) == 1  # rsync --delete ran only into the empty target
    (target / MIRROR).write_text(mirror.mark_text(writer.identity.uuid), encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert len(fake.mirrored) == 2


def test_the_mirror_marks_its_copy_first_and_the_mark_alone_is_still_empty(tmp_path, writer):
    # why: §3. The copy carries archive.json, so without a mark `recordings init --adopt` on the
    # Mac would make a second writer that every check accepts. A first run cut short leaves only
    # the mark, which must not lock the mirror out.
    target = tmp_path / "mirror"
    target.mkdir()
    fake = FakeRsync(target)
    mc = mirror_config(tmp_path, writer, str(target))
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert (target / MIRROR).is_file() and not (writer.root / MIRROR).exists()
    assert writer.identity.uuid in (target / MIRROR).read_text(encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert len(fake.mirrored) == 2


def test_a_listing_it_cannot_read_is_not_an_empty_target(tmp_path, writer):
    # why: a line the parser doesn't know could be anything; --delete must not take it for nothing.
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"))
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "an unexpected listing format\n", "")

    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=runner)
    assert len(calls) == 1  # the listing; no mark, no dry run, no rsync --delete


def test_a_dry_run_it_cannot_read_stops_the_mirror(tmp_path, writer):
    target = tmp_path / "mirror"
    target.mkdir()
    fake = FakeRsync(target)
    mc = mirror_config(tmp_path, writer, str(target))
    for dry in (
        subprocess.CompletedProcess([], 0, "what is this\n", ""),
        subprocess.CompletedProcess([], 23, "", "rsync: some error\n"),
    ):

        def runner(cmd, dry=dry, **kwargs):
            return dry if "--dry-run" in cmd else fake(cmd, **kwargs)

        with pytest.raises(MirrorError, match="dry run") as caught:
            run_mirror(mc, expected_uuid=writer.identity.uuid, runner=runner)
        assert caught.value.returncode == dry.returncode or dry.returncode == 0
    assert fake.mirrored == []  # rsync --delete never ran


def test_a_deletion_whose_name_is_all_whitespace_is_not_passed_over(tmp_path, writer):
    # why: `*deleting` followed by only spaces is a deletion, however odd its name. It must not
    # read as an itemized file change and so go uncounted: the mirror stops, deleting nothing.
    target = tmp_path / "mirror"
    target.mkdir()
    fake = FakeRsync(target)
    mc = mirror_config(tmp_path, writer, str(target))

    def runner(cmd, **kwargs):
        if "--dry-run" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "*deleting   \n", "")
        return fake(cmd, **kwargs)

    with pytest.raises(MirrorError, match="could not be read"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=runner)
    assert fake.mirrored == []


def test_a_failed_rsync_is_an_error(tmp_path, writer):
    target = tmp_path / "mirror"
    target.mkdir()
    mc = mirror_config(tmp_path, writer, str(target))
    with pytest.raises(MirrorError, match="exit 23") as caught:
        run_mirror(
            mc,
            expected_uuid=writer.identity.uuid,
            runner=FakeRsync(target, returncode=23, stderr="rsync: some error\n"),
        )
    assert caught.value.returncode == 23


def test_more_deletions_than_the_limit_stop_the_mirror(tmp_path, writer):
    # why: §15.1. The dry run refuses before anything is deleted; --max-delete is the backstop if
    # the archive changes after it, and rsync exits 25 when it is reached.
    target = tmp_path / "mirror"
    target.mkdir()
    cfg, environ = setup(tmp_path, writer, str(target))
    fake = FakeRsync(target, returncode=25)
    with pytest.raises(MirrorError, match="max_delete"):
        run_mirror(
            mirror.from_config(cfg, environ), expected_uuid=writer.identity.uuid, runner=fake
        )
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=fake)
    assert run_job(ctx, "mirror").detail == "MirrorError; exit 25"


def test_the_loop_caps_a_run_and_a_run_from_the_cli_may_finish_a_first_mirror(
    tmp_path, writer, monkeypatch
):
    # why: a first mirror can outlast the loop's cap. Stopped and started again every 6 hours, it
    # holds the backups off each time, and rsync 3.x on the NAS reads and rewrites every file
    # staged in .~tmp~ before it moves on, so a big one over a slow link may never finish. From a
    # terminal it runs to the end; rsync's own --timeout still ends a stall, there and in the loop.
    target = tmp_path / "mirror"
    target.mkdir()
    cfg, environ = setup(tmp_path, writer, str(target))
    fake, timeouts = FakeRsync(target), []

    def runner(cmd, **kwargs):
        if "--list-only" not in cmd and not cmd[-2].endswith("/" + MIRROR):
            timeouts.append(kwargs["timeout"])  # the dry run, then the run
        return fake(cmd, **kwargs)

    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=runner)
    assert run_job(ctx, "mirror").ok and timeouts == [mirror.TIMEOUT, mirror.TIMEOUT]
    monkeypatch.setattr(cli, "_ops_context", lambda as_json: ctx)
    timeouts.clear()
    assert cli.main(["mirror", "--json"]) == 0 and timeouts == [None, None]
    assert all("--timeout=600" in run for run in fake.mirrored)


def test_a_refusal_names_recordings_only_under_debug_and_the_cli_allows_more_for_one_run(
    tmp_path, writer, monkeypatch, caplog
):
    # why: "if they are meant, raise it for one run" must not need a config edit that outlives
    # the run, and Dan must first see what would go: recording IDs and counts, at debug level only
    # (the loop logs at INFO), never a name, since a file left in the mirror could be called
    # anything.
    target = tmp_path / "mirror"
    target.mkdir()
    cfg, environ = setup(tmp_path, writer, str(target), "max_delete = 2\n")
    rid = "20261008T143512+0200_0123abcd"
    gone = "".join(
        f"*deleting   recordings/2026/10/{rid}/{name}\n"
        for name in ("audio.m4a", "recording.json", "")
    )  # "": the folder itself
    gone += "*deleting   Grace and Ada.m4a\n"
    fake = FakeRsync(target)

    def runner(cmd, **kwargs):
        if "--dry-run" in cmd:
            return subprocess.CompletedProcess(cmd, 0, gone, "")
        return fake(cmd, **kwargs)

    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=runner)
    with caplog.at_level(logging.INFO, logger="recordings"):
        assert run_job(ctx, "mirror").detail == "MirrorRefused"
    assert rid not in caplog.text
    with caplog.at_level(logging.DEBUG, logger="recordings"):
        assert run_job(ctx, "mirror").detail == "MirrorRefused"
    assert f"would delete, recording {rid}: 3" in caplog.text
    assert "would delete, outside any recording: 1" in caplog.text and "Ada" not in caplog.text
    assert fake.mirrored == []
    monkeypatch.setattr(cli, "_ops_context", lambda as_json: ctx)
    assert cli.main(["mirror", "--json", "--max-delete", "4"]) == 0
    assert "--max-delete=4" in fake.mirrored[-1] and len(fake.mirrored) == 1
    assert mirror.from_config(cfg, environ).max_delete == 2  # the config keeps its limit
    assert cli.main(["mirror", "--json", "--max-delete", "-1"]) == 2


def _rsync_with_delay_updates() -> str | None:
    exe = shutil.which("rsync")
    if exe is None:
        return None
    help_text = subprocess.run([exe, "--help"], capture_output=True, text=True).stdout
    return exe if "--delay-updates" in help_text and "--delete-delay" in help_text else None


def test_a_real_local_mirror(tmp_path, writer, make_incoming):
    if _rsync_with_delay_updates() is None:
        pytest.skip("this rsync has no --delay-updates (macOS before 26's openrsync; 26's has it)")
    rec = writer.add(make_incoming()).recording
    target = tmp_path / "mirror"
    target.mkdir()
    (writer.root / ".tmp" / "x").mkdir(parents=True)
    mc = mirror_config(tmp_path, writer, f"{target}/")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"  # into an empty target
    assert (target / SENTINEL).is_file()
    assert (target / "recordings" / "2026" / "10" / rec.id / "recording.json").is_file()
    assert not (target / ".tmp").exists()
    (target / "stale-file").write_text("from before", encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"  # into its own mirror
    assert not (target / "stale-file").exists()
    assert not list(target.rglob(".~tmp~"))
    assert (target / MIRROR).is_file() and not (writer.root / MIRROR).exists()  # --delete kept it


def _real_rsync_mirror(tmp_path, writer, extra=""):
    if _rsync_with_delay_updates() is None:
        pytest.skip("this rsync has no --delay-updates (macOS before 26's openrsync; 26's has it)")
    target = tmp_path / "mirror"
    target.mkdir()
    return target, mirror_config(tmp_path, writer, f"{target}/", extra)


def test_a_first_mirror_cut_short_is_resumed(tmp_path, writer):
    # why: --delay-updates holds archive.json in .~tmp~/ until the end, so an interrupted first run
    # leaves a mark, folders and no archive.json. The next run must still fill the mirror.
    target, mc = _real_rsync_mirror(tmp_path, writer)
    (target / MIRROR).write_text(mirror.mark_text(writer.identity.uuid), encoding="utf-8")
    (target / ".~tmp~").mkdir()
    (target / ".~tmp~" / SENTINEL).write_text("{}", encoding="utf-8")
    (target / "recordings").mkdir()
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"
    assert (target / SENTINEL).read_text(encoding="utf-8") == (writer.root / SENTINEL).read_text(
        encoding="utf-8"
    )


def test_real_rsync_refuses_another_archives_mark_a_foreign_folder_and_the_archive_itself(
    tmp_path, writer
):
    target, mc = _real_rsync_mirror(tmp_path, writer)
    (target / MIRROR).write_text(mirror.mark_text(OTHER_UUID), encoding="utf-8")
    with pytest.raises(MirrorRefused, match="another archive's mirror"):
        run_mirror(mc, expected_uuid=writer.identity.uuid)
    (target / MIRROR).unlink()
    (target / "photos").mkdir()  # the listing is rsync's own, not the fake's
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid)
    assert (target / "photos").is_dir()
    own = mirror_config(tmp_path, writer, f"{writer.root}/")  # archive.json, but no mark
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(own, expected_uuid=writer.identity.uuid)
    assert not (writer.root / MIRROR).exists()


def test_more_deletions_than_the_limit_are_refused_before_any_is_made(tmp_path, writer):
    # why: §15.1. --max-delete alone caps each run: rsync deletes up to N and carries on, so an
    # hourly mirror of a damaged archive would drain the NAS (and the Mac) N entries an hour.
    target, mc = _real_rsync_mirror(tmp_path, writer, "max_delete = 5\n")
    (target / MIRROR).write_text(mirror.mark_text(writer.identity.uuid), encoding="utf-8")
    for n in range(20):
        (target / f"stale-{n}").write_text("from before", encoding="utf-8")
    with pytest.raises(MirrorRefused, match="would delete 20 files and folders") as caught:
        run_mirror(mc, expected_uuid=writer.identity.uuid)
    assert "stale-" not in str(caught.value)  # counts only, never a path
    assert len(list(target.glob("stale-*"))) == 20
    assert not (target / SENTINEL).exists()  # and nothing was copied either


def test_a_dropped_recording_goes_even_when_an_eadir_sits_in_its_folder(tmp_path, writer):
    target, mc = _real_rsync_mirror(tmp_path, writer)
    (target / MIRROR).write_text(mirror.mark_text(writer.identity.uuid), encoding="utf-8")
    gone = target / "recordings" / "2026" / "10" / "dropped"
    (gone / "@eaDir").mkdir(parents=True)
    (gone / "@eaDir" / "thumb").write_text("x", encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"
    assert not gone.exists()
