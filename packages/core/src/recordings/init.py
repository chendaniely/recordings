"""`recordings init`: set up a new archive, explicitly and once (spec §6.7).

It refuses any folder that isn't empty, and any state folder that already has a state.db, so it can
never adopt or overwrite something by accident. The sentinel is written last: an interrupted init
leaves no archive.json, and every writer refuses to run against it.

`recordings init --adopt` is the way back when state.db is lost and no backup copy is usable
(§6.8). It recreates state.db for an existing archive from the archive's own sentinel, and writes
nothing into the archive. The merge bases went with the old state.db, so the app adopts each
recording.json as it stands the next time it writes it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recordings.selfdoc import write_docs
from recordings.sentinel import (
    MIRROR,
    SENTINEL,
    ArchiveIdentity,
    SentinelError,
    is_mirror,
    read_sentinel,
    write_sentinel,
)
from recordings.state import State, StateError

WRITER_ID_PATTERN = r"[a-z0-9][a-z0-9_.-]{0,62}"


class InitError(RuntimeError):
    pass


def _check_writer_id(writer_id: str) -> None:
    if not re.fullmatch(WRITER_ID_PATTERN, writer_id or ""):
        raise InitError(f"writer_id {writer_id!r} must be a short lowercase name, such as homelab")


def init_archive(
    archive_root: Path,
    state_path: Path,
    *,
    writer_id: str,
    archive_uuid: str | None = None,
    created_at: datetime | None = None,
) -> ArchiveIdentity:
    root, state_dir = Path(archive_root), Path(state_path)
    _check_writer_id(writer_id)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} is not a folder")
    if root.is_dir() and any(root.iterdir()):
        raise InitError(f"{root} is not empty: init only sets up a new, empty archive")
    if (state_dir / "state.db").exists():
        raise InitError(f"{state_dir / 'state.db'} already exists: init runs once per archive")
    identity = ArchiveIdentity(
        uuid=str(uuid.UUID(archive_uuid)) if archive_uuid else str(uuid.uuid4()),
        created_at=created_at or datetime.now(timezone.utc),
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "recordings").mkdir()
        write_docs(root)
        State.create(state_dir, archive_uuid=identity.uuid, writer_id=writer_id)
        write_sentinel(root, identity)
    except (OSError, StateError) as exc:
        # Never "remove": a wrong path in that advice could cost a real archive.
        raise InitError(
            f"init stopped part-way ({exc}) and wrote no {SENTINEL}. Check that {root} and "
            f"{state_dir / 'state.db'} hold only what this init just wrote, then move both "
            "aside and run init again."
        ) from None
    return identity


def adopt_archive(archive_root: Path, state_path: Path, *, writer_id: str) -> ArchiveIdentity:
    """Recreate a lost state.db for an existing archive, from its sentinel (§6.8)."""
    root, state_dir = Path(archive_root), Path(state_path)
    _check_writer_id(writer_id)
    try:
        identity = read_sentinel(root)
    except SentinelError as exc:
        raise InitError(str(exc)) from None
    if is_mirror(root):
        raise InitError(
            f"{root} holds {MIRROR}: it is the NAS mirror's copy, not the archive. Only the "
            "archive's writer writes (spec §3), so --adopt runs there; anywhere else, use the "
            "app."
        )
    if (state_dir / "state.db").exists():
        raise InitError(
            f"{state_dir / 'state.db'} exists: --adopt only replaces a lost one. If it is damaged "
            "and no backup copy is usable, stop the app and move the whole state folder aside "
            "first."
        )
    try:
        State.create(state_dir, archive_uuid=identity.uuid, writer_id=writer_id)
    except (OSError, StateError) as exc:
        raise InitError(f"--adopt stopped ({exc}); it changed nothing in {root}") from None
    return identity
