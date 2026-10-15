import json
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from recordings import doctor as doctor_module
from recordings.cli import main
from recordings.config import SECRETS
from recordings.doctor import JOB_SECRETS, doctor, read_only_mount
from recordings.init import init_archive
from recordings.sentinel import SENTINEL
from recordings.ssh import nas_free_bytes

NAS_FREE = 500 * 1024 ** 3

SENTINELS = ("pw-do-not-print", "key-do-not-print", "topic-do-not-print", "ping-do-not-print",
             "rest-do-not-print", "hunter2")
SECRET_ENV = ("RESTIC_PASSWORD_FILE", "RESTIC_REST_PASSWORD_FILE", "RECORDINGS_NAS_SSH_KEY_FILE",
              "RECORDINGS_NAS_KNOWN_HOSTS_FILE", "RECORDINGS_NTFY_TOPIC_FILE",
              "RECORDINGS_NTFY_TOKEN_FILE", "RECORDINGS_DEADMAN_URL_FILE")


def server(tmp_path, *, repository="sftp:backup@nas:/volume1/backups/recordings",
           backup_extra=""):
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="homelab")
    (tmp_path / "scratch").mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in (("pw", "pw-do-not-print"), ("key", "key-do-not-print"),
                        ("known_hosts", "nas ssh-ed25519 AAAAexample"),
                        ("topic", "topic-do-not-print-20"),
                        ("ping", "https://hc.example/ping-do-not-print"),
                        ("rest_password", "rest-do-not-print")):
        (secrets / name).write_text(value + "\n", encoding="utf-8")
        (secrets / name).chmod(0o600)  # as the runbook makes them
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n'
        f'[nas]\nssh = "backup@nas"\n'
        f'[backup]\nrepository = "{repository}"\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'nas_repo_path = "/volume1/backups/recordings"\n'
        f'extra_paths = ["/backup-extra/deploy.env"]\n'
        f'cache_dir = "{tmp_path / "cache"}"\n{backup_extra}'
        f'[mirror]\ntarget = "backup@nas:/volume1/recordings-mirror/"\n'
        f'[alerts]\nntfy_server = "https://ntfy.sh"\n', encoding="utf-8")
    return {"RECORDINGS_CONFIG": str(cfg), "RESTIC_PASSWORD_FILE": str(secrets / "pw"),
            "RECORDINGS_NAS_SSH_KEY_FILE": str(secrets / "key"),
            "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(secrets / "known_hosts"),
            "RECORDINGS_NTFY_TOPIC_FILE": str(secrets / "topic"),
            "RECORDINGS_DEADMAN_URL_FILE": str(secrets / "ping")}


def web_only(environ):
    """What the web service has: the same config, and no secrets."""
    return {k: v for k, v in environ.items() if k not in SECRET_ENV}


@pytest.fixture(autouse=True)
def tools_installed(monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture(autouse=True)
def no_docker_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_module, "SECRETS_DIR", tmp_path / "run-secrets")


@pytest.fixture(autouse=True)
def nas_answers_df(monkeypatch):
    # No test reaches a real NAS: the free-space check answers from here.
    monkeypatch.setattr(doctor_module, "nas_free_bytes", lambda nas, path: NAS_FREE)


def test_a_fully_configured_writer_has_no_problems(tmp_path):
    report = doctor(server(tmp_path))
    assert report["problems"] == [] and report["warnings"] == []
    assert report["role"] == "all" and report["user"]["passwd_entry"] is True
    assert report["archive"]["sentinel"] and report["archive"]["hard_links"]
    assert report["archive"]["read_only"] is False
    assert report["state"]["matches"] is True
    assert report["backup"] == {"transport": "sftp", "password_set": True, "prune": True,
                                "cacert_exists": None, "restore_scratch_exists": True,
                                "nas_free_check": True, "nas_free_bytes": NAS_FREE,
                                "restic_installed": True}
    assert report["mirror"] == {"remote": True, "rsync_installed": True}
    assert report["alerts"] == {"ntfy_server_set": True, "topic_set": True, "deadman_set": True}
    for name in ("restic_password", "nas_ssh_key", "nas_known_hosts", "ntfy_topic", "deadman_url"):
        assert report["secrets"][name]["set"], name
    assert report["secrets"]["nas_ssh_key"]["env"] == "RECORDINGS_NAS_SSH_KEY_FILE"


def test_doctor_never_prints_a_secret_or_the_repository(tmp_path):
    environ = server(tmp_path, repository="rest:https://recordings:hunter2@nas:8000/recordings/")
    environ["RESTIC_REST_PASSWORD_FILE"] = str(tmp_path / "secrets" / "rest_password")
    report = doctor(environ)
    assert any("credentials" in p for p in report["problems"])  # Task 11 refuses user:pass@
    text = json.dumps(report, ensure_ascii=False)
    # A topic ntfy would refuse and a ping URL without https: their errors must not echo them.
    (tmp_path / "secrets" / "topic").write_text("topic-do-not-print!\n", encoding="utf-8")
    (tmp_path / "secrets" / "ping").write_text("http://hc.example/ping-do-not-print\n",
                                               encoding="utf-8")
    text += json.dumps(doctor(environ), ensure_ascii=False)
    for value in SENTINELS:
        assert value not in text
    assert "nas:8000" not in text


def test_a_missing_sentinel_or_another_archives_state_is_a_problem(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "archive" / SENTINEL).unlink()
    assert any("mounted" in p for p in doctor(environ)["problems"])


def test_state_from_another_writer_is_a_problem(tmp_path):
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.replace('writer_id = "homelab"', 'writer_id = "laptop"'), encoding="utf-8")
    report = doctor(environ)
    assert report["state"]["matches"] is False
    assert any("another archive or another writer_id" in p for p in report["problems"])


def test_a_state_db_that_vanishes_between_the_check_and_the_open_is_not_a_crash(tmp_path,
                                                                                 monkeypatch):
    # why: State.read_only returns None when state.db is gone (Task 15's council, minor 6).
    environ = server(tmp_path)
    monkeypatch.setattr(doctor_module.State, "read_only", classmethod(lambda cls, path: None))
    report = doctor(environ)
    assert report["problems"] == [] and any("no state.db yet" in w for w in report["warnings"])


def test_a_damaged_state_db_is_a_problem_with_the_way_back(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "state" / "state.db").write_bytes(b"not a database, " * 64)
    assert any("restore state.db from the backup" in p for p in doctor(environ)["problems"])


def test_a_broken_config_still_reports_the_secrets(tmp_path):
    # why: stage-1 carry-over.
    environ = server(tmp_path)
    (tmp_path / "config.toml").write_text("path = [unclosed", encoding="utf-8")
    report = doctor(environ)
    assert report["secrets"]["restic_password"]["set"] is True
    assert any("config.toml" in p for p in report["problems"])


def test_a_key_given_as_a_value_is_a_problem(tmp_path):
    environ = server(tmp_path) | {"RECORDINGS_NAS_SSH_KEY": "key-do-not-print"}
    report = doctor(environ)
    assert any("RECORDINGS_NAS_SSH_KEY_FILE" in p for p in report["problems"])
    assert "key-do-not-print" not in json.dumps(report)


def test_an_empty_optional_secret_is_only_a_warning(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "secrets" / "rest").write_text("", encoding="utf-8")
    report = doctor(environ | {"RESTIC_REST_PASSWORD_FILE": str(tmp_path / "secrets" / "rest")})
    assert report["problems"] == [] and any("is empty" in w for w in report["warnings"])


def test_an_unset_optional_token_is_nothing_to_warn_about(tmp_path):
    # why: Compose names RECORDINGS_NTFY_TOKEN_FILE only when deploy.env sets a token file; the
    # backup service otherwise has it empty, and an empty optional secret is not a finding.
    report = doctor(server(tmp_path) | {"RECORDINGS_NTFY_TOKEN_FILE": ""})
    assert report["problems"] == [] and report["warnings"] == []
    assert report["secrets"]["ntfy_token"]["set"] is False


def test_leftover_job_folders_and_an_unset_cache_dir_are_warnings(tmp_path):
    # why: a killed repository check leaves restic-check-cache-* in restic's cache folder, and a
    # killed restore test leaves restore-*, an unencrypted copy of the archive, in the scratch
    # folder (Task 11's council). With no [backup] cache_dir, restic's cache falls to
    # $HOME/.cache, which in the container is the memory-backed /tmp and is lost at every restart.
    environ = server(tmp_path)
    assert doctor(environ, role="backup")["warnings"] == []
    (tmp_path / "cache" / "restic-check-cache-1234").mkdir(parents=True)
    (tmp_path / "scratch" / "restore-20261009T080000Z").mkdir()
    for role in ("backup", "all"):
        warnings = doctor(environ, role=role)["warnings"]
        assert any("restic-check-cache-" in w and "killed" in w for w in warnings), role
        assert any("restore-" in w and "unencrypted" in w for w in warnings), role
    web = doctor(web_only(environ), role="web")["warnings"]
    assert not any("restic-check-cache-" in w or "restore-" in w for w in web)  # not its job
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        f'cache_dir = "{tmp_path / "cache"}"\n', ""), encoding="utf-8")
    assert any("cache_dir" in w and "/tmp" in w for w in doctor(environ, role="backup")["warnings"])


def test_ntfy_off_for_want_of_a_topic_is_a_warning(tmp_path):
    # why: AlertConfig.enabled needs a server and a topic, and send() returns False without a
    # word when either is missing, so only doctor says that no alert will ever arrive.
    environ = {k: v for k, v in server(tmp_path).items() if k != "RECORDINGS_NTFY_TOPIC_FILE"}
    report = doctor(environ)
    assert report["problems"] == [] and report["alerts"]["topic_set"] is False
    assert any("alerts are off" in w for w in report["warnings"])


def test_a_short_topic_on_a_public_server_is_a_problem_unless_a_token_guards_it(tmp_path):
    # why: the app refuses a topic under 20 characters on ntfy.sh without a token (the topic is
    # then all that keeps the alerts private), so alerts silently stop; doctor must say why.
    environ = server(tmp_path)
    (tmp_path / "secrets" / "topic").write_text("short-topic\n", encoding="utf-8")
    report = doctor(environ)
    assert [p for p in report["problems"] if "20 or more random characters" in p]
    assert "short-topic" not in json.dumps(report)
    token = tmp_path / "secrets" / "token"
    token.write_text("tk_abc123\n", encoding="utf-8")
    token.chmod(0o600)
    assert doctor(environ | {"RECORDINGS_NTFY_TOKEN_FILE": str(token)})["problems"] == []


def test_the_empty_optional_token_mount_is_not_a_finding(tmp_path):
    # why: Compose mounts ntfy_token from /dev/null unless deploy.env names a file (Task 14), so
    # in the backup service the secret is a character device, empty, and nothing names it.
    environ = server(tmp_path)
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    (run_secrets / "ntfy_token").symlink_to("/dev/null")
    for role in ("backup", "all"):
        report = doctor(environ, role=role)
        assert report["problems"] == [] and report["warnings"] == [], role
        assert report["secrets"]["ntfy_token"]["set"] is False


def test_a_cache_dir_on_another_disk_than_the_state_is_a_warning(tmp_path, monkeypatch):
    # why: restic's cache belongs on the data disk (Task 11's council): on the container's /tmp it
    # is memory and lost at every restart, so every deploy downloads the repository's index again.
    environ = server(tmp_path)
    assert doctor(environ, role="backup")["warnings"] == []
    cache = tmp_path / "cache"
    monkeypatch.setattr(doctor_module, "_device", lambda path: 1 if Path(path) == cache else 2)
    warnings = doctor(environ, role="backup")["warnings"]
    assert any("cache_dir" in w and "not on the disk" in w for w in warnings)
    assert not any("cache_dir" in w for w in doctor(web_only(environ), role="web")["warnings"])


def test_a_topic_with_no_server_is_a_warning(tmp_path):
    # why: AlertConfig.enabled needs both, so a deleted ntfy_server line drops every alert in
    # silence. The reverse (a server, no topic) is the template's normal dead-man-only setup.
    environ = server(tmp_path)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('ntfy_server = "https://ntfy.sh"\n', ""),
                   encoding="utf-8")
    report = doctor(environ)
    assert report["problems"] == []
    assert any("ntfy_server" in w and "no alert is sent" in w for w in report["warnings"])
    assert "topic-do-not-print" not in json.dumps(report)


def test_the_backup_role_warns_when_deploy_env_is_not_backed_up(tmp_path):
    environ = server(tmp_path)  # the fixture names deploy.env in extra_paths, as the template does
    assert not any("deploy.env" in w for w in doctor(environ, role="backup")["warnings"])
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'extra_paths = ["/backup-extra/deploy.env"]\n', ""), encoding="utf-8")
    assert any("deploy.env" in w for w in doctor(environ, role="backup")["warnings"])
    assert not any("deploy.env" in w for w in doctor(environ, role="all")["warnings"])


def test_a_secret_file_others_can_read_is_a_warning(tmp_path):
    # why: ssh refuses such a key outright, and any secret file should be 600.
    environ = server(tmp_path)
    (tmp_path / "secrets" / "key").chmod(0o644)
    report = doctor(environ)
    assert report["problems"] == []
    assert [w for w in report["warnings"] if "chmod 600" in w] == [
        "RECORDINGS_NAS_SSH_KEY_FILE names a file that group or others can open (mode 644): "
        "chmod 600 it"]
    assert "key-do-not-print" not in json.dumps(report)


def test_hard_links_that_do_not_work_are_a_problem(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError("Operation not permitted")

    environ = server(tmp_path)  # before the patch: init itself publishes with a hard link
    monkeypatch.setattr(doctor_module.os, "link", no_links)
    report = doctor(environ)
    assert report["archive"]["hard_links"] is False
    assert any("hard links" in p for p in report["problems"])
    assert not (tmp_path / "archive" / ".tmp").exists()  # the probe leaves nothing behind


def test_missing_tools_and_alerts_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)
    environ = {k: v for k, v in server(tmp_path).items() if "DEADMAN" not in k}
    report = doctor(environ)
    assert report["tools"] == {"restic": False, "rsync": False, "ssh": False, "ffprobe": False,
                               "ffmpeg": False}
    assert any("restic is not installed" in p for p in report["problems"])
    assert any("dead-man" in w for w in report["warnings"])
    assert any("ffprobe and ffmpeg not installed" in w for w in report["warnings"])


def test_retention_off_here_is_a_warning_in_every_role(tmp_path):
    # why: with rest-server --append-only, the server can't prune, so retention runs on the NAS.
    # If that task is missing, nothing else says so, and the repository grows without limit.
    environ = server(tmp_path, backup_extra="prune = false\n")
    warning = ("retention is off here: it must run on the NAS, or the repository grows without "
               "limit")
    for role, env in (("all", environ), ("backup", environ), ("web", web_only(environ))):
        report = doctor(env, role=role)
        assert report["problems"] == [] and report["warnings"] == [warning], role
    assert doctor(environ)["backup"]["prune"] is False


def test_a_consent_list_mounted_only_for_imports_is_information(tmp_path):
    # why: in 2a the list is mounted only for `import-audio-router` runs, which read it and fail
    # closed by themselves. The long-running services don't have it, and that is no problem.
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    base = cfg.read_text(encoding="utf-8")
    cfg.write_text(base + '[plaud]\nconsent_list = "/consent/consent-list.md"\n', encoding="utf-8")
    for role, env in (("web", web_only(environ)), ("backup", environ)):
        report = doctor(env, role=role)
        assert report["problems"] == [] and report["warnings"] == [], role
        assert report["consent_list"] == {
            "path": "/consent/consent-list.md", "here": False,
            "note": "not mounted here; the import reads it and fails closed"}
    listed = tmp_path / "consent-list.md"
    listed.write_text("- " + "d" * 32 + "\n", encoding="utf-8")
    cfg.write_text(base + f'[plaud]\nconsent_list = "{listed}"\n', encoding="utf-8")
    report = doctor(web_only(environ), role="web")
    assert report["consent_list"] == {"path": str(listed), "here": True, "note": None}
    assert "d" * 32 not in json.dumps(report)  # its IDs are never read


def test_an_unknown_default_timezone_is_a_problem_in_every_role(tmp_path, monkeypatch):
    # why: zoneinfo reads the system's tzdata. A typo, or an image without it, leaves the import
    # unable to place any recording (Task 8), and Status without a local time.
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    good = cfg.read_text(encoding="utf-8")
    assert doctor(environ, role="backup")["problems"] == []  # the default, America/Vancouver
    cfg.write_text(good.replace('writer_id = "homelab"\n',
                                'writer_id = "homelab"\ndefault_timezone = "Mars/Olympus"\n', 1),
                   encoding="utf-8")
    for role, env in (("web", web_only(environ)), ("backup", environ)):
        (problem,) = doctor(env, role=role)["problems"]
        assert "default_timezone" in problem and "Mars/Olympus" in problem, role
    cfg.write_text(good, encoding="utf-8")

    def no_database(name):
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(doctor_module, "ZoneInfo", no_database)  # a machine without tzdata
    assert any("tzdata" in p for p in doctor(web_only(environ), role="web")["problems"])


def test_a_bad_disk_threshold_pair_is_a_problem(tmp_path):
    # why: the import exits 78 on it and Status quietly uses the defaults (Task 9, Task 15).
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + "[disk]\nwarn_free_percent = 20\nstop_free_percent = 30\n", encoding="utf-8")
    (problem,) = doctor(web_only(environ), role="web")["problems"]
    assert "stop_free_percent" in problem


def test_the_rest_server_setup_has_no_problems(tmp_path):
    # why: rest-server --append-only is the primary transport (council ruling, 2026-10-09).
    (tmp_path / "rest-server.crt").write_text("a test certificate\n", encoding="utf-8")
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/", backup_extra=(
        f'rest_username = "recordings"\nprune = false\n'
        f'cacert = "{tmp_path / "rest-server.crt"}"\n'))
    environ["RESTIC_REST_PASSWORD_FILE"] = str(tmp_path / "secrets" / "rest_password")
    report = doctor(environ)
    assert report["problems"] == []
    assert report["backup"]["transport"] == "rest" and report["backup"]["cacert_exists"] is True
    assert "rest-do-not-print" not in json.dumps(report)


def test_a_missing_cacert_is_a_problem(tmp_path):
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/",
                     backup_extra=f'cacert = "{tmp_path / "missing.crt"}"\n')
    assert any("cacert" in p for p in doctor(environ)["problems"])


def test_a_rest_user_without_its_password_is_a_problem(tmp_path):
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/",
                     backup_extra='rest_username = "recordings"\n')
    assert any("RESTIC_REST_PASSWORD_FILE" in p for p in doctor(environ)["problems"])


def test_a_nas_whose_free_space_cant_be_read_is_a_warning(tmp_path, monkeypatch):
    # why: §15.1. When neither ssh's df nor sftp's df answers, the NAS's free space is unknown,
    # and doctor says so rather than nothing.
    calls = []

    def neither_answers(cmd, **kwargs):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 255, "", "")

    monkeypatch.setattr(doctor_module, "nas_free_bytes",
                        lambda nas, path: nas_free_bytes(nas, path, runner=neither_answers))
    report = doctor(server(tmp_path), role="backup")
    assert calls == ["ssh", "sftp"]
    assert report["problems"] == [] and report["backup"]["nas_free_bytes"] is None
    assert report["warnings"] == [
        "the NAS's free space is unknown: neither ssh df nor sftp df answered"]


def test_the_web_role_needs_no_secrets_and_refuses_any(tmp_path):
    full = server(tmp_path)
    report = doctor(web_only(full), role="web")
    assert report["problems"] == [] and report["role"] == "web" and report["user"] is None
    assert report["backup"] is None and report["mirror"] is None and report["alerts"] is None
    report = doctor(web_only(full) | {"RESTIC_PASSWORD_FILE": full["RESTIC_PASSWORD_FILE"]},
                    role="web")
    assert any("restic_password" in p and "web" in p for p in report["problems"])
    assert "pw-do-not-print" not in json.dumps(report)
    for name in JOB_SECRETS:  # the optional ntfy_token included
        held = web_only(full) | {f"{SECRETS[name].env}_FILE": full["RESTIC_PASSWORD_FILE"]}
        assert any(name in p for p in doctor(held, role="web")["problems"]), name
    (tmp_path / "run-secrets").mkdir()  # a secret Compose mounted into web by mistake
    (tmp_path / "run-secrets" / "nas_ssh_key").write_text("x\n", encoding="utf-8")
    assert any("nas_ssh_key" in p for p in doctor(web_only(full), role="web")["problems"])


def test_every_job_secret_is_a_known_secret():
    assert set(JOB_SECRETS) <= set(SECRETS)


def test_the_backup_role_skips_the_hard_link_probe_on_a_read_only_archive(tmp_path, monkeypatch):
    environ = server(tmp_path)
    monkeypatch.setattr(doctor_module, "read_only_mount", lambda root: True)
    monkeypatch.setattr(doctor_module, "hard_links_work",
                        lambda root: pytest.fail("probed a read-only archive"))
    report = doctor(environ, role="backup")
    assert report["problems"] == [] and report["index"] is None
    assert report["archive"]["read_only"] is True and report["archive"]["hard_links"] is None
    report = doctor(environ, role="all")  # a writer can't work on a read-only archive
    assert any("mounted read-only" in p for p in report["problems"])


def test_a_writable_folder_is_not_a_read_only_mount(tmp_path):
    assert read_only_mount(tmp_path) is False


def test_a_user_without_a_passwd_entry_is_a_problem(tmp_path, monkeypatch):
    # why: ssh exits at once when its uid has no passwd entry, so the mirror and the NAS's
    # free-space check would fail in the backup service.
    environ = server(tmp_path)

    def no_entry(uid):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(doctor_module.pwd, "getpwuid", no_entry)
    report = doctor(environ, role="backup")
    assert report["user"]["passwd_entry"] is False
    assert any("passwd entry" in p for p in report["problems"])
    assert doctor(web_only(environ), role="web")["problems"] == []  # web runs no ssh


def test_the_role_comes_from_the_environment_and_an_unknown_one_checks_all(tmp_path):
    environ = server(tmp_path)
    assert doctor(environ | {"RECORDINGS_ROLE": "backup"})["role"] == "backup"
    report = doctor(environ | {"RECORDINGS_ROLE": "worker"})
    assert report["role"] == "all" and any("RECORDINGS_ROLE" in p for p in report["problems"])


def test_the_cli_exits_0_when_all_is_well(tmp_path, monkeypatch, capsys):
    for key, value in server(tmp_path).items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE"):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []


def test_the_cli_takes_the_role(tmp_path, monkeypatch, capsys):
    for key, value in web_only(server(tmp_path)).items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE",
                 *SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--role", "web", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] == "web"
    assert main(["doctor", "--role", "backup", "--json"]) == 78  # no restic password here
    assert "RESTIC_PASSWORD_FILE" in " ".join(json.loads(capsys.readouterr().out)["problems"])
