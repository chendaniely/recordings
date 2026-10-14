"""The read-only mirror for the Mac (spec §15.1): an hourly one-way rsync of the archive to a
share on the NAS, through the mirror's own NAS account ([nas] ssh, with its key).

- `--delay-updates` puts every changed file in place at the end, so a reader never sees half a
  copy.
- `--delete --delete-delay` removes what the archive no longer holds, after the transfer, and
  `--max-delete` bounds it: more deletions than [mirror] max_delete stop the run (rsync exit 25).
- `.tmp/` and `.*.tmp` (work in progress) are never copied. `.~tmp~/`, where `--delay-updates`
  stages files and which an interrupted run leaves behind, is excluded too; on the Mac, Synology
  Drive's sync filter must exclude it as well. DSM's own `#recycle` and `@eaDir` folders are left
  alone.
- It refuses to run when the archive's sentinel is missing or its UUID has changed (§6.7), so an
  unmounted archive can never empty the mirror through --delete.
- A remote target needs RECORDINGS_NAS_KNOWN_HOSTS_FILE even when [nas] ssh is unset: only that
  pinned file is trusted for the host (ssh.ssh_options).
- It refuses a target that is neither empty nor this archive's mirror (an `archive.json` with the
  same UUID). So a wrong path, or a mirror made before a rollback to a fresh `init` (a new UUID),
  is never emptied, and Synology Drive never deletes it on the Mac.
- It puts `MIRROR.md` on the target before anything else, and `--delete` never removes it, so
  the copy says it is one: `recordings init --adopt` and every Writer refuse a folder holding it
  (§3), and it tells a person or an agent reading the copy to change nothing there.
- `[mirror] no_perms = true` adds `--no-perms`, for a share with Windows ACLs where rsync's chmod
  fails (the runbook's spike).

The mirror holds private recordings too: on the Mac, keeping external agents out of them is
policy (§7.4).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError
from recordings.sentinel import MIRROR, SENTINEL, SentinelError, read_sentinel
from recordings.ssh import Nas, nas_from_config, require_known_hosts

MAX_DELETE = 50
DSM_FOLDERS = ("#recycle", "@eaDir")  # the NAS's recycle bin and its indexer's folders
# One line of `rsync --list-only`: permissions, size, date, time, then the name.
_LISTED = re.compile(r"^[-dlpscb][-rwxsStT]{9}\S*\s+[\d,.]+[KMGTP]?\s+\S+\s+\S+\s+(?P<name>.+)$")


class MirrorError(RuntimeError):
    """The mirror failed or refused; a failed rsync raises this class itself, with rsync's exit
    status as `returncode` (25: max_delete was reached). Only the type's name and `returncode`
    are recorded and alerted (ops.failure_detail)."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class MirrorRefused(MirrorError):
    """Refused before rsync copied anything: the archive, or the target, isn't the right one."""


@dataclass(frozen=True)
class MirrorConfig:
    target: str
    archive: Path
    rsync: str
    nas: Nas
    max_delete: int = MAX_DELETE
    no_perms: bool = False


def from_config(cfg: Config, environ: Mapping[str, str]) -> MirrorConfig:
    section = cfg.data.get("mirror", {})
    target = section.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ConfigError("[mirror] target is not set (see config.example.toml)")
    if cfg.archive_path is None:
        raise ConfigError("set [archive] path before mirroring")
    max_delete = section.get("max_delete", MAX_DELETE)
    if not isinstance(max_delete, int) or isinstance(max_delete, bool) or max_delete < 0:
        raise ConfigError("[mirror] max_delete must be a whole number, 0 or more")
    no_perms = section.get("no_perms", False)
    if not isinstance(no_perms, bool):
        raise ConfigError("[mirror] no_perms must be true or false")
    nas = nas_from_config(cfg, environ)
    if is_remote(target.strip()):
        require_known_hosts(nas)  # also when [nas] ssh is unset: the plaintext archive goes over ssh
    return MirrorConfig(target=target.strip(), archive=cfg.archive_path,
                        rsync=str(section.get("rsync", "rsync")), nas=nas,
                        max_delete=max_delete, no_perms=no_perms)


def is_remote(target: str) -> bool:
    """rsync's rule: `[user@]host:path` is remote; a path whose first part has no colon is local."""
    first = target.split("/", 1)[0]
    return ":" in first and not target.startswith((".", "/"))


def _ssh(mc: MirrorConfig) -> list[str]:
    return ["-e", shlex.join(["ssh", *mc.nas.options()])] if is_remote(mc.target) else []


def _folder(mc: MirrorConfig) -> str:
    return mc.target if mc.target.endswith("/") else mc.target + "/"


def rsync_command(mc: MirrorConfig) -> list[str]:
    cmd = [mc.rsync, "-a", "--delete", "--delete-delay", "--delay-updates",
           f"--max-delete={mc.max_delete}", "--exclude=/.tmp/", "--exclude=.*.tmp",
           "--exclude=.~tmp~/", "--exclude=/#recycle/", "--exclude=@eaDir/",
           f"--exclude=/{MIRROR}"]  # the mark: never copied over, never deleted by --delete
    if mc.no_perms:
        cmd.append("--no-perms")
    return [*cmd, *_ssh(mc), f"{mc.archive}/", mc.target]


def _target_names(mc: MirrorConfig, runner) -> list[str]:
    """What the target folder holds at its top, from `rsync --list-only`, which needs no shell."""
    proc = runner([mc.rsync, "--list-only", *_ssh(mc), _folder(mc)], capture_output=True,
                  text=True, timeout=600)
    if proc.returncode != 0:
        raise MirrorRefused(f"refusing to mirror: the target can't be listed (rsync exit "
                            f"{proc.returncode}); create its folder first", returncode=proc.returncode)
    names = []
    for line in proc.stdout.splitlines():
        match = _LISTED.match(line)
        if match and match["name"] not in (".", MIRROR, *DSM_FOLDERS):
            names.append(match["name"])
    return names


def _check_target(mc: MirrorConfig, expected_uuid: str, runner) -> None:
    names = _target_names(mc, runner)
    if not names:
        return  # empty: the first mirror
    if SENTINEL not in names:
        raise MirrorRefused("refusing to mirror: the target holds other files and no archive.json")
    with tempfile.TemporaryDirectory(prefix="recordings-mirror-") as tmp:
        proc = runner([mc.rsync, *_ssh(mc), _folder(mc) + SENTINEL, f"{tmp}/"],
                      capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise MirrorRefused(f"refusing to mirror: the target's {SENTINEL} can't be read (rsync "
                                f"exit {proc.returncode})", returncode=proc.returncode)
        try:
            uuid = read_sentinel(Path(tmp)).uuid
        except SentinelError:
            raise MirrorRefused(f"refusing to mirror: the target's {SENTINEL} is not valid") from None
    if uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the target is another archive's mirror (its UUID "
                            "differs). If that archive is gone for good, empty the target first")


MIRROR_TEXT = """# A read-only copy of the archive

The homelab server copies its archive here every hour (spec §15.1). Read it freely, but change
nothing: the next run overwrites or deletes every change. `recordings init --adopt` and every
writer refuse a folder that holds this file. To change anything, use the app.
"""


def _mark(mc: MirrorConfig, runner) -> None:
    """MIRROR.md goes on first, so the copy says it is one before its archive.json arrives."""
    with tempfile.TemporaryDirectory(prefix="recordings-mirror-") as tmp:
        mark = Path(tmp) / MIRROR
        mark.write_text(MIRROR_TEXT, encoding="utf-8")
        proc = runner([mc.rsync, *_ssh(mc), str(mark), _folder(mc)], capture_output=True,
                      text=True, timeout=600)
    if proc.returncode != 0:
        raise MirrorError(f"the target's {MIRROR} could not be written (rsync exit "
                          f"{proc.returncode})", returncode=proc.returncode)


def run_mirror(mc: MirrorConfig, *, expected_uuid: str, runner=subprocess.run) -> str:
    try:
        identity = read_sentinel(mc.archive)
    except SentinelError as exc:
        raise MirrorRefused(f"refusing to mirror: {exc}") from None
    if identity.uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the archive's UUID changed since `recordings init`")
    _check_target(mc, expected_uuid, runner)
    _mark(mc, runner)
    proc = runner(rsync_command(mc), capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode == 25:
        raise MirrorError(f"rsync stopped after {mc.max_delete} deletions ([mirror] max_delete). "
                          "If they are meant, raise it for one run", returncode=25)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise MirrorError(f"rsync failed (exit {proc.returncode}): {last[0][:300]}",
                          returncode=proc.returncode)
    return "mirrored"
