import json
import shutil

from recordings.cli import main
from recordings.init import init_archive


def _writer_config(tmp_path, monkeypatch, archive, state, index=None):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{archive}"\nwriter_id = "test"\n[state]\npath = "{state}"\n'
        f'[index]\npath = "{index or tmp_path / "index.db"}"\n', encoding="utf-8")
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
    path.write_text(path.read_text(encoding="utf-8").replace('"Week 4"', '"Edited"'),
                    encoding="utf-8")
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recordings"] == 1 and out["invalid"] == []
    assert out["adopted"] == [f"recordings/2026/10/{rec.id}/recording.json"]


def test_reindex_exits_1_and_names_a_recording_the_rebuild_left_out(
        writer, make_incoming, tmp_path, monkeypatch, capsys):
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

def test_reindex_names_an_archive_it_cannot_write_docs_into(
        writer, tmp_path, monkeypatch, capsys):
    # why: a read-only archive mount or a full disk is an OSError out of write_docs; the command
    # names it and exits, it does not print a traceback.
    def refuse(root):
        raise PermissionError(13, "Read-only file system", str(root))

    monkeypatch.setattr("recordings.writer.write_docs", refuse)
    _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
    assert main(["reindex", "--json"]) == 78
    assert "Read-only file system" in json.loads(capsys.readouterr().out)["error"]


def test_validate_exits_1_on_problems(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    folder = tmp_path / "a" / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1
