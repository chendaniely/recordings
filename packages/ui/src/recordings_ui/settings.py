"""Where the UI reads from. Demo mode never touches real data (spec §17)."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import ConfigError, load_config


@dataclass(frozen=True)
class Settings:
    archive: Path
    demo: bool


def find_demo_archive(environ: Mapping[str, str]) -> Path:
    if environ.get("RECORDINGS_DEMO_ARCHIVE"):
        return Path(environ["RECORDINGS_DEMO_ARCHIVE"])
    # Running from the repo (editable install): walk up to demo/archive.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "demo" / "archive"
        if (candidate / "README.md").is_file():
            return candidate
    raise SystemExit("demo archive not found; set RECORDINGS_DEMO_ARCHIVE")


def from_env(environ: Mapping[str, str], *, demo: bool | None = None) -> Settings:
    if demo is None:
        demo = environ.get("RECORDINGS_DEMO") == "1"
    if demo:
        # A fresh copy every start: the same state for every bug report, and the committed
        # demo is never modified. config.toml, RECORDINGS_ARCHIVE and secrets are never read.
        copy = Path(tempfile.mkdtemp(prefix="recordings-demo-")) / "archive"
        shutil.copytree(find_demo_archive(environ), copy)
        return Settings(archive=copy, demo=True)
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    if cfg.archive_path is None:
        raise SystemExit(
            "no archive path: set [archive] path in config.toml (see config.example.toml), "
            "set RECORDINGS_ARCHIVE, or run with --demo")
    return Settings(archive=cfg.archive_path, demo=False)
