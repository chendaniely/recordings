"""Reading the archive (spec §6). The archive is the source of truth.

Reading only. Every write goes through `recordings.writer.Writer`, which holds the locks and the
merge bases (§6.4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from recordings.ids import relative_dir
from recordings.models import Recording, Rendition

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def utc_stamp(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class RawSource:
    kind: str
    ref: str
    added_at: datetime
    payload: bytes | None = None  # written verbatim as source/<kind>-<stamp><suffix>
    fetched_at: datetime | None = None  # when the source returned it; names the file
    suffix: str = ".json"  # ".txt" keeps a prose export as it came (spec §9.3)

    def __post_init__(self) -> None:
        ext = self.suffix[1:]
        if not (self.suffix.startswith(".") and ext.isascii() and ext.isalnum()):
            raise ValueError(f"RawSource.suffix {self.suffix!r} must look like .json or .txt")
        for name in ("added_at", "fetched_at"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"RawSource.{name} must be timezone-aware")


@dataclass(frozen=True)
class Problem:
    path: Path
    message: str


@dataclass
class Archive:
    root: Path
    problems: list[Problem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def recording_dirs(self) -> Iterator[Path]:
        base = self.root / "recordings"
        if not base.is_dir():
            return
        for folder in sorted(base.glob("*/*/*")):
            if (folder / "recording.json").is_file():
                yield folder

    def iter_recordings(self) -> Iterator[Recording]:
        """Every readable recording. A broken file is recorded in .problems, not raised (§11).

        So is a recording whose id doesn't match its folder (a typo, or a copied folder):
        listing it would show a recording that load() can't find, or the same one twice.
        """
        self.problems = []
        for folder in self.recording_dirs():
            path = folder / "recording.json"
            try:
                rec = Recording.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                self.problems.append(Problem(path=path, message=str(exc).splitlines()[0]))
                continue
            try:
                expected = relative_dir(rec.id).as_posix()
            except ValueError:  # an id of the right shape with an impossible date
                expected = None
            if expected != folder.relative_to(self.root).as_posix():
                message = f"id {rec.id!r} does not match its folder"
                self.problems.append(Problem(path=path, message=message))
                continue
            yield rec

    def path_for(self, rid: str) -> Path:
        return self.root / relative_dir(rid)

    def load(self, rid: str) -> Recording:
        path = self.path_for(rid) / "recording.json"
        if not path.is_file():
            raise KeyError(rid)
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def media_path(self, rid: str) -> Path:
        return self.path_for(rid) / self.load(rid).media.file

    def renditions(
        self, rid: str, problems: list[Problem] | None = None
    ) -> list[tuple[str, Rendition]]:
        folder = self.path_for(rid)
        out = []
        for path in sorted((folder / "renditions").glob("*.json")):
            try:
                rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
                out.append((path.relative_to(folder).as_posix(), rendition))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                if problems is not None:
                    problems.append(Problem(path=path, message=str(exc).splitlines()[0]))
                else:
                    raise
        return sorted(out, key=lambda item: (item[1].created_at, item[0]))

    def read_my_notes(self, rid: str) -> str | None:
        path = self.path_for(rid) / "my-notes.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None
