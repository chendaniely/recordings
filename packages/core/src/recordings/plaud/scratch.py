"""The Plaud import's scratch folder, the archive's `.tmp/plaud-dl/` (stage 2b spec §9.2, §11.5
step 6): where a recording's audio is streamed before `Writer.add` copies it in.

- **Scratch, not archive data** (CLAUDE.md): a download enters the archive only through
  `Writer.add`, which copies and hashes it again. `.tmp/` is left out of every backup and the
  mirror (`backup.py`, `mirror.py`), so nothing half-downloaded is ever kept.
- **On the archive's disk, never `/tmp`,** which is a 1 GiB RAM tmpfs in the container: a long
  recording would fill it. Never in memory either: the client streams it.
- **Made with one retry** (`download_dir`): another process's `Writer._drop_empty_tmp` can remove
  an empty `.tmp/` between the two `mkdir`s.
- **Swept at the worker's start** (`sweep_downloads`, §9.2 step 2): a download in flight when the
  worker was stopped is abandoned. Only under a non-blocking try of the `plaud` lock: a CLI run
  holding it owns what is there.

Network-free (no client, no token store), so the worker imports it at module level.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from recordings.locks import PLAUD_LOCK, Locks, LockTimeout
from recordings.state import State

DOWNLOADS = Path(".tmp") / "plaud-dl"


def download_dir(archive_root: Path) -> Path:
    """`<archive>/.tmp/plaud-dl/`, made if missing; `mkdir` is tried once more when the `.tmp/`
    it made was removed under it (§11.5 step 6)."""
    folder = Path(archive_root) / DOWNLOADS
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except FileNotFoundError:  # .tmp/ went between the two levels: once more
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def drop_download_dir(archive_root: Path) -> None:
    """Remove `.tmp/plaud-dl/` and `.tmp/` when they are empty, as `Writer._drop_empty_tmp` does
    for its own assemblies; anything still in them stays."""
    root = Path(archive_root)
    for folder in (root / DOWNLOADS, root / ".tmp"):
        with contextlib.suppress(OSError):
            folder.rmdir()


def sweep_downloads(archive_root: Path, state: State) -> int | None:
    """The worker's start (§9.2 step 2): remove what a stopped download left in `.tmp/plaud-dl/`.
    Under a non-blocking try of `plaud`: None when another run holds it (it owns the files), else
    how many entries went."""
    folder = Path(archive_root) / DOWNLOADS
    try:
        with Locks(state.locks_dir, timeout=0).hold(PLAUD_LOCK):
            if not folder.is_dir():
                return 0
            removed = 0
            for entry in sorted(folder.iterdir()):
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        entry.unlink()
                removed += 1
            drop_download_dir(archive_root)
            return removed
    except LockTimeout:
        return None
