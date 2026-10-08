from datetime import datetime, timezone

from recordings.archive import Archive, RawSource
from recordings.models import Rendition
from recordings_ui.views import library_view, recording_view, render_markdown


def test_library_lists_the_demo_newest_first(demo_archive, demo_ids):
    view = library_view(Archive(demo_archive))
    assert [r["id"] for r in view["recordings"]] == [
        demo_ids["apollo13-problem"], demo_ids["apollo11-first-steps"],
        demo_ids["jfk-rice"], demo_ids["fdr-fireside-1"]]
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
    assert jfk["media_url"] == f"../media/{demo_ids['jfk-rice']}.mp3"
    apollo = recording_view(archive, demo_ids["apollo11-first-steps"], media_base="../media/")
    assert apollo["media_url"] == f"../media/{demo_ids['apollo11-first-steps']}.mp4"


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
    assert recording_view(archive, "20261399T256199+0000_deadbeef") is None  # regex-valid, impossible date


def test_a_recording_with_no_outputs_has_empty_tabs(tmp_path):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    rec = archive.add_recording(
        media=media, recorded_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        timezone_name="UTC", time_source="ingest", title="Fresh upload", kind="audio",
        source=RawSource(kind="upload", ref="x.mp3", added_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
    view = recording_view(archive, rec.id)
    assert view["transcripts"] == [] and view["chosen_transcript"] is None
    assert view["notes"] == [] and view["plaud_notes"] == [] and view["my_notes_html"] is None


def test_model_written_html_is_escaped(tmp_path):
    assert "<script>" not in render_markdown("hi <script>alert(1)</script>")
    assert "&lt;script&gt;" in render_markdown("hi <script>alert(1)</script>")
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = archive.add_recording(
        media=media, recorded_at=t, timezone_name="UTC", time_source="ingest", title="x",
        kind="audio", source=RawSource(kind="upload", ref="x", added_at=t),
        renditions=[Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                              version="m@1", created_at=t,
                              payload={"markdown": "<img src=x onerror=alert(1)>"})])
    html = recording_view(archive, rec.id)["notes"][0]["outputs"][0]["html"]
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
        [demo_ids["jfk-rice"], demo_ids["apollo11-first-steps"], demo_ids["fdr-fireside-1"]])
    assert sorted(p["path"] for p in view["problems"]) == sorted(
        [str(copy / "recording.json"), str(apollo)])
    assert all("does not match its folder" in p["message"] for p in view["problems"])


def test_a_capitalised_private_tag_is_private_everywhere(tmp_path):
    from recordings.models import TagRef

    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = archive.add_recording(
        media=media, recorded_at=t, timezone_name="UTC", time_source="ingest", title="x",
        kind="audio", source=RawSource(kind="upload", ref="x", added_at=t),
        tags=[TagRef(tag="Private/Health"), TagRef(tag="notes/private")])
    view = library_view(archive)
    assert {t["tag"]: t["private"] for t in view["tags"]} == {"Private/Health": True}
    assert view["recordings"][0]["private"] is True
    assert recording_view(archive, rec.id)["private"] is True
