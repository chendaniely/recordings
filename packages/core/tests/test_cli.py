import json
import shutil
from datetime import UTC, datetime

from recordings.archive import RawSource
from recordings.cli import main
from recordings.init import init_archive
from recordings.locks import LockTimeout
from recordings.writer import Writer


def _writer_config(tmp_path, monkeypatch, archive, state, index=None):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{archive}"\nwriter_id = "test"\n[state]\npath = "{state}"\n'
        f'[index]\npath = "{index or tmp_path / "index.db"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_docs_then_validate_on_a_new_archive(tmp_path, monkeypatch, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    _writer_config(tmp_path, monkeypatch, tmp_path / "a", tmp_path / "state")
    (tmp_path / "a" / "FORMAT.md").unlink()
    assert main(["docs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == ["FORMAT.md"]
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_docs_are_never_written_into_the_macs_mirror(tmp_path, monkeypatch, capsys):
    # why: §6.7. The mirror carries archive.json too, so checking the sentinel alone let
    # `recordings docs` write into it. Only the archive's writer writes, through Writer.open.
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    mirror = tmp_path / "mirror"
    shutil.copytree(tmp_path / "a", mirror)
    (mirror / "FORMAT.md").unlink()
    _writer_config(tmp_path, monkeypatch, tmp_path / "a", tmp_path / "state")
    assert main(["docs", str(mirror), "--json"]) == 78  # not the configured archive
    assert "not this machine's archive" in json.loads(capsys.readouterr().out)["error"]
    _writer_config(tmp_path, monkeypatch, mirror, tmp_path / "mac-state")  # the Mac: no state.db
    assert main(["docs", "--json"]) == 78
    assert "state.db" in json.loads(capsys.readouterr().out)["error"]
    assert not (mirror / "FORMAT.md").exists()


def test_reindex_accepts_an_outside_edit(writer, make_incoming, tmp_path, monkeypatch, capsys):
    rec = writer.add(make_incoming(sources=())).recording  # no Plaud source to reconcile
    path = writer.archive.path_for(rec.id) / "recording.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"Week 4"', '"Edited"'), encoding="utf-8"
    )
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recordings"] == 1 and out["invalid"] == []
    assert out["adopted"] == [f"recordings/2026/10/{rec.id}/recording.json"]


def test_reindex_exits_1_and_names_a_recording_the_rebuild_left_out(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    # why: §11. Index.rebuild drops a recording it can't list; reindex must say so, not exit 0.
    rec = writer.add(make_incoming(sources=())).recording
    folder = writer.archive.path_for(rec.id)
    shutil.copytree(folder, folder.parent / "20261031T090000-0700_deadbeef")  # a copied folder
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["recordings"] == 1 and out["invalid"] == []
    (problem,) = out["problems"]
    assert "does not match its folder" in problem["message"]


def test_reindex_writes_no_docs_so_a_read_only_archive_cannot_stop_it(
    writer, tmp_path, monkeypatch, capsys
):
    # why: reindex is the recovery command and writes nothing into the archive. A docs write
    # failure (read-only mount, full disk) must not stop it before it adopts anything.
    def refuse(root):
        raise PermissionError(13, "Read-only file system", str(root))

    monkeypatch.setattr("recordings.writer.write_docs", refuse)
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["invalid"] == []


def test_reindex_names_an_index_it_cannot_rebuild(writer, tmp_path, monkeypatch, capsys):
    # why: a full disk or a bad mode is an OSError out of the rebuild; the command names it and
    # exits 1, it does not print a traceback.
    def refuse(self, **kwargs):
        raise PermissionError(13, "Read-only file system", "index.db")

    monkeypatch.setattr("recordings.writer.Writer.rebuild_index", refuse)
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    assert "Read-only file system" in json.loads(capsys.readouterr().out)["error"]


def test_reindex_refuses_when_recordings_state_db_knows_are_gone(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    # why: a half-mounted or partly restored archive lists fewer recordings, not none, so the
    # empty-index guard doesn't fire. Rebuilding would drop them from the index, and an import
    # could then make a second copy of their bytes (§6.4).
    keep = writer.add(make_incoming(sources=())).recording
    gone = writer.add(make_incoming(sources=())).recording
    shutil.move(writer.archive.path_for(gone.id), tmp_path / "aside")
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    message = json.loads(capsys.readouterr().out)["error"]
    assert gone.id in message and keep.id not in message
    assert writer.index.count() == 2  # the index is untouched
    assert main(["reindex", "--allow-missing", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["forgotten"] == [gone.id]
    assert writer.index.count() == 1
    assert main(["reindex", "--json"]) == 0  # forgotten once: the next plain reindex is clean


def test_validate_exits_1_on_problems(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    folder = tmp_path / "a" / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1


def test_reindex_allow_missing_works_when_index_db_is_gone(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    # why: the runbook's restore does not bring index.db back, and a hand deletion needs
    # --allow-missing. The command must not stop at the Writer's own start-up rebuild (exit 78).
    writer.add(make_incoming(sources=()))
    gone = writer.add(make_incoming(sources=())).recording
    shutil.rmtree(writer.archive.path_for(gone.id))
    writer.index.path.unlink()
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    assert gone.id in json.loads(capsys.readouterr().out)["error"]
    assert main(["reindex", "--allow-missing", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["forgotten"] == [gone.id]
    assert main(["reindex", "--json"]) == 0


def test_reindex_allow_missing_works_with_an_index_update_pending(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    keep = writer.add(make_incoming(sources=())).recording
    gone = writer.add(make_incoming(sources=())).recording
    shutil.rmtree(writer.archive.path_for(gone.id))
    writer.state.mark_index_pending(keep.id)
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    capsys.readouterr()
    assert main(["reindex", "--allow-missing", "--json"]) == 0
    assert main(["reindex", "--json"]) == 0
    assert writer.state.index_pending() == []


def test_reindex_exits_1_when_a_plaud_recording_could_not_be_reconciled(
    writer, make_incoming, plaud_env, tmp_path, monkeypatch, capsys
):
    # why: that recording's outputs are missing, so a script must be told, as it is for an
    # invalid file. The message ("busy") must not reach the report.
    when = datetime(2026, 8, 1, 16, 10, tzinfo=UTC)
    writer.add(
        make_incoming(
            sources=(
                RawSource(
                    kind="plaud",
                    ref="0123456789abcdef0123456789abcdef",
                    added_at=when,
                    fetched_at=when,
                    payload=json.dumps(plaud_env()).encode(),
                ),
            )
        )
    )

    def refuse(self, recording_id):
        raise LockTimeout("busy")

    monkeypatch.setattr(Writer, "reconcile_plaud", refuse)
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["plaud"]["problems"] == 1
    assert "busy" not in json.dumps(out)


def test_the_cli_refuses_abbreviated_flags():
    # why: argparse accepts `doctor --rol web` until a second option shares the prefix, so a flag
    # renamed to an extension of the old one would keep the runbook "working" by accident. The
    # runbook test parses with this parser, so it must not guess.
    import pytest
    from recordings.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["doctor", "--role", "web"]).role == "web"
    for argv in (
        ["doctor", "--rol", "web"],
        ["backup", "run", "--ta", "x"],
        ["validate", "x", "--dee"],
        ["init", "--ado"],
        ["mirror", "--max-del", "3"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
