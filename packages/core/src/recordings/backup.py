"""Backups with restic (spec §15.1): encrypted, deduplicated and versioned, to the NAS.

What is backed up is an explicit list: the archive, config.toml, docker/deploy.env (as
[backup] extra_paths), and a consistent copy of state.db taken with SQLite's own backup command.
Nothing else in the state folder is named, so voice.db and the Plaud token store are left out by
construction. The archive must carry its sentinel, with the UUID state.db expects, before anything
is backed up: an empty mount point backed up hourly would age the real snapshots out (§6.7).

restic is run through its CLI, with --json where it has it
(https://restic.readthedocs.io/en/stable/075_scripting.html). The repository's password reaches
restic only through its environment (RESTIC_PASSWORD_FILE, or RESTIC_PASSWORD), never as an
argument, and nothing here prints it. The transport is rest-server with --append-only, over TLS
with `--cacert` or over the tailnet; SFTP is the fallback.

restic never treats another process's lock as stale on its own: only `restic unlock` removes one
(it removes stale locks only, and works in append-only mode). So every job that locks the
repository runs `unlock` first. That is safe only because the ops lock (recordings.ops) keeps
this server's restic runs one at a time: restic judges staleness by host name and PID, and the
CLI's container and the loop's share a host name but not their PIDs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recordings.archive import Archive, utc_stamp
from recordings.config import Config, ConfigError, secret
from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT
from recordings.selfdoc import validate
from recordings.sentinel import SentinelError, read_sentinel
from recordings.ssh import Nas, nas_free_bytes, nas_from_config
from recordings.state import State

TAG = "recordings"
MILESTONE_TAGS = ("pre-import", "post-import")
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")
_PASSWORD_IN_URL = re.compile(r"://[^/@\s]*:[^/@\s]*@")


class BackupError(RuntimeError):
    """A backup job failed or refused; a failed restic raises this class itself, with restic's
    exit status as `returncode`. Only the type's name and `returncode` are recorded and alerted
    (ops.failure_detail). The message can name paths, so it goes only to the debug log."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class BackupRefused(BackupError):
    """Refused before restic ran: no sentinel, another archive, a missing file or password."""


class NasLowOnSpace(BackupRefused):
    pass


class RestoreTestFailed(BackupError):
    """The restored copy did not validate."""


class RestoreCopyLeftBehind(BackupError):
    """The restore test's copy could not be removed: a full plaintext copy is on the disk."""


@dataclass(frozen=True)
class Keep:
    hourly: int = 24
    daily: int = 14
    weekly: int = 8
    monthly: int = 12


@dataclass(frozen=True)
class BackupConfig:
    repository: str
    host: str  # restic's --host: the writer ID, never a container's host name
    archive: Path
    state: Path
    paths: tuple[Path, ...]  # config.toml and the extra paths, besides the archive and state copy
    keep: Keep
    prune: bool
    restic: str
    cache_dir: Path | None
    restore_scratch: Path | None
    nas: Nas
    nas_repo_path: str | None
    min_nas_free_bytes: int
    rest_username: str | None = None  # rest-server's user, with a rest: repository
    cacert: Path | None = None  # rest-server's self-signed certificate, for an https: repository
    retry_lock: str | None = None  # restic --retry-lock: set by ops.backup_config for the CLI


def _positive_int(section: dict, key: str, default: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"[backup] {key} must be a whole number of 1 or more")
    return value


def from_config(cfg: Config, environ: Mapping[str, str]) -> BackupConfig:
    section = cfg.data.get("backup", {})
    repository = section.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        raise ConfigError("[backup] repository is not set (see config.example.toml)")
    if _PASSWORD_IN_URL.search(repository):  # the message never repeats the URL
        raise ConfigError("[backup] repository: the repository URL carries credentials; put the "
                          "password in the secret file (RESTIC_REST_PASSWORD_FILE) and the user "
                          "in [backup] rest_username")
    missing = [n for n, v in (("[archive] path", cfg.archive_path), ("[state] path", cfg.state_path),
                              ("[archive] writer_id", cfg.writer_id)) if v is None]
    if missing:
        raise ConfigError("set " + ", ".join(missing) + " before backing up")
    extra = section.get("extra_paths", [])
    if not isinstance(extra, list) or not all(isinstance(p, str) for p in extra):
        raise ConfigError("[backup] extra_paths must be a list of paths")
    prune = section.get("prune", True)
    if not isinstance(prune, bool):
        raise ConfigError("[backup] prune must be true or false")
    min_free_gb = section.get("min_nas_free_gb", 10)
    if not isinstance(min_free_gb, (int, float)) or isinstance(min_free_gb, bool) or min_free_gb < 0:
        raise ConfigError("[backup] min_nas_free_gb must be a number")
    cacert = section.get("cacert")
    if cacert is not None and (not isinstance(cacert, str) or not cacert.strip()):
        raise ConfigError("[backup] cacert must be the path to rest-server's certificate")
    cacert_path = Path(cacert) if cacert else None
    try:
        if cacert_path is not None and cacert_path.stat().st_size == 0:
            cacert_path = None  # left empty: http over the tailnet, so no certificate (runbook 1b)
    except OSError:
        pass  # a missing file stays named, and restic says it can't read it
    paths = ([cfg.path] if cfg.path else []) + [Path(p) for p in extra]
    keep = Keep(**{k: _positive_int(section, f"keep_{k}", d)
                   for k, d in (("hourly", 24), ("daily", 14), ("weekly", 8), ("monthly", 12))})
    return BackupConfig(
        repository=repository.strip(), host=cfg.writer_id, archive=cfg.archive_path,
        state=cfg.state_path, paths=tuple(paths), keep=keep, prune=prune,
        restic=str(section.get("restic", "restic")),
        cache_dir=Path(section["cache_dir"]) if section.get("cache_dir") else None,
        restore_scratch=Path(section["restore_scratch"]) if section.get("restore_scratch") else None,
        nas=nas_from_config(cfg, environ),
        nas_repo_path=section.get("nas_repo_path") or None,
        min_nas_free_bytes=int(min_free_gb * 1024 ** 3),
        rest_username=section.get("rest_username") or None,
        cacert=cacert_path)


def restic_env(bc: BackupConfig, environ: Mapping[str, str]) -> dict[str, str]:
    """restic's whole environment: nothing from ours but these, so no stray variable leaks in."""
    env = {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "HOME": environ.get("HOME", "/tmp"), "RESTIC_REPOSITORY": bc.repository}
    if environ.get("RESTIC_PASSWORD_FILE"):
        env["RESTIC_PASSWORD_FILE"] = environ["RESTIC_PASSWORD_FILE"]
    elif environ.get("RESTIC_PASSWORD"):
        env["RESTIC_PASSWORD"] = environ["RESTIC_PASSWORD"]
    else:
        raise BackupRefused("no restic password: set RESTIC_PASSWORD_FILE (a Docker secret)")
    if bc.cache_dir is not None:
        env["RESTIC_CACHE_DIR"] = str(bc.cache_dir)
    if bc.repository.startswith("rest:"):
        rest_password = secret("rest_password", environ)
        if rest_password:
            env["RESTIC_REST_PASSWORD"] = rest_password
        if bc.rest_username:
            env["RESTIC_REST_USERNAME"] = bc.rest_username
    return env


def restic_base(bc: BackupConfig) -> list[str]:
    base = [bc.restic]
    if bc.repository.startswith("sftp:"):
        base += ["-o", "sftp.args=" + " ".join(bc.nas.options())]
    if bc.cacert is not None:
        base += ["--cacert", str(bc.cacert)]
    if bc.retry_lock:
        base += ["--retry-lock", bc.retry_lock]
    return base


def backup_command(bc: BackupConfig, state_copy: Path, *, tags: Sequence[str] = ()) -> list[str]:
    tag_args = [x for t in (TAG, *tags) for x in ("--tag", t)]
    return [*restic_base(bc), "backup", "--json", "--host", bc.host, *tag_args,
            "--exclude", str(bc.archive / ".tmp"), "--exclude", ".*.tmp",
            str(bc.archive), *(str(p) for p in bc.paths), str(state_copy)]


def forget_command(bc: BackupConfig) -> list[str]:
    keep_tags = [x for t in MILESTONE_TAGS for x in ("--keep-tag", t)]
    return [*restic_base(bc), "forget", "--host", bc.host, "--tag", TAG,
            "--keep-hourly", str(bc.keep.hourly), "--keep-daily", str(bc.keep.daily),
            "--keep-weekly", str(bc.keep.weekly), "--keep-monthly", str(bc.keep.monthly),
            *keep_tags, "--prune"]


def unlock_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "unlock"]  # stale locks only; never --remove-all


def check_subset(now: datetime) -> str:
    """Which quarter of the pack files this week's check reads back: 1/4 to 4/4 in turn, so
    four weekly checks read all of them. A plain `check` never reads the data itself."""
    return f"{now.toordinal() // 7 % 4 + 1}/4"


def check_command(bc: BackupConfig, *, subset: str | None = None) -> list[str]:
    return [*restic_base(bc), "check", *([f"--read-data-subset={subset}"] if subset else [])]


def restore_command(bc: BackupConfig, target: Path) -> list[str]:
    # --verify reads every restored file back and compares it with the repository.
    return [*restic_base(bc), "restore", "latest", "--host", bc.host, "--tag", TAG,
            "--target", str(target), "--verify"]


def init_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "init"]


def _run(bc: BackupConfig, cmd: list[str], environ: Mapping[str, str], what: str, runner,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = runner(cmd, env=restic_env(bc, environ), capture_output=True, text=True,
                  timeout=timeout)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise BackupError(f"restic {what} failed (exit {proc.returncode}): "
                          + _USERINFO.sub(r"\1***@", last[0])[:300], returncode=proc.returncode)
    return proc


def _unlock(bc: BackupConfig, environ: Mapping[str, str], runner) -> None:
    _run(bc, unlock_command(bc), environ, "unlock", runner, timeout=600)


def _check_archive(bc: BackupConfig, state: State) -> str:
    try:
        identity = read_sentinel(bc.archive)
    except SentinelError as exc:
        raise BackupRefused(f"refusing to back up: {exc}") from None
    if state.meta("archive_uuid") != identity.uuid:
        raise BackupRefused("refusing to back up: the archive's UUID changed since `recordings init`")
    return identity.uuid


@dataclass(frozen=True)
class BackupResult:
    snapshot_id: str
    files: int
    data_added: int
    nas_free: int | None = None  # bytes free on the NAS; None when neither df answered (§15.1)


def run_init(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _run(bc, init_command(bc), environ, "init", runner, timeout=600)
    return "repository initialised"


def run_backup(bc: BackupConfig, state: State, environ: Mapping[str, str], *,
               tags: Sequence[str] = (), runner=subprocess.run) -> BackupResult:
    _check_archive(bc, state)
    for path in bc.paths:
        if not path.is_file():
            raise BackupRefused(f"refusing to back up: {path} is missing")
    free = None
    if bc.nas_repo_path and bc.nas.ssh:
        free = nas_free_bytes(bc.nas, bc.nas_repo_path, runner=runner)
        if free is not None and free < bc.min_nas_free_bytes:
            raise NasLowOnSpace(f"refusing to back up: only {free / 1024 ** 3:.1f} GB free on the NAS")
    state_copy = state.backup_to(bc.state / "backup" / "state.db")
    _unlock(bc, environ, runner)
    proc = _run(bc, backup_command(bc, state_copy, tags=tags), environ, "backup", runner)
    summary = None
    for line in proc.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary":
            summary = message
    if not summary or not summary.get("snapshot_id"):
        raise BackupError("restic backup finished without a snapshot")
    return BackupResult(snapshot_id=summary["snapshot_id"],
                        files=int(summary.get("total_files_processed") or 0),
                        data_added=int(summary.get("data_added") or 0), nas_free=free)


def run_forget(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _unlock(bc, environ, runner)
    _run(bc, forget_command(bc), environ, "forget", runner)
    return "retention applied"


def run_check(bc: BackupConfig, environ: Mapping[str, str], *, now: datetime | None = None,
              runner=subprocess.run) -> str:
    """`restic check`; given `now`, it also reads back this week's quarter of the data."""
    subset = check_subset(now) if now is not None else None
    _unlock(bc, environ, runner)
    _run(bc, check_command(bc, subset=subset), environ, "check", runner)
    return f"repository checked, data {subset} read back" if subset else "repository checked"


@dataclass(frozen=True)
class RestoreResult:
    recordings: int
    problems: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def _tree_bytes(root: Path) -> int:
    total = 0
    for folder, _, names in os.walk(root):
        for name in names:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def _check_scratch_space(bc: BackupConfig, keep_free_percents: Sequence[float]) -> None:
    """The restore fits, and doesn't take its disk below a threshold it is above now. The scratch
    folder sits on the archive's disk, where 20% free raises a warning and 5% stops imports
    (Task 9's [disk]); a disk already below one isn't made to fail the test for it."""
    needed = _tree_bytes(bc.archive) + sum(p.stat().st_size for p in bc.paths if p.is_file())
    usage = shutil.disk_usage(bc.restore_scratch)
    after = usage.free - needed
    crossed = [t for t in sorted(keep_free_percents, reverse=True)
               if usage.free * 100 >= t * usage.total > after * 100]
    if after <= 0 or crossed:
        where = f"below {crossed[0]:g}% free" if crossed else "past full"
        raise BackupRefused(f"refusing the restore test: restoring {needed / 1024 ** 3:.1f} GB "
                            f"would take the scratch folder's disk {where}")


def run_restore_test(bc: BackupConfig, environ: Mapping[str, str], *, expected_uuid: str,
                     now: datetime,
                     keep_free_percents: Sequence[float] = (WARN_FREE_PERCENT, STOP_FREE_PERCENT),
                     runner=subprocess.run) -> RestoreResult:
    """Restore the newest snapshot into scratch and prove it (§15.1): restic's --verify, then
    `recordings validate` with every hash checked, and the same archive UUID.

    Copies an earlier, killed test left behind are removed first. The new copy is always removed,
    and a copy that can't be is an error: it is the whole archive, unencrypted.
    """
    if bc.restore_scratch is None:
        raise BackupRefused("[backup] restore_scratch is not set")
    if not bc.restore_scratch.is_dir():
        raise BackupRefused(f"[backup] restore_scratch {bc.restore_scratch} doesn't exist")
    for old in sorted(bc.restore_scratch.glob("restore-*")):
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)  # errors propagate: a copy left behind must not be hidden
        else:
            old.unlink()
    _check_scratch_space(bc, keep_free_percents)
    target = bc.restore_scratch / f"restore-{utc_stamp(now)}"
    try:
        _unlock(bc, environ, runner)
        _run(bc, restore_command(bc, target), environ, "restore", runner)
        restored = target / bc.archive.relative_to(bc.archive.anchor)
        problems = list(validate(restored, deep=True))
        try:
            if read_sentinel(restored).uuid != expected_uuid:
                problems.append({"path": str(restored), "message": "a different archive's UUID"})
        except SentinelError as exc:
            problems.append({"path": str(restored), "message": str(exc)})
        count = sum(1 for _ in Archive(restored).recording_dirs())
    finally:
        shutil.rmtree(target, ignore_errors=True)
    if target.exists():  # checked here, so the error a failed restore raised isn't masked above
        raise RestoreCopyLeftBehind(f"the restored copy at {target} could not be removed: "
                                    "delete it by hand")
    return RestoreResult(recordings=count, problems=tuple(problems))
