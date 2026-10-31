import hashlib
import json
import socket
import sqlite3
import subprocess
import urllib.request
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest
from recordings import doctor as doctor_module
from recordings.archive import Archive
from recordings.cli import main
from recordings.config import SECRETS
from recordings.doctor import ALERT_SECRETS, BACKUP_SECRETS, doctor, read_only_mount
from recordings.index import Index
from recordings.init import init_archive
from recordings.plaud.keepout import typo_phrase
from recordings.plaud.settings import BAD_WORD
from recordings.sentinel import MIRROR, SENTINEL, read_sentinel
from recordings.ssh import nas_free_bytes
from recordings.state import SCHEMA_VERSION, State

NAS_FREE = 500 * 1024**3

SENTINELS = (
    "pw-do-not-print",
    "key-do-not-print",
    "topic-do-not-print",
    "ping-do-not-print",
    "rest-do-not-print",
    "hunter2",
)
SECRET_ENV = (
    "RESTIC_PASSWORD_FILE",
    "RESTIC_REST_PASSWORD_FILE",
    "RECORDINGS_NAS_SSH_KEY_FILE",
    "RECORDINGS_NAS_KNOWN_HOSTS_FILE",
    "RECORDINGS_NTFY_TOPIC_FILE",
    "RECORDINGS_NTFY_TOKEN_FILE",
    "RECORDINGS_DEADMAN_URL_FILE",
)


# As config.example.toml and the runbook's section 6 have it: deploy.env and the consent folder.
EXTRA_PATHS = 'extra_paths = ["/backup-extra/deploy.env", "/consent"]\n'


def server(tmp_path, *, repository="sftp:backup@nas:/volume1/backups/recordings", backup_extra=""):
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="homelab")
    (tmp_path / "scratch").mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in (
        ("pw", "pw-do-not-print"),
        ("key", "key-do-not-print"),
        ("known_hosts", "nas ssh-ed25519 AAAAexample"),
        ("topic", "topic-do-not-print-20"),
        ("ping", "https://hc.example/ping-do-not-print"),
        ("rest_password", "rest-do-not-print"),
    ):
        (secrets / name).write_text(value + "\n", encoding="utf-8")
        (secrets / name).chmod(0o600)  # as the runbook makes them
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n'
        f'[nas]\nssh = "backup@nas"\n'
        f'[backup]\nrepository = "{repository}"\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'nas_repo_path = "/volume1/backups/recordings"\n'
        f"{EXTRA_PATHS}"
        f'cache_dir = "{tmp_path / "cache"}"\n{backup_extra}'
        f'[mirror]\ntarget = "backup@nas:/volume1/recordings-mirror/"\n'
        f'[alerts]\nntfy_server = "https://ntfy.sh"\n',
        encoding="utf-8",
    )
    return {
        "RECORDINGS_CONFIG": str(cfg),
        "RESTIC_PASSWORD_FILE": str(secrets / "pw"),
        "RECORDINGS_NAS_SSH_KEY_FILE": str(secrets / "key"),
        "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(secrets / "known_hosts"),
        "RECORDINGS_NTFY_TOPIC_FILE": str(secrets / "topic"),
        "RECORDINGS_DEADMAN_URL_FILE": str(secrets / "ping"),
    }


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


@pytest.fixture(autouse=True)
def no_plaud_mount(monkeypatch, tmp_path):
    # This machine's own /plaud, if it had one, never decides a test: the mount is a tmp folder,
    # absent unless a test makes it.
    monkeypatch.setattr(doctor_module, "PLAUD_MOUNT", tmp_path / "plaud-mount")


SYNC_PING = "https://hc.example/sync-ping-do-not-print"
TOKEN = '{"access_token": "token-do-not-print"}'  # synthetic
LISTED = "of_" + f"{1:032x}"  # synthetic Plaud IDs
KEPT = "of_" + f"{2:032x}"


def worker(tmp_path, *, consent=f"- `{LISTED}` — synthetic\n", token=True, plaud_extra=""):
    """What the worker service has: the shared config with a [plaud] section, its consent list,
    its sign-in's folder (0700, the token 0600), and the sync's ping URL as its only secret."""
    environ = web_only(server(tmp_path))
    plaud = tmp_path / "plaud"
    plaud.mkdir()
    plaud.chmod(0o700)
    if token:
        (plaud / "tokens.json").write_text(TOKEN + "\n", encoding="utf-8")
        (plaud / "tokens.json").chmod(0o600)
    folder = tmp_path / "consent"
    folder.mkdir()
    if consent is not None:
        (folder / "consent-list.md").write_text(consent, encoding="utf-8")
    ping = tmp_path / "secrets" / "deadman_sync_url"
    ping.write_text(SYNC_PING + "\n", encoding="utf-8")
    ping.chmod(0o600)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + f'[plaud]\nconsent_list = "{folder / "consent-list.md"}"\n'
        + f'token_store = "{plaud / "tokens.json"}"\n{plaud_extra}',
        encoding="utf-8",
    )
    return environ | {"RECORDINGS_DEADMAN_SYNC_URL_FILE": str(ping)}


def test_a_fully_configured_writer_has_no_problems(tmp_path):
    report = doctor(server(tmp_path))
    assert report["problems"] == [] and report["warnings"] == []
    assert report["role"] == "all" and report["user"]["passwd_entry"] is True
    assert report["archive"]["sentinel"] and report["archive"]["hard_links"]
    assert report["archive"]["read_only"] is False
    assert report["state"]["matches"] is True
    assert report["backup"] == {
        "transport": "sftp",
        "password_set": True,
        "prune": True,
        "cacert_exists": None,
        "restore_scratch_exists": True,
        "nas_free_check": True,
        "nas_free_bytes": NAS_FREE,
        "restic_installed": True,
    }
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
    (tmp_path / "secrets" / "ping").write_text(
        "http://hc.example/ping-do-not-print\n", encoding="utf-8"
    )
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


def test_a_state_db_that_vanishes_between_the_check_and_the_open_is_not_a_crash(
    tmp_path, monkeypatch
):
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
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(f'cache_dir = "{tmp_path / "cache"}"\n', ""),
        encoding="utf-8",
    )
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
    for role, env in (("backup", environ), ("all", environ), ("web", web_only(environ))):
        report = doctor(env, role=role)  # web reads SECRETS_DIR: an empty mount holds nothing
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
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('ntfy_server = "https://ntfy.sh"\n', ""),
        encoding="utf-8",
    )
    report = doctor(environ)
    assert report["problems"] == []
    assert any("ntfy_server" in w and "no alert is sent" in w for w in report["warnings"])
    assert "topic-do-not-print" not in json.dumps(report)


def test_the_backup_role_warns_when_deploy_env_is_not_backed_up(tmp_path):
    environ = server(tmp_path)  # the fixture names deploy.env in extra_paths, as the template does
    assert not any("deploy.env" in w for w in doctor(environ, role="backup")["warnings"])
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(EXTRA_PATHS, 'extra_paths = ["/consent"]\n'),
        encoding="utf-8",
    )
    assert any("deploy.env" in w for w in doctor(environ, role="backup")["warnings"])
    assert not any("deploy.env" in w for w in doctor(environ, role="all")["warnings"])


def test_a_secret_file_others_can_read_is_a_warning(tmp_path):
    # why: ssh refuses such a key outright, and any secret file should be 600.
    environ = server(tmp_path)
    (tmp_path / "secrets" / "key").chmod(0o644)
    report = doctor(environ)
    assert report["problems"] == []
    assert [w for w in report["warnings"] if "chmod 600" in w] == [
        (
            "RECORDINGS_NAS_SSH_KEY_FILE names a file that group or others can open (mode 644): "
            "chmod 600 it"
        )
    ]
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
    assert report["tools"] == {
        "restic": False,
        "rsync": False,
        "ssh": False,
        "ffprobe": False,
        "ffmpeg": False,
    }
    assert any("restic is not installed" in p for p in report["problems"])
    assert any("dead-man" in w for w in report["warnings"])
    assert any("ffprobe and ffmpeg not installed" in w for w in report["warnings"])


def test_retention_off_here_is_a_warning_in_every_role(tmp_path):
    # why: with rest-server --append-only, the server can't prune, so retention runs on the NAS.
    # If that task is missing, nothing else says so, and the repository grows without limit.
    environ = server(tmp_path, backup_extra="prune = false\n")
    warning = "retention is off here: it must run on the NAS, or the repository grows without limit"
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
            "path": "/consent/consent-list.md",
            "here": False,
            "note": "not mounted here; the import reads it and fails closed",
        }
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
    cfg.write_text(
        good.replace(
            'writer_id = "homelab"\n',
            'writer_id = "homelab"\ndefault_timezone = "Mars/Olympus"\n',
            1,
        ),
        encoding="utf-8",
    )
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
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "[disk]\nwarn_free_percent = 20\nstop_free_percent = 30\n",
        encoding="utf-8",
    )
    (problem,) = doctor(web_only(environ), role="web")["problems"]
    assert "stop_free_percent" in problem


def test_the_rest_server_setup_has_no_problems(tmp_path):
    # why: rest-server --append-only is the primary transport (council ruling, 2026-10-09).
    (tmp_path / "rest-server.crt").write_text("a test certificate\n", encoding="utf-8")
    environ = server(
        tmp_path,
        repository="rest:https://nas:8000/recordings/",
        backup_extra=(
            f'rest_username = "recordings"\nprune = false\n'
            f'cacert = "{tmp_path / "rest-server.crt"}"\n'
        ),
    )
    environ["RESTIC_REST_PASSWORD_FILE"] = str(tmp_path / "secrets" / "rest_password")
    report = doctor(environ)
    assert report["problems"] == []
    assert report["backup"]["transport"] == "rest" and report["backup"]["cacert_exists"] is True
    assert "rest-do-not-print" not in json.dumps(report)


def test_a_missing_cacert_is_a_problem(tmp_path):
    environ = server(
        tmp_path,
        repository="rest:https://nas:8000/recordings/",
        backup_extra=f'cacert = "{tmp_path / "missing.crt"}"\n',
    )
    assert any("cacert" in p for p in doctor(environ)["problems"])


def test_a_rest_user_without_its_password_is_a_problem(tmp_path):
    environ = server(
        tmp_path,
        repository="rest:https://nas:8000/recordings/",
        backup_extra='rest_username = "recordings"\n',
    )
    assert any("RESTIC_REST_PASSWORD_FILE" in p for p in doctor(environ)["problems"])


def test_a_nas_whose_free_space_cant_be_read_is_a_warning(tmp_path, monkeypatch):
    # why: §15.1. When neither ssh's df nor sftp's df answers, the NAS's free space is unknown,
    # and doctor says so rather than nothing.
    calls = []

    def neither_answers(cmd, **kwargs):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 255, "", "")

    monkeypatch.setattr(
        doctor_module,
        "nas_free_bytes",
        lambda nas, path: nas_free_bytes(nas, path, runner=neither_answers),
    )
    report = doctor(server(tmp_path), role="backup")
    assert calls == ["ssh", "sftp"]
    assert report["problems"] == [] and report["backup"]["nas_free_bytes"] is None
    assert report["warnings"] == [
        "the NAS's free space is unknown: neither ssh df nor sftp df answered"
    ]


def test_the_web_role_needs_no_secrets_and_refuses_any(tmp_path):
    full = server(tmp_path)
    report = doctor(web_only(full), role="web")
    assert report["problems"] == [] and report["role"] == "web" and report["user"] is None
    assert report["backup"] is None and report["mirror"] is None and report["alerts"] is None
    report = doctor(
        web_only(full) | {"RESTIC_PASSWORD_FILE": full["RESTIC_PASSWORD_FILE"]}, role="web"
    )
    assert any("restic_password" in p and "web" in p for p in report["problems"])
    assert "pw-do-not-print" not in json.dumps(report)
    for name in (*BACKUP_SECRETS, *ALERT_SECRETS):  # the optional ntfy_token included
        held = web_only(full) | {f"{SECRETS[name].env}_FILE": full["RESTIC_PASSWORD_FILE"]}
        assert any(name in p for p in doctor(held, role="web")["problems"]), name
    (tmp_path / "run-secrets").mkdir()  # a secret Compose mounted into web by mistake
    (tmp_path / "run-secrets" / "nas_ssh_key").write_text("x\n", encoding="utf-8")
    assert any("nas_ssh_key" in p for p in doctor(web_only(full), role="web")["problems"])


def test_every_secret_a_role_forbids_is_a_known_secret():
    assert {*BACKUP_SECRETS, *ALERT_SECRETS, "deadman_sync_url"} <= set(SECRETS)


def test_the_backup_role_skips_the_hard_link_probe_on_a_read_only_archive(tmp_path, monkeypatch):
    environ = server(tmp_path)
    monkeypatch.setattr(doctor_module, "read_only_mount", lambda root: True)
    monkeypatch.setattr(
        doctor_module, "hard_links_work", lambda root: pytest.fail("probed a read-only archive")
    )
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
    assert doctor(environ | {"RECORDINGS_ROLE": "worker"})["role"] == "worker"
    report = doctor(environ | {"RECORDINGS_ROLE": "builder"})
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
    for name in (
        "RECORDINGS_ARCHIVE",
        "RECORDINGS_STATE",
        "RECORDINGS_INDEX",
        "RECORDINGS_ROLE",
        *SECRET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--role", "web", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] == "web"
    assert main(["doctor", "--role", "backup", "--json"]) == 78  # no restic password here
    assert "RESTIC_PASSWORD_FILE" in " ".join(json.loads(capsys.readouterr().out)["problems"])


def test_an_empty_named_token_file_is_a_problem_because_no_alert_is_ever_sent(
    tmp_path, monkeypatch, capsys
):
    # why: the loop reads any alerts.from_config failure as "alerts off": no alert and no dead-man
    # ping is sent, so doctor, the deploy gate, must say so and exit 78 (Task 16 review).
    environ = server(tmp_path)
    token = tmp_path / "secrets" / "token"
    token.write_text("", encoding="utf-8")
    token.chmod(0o600)
    environ["RECORDINGS_NTFY_TOKEN_FILE"] = str(token)
    report = doctor(environ)
    assert [
        p
        for p in report["problems"]
        if "alerts and dead-man pings are off" in p
        and "RECORDINGS_NTFY_TOKEN_FILE" in p
        and "is empty" in p
    ]
    assert not any("is empty" in w for w in report["warnings"])  # moved, not repeated
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE"):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--json"]) == 78
    capsys.readouterr()


def test_a_rest_repository_with_a_broken_password_file_is_a_problem_without_a_username(tmp_path):
    # why: restic_env raises on every backup when a named rest_password file is empty, whether or
    # not [backup] rest_username is set.
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/")
    empty = tmp_path / "secrets" / "rest_empty"
    empty.write_text("", encoding="utf-8")
    empty.chmod(0o600)
    report = doctor(environ | {"RESTIC_REST_PASSWORD_FILE": str(empty)})
    assert [p for p in report["problems"] if "RESTIC_REST_PASSWORD_FILE" in p and "is empty" in p]
    assert not any("is empty" in w for w in report["warnings"])


def test_in_the_backup_role_a_missing_state_db_or_an_unconfigured_job_is_a_problem(tmp_path):
    # why: `ops --loop` exits 78 without state.db, and the service crash-loops; and backup is the
    # role whose whole job is the backups and the mirror.
    environ = server(tmp_path)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    good = cfg.read_text(encoding="utf-8")
    (tmp_path / "state" / "state.db").unlink()
    assert any("no state.db yet" in p for p in doctor(environ, role="backup")["problems"])
    assert any("no state.db yet" in w for w in doctor(environ, role="all")["warnings"])
    cfg.write_text(
        good.replace('repository = "sftp:backup@nas:/volume1/backups/recordings"\n', "").replace(
            'target = "backup@nas:/volume1/recordings-mirror/"\n', ""
        ),
        encoding="utf-8",
    )
    problems = doctor(environ, role="backup")["problems"]
    assert any("backups aren't configured" in p for p in problems)
    assert any("the mirror isn't configured" in p for p in problems)
    warnings = doctor(environ, role="all")["warnings"]
    assert any("backups aren't configured" in w for w in warnings)


def test_a_missing_ssh_is_a_problem_when_something_needs_it(tmp_path, monkeypatch):
    environ = server(tmp_path)
    monkeypatch.setattr(
        doctor_module.shutil, "which", lambda name: None if name == "ssh" else f"/usr/bin/{name}"
    )
    assert any("ssh is not installed" in p for p in doctor(environ, role="backup")["problems"])
    assert doctor(web_only(environ), role="web")["problems"] == []  # web runs no ssh


def test_an_unmounted_archive_does_not_also_blame_the_state(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "archive" / SENTINEL).unlink()
    report = doctor(environ)
    assert report["state"]["matches"] is None
    assert not any("belongs to another archive" in p for p in report["problems"])


def test_state_from_another_archive_is_a_problem(tmp_path):
    environ = server(tmp_path)
    init_archive(tmp_path / "archive2", tmp_path / "state2", writer_id="homelab")
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            f'path = "{tmp_path / "state"}"', f'path = "{tmp_path / "state2"}"'
        ),
        encoding="utf-8",
    )
    report = doctor(environ)
    assert report["state"]["matches"] is False
    assert any("another archive or another writer_id" in p for p in report["problems"])


def test_an_empty_restic_password_file_is_not_also_reported_unset(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "secrets" / "pw").write_text("", encoding="utf-8")
    problems = doctor(environ)["problems"]
    assert any("is empty" in p for p in problems)
    assert not any("is not set" in p for p in problems)


@pytest.mark.parametrize("value", ["5", "[1]"])
def test_a_default_timezone_that_is_not_a_string_is_a_problem_not_a_crash(tmp_path, value):
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'writer_id = "homelab"\n', f'writer_id = "homelab"\ndefault_timezone = {value}\n', 1
        ),
        encoding="utf-8",
    )
    assert any("default_timezone" in p for p in doctor(web_only(environ), role="web")["problems"])


def test_a_job_running_now_owns_its_folders_and_doctor_says_to_leave_them(tmp_path):
    # why: the runbook runs doctor right after `up`, while the first check and restore test run;
    # deleting a live restore test's folder fails it.
    from datetime import datetime

    from recordings.locks import OPS_LOCK, Locks, announce

    environ = server(tmp_path)
    (tmp_path / "cache" / "restic-check-cache-1234").mkdir(parents=True)
    (tmp_path / "scratch" / "restore-20261009T080000Z").mkdir()
    folders = tmp_path / "state" / "locks"
    with (
        Locks(folders, timeout=0).hold(OPS_LOCK),
        announce(folders, "check", datetime.now(UTC)),
    ):
        report = doctor(environ, role="backup")
    assert not any("restic-check-cache-" in w for w in report["warnings"])
    assert any("restore-" in w and "unencrypted" in w for w in report["warnings"])  # not check's
    assert any("belongs to check, running now: leave it" in n for n in report["notes"])


def test_the_mirror_and_a_reader_are_never_written(tmp_path, monkeypatch):
    # why: spec §3, the mirror's copy is never written, and a reader writes nothing; doctor's
    # hard-link probe wrote .tmp/ in both (Task 16's council).
    environ = server(tmp_path)
    monkeypatch.setattr(
        doctor_module,
        "hard_links_work",
        lambda root: pytest.fail("probed an archive this machine never writes"),
    )
    cfg = Path(environ["RECORDINGS_CONFIG"])
    writer = cfg.read_text(encoding="utf-8")
    cfg.write_text(writer.replace('writer_id = "homelab"\n', "", 1), encoding="utf-8")  # a reader
    report = doctor(web_only(environ), role="web")
    assert report["archive"]["hard_links"] is None
    assert not any("hard links" in p or "read-only" in p for p in report["problems"])
    cfg.write_text(writer, encoding="utf-8")
    (tmp_path / "archive" / MIRROR).write_text("a mirror\n", encoding="utf-8")
    report = doctor(environ)
    assert report["archive"]["hard_links"] is None
    assert any(MIRROR in p and "never written" in p for p in report["problems"])


def test_a_crash_is_one_line_naming_the_check_never_a_traceback(tmp_path, monkeypatch, capsys):
    # why: a config shape no check expects ([plaud] as a string; a number for cache_dir,
    # nas_repo_path or extra_paths) ended doctor in a traceback, with no report (Task 16's council).
    environ = server(tmp_path)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        'plaud = "rest:https://u:pw-do-not-print@nas:8000/"\n' + cfg.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE"):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--json"]) == 70
    out = capsys.readouterr().out
    assert json.loads(out)["error"].startswith("doctor stopped in _consent on AttributeError")
    assert "do-not-print" not in out and "Traceback" not in out


def test_a_state_db_from_a_newer_release_is_a_problem_as_state_open_refuses_it(tmp_path):
    # why: after a rollback to an older release, State.open refuses a newer state.db (the
    # runbook's "Restoring for real" step 3) while doctor passed it (Task 16's council).
    environ = server(tmp_path)
    assert doctor(environ)["problems"] == []
    with sqlite3.connect(tmp_path / "state" / "state.db") as conn:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),)
        )
    for role, env in (("web", web_only(environ)), ("backup", environ), ("all", environ)):
        problems = doctor(env, role=role)["problems"]
        assert any(
            f"schema version {SCHEMA_VERSION + 1}" in p and "newer" in p for p in problems
        ), role
    with sqlite3.connect(tmp_path / "state" / "state.db") as conn:
        conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    assert any("schema_version" in p for p in doctor(environ)["problems"])  # damaged, as open says


def test_an_archive_that_is_the_mirrors_copy_is_a_problem_for_a_writer(tmp_path):
    # why: Writer.__init__ refuses an archive carrying MIRROR.md, and the sentinel and UUID checks
    # can't see it: the mirror's copy carries archive.json too (Task 16's council).
    environ = server(tmp_path)
    (tmp_path / "archive" / MIRROR).write_text("a mirror\n", encoding="utf-8")
    problems = doctor(environ)["problems"]
    assert any(MIRROR in p and "never written" in p for p in problems)
    reader = Path(environ["RECORDINGS_CONFIG"])  # a machine with no writer_id only reads it
    reader.write_text(
        reader.read_text(encoding="utf-8").replace('writer_id = "homelab"\n', ""), encoding="utf-8"
    )
    assert not any(MIRROR in p for p in doctor(web_only(environ), role="web")["problems"])


def test_the_nas_key_is_not_demanded_but_the_host_key_is(tmp_path):
    # why: ssh_options treats a missing key as "use ssh's own" (an agent), and spec §5 requires
    # only known_hosts, which backup.from_config and mirror.from_config already enforce.
    environ = server(tmp_path)
    no_key = {k: v for k, v in environ.items() if k != "RECORDINGS_NAS_SSH_KEY_FILE"}
    for role in ("backup", "all"):
        report = doctor(no_key, role=role)
        assert report["problems"] == [], role
    no_hosts = {k: v for k, v in environ.items() if k != "RECORDINGS_NAS_KNOWN_HOSTS_FILE"}
    problems = doctor(no_hosts, role="backup")["problems"]
    assert problems and all("host key" in p for p in problems)


def test_a_missing_or_unusable_index_is_a_warning(tmp_path):
    # why: Writer.open refuses without [index] path, and an index of another archive or a damaged
    # one answers nothing, so the Library lists no recording (Task 16's council).
    environ = server(tmp_path)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    good = cfg.read_text(encoding="utf-8")
    index_path = tmp_path / "index.db"
    index_path.write_bytes(b"not an index at all" * 100)  # damaged
    report = doctor(environ)
    assert report["problems"] == []
    assert any("index" in w and "reindex" in w for w in report["warnings"])
    assert report["index"]["matches"] is False
    index_path.unlink()
    init_archive(tmp_path / "other", tmp_path / "other-state", writer_id="homelab")
    Index(index_path).rebuild(
        Archive(tmp_path / "other"), archive_uuid="another-archive", allow_empty=True
    )  # foreign
    report = doctor(environ)
    assert any("index" in w and "reindex" in w for w in report["warnings"])
    Index(index_path).rebuild(
        Archive(tmp_path / "archive"),
        archive_uuid=read_sentinel(tmp_path / "archive").uuid,
        allow_empty=True,
    )  # this archive's own
    report = doctor(environ)
    assert report["warnings"] == [] and report["index"]["matches"] is True
    cfg.write_text(good.replace(f'[index]\npath = "{index_path}"\n', ""), encoding="utf-8")
    report = doctor(environ)
    assert report["problems"] == [] and report["index"] is None
    assert any("[index] path" in w for w in report["warnings"])
    assert not any("[index] path" in w for w in doctor(environ, role="backup")["warnings"])


def test_an_unset_consent_list_is_a_note_not_a_warning(tmp_path):
    # why: the import refuses to run without one (spec §9.1), and the long-running services never
    # need it, so it is information (Task 16's council).
    environ = server(tmp_path)
    for role, env in (("web", web_only(environ)), ("backup", environ), ("all", environ)):
        report = doctor(env, role=role)
        assert report["problems"] == [] and report["warnings"] == [], role
        assert any("consent_list is not set" in n and "import" in n for n in report["notes"]), role
        assert report["consent_list"] is None
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + '[plaud]\nconsent_list = "   "\n', encoding="utf-8"
    )
    assert any("consent_list is not set" in n for n in doctor(environ)["notes"])  # blank is unset


def test_only_an_empty_mount_is_ignored_as_a_job_secret_in_web(tmp_path):
    # why: Compose mounts an optional secret from /dev/null; any other character device (a tty,
    # /dev/zero) is a mount that holds something (Task 16's council: compare st_rdev).
    environ = web_only(server(tmp_path))
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    (run_secrets / "ntfy_token").symlink_to("/dev/null")
    assert doctor(environ, role="web")["problems"] == []
    (run_secrets / "ntfy_token").unlink()
    (run_secrets / "ntfy_token").symlink_to("/dev/zero")
    assert any("ntfy_token" in p for p in doctor(environ, role="web")["problems"])


def test_the_worker_role_skips_backup_mirror_ssh_and_restic(tmp_path, monkeypatch):
    # why: stage 2b spec §17.2. The worker holds no restic, NAS or ssh secret, and runs none of
    # those jobs: their checks would be noise, or demand secrets it must not hold.
    monkeypatch.setattr(
        doctor_module.shutil,
        "which",
        lambda name: None if name in ("restic", "rsync", "ssh") else f"/usr/bin/{name}",
    )
    report = doctor(worker(tmp_path), role="worker")
    assert report["problems"] == [], report["problems"]
    assert report["backup"] is None and report["mirror"] is None
    assert report["user"]["passwd_entry"] is True
    assert report["plaud"] == {
        "consent_ids": 1,
        "consent_stop": None,
        "review_lines": [],
        "token_store": True,
        "keep_out_words": [],
        "deadman_sync_set": True,
    }
    assert report["alerts"]["deadman_set"] is True
    assert not any(word in " ".join(report["warnings"]) for word in ("restic", "ssh", "rsync"))


def test_the_worker_has_no_alerts_off_warning(tmp_path):
    # why: decision 10. The worker sends no push by design; its Healthchecks check is its alert.
    report = doctor(worker(tmp_path), role="worker")
    assert report["warnings"] == []
    assert report["alerts"] == {"ntfy_server_set": True, "topic_set": False, "deadman_set": True}


def test_a_worker_consent_stop_names_line_numbers_and_kept_lines_are_a_note(tmp_path):
    typo = "a" * 31
    consent = f"# synthetic\n- `{typo}` — one digit short\n{KEPT} kept: she agreed later\n"
    report = doctor(worker(tmp_path, consent=consent), role="worker")
    assert report["problems"] == [
        f"{typo_phrase([2])}: nothing is imported until it reads (stage 2b spec §12.1)"
    ]
    assert report["plaud"]["consent_stop"] == typo_phrase([2])
    assert report["plaud"]["consent_ids"] is None and report["plaud"]["review_lines"] == [3]
    # the same sentence `recordings plaud consent` prints (one keepout helper)
    assert "lines read as kept, not excluded (check each meant keep): 3" in report["notes"]
    text = json.dumps(report)
    assert typo not in text and KEPT not in text  # line numbers, never the text


def test_a_worker_with_no_consent_list_reports_it(tmp_path):
    report = doctor(worker(tmp_path, consent=None), role="worker")
    assert report["problems"] == [
        "the consent list is missing: nothing is imported until it reads (stage 2b spec §12.1)"
    ]


def test_a_worker_with_no_token_store_needs_the_sign_in(tmp_path):
    report = doctor(worker(tmp_path, token=False), role="worker")
    (problem,) = report["problems"]
    assert problem.startswith("sign-in needed")
    assert "make plaud-sign-in" not in problem  # that target arrives in Phase 2
    assert "runbook section 6" in problem
    assert report["plaud"]["token_store"] is False


def test_a_plaud_folder_wider_than_0700_is_a_problem(tmp_path):
    environ = worker(tmp_path)
    (tmp_path / "plaud").chmod(0o750)
    (problem,) = doctor(environ, role="worker")["problems"]
    assert str(tmp_path / "plaud") in problem and "mode 750" in problem and "chmod 700" in problem


def test_a_token_wider_than_0600_is_a_problem(tmp_path):
    environ = worker(tmp_path)
    (tmp_path / "plaud" / "tokens.json").chmod(0o640)
    (problem,) = doctor(environ, role="worker")["problems"]
    assert "tokens.json" in problem and "mode 640" in problem and "chmod 600" in problem


def test_a_sign_in_that_isnt_this_users_is_a_problem(tmp_path, monkeypatch):
    environ = worker(tmp_path)
    monkeypatch.setattr(doctor_module, "_owner", lambda: 4242)
    problems = doctor(environ, role="worker")["problems"]
    assert len(problems) == 2 and all("not this user (4242)" in p for p in problems)


def test_a_bad_keep_out_word_is_a_worker_problem(tmp_path):
    environ = worker(tmp_path, plaud_extra='keep_out_word = "no archive!"\n')
    assert doctor(environ, role="worker")["problems"] == [BAD_WORD]


def test_a_missing_sync_ping_url_is_a_worker_problem(tmp_path):
    environ = worker(tmp_path)
    del environ["RECORDINGS_DEADMAN_SYNC_URL_FILE"]
    (problem,) = doctor(environ, role="worker")["problems"]
    assert "RECORDINGS_DEADMAN_SYNC_URL_FILE" in problem
    assert doctor(environ, role="worker")["plaud"]["deadman_sync_set"] is False


def test_a_bad_or_empty_sync_ping_url_is_a_worker_problem(tmp_path):
    environ = worker(tmp_path)
    ping = Path(environ["RECORDINGS_DEADMAN_SYNC_URL_FILE"])
    for content in ("http://hc.example/sync-ping-do-not-print\n", ""):
        ping.write_text(content, encoding="utf-8")
        report = doctor(environ, role="worker")
        (problem,) = report["problems"]
        assert "RECORDINGS_DEADMAN_SYNC_URL" in problem
        assert "do-not-print" not in json.dumps(report)


def test_web_forbids_both_secret_sets_and_the_sync_url(tmp_path):
    environ = web_only(server(tmp_path))
    held = tmp_path / "secrets" / "pw"
    for name in (*BACKUP_SECRETS, *ALERT_SECRETS, "deadman_sync_url"):
        with_one = environ | {f"{SECRETS[name].env}_FILE": str(held)}
        assert any(name in p and "web" in p for p in doctor(with_one, role="web")["problems"]), name


def test_worker_forbids_the_backup_set(tmp_path):
    environ = worker(tmp_path)
    held = tmp_path / "secrets" / "pw"
    for name in BACKUP_SECRETS:
        with_one = environ | {f"{SECRETS[name].env}_FILE": str(held)}
        problems = doctor(with_one, role="worker")["problems"]
        assert any(name in p and "worker" in p for p in problems), name
    # the alert set stays allowed: the later ntfy change mounts it in `worker` (spec §16)
    topic = environ | {"RECORDINGS_NTFY_TOPIC_FILE": str(tmp_path / "secrets" / "topic")}
    assert not any("ntfy_topic" in p for p in doctor(topic, role="worker")["problems"])


def test_backup_forbids_the_sync_url(tmp_path):
    environ = server(tmp_path)
    assert not any("deadman_sync_url" in p for p in doctor(environ, role="backup")["problems"])
    environ["RECORDINGS_DEADMAN_SYNC_URL_FILE"] = str(tmp_path / "secrets" / "ping")
    problems = doctor(environ, role="backup")["problems"]
    assert any("deadman_sync_url" in p and "backup" in p for p in problems)


def test_web_and_backup_report_a_mounted_plaud_or_a_plaud_token(tmp_path):
    full = server(tmp_path)
    for role, environ in (("web", web_only(full)), ("backup", full)):
        assert not any("plaud" in p.lower() for p in doctor(environ, role=role)["problems"]), role
    (tmp_path / "plaud-mount").mkdir()
    for role, environ in (("web", web_only(full)), ("backup", full)):
        problems = doctor(environ, role=role)["problems"]
        assert any("plaud-mount is mounted here" in p for p in problems), role
    (tmp_path / "plaud-mount").rmdir()
    for role, environ in (("web", web_only(full)), ("backup", full)):
        report = doctor(environ | {"RECORDINGS_PLAUD_TOKEN": "token-do-not-print"}, role=role)
        assert any("RECORDINGS_PLAUD_TOKEN is set here" in p for p in report["problems"]), role
        assert "token-do-not-print" not in json.dumps(report)


def test_the_token_file_variable_is_a_problem_in_web_and_backup_too(tmp_path):
    full = server(tmp_path)
    for role, environ in (("web", web_only(full)), ("backup", full)):
        report = doctor(environ | {"RECORDINGS_PLAUD_TOKEN_FILE": "/x/not-read"}, role=role)
        assert any("RECORDINGS_PLAUD_TOKEN_FILE is set here" in p for p in report["problems"]), role


def test_web_and_backup_check_the_folder_token_store_names(tmp_path):
    full = server(tmp_path)
    other = tmp_path / "other-plaud"
    other.mkdir()
    cfg = Path(full["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f'[plaud]\ntoken_store = "{other / "tokens.json"}"\n',
        encoding="utf-8",
    )
    for role, environ in (("web", web_only(full)), ("backup", full)):
        problems = doctor(environ, role=role)["problems"]
        assert any(f"{other} is mounted here" in p for p in problems), role


def test_the_workers_consent_notes_are_true_for_the_worker(tmp_path):
    # why: the worker stays up and needs the list; the 2a notes ("the services that stay up don't
    # need it", "not mounted here") were written for the other services.
    environ = worker(tmp_path, consent=None)
    for role in ("worker",):
        report = doctor(environ, role=role)
        assert not any("stay up" in n or "not mounted" in n for n in report["notes"])
        assert (report["consent_list"] or {}).get("note") is None
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        "".join(
            line
            for line in cfg.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith("consent_list")
        ),
        encoding="utf-8",
    )
    report = doctor(environ, role="worker")
    assert not any("stay up" in n or "not mounted" in n for n in report["notes"])
    assert any("consent list" in p for p in report["problems"])


def _store_words(tmp_path, value):
    from datetime import datetime

    from recordings.plaud.checks import K_KEEP_OUT_WORDS, PlaudDB

    state = State.open(tmp_path / "state")
    PlaudDB(state).put(K_KEEP_OUT_WORDS, value, now=datetime(2026, 10, 1, tzinfo=UTC))


def test_doctor_lists_the_enforced_keep_out_words(tmp_path):
    # why: stage 2b spec §12.2: doctor lists the words the worker enforces.
    environ = worker(tmp_path)
    _store_words(tmp_path, ["noarchive", "hush"])
    report = doctor(environ, role="worker")
    assert report["problems"] == []
    assert report["plaud"]["keep_out_words"] == ["noarchive", "hush"]


def test_a_keep_out_words_row_that_isnt_a_list_is_a_worker_problem(tmp_path):
    environ = worker(tmp_path)
    _store_words(tmp_path, {"not": "a list"})
    report = doctor(environ, role="worker")
    (problem,) = report["problems"]
    assert "keep-out words" in problem or "keep_out" in problem
    assert report["plaud"]["keep_out_words"] is None


def test_an_unreadable_state_db_reports_no_words_not_an_empty_list(tmp_path):
    environ = worker(tmp_path)
    (tmp_path / "state" / "state.db").write_bytes(b"not a database" * 100)
    report = doctor(environ, role="worker")
    assert report["problems"]  # _state says so
    assert report["plaud"]["keep_out_words"] is None


def test_the_sync_url_hint_only_names_the_sync_url_when_it_is_the_cause(tmp_path):
    environ = worker(tmp_path)
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            'ntfy_server = "https://ntfy.sh"', 'ntfy_server = "http://plain.example"'
        ),
        encoding="utf-8",
    )
    problems = doctor(environ, role="worker")["problems"]
    assert any("ntfy_server" in p for p in problems)
    assert not any("ntfy_server" in p and "DEADMAN_SYNC_URL_FILE" in p for p in problems)


def test_doctor_never_calls_plaud(tmp_path, monkeypatch):
    # why: stage 2b spec §17.2. `recordings plaud check` reaches Plaud; doctor never does.
    def refuse(*args, **kwargs):
        raise AssertionError("doctor made a network call")

    environ = worker(tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert doctor(environ, role="worker")["problems"] == []


def test_doctor_prints_no_value_or_hash(tmp_path):
    environ = worker(tmp_path, consent=f"- `{LISTED}` — synthetic\n{KEPT} kept: later\n")
    text = json.dumps(doctor(environ, role="worker"), ensure_ascii=False)
    for value in (SYNC_PING, TOKEN, "token-do-not-print", LISTED, KEPT):
        assert value not in text
        assert hashlib.sha256(value.encode()).hexdigest() not in text
    token_bytes = (tmp_path / "plaud" / "tokens.json").read_bytes()
    assert hashlib.sha256(token_bytes).hexdigest() not in text


def with_extra_paths(environ, *entries: str) -> None:
    cfg = Path(environ["RECORDINGS_CONFIG"])
    text = cfg.read_text(encoding="utf-8")
    start = text.index("extra_paths = ")
    end = text.index("\n", start) + 1
    listed = ", ".join(f'"{e}"' for e in entries)
    cfg.write_text(text[:start] + f"extra_paths = [{listed}]\n" + text[end:], encoding="utf-8")


@pytest.mark.parametrize(
    "entry, what",
    [
        ("/plaud", "Plaud sign-in"),
        ("/plaud/tokens.json", "Plaud sign-in"),
        ("/", "Plaud sign-in"),
        ("/state", "whole state folder"),
        ("{state}", "whole state folder"),
    ],
)
def test_extra_paths_holding_the_token_folder_or_the_whole_state_is_a_problem(
    tmp_path, entry, what
):
    # why: stage 2b spec §17.2. The token is never backed up, and state.db is backed up on its
    # own, as a consistent copy. Every role reads the one config.toml, so each says so.
    environ = server(tmp_path)
    entry = entry.format(state=tmp_path / "state")
    with_extra_paths(environ, "/backup-extra/deploy.env", "/consent", entry)
    for role, env in (("web", web_only(environ)), ("backup", environ), ("all", environ)):
        problems = doctor(env, role=role)["problems"]
        assert any(f"names {entry}," in p and what in p for p in problems), role


def test_the_manifests_folder_and_the_state_copy_are_not_problems(tmp_path):
    environ = server(tmp_path)
    with_extra_paths(environ, "/backup-extra/deploy.env", "/consent", "/state/manifests")
    assert doctor(environ, role="backup")["problems"] == []


def test_backup_without_the_consent_folder_is_a_warning(tmp_path):
    environ = server(tmp_path)
    assert not any("consent folder" in w for w in doctor(environ, role="backup")["warnings"])
    with_extra_paths(environ, "/backup-extra/deploy.env")
    warnings = doctor(environ, role="backup")["warnings"]
    assert any("consent folder" in w and 'add "/consent"' in w for w in warnings)
    for role, env in (("web", web_only(environ)), ("all", environ)):
        assert not any("consent folder" in w for w in doctor(env, role=role)["warnings"]), role
    # a consent list configured somewhere else: its own folder counts
    folder = tmp_path / "elsewhere"
    with_extra_paths(environ, "/backup-extra/deploy.env", str(folder))
    cfg = Path(environ["RECORDINGS_CONFIG"])
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f'[plaud]\nconsent_list = "{folder / "c.md"}"\n',
        encoding="utf-8",
    )
    assert not any("consent folder" in w for w in doctor(environ, role="backup")["warnings"])
