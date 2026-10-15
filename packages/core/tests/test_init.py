import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from recordings import init as init_module
from recordings import state as state_module
from recordings.cli import main
from recordings.init import InitError, adopt_archive, init_archive
from recordings.selfdoc import validate
from recordings.sentinel import (
    MIRROR,
    SENTINEL,
    ArchiveIdentity,
    SentinelError,
    read_sentinel,
    write_sentinel,
)
from recordings.state import MIGRATIONS, SCHEMA_VERSION, State, StateError

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
UUID = "5f0c8e2a-3b1d-4c6e-9a7f-1d2e3f405162"


def test_init_sets_up_an_empty_archive_and_its_state(tmp_path):
    root, state = tmp_path / "archive", tmp_path / "state"
    identity = init_archive(root, state, writer_id="homelab", archive_uuid=UUID, created_at=T0)
    assert identity == ArchiveIdentity(uuid=UUID, created_at=T0)
    assert read_sentinel(root) == identity
    assert (root / "recordings").is_dir() and (root / "FORMAT.md").is_file()
    st = State.open(state)
    assert st.meta("archive_uuid") == UUID and st.meta("writer_id") == "homelab"
    assert st.locks_dir.is_dir()
    assert validate(root) == []


def test_init_refuses_a_folder_that_is_not_empty(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / "something.txt").write_text("x", encoding="utf-8")
    with pytest.raises(InitError, match="not empty"):
        init_archive(root, tmp_path / "state", writer_id="homelab")
    assert not (root / SENTINEL).exists()


def test_init_runs_once_per_state_folder(new_archive, tmp_path):
    _, state = new_archive
    with pytest.raises(InitError, match="already"):
        init_archive(tmp_path / "other", state, writer_id="test")


@pytest.mark.parametrize("bad", ["", "Homelab", "my homelab", "-x", "a" * 64])
def test_init_refuses_a_bad_writer_id(tmp_path, bad):
    with pytest.raises(InitError, match="writer_id"):
        init_archive(tmp_path / "archive", tmp_path / "state", writer_id=bad)


def test_the_sentinel_is_never_replaced(new_archive):
    root, _ = new_archive
    before = (root / SENTINEL).read_bytes()
    with pytest.raises(FileExistsError):
        write_sentinel(root, ArchiveIdentity(uuid=UUID, created_at=T0))
    assert (root / SENTINEL).read_bytes() == before


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        json.dumps({"format": "something-else@1", "uuid": UUID, "created_at": T0.isoformat()}),
        json.dumps(
            {"format": "recordings-archive@1", "uuid": "nope", "created_at": T0.isoformat()}
        ),
        json.dumps({"format": "recordings-archive@1", "uuid": 7, "created_at": T0.isoformat()}),
        json.dumps(
            {"format": "recordings-archive@1", "uuid": UUID, "created_at": "2026-10-08T12:00:00"}
        ),
    ],
    ids=["truncated", "not-an-object", "wrong-format", "bad-uuid", "uuid-not-text", "naive-time"],
)
def test_a_damaged_sentinel_is_refused(tmp_path, text):
    (tmp_path / SENTINEL).write_text(text, encoding="utf-8")
    with pytest.raises(SentinelError):
        read_sentinel(tmp_path)


def test_a_missing_sentinel_asks_whether_the_disk_is_mounted(tmp_path):
    with pytest.raises(SentinelError, match="mounted"):
        read_sentinel(tmp_path)


def test_a_machine_without_state_is_not_the_writer(tmp_path):
    with pytest.raises(StateError, match="not the archive's writer"):
        State.open(tmp_path / "state")


@pytest.mark.parametrize("content", [b"not a database " * 100, b""], ids=["garbage", "no-tables"])
def test_a_damaged_state_db_is_refused_with_the_way_back(tmp_path, content):
    # why: a bare sqlite3.DatabaseError would reach the CLI as a traceback, with no way forward.
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.db").write_bytes(content)
    with pytest.raises(StateError, match="Restoring for real"):
        State.open(tmp_path / "state")


def test_state_never_creates_a_state_db_by_accident(tmp_path):
    # why: a plain connect makes an empty file, which later passes for a state.db.
    (tmp_path / "state").mkdir()
    with pytest.raises(sqlite3.OperationalError):
        State(tmp_path / "state").meta("writer_id")
    assert not (tmp_path / "state" / "state.db").exists()


def test_two_inits_racing_for_one_state_folder_fail_cleanly(tmp_path, monkeypatch):
    # why: both can pass the exists() check. The loser gets a StateError, not an IntegrityError.
    real_connect = State._connect

    def rival_wins(self, mode):
        monkeypatch.setattr(State, "_connect", real_connect)
        State.create(self.path, archive_uuid=UUID, writer_id="rival")
        return real_connect(self, mode)

    monkeypatch.setattr(State, "_connect", rival_wins)
    with pytest.raises(StateError, match="already exists"):
        State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    assert State.open(tmp_path / "state").meta("writer_id") == "rival"


def test_a_state_db_newer_than_the_code_is_refused(tmp_path):
    # why: a rollback after a deploy. Older code would misread tables it doesn't know.
    st = State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    st.set_meta("schema_version", str(SCHEMA_VERSION + 1))
    with pytest.raises(StateError, match="newer"):
        State.open(tmp_path / "state")
    assert st.meta("schema_version") == str(SCHEMA_VERSION + 1)


def test_open_runs_the_numbered_migrations_in_order(tmp_path, monkeypatch):
    # why: CREATE TABLE IF NOT EXISTS can't add a column, so a later stage changes a table this way.
    State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    monkeypatch.setattr(
        state_module,
        "MIGRATIONS",
        {2: ("CREATE TABLE extra (x TEXT)",), 3: ("ALTER TABLE extra ADD COLUMN y TEXT",)},
    )
    monkeypatch.setattr(state_module, "SCHEMA_VERSION", 3)
    st = State.open(tmp_path / "state")
    State.open(tmp_path / "state")  # a second open finds nothing left to do
    assert st.meta("schema_version") == "3"
    with st.db() as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(extra)")] == ["x", "y"]


def test_the_migrations_are_numbered_from_2_without_gaps():
    assert sorted(MIGRATIONS) == list(range(2, SCHEMA_VERSION + 1))


def test_an_interrupted_init_writes_no_sentinel_and_never_says_remove(tmp_path, monkeypatch):
    # why: "Remove <archive>" is one wrong path away from deleting a real archive.
    def failing_docs(root):
        raise OSError("Simulated disk full")

    monkeypatch.setattr(init_module, "write_docs", failing_docs)
    with pytest.raises(InitError) as caught:
        init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    assert "move both aside" in str(caught.value) and "Remove" not in str(caught.value)
    assert not (tmp_path / "archive" / SENTINEL).exists()


def test_adopt_recreates_a_lost_state_db_from_the_sentinel(new_archive):
    # why: §6.8. Without it, a lost state.db with no usable backup leaves the archive unwritable.
    root, state = new_archive
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    for path in state.glob("state.db*"):
        path.unlink()
    identity = adopt_archive(root, state, writer_id="test")
    assert identity == read_sentinel(root)
    st = State.open(state)
    assert (st.meta("archive_uuid"), st.meta("writer_id")) == (identity.uuid, "test")
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before  # archive untouched


def test_adopt_never_replaces_a_state_db(new_archive):
    root, state = new_archive
    with pytest.raises(InitError, match="whole state folder aside"):
        adopt_archive(root, state, writer_id="test")


def test_adopt_refuses_the_nas_mirror(new_archive, tmp_path):
    # why: §3. The mirror carries archive.json, so adopting it would give a second machine a
    # state.db that every writer check accepts. The mirror job marks its copy (Task 12).
    root, _ = new_archive
    (root / MIRROR).write_text("a copy\n", encoding="utf-8")
    with pytest.raises(InitError, match="mirror"):
        adopt_archive(root, tmp_path / "mac-state", writer_id="homelab")
    assert not (tmp_path / "mac-state").exists()


@pytest.mark.parametrize("log", ["state.db-wal", "state.db-journal"])
def test_a_new_state_db_never_meets_an_earlier_ones_log(new_archive, log):
    # why: SQLite replays a stray -wal over the next state.db put beside it, so a restored copy
    # silently stops being the backup, and deletes it beside a new, empty one, so moving state.db
    # alone and adopting destroys its latest commits. The whole state folder moves aside.
    root, state = new_archive
    (state / "state.db").rename(state.parent / "state.db.aside")
    (state / log).write_bytes(b"the latest commits")
    with pytest.raises(InitError, match="whole state folder"):
        adopt_archive(root, state, writer_id="test")
    assert (state / log).read_bytes() == b"the latest commits"
    assert not (state / "state.db").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens files whatever their mode")
def test_a_state_db_that_cant_be_opened_is_not_called_damaged(new_archive):
    # why: "restore it, or move it aside and adopt" for a sound file that a mode, an owner or a
    # full disk keeps shut would throw away the queue and every merge base.
    _, state = new_archive
    state.chmod(0o500)  # WAL can't make its -shm in a read-only folder
    try:
        with pytest.raises(StateError) as caught:
            State.open(state)
    finally:
        state.chmod(0o700)
    assert "not damage" in str(caught.value)
    assert "--adopt" not in str(caught.value) and "Restoring for real" not in str(caught.value)


def test_adopt_needs_the_archives_sentinel(tmp_path):
    (tmp_path / "archive").mkdir()
    with pytest.raises(InitError, match="mounted"):
        adopt_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    assert not (tmp_path / "state").exists()


def _config(tmp_path, monkeypatch, *, writer_id="homelab"):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "{writer_id}"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_cli_init_uses_the_config_and_runs_once(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["uuid"] == read_sentinel(tmp_path / "archive").uuid
    assert out["writer_id"] == "homelab"
    assert main(["init", "--json"]) == 1
    assert "not empty" in json.loads(capsys.readouterr().out)["error"]


def test_cli_init_adopt_recreates_a_lost_state_db(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    uuid = json.loads(capsys.readouterr().out)["uuid"]
    for path in (tmp_path / "state").glob("state.db*"):
        path.unlink()
    assert main(["init", "--adopt", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["uuid"], out["writer_id"], out["adopted"]) == (uuid, "homelab", True)
    assert main(["init", "--adopt", "--json"]) == 1
    assert "exists" in json.loads(capsys.readouterr().out)["error"]


def test_cli_init_says_what_is_missing(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[archive]\npath = "{tmp_path / "archive"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.delenv("RECORDINGS_STATE", raising=False)
    assert main(["init", "--json"]) == 78
    error = json.loads(capsys.readouterr().out)["error"]
    assert "[state] path" in error and "[archive] writer_id" in error


def test_docs_refuses_a_folder_without_a_sentinel(tmp_path, monkeypatch, capsys):
    # why: §6.7. Writing the docs into an unmounted, empty folder would make it look like an archive.
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    empty = tmp_path / "unmounted"
    empty.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{empty}"\nwriter_id = "test"\n[state]\npath = "{tmp_path / "state"}"\n'
        f'[index]\npath = "{tmp_path / "index.db"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)
    assert main(["docs", "--json"]) == 78
    assert "mounted" in json.loads(capsys.readouterr().out)["error"]
    assert list(empty.iterdir()) == []


def test_validate_reports_a_missing_sentinel(tmp_path):
    (tmp_path / "recordings").mkdir()
    (problem,) = validate(tmp_path)
    assert problem["path"] == str(tmp_path / SENTINEL)
