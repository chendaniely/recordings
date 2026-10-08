"""Durable file writes (spec §6.4).

Every save writes a temporary file, fsyncs it, renames it into place, then fsyncs the folder.
After a crash the folder holds the old file or the new one, never a torn one, and a write that
returned survives a power cut. Write-once files are published with an exclusive hard link, so
nothing can overwrite them, not even a second writer racing for the same name.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import uuid
from pathlib import Path

_CHUNK = 1 << 20
_PUBLISH_TRIES = 100  # a name taken this often is not a race


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def temp_name(path: Path) -> Path:
    """A hidden sibling, `.name.<hex>.tmp`, which readers, backups and the mirror skip."""
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _write_new_synced(path: Path, data: bytes) -> None:
    with open(path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = temp_name(path)
    try:
        _write_new_synced(tmp, data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def unique_path(path: Path) -> Path:
    # lexists, not exists: a dangling symlink is a taken name, though exists() calls it free.
    if not os.path.lexists(path):
        return path
    n = 2
    while os.path.lexists(candidate := path.with_name(f"{path.stem}-{n}{path.suffix}")):
        n += 1
    return candidate


def create_exclusive(path: Path, data: bytes) -> None:
    """Write `data` at `path`, or raise FileExistsError: never replace what is there."""
    tmp = temp_name(path)
    try:
        _write_new_synced(tmp, data)
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    fsync_dir(path.parent)


def publish_exclusive(data: bytes, target: Path) -> Path:
    """Write-once: publish at `target`, or at `target-2`, `-3` … when the name is taken."""
    for _ in range(_PUBLISH_TRIES):
        path = unique_path(target)
        try:
            create_exclusive(path, data)
        except FileExistsError:
            continue  # another writer took the name between the check and the link
        return path
    raise FileExistsError(f"no free name for {target} after {_PUBLISH_TRIES} tries")


def copy_file_synced(src: Path, dst: Path) -> None:
    """Copy bytes and modification time, then fsync, so the copy survives a power cut."""
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(src, dst)
