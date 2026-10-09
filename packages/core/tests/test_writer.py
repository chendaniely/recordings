import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings import writer as writer_module
from recordings.archive import Archive, RawSource, sha256_file
from recordings.config import load_config
from recordings.index import Index, IndexRefused
from recordings.init import adopt_archive
from recordings.models import Rendition, TagRef, is_private
from recordings.sentinel import MIRROR, SENTINEL
from recordings.state import State
from recordings.writer import (
    AddTags,
    InvalidFile,
    MergeIncoming,
    NotTheWriter,
    StaleFile,
    Writer,
    WriterError,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime.fromisoformat("2026-10-07T09:00:00-07:00")  # a second, different ID


def notes(created_at=T0, model="qwen3.6-35b-a3b", markdown="# Notes") -> Rendition:
    return Rendition(kind="notes", note_type="lecture", engine="canned", model=model,
                     version=f"{model}@demo", created_at=created_at, payload={"markdown": markdown})


def test_add_lays_out_the_folder_and_starts_at_rev_1(writer, make_incoming):
    incoming = make_incoming()
    added = writer.add(incoming)
    rec, sha = added.recording, sha256_file(incoming.media)
    assert added.created and rec.id == f"20261006T140003-0700_{sha[:8]}" and rec.rev == 1
    folder = writer.root / "recordings" / "2026" / "10" / rec.id
    assert (folder / f"{rec.id}.mp3").read_bytes() == incoming.media.read_bytes()
    assert (folder / "source" / "plaud-20261008T120000Z.json").read_bytes() == b'{"id": 1}'
    data = json.loads((folder / "recording.json").read_text(encoding="utf-8"))
    assert data["rev"] == 1 and data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["sources"][0]["sha256"] == hashlib.sha256(b'{"id": 1}').hexdigest()
    assert not (writer.root / ".tmp").exists()
    assert writer.index.find_by_sha256(sha) == rec.id
    revision = writer.state.last_committed(f"recordings/2026/10/{rec.id}/recording.json")
    assert revision.rev == 1 and revision.body == (folder / "recording.json").read_bytes()


def test_a_writer_needs_the_sentinel_the_same_archive_and_the_same_writer(make_writer, tmp_path):
    writer = make_writer()
    with pytest.raises(NotTheWriter, match="writer_id"):
        Writer(writer.root, writer.state, writer.index, writer_id="laptop")
    other = make_writer("other")
    with pytest.raises(NotTheWriter, match="UUID changed"):
        Writer(writer.root, other.state, writer.index, writer_id="test")
    (writer.root / SENTINEL).unlink()
    with pytest.raises(NotTheWriter, match="mounted"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")


def test_a_writer_refuses_the_nas_mirror(writer):
    # why: §3. The mirror's copy carries archive.json; once `init --adopt` has run beside it, the
    # UUID and writer_id checks all pass. The mirror job marks its copy (Task 12).
    (writer.root / MIRROR).write_text("a copy\n", encoding="utf-8")
    with pytest.raises(NotTheWriter, match="mirror"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")


def test_writer_open_says_which_settings_are_missing_and_writes_the_docs(writer, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[archive]\npath = "{writer.root}"\n', encoding="utf-8")
    with pytest.raises(NotTheWriter, match=r"\[state\] path"):
        Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n[state]\npath = "{writer.state.path}"\n'
        f'[index]\npath = "{writer.index.path}"\n', encoding="utf-8")
    (writer.root / "FORMAT.md").unlink()
    Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    assert (writer.root / "FORMAT.md").is_file()  # §6.7: the docs are written at startup


def test_a_damaged_state_db_is_not_the_writer_and_says_to_restore_it(writer, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n[state]\npath = "{writer.state.path}"\n'
        f'[index]\npath = "{writer.index.path}"\n', encoding="utf-8")
    for leftover in writer.state.path.glob("state.db-*"):  # WAL files: garbage stands alone
        leftover.unlink()
    writer.state.db_path.write_bytes(b"this is not a database\n" * 64)
    with pytest.raises(NotTheWriter, match="(?i)restore state.db from the backup"):
        Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    with pytest.raises(NotTheWriter, match="(?i)restore state.db from the backup"):
        Writer(writer.root, State(writer.state.path), writer.index, writer_id="test")


def test_after_init_adopt_the_next_write_takes_each_file_as_its_base(writer, make_incoming):
    # why: §6.8. `recordings init --adopt` recreates a lost state.db, which has no merge bases.
    rec = writer.add(make_incoming()).recording
    shutil.rmtree(writer.state.path)
    adopt_archive(writer.root, writer.state.path, writer_id="test")
    fresh = Writer(writer.root, State.open(writer.state.path), writer.index, writer_id="test")
    done = fresh.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert done.rev == 2 and [t.tag for t in done.tags] == ["talks"]


def test_a_writer_never_empties_the_index_when_it_starts(writer, make_incoming, tmp_path):
    # why: §6.7. A pending mark made the next Writer rebuild with allow_empty=True, so an archive
    # that reads as empty (not mounted, or unreadable after a UID change) emptied the index.
    rec = writer.add(make_incoming()).recording
    writer.state.mark_index_pending(rec.id)
    (writer.root / "recordings").rename(tmp_path / "moved-away")
    (writer.root / "recordings").mkdir()
    with pytest.raises(NotTheWriter, match="refusing to empty"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")
    assert writer.index.count() == 1


def test_a_writer_never_takes_state_db_for_its_index(writer):
    # why: state.db's meta holds the archive's UUID too. Taken for the index, it answered every
    # lookup with nothing, and a refused rebuild's hint said to delete it.
    with pytest.raises(NotTheWriter, match="not an index") as caught:
        Writer(writer.root, writer.state, Index(writer.state.db_path), writer_id="test")
    assert "delete" not in str(caught.value)
    assert list(writer.state.path.glob("state.db*.unreadable-*")) == []
    assert writer.state.meta("writer_id") == "test"


def test_the_same_bytes_again_merge_and_keep_the_second_copys_outputs_tags_and_notes(
        writer, make_incoming):
    # why: §6.4. The merge keeps the second copy's sources, outputs, decisions and notes.
    first = writer.add(make_incoming(b"same bytes", my_notes="first\n")).recording
    second = writer.add(make_incoming(
        b"same bytes", name="other.mp3",
        sources=(RawSource(kind="upload", ref="other.mp3", added_at=T0),),
        tags=(TagRef(tag="talks"),), renditions=(notes(),), my_notes="second\n",
        excluded_note_types=("glossary",)))
    assert not second.created and second.recording.id == first.id
    assert (second.sources_added, second.renditions_added) == (1, 1)
    rec = writer.archive.load(first.id)
    assert [s.kind for s in rec.sources] == ["plaud", "upload"]
    assert [t.tag for t in rec.tags] == ["talks"] and rec.excluded_note_types == ["glossary"]
    assert rec.rev == 2
    assert len(writer.archive.renditions(first.id)) == 1
    assert writer.archive.read_my_notes(first.id) == "first\n\n---\n\nsecond\n"
    assert len(list(writer.archive.recording_dirs())) == 1


def test_adding_the_same_thing_twice_adds_nothing(writer, make_incoming):
    incoming = make_incoming(b"bytes", renditions=(notes(),), my_notes="mine\n")
    writer.add(incoming)
    again = writer.add(incoming)
    assert (again.sources_added, again.renditions_added) == (0, 0)
    assert again.recording.rev == 1  # nothing changed, so nothing was written
    assert writer.archive.read_my_notes(again.recording.id) == "mine\n"


def test_a_private_tag_on_a_duplicate_lands_before_any_of_its_content(writer, make_incoming,
                                                                      monkeypatch):
    # why: §7.4. A merge killed after publishing must never leave private content in a
    # recording that isn't private yet.
    rec = writer.add(make_incoming(b"bytes")).recording

    def killed(folder, source):
        raise KeyboardInterrupt("killed")

    monkeypatch.setattr(Writer, "_publish_raw", staticmethod(killed))
    with pytest.raises(KeyboardInterrupt):
        writer.add(make_incoming(
            b"bytes", tags=(TagRef(tag="private"),), renditions=(notes(),),
            sources=(RawSource(kind="upload", ref="u", added_at=T0, payload=b"{}"),)))
    assert is_private(writer.archive.load(rec.id))


def test_a_merge_killed_before_its_mutate_leaves_no_duplicate_source_on_the_rerun(
        writer, make_incoming, monkeypatch):
    # Review Focus 2: the new snapshot is published, then the process dies before mutate records
    # it. Running the same add again must reuse that file, not publish a `-2` copy.
    writer.add(make_incoming(b"bytes"))
    later = RawSource(kind="plaud", ref="a" * 32, added_at=T0,
                      fetched_at=datetime(2026, 10, 9, tzinfo=timezone.utc), payload=b'{"id": 2}')
    real_mutate = Writer.mutate

    def killed_in_the_merge(self, recording_id, op):
        if isinstance(op, MergeIncoming):
            raise KeyboardInterrupt("killed")
        return real_mutate(self, recording_id, op)

    monkeypatch.setattr(Writer, "mutate", killed_in_the_merge)
    with pytest.raises(KeyboardInterrupt):
        writer.add(make_incoming(b"bytes", sources=(later,)))
    monkeypatch.undo()
    again = writer.add(make_incoming(b"bytes", sources=(later,)))
    folder = writer.archive.path_for(again.recording.id)
    expected = ["plaud-20261008T120000Z.json", "plaud-20261009T000000Z.json"]
    assert sorted(p.name for p in (folder / "source").iterdir()) == expected
    assert [s.raw for s in again.recording.sources] == [f"source/{name}" for name in expected]


def test_a_new_snapshot_of_the_same_source_is_kept_beside_the_first(writer, make_incoming):
    writer.add(make_incoming(b"bytes"))
    later = RawSource(kind="plaud", ref="a" * 32, added_at=T0,
                      fetched_at=datetime(2026, 10, 9, tzinfo=timezone.utc), payload=b'{"id": 2}')
    added = writer.add(make_incoming(b"bytes", sources=(later,)))
    assert added.sources_added == 1
    assert [s.raw for s in added.recording.sources] == [
        "source/plaud-20261008T120000Z.json", "source/plaud-20261009T000000Z.json"]


def test_renditions_are_write_once_ordered_and_never_duplicated(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    a = writer.write_rendition(rec.id, notes(T0.replace(minute=5), model="b-model"))
    b = writer.write_rendition(rec.id, notes(T0, model="a-model"))
    c = writer.write_rendition(rec.id, notes(T0, model="a-model", markdown="# Other"))
    assert writer.write_rendition(rec.id, notes(T0, model="a-model")) is None  # identical
    assert c.endswith("-2.json")
    paths = [p for p, _ in writer.archive.renditions(rec.id)]
    assert set(paths[:2]) == {b, c} and paths[2] == a
    with pytest.raises(KeyError):
        writer.write_rendition("20200101T000000+0000_00000000", notes())


def test_a_non_ascii_title_round_trips_as_utf8(writer, make_incoming):
    rec = writer.add(make_incoming(title="会議メモ 🎙")).recording
    raw = (writer.archive.path_for(rec.id) / "recording.json").read_bytes()
    assert "会議メモ 🎙".encode() in raw and writer.archive.load(rec.id).title == "会議メモ 🎙"


def test_a_failed_assembly_leaves_nothing_behind(writer, make_incoming, monkeypatch):
    def failing_copy(*args, **kwargs):
        raise OSError("Simulated copy failure")

    monkeypatch.setattr(writer_module, "copy_file_synced", failing_copy)
    with pytest.raises(OSError, match="Simulated copy failure"):
        writer.add(make_incoming())
    assert not (writer.root / ".tmp").exists()
    assert list(writer.archive.recording_dirs()) == []


def test_a_new_year_folder_is_made_durable_in_recordings(writer, make_incoming, monkeypatch):
    # why: §6.4. A new year folder is an entry in recordings/, so recordings/ needs an fsync too.
    synced = []
    real_fsync_dir = writer_module.fsync_dir

    def spy(path):
        synced.append(Path(path))
        real_fsync_dir(path)

    monkeypatch.setattr(writer_module, "fsync_dir", spy)
    writer.add(make_incoming())
    assert writer.root / "recordings" in synced


def test_media_that_changes_while_it_is_copied_is_refused(writer, make_incoming, monkeypatch):
    real_copy = writer_module.copy_file_synced

    def copy_then_corrupt(src, dst):
        real_copy(src, dst)
        dst.write_bytes(b"different")

    monkeypatch.setattr(writer_module, "copy_file_synced", copy_then_corrupt)
    with pytest.raises(WriterError, match="changed while it was being copied"):
        writer.add(make_incoming())
    assert list(writer.archive.recording_dirs()) == []


def test_mutate_bumps_rev_and_a_no_op_writes_nothing(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    tagged = writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert tagged.rev == 2 and writer.archive.load(rec.id).rev == 2
    path = writer.archive.path_for(rec.id) / "recording.json"
    before = path.read_bytes()
    assert writer.mutate(rec.id, AddTags((TagRef(tag="talks"),))).rev == 2
    assert path.read_bytes() == before


def test_rev_never_goes_backwards_after_a_hand_edit_lowers_it(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.mutate(rec.id, AddTags((TagRef(tag="a"),)))
    writer.mutate(rec.id, AddTags((TagRef(tag="b"),)))  # rev 3
    path = writer.archive.path_for(rec.id) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rev"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    writer.reindex()
    assert writer.mutate(rec.id, AddTags((TagRef(tag="c"),))).rev == 4


def test_mutate_refuses_an_outside_edit_until_reindex_accepts_it(writer, make_incoming):
    # why: §6.4. Changes are found by content hash, not mtime or rev: an editor can keep both.
    rec = writer.add(make_incoming()).recording
    path = writer.archive.path_for(rec.id) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = "Edited by hand"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StaleFile, match="recordings reindex"):
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert [f["kind"] for f in writer.state.flags()] == ["stale"]
    report = writer.reindex()
    assert report.adopted == (f"recordings/2026/10/{rec.id}/recording.json",)
    assert writer.state.flags() == []
    done = writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert done.title == "Edited by hand" and done.rev == 2


def test_reindex_clears_a_stale_flag_once_the_edit_is_undone(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    path = writer.archive.path_for(rec.id) / "recording.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"Week 4"', b'"Edited"'))
    with pytest.raises(StaleFile):
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    path.write_bytes(original)  # the edit is undone by hand
    assert writer.reindex().adopted == ()
    assert writer.state.flags() == []


def test_mutate_refuses_an_invalid_file_and_flags_it(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.add(make_incoming(recorded_at=NEXT_DAY))
    path = writer.archive.path_for(rec.id) / "recording.json"
    path.write_text("{", encoding="utf-8")
    report = writer.reindex()  # accepts nothing for this file: it can't be parsed
    # §11: the rebuild drops a file it can't read, so the report must name it, twice over.
    rel = path.relative_to(writer.root).as_posix()
    assert report.recordings == 1 and report.invalid == (rel,)
    assert [p["path"] for p in report.problems] == [str(path)]
    assert [f["kind"] for f in writer.state.flags()] == ["invalid"]
    with pytest.raises(InvalidFile) as refused:
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert "reindex" not in str(refused.value)  # reindex can't help: it flags it invalid again
    assert [f["kind"] for f in writer.state.flags()] == ["invalid"]  # never "stale" as well
    assert path.read_text(encoding="utf-8") == "{"  # §11: never overwritten or "fixed"


def test_a_crash_before_the_file_is_replaced_loses_only_that_write(writer, make_incoming,
                                                                   monkeypatch):
    # Review Focus 1: a pending revision whose file never landed is discarded, not "stale".
    rec = writer.add(make_incoming()).recording

    def power_cut(path, data):
        raise OSError("power cut")

    monkeypatch.setattr(writer_module, "write_bytes_atomic", power_cut)
    with pytest.raises(OSError, match="power cut"):
        writer.mutate(rec.id, AddTags((TagRef(tag="lost"),)))
    monkeypatch.undo()
    done = writer.mutate(rec.id, AddTags((TagRef(tag="kept"),)))
    assert [t.tag for t in done.tags] == ["kept"] and done.rev == 2


def test_a_crash_after_the_file_is_replaced_keeps_the_write(writer, make_incoming, monkeypatch):
    # Review Focus 1: the file landed but its revision wasn't committed. The next write commits
    # it, instead of calling the app's own write an outside edit.
    rec = writer.add(make_incoming()).recording
    real_commit = writer.state.commit
    calls = []

    def crash_once(revision_id):
        calls.append(revision_id)
        if len(calls) == 1:
            raise OSError("power cut")
        real_commit(revision_id)

    monkeypatch.setattr(writer.state, "commit", crash_once)
    with pytest.raises(OSError, match="power cut"):
        writer.mutate(rec.id, AddTags((TagRef(tag="landed"),)))
    done = writer.mutate(rec.id, AddTags((TagRef(tag="next"),)))
    assert [t.tag for t in done.tags] == ["landed", "next"] and done.rev == 3


def test_a_crash_after_a_new_recording_lands_but_before_its_commit_merges_on_the_next_add(
        writer, make_incoming, monkeypatch):
    # Review Focus 1, for add: the folder landed, its revision wasn't committed, and the index
    # never heard of it. The same bytes again, under another ID, merge into it without StaleFile.
    def power_cut(revision_id):
        raise OSError("power cut")

    monkeypatch.setattr(writer.state, "commit", power_cut)
    with pytest.raises(OSError, match="power cut"):
        writer.add(make_incoming(b"same bytes"))
    monkeypatch.undo()
    (folder,) = writer.archive.recording_dirs()
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY, sources=(
        RawSource(kind="upload", ref="u", added_at=T0),)))
    assert not again.created and again.recording.id == folder.name
    assert again.sources_added == 1 and again.recording.rev == 2


def test_an_index_left_behind_by_a_crash_never_causes_a_duplicate(writer, make_incoming):
    first = writer.add(make_incoming(b"bytes")).recording
    writer.index.path.unlink()  # as if the process died after the rename, before the index
    Index(writer.index.path).rebuild(Archive(writer.root / "nowhere"),
                                     archive_uuid=writer.identity.uuid, allow_empty=True)
    again = writer.add(make_incoming(b"bytes", sources=(RawSource(kind="upload", ref="u",
                                                                  added_at=T0),)))
    assert again.recording.id == first.id and not again.created


def test_an_index_that_cannot_answer_is_rebuilt_before_an_add_believes_it(writer, make_incoming):
    # why: a gone or unreadable index says "none" to every lookup, and the folder check only
    # catches the same recorded_at: the same bytes under another one became a second recording.
    first = writer.add(make_incoming(b"same bytes")).recording
    writer.index.path.unlink()
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY))
    assert not again.created and again.recording.id == first.id


def test_a_stuck_index_never_turns_the_same_bytes_into_a_second_recording(
        writer, make_incoming, monkeypatch):
    # why: the index answers "none" while it can't be updated, and its UUID still matches, so no
    # rebuild runs. The pending marks name the recordings it has not heard of: ask them too.
    def busy(rec):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer.index, "upsert", busy)
    first = writer.add(make_incoming(b"same bytes")).recording
    again = writer.add(make_incoming(
        b"same bytes", recorded_at=NEXT_DAY,
        sources=(RawSource(kind="upload", ref="u", added_at=T0),)))
    assert not again.created and again.recording.id == first.id
    assert len(list(writer.archive.recording_dirs())) == 1
    assert [s.kind for s in writer.archive.load(first.id).sources] == ["plaud", "upload"]


def test_a_catch_up_between_the_index_answer_and_the_marks_cannot_hide_a_recording(
        writer, make_incoming, monkeypatch):
    # why: another process's catch-up can finish a recording's update after this add asked the
    # index (which said none) and before it read the marks. The marks are read first.
    real_upsert = writer.index.upsert

    def busy(rec):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer.index, "upsert", busy)
    first = writer.add(make_incoming(b"same bytes")).recording
    real_find = writer.index.find_by_sha256
    calls = []

    def find_then_catch_up(sha):
        found = real_find(sha)
        if not calls:
            calls.append(sha)
            monkeypatch.setattr(writer.index, "upsert", real_upsert)
            writer._catch_up_index()  # as another process: the update lands, the mark goes
        return found

    monkeypatch.setattr(writer.index, "find_by_sha256", find_then_catch_up)
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY))
    assert not again.created and again.recording.id == first.id
    assert len(list(writer.archive.recording_dirs())) == 1


def test_a_failed_index_update_never_fails_the_write_and_the_next_add_catches_up(
        writer, make_incoming, monkeypatch):
    # The write has landed, so a busy index must not report it as failed. The next add must
    # still find it, or the same bytes under another ID would become a second recording.
    def busy(rec):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer.index, "upsert", busy)
    first = writer.add(make_incoming(b"same bytes")).recording
    assert writer.state.index_pending() == [first.id]
    monkeypatch.undo()
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY))
    assert not again.created and again.recording.id == first.id
    assert writer.state.index_pending() == []


def test_a_rebuild_finishes_pending_marks_instead_of_clearing_them(writer, make_incoming,
                                                                   monkeypatch):
    # why: a recording whose folder landed after the rebuild listed the archive still has its
    # mark. Clearing every mark at once left it out of the index until the next reindex.
    rec = writer.add(make_incoming(b"bytes")).recording
    real = writer.index.rebuild

    def rebuild_that_missed_it(*args, **kwargs):
        count = real(*args, **kwargs)
        Index(writer.index.path).rebuild(Archive(writer.root / "nowhere"),
                                         archive_uuid=writer.identity.uuid, allow_empty=True)
        return count

    monkeypatch.setattr(writer.index, "rebuild", rebuild_that_missed_it)
    writer.state.mark_index_pending(rec.id)
    writer.rebuild_index()
    assert writer.index.find_by_sha256(rec.media.sha256) == rec.id
    assert writer.state.index_pending() == []


def test_a_pending_mark_on_an_invalid_file_does_not_make_every_start_rebuild(writer,
                                                                             make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.add(make_incoming(recorded_at=NEXT_DAY))
    (writer.archive.path_for(rec.id) / "recording.json").write_text("{", encoding="utf-8")
    writer.state.mark_index_pending(rec.id)
    writer.rebuild_index()
    assert writer.state.index_pending() == []


def test_an_index_entry_whose_folder_was_deleted_by_hand_is_not_an_error(writer, make_incoming):
    # A KeyError used to escape add() here, and the import doesn't catch KeyError.
    writer.add(make_incoming(b"kept"))
    gone = writer.add(make_incoming(b"deleted")).recording
    shutil.rmtree(writer.archive.path_for(gone.id))
    again = writer.add(make_incoming(b"deleted", recorded_at=NEXT_DAY))
    assert again.created and again.recording.id != gone.id
    assert writer.index.find_by_sha256(gone.media.sha256) == again.recording.id
    assert writer.index.count() == 2


def test_a_killed_writers_assembly_is_swept_when_its_lock_is_free(writer, make_incoming):
    # Review Focus 2: a writer killed mid-assembly leaves .tmp/<id>.<hex>; the next writer
    # removes it, unless that recording's lock is still held.
    rid = "20261006T140003-0700_deadbeef"
    stray = writer.root / ".tmp" / f"{rid}.0123"
    (stray / "source").mkdir(parents=True)
    held = writer.root / ".tmp" / "20261007T140003-0700_cafebabe.4567"
    held.mkdir()
    with writer.locks.hold("20261007T140003-0700_cafebabe"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")
    assert not stray.exists() and held.exists()


def test_reindex_refuses_to_empty_the_index(writer, make_incoming, tmp_path):
    writer.add(make_incoming())
    (writer.root / "recordings").rename(tmp_path / "moved-away")
    (writer.root / "recordings").mkdir()
    with pytest.raises(IndexRefused):
        writer.reindex()
    assert writer.index.count() == 1


SCRIPT = r"""
import sys
from pathlib import Path
from recordings.index import Index
from recordings.models import TagRef
from recordings.state import State
from recordings.writer import AddTags, Writer
root, state, index, rid, prefix, n = sys.argv[1:7]
w = Writer(Path(root), State.open(Path(state)), Index(Path(index)), writer_id="test")
for i in range(int(n)):
    w.mutate(rid, AddTags((TagRef(tag=f"{prefix}{i}"),)))
"""


def test_two_writer_processes_never_lose_an_update(writer, make_incoming):
    # why: §9.1.1 asks for a lost-update test with two writer processes.
    rec = writer.add(make_incoming()).recording
    args = [str(writer.root), str(writer.state.path), str(writer.index.path), rec.id]
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT, *args, prefix, "25"])
             for prefix in ("a", "b")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    final = writer.archive.load(rec.id)
    assert len(final.tags) == 50 and final.rev == 51
