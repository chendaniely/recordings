import os
import shutil
import subprocess
from pathlib import Path

import pytest

from recordings import mirror
from recordings.config import ConfigError, load_config
from recordings.mirror import MirrorError, MirrorRefused, is_remote, rsync_command, run_mirror
from recordings.ops import Context, run_job
from recordings.sentinel import MIRROR, SENTINEL

OTHER_UUID = "00000000-0000-4000-8000-000000000000"


def setup(tmp_path, writer, target, extra="", known_hosts=True):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
                        f'[state]\npath = "{writer.state.path}"\n'
                        f'[mirror]\ntarget = "{target}"\n{extra}', encoding="utf-8")
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
    archive.json fetch from that folder, and records the mirror runs themselves."""

    def __init__(self, target: Path, returncode=0, stderr=""):
        self.target, self.returncode, self.stderr, self.mirrored = target, returncode, stderr, []

    def __call__(self, cmd, **kwargs):
        if "--list-only" in cmd:
            if not self.target.is_dir():
                return subprocess.CompletedProcess(cmd, 23, "", "change_dir failed\n")
            names = [".", *sorted(p.name for p in self.target.iterdir())]
            listing = "".join(f"drwxr-xr-x          4,096 2026/10/09 08:00:00 {n}\n" for n in names)
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        if cmd[-2].endswith("/" + SENTINEL):
            shutil.copy(self.target / SENTINEL, cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[-2].endswith("/" + MIRROR):  # the mark, which goes on before anything else
            shutil.copy(cmd[-2], self.target / MIRROR)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.mirrored.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def test_the_command_deletes_late_and_little_puts_updates_in_place_together_and_skips_work_in_progress(
        tmp_path, writer):
    mc = mirror_config(tmp_path, writer, "recordings-mirror@nas:/volume1/recordings-mirror/")
    cmd = rsync_command(mc)
    for flag in ("-a", "--delete", "--delete-delay", "--delay-updates", "--max-delete=50",
                 "--exclude=/.tmp/", "--exclude=.*.tmp", "--exclude=.~tmp~/",
                 "--exclude=/#recycle/", "--exclude=@eaDir/", f"--exclude=/{MIRROR}"):
        assert flag in cmd, flag
    assert "--no-perms" not in cmd
    assert cmd[-2:] == [f"{writer.root}/", "recordings-mirror@nas:/volume1/recordings-mirror/"]
    ssh = cmd[cmd.index("-e") + 1]
    assert ssh.startswith("ssh -o BatchMode=yes") and "StrictHostKeyChecking=yes" in ssh
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in ssh
    assert "GlobalKnownHostsFile=/dev/null" in ssh  # only the pinned file is trusted
    local = rsync_command(mirror_config(tmp_path, writer, str(tmp_path / "mirror")))
    assert "-e" not in local


def test_the_deletion_limit_and_no_perms_come_from_config(tmp_path, writer):
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"), "max_delete = 500\nno_perms = true\n")
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


@pytest.mark.parametrize("target, remote", [("nas:/x", True), ("backup@nas:/x/", True),
                                            ("/mnt/mirror", False), ("./x:y", False)])
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
    # `init` (a new UUID), and Synology Drive would then delete it on the Mac too.
    target = tmp_path / "mirror"
    mc = mirror_config(tmp_path, writer, str(target))
    fake = FakeRsync(target)
    with pytest.raises(MirrorRefused, match="create its folder"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    (target / "#recycle").mkdir(parents=True)  # DSM's own folders don't count
    (target / "@eaDir").mkdir()
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    (target / "photos").mkdir()
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    sentinel = (writer.root / SENTINEL).read_text(encoding="utf-8")
    (target / SENTINEL).write_text(sentinel.replace(writer.identity.uuid, OTHER_UUID),
                                   encoding="utf-8")
    with pytest.raises(MirrorRefused, match="another archive's mirror"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    assert len(fake.mirrored) == 1  # rsync --delete ran only into the empty target
    (target / SENTINEL).write_text(sentinel, encoding="utf-8")
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
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert len(fake.mirrored) == 2


def test_a_failed_rsync_is_an_error(tmp_path, writer):
    target = tmp_path / "mirror"
    target.mkdir()
    mc = mirror_config(tmp_path, writer, str(target))
    with pytest.raises(MirrorError, match="exit 23") as caught:
        run_mirror(mc, expected_uuid=writer.identity.uuid,
                   runner=FakeRsync(target, returncode=23, stderr="rsync: some error\n"))
    assert caught.value.returncode == 23


def test_more_deletions_than_the_limit_stop_the_mirror(tmp_path, writer):
    # why: §15.1. --max-delete bounds an accident; rsync exits 25 when it is reached.
    target = tmp_path / "mirror"
    target.mkdir()
    cfg, environ = setup(tmp_path, writer, str(target))
    fake = FakeRsync(target, returncode=25)
    with pytest.raises(MirrorError, match="max_delete"):
        run_mirror(mirror.from_config(cfg, environ), expected_uuid=writer.identity.uuid, runner=fake)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=fake)
    assert run_job(ctx, "mirror").detail == "MirrorError; exit 25"


def _rsync_with_delay_updates() -> str | None:
    exe = shutil.which("rsync")
    if exe is None:
        return None
    help_text = subprocess.run([exe, "--help"], capture_output=True, text=True).stdout
    return exe if "--delay-updates" in help_text and "--delete-delay" in help_text else None


def test_a_real_local_mirror(tmp_path, writer, make_incoming):
    if _rsync_with_delay_updates() is None:
        pytest.skip("this rsync has no --delay-updates (macOS ships openrsync); CI's has")
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
