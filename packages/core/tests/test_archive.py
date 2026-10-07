import json
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
