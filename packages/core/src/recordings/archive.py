"""Reading and writing the archive (spec §6). The archive is the source of truth.

Write rules: media, source/ and renditions/ are write-once; recording.json and
my-notes.md are replaced atomically; a new recording is assembled under .tmp/ and
renamed into place, so a reader never sees half a recording.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from recordings.ids import make_id, relative_dir
from recordings.models import (
    SCHEMA_REF,
    MediaInfo,
    Recording,
    Rendition,
    SourceRef,
    TagRef,
    TimeSource,
    dump_json,
)

_CHUNK = 1 << 20
_UNSAFE = re.compile(r"[^A-Za-z0-9.@+_-]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def utc_stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value).strip("_") or "x"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem}-{n}{path.suffix}")).exists():
        n += 1
    return candidate


@dataclass(frozen=True)
class RawSource:
    kind: str
    ref: str
    added_at: datetime
    payload: bytes | None = None  # written verbatim as source/<kind>-<stamp>.json


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

    # ---- reading -------------------------------------------------------------------
    def recording_dirs(self) -> Iterator[Path]:
        base = self.root / "recordings"
        if not base.is_dir():
            return
        for folder in sorted(base.glob("*/*/*")):
            if (folder / "recording.json").is_file():
                yield folder

    def iter_recordings(self) -> Iterator[Recording]:
        """Every readable recording. A broken file is recorded in .problems, not raised (§11)."""
        self.problems = []
        for folder in self.recording_dirs():
            path = folder / "recording.json"
            try:
                yield Recording.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                self.problems.append(Problem(path=path, message=str(exc).splitlines()[0]))

    def path_for(self, rid: str) -> Path:
        return self.root / relative_dir(rid)

    def load(self, rid: str) -> Recording:
        path = self.path_for(rid) / "recording.json"
        if not path.is_file():
            raise KeyError(rid)
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def media_path(self, rid: str) -> Path:
        return self.path_for(rid) / self.load(rid).media.file

    def renditions(self, rid: str) -> list[tuple[str, Rendition]]:
        folder = self.path_for(rid)
        out = []
        for path in sorted((folder / "renditions").glob("*.json")):
            rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            out.append((path.relative_to(folder).as_posix(), rendition))
        return sorted(out, key=lambda item: (item[1].created_at, item[0]))

    def read_my_notes(self, rid: str) -> str | None:
        path = self.path_for(rid) / "my-notes.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def find_by_sha256(self, sha: str) -> Recording | None:
        return next((r for r in self.iter_recordings() if r.media.sha256 == sha), None)

    # ---- writing -------------------------------------------------------------------
    def add_recording(
        self,
        *,
        media: Path,
        recorded_at: datetime,
        timezone_name: str,
        time_source: TimeSource,
        title: str,
        kind: Literal["audio", "video"],
        source: RawSource,
        duration_ms: int | None = None,
        tags: Sequence[TagRef] = (),
        renditions: Sequence[Rendition] = (),
        my_notes: str | None = None,
    ) -> Recording:
        sha = sha256_file(media)
        existing = self.find_by_sha256(sha)
        if existing is not None:  # same bytes again: one recording, one more source (§6.4)
            return self._merge_source(existing, source)

        rid = make_id(recorded_at, sha)
        final = self.path_for(rid)
        if final.exists():
            raise FileExistsError(f"{final} exists but holds different media")
        tmp = self.root / ".tmp" / uuid.uuid4().hex
        (tmp / "source").mkdir(parents=True)
        (tmp / "renditions").mkdir()
        media_name = f"{rid}{media.suffix.lower()}"
        shutil.copy2(media, tmp / media_name)
        rec = Recording(
            schema_ref=SCHEMA_REF,
            id=rid,
            title=title,
            recorded_at=recorded_at,
            timezone=timezone_name,
            time_source=time_source,
            media=MediaInfo(file=media_name, sha256=sha, kind=kind, duration_ms=duration_ms),
            sources=[self._write_raw(tmp, source)],
            tags=list(tags),
        )
        for rendition in renditions:
            self._write_rendition_into(tmp, rendition)
        if my_notes is not None:
            write_text_atomic(tmp / "my-notes.md", my_notes)
        write_text_atomic(tmp / "recording.json", dump_json(rec))
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, final)
        self._drop_empty_tmp()
        return rec

    def write_rendition(self, rid: str, rendition: Rendition) -> str:
        folder = self.path_for(rid)
        if not (folder / "recording.json").is_file():
            raise KeyError(rid)
        return self._write_rendition_into(folder, rendition)

    # ---- helpers -------------------------------------------------------------------
    def _write_raw(self, folder: Path, source: RawSource) -> SourceRef:
        raw = None
        if source.payload is not None:
            path = _unique(folder / "source" / f"{_slug(source.kind)}-{utc_stamp(source.added_at)}.json")
            path.write_bytes(source.payload)
            raw = path.relative_to(folder).as_posix()
        return SourceRef(kind=source.kind, ref=source.ref, added_at=source.added_at, raw=raw)

    def _write_rendition_into(self, folder: Path, rendition: Rendition) -> str:
        kind = f"notes-{rendition.note_type}" if rendition.kind == "notes" else rendition.kind
        name = "-".join(
            [_slug(kind), _slug(rendition.engine), _slug(rendition.version), utc_stamp(rendition.created_at)]
        )
        path = _unique(folder / "renditions" / f"{name}.json")
        path.parent.mkdir(exist_ok=True)
        # write-once: a fresh temp file, then a rename onto a name nobody holds yet
        write_text_atomic(path, dump_json(rendition))
        return path.relative_to(folder).as_posix()

    def _merge_source(self, rec: Recording, source: RawSource) -> Recording:
        if any(s.kind == source.kind and s.ref == source.ref for s in rec.sources):
            return rec
        folder = self.path_for(rec.id)
        updated = rec.model_copy(update={"sources": [*rec.sources, self._write_raw(folder, source)]})
        write_text_atomic(folder / "recording.json", dump_json(updated))
        return updated

    def _drop_empty_tmp(self) -> None:
        try:
            (self.root / ".tmp").rmdir()
        except OSError:
            pass  # not empty (another assembly in flight) or already gone
