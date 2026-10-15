import json
import shutil
from datetime import UTC, datetime

import pytest
from recordings.archive import Archive, RawSource
from recordings.models import Rendition

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_a_broken_recording_json_is_reported_not_fatal(writer, make_incoming):
    good = writer.add(make_incoming()).recording
    bad = writer.add(
        make_incoming(recorded_at=datetime.fromisoformat("2026-10-07T09:00:00-07:00"))
    ).recording
    (writer.archive.path_for(bad.id) / "recording.json").write_text("{ truncated", encoding="utf-8")
    archive = Archive(writer.root)
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    (problem,) = archive.problems
    assert problem.path.name == "recording.json" and bad.id in str(problem.path)


def test_load_unknown_id_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        Archive(tmp_path).load("20261006T140003-0700_00000000")


def test_media_path_points_at_the_media_file(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    assert Archive(writer.root).media_path(rec.id).name == f"{rec.id}.mp3"


def test_renditions_skips_corrupt_files_when_problems_given(writer, make_incoming):
    rec = writer.add(
        make_incoming(
            renditions=(
                Rendition(
                    kind="notes",
                    note_type="lecture",
                    engine="canned",
                    model="m",
                    version="m@1",
                    created_at=T0,
                    payload={"markdown": "# Notes"},
                ),
            )
        )
    ).recording
    corrupt = writer.archive.path_for(rec.id) / "renditions" / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    problems = []
    assert len(Archive(writer.root).renditions(rec.id, problems)) == 1
    (problem,) = problems
    assert problem.path == corrupt and problem.message


def _set_id(folder, rid: str) -> None:
    path = folder / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = rid
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "wrong",
    [
        lambda rid: rid[:-1] + ("1" if rid[-1] == "0" else "0"),  # a typo in the hash
        lambda rid: rid.replace("T140003", "T140004"),  # a typo in the time
        lambda rid: "20261399T256199+0000_deadbeef",  # the right shape, but no such date
    ],
    ids=["hash", "time", "impossible-date"],
)
def test_a_recording_whose_id_does_not_match_its_folder_is_reported(writer, make_incoming, wrong):
    good = writer.add(make_incoming()).recording
    bad = writer.add(
        make_incoming(recorded_at=datetime.fromisoformat("2026-10-07T14:00:03-07:00"))
    ).recording
    typo = wrong(bad.id)
    _set_id(writer.archive.path_for(bad.id), typo)
    archive = Archive(writer.root)
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    (problem,) = archive.problems
    assert problem.path == archive.path_for(bad.id) / "recording.json"
    assert problem.message == f"id {typo!r} does not match its folder"


def test_a_copied_recording_folder_is_reported_and_the_original_kept(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    original = writer.archive.path_for(rec.id)
    copy = original.with_name(f"{rec.id} copy")  # what Finder calls a duplicate
    shutil.copytree(original, copy)
    archive = Archive(writer.root)
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
