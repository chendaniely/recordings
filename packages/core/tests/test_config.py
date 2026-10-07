import json
import os
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


def test_secret_empty_file_is_an_error(tmp_path):
    f = tmp_path / "plaud_token"
    f.write_text("   \n")
    with pytest.raises(ConfigError, match="is empty"):
        secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(f)})


def test_secret_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(tmp_path / "nope")})


def test_secret_directory_instead_of_file_is_an_error(tmp_path):
    d = tmp_path / "secret_dir"
    d.mkdir()
    with pytest.raises(ConfigError, match="is not a file"):
        secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(d)})


@pytest.mark.skipif(os.getuid() == 0, reason="root can read files with no permissions")
def test_secret_unreadable_file_is_an_error(tmp_path):
    f = tmp_path / "plaud_token"
    f.write_text("sentinel-do-not-print-8c1f\n")
    f.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="could not be read"):
            secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(f)})
    finally:
        f.chmod(0o644)


def test_doctor_with_empty_secret_file(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    f = tmp_path / "secret"
    f.write_text("   \n")
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN_FILE": str(f),
    }
    report = doctor(env)
    assert report["problems"]
    assert "is empty" in report["problems"][0]
    assert report["secrets"]["plaud_token"]["set"] is False
    assert "sentinel-do-not-print-8c1f" not in json.dumps(report)


def test_doctor_with_missing_secret_file(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN_FILE": str(tmp_path / "nope"),
    }
    report = doctor(env)
    assert report["problems"]
    assert "does not exist" in report["problems"][0]
    assert report["secrets"]["plaud_token"]["set"] is False
    assert "sentinel-do-not-print-8c1f" not in json.dumps(report)


def test_doctor_with_secret_directory(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    d = tmp_path / "secret_dir"
    d.mkdir()
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN_FILE": str(d),
    }
    report = doctor(env)
    assert report["problems"]
    assert "is not a file" in report["problems"][0]
    assert report["secrets"]["plaud_token"]["set"] is False
    assert "sentinel-do-not-print-8c1f" not in json.dumps(report)


@pytest.mark.skipif(os.getuid() == 0, reason="root can read files with no permissions")
def test_doctor_with_unreadable_secret_file(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    f = tmp_path / "secret"
    f.write_text("sentinel-do-not-print-8c1f\n")
    f.chmod(0o000)
    try:
        env = {
            "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
            "RECORDINGS_PLAUD_TOKEN_FILE": str(f),
        }
        report = doctor(env)
        assert report["problems"]
        assert "could not be read" in report["problems"][0]
        assert report["secrets"]["plaud_token"]["set"] is False
        assert "sentinel-do-not-print-8c1f" not in json.dumps(report)
    finally:
        f.chmod(0o644)


def test_doctor_with_both_secret_env_and_file(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    f = tmp_path / "secret"
    f.write_text("sentinel-do-not-print-8c1f\n")
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN": "sentinel-do-not-print-8c1f",
        "RECORDINGS_PLAUD_TOKEN_FILE": str(f),
    }
    report = doctor(env)
    assert report["problems"]
    assert "not both" in report["problems"][0]
    assert report["secrets"]["plaud_token"]["set"] is False
    assert "sentinel-do-not-print-8c1f" not in json.dumps(report)


@pytest.mark.skipif(os.getuid() == 0, reason="root can read files with no permissions")
def test_cli_doctor_json_with_unreadable_secret(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    f = tmp_path / "secret"
    f.write_text("sentinel-do-not-print-8c1f\n")
    cfg = write(tmp_path, f'[archive]\npath = "{archive}"\n')
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.setenv("RECORDINGS_PLAUD_TOKEN_FILE", str(f))
    f.chmod(0o000)
    try:
        result = main(["doctor", "--json"])
        assert result == 78
        out, err = capsys.readouterr()
        assert "sentinel-do-not-print-8c1f" not in out
        assert "sentinel-do-not-print-8c1f" not in err
    finally:
        f.chmod(0o644)


@pytest.mark.skipif(os.getuid() == 0, reason="root can read files with no permissions")
def test_cli_doctor_plain_with_unreadable_secret(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    f = tmp_path / "secret"
    f.write_text("sentinel-do-not-print-8c1f\n")
    cfg = write(tmp_path, f'[archive]\npath = "{archive}"\n')
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.setenv("RECORDINGS_PLAUD_TOKEN_FILE", str(f))
    f.chmod(0o000)
    try:
        result = main(["doctor"])
        assert result == 78
        out, err = capsys.readouterr()
        assert "sentinel-do-not-print-8c1f" not in out
        assert "sentinel-do-not-print-8c1f" not in err
    finally:
        f.chmod(0o644)


def test_doctor_reports_presence_never_values(tmp_path, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN": "sentinel-do-not-print-8c1f",
    }
    report = doctor(env)
    assert report["problems"] == []
    assert report["secrets"]["plaud_token"]["set"] is True
    assert report["secrets"]["claude_oauth_token"]["set"] is False
    assert "sentinel-do-not-print-8c1f" not in json.dumps(report)


def test_cli_doctor_exits_78_when_the_archive_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RECORDINGS_ARCHIVE", raising=False)
    monkeypatch.delenv("RECORDINGS_CONFIG", raising=False)
    assert main(["doctor", "--json"]) == 78
    assert "no archive path" in json.loads(capsys.readouterr().out)["problems"][0]
