from datetime import datetime, timezone
from threading import Thread
import time

import pytest
import sqlite3

from recordings import index as index_module
from recordings.archive import Archive
from recordings.ids import make_id, relative_dir
from recordings.index import Index, IndexRefused
from recordings.models import MediaInfo, Recording, SourceRef, dump_json
from recordings.refs import canonical_plaud_id, canonical_ref
from recordings.state import State

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
HEX = "0123456789abcdef0123456789abcdef"


def put(root, *, when="2026-10-06T14:00:03-07:00", sha="a" * 64, sources=()) -> Recording:
    """A minimal valid recording.json, written by hand: the index only reads files."""
    recorded_at = datetime.fromisoformat(when)
    rid = make_id(recorded_at, sha)
    folder = root / relative_dir(rid)
    folder.mkdir(parents=True)
    rec = Recording(id=rid, title="x", recorded_at=recorded_at, timezone="UTC",
                    time_source="ingest",
                    media=MediaInfo(file=f"{rid}.mp3", sha256=sha, kind="audio"),
                    sources=[SourceRef(kind=k, ref=r, added_at=T0) for k, r in sources])
    (folder / "recording.json").write_text(dump_json(rec), encoding="utf-8")
    return rec


@pytest.mark.parametrize("ref, canonical", [
    (HEX, HEX), ("of_" + HEX, HEX), ("OF_" + HEX.upper(), HEX), (" of_" + HEX + " ", HEX),
    ("demo-apollo13", "demo-apollo13"), ("of_xyz", "of_xyz"),
])
def test_both_plaud_id_forms_are_one_id(ref, canonical):
    # why: §6.3. Plaud's listing switched from bare hex to of_<hex> in September 2026.
    assert canonical_plaud_id(ref) == canonical


def test_other_sources_keep_their_references_as_they_are():
    assert canonical_ref("recorder", "Of_" + HEX) == "Of_" + HEX
    assert canonical_ref("plaud", "of_" + HEX) == HEX


def test_rebuild_then_look_up_by_hash_and_by_either_plaud_id_form(new_archive, tmp_path):
    root, _ = new_archive
    a = put(root, sha="a" * 64, sources=[("plaud", HEX), ("audio_router", f"plaud/{HEX}")])
    b = put(root, when="2026-10-07T09:00:00-07:00", sha="b" * 64,
            sources=[("pocket", "11111111-1111-4111-8111-111111111111")])
    index = Index(tmp_path / "index" / "index.db")
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 2
    assert (index.count(), index.archive_uuid()) == (2, "u-1")
    assert index.find_by_sha256("b" * 64) == b.id
    assert index.sha256_of(a.id) == "a" * 64
    assert index.find_by_sha256("c" * 64) is None
    assert index.find_by_source("plaud", "of_" + HEX) == [a.id]
    assert index.find_by_source("audio_router", f"plaud/{HEX}") == [a.id]
    assert index.find_by_source("plaud", "f" * 32) == []


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
    # why: §6.8. index.db is derived, so a file SQLite can't read is thrown away and rebuilt.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    # Write a file with SQLite header but corrupted content
    index.path.write_bytes(b"SQLite format 3\x00" + b"corrupted data " * 100)
    assert (index.count(), index.archive_uuid()) == (0, None)
    assert index.find_by_source("plaud", HEX) == []
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.find_by_source("plaud", HEX) == [rec.id]
    # The corrupted file should have been renamed aside
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


def test_upsert_replaces_a_recordings_references(new_archive, tmp_path):
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    moved = rec.model_copy(update={"sources": [SourceRef(kind="recorder", ref="r-1", added_at=T0)],
                                   "rev": 3})
    index.upsert(moved)
    assert index.find_by_source("plaud", HEX) == []
    assert index.find_by_source("recorder", "r-1") == [rec.id]
    assert index.count() == 1


def test_rebuild_refuses_to_empty_a_locked_index(new_archive, tmp_path):
    # why: the empty check moved inside the transaction, so a lock can't bypass the guard.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    # Now create an archive listing that is empty
    empty = tmp_path / "empty"
    (empty / "recordings").mkdir(parents=True)

    # Simulate a lock by using a separate connection that holds BEGIN IMMEDIATE.
    # Create a separate Index instance with short timeout to attempt rebuild.
    # The lock_conn is not closed, keeping the lock active.
    lock_conn = sqlite3.connect(str(index.path), timeout=0.5, check_same_thread=False)
    lock_conn.execute("BEGIN IMMEDIATE")

    def release_lock():
        time.sleep(0.1)
        lock_conn.rollback()
        lock_conn.close()

    thread = Thread(target=release_lock, daemon=True)
    thread.start()

    # Create a rebuild Index with short timeout to hit the lock
    rebuild_index = Index(tmp_path / "index.db")
    # With the lock held, rebuild should timeout or raise IndexRefused inside the transaction
    try:
        rebuild_index.rebuild(Archive(empty), archive_uuid="u-1", allow_empty=False)
        # If we get here, the lock was released and the check happened inside.
        # The rebuild should have raised IndexRefused after acquiring the lock.
        pytest.fail("Expected IndexRefused when trying to empty locked index")
    except (IndexRefused, sqlite3.OperationalError):
        # Either IndexRefused (check passed) or OperationalError (timeout waiting for lock)
        pass
    finally:
        thread.join(timeout=1)
        try:
            lock_conn.close()
        except (sqlite3.ProgrammingError, RuntimeError):
            pass

    # Index should still have the recording
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
    # why: index.db is derived; damaged SQLite is thrown away by renaming, not deleting.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    # Corrupt the SQLite file (keep header, corrupt data)
    header = b"SQLite format 3\x00"
    index.path.write_bytes(header + b"corrupted data" * 100)
    # Rebuild should rename it aside and create a new one
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    # Original file should have been renamed with a timestamp suffix
    renamed_files = list(tmp_path.glob("index.db.unreadable-*"))
    assert len(renamed_files) == 1
    assert renamed_files[0].read_bytes() == header + b"corrupted data" * 100
    # New index should be readable and find the recording
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.find_by_source("plaud", HEX) == [rec.id]
