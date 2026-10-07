"""Machine settings and secrets (spec §5).

config.toml holds settings and never secrets. It is git-ignored at the repo root;
config.example.toml is the committed template; RECORDINGS_CONFIG points elsewhere (Docker:
/config/config.toml). Secrets come only from the environment: NAME, or NAME_FILE naming a
file (Docker secrets live in /run/secrets/). A secret's value is never printed or logged.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("config.toml")


@dataclass(frozen=True)
class SecretSpec:
    env: str
    purpose: str
    stage: int  # the build stage that first needs it


SECRETS: dict[str, SecretSpec] = {
    "plaud_token": SecretSpec("RECORDINGS_PLAUD_TOKEN", "Plaud API token (Plaud sync)", 2),
    "spark_api_key": SecretSpec(
        "RECORDINGS_SPARK_API_KEY", "this app's llama-swap key on the Spark", 4),
    "claude_oauth_token": SecretSpec(
        "CLAUDE_CODE_OAUTH_TOKEN", "Claude subscription token from `claude setup-token`", 4),
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    path: Path | None  # the file actually read, or None
    archive_path: Path | None
    writer_host: str | None
    default_timezone: str
    base_url: str | None
    data: dict[str, Any] = field(default_factory=dict)  # every section, for later stages


def config_path(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("RECORDINGS_CONFIG") or DEFAULT_PATH)


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
    return Config(
        path=path if path.is_file() else None,
        archive_path=Path(archive_path).expanduser() if archive_path else None,
        writer_host=archive.get("writer_host"),
        default_timezone=archive.get("default_timezone", "America/Vancouver"),
        base_url=data.get("server", {}).get("base_url"),
        data=data,
    )


def secret(name: str, environ: Mapping[str, str]) -> str | None:
    spec = SECRETS[name]
    direct = environ.get(spec.env)
    file_var = environ.get(f"{spec.env}_FILE")
    if direct and file_var:
        raise ConfigError(f"set {spec.env} or {spec.env}_FILE, not both")
    if file_var:
        path = Path(file_var)
        if not path.is_file():
            raise ConfigError(f"{spec.env}_FILE points at {path}, which does not exist")
        return path.read_text(encoding="utf-8").strip() or None
    return direct or None


def doctor(environ: Mapping[str, str]) -> dict[str, Any]:
    """What is configured, as presence only: never a secret's value."""
    report: dict[str, Any] = {"config": None, "archive": None, "secrets": {}, "problems": []}
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        report["problems"].append(str(exc))
        return report
    report["config"] = str(cfg.path) if cfg.path else None
    if cfg.archive_path is None:
        report["problems"].append(
            "no archive path: set [archive] path in config.toml, or RECORDINGS_ARCHIVE")
    else:
        report["archive"] = {"path": str(cfg.archive_path), "exists": cfg.archive_path.is_dir()}
        if not cfg.archive_path.is_dir():
            report["problems"].append(f"archive folder {cfg.archive_path} does not exist")
    for name, spec in SECRETS.items():
        try:
            present = secret(name, environ) is not None
        except ConfigError as exc:
            report["problems"].append(str(exc))
            present = False
        report["secrets"][name] = {
            "env": spec.env, "set": present, "needed_from_stage": spec.stage,
            "purpose": spec.purpose,
        }
    return report
