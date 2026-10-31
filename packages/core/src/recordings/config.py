"""Machine settings and secrets (spec §5).

config.toml holds settings and never secrets. It is git-ignored at the repo root;
config.example.toml is the committed template; RECORDINGS_CONFIG points elsewhere (Docker:
/config/config.toml). Secrets come only from the environment: NAME, or NAME_FILE naming a
file (Docker secrets live in /run/secrets/). A secret's value is never printed or logged.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("config.toml")
WRITER_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")


@dataclass(frozen=True)
class SecretSpec:
    env: str
    purpose: str
    stage: int  # the build stage that first needs it
    file_only: bool = False  # only NAME_FILE makes sense: the program needs a path (an SSH key)
    optional: bool = False  # needed only for some setups (rest-server's password)


SECRETS: dict[str, SecretSpec] = {
    "spark_api_key": SecretSpec(
        "RECORDINGS_SPARK_API_KEY", "this app's llama-swap key on the Spark", 4
    ),
    "claude_oauth_token": SecretSpec(
        "CLAUDE_CODE_OAUTH_TOKEN", "Claude subscription token from `claude setup-token`", 4
    ),
    "restic_password": SecretSpec(
        "RESTIC_PASSWORD",
        "the restic repository's password; keep a copy in your password manager",
        2,
    ),
    "rest_password": SecretSpec(
        "RESTIC_REST_PASSWORD", "rest-server's password, when backups go there", 2, optional=True
    ),
    "nas_ssh_key": SecretSpec(
        "RECORDINGS_NAS_SSH_KEY", "the NAS account's SSH private key, as a file", 2, file_only=True
    ),
    "nas_known_hosts": SecretSpec(
        "RECORDINGS_NAS_KNOWN_HOSTS",
        "the NAS's SSH host key, as a known_hosts file",
        2,
        file_only=True,
    ),
    "ntfy_topic": SecretSpec(
        "RECORDINGS_NTFY_TOPIC", "the private ntfy topic that alerts go to", 2
    ),
    "deadman_url": SecretSpec(
        "RECORDINGS_DEADMAN_URL", "the Healthchecks.io ping URL (it carries its own token)", 2
    ),
    # Stage 2b: the Plaud sync's own check, apart from the backup's (Dan's decision 4), worker only.
    # The Plaud sign-in is a file the worker owns (`[plaud] token_store`), never a secret here.
    "deadman_sync_url": SecretSpec(
        "RECORDINGS_DEADMAN_SYNC_URL", "the Plaud sync's own Healthchecks ping URL", 2
    ),
    "ntfy_token": SecretSpec(
        "RECORDINGS_NTFY_TOKEN",
        "an ntfy access token, only for a server that needs one",
        2,
        optional=True,
    ),
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    path: Path | None  # the file actually read, or None
    archive_path: Path | None
    writer_id: str | None  # the archive's one writer (§3); `recordings init` records it
    default_timezone: str
    base_url: str | None
    data: dict[str, Any] = field(default_factory=dict)  # every section, for later stages
    # Names the app answers to besides localhost and base_url's host (recordings_ui.hosts).
    allowed_hosts: tuple[str, ...] = ()
    state_path: Path | None = None  # [state] path: state.db and the locks (§6.8)
    index_path: Path | None = None  # [index] path: the derived index.db


def config_path(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("RECORDINGS_CONFIG") or DEFAULT_PATH)


_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")


def normalise_host(value: str) -> str:
    """A bare host name or address as a browser sends it in `Host`: lowercased, no scheme, path
    or port; IP literals in canonical form (`fd7a:0:0::1` becomes `fd7a::1`); a name is dot-
    separated labels of letters, digits and hyphens. Entries like `my-homelab:8000` used to pass
    validation and then never match (stage-1 carry-over)."""
    host = value.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if getattr(address, "scope_id", None) or getattr(address, "ipv4_mapped", None):
            raise ValueError(
                f"{value!r} is an address no browser sends in `Host`: write it as "
                "a browser shows it (no zone ID, and an IPv4 address as 4 numbers)"
            )
        return str(address)
    if all(label.isdigit() or re.fullmatch(r"0x[0-9a-f]*", label) for label in host.split(".")):
        raise ValueError(
            f"{value!r} is a number, not a host name, and no browser sends it as "
            "written (127.1 and 0x7f.1 become 127.0.0.1): write the address in full"
        )
    if "://" in host or "/" in host:
        raise ValueError(f"{value!r} is not a host name: give the name or address alone")
    if ":" in host:
        raise ValueError(f"{value!r} has a port: give the host name alone")
    if not all(_LABEL.fullmatch(label) for label in host.split(".")):
        raise ValueError(f"{value!r} is not a host name: use letters, digits, hyphens and dots")
    return host


def _allowed_hosts(server: dict[str, Any]) -> tuple[str, ...]:
    value = server.get("allowed_hosts", [])
    if not isinstance(value, list) or not all(isinstance(h, str) for h in value):
        raise ConfigError(
            "[server] allowed_hosts must be a list of host names, "
            'for example ["my-homelab", "100.64.0.1"]'
        )
    try:
        return tuple(normalise_host(h) for h in value)
    except ValueError as exc:
        raise ConfigError(f"[server] allowed_hosts: {exc}") from None


def _writer_id(archive: dict[str, Any]) -> str | None:
    if "writer_host" in archive:
        raise ConfigError(
            "[archive] writer_host was renamed writer_id in stage 2a. It names the archive's "
            'one writer and is not a host name: set writer_id = "homelab" (any short name).'
        )
    value = archive.get("writer_id")
    if value is None:
        return None
    if not isinstance(value, str) or not WRITER_ID_RE.fullmatch(value):
        raise ConfigError(
            '[archive] writer_id must be a short lowercase name such as "homelab": letters, '
            "digits, '.', '_' or '-'"
        )
    return value


def _base_url(server: dict[str, Any]) -> str | None:
    value = server.get("base_url")
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise ConfigError('[server] base_url must be a URL, such as "http://my-homelab:8000"')
    return value


def _path(environ: Mapping[str, str], env: str, name: str, data: dict[str, Any]) -> Path | None:
    value = environ.get(env) or data.get(name, {}).get("path")
    if not value:
        return None
    path = Path(value).expanduser() if isinstance(value, str) else None
    if path is None or not path.is_absolute():
        # A relative one follows the folder a command runs in, and a second state.db, with its own
        # locks, would be a second writer (spec §3).
        raise ConfigError(f"[{name}] path (or {env}) must be an absolute path, in quotes")
    return path


def _index_apart(index: Path | None, state: Path | None) -> None:
    """The index never lies inside the state folder (spec §6.8). state.db, its backup copy and
    voice.db live there, and their writers don't take the index lock: a typo that points the index
    there is refused before rebuild opens any of them."""
    if index is not None and state is not None and index.resolve().is_relative_to(state.resolve()):
        raise ConfigError(
            f"[index] path (or RECORDINGS_INDEX) {index} lies inside [state] path {state}, beside "
            "state.db. Give the index a folder of its own, such as /srv/recordings/index/index.db."
        )


def load_config(environ: Mapping[str, str]) -> Config:
    path = config_path(environ)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    elif environ.get("RECORDINGS_CONFIG"):
        raise ConfigError(f"RECORDINGS_CONFIG points at {path}, which does not exist")
    archive = data.get("archive", {})
    # The environment wins, so one config.toml works on the host and inside Docker.
    archive_path = environ.get("RECORDINGS_ARCHIVE") or archive.get("path")
    server = data.get("server", {})
    state_path = _path(environ, "RECORDINGS_STATE", "state", data)
    index_path = _path(environ, "RECORDINGS_INDEX", "index", data)
    _index_apart(index_path, state_path)
    return Config(
        path=path if path.is_file() else None,
        archive_path=Path(archive_path).expanduser() if archive_path else None,
        writer_id=_writer_id(archive),
        default_timezone=archive.get("default_timezone", "America/Vancouver"),
        base_url=_base_url(server),
        data=data,
        allowed_hosts=_allowed_hosts(server),
        state_path=state_path,
        index_path=index_path,
    )


def _no_such_file(env: str) -> ConfigError:
    """NAME_FILE names nothing that exists. Its value is never repeated: it may be the secret
    itself, put where the path belongs, and doctor's output is pasted into issues and chats."""
    return ConfigError(
        f"{env}_FILE names a file that does not exist. Its value is not shown: give "
        "the path to the secret's file there, never the secret itself"
    )


def secret_path(name: str, environ: Mapping[str, str]) -> Path | None:
    """The file a NAME_FILE secret names, checked to exist and be readable, never read here."""
    spec = SECRETS[name]
    if spec.file_only and environ.get(spec.env):
        raise ConfigError(f"give {spec.env}_FILE, the path to the file, not the value itself")
    value = environ.get(f"{spec.env}_FILE")
    if not value:
        return None
    path = Path(value)
    if not path.exists():
        raise _no_such_file(spec.env)
    if not path.is_file():
        raise ConfigError(f"{spec.env}_FILE points at {path}, which is not a file")
    try:
        with path.open("rb") as fh:
            fh.read(1)
    except OSError:
        raise ConfigError(f"{spec.env}_FILE at {path} could not be read") from None
    return path


def secret(name: str, environ: Mapping[str, str]) -> str | None:
    if SECRETS[name].file_only:
        return str(path) if (path := secret_path(name, environ)) else None
    spec = SECRETS[name]
    direct = environ.get(spec.env)
    file_var = environ.get(f"{spec.env}_FILE")
    if direct and file_var:
        raise ConfigError(f"set {spec.env} or {spec.env}_FILE, not both")
    if file_var:
        path = Path(file_var)
        if not path.exists():
            raise _no_such_file(spec.env)
        if not path.is_file():
            raise ConfigError(f"{spec.env}_FILE points at {path}, which is not a file")
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            raise ConfigError(f"{spec.env}_FILE at {path} could not be read") from None
        if not content:
            raise ConfigError(f"{spec.env}_FILE at {path} is empty")
        return content
    return direct or None
