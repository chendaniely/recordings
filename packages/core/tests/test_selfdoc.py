from datetime import datetime, timezone

from recordings.archive import Archive, RawSource
from recordings.models import Rendition
from recordings.selfdoc import layout_patterns, validate, write_docs

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def build_one(tmp_path) -> Archive:
    media = tmp_path / "clip.mp3"
    media.write_bytes(b"ID3 fake")
    archive = Archive(tmp_path / "archive")
    rec = archive.add_recording(
        media=media,
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone_name="America/Vancouver",
        time_source="plaud",
        title="Week 4",
        kind="audio",
        source=RawSource(kind="plaud", ref="of_x", added_at=T0, payload=b"{}"),
        my_notes="mine\n",
        renditions=[
            Rendition(kind="transcript", engine="canned", model="whisper-large-v3-turbo",
                      version="large-v3-turbo@a4aaeec", created_at=T0,
                      payload={"segments": [{"start": 0, "end": 1, "text": "hi"}]}),
            Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                      version="m@demo", created_at=T0, payload={"markdown": "x"}),
        ],
    )
    write_docs(archive.root)
    return archive, rec


def test_write_docs_installs_every_doc_and_schema(tmp_path):
    archive, _ = build_one(tmp_path)
    for rel in ["README.md", "AGENTS.md", "FORMAT.md", "recordings/README.md",
                "catalog/README.md", "schemas/recording.schema.json",
                "schemas/rendition.schema.json"]:
        assert (archive.root / rel).is_file(), rel


def test_write_docs_is_idempotent(tmp_path):
    archive, _ = build_one(tmp_path)
    assert write_docs(archive.root) == []


def test_agents_md_states_the_privacy_rule():
    from importlib import resources
    text = (resources.files("recordings") / "format" / "AGENTS.md").read_text(encoding="utf-8")
    assert "`private` or `private/…` tag" in text


def test_every_file_the_writer_produces_is_documented(tmp_path):
    archive, rec = build_one(tmp_path)
    patterns = layout_patterns()
    folder = archive.path_for(rec.id)
    files = [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()]
    assert files, "the writer produced nothing"
    for rel in files:
        assert any(p.fullmatch(rel.replace(rec.id, "<id>")) for p in patterns), (
            f"{rel} is not described in FORMAT.md's layout block"
        )


def test_validate_reports_bad_files_with_their_path(tmp_path):
    archive, rec = build_one(tmp_path)
    assert validate(archive.root) == []
    bad = archive.path_for(rec.id) / "recording.json"
    bad.write_text('{"id": "nope"}', encoding="utf-8")
    problems = validate(archive.root)
    assert len(problems) == 1
    assert problems[0]["path"].endswith("recording.json")


def test_writer_names_files_in_the_documented_shape(tmp_path):
    archive, rec = build_one(tmp_path)
    folder = archive.path_for(rec.id)
    # The fixture's exact inputs produce deterministic filenames
    expected = {
        f"{rec.id}.mp3",
        "my-notes.md",
        "recording.json",
        "renditions/transcript-canned-large-v3-turbo@a4aaeec-20261008T120000Z.json",
        "renditions/notes-lecture-canned-m@demo-20261008T120000Z.json",
        "source/plaud-20261008T120000Z.json",
    }
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    assert actual == expected, f"writer produced {actual}"


def test_layout_patterns_reject_bogus_paths():
    patterns = layout_patterns()
    bogus = [
        "renditions/x.txt",
        "renditions/notes.json",  # no stamp
        "foo.json",
        "source/x.json",
        "notes/<id>.md",
        "recording.yaml",
        "<id>.mp3/extra",
    ]
    for path in bogus:
        assert not any(p.fullmatch(path) for p in patterns), (
            f"pattern incorrectly accepted {path!r}"
        )


def test_validate_reports_a_copied_folder_and_a_mismatched_id(tmp_path, capsys):
    import json
    import shutil

    from recordings.cli import main

    archive, rec = build_one(tmp_path)
    folder = archive.path_for(rec.id)
    copy = folder.parent.parent / "11" / rec.id  # copied into the wrong month
    shutil.copytree(folder, copy)
    other = folder.with_name("20261006T140003-0700_00000000")  # renamed by hand
    shutil.copytree(folder, other)
    expected = sorted([str(copy / "recording.json"), str(other / "recording.json")])

    problems = validate(archive.root)
    assert sorted(p["path"] for p in problems) == expected
    assert all(p["message"] == f"id {rec.id!r} does not match its folder" for p in problems)

    assert main(["validate", str(archive.root), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)["problems"]
    assert sorted(p["path"] for p in out) == expected
