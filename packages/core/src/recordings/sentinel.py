"""The archive sentinel (spec §6.7): `archive.json` at the archive root.

An archive is never set up implicitly. Without this file, a writer started against an unmounted,
empty folder would make a new archive there, and the mirror's `rsync --delete` would then empty the
NAS copy. So `recordings init` writes it once, with the archive's UUID, and every writer, the
backups and the mirror refuse to run when it is missing or its UUID has changed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recordings.files import create_exclusive
from recordings.models import FORMAT

SENTINEL = "archive.json"


class SentinelError(RuntimeError):
    """The folder is not an initialised archive, or not the one expected."""


@dataclass(frozen=True)
class ArchiveIdentity:
    uuid: str
    created_at: datetime


def read_sentinel(root: Path) -> ArchiveIdentity:
    path = Path(root) / SENTINEL
    if not path.is_file():
        raise SentinelError(
            f"{root} has no {SENTINEL}, so it is not an initialised archive. Is the archive's "
            "disk mounted? A new archive is set up once, with `recordings init`.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        if data.get("format") != FORMAT:
            raise ValueError(f"format is {data.get('format')!r}, not {FORMAT!r}")
        identity = ArchiveIdentity(uuid=str(uuid.UUID(data["uuid"])),
                                   created_at=datetime.fromisoformat(data["created_at"]))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SentinelError(f"{path} is not a valid sentinel: {exc}") from None
    if identity.created_at.utcoffset() is None:
        raise SentinelError(f"{path}: created_at has no UTC offset")
    return identity


def write_sentinel(root: Path, identity: ArchiveIdentity) -> Path:
    path = Path(root) / SENTINEL
    text = json.dumps({"format": FORMAT, "uuid": identity.uuid,
                       "created_at": identity.created_at.isoformat()}, indent=2) + "\n"
    create_exclusive(path, text.encode("utf-8"))
    return path
