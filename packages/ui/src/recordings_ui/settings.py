"""Where the UI reads from. Demo mode never touches real data (spec §17)."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from recordings.config import Config, ConfigError, load_config, normalise_host
from recordings.sentinel import SentinelError, read_sentinel
from recordings_ui.hosts import LOCAL_HOSTS


@dataclass(frozen=True)
class Settings:
    archive: Path
    demo: bool
    # The names the app answers to (recordings_ui.hosts.HostGuard); always the local ones.
    allowed_hosts: frozenset[str] = LOCAL_HOSTS
    config: Config | None = None  # None in demo mode, which never reads config.toml


def env_hosts(environ: Mapping[str, str]) -> frozenset[str]:
    """RECORDINGS_ALLOWED_HOSTS: extra names, comma-separated, checked like [server] allowed_hosts."""
    names = [n for n in environ.get("RECORDINGS_ALLOWED_HOSTS", "").split(",") if n.strip()]
    try:
        return frozenset(normalise_host(n) for n in names)
    except ValueError as exc:
        raise SystemExit(f"RECORDINGS_ALLOWED_HOSTS: {exc}") from None


# demo/build.py's DEMO_UUID. A real archive's archive.json carries its own, so demo mode serves
# the demo or nothing, whatever RECORDINGS_DEMO_ARCHIVE names (spec §17).
DEMO_UUID = "5f0c8e2a-3b1d-4c6e-9a7f-1d2e3f405162"


def find_demo_archive(environ: Mapping[str, str]) -> Path:
    if environ.get("RECORDINGS_DEMO_ARCHIVE"):
        return _only_the_demo(Path(environ["RECORDINGS_DEMO_ARCHIVE"]))
    # Running from the repo (editable install): walk up to demo/archive.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "demo" / "archive"
        if (candidate / "README.md").is_file():
            return _only_the_demo(candidate)
    raise SystemExit("demo archive not found; set RECORDINGS_DEMO_ARCHIVE")


def _only_the_demo(path: Path) -> Path:
    try:
        ok = read_sentinel(path).uuid == DEMO_UUID
    except SentinelError:
        ok = False
    if not ok:
        raise SystemExit(f"{path} is not the demo archive (its archive.json lacks the demo's "
                         "UUID); demo mode serves nothing else")
    return path


def from_env(environ: Mapping[str, str], *, demo: bool | None = None) -> Settings:
    if demo is None:
        demo = environ.get("RECORDINGS_DEMO") == "1"
    if demo:
        # A fresh copy every start: the same state for every bug report, and the committed
        # demo is never modified. config.toml, RECORDINGS_ARCHIVE and secrets are never read.
        copy = Path(tempfile.mkdtemp(prefix="recordings-demo-")) / "archive"
        atexit.register(shutil.rmtree, copy.parent, ignore_errors=True)  # stage-1 carry-over
        shutil.copytree(find_demo_archive(environ), copy)
        return Settings(archive=copy, demo=True, allowed_hosts=LOCAL_HOSTS | env_hosts(environ))
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    if cfg.archive_path is None:
        raise SystemExit(
            "no archive path: set [archive] path in config.toml (see config.example.toml), "
            "set RECORDINGS_ARCHIVE, or run with --demo")
    hosts = LOCAL_HOSTS | env_hosts(environ) | set(cfg.allowed_hosts)  # already normalised
    if cfg.base_url and (name := urlsplit(cfg.base_url).hostname):
        try:  # the same rules as allowed_hosts, so the canonical form a browser sends matches
            hosts |= {normalise_host(name)}
        except ValueError as exc:
            raise SystemExit(f"[server] base_url: {exc}") from None
    return Settings(archive=cfg.archive_path, demo=False, allowed_hosts=hosts, config=cfg)
