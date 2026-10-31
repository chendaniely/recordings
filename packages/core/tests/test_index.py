import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from recordings import index as index_module
from recordings.archive import Archive
from recordings.ids import make_id, relative_dir
from recordings.index import Index, IndexRefused
from recordings.models import MediaInfo, Recording, SourceRef, dump_json
from recordings.refs import canonical_plaud_id, canonical_ref
from recordings.state import State
from recordings.writer import Writer

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
HEX = "0123456789abcdef0123456789abcdef"
# The first 100 bytes of a file that says it is an index (its application_id at offset 68), with a
# page size of 0, so SQLite can't read a page of it.
INDEX_HEADER = (
    b"SQLite format 3\x00" + bytes(52) + index_module.APPLICATION_ID.to_bytes(4, "big") + bytes(28)
)


def put(root, *, when="2026-10-06T14:00:03-07:00", sha="a" * 64, sources=()) -> Recording:
    """A minimal valid recording.json, written by hand: the index only reads files."""
    recorded_at = datetime.fromisoformat(when)
    rid = make_id(recorded_at, sha)
    folder = root / relative_dir(rid)
    folder.mkdir(parents=True)
    rec = Recording(
        id=rid,
        title="x",
        recorded_at=recorded_at,
        timezone="UTC",
        time_source="ingest",
        media=MediaInfo(file=f"{rid}.mp3", sha256=sha, kind="audio"),
        sources=[SourceRef(kind=k, ref=r, added_at=T0) for k, r in sources],
    )
    (folder / "recording.json").write_text(dump_json(rec), encoding="utf-8")
    return rec


@pytest.mark.parametrize(
    "ref, canonical",
    [
        (HEX, HEX),
        ("of_" + HEX, HEX),
        ("OF_" + HEX.upper(), HEX),
        (" of_" + HEX + " ", HEX),
        ("demo-apollo13", "demo-apollo13"),
        ("of_xyz", "of_xyz"),
    ],
)
def test_both_plaud_id_forms_are_one_id(ref, canonical):
    # why: §6.3. Plaud's listing switched from bare hex to of_<hex> in September 2026.
    assert canonical_plaud_id(ref) == canonical


def test_other_sources_keep_their_references_as_they_are():
    assert canonical_ref("recorder", "Of_" + HEX) == "Of_" + HEX
    assert canonical_ref("plaud", "of_" + HEX) == HEX


def test_rebuild_then_look_up_by_hash_and_by_either_plaud_id_form(new_archive, tmp_path):
    root, _ = new_archive
    a = put(root, sha="a" * 64, sources=[("plaud", HEX), ("audio_router", f"plaud/{HEX}")])
    b = put(
        root,
        when="2026-10-07T09:00:00-07:00",
        sha="b" * 64,
        sources=[("pocket", "11111111-1111-4111-8111-111111111111")],
    )
    index = Index(tmp_path / "index" / "index.db")
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 2
    assert (index.count(), index.archive_uuid()) == (2, "u-1")
    assert index.find_by_sha256("b" * 64) == b.id
    assert index.sha256_of(a.id) == "a" * 64
    assert index.find_by_sha256("c" * 64) is None
    assert index.find_by_source("plaud", "of_" + HEX) == [a.id]
    assert index.find_by_source("audio_router", f"plaud/{HEX}") == [a.id]
    assert index.find_by_source("plaud", "f" * 32) == []


def test_index_db_is_in_wal_mode(new_archive, tmp_path):
    # why: 2b spec §9.5 (2a rulings, item 72d). web and the worker each hold a Writer: in WAL mode
    # one upserts or rebuilds while the other reads, instead of waiting on a whole-file lock.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index" / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    with closing(sqlite3.connect(index.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    index.upsert(rec)  # a later writer's connection keeps the mode the file holds
    assert index.find_by_sha256(rec.media.sha256) == rec.id


def test_a_writer_switches_this_archives_index_to_wal(writer):
    # why: the plan council's R6 and BA1. A Writer rebuilds only a new, damaged or foreign index,
    # so the valid index a 2a deploy left, in rollback-journal mode, would have stayed so: a Writer
    # that finds the index this archive's switches it.
    with closing(sqlite3.connect(writer.index.path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")  # as stage 2a left it
    Writer(writer.root, writer.state, Index(writer.index.path), writer_id="test")
    with closing(sqlite3.connect(writer.index.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_switch_to_wal_that_cant_happen_now_is_logged_and_tried_again(
    new_archive, tmp_path, caplog
):
    # why: the plan council's BA5. The switch needs the file to itself, so it fails while another
    # process reads it (web, as the worker starts). WAL only speeds things up: the rebuild or the
    # Writer carries on, a fixed phrase is logged, and the next one tries again.
    root, _ = new_archive
    put(root)
    index = Index(tmp_path / "index" / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    with closing(sqlite3.connect(index.path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    reader = sqlite3.connect(index.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM recordings").fetchone()  # a read in progress
        with caplog.at_level("WARNING", logger="recordings.index"):
            assert index.wal(timeout=0.05) is False
    finally:
        reader.close()
    assert "index.db stays in rollback-journal mode for now (OperationalError)" in caplog.text
    assert index.wal() is True
    with closing(sqlite3.connect(index.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_missing_index_answers_nothing(tmp_path):
    index = Index(tmp_path / "nope" / "index.db")
    assert (index.exists(), index.count(), index.archive_uuid()) == (False, 0, None)
    assert index.find_by_sha256("a" * 64) is None and index.find_by_source("plaud", HEX) == []


def test_rebuild_refuses_to_empty_a_non_empty_index(new_archive, tmp_path):
    # why: §6.7. An archive that suddenly lists nothing is more likely unmounted than empty.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    empty = tmp_path / "empty"
    (empty / "recordings").mkdir(parents=True)
    with pytest.raises(IndexRefused, match="refusing to empty"):
        index.rebuild(Archive(empty), archive_uuid="u-1")
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.rebuild(Archive(empty), archive_uuid="u-1", allow_empty=True) == 0
    assert index.count() == 0


def test_a_failed_rebuild_leaves_the_old_index_and_nothing_else(new_archive, tmp_path, monkeypatch):
    # why: the failure comes after the old tables are dropped, inside the one transaction.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    put(root, when="2026-10-07T09:00:00-07:00", sha="b" * 64)

    def failing_insert(db, recording):
        raise OSError("Simulated disk full")

    monkeypatch.setattr(index_module, "_insert", failing_insert)
    with pytest.raises(OSError, match="Simulated disk full"):
        index.rebuild(Archive(root), archive_uuid="u-1")
    assert index.count() == 1 and index.find_by_sha256(rec.media.sha256) == rec.id
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive", "index.db", "state"]


def test_a_garbage_index_is_rebuilt(new_archive, tmp_path):
    # why: §6.8. index.db is derived, so a corrupted SQLite file is renamed aside and rebuilt.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.path.write_bytes(INDEX_HEADER + b"corrupted data " * 100)
    assert (index.count(), index.archive_uuid()) == (0, None)
    assert index.find_by_source("plaud", HEX) == []
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.find_by_source("plaud", HEX) == [rec.id]
    renamed_files = list(tmp_path.glob("index.db.unreadable-*"))
    assert len(renamed_files) == 1


def test_rebuild_never_replaces_a_database_that_is_not_an_index(new_archive, tmp_path):
    # why: the rebuild drops its tables in place. A mistyped [index] path must not take state.db's
    # tables with it.
    root, state = new_archive
    put(root)
    with pytest.raises(IndexRefused, match="not an index"):
        Index(state / "state.db").rebuild(Archive(root), archive_uuid="u-1")
    assert State.open(state).meta("writer_id") == "test"


def test_a_database_written_during_the_probe_is_refused_not_quarantined(new_archive, monkeypatch):
    # why: the immutable probe can misread a file that changes under it, and state.db's writers
    # don't take the index lock. A mistyped [index] path must still be refused, never renamed.
    root, state = new_archive
    put(root)
    db = state / "state.db"
    original = db.read_bytes()

    def written_meanwhile(self):  # another process writes while the probe reads it torn
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        return False

    monkeypatch.setattr(Index, "_readable", written_meanwhile)
    with pytest.raises(IndexRefused, match="not an index"):
        Index(db).rebuild(Archive(root), archive_uuid="u-1")
    assert db.read_bytes() == original
    assert list(state.glob("state.db*.unreadable-*")) == []
    assert State.open(state).meta("writer_id") == "test"


def test_upsert_replaces_a_recordings_references(new_archive, tmp_path):
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    moved = rec.model_copy(
        update={"sources": [SourceRef(kind="recorder", ref="r-1", added_at=T0)], "rev": 3}
    )
    index.upsert(moved)
    assert index.find_by_source("plaud", HEX) == []
    assert index.find_by_source("recorder", "r-1") == [rec.id]
    assert index.count() == 1


def test_rebuild_refuses_to_empty_a_locked_index(new_archive, tmp_path, monkeypatch):
    # why: The old code read the count outside the transaction, where a locked index read as 0.
    # This patch reproduces that read, so the test fails on the old code and passes now.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    monkeypatch.setattr(Index, "count", lambda self: 0)
    empty = tmp_path / "empty"
    (empty / "recordings").mkdir(parents=True)
    with pytest.raises(IndexRefused, match="refusing to empty"):
        index.rebuild(Archive(empty), archive_uuid="u-1")
    monkeypatch.undo()
    assert index.count() == 1 and index.find_by_sha256(rec.media.sha256) == rec.id


def test_non_sqlite_file_at_index_path_is_refused_and_untouched(tmp_path):
    # why: never delete a file we're not sure about.
    index = Index(tmp_path / "index.db")
    index.path.write_bytes(b"ID3 not a database " * 10)
    original_bytes = index.path.read_bytes()
    with pytest.raises(IndexRefused, match="not an index"):
        index.rebuild(Archive(tmp_path / "nope"), archive_uuid="u-1")
    # File must be untouched
    assert index.path.read_bytes() == original_bytes


def test_corrupted_sqlite_file_is_renamed_aside_and_rebuilt(new_archive, tmp_path):
    # why: index.db is derived; damaged SQLite is renamed aside and rebuilt, never deleted.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    header = INDEX_HEADER
    index.path.write_bytes(header + b"corrupted data" * 100)
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    renamed_files = list(tmp_path.glob("index.db.unreadable-*"))
    assert len(renamed_files) == 1
    assert renamed_files[0].read_bytes() == header + b"corrupted data" * 100
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.find_by_source("plaud", HEX) == [rec.id]


def test_quarantine_name_collision_is_avoided(new_archive, tmp_path, monkeypatch):
    # why: Path.rename overwrites on POSIX, so two rebuilds in the same second would collide.
    # Make the suffix collision-safe with -2, -3, etc.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    # Create a pre-existing quarantine file with a fixed timestamp
    header = INDEX_HEADER
    fixed_stamp = "20261009T143512Z"
    pre_existing = tmp_path / f"index.db.unreadable-{fixed_stamp}"
    pre_existing.write_bytes(b"pre-existing data")
    # Patch the datetime module's now method to return a fixed timestamp
    import recordings.index

    fixed_dt = datetime(2026, 10, 9, 14, 35, 12, tzinfo=UTC)
    mock_datetime = Mock()
    mock_datetime.now.return_value = fixed_dt
    monkeypatch.setattr(recordings.index, "datetime", mock_datetime)
    # Corrupt the index and rebuild
    index.path.write_bytes(header + b"new corrupted data" * 100)
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    # Both files should exist with their original bytes
    assert pre_existing.read_bytes() == b"pre-existing data"
    new_file = tmp_path / f"index.db.unreadable-{fixed_stamp}-2"
    assert new_file.exists()
    assert b"new corrupted data" in new_file.read_bytes()
    assert index.find_by_sha256(rec.media.sha256) == rec.id


def test_corrupted_index_quarantines_every_sibling_untouched(new_archive, tmp_path):
    # why: the -wal, -shm and -journal beside an unreadable index are evidence too. They move with
    # the main file under one suffix, byte for byte; the readability probe must not alter them.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    original = {
        "": INDEX_HEADER + b"garbage" * 50,
        "-wal": b"wal bytes " * 40,
        "-shm": b"shm bytes " * 40,
        "-journal": b"journal bytes " * 40,
    }
    for sibling, data in original.items():
        (tmp_path / f"index.db{sibling}").write_bytes(data)
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    main = list(tmp_path.glob("index.db.unreadable-*"))
    assert len(main) == 1
    suffix = main[0].name[len("index.db") :]
    assert suffix.startswith(".unreadable-")
    for sibling, data in original.items():
        assert (tmp_path / f"index.db{sibling}{suffix}").read_bytes() == data
    assert not (tmp_path / "index.db-journal").exists()
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.count() == 1


def test_empty_index_file_is_replaced_not_renamed(new_archive, tmp_path):
    # why: a 0-byte index.db can be safely replaced, no quarantine needed.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    # Write an empty file
    index.path.write_bytes(b"")
    # Rebuild
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    # No quarantine files should exist
    quarantined = list(tmp_path.glob("index.db.unreadable-*"))
    assert len(quarantined) == 0
    # Index should work normally
    assert index.find_by_sha256(rec.media.sha256) == rec.id


def test_a_damaged_database_that_does_not_say_it_is_an_index_is_left_alone(tmp_path):
    # why: only a file whose header says it is an index is ever renamed aside. A probe that misreads
    # a live state.db, voice.db or another program's database then can't move it, on any filesystem.
    index = Index(tmp_path / "index.db")
    data = b"SQLite format 3\x00" + b"garbage" * 50
    index.path.write_bytes(data)
    with pytest.raises(IndexRefused, match="doesn't say it is an index"):
        index.rebuild(Archive(tmp_path / "nope"), archive_uuid="u-1", allow_empty=True)
    assert index.path.read_bytes() == data
    assert list(tmp_path.glob("index.db*.unreadable-*")) == []


def test_state_db_is_never_taken_for_this_archives_index(new_archive):
    # why: state.db's meta holds this archive's archive_uuid too, so a Writer pointed at it would
    # take it for its index, skip the rebuild and find nothing on every lookup.
    _, state = new_archive
    assert Index(state / "state.db").archive_uuid() is None


def test_an_index_whose_header_lost_its_mark_is_not_this_archives(new_archive, tmp_path):
    # why: archive_uuid() answers only for a file whose header says it is an index, so a Writer
    # rebuilds one that doesn't; the rebuild writes the mark again.
    root, _ = new_archive
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1", allow_empty=True)
    assert index.path.read_bytes()[68:72] == b"RIDX" and index.archive_uuid() == "u-1"
    db = sqlite3.connect(index.path)
    db.execute("PRAGMA application_id = 0")
    db.close()
    assert index.archive_uuid() is None
    index.rebuild(Archive(root), archive_uuid="u-1", allow_empty=True)
    assert index.archive_uuid() == "u-1"


def test_an_index_written_during_the_probe_is_rebuilt_in_place_not_quarantined(
    new_archive, tmp_path, monkeypatch
):
    # why: the immutable probe can misread a file that changes under it; the stamp check keeps an
    # index that is being written from being renamed aside.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")

    def written_meanwhile(self):
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        return False

    monkeypatch.setattr(Index, "_readable", written_meanwhile)
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    assert list(tmp_path.glob("index.db*.unreadable-*")) == []
    assert index.find_by_sha256(rec.media.sha256) == rec.id


def test_a_quarantine_that_fails_part_way_never_leaves_a_wal_alone(
    new_archive, tmp_path, monkeypatch
):
    # why: the siblings move first and the file last. A -wal or -journal left beside a new, empty
    # index.db is deleted by SQLite, so a rename that fails part-way must leave the file, not them.
    root, _ = new_archive
    put(root)
    index = Index(tmp_path / "index.db")
    index.path.write_bytes(INDEX_HEADER + b"garbage" * 50)
    wal = b"wal bytes " * 40
    (tmp_path / "index.db-wal").write_bytes(wal)
    rename = Path.rename

    def failing_rename(self, target):
        if self.name == "index.db":
            raise OSError("Simulated I/O error")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(OSError, match="Simulated I/O error"):
        index.rebuild(Archive(root), archive_uuid="u-1")
    assert index.path.read_bytes().startswith(INDEX_HEADER)
    (moved,) = tmp_path.glob("index.db-wal.unreadable-*")
    assert moved.read_bytes() == wal


def test_a_strict_lookup_raises_on_a_busy_index_where_a_plain_one_answers_none(
    new_archive, tmp_path, monkeypatch
):
    # why: §6.4, §9.3 (the final review's minor 3). A lookup that waits out the busy timeout
    # answers "none", and the import's plan took that for an answer: a recording already here
    # planned as new, one the consent list now excludes left uncounted. strict raises SQLite's
    # own error instead, as find_by_sha256's has for the Writer since Task 5b.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")

    @contextmanager
    def at_once(self):  # the real lookups wait 30 s for the lock, then fail just the same
        with closing(sqlite3.connect(self.path, timeout=0)) as conn:
            yield conn

    monkeypatch.setattr(Index, "_db", at_once)
    lookups = {
        "find_by_source": lambda **kw: index.find_by_source("plaud", HEX, **kw),
        "find_by_sha256": lambda **kw: index.find_by_sha256(rec.media.sha256, **kw),
        "sha256_of": lambda **kw: index.sha256_of(rec.id, **kw),
    }
    holder = sqlite3.connect(index.path)
    # Another process holds the index. In WAL mode (2b spec §9.5) a plain writer no longer blocks
    # readers, so the holder takes the file in exclusive locking mode, which does.
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        for name, lookup in lookups.items():
            with pytest.raises(sqlite3.OperationalError) as busy:
                lookup(strict=True)
            assert busy.value.sqlite_errorname == "SQLITE_BUSY", name
            assert lookup() in ([], None), name  # without strict: "none", as before
    finally:
        holder.rollback()
        holder.close()
    assert {name: lookup(strict=True) for name, lookup in lookups.items()} == {
        "find_by_source": [rec.id],
        "find_by_sha256": rec.id,
        "sha256_of": rec.media.sha256,
    }


def test_a_switch_to_wal_that_sqlite_refuses_in_any_form_is_logged(
    new_archive, tmp_path, caplog, monkeypatch
):
    # why: the review of Task 6. A DatabaseError (a race with another process) or an answer other
    # than "wal" is not an OperationalError, and used to escape or pass without a word.
    root, _ = new_archive
    put(root)
    index = Index(tmp_path / "index" / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    phrase = "index.db stays in rollback-journal mode for now"

    def raises(*args, **kwargs):
        raise sqlite3.DatabaseError("file is not a database")

    class Answers:
        def execute(self, sql):
            return self

        def fetchone(self):
            return ("delete",)

        def close(self):
            pass

    with caplog.at_level("WARNING", logger="recordings.index"):
        monkeypatch.setattr(index_module.sqlite3, "connect", raises)
        assert index.wal() is False
        assert f"{phrase} (DatabaseError)" in caplog.text
        caplog.clear()
        monkeypatch.setattr(index_module.sqlite3, "connect", lambda *a, **k: Answers())
        assert index.wal() is False
        assert phrase in caplog.text and "delete" not in caplog.text


def test_a_writer_opens_when_the_switch_to_wal_is_refused(writer, monkeypatch, caplog):
    # why: WAL only speeds things up; a reader holding the index (web) must not stop a Writer.
    with closing(sqlite3.connect(writer.index.path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    original = Index.wal
    monkeypatch.setattr(Index, "wal", lambda self, *, timeout=5.0: original(self, timeout=0.05))
    reader = sqlite3.connect(writer.index.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM recordings").fetchone()
        with caplog.at_level("WARNING", logger="recordings.index"):
            again = Writer(writer.root, writer.state, Index(writer.index.path), writer_id="test")
    finally:
        reader.close()
    assert again.index.path == writer.index.path
    assert "index.db stays in rollback-journal mode for now (OperationalError)" in caplog.text
    with closing(sqlite3.connect(writer.index.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
