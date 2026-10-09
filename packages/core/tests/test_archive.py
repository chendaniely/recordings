import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings.archive import Archive, RawSource, sha256_file, utc_stamp
from recordings.models import Rendition, TagRef

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LOCAL = datetime.fromisoformat("2026-10-06T14:00:03-07:00")


def media(tmp_path: Path, content: bytes = b"ID3 fake audio", name: str = "clip.MP3") -> Path:
    p = tmp_path / "in" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def add(archive: Archive, path: Path, **over):
    kwargs = dict(
        media=path,
        recorded_at=LOCAL,
        timezone_name="America/Vancouver",
        time_source="plaud",
        title="Week 4",
        kind="audio",
        source=RawSource(kind="plaud", ref="of_" + "a" * 32, added_at=T0, payload=b'{"id": 1}'),
    )
    kwargs.update(over)
    return archive.add_recording(**kwargs)


def notes(created_at=T0, model="qwen3.6-35b-a3b") -> Rendition:
    return Rendition(
        kind="notes",
        note_type="lecture",
        engine="canned",
        model=model,
        version=f"{model}@demo",
        created_at=created_at,
        payload={"markdown": "# Notes"},
    )


def test_a_broken_recording_json_is_reported_not_fatal(tmp_path):
    archive = Archive(tmp_path / "archive")
    good = add(archive, media(tmp_path))
    bad = add(archive, media(tmp_path, b"other bytes", name="b.mp3"),
              recorded_at=datetime.fromisoformat("2026-10-07T09:00:00-07:00"))
    (archive.path_for(bad.id) / "recording.json").write_text("{ truncated", encoding="utf-8")
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    assert len(archive.problems) == 1
    assert archive.problems[0].path.name == "recording.json"
    assert bad.id in str(archive.problems[0].path)


def test_load_unknown_id_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        Archive(tmp_path).load("20261006T140003-0700_00000000")


def test_media_path_points_at_the_media_file(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path))
    assert archive.media_path(rec.id).name == f"{rec.id}.mp3"


def test_renditions_skips_corrupt_files_when_problems_given(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path), renditions=[notes()])
    folder = archive.path_for(rec.id)
    
    # Add a corrupt rendition file
    corrupt_path = folder / "renditions" / "corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")
    
    # Collect problems
    problems = []
    renditions = archive.renditions(rec.id, problems)
    
    # Should have one good rendition and one problem
    assert len(renditions) == 1
    assert len(problems) == 1
    assert problems[0].path == corrupt_path
    assert len(problems[0].message) > 0


def _set_id(folder: Path, rid: str) -> None:
    path = folder / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = rid
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("wrong", [
    lambda rid: rid[:-1] + ("1" if rid[-1] == "0" else "0"),  # a typo in the hash
    lambda rid: rid.replace("T140003", "T140004"),  # a typo in the time
    lambda rid: "20261399T256199+0000_deadbeef",  # the right shape, but no such date
], ids=["hash", "time", "impossible-date"])
def test_a_recording_whose_id_does_not_match_its_folder_is_reported(tmp_path, wrong):
    archive = Archive(tmp_path / "archive")
    good = add(archive, media(tmp_path))
    bad = add(archive, media(tmp_path, b"other bytes", name="b.mp3"),
              recorded_at=datetime.fromisoformat("2026-10-07T14:00:03-07:00"))
    typo = wrong(bad.id)
    _set_id(archive.path_for(bad.id), typo)
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    (problem,) = archive.problems
    assert problem.path == archive.path_for(bad.id) / "recording.json"
    assert problem.message == f"id {typo!r} does not match its folder"


def test_a_copied_recording_folder_is_reported_and_the_original_kept(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path))
    original = archive.path_for(rec.id)
    copy = original.with_name(f"{rec.id} copy")  # what Finder calls a duplicate
    shutil.copytree(original, copy)
    assert [r.id for r in archive.iter_recordings()] == [rec.id]
    (problem,) = archive.problems
    assert problem.path == copy / "recording.json"
    assert problem.message == f"id {rec.id!r} does not match its folder"


def test_a_raw_source_needs_aware_times():
    # why: stage-1 carry-over. A naive time would be read as UTC somewhere and as local elsewhere.
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=datetime(2026, 10, 8, 12, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=T0, fetched_at=datetime(2026, 8, 1))


