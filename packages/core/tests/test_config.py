import json
import tomllib
from pathlib import Path

import pytest

from recordings.cli import main
from recordings.config import SECRETS, ConfigError, doctor, load_config, secret

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "config.example.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_template_parses_and_loads():
    tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    cfg = load_config({"RECORDINGS_CONFIG": str(TEMPLATE)})
    assert cfg.archive_path == Path("/srv/recordings/archive")
    assert cfg.data["models"]["claude:opus"]["backend"] == "claude"


def test_the_template_documents_every_secret_and_sets_none():
    text = TEMPLATE.read_text(encoding="utf-8")
    for spec in SECRETS.values():
        assert spec.env in text, f"{spec.env} missing from config.example.toml"
        assert f"{spec.env}=" not in text and f"{spec.env} =" not in text


def test_environment_overrides_the_file(tmp_path):
    cfg_file = write(tmp_path, '[archive]\npath = "/from/file"\n')
    env = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_ARCHIVE": "/from/env"}
    assert load_config(env).archive_path == Path("/from/env")


def test_no_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config({})
    assert cfg.path is None and cfg.archive_path is None
    assert cfg.default_timezone == "America/Vancouver"


def test_a_broken_file_names_itself(tmp_path):
    with pytest.raises(ConfigError, match="config.toml"):
        load_config({"RECORDINGS_CONFIG": str(write(tmp_path, "path = [unclosed"))})


def test_a_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config({"RECORDINGS_CONFIG": str(tmp_path / "nope.toml")})


def test_secret_from_env_or_file(tmp_path):
    assert secret("plaud_token", {}) is None
    assert secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN": "abc"}) == "abc"
    f = tmp_path / "plaud_token"
    f.write_text("from-file\n")
    assert secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(f)}) == "from-file"
    with pytest.raises(ConfigError, match="not both"):
        secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN": "a", "RECORDINGS_PLAUD_TOKEN_FILE": str(f)})


def test_doctor_reports_presence_never_values(tmp_path, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN": "sekrit-value-123",
    }
    report = doctor(env)
    assert report["problems"] == []
    assert report["secrets"]["plaud_token"]["set"] is True
    assert report["secrets"]["claude_oauth_token"]["set"] is False
    assert "sekrit-value-123" not in json.dumps(report)


def test_cli_doctor_exits_78_when_the_archive_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RECORDINGS_ARCHIVE", raising=False)
    monkeypatch.delenv("RECORDINGS_CONFIG", raising=False)
    assert main(["doctor", "--json"]) == 78
    assert "no archive path" in json.loads(capsys.readouterr().out)["problems"][0]
