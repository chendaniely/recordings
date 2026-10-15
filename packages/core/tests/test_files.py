import os

import pytest
from recordings import files


def test_write_bytes_atomic_replaces_and_syncs_the_file_and_its_folder(tmp_path, monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(files.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    target = tmp_path / "recording.json"
    target.write_text("old\n", encoding="utf-8")
    files.write_bytes_atomic(target, b"new\n")
    assert target.read_bytes() == b"new\n"
    assert len(synced) == 2  # the temporary file, then the folder after the rename
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.json"]


def test_write_text_atomic_leaves_no_temp_file_when_the_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "recording.json"
    target.write_text("old\n", encoding="utf-8")

    def failing_replace(src, dst):
        raise OSError("Simulated replace failure")

    monkeypatch.setattr(files.os, "replace", failing_replace)
    with pytest.raises(OSError, match="Simulated replace failure"):
        files.write_text_atomic(target, "new\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.json"]
    assert target.read_text(encoding="utf-8") == "old\n"


def test_create_exclusive_never_replaces_a_file(tmp_path):
    path = tmp_path / "archive.json"
    files.create_exclusive(path, b"one")
    with pytest.raises(FileExistsError):
        files.create_exclusive(path, b"two")
    assert path.read_bytes() == b"one"
    assert [p.name for p in tmp_path.iterdir()] == ["archive.json"]


def test_publish_exclusive_takes_the_next_free_name(tmp_path):
    first = files.publish_exclusive(b"a", tmp_path / "x.json")
    second = files.publish_exclusive(b"b", tmp_path / "x.json")
    assert (first.name, second.name) == ("x.json", "x-2.json")
    assert first.read_bytes() == b"a" and second.read_bytes() == b"b"


def test_publish_exclusive_survives_a_race_for_the_name(tmp_path, monkeypatch):
    # why: another writer can take the name between unique_path's check and the link.
    taken = tmp_path / "x.json"
    taken.write_bytes(b"theirs")
    real_unique = files.unique_path
    calls = []

    def racing_unique(path):
        calls.append(path)
        return taken if len(calls) == 1 else real_unique(path)

    monkeypatch.setattr(files, "unique_path", racing_unique)
    published = files.publish_exclusive(b"ours", tmp_path / "x.json")
    assert taken.read_bytes() == b"theirs"
    assert published.name == "x-2.json" and published.read_bytes() == b"ours"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_a_dangling_symlink_is_a_taken_name(tmp_path):
    # why: Path.exists() follows the link, so a dangling one read as free and the link retried forever.
    link = tmp_path / "x.json"
    link.symlink_to(tmp_path / "nowhere")
    published = files.publish_exclusive(b"ours", link)
    assert published.name == "x-2.json" and published.read_bytes() == b"ours"
    assert link.is_symlink() and not (tmp_path / "nowhere").exists()


def test_publish_exclusive_gives_up_instead_of_looping(tmp_path, monkeypatch):
    taken = tmp_path / "x.json"
    taken.write_bytes(b"theirs")
    monkeypatch.setattr(files, "unique_path", lambda path: taken)
    with pytest.raises(FileExistsError, match="x.json"):
        files.publish_exclusive(b"ours", taken)
    assert taken.read_bytes() == b"theirs"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_copy_file_synced_copies_bytes_and_mtime(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"ID3 audio")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dst = tmp_path / "b.mp3"
    files.copy_file_synced(src, dst)
    assert dst.read_bytes() == b"ID3 audio"
    assert dst.stat().st_mtime == 1_700_000_000
    with pytest.raises(FileExistsError):
        files.copy_file_synced(src, dst)  # never over an existing file
