import json

from recordings.cli import main
from recordings.init import init_archive


def test_docs_then_validate_on_a_new_archive(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    (tmp_path / "a" / "FORMAT.md").unlink()
    assert main(["docs", str(tmp_path / "a"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == ["FORMAT.md"]
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_validate_exits_1_on_problems(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    folder = tmp_path / "a" / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1
