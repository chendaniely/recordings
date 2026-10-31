"""`recordings doctor` (spec §5, §6.7, §15.1): what is configured, as presence only.

It never prints a secret's value, nor a repository URL, which can carry a password: only whether
each is set and which transport it is. It reports the secrets first, so a broken config.toml still
shows them (stage-1 carry-over). On a machine that writes the archive it probes hard links on the
archive's disk, because write-once publishing needs them, and it reports whether this machine's
state.db matches the archive and writer ID and is not from a newer release. An archive carrying
MIRROR.md is the NAS mirror's copy, which is never written. A missing or unusable index is a
warning: `recordings reindex` rebuilds it.

Each Compose service runs it in its own role (`--role`, else RECORDINGS_ROLE, else `all`):
- `web` serves the Library and runs the import. It must hold no backup, mirror or alert secret,
  nor the Plaud sync's ping URL, nor `/plaud`, and doctor checks that none is there.
- `backup` runs the backups, the mirror and the alerts. Its archive mount is read-only, so the
  hard-link probe is skipped there, and ssh needs this user's passwd entry. It holds neither the
  sync's ping URL nor `/plaud`.
- `worker` (stage 2b spec §17.2) runs the Plaud sync. Its backup, mirror, ssh and restic checks
  are skipped, and so is the "alerts are off" warning: it sends no push by design. Its own: the
  consent list, the sign-in's presence and modes (never its contents), the keep-out word, and the
  sync's dead-man URL, whose check is its only alert. It must hold no backup secret. Doctor never
  calls Plaud; `recordings plaud check` does.
- `all`, the default outside Docker, checks everything but the worker's own.
Every role warns when retention is off here, and reports where the consent list is, as
information (or, when it is unset, as a note in `notes`): the import reads it and fails closed
by itself. Every role also checks that
`[archive] default_timezone` is a zone this machine knows, and that `[disk]` is a valid pair.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import shutil
import sqlite3
import stat
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings import alerts, backup, mirror
from recordings.config import SECRETS, Config, ConfigError, load_config, secret, secret_path
from recordings.disk import disk_thresholds
from recordings.index import Index
from recordings.locks import running
from recordings.plaud.baseline import KeepOutWordsError, enforced_words
from recordings.plaud.checks import NoSyncYet, PlaudDB
from recordings.plaud.consent import ConsentListError, consent_list_path
from recordings.plaud.keepout import kept_lines_note, read_consent
from recordings.plaud.settings import BAD_WORD, DEFAULT_TOKEN_STORE, plaud_settings
from recordings.sentinel import MIRROR, SentinelError, is_mirror, read_sentinel
from recordings.ssh import nas_free_bytes
from recordings.state import State, StateError, unreadable

TOOLS = ("restic", "rsync", "ssh", "ffprobe", "ffmpeg")
ROLES = ("web", "backup", "worker", "all")
# docker/compose.yml gives each set to one service: the backup set to `backup`, the alert set to
# `backup` too (and to `worker` with the later ntfy change, stage 2b spec §16), and the sync's ping
# URL, deadman_sync_url, to `worker` alone. `web` faces the browser and holds none of them.
BACKUP_SECRETS = (
    "restic_password",
    "rest_password",
    "nas_ssh_key",
    "nas_known_hosts",
    "deadman_url",
)
ALERT_SECRETS = ("ntfy_topic", "ntfy_token")
SECRETS_DIR = Path("/run/secrets")  # where Docker mounts a service's secrets, by name
# What each role must not hold (see the comment above): the table `doctor()` reads.
FORBIDDEN = {
    "web": (*BACKUP_SECRETS, *ALERT_SECRETS, "deadman_sync_url"),
    "worker": BACKUP_SECRETS,
    "backup": ("deadman_sync_url",),
}
PLAUD_MOUNT = DEFAULT_TOKEN_STORE.parent  # the sign-in's folder, mounted in `worker` only
OPEN_TO_OTHERS = 0o077  # ssh refuses a key with any of these bits set; doctor warns for any secret


def _secrets(environ: Mapping[str, str], problems: list[str], warnings: list[str]) -> dict:
    out = {}
    for name, spec in SECRETS.items():
        try:
            found = secret_path(name, environ) if spec.file_only else secret(name, environ)
            present = found is not None
        except ConfigError as exc:
            (warnings if spec.optional else problems).append(str(exc))
            present = False
        file_var = f"{spec.env}_FILE"
        if present and environ.get(file_var):
            with contextlib.suppress(OSError):
                mode = Path(environ[file_var]).stat().st_mode & 0o777
                if mode & OPEN_TO_OTHERS:
                    warnings.append(
                        f"{file_var} names a file that group or others can open "
                        f"(mode {mode:o}): chmod 600 it"
                    )
        out[name] = {
            "env": file_var if spec.file_only else spec.env,
            "set": present,
            "needed_from_stage": spec.stage,
            "purpose": spec.purpose,
        }
    return out


def _forbid(
    environ: Mapping[str, str], names: Sequence[str], role: str, problems: list[str]
) -> None:
    """A secret this role must not hold, by its variable, its _FILE variable, or a Compose mount."""
    held = [
        name
        for name in names
        if environ.get(SECRETS[name].env)
        or environ.get(f"{SECRETS[name].env}_FILE")
        or _mounted(SECRETS_DIR / name)
    ]
    if held:
        problems.append(
            f"the {role} service must not hold " + ", ".join(held) + ": docker/compose.yml "
            "gives each secret only to the service that uses it"
        )


def _no_plaud_here(environ: Mapping[str, str], problems: list[str]) -> None:
    """`web` and `backup` never hold the Plaud sign-in (stage 2b spec §10.4, §17.2). The old static
    token's variable is read by name: it is no longer one of SECRETS."""
    _plaud_folder_here(PLAUD_MOUNT, problems)
    held = [n for n in ("RECORDINGS_PLAUD_TOKEN", "RECORDINGS_PLAUD_TOKEN_FILE") if environ.get(n)]
    if held:
        problems.append(
            ", ".join(held) + " is set here, but only the worker holds the Plaud sign-in, in "
            f"{DEFAULT_TOKEN_STORE}: remove it"
        )


def _plaud_folder_here(folder: Path, problems: list[str]) -> None:
    if folder.exists():
        problems.append(
            f"{folder} is mounted here, and it holds the Plaud sign-in: docker/compose.yml "
            "mounts it in `worker` only"
        )


def _no_plaud_folder_named(cfg: Config, problems: list[str]) -> None:
    """The folder `[plaud] token_store` names, when config names one other than the default."""
    if not cfg.data.get("plaud", {}).get("token_store"):
        return
    try:
        folder = plaud_settings(cfg).token_store.parent
    except ConfigError:
        return  # the worker's own check says so; here it can name no folder
    if folder != PLAUD_MOUNT:
        _plaud_folder_here(folder, problems)


def _mounted(path: Path) -> bool:
    """A secret Compose mounted. An optional one it leaves empty is /dev/null itself and holds
    nothing; any other character device (/dev/zero, a tty) is a mount that holds something."""
    try:
        found = path.stat()
        empty = stat.S_ISCHR(found.st_mode) and found.st_rdev == os.stat(os.devnull).st_rdev
    except OSError:
        return False
    return not empty


def _user(problems: list[str]) -> dict:
    uid = os.getuid()
    try:
        pwd.getpwuid(uid)
    except KeyError:
        problems.append(
            f"uid {uid} has no passwd entry, and ssh exits without one: rebuild the "
            "image, whose APP_UID and APP_GID build args come from deploy.env's "
            "RECORDINGS_UID and RECORDINGS_GID"
        )
        return {"uid": uid, "passwd_entry": False}
    return {"uid": uid, "passwd_entry": True}


def hard_links_work(root: Path) -> bool:
    """Try one hard link inside the archive's .tmp/ (which readers and mirrors skip), and leave
    nothing behind."""
    tmp = Path(root) / ".tmp"
    made = not tmp.exists()
    first = tmp / f".doctor-{uuid.uuid4().hex}"
    second = first.with_name(first.name + "-link")
    try:
        tmp.mkdir(exist_ok=True)
        first.write_bytes(b"")
        os.link(first, second)
        return True
    except OSError:
        return False
    finally:
        for path in (second, first):
            with contextlib.suppress(OSError):
                path.unlink()
        if made:
            with contextlib.suppress(OSError):
                tmp.rmdir()


def read_only_mount(root: Path) -> bool:
    """True when the archive's file system is mounted read-only, as the backup service's is."""
    try:
        return bool(os.statvfs(root).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def _archive(cfg: Config, role: str, report: dict, problems: list[str]) -> str | None:
    if cfg.archive_path is None:
        problems.append("no archive path: set [archive] path in config.toml, or RECORDINGS_ARCHIVE")
        return None
    info = {
        "path": str(cfg.archive_path),
        "exists": cfg.archive_path.is_dir(),
        "sentinel": False,
        "read_only": None,
        "hard_links": None,
    }
    report["archive"] = info
    if not info["exists"]:
        problems.append(f"archive folder {cfg.archive_path} does not exist")
        return None
    try:
        identity = read_sentinel(cfg.archive_path)
    except SentinelError as exc:
        problems.append(str(exc))
        return None
    info["sentinel"] = True
    if is_mirror(cfg.archive_path):  # §3: the mirror's copy is never written, by doctor neither
        if cfg.writer_id is not None:
            problems.append(
                f"the archive path holds {MIRROR}: it is the NAS mirror's copy, which "
                "is never written. Point [archive] path at the archive itself"
            )
        return identity.uuid
    info["read_only"] = read_only_mount(cfg.archive_path)
    if cfg.writer_id is None:  # a reader writes nothing, so it needs no hard links: no probe
        return identity.uuid
    if info["read_only"]:
        if role != "backup":  # the backup service mounts it read-only on purpose
            problems.append("the archive is mounted read-only here, so this machine can't write it")
        return identity.uuid
    info["hard_links"] = hard_links_work(cfg.archive_path)
    if not info["hard_links"]:
        problems.append(
            "hard links don't work on the archive's disk, and write-once publishing "
            "needs them: is it a network share?"
        )
    return identity.uuid


def _state(
    cfg: Config,
    archive_uuid: str | None,
    role: str,
    report: dict,
    problems: list[str],
    warnings: list[str],
) -> None:
    # Without state.db, `ops --loop` exits 78 and the backup service crash-loops; the worker
    # can't start either.
    missing = problems if role in ("backup", "worker") else warnings
    if cfg.writer_id is None:
        warnings.append("no [archive] writer_id: this machine can't write the archive")
    if cfg.state_path is None:
        warnings.append("no [state] path: this machine can't write the archive")
        return
    info = {
        "path": str(cfg.state_path),
        "state_db": (cfg.state_path / "state.db").is_file(),
        "writer_id": cfg.writer_id,
        "matches": None,
    }
    report["state"] = info
    if not info["state_db"]:
        missing.append("no state.db yet: run `recordings init` once, on the writer")
        return
    state = State.read_only(cfg.state_path)
    if state is None:  # state.db went away since the check above
        info["state_db"] = False
        missing.append("no state.db yet: run `recordings init` once, on the writer")
        return
    try:
        found_uuid, found_writer = state.meta("archive_uuid"), state.meta("writer_id")
    except sqlite3.DatabaseError as exc:  # the restore advice only for real damage
        problems.append(unreadable(state.db_path, exc))
        return
    try:  # State.open refuses a schema newer than this release (a rollback) or a damaged one
        with state.db() as conn:
            state._version(conn)
    except StateError as exc:
        problems.append(str(exc))
    except sqlite3.DatabaseError as exc:
        problems.append(unreadable(state.db_path, exc))
        return
    if archive_uuid is None:  # the archive's own problem is already reported
        return
    info["matches"] = found_uuid == archive_uuid and found_writer == cfg.writer_id
    if not info["matches"]:
        problems.append(
            "state.db belongs to another archive or another writer_id: this machine "
            "must not write here"
        )


def _index(cfg: Config, archive_uuid: str | None, report: dict, warnings: list[str]) -> None:
    """The derived index (§6.8) is rebuilt from the archive, so a missing, foreign or damaged one
    costs a `recordings reindex`, not data: a warning. Index.count() answers 0 for a damaged file,
    so whose index it is must be asked."""
    if cfg.index_path is None:
        warnings.append(
            "no [index] path: the Library can't list recordings, and the writer "
            "refuses to open (config.example.toml has the line)"
        )
        return
    index = Index(cfg.index_path)
    info = {
        "path": str(cfg.index_path),
        "exists": index.exists(),
        "recordings": index.count(),
        "matches": None,
    }
    report["index"] = info
    if info["exists"] and archive_uuid is not None:
        info["matches"] = index.archive_uuid() == archive_uuid
        if not info["matches"]:
            warnings.append(
                "the index is damaged or belongs to another archive, so the Library "
                "lists nothing: `recordings reindex` rebuilds it"
            )


def _left_behind(cfg: Config, bc: backup.BackupConfig, report: dict, warnings: list[str]) -> None:
    """What a killed job leaves (Task 11's council), and the cache folder it depends on. A job
    running now has these folders too: when the ops lock says that job runs, the folder is its
    and doctor says to leave it."""
    held = running(cfg.state_path / "locks") if cfg.state_path is not None else None
    job = held[0] if held else None
    notes = report["notes"]
    if bc.cache_dir is None:
        warnings.append(
            "[backup] cache_dir is not set, so restic's cache goes under $HOME/.cache: "
            "in the container that is the memory-backed /tmp, lost at every restart "
            '(config.example.toml: cache_dir = "/state/cache/restic")'
        )
    else:
        if cfg.state_path is not None and _device(bc.cache_dir) != _device(cfg.state_path):
            warnings.append(
                f"[backup] cache_dir {bc.cache_dir} is not on the disk [state] path is "
                "on, so restic's cache (about 0.03% of the repository) is lost at a "
                "restart or fills the wrong disk: put it under the state folder "
                '(config.example.toml: cache_dir = "/state/cache/restic")'
            )
        if _has(bc.cache_dir, "restic-check-cache-*") and job == "check":
            notes.append(
                f"{bc.cache_dir} holds a restic-check-cache-* folder that belongs to "
                "check, running now: leave it"
            )
        elif _has(bc.cache_dir, "restic-check-cache-*"):
            warnings.append(
                f"{bc.cache_dir} holds restic-check-cache-* folders, which a killed "
                "repository check leaves behind: delete them"
            )
    if bc.restore_scratch is not None and _has(bc.restore_scratch, "restore-*"):
        if job == "restore-test":
            notes.append(
                f"{bc.restore_scratch} holds a restore-* folder that belongs to "
                "restore-test, running now: leave it"
            )
        else:
            warnings.append(
                f"{bc.restore_scratch} holds a restore-* folder, an unencrypted copy of the "
                "archive that a killed restore test left behind: the next restore "
                "test removes it, or delete it now"
            )


def _device(path: Path) -> int | None:
    """The disk a folder is on, or would be created on: its nearest existing ancestor's."""
    for candidate in (path, *path.parents):
        try:
            return candidate.stat().st_dev
        except OSError:
            continue
    return None


def _has(folder: Path, pattern: str) -> bool:
    try:
        return any(folder.glob(pattern))
    except OSError:
        return False


def _transport(repository: str) -> str:
    for prefix, name in (("sftp:", "sftp"), ("rest:", "rest"), ("/", "local")):
        if repository.startswith(prefix):
            return name
    return "other"


def _backup(
    cfg: Config,
    environ: Mapping[str, str],
    role: str,
    report: dict,
    problems: list[str],
    warnings: list[str],
) -> None:
    if not cfg.data.get("backup", {}).get("repository"):
        # the backup service exists to run them; elsewhere it is a choice
        (problems if role == "backup" else warnings).append(
            "backups aren't configured: set [backup] repository"
        )
        return
    # before from_config, which may refuse first; a named but empty file is already reported
    if not report["secrets"]["restic_password"]["set"] and not environ.get("RESTIC_PASSWORD_FILE"):
        problems.append("RESTIC_PASSWORD_FILE is not set, so backups can't run")
    try:
        bc = backup.from_config(cfg, environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return
    info = {
        "transport": _transport(bc.repository),
        "password_set": report["secrets"]["restic_password"]["set"],
        "prune": bc.prune,
        "cacert_exists": bc.cacert.is_file() if bc.cacert is not None else None,
        "restore_scratch_exists": bool(bc.restore_scratch and bc.restore_scratch.is_dir()),
        "nas_free_check": bool(bc.nas.ssh and bc.nas_repo_path),
        "nas_free_bytes": None,
        "restic_installed": shutil.which(bc.restic) is not None,
    }
    report["backup"] = info
    if info["transport"] == "rest" and environ.get("RESTIC_REST_PASSWORD_FILE"):
        try:  # a named but broken file refuses every backup, with or without a username
            secret("rest_password", environ)
        except ConfigError as exc:
            if str(exc) in warnings:
                warnings.remove(str(exc))
            problems.append(str(exc))
    elif (
        info["transport"] == "rest"
        and bc.rest_username
        and not report["secrets"]["rest_password"]["set"]
    ):
        problems.append(
            "a rest-server repository with [backup] rest_username needs RESTIC_REST_PASSWORD_FILE"
        )
    if info["cacert_exists"] is False:
        problems.append(
            f"[backup] cacert names {bc.cacert}, which is not a file, so restic "
            "can't check rest-server's certificate"
        )
    if not info["restic_installed"]:
        problems.append(f"{bc.restic} is not installed")
    if not info["restore_scratch_exists"]:
        warnings.append("[backup] restore_scratch doesn't exist, so the restore test can't run")
    _left_behind(cfg, bc, report, warnings)
    if not info["nas_free_check"]:
        warnings.append(
            "the NAS's free space isn't checked: set [nas] ssh and [backup] nas_repo_path"
        )
    else:  # §15.1: ssh df, else sftp df; if neither answers, it is unknown and doctor says so
        info["nas_free_bytes"] = nas_free_bytes(bc.nas, bc.nas_repo_path)
        if info["nas_free_bytes"] is None:
            warnings.append("the NAS's free space is unknown: neither ssh df nor sftp df answered")


def _mirror(
    cfg: Config,
    environ: Mapping[str, str],
    role: str,
    report: dict,
    problems: list[str],
    warnings: list[str],
) -> None:
    if not cfg.data.get("mirror", {}).get("target"):
        (problems if role == "backup" else warnings).append(
            "the mirror isn't configured: set [mirror] target"
        )
        return
    try:
        mc = mirror.from_config(cfg, environ)
    except ConfigError as exc:
        if str(exc) not in problems:  # the backup check says the same of a missing host key
            problems.append(str(exc))
        return
    info = {
        "remote": mirror.is_remote(mc.target),
        "rsync_installed": shutil.which(mc.rsync) is not None,
    }
    report["mirror"] = info
    if not info["rsync_installed"]:
        problems.append(f"{mc.rsync} is not installed")


def _retention(cfg: Config, warnings: list[str]) -> None:
    """Config only, so every role says it: the runbook expects it from web and backup alike."""
    section = cfg.data.get("backup", {})
    if section.get("repository") and section.get("prune") is False:
        warnings.append(
            "retention is off here: it must run on the NAS, or the repository grows without limit"
        )


def _consent(cfg: Config, role: str, report: dict) -> None:
    """[plaud] consent_list, as information only. In 2a it is mounted just for import runs, and
    the import reads it and fails closed by itself. Its IDs are never read here. The worker stays
    up and needs the list, and `_worker` reports it as a problem, so those notes stay off it."""
    try:
        resolved = consent_list_path(cfg.data)  # as the import reads it: blank is unset
    except ConsentListError:
        if role != "worker":
            report["notes"].append(
                "[plaud] consent_list is not set, so the import refuses to run "
                "(spec §9.1); the services that stay up don't need it"
            )
        return
    path = cfg.data["plaud"]["consent_list"]  # shown as configured, not expanded
    here = resolved.is_file()
    report["consent_list"] = {
        "path": path,
        "here": here,
        "note": None
        if here or role == "worker"
        else "not mounted here; the import reads it and fails closed",
    }


def _timezone(cfg: Config, problems: list[str]) -> None:
    """[archive] default_timezone must name a zone this machine knows. zoneinfo reads the
    system's database (the image installs tzdata; uv.lock has no wheel), so a typo, or a machine
    without the database, leaves the import unable to place any recording."""
    try:
        ZoneInfo(cfg.default_timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        problems.append(
            f"[archive] default_timezone {cfg.default_timezone!r} is not a time zone "
            "this machine knows: check the name, and that the system's tzdata is "
            "installed"
        )


def _disk(cfg: Config, problems: list[str]) -> None:
    """[disk] thresholds, read as the import reads them: a bad pair stops the import (exit 78),
    and Status falls back to the defaults without a word, so say it here."""
    try:
        disk_thresholds(cfg)
    except ConfigError as exc:
        problems.append(str(exc))


def _alert_config(
    cfg: Config,
    environ: Mapping[str, str],
    report: dict,
    problems: list[str],
    warnings: list[str],
    deadman: str,
) -> tuple[alerts.AlertConfig | None, str]:
    """The alert config, for `_alerts` (`deadman` is `deadman_url`) and `_worker`
    (`deadman_sync_url`). When it can't be read: None and the refusal's text (a setting's name,
    never a value), which `_secrets` may have listed already, so it is removed from both lists. When
    it can: `report["alerts"]` is set, with `deadman_set` from the named field."""
    try:
        ac = alerts.from_config(cfg, environ)
    except ConfigError as exc:
        for found in (problems, warnings):
            if str(exc) in found:
                found.remove(str(exc))
        return None, str(exc)
    report["alerts"] = {
        "ntfy_server_set": bool(ac.ntfy_server),
        "topic_set": bool(ac.topic),
        "deadman_set": bool(getattr(ac, deadman)),
    }
    return ac, ""


def _alerts(
    cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str], warnings: list[str]
) -> None:
    ac, refusal = _alert_config(cfg, environ, report, problems, warnings, "deadman_url")
    if ac is None:
        # The loop reads any failure as "alerts off": no alert and no dead-man ping is sent. So
        # each is a problem (a bad URL, topic or secret file, never its value), whether or not
        # _secrets listed the same text as a warning.
        problems.append(f"alerts and dead-man pings are off: {refusal}")
        return
    if ac.topic and not ac.ntfy_server:  # the template presets ntfy_server, so a deleted line
        warnings.append(
            "RECORDINGS_NTFY_TOPIC_FILE is set but [alerts] ntfy_server is not, so no "
            "alert is sent: set ntfy_server"
        )
    elif not ac.enabled:
        warnings.append("alerts are off: set [alerts] ntfy_server and RECORDINGS_NTFY_TOPIC_FILE")
    if not ac.deadman_url:
        warnings.append(
            "no dead-man's switch, so a stopped server or backup goes unnoticed: "
            "set RECORDINGS_DEADMAN_URL_FILE"
        )


def _ssh(report: dict, problems: list[str]) -> None:
    """An SFTP repository, a remote mirror and the NAS's free-space check all run ssh."""
    info, mirror_info = report["backup"], report["mirror"]
    needs = (info is not None and (info["transport"] == "sftp" or info["nas_free_check"])) or (
        mirror_info is not None and mirror_info["remote"]
    )
    if needs and not report["tools"]["ssh"]:
        problems.append(
            "ssh is not installed, and the SFTP repository, the remote mirror or the "
            "NAS's free-space check needs it"
        )


def _owner() -> int:
    return os.geteuid()


def _plaud_modes(token_store: Path, problems: list[str]) -> None:
    """The sign-in's folder at most 0700 and its token at most 0600, both this user's. Their
    modes and owners only: the token is never opened."""
    for path, widest, fix in (
        (token_store.parent, 0o700, "chmod 700"),
        (token_store, 0o600, "chmod 600"),
    ):
        try:
            found = path.stat()
        except OSError:
            continue  # a missing token is the sign-in problem
        mode = found.st_mode & 0o777
        if mode & ~widest:
            problems.append(f"{path} is open to more than its owner (mode {mode:o}): {fix} it")
        if found.st_uid != _owner():
            problems.append(
                f"{path} belongs to uid {found.st_uid}, not this user ({_owner()}): the worker "
                "must own the Plaud sign-in"
            )


def _enforced_words(cfg: Config, problems: list[str]) -> list[str] | None:
    """The keep-out words the worker enforces (stage 2b spec §12.2), from plaud_state, read-only,
    through the worker's own reader. None when they can't be read: no state.db or a damaged one
    (`_state` says so), or a row that isn't a list (a problem here). `[]` means none enforced."""
    state = State.read_only(cfg.state_path) if cfg.state_path is not None else None
    if state is None:
        return None
    try:
        return list(enforced_words(PlaudDB(state)))
    except NoSyncYet:
        return []  # a state.db from before the sync's tables: no word has been enforced
    except KeepOutWordsError as exc:
        problems.append(str(exc))
    except sqlite3.Error:
        pass  # `_state` reports the damaged state.db
    return None


def _worker(
    cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str], warnings: list[str]
) -> None:
    """The worker's own checks (stage 2b spec §17.2): the sync's dead-man URL, the keep-out word,
    the consent list, and the sign-in's presence and modes. Never a value or a hash, never the
    token's contents (decision 28: its expiry waits for Phase 2), and never a call to Plaud."""
    info: dict[str, Any] = {
        "consent_ids": None,
        "consent_stop": None,
        "review_lines": [],
        "token_store": False,
        "keep_out_words": [],
        "deadman_sync_set": False,
    }
    report["plaud"] = info
    ac, refusal = _alert_config(cfg, environ, report, problems, warnings, "deadman_sync_url")
    if ac is None:  # its text names a setting, never a value
        hint = (
            " (RECORDINGS_DEADMAN_SYNC_URL_FILE names its URL's file)"
            if "DEADMAN_SYNC_URL" in refusal
            else ""
        )
        problems.append(f"the Plaud sync's check is never pinged: {refusal}{hint}")
    else:
        info["deadman_sync_set"] = bool(ac.deadman_sync_url)
        if not ac.deadman_sync_url:
            problems.append(
                "the Plaud sync has no dead-man URL, and its Healthchecks check is its only "
                "alert: set RECORDINGS_DEADMAN_SYNC_URL_FILE"
            )
    try:
        settings = plaud_settings(cfg)
    except ConfigError as exc:
        problems.append(str(exc))
        return
    if not settings.keep_out_word_ok:
        problems.append(BAD_WORD)
    consent = read_consent(settings.consent_list)
    info["consent_stop"] = consent.stop
    info["review_lines"] = list(consent.review_lines)
    if consent.stop is not None:
        problems.append(f"{consent.stop}: nothing is imported until it reads (stage 2b spec §12.1)")
    else:
        info["consent_ids"] = len(consent.ids)
    if consent.review_lines:
        report["notes"].append(kept_lines_note(consent.review_lines))
    info["token_store"] = settings.token_store.is_file()  # presence only: never opened
    if not info["token_store"]:
        # Phase 1 has no sign-in command yet (`make plaud-sign-in` arrives in Phase 2, which
        # restores spec §17.2's wording): never point at one that doesn't exist.
        problems.append(
            "sign-in needed: the Plaud sign-in comes with stage 2b's next phase; until then "
            "the worker stays idle (runbook section 6)"
        )
    _plaud_modes(settings.token_store, problems)
    info["keep_out_words"] = _enforced_words(cfg, problems)


def _extra_paths(cfg: Config, warnings: list[str]) -> None:
    """In Docker, deploy.env is backed up only if [backup] extra_paths names its mount
    (config.example.toml has the line); the default is no extra path at all."""
    section = cfg.data.get("backup", {})
    paths = section.get("extra_paths", [])
    if section.get("repository") and not any(
        isinstance(p, str) and Path(p).name == "deploy.env" for p in paths
    ):
        warnings.append(
            "deploy.env is not in [backup] extra_paths, so no backup carries it "
            "(config.example.toml has the line)"
        )


def doctor(environ: Mapping[str, str], *, role: str | None = None) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    if role is None:
        role = environ.get("RECORDINGS_ROLE") or "all"
        if role not in ROLES:
            problems.append(f"RECORDINGS_ROLE must be one of {', '.join(ROLES)}: checking all")
            role = "all"
    report: dict[str, Any] = {
        "role": role,
        "config": None,
        "user": None,
        "archive": None,
        "state": None,
        "index": None,
        "backup": None,
        "mirror": None,
        "alerts": None,
        "consent_list": None,
        "plaud": None,
        "notes": [],
        "tools": {tool: shutil.which(tool) is not None for tool in TOOLS},
        "secrets": _secrets(environ, problems, warnings),
        "problems": problems,
        "warnings": warnings,
    }
    if role != "web":
        report["user"] = _user(problems)
    _forbid(environ, FORBIDDEN.get(role, ()), role, problems)
    if role in ("web", "backup"):
        _no_plaud_here(environ, problems)
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return report
    report["config"] = str(cfg.path) if cfg.path else None
    if role in ("web", "backup"):
        _no_plaud_folder_named(cfg, problems)
    _timezone(cfg, problems)
    _disk(cfg, problems)
    archive_uuid = _archive(cfg, role, report, problems)
    _state(cfg, archive_uuid, role, report, problems, warnings)
    if role != "backup":  # the backup service has no index
        _index(cfg, archive_uuid, report, warnings)
    if role in ("backup", "all"):
        _backup(cfg, environ, role, report, problems, warnings)
        _mirror(cfg, environ, role, report, problems, warnings)
        _alerts(cfg, environ, report, problems, warnings)
        _ssh(report, problems)
    if role == "worker":
        _worker(cfg, environ, report, problems, warnings)
    if role == "backup":
        _extra_paths(cfg, warnings)
    _retention(cfg, warnings)
    _consent(cfg, role, report)
    missing = [tool for tool in ("ffprobe", "ffmpeg") if not report["tools"][tool]]
    if role != "backup" and missing:  # web runs the import, which decodes media to measure it
        warnings.append(
            " and ".join(missing) + " not installed, so the import can't measure "
            "media that isn't a plain WAV file, and refuses to import it"
        )
    return report
