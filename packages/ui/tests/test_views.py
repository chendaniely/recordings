from datetime import UTC, datetime

import pytest
from recordings.archive import Archive, RawSource
from recordings.models import Rendition, TagRef
from recordings.writer import Incoming
from recordings_ui.views import (
    library_view,
    library_view_from,
    plain_text,
    recording_view,
    render_markdown,
    row,
    snippet,
    summarise,
)


def test_library_lists_the_demo_newest_first(demo_archive, demo_ids):
    view = library_view(Archive(demo_archive))
    assert [r["id"] for r in view["recordings"]] == [
        demo_ids["apollo13-problem"],
        demo_ids["apollo11-first-steps"],
        demo_ids["jfk-rice"],
        demo_ids["fdr-fireside-1"],
    ]
    assert view["counts"] == {"all": 4, "untagged": 1}
    assert {t["tag"] for t in view["tags"]} == {"talks", "space/apollo", "private"}
    assert next(t for t in view["tags"] if t["tag"] == "private")["private"] is True
    assert {n["note_type"] for n in view["note_types"]} == {"conference-talk", "general-fallback"}
    assert view["problems"] == []


def test_library_reports_a_broken_file_and_keeps_the_rest(demo_archive, demo_ids):
    archive = Archive(demo_archive)
    (archive.path_for(demo_ids["jfk-rice"]) / "recording.json").write_text("{", encoding="utf-8")
    view = library_view(archive)
    assert view["counts"]["all"] == 3
    assert len(view["problems"]) == 1 and view["problems"][0]["path"].endswith("recording.json")


def test_recording_view_for_jfk(demo_archive, demo_ids):
    view = recording_view(Archive(demo_archive), demo_ids["jfk-rice"])
    assert view["media_url"] == f"/media/{demo_ids['jfk-rice']}"
    assert view["kind"] == "audio" and view["private"] is False
    assert view["chosen_transcript"] == view["transcripts"][0]["rendition"]
    turns = view["transcripts"][0]["turns"]
    assert turns and all(t["end"] >= t["start"] for t in turns)
    (group,) = view["notes"]
    assert group["note_type"] == "conference-talk"
    assert {o["model"] for o in group["outputs"]} == {"qwen3.6-35b-a3b", "claude-opus-5-5"}
    assert "<h1>" in group["outputs"][0]["html"]
    assert "My notes" in view["my_notes_html"]


def test_a_media_base_links_the_media_file_itself(demo_archive, demo_ids):
    # The static Pages demo (spec §17.1) serves media as files, so the URL keeps the extension
    # and the host sends the right Content-Type.
    archive = Archive(demo_archive)
    jfk = recording_view(archive, demo_ids["jfk-rice"], media_base="../media/")
    assert jfk["media_url"] == f"../media/{demo_ids['jfk-rice']}.mp3"  # unchanged by encoding
    assert jfk["problems"] == []
    apollo = recording_view(archive, demo_ids["apollo11-first-steps"], media_base="../media/")
    assert apollo["media_url"] == f"../media/{demo_ids['apollo11-first-steps']}.mp4"


def _set_media_file(archive, rid, name):
    import json

    path = archive.path_for(rid) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["media"]["file"] = name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "name", ["../x", "../../etc/passwd", "sub/x.mp3", "sub\\x.mp3", "/x.mp3", "", ".", ".."]
)
def test_a_media_base_links_no_file_outside_the_media_folder(demo_archive, demo_ids, name):
    # recording.json is hand-editable: with a base, media.file becomes part of a URL.
    archive = Archive(demo_archive)
    path = _set_media_file(archive, demo_ids["jfk-rice"], name)
    view = recording_view(archive, demo_ids["jfk-rice"], media_base="../media/")
    assert view["media_url"] is None
    assert [p["path"] for p in view["problems"]] == [str(path)]
    assert "media.file" in view["problems"][0]["message"]
    # The server's route checks the file itself, so its URL doesn't change.
    assert (
        recording_view(archive, demo_ids["jfk-rice"])["media_url"]
        == f"/media/{demo_ids['jfk-rice']}"
    )


def test_a_media_base_percent_encodes_the_file_name(demo_archive, demo_ids):
    archive = Archive(demo_archive)
    _set_media_file(archive, demo_ids["jfk-rice"], "talk #1 50%?.mp3")
    view = recording_view(archive, demo_ids["jfk-rice"], media_base="../media/")
    assert view["media_url"] == "../media/talk%20%231%2050%25%3F.mp3"
    assert view["problems"] == []


def test_without_a_media_base_media_goes_through_the_server_route(demo_archive, demo_ids):
    view = recording_view(Archive(demo_archive), demo_ids["apollo11-first-steps"], media_base=None)
    assert view["media_url"] == f"/media/{demo_ids['apollo11-first-steps']}"


def test_plaud_outputs_go_to_the_plaud_tab_not_notes(demo_archive, demo_ids):
    view = recording_view(Archive(demo_archive), demo_ids["apollo13-problem"])
    assert view["notes"] == []
    assert len(view["plaud_notes"]) == 1
    assert {t["engine"] for t in view["transcripts"]} == {"canned", "plaud"}


def test_unknown_or_malformed_id_is_none(demo_archive):
    archive = Archive(demo_archive)
    assert recording_view(archive, "20200101T000000+0000_00000000") is None
    assert recording_view(archive, "../../etc/passwd") is None
    assert (
        recording_view(archive, "20261399T256199+0000_deadbeef") is None
    )  # regex-valid, impossible date


def test_a_recording_with_no_outputs_has_empty_tabs(tmp_path, writer):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    rec = writer.add(
        Incoming(
            media=media,
            recorded_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            timezone_name="UTC",
            time_source="ingest",
            title="Fresh upload",
            kind="audio",
            sources=(
                RawSource(kind="upload", ref="x.mp3", added_at=datetime(2026, 10, 1, tzinfo=UTC)),
            ),
        )
    ).recording
    view = recording_view(writer.archive, rec.id)
    assert view["transcripts"] == [] and view["chosen_transcript"] is None
    assert view["notes"] == [] and view["plaud_notes"] == [] and view["my_notes_html"] is None


def test_model_written_html_is_escaped(tmp_path, writer):
    assert "<script>" not in render_markdown("hi <script>alert(1)</script>")
    assert "&lt;script&gt;" in render_markdown("hi <script>alert(1)</script>")
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=UTC)
    rec = writer.add(
        Incoming(
            media=media,
            recorded_at=t,
            timezone_name="UTC",
            time_source="ingest",
            title="x",
            kind="audio",
            sources=(RawSource(kind="upload", ref="x", added_at=t),),
            renditions=(
                Rendition(
                    kind="notes",
                    note_type="lecture",
                    engine="canned",
                    model="m",
                    version="m@1",
                    created_at=t,
                    payload={"markdown": "<img src=x onerror=alert(1)>"},
                ),
            ),
        )
    ).recording
    html = recording_view(writer.archive, rec.id)["notes"][0]["outputs"][0]["html"]
    assert "<img" not in html


def test_markdown_images_are_not_rendered():
    # An image in notes would make the browser fetch it: no external requests, ever.
    html = render_markdown("before ![x](https://example.com/x.png) after")
    assert "<img" not in html
    assert "before" in html and "after" in html


def test_recording_view_with_corrupt_rendition_shows_problems(demo_archive, demo_ids):
    archive = Archive(demo_archive)
    # Add a corrupt rendition file to a demo recording
    folder = archive.path_for(demo_ids["jfk-rice"])
    corrupt_path = folder / "renditions" / "corrupt.json"
    corrupt_path.write_text("{junk", encoding="utf-8")

    view = recording_view(archive, demo_ids["jfk-rice"])
    assert view is not None
    assert len(view["problems"]) == 1
    assert view["problems"][0]["path"].endswith("corrupt.json")
    # Good renditions should still be there
    assert len(view["transcripts"]) > 0


def test_recording_view_with_corrupt_recording_json_is_none(demo_archive, demo_ids):
    archive = Archive(demo_archive)
    folder = archive.path_for(demo_ids["jfk-rice"])
    (folder / "recording.json").write_text("{", encoding="utf-8")

    view = recording_view(archive, demo_ids["jfk-rice"])
    assert view is None


def test_library_reports_a_copied_folder_and_a_mistyped_id(demo_archive, demo_ids):
    import json
    import shutil

    archive = Archive(demo_archive)
    jfk = archive.path_for(demo_ids["jfk-rice"])
    copy = jfk.with_name(f"{jfk.name} copy")
    shutil.copytree(jfk, copy)
    apollo = archive.path_for(demo_ids["apollo13-problem"]) / "recording.json"
    data = json.loads(apollo.read_text(encoding="utf-8"))
    data["id"] = data["id"][:-1] + ("1" if data["id"][-1] == "0" else "0")
    apollo.write_text(json.dumps(data), encoding="utf-8")

    view = library_view(archive)
    assert sorted(r["id"] for r in view["recordings"]) == sorted(
        [demo_ids["jfk-rice"], demo_ids["apollo11-first-steps"], demo_ids["fdr-fireside-1"]]
    )
    assert sorted(p["path"] for p in view["problems"]) == sorted(
        [str(copy / "recording.json"), str(apollo)]
    )
    assert all("does not match its folder" in p["message"] for p in view["problems"])


def test_a_capitalised_private_tag_is_private_everywhere(tmp_path, writer):
    from recordings.models import TagRef

    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=UTC)
    rec = writer.add(
        Incoming(
            media=media,
            recorded_at=t,
            timezone_name="UTC",
            time_source="ingest",
            title="x",
            kind="audio",
            sources=(RawSource(kind="upload", ref="x", added_at=t),),
            tags=(TagRef(tag="Private/Health"), TagRef(tag="notes/private")),
        )
    ).recording
    view = library_view(writer.archive)
    assert {t["tag"]: t["private"] for t in view["tags"]} == {"Private/Health": True}
    assert view["recordings"][0]["private"] is True
    assert recording_view(writer.archive, rec.id)["private"] is True


def test_a_turn_shows_plauds_name_for_its_speaker(tmp_path, writer):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=UTC)
    rec = writer.add(
        Incoming(
            media=media,
            recorded_at=t,
            timezone_name="UTC",
            time_source="plaud",
            title="x",
            kind="audio",
            sources=(RawSource(kind="plaud", ref="x", added_at=t),),
            renditions=(
                Rendition(
                    kind="transcript",
                    engine="plaud",
                    model="plaud",
                    version="plaud@1",
                    created_at=t,
                    payload={
                        "segments": [
                            {
                                "start": 0,
                                "end": 1,
                                "speaker": "Speaker 1",
                                "speaker_name": "Ada",
                                "text": "Hello.",
                            },
                            {"start": 1, "end": 2, "speaker": "Speaker 2", "text": "Hi."},
                        ]
                    },
                ),
            ),
        )
    ).recording
    turns = recording_view(writer.archive, rec.id)["transcripts"][0]["turns"]
    assert [t["speaker"] for t in turns] == ["Ada", "Speaker 2"]


def test_rows_carry_the_duration_in_milliseconds(demo_archive, demo_ids):
    # why: the Tags grid sorts by duration (tagging spec §7); the label alone can't sort.
    rows = {r["id"]: r for r in library_view(Archive(demo_archive))["recordings"]}
    assert rows[demo_ids["jfk-rice"]]["duration_ms"] == 90009
    assert rows[demo_ids["jfk-rice"]]["duration"] == "1 min"


def test_summarise_recounts_from_the_rows_alone(demo_archive):
    view = library_view(Archive(demo_archive))
    again = summarise(view["recordings"])
    assert again == {k: view[k] for k in ("counts", "tags", "note_types")}
    tagged = [
        {**r, "tags": [*r["tags"], {"tag": "Private/x", "by": "you"}]} for r in view["recordings"]
    ]
    counted = summarise(tagged)
    assert counted["counts"] == {"all": 4, "untagged": 0}
    assert {"tag": "Private/x", "count": 4, "private": True} in counted["tags"]


def test_a_view_from_recordings_matches_the_archives_own(demo_archive):
    archive = Archive(demo_archive)
    recordings = list(archive.iter_recordings())
    assert library_view_from(recordings, archive.problems) == library_view(archive)
    assert row(recordings[0])["id"] == recordings[0].id


def test_a_note_type_known_only_from_an_exclusion_is_listed_at_zero(demo_archive):
    # why: tagging plan, decision 18. A type removed from every recording is still one the
    # archive has, through the exclusion, so the picker and the sidebar keep offering it.
    archive = Archive(demo_archive)
    first, *rest = archive.iter_recordings()
    excluded = first.model_copy(update={"excluded_note_types": ["lecture"]})
    view = library_view_from([excluded, *rest], archive.problems)
    assert {"note_type": "lecture", "count": 0} in view["note_types"]
    assert summarise(view["recordings"], ["lecture"])["note_types"] == view["note_types"]
    assert {"note_type": "lecture", "count": 0} not in summarise(view["recordings"])["note_types"]


def test_a_snippet_is_the_chosen_transcripts_first_words_as_plain_text(demo_archive, demo_ids):
    text = snippet(Archive(demo_archive), demo_ids["jfk-rice"])
    assert text and len(text) <= 160 and "\n" not in text


def test_a_snippet_falls_back_to_the_first_plaud_note_without_markdown(tmp_path, writer):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3 synthetic")
    rec = writer.add(
        Incoming(
            media=media,
            recorded_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            timezone_name="UTC",
            time_source="ingest",
            title="Week 4",
            kind="audio",
            sources=(),
        )
    ).recording
    assert snippet(writer.archive, rec.id) is None  # nothing yet: the grid says so
    writer.write_rendition(
        rec.id,
        Rendition(
            kind="notes",
            note_type="plaud-summary",
            engine="plaud",
            version="plaud@1",
            created_at=datetime(2026, 10, 1, 10, tzinfo=UTC),
            payload={"markdown": "# Week 4\n\n**Bold** words and `code` here.\n\n- one\n- two"},
        ),
    )
    assert snippet(writer.archive, rec.id) == "Week 4 Bold words and code here. one two"


def test_a_long_snippet_stops_at_160_characters():
    assert plain_text("*a* " * 200).startswith("a a a")
    long = " ".join(["word"] * 100)
    from recordings_ui.views import _clip

    clipped = _clip(long, 160)
    assert len(clipped) == 160 and clipped.endswith("…")


def test_a_snippet_of_an_unknown_recording_is_none(demo_archive):
    assert snippet(Archive(demo_archive), "20200101T000000+0000_0000beef") is None


def _with_transcript(tmp_path, writer, name: str, minute: int, tags: tuple[TagRef, ...]) -> str:
    media = tmp_path / f"{name}.mp3"
    media.write_bytes(f"ID3 synthetic {name}".encode())
    t = datetime(2026, 10, 1, 9, minute, tzinfo=UTC)
    transcript = Rendition(
        kind="transcript",
        engine="canned",
        model="m",
        version="m@1",
        created_at=t,
        payload={"segments": [{"start": 0, "end": 1, "text": "Synthetic words"}]},
    )
    incoming = Incoming(
        media=media,
        recorded_at=t,
        timezone_name="UTC",
        time_source="ingest",
        title="Synthetic 042",
        kind="audio",
        sources=(),
        tags=tags,
        renditions=(transcript,),
    )
    return writer.add(incoming).recording.id


def test_a_private_recording_has_no_snippet(tmp_path, writer):
    # why: least privilege (Task 7's council). The browser asks only for untagged rows, which are
    # never private, but the server doesn't count on it: it never reads a private transcript.
    shown = _with_transcript(tmp_path, writer, "open", 0, ())
    assert snippet(writer.archive, shown) == "Synthetic words"
    for minute, tag in enumerate(("private", "Private/journal"), start=1):
        hidden = _with_transcript(
            tmp_path, writer, f"p{minute}", minute, (TagRef(tag=tag, by="you"),)
        )
        withheld = snippet(writer.archive, hidden) is None  # a boolean: a failure prints no text
        assert withheld, tag


def test_the_recording_view_follows_the_stores_in_memory_tags(demo_archive, demo_ids):
    # why: Pages keeps tags in memory (tagging spec §5), so the pane's record must come from the
    # store the service holds, not the files, which never change there. One source of truth.
    from recordings.models import TagRef
    from recordings.tagops import AddTags, Recorded, RemoveTags
    from recordings_ui.tagging import MemoryStore

    archive = Archive(demo_archive)
    store = MemoryStore(archive)
    rid = demo_ids["apollo13-problem"]
    store.apply(rid, Recorded(AddTags((TagRef(tag="private", by="you"),)), "req0001abcd"))
    assert recording_view(archive, rid)["private"] is False  # the files, as the real app reads them
    view = recording_view(archive, rid, read=store.read)
    assert view["private"] is True and "private" in {t["tag"] for t in view["tags"]}
    store.apply(rid, Recorded(RemoveTags(("private",), may_expose=frozenset({rid})), "req0002abcd"))
    assert recording_view(archive, rid, read=store.read)["private"] is False
