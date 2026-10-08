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


def test_add_recording_lays_out_the_folder(tmp_path):
    archive = Archive(tmp_path / "archive")
    src = media(tmp_path)
    rec = add(archive, src)
    sha = sha256_file(src)
    assert rec.id == f"20261006T140003-0700_{sha[:8]}"
    folder = archive.root / "recordings" / "2026" / "10" / rec.id
    assert (folder / f"{rec.id}.mp3").read_bytes() == src.read_bytes()  # suffix lower-cased
    raw = folder / "source" / f"plaud-{utc_stamp(T0)}.json"
    assert raw.read_bytes() == b'{"id": 1}'
    data = json.loads((folder / "recording.json").read_text(encoding="utf-8"))
    assert data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["sources"][0]["raw"] == f"source/plaud-{utc_stamp(T0)}.json"
    assert not (archive.root / ".tmp").exists()  # assembled in .tmp, moved, cleaned up


def test_same_bytes_again_merge_into_one_recording(tmp_path):
    archive = Archive(tmp_path / "archive")
    first = add(archive, media(tmp_path))
    second = add(
        archive,
        media(tmp_path, name="other.mp3"),
        source=RawSource(kind="upload", ref="other.mp3", added_at=T0),
    )
    assert second.id == first.id
    assert [s.kind for s in archive.load(first.id).sources] == ["plaud", "upload"]
    assert len(list(archive.recording_dirs())) == 1


def test_renditions_are_write_once_and_ordered(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path))
    a = archive.write_rendition(rec.id, notes(T0.replace(minute=5), model="b-model"))
    b = archive.write_rendition(rec.id, notes(T0, model="a-model"))
    c = archive.write_rendition(rec.id, notes(T0, model="a-model"))  # same name → suffix
    assert c != b and c.endswith("-2.json")
    paths = [p for p, _ in archive.renditions(rec.id)]
    assert set(paths[:2]) == {b, c} and paths[2] == a  # ordered by created_at
    assert a.startswith("renditions/notes-lecture-canned-b-model@demo-")


def test_add_recording_writes_given_renditions_and_my_notes(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path), renditions=[notes()], my_notes="hello\n",
              tags=[TagRef(tag="talks")])
    assert len(archive.renditions(rec.id)) == 1
    assert archive.read_my_notes(rec.id) == "hello\n"
    assert archive.load(rec.id).tags[0].tag == "talks"


def test_non_ascii_title_round_trips_as_utf8(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path), title="会議メモ 🎙")
    raw = (archive.path_for(rec.id) / "recording.json").read_bytes()
    assert "会議メモ 🎙".encode() in raw  # stored as UTF-8, not \u escapes
    assert archive.load(rec.id).title == "会議メモ 🎙"
    assert rec.id.isascii()


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


def test_add_recording_cleans_up_on_failure(tmp_path, monkeypatch):
    """Failed assembly must remove .tmp directory and any partial recording folder."""
    from recordings import archive as archive_module

    archive = Archive(tmp_path / "archive")
    src = media(tmp_path)

    def failing_copy(*args, **kwargs):
        raise OSError("Simulated copy failure")

    monkeypatch.setattr(archive_module, "copy_file_synced", failing_copy)

    # Try to add recording with failing copy
    with pytest.raises(OSError, match="Simulated copy failure"):
        add(archive, src)

    # .tmp directory must not exist
    tmp_dir = archive.root / ".tmp"
    assert not tmp_dir.exists()

    # No recording folder should be created
    recordings_dir = archive.root / "recordings"
    if recordings_dir.exists():
        assert len(list(recordings_dir.glob("*/*/*"))) == 0


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


def test_a_raw_source_records_its_hash_and_fetch_time(tmp_path):
    archive = Archive(tmp_path / "archive")
    fetched = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    rec = add(archive, media(tmp_path), source=RawSource(
        kind="plaud", ref="of_" + "a" * 32, added_at=T0, fetched_at=fetched, payload=b'{"id": 1}'))
    (ref,) = rec.sources
    assert ref.raw == "source/plaud-20260801T090000Z.json"  # named by when it was fetched
    assert ref.fetched_at == fetched
    assert ref.sha256 == hashlib.sha256(b'{"id": 1}').hexdigest()
