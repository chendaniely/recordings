import importlib.util
import json
import shutil
import tomllib
from pathlib import Path

from recordings.archive import Archive
from recordings.models import is_private, is_untagged
from recordings.selfdoc import validate

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "demo"


def load_build():
    spec = importlib.util.spec_from_file_location("demo_build", DEMO / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aliases() -> dict[str, str]:
    return json.loads((DEMO / "canned" / "aliases.json").read_text())


def test_the_committed_demo_archive_is_valid():
    assert validate(DEMO / "archive") == []


def test_the_demo_covers_what_the_ui_needs():
    archive = Archive(DEMO / "archive")
    by_slug = {aliases()[r.id]: r for r in archive.iter_recordings()}
    assert set(by_slug) == {"jfk-rice", "apollo13-problem", "apollo11-first-steps", "fdr-fireside-1"}
    assert is_untagged(by_slug["apollo13-problem"])
    assert is_private(by_slug["fdr-fireside-1"])
    assert by_slug["apollo11-first-steps"].media.kind == "video"
    jfk_notes = [r for _, r in archive.renditions(by_slug["jfk-rice"].id) if r.kind == "notes"]
    assert {r.model for r in jfk_notes} == {"qwen3.6-35b-a3b", "claude-opus-5-5"}
    plaud = [r for _, r in archive.renditions(by_slug["apollo13-problem"].id) if r.engine == "plaud"]
    assert {r.kind for r in plaud} == {"transcript", "notes"}
    assert archive.read_my_notes(by_slug["jfk-rice"].id)


def test_rebuilding_from_the_archives_own_media_is_byte_identical(tmp_path):
    """The build is deterministic: same media + canned → same archive, byte for byte."""
    entries = tomllib.loads((DEMO / "sources.toml").read_text())["recording"]
    ext = {e["slug"]: e["ext"] for e in entries}
    archive = Archive(DEMO / "archive")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    for rid, slug in aliases().items():
        shutil.copy(archive.media_path(rid), media_dir / f"{slug}.{ext[slug]}")
    out = tmp_path / "archive"
    built = load_build().build(media_dir, out, DEMO / "canned")
    assert built == aliases()
    committed = {p.relative_to(DEMO / "archive"): p.read_bytes()
                 for p in (DEMO / "archive").rglob("*") if p.is_file()}
    rebuilt = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert rebuilt.keys() == committed.keys()
    for rel in committed:
        assert rebuilt[rel] == committed[rel], f"{rel} differs: run `make demo-archive`"
