"""JSON Schemas for the archive files, generated from the models so they cannot drift."""

from __future__ import annotations

import json
from pathlib import Path

from recordings.models import Recording, Rendition


def generate() -> dict[str, dict]:
    return {
        "recording.schema.json": Recording.model_json_schema(by_alias=True),
        "rendition.schema.json": Rendition.model_json_schema(by_alias=True),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def package_dir() -> Path:
    return Path(__file__).resolve().parent / "format" / "schemas"


def write(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in generate().items():
        path = directory / name
        path.write_text(render(schema), encoding="utf-8")
        written.append(path)
    return written


def stale(directory: Path) -> list[str]:
    out = []
    for name, schema in generate().items():
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8") != render(schema):
            out.append(name)
    return out
