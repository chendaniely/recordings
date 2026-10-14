"""The read-only mirror for the Mac (spec §15.1): an hourly one-way rsync of the archive to a
share on the NAS, through the mirror's own NAS account ([nas] ssh, with its key).

- `--delay-updates` puts every changed file in place at the end, so a reader never sees half a
  copy.
- `--delete --delete-delay` removes what the archive no longer holds, after the transfer. A
  dry run comes first and counts the deletions: more than [mirror] max_delete and the run is
  refused before it deletes anything, every hour, until someone looks (--max-delete=N on the real
  run stays as a backstop, rsync exit 25, for a change made between the two passes). --max-delete
  alone would cap each run, not the accident: rsync deletes up to N and carries on.
- `.tmp/` and `.*.tmp` (work in progress) are never copied. `.~tmp~/`, where `--delay-updates`
  stages files and which an interrupted run leaves behind, is excluded too; on the Mac, Synology
  Drive's sync filter must exclude it as well. DSM's own `#recycle` and `@eaDir` folders are left
  alone. `@eaDir/` and `.~tmp~/` are perishable filters, so a folder the archive dropped can still
  be removed from the mirror when one of them sits inside it.
- It refuses to run when the archive's sentinel is missing or its UUID has changed (§6.7), so an
  unmounted archive can never empty the mirror through --delete.
- A remote target needs RECORDINGS_NAS_KNOWN_HOSTS_FILE even when [nas] ssh is unset: only that
  pinned file is trusted for the host (ssh.ssh_options).
- It refuses a target that is neither empty nor this archive's mirror: a `MIRROR.md` that carries
  this archive's UUID. So a wrong path, a mirror made before a rollback to a fresh `init` (a new
  UUID), or the archive itself is never emptied, and Synology Drive never deletes it on the Mac.
  The mark, not `archive.json`, says whose mirror it is: with --delay-updates `archive.json` only
  arrives at the end, so a first run cut short must still be recognised on the next one.
- It puts `MIRROR.md` on the target before anything else, and `--delete` never removes it, so
  the copy says it is one: `recordings init --adopt` and every Writer refuse a folder holding it
  (§3), and it tells a person or an agent reading the copy to change nothing there.
- A listing it can't read is not an empty target: a line it doesn't understand counts as a file.
- `[mirror] no_perms = true` adds `--no-perms`, for a share with Windows ACLs where rsync's chmod
  fails (the runbook's spike).
- Time: rsync's own `--timeout` ends a run that moves no data for IO_TIMEOUT (exit 30), so a
  stalled NAS never holds the ops lock for long. The loop also stops a run (and its dry run)
  after TIMEOUT, so the backups between them go on; `recordings mirror` from the CLI has no such
  limit, so a first mirror bigger than TIMEOUT can be made in one run (the runbook, step 4). A
  run cut short keeps its finished files in `.~tmp~/`, which rsync 3.x on the NAS uses as the
  basis for the same files next time: they don't cross the network again, but the NAS reads and
  writes each of them once more.
- A refused run's deletions are logged at debug level, as recording IDs and counts, so
  `recordings mirror --debug` shows which recordings would go; `recordings mirror --max-delete N`
  then allows N for that one run, and the config keeps its limit.

The mirror holds private recordings too: on the Mac, keeping external agents out of them is
policy (§7.4).
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError
from recordings.ids import ID_RE
from recordings.sentinel import MIRROR, SentinelError, read_sentinel
from recordings.ssh import Nas, nas_from_config, require_known_hosts

log = logging.getLogger(__name__)
MAX_DELETE = 50
TIMEOUT = 6 * 3600  # seconds the loop gives a run, and its dry run; a CLI run has no limit
IO_TIMEOUT = 600  # rsync --timeout: a run that moves no data for this long stops (exit 30)
DSM_FOLDERS = ("#recycle", "@eaDir")  # the NAS's recycle bin and its indexer's folders
# One line of `rsync --list-only`: permissions, size, date, time, then the name.
_LISTED = re.compile(r"^[-dlpscb][-rwxsStT]{9}\S*\s+[\d,.]+[KMGTP]?\s+\S+\s+\S+\s+(?P<name>.+)$")
UNREADABLE = "<a listing line that could not be read>"
# One line of `rsync --dry-run --itemize-changes`: `*deleting   name`, or the itemized changes of a
# file (`>f+++++++++ name`; openrsync prints two characters fewer than rsync 3.x).
_ITEMIZED = re.compile(r"^(?:\*deleting\s+(?P<gone>\S.*)|[<>ch.][fdLDS][.+?a-zA-Z]{7,9}\s.+)$")
_MARK_UUID = re.compile(r"archive-uuid: ([0-9a-f-]{36})")


class MirrorError(RuntimeError):
    """The mirror failed or refused; a failed rsync raises this class itself, with rsync's exit
    status as `returncode` (25: max_delete was reached). Only the type's name and `returncode`
    are recorded and alerted (ops.failure_detail)."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class MirrorRefused(MirrorError):
    """Refused before rsync copied or deleted anything: the archive, or the target, isn't the right
    one, or the run would delete more than [mirror] max_delete allows."""


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


def rsync_command(mc: MirrorConfig, *, dry_run: bool = False) -> list[str]:
    """The mirror run, or with `dry_run` its dry run: it lists what would change and what would be
    deleted, and has no --max-delete, which would stop the count at the limit."""
    cmd = [mc.rsync, "-a", "--delete", "--delete-delay", "--delay-updates",
           f"--timeout={IO_TIMEOUT}",
           *(["--dry-run", "--itemize-changes"] if dry_run else [f"--max-delete={mc.max_delete}"]),
           "--exclude=/.tmp/", "--exclude=.*.tmp", "--filter=-p .~tmp~/",
           "--exclude=/#recycle/", "--filter=-p @eaDir/",
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
                            f"{proc.returncode}); check that its folder exists and that the "
                            "account can reach it", returncode=proc.returncode)
    names = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        match = _LISTED.match(line)
        if match is None:
            names.append(UNREADABLE)  # fails closed: a line it can't read is a file it can't see
        elif match["name"] not in (".", *DSM_FOLDERS):
            names.append(match["name"])
    return names


MIRROR_TEXT = """# A read-only copy of the archive

The homelab server copies its archive here every hour (spec §15.1). Read it freely, but change
nothing: the next run overwrites or deletes every change. `recordings init --adopt` and every
writer refuse a folder that holds this file. To change anything, use the app.
"""


def mark_text(uuid: str) -> str:
    """MIRROR.md's text: the notice, and the UUID of the archive this is the mirror of."""
    return f"{MIRROR_TEXT}\narchive-uuid: {uuid}\n"


def _mark_uuid(path: Path) -> str | None:
    """The UUID in a fetched MIRROR.md, or None unless it is a small file with exactly one."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    found = [m[1] for line in lines if (m := _MARK_UUID.fullmatch(line))]
    return found[0] if len(found) == 1 else None


def _check_target(mc: MirrorConfig, expected_uuid: str, runner) -> None:
    names = _target_names(mc, runner)
    if MIRROR not in names:
        if names:
            raise MirrorRefused("refusing to mirror: the target holds other files and no "
                                f"{MIRROR} of this archive")
        return  # empty: the first mirror
    with tempfile.TemporaryDirectory(prefix="recordings-mirror-") as tmp:
        proc = runner([mc.rsync, *_ssh(mc), _folder(mc) + MIRROR, f"{tmp}/"],
                      capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise MirrorRefused(f"refusing to mirror: the target's {MIRROR} can't be read (rsync "
                                f"exit {proc.returncode})", returncode=proc.returncode)
        uuid = _mark_uuid(Path(tmp) / MIRROR)
    if uuid is None:
        raise MirrorRefused(f"refusing to mirror: the target's {MIRROR} is not valid")
    if uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the target is another archive's mirror (its UUID "
                            "differs). If that archive is gone for good, empty the target first")


def _mark(mc: MirrorConfig, uuid: str, runner) -> None:
    """MIRROR.md goes on first, so the copy says it is one, and whose, before its archive.json
    arrives. -c: an unchanged mark isn't rewritten, so Synology Drive doesn't sync it every hour."""
    with tempfile.TemporaryDirectory(prefix="recordings-mirror-") as tmp:
        mark = Path(tmp) / MIRROR
        mark.write_text(mark_text(uuid), encoding="utf-8")
        proc = runner([mc.rsync, "-c", *_ssh(mc), str(mark), _folder(mc)], capture_output=True,
                      text=True, timeout=600)
    if proc.returncode != 0:
        raise MirrorError(f"the target's {MIRROR} could not be written (rsync exit "
                          f"{proc.returncode})", returncode=proc.returncode)


def _where(path: str) -> str:
    """The recording a deletion belongs to, by ID, and never a name: a file someone left in the
    mirror could be called anything."""
    rid = next((part for part in path.split("/") if ID_RE.fullmatch(part)), None)
    return f"recording {rid}" if rid else "outside any recording"


def _check_deletions(mc: MirrorConfig, runner, timeout: float | None) -> None:
    """Count what the run would delete, and refuse, deleting nothing, past [mirror] max_delete.
    A dry run it can't read stops the mirror too. No path leaves this function: the refusal
    carries the count, and the debug log (`recordings mirror --debug`) recording IDs and counts."""
    proc = runner(rsync_command(mc, dry_run=True), capture_output=True, text=True,
                  timeout=timeout)
    if proc.returncode != 0:
        raise MirrorError(f"the rsync dry run failed (exit {proc.returncode}); nothing was "
                          "deleted", returncode=proc.returncode)
    gone: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        match = _ITEMIZED.match(line)
        if match is None:
            raise MirrorError("the rsync dry run's output could not be read; nothing was deleted")
        if match["gone"] is not None:
            gone[_where(match["gone"])] += 1
    if gone.total() > mc.max_delete:
        for where, count in sorted(gone.items()):  # the loop logs at INFO: --debug only
            log.debug("the mirror would delete, %s: %d", where, count)
        raise MirrorRefused(f"the mirror would delete {gone.total()} files and folders, over the "
                            f"limit of {mc.max_delete} ([mirror] max_delete): nothing was deleted. "
                            "`recordings mirror --debug` shows where; if they are meant, "
                            "`recordings mirror --max-delete N` allows N for one run")


def run_mirror(mc: MirrorConfig, *, expected_uuid: str, runner=subprocess.run,
               timeout: float | None = TIMEOUT) -> str:
    """One mirror run. `timeout` caps the dry run and the run each (the loop's TIMEOUT); None, for
    the CLI, lets a big first mirror finish, and rsync's --timeout still ends a stall."""
    try:
        identity = read_sentinel(mc.archive)
    except SentinelError as exc:
        raise MirrorRefused(f"refusing to mirror: {exc}") from None
    if identity.uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the archive's UUID changed since `recordings init`")
    _check_target(mc, expected_uuid, runner)
    _mark(mc, identity.uuid, runner)
    _check_deletions(mc, runner, timeout)
    proc = runner(rsync_command(mc), capture_output=True, text=True, timeout=timeout)
    if proc.returncode == 25:
        raise MirrorError(f"rsync stopped deleting at {mc.max_delete} ([mirror] max_delete): the "
                          "archive changed since the dry run. The next run counts again",
                          returncode=25)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise MirrorError(f"rsync failed (exit {proc.returncode}): {last[0][:300]}",
                          returncode=proc.returncode)
    return "mirrored"
