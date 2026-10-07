import json

from recordings.cli import main


def test_docs_then_validate_on_an_empty_archive(tmp_path, capsys):
    assert main(["docs", str(tmp_path), "--json"]) == 0
    written = json.loads(capsys.readouterr().out)["written"]
    assert "FORMAT.md" in written
    assert main(["validate", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_validate_exits_1_on_problems(tmp_path, capsys):
    folder = tmp_path / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1
