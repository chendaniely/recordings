"""The archive documents itself (spec §6.7): docs and schemas come from this package."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from recordings.archive import Archive, write_text_atomic
from recordings.models import Rendition

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
    block = re.search(r"<!-- layout:begin -->\n(.*?)<!-- layout:end -->",
                      _package_text("FORMAT.md"), re.S)
    if block is None:
        raise RuntimeError("FORMAT.md has no layout block")
    patterns = []
    for line in block.group(1).split():
        rx = re.escape(line)
        for token, sub in _TOKENS.items():
            rx = rx.replace(re.escape(token), sub)
        patterns.append(re.compile(rx))
    return patterns


def validate(root: Path) -> list[dict]:
    archive = Archive(Path(root))
    problems = []
    recordings = list(archive.iter_recordings())
    problems += [{"path": str(p.path), "message": p.message} for p in archive.problems]
    for rec in recordings:
        for path in sorted((archive.path_for(rec.id) / "renditions").glob("*.json")):
            try:
                Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError) as exc:
                problems.append({"path": str(path), "message": str(exc).splitlines()[0]})
    return problems
