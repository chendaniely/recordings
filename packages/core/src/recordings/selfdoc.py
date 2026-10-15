"""The archive documents itself (spec §6.7): docs and schemas come from this package."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from recordings.archive import Archive, sha256_file
from recordings.files import write_text_atomic
from recordings.models import Rendition
from recordings.sentinel import SENTINEL, SentinelError, read_sentinel

DOC_FILES = {
    "README.md": "README.md",
    "AGENTS.md": "AGENTS.md",
    "FORMAT.md": "FORMAT.md",
    "recordings/README.md": "recordings.README.md",
    "catalog/README.md": "catalog.README.md",
    "schemas/recording.schema.json": "schemas/recording.schema.json",
    "schemas/rendition.schema.json": "schemas/rendition.schema.json",
}

_TOKENS = {
    "<id>": r"<id>",  # the caller substitutes the real ID with this literal first
    "<ext>": r"[a-z0-9]+",
    "<name>": r"[A-Za-z0-9.@+_-]+",
    "<utc-stamp>": r"\d{8}T\d{6}Z(?:-\d+)?",
}


def _package_text(rel: str) -> str:
    return (resources.files("recordings") / "format" / rel).read_text(encoding="utf-8")


def write_docs(root: Path) -> list[str]:
    changed = []
    for dest, src in DOC_FILES.items():
        target = Path(root) / dest
        text = _package_text(src)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, text)
        changed.append(dest)
    return changed


def layout_patterns() -> list[re.Pattern]:
    block = re.search(
        r"<!-- layout:begin -->\n(.*?)<!-- layout:end -->", _package_text("FORMAT.md"), re.DOTALL
    )
    if block is None:
        raise RuntimeError("FORMAT.md has no layout block")
    patterns = []
    for line in block.group(1).split():
        rx = re.escape(line)
        for token, sub in _TOKENS.items():
            rx = rx.replace(re.escape(token), sub)
        patterns.append(re.compile(rx))
    return patterns


def validate(root: Path, *, deep: bool = False) -> list[dict]:
    root = Path(root)
    # Checking the wrong folder must not look like a clean archive.
    if not root.exists():
        return [{"path": str(root), "message": "does not exist"}]
    if not (root / "recordings").is_dir():
        return [
            {
                "path": str(root),
                "message": "is not a recordings archive: it has no recordings/ folder",
            }
        ]
    problems = []
    try:
        read_sentinel(root)
    except SentinelError as exc:
        problems.append({"path": str(root / SENTINEL), "message": str(exc)})
    archive = Archive(root)
    recordings = list(archive.iter_recordings())
    problems += [{"path": str(p.path), "message": p.message} for p in archive.problems]
    for rec in recordings:
        problems += _recording_problems(archive, rec, deep=deep)
    # Every folder's outputs, a broken recording.json's included.
    for folder in archive.recording_dirs():
        for path in sorted((folder / "renditions").glob("*.json")):
            try:
                Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                problems.append({"path": str(path), "message": str(exc).splitlines()[0]})
    return problems


def _recording_problems(archive: Archive, rec, *, deep: bool = False) -> list[dict]:
    folder = archive.path_for(rec.id)
    here = str(folder / "recording.json")
    problems = []
    media = folder / rec.media.file
    try:
        present = media.resolve().is_relative_to(folder.resolve()) and media.is_file()
    except (OSError, ValueError):
        present = False
    if not present:
        problems.append({"path": here, "message": f"media.file {rec.media.file!r} is missing"})
    problems.extend(
        {"path": here, "message": f"source file {source.raw} is missing"}
        for source in rec.sources
        if source.raw and not (folder / source.raw).is_file()
    )
    if rec.media.duration_ms is not None:
        end = rec.media.duration_ms / 1000
        problems.extend(
            {
                "path": here,
                "message": f"speakers span {span.start}-{span.end} s runs past the "
                f"media's end ({end} s)",
            }
            for span in rec.speakers.spans
            if span.end > end
        )
    if deep:  # every byte, as the restore test needs: a file that's there isn't proof enough
        if present and sha256_file(media) != rec.media.sha256:
            problems.append(
                {
                    "path": here,
                    "message": f"media.file {rec.media.file!r} does not match its SHA-256",
                }
            )
        for source in rec.sources:
            path = folder / source.raw if source.raw else None
            if (
                source.sha256
                and path is not None
                and path.is_file()
                and sha256_file(path) != source.sha256
            ):
                problems.append(
                    {
                        "path": here,
                        "message": f"source file {source.raw} does not match its SHA-256",
                    }
                )
    return problems
