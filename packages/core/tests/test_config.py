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


def test_allowed_hosts_default_to_none_listed(tmp_path):
    assert load_config({"RECORDINGS_CONFIG": str(write(tmp_path, ""))}).allowed_hosts == ()


def test_allowed_hosts_are_read_from_server(tmp_path):
    cfg = load_config({"RECORDINGS_CONFIG": str(write(
        tmp_path, '[server]\nallowed_hosts = ["my-homelab", " 100.64.0.1 "]\n'))})
    assert cfg.allowed_hosts == ("my-homelab", "100.64.0.1")


def test_the_template_documents_allowed_hosts():
    cfg = load_config({"RECORDINGS_CONFIG": str(TEMPLATE)})
    assert cfg.allowed_hosts == ("my-homelab", "my-homelab.your-tailnet.ts.net", "100.64.0.1")


@pytest.mark.parametrize("value", ['"my-homelab"', "[1]", '[""]', '["  "]', '[["a"]]', "true"])
def test_allowed_hosts_must_be_a_list_of_names(tmp_path, value):
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load_config({"RECORDINGS_CONFIG": str(write(tmp_path, f"[server]\nallowed_hosts = {value}\n"))})


def test_doctor_reports_a_bad_allowed_hosts(tmp_path):
    report = doctor({"RECORDINGS_CONFIG": str(write(tmp_path, "[server]\nallowed_hosts = 5\n"))})
    assert any("allowed_hosts" in p for p in report["problems"])


def test_writer_id_and_the_state_and_index_paths(tmp_path):
    cfg_file = write(tmp_path, '[archive]\nwriter_id = "homelab"\n[state]\npath = "/s"\n'
                               '[index]\npath = "/i/index.db"\n')
    cfg = load_config({"RECORDINGS_CONFIG": str(cfg_file)})
    assert (cfg.writer_id, cfg.state_path, cfg.index_path) == ("homelab", Path("/s"), Path("/i/index.db"))


def test_the_environment_overrides_the_state_and_index_paths(tmp_path):
    cfg_file = write(tmp_path, '[state]\npath = "/s"\n[index]\npath = "/i/index.db"\n')
    cfg = load_config({"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_STATE": "/state",
                       "RECORDINGS_INDEX": "/index/index.db"})
    assert (cfg.state_path, cfg.index_path) == (Path("/state"), Path("/index/index.db"))


def test_the_old_writer_host_key_fails_loudly(tmp_path):
    # why: it named a host, and §3 says a writer's identity never comes from a host name.
    cfg_file = write(tmp_path, '[archive]\nwriter_host = "my-homelab"\n')
    with pytest.raises(ConfigError, match="renamed writer_id"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


@pytest.mark.parametrize("value", ['"My Homelab"', '"-x"', "7"])
def test_a_bad_writer_id_is_refused(tmp_path, value):
    cfg_file = write(tmp_path, f"[archive]\nwriter_id = {value}\n")
    with pytest.raises(ConfigError, match="writer_id"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


@pytest.mark.parametrize("value", ["7", '"my-homelab:8000"', '"ftp://x"'])
def test_base_url_must_be_an_http_url(tmp_path, value):
    cfg_file = write(tmp_path, f"[server]\nbase_url = {value}\n")
    with pytest.raises(ConfigError, match="base_url"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


@pytest.mark.parametrize("line", ['path = "state"', "path = 7", "path = ['/srv']"])
def test_the_state_and_index_paths_are_absolute_text(tmp_path, line):
    # why: §3. A relative [state] path follows the folder a command runs in, so a second state.db,
    # with its own locks, is one `cd` away; a number reached Path() as a TypeError.
    cfg = tmp_path / "config.toml"
    for section in ("state", "index"):
        cfg.write_text(f"[{section}]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match=rf"\[{section}\] path"):
            load_config({"RECORDINGS_CONFIG": str(cfg)})
    with pytest.raises(ConfigError, match="RECORDINGS_STATE"):
        load_config({"RECORDINGS_CONFIG": str(cfg), "RECORDINGS_STATE": "state"})


@pytest.mark.parametrize("index", ["/srv/r/state/state.db", "/srv/r/state/index.db",
                                   "/srv/r/state/backup/state.db", "/srv/r/state"])
def test_the_index_never_lies_inside_the_state_folder(tmp_path, index):
    # why: §6.8. The state folder holds state.db, its backup copy, voice.db and the Plaud token, and
    # their writers don't take the index lock. A typo that points the index there is refused here,
    # before rebuild opens any of them; the index's header mark is the second guard.
    cfg_file = write(tmp_path, f'[state]\npath = "/srv/r/state"\n[index]\npath = "{index}"\n')
    with pytest.raises(ConfigError, match=r"\[index\] path .* inside \[state\] path"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})
    with pytest.raises(ConfigError, match="RECORDINGS_INDEX"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_STATE": "/state",
                     "RECORDINGS_INDEX": "/state/state.db"})


def test_the_index_inside_the_state_folder_through_a_symlink_is_refused(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "index").symlink_to(tmp_path / "state")
    cfg_file = write(tmp_path, f'[state]\npath = "{tmp_path / "state"}"\n'
                               f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n')
    with pytest.raises(ConfigError, match="inside"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


def test_an_index_beside_the_state_folder_is_fine(tmp_path):
    cfg_file = write(tmp_path, '[state]\npath = "/srv/r/state"\n'
                               '[index]\npath = "/srv/r/state-index/index.db"\n')
    assert load_config({"RECORDINGS_CONFIG": str(cfg_file)}).index_path == Path(
        "/srv/r/state-index/index.db")


@pytest.mark.parametrize("value, expected", [
    ("My-Homelab", "my-homelab"), (" 100.64.0.1 ", "100.64.0.1"), ("[fd7a::1]", "fd7a::1"),
    ("fd7a::1", "fd7a::1"), ("fd7a:0:0::1", "fd7a::1"), ("My.Home-lab.example", "my.home-lab.example")])
def test_host_names_are_normalised(value, expected):
    from recordings.config import normalise_host
    assert normalise_host(value) == expected


@pytest.mark.parametrize("value", ["my-homelab:8000", "http://my-homelab", "my-homelab/x", "", "a b",
                                   "a\\tb", "user@my-homelab", "my-homelab?x", "my-homelab.",
                                   "-my-homelab", "my-homelab-", "my..homelab"])
def test_allowed_hosts_with_a_port_scheme_or_path_are_refused(tmp_path, value):
    # why: stage-1 carry-over. They passed validation, never matched, and gave a bare 400.
    cfg_file = write(tmp_path, f'[server]\nallowed_hosts = ["{value}"]\n')
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})
