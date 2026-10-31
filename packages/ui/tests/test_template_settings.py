"""Every setting in config.example.toml reaches the code that reads it (spec §5, §16).

The table gives each template key a distinctive value and the reader that must hand it back. A key
added to the template without a row fails the test, so a setting can't be documented and then
silently ignored. Settings for later stages are listed in LATER, with their stage.
"""

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

from recordings import alerts, backup, mirror
from recordings.config import SECRETS, load_config, secret, secret_path
from recordings.disk import disk_thresholds
from recordings.plaud.consent import consent_list_path
from recordings.plaud.reconcile import patterns_from_config
from recordings.plaud.settings import plaud_settings
from recordings_ui.status import status_settings

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "config.example.toml"
LATER_SECTIONS = {"spark": "4", "models": "4", "prompts": "4", "watched_folder": "5"}
LATER: dict[tuple[str, str], str] = {}
LATER_SECRETS = {"spark_api_key": "4", "claude_oauth_token": "4"}
SECRET_VALUES = {
    "ntfy_topic": "topic-7",
    "deadman_url": "https://hc-7.example/ping/7",
    "deadman_sync_url": "https://hc-7.example/ping/sync-7",
}


def table(tmp: Path) -> dict:
    """(section, key) -> (the value written, how to read it back, what the reader must return)."""
    return {
        ("archive", "path"): (
            str(tmp / "archive-7"),
            lambda r: r.cfg.archive_path,
            tmp / "archive-7",
        ),
        ("archive", "writer_id"): ("writer-7", lambda r: r.cfg.writer_id, "writer-7"),
        ("archive", "default_timezone"): (
            "Asia/Kathmandu",
            lambda r: r.status.timezone_name,
            "Asia/Kathmandu",
        ),
        ("server", "base_url"): (
            "https://rec-7.example:8443",
            lambda r: r.cfg.base_url,
            "https://rec-7.example:8443",
        ),
        ("server", "allowed_hosts"): (
            ["rec-7.example"],
            lambda r: r.cfg.allowed_hosts,
            ("rec-7.example",),
        ),
        ("state", "path"): (str(tmp / "state-7"), lambda r: r.status.state_path, tmp / "state-7"),
        ("index", "path"): (
            str(tmp / "index-7.db"),
            lambda r: r.cfg.index_path,
            tmp / "index-7.db",
        ),
        ("disk", "warn_free_percent"): (37.5, lambda r: r.disk["warn"], 37.5),
        ("disk", "stop_free_percent"): (3.25, lambda r: r.status.stop, 3.25),
        ("nas", "ssh"): ("mirror-7@nas-7", lambda r: r.backup.nas.ssh, "mirror-7@nas-7"),
        ("nas", "port"): (2207, lambda r: r.mirror.nas.port, 2207),
        ("backup", "repository"): (
            "rest:https://nas-7:8007/rec-7/",
            lambda r: r.backup.repository,
            "rest:https://nas-7:8007/rec-7/",
        ),
        ("backup", "rest_username"): (
            "rest-user-7",
            lambda r: r.backup.rest_username,
            "rest-user-7",
        ),
        ("backup", "cacert"): (
            str(tmp / "rest-server-7.crt"),
            lambda r: r.backup.cacert,
            tmp / "rest-server-7.crt",
        ),
        ("backup", "restic"): ("/opt/restic-7", lambda r: r.backup.restic, "/opt/restic-7"),
        ("backup", "extra_paths"): (
            [str(tmp / "deploy-7.env")],
            lambda r: tmp / "deploy-7.env" in r.backup.paths,
            True,
        ),
        ("backup", "keep_hourly"): (23, lambda r: r.backup.keep.hourly, 23),
        ("backup", "keep_daily"): (13, lambda r: r.backup.keep.daily, 13),
        ("backup", "keep_weekly"): (7, lambda r: r.backup.keep.weekly, 7),
        ("backup", "keep_monthly"): (11, lambda r: r.backup.keep.monthly, 11),
        ("backup", "prune"): (False, lambda r: r.backup.prune, False),
        ("backup", "nas_repo_path"): ("/rec-7", lambda r: r.backup.nas_repo_path, "/rec-7"),
        ("backup", "min_nas_free_gb"): (17, lambda r: r.backup.min_nas_free_bytes, 17 * 1024**3),
        ("backup", "restore_scratch"): (
            str(tmp / "restore-7"),
            lambda r: r.backup.restore_scratch,
            tmp / "restore-7",
        ),
        ("backup", "cache_dir"): (
            str(tmp / "cache-7"),
            lambda r: r.backup.cache_dir,
            tmp / "cache-7",
        ),
        ("mirror", "target"): (
            "mirror-7@nas-7:/mirror-7/",
            lambda r: r.mirror.target,
            "mirror-7@nas-7:/mirror-7/",
        ),
        ("mirror", "rsync"): ("/opt/rsync-7", lambda r: r.mirror.rsync, "/opt/rsync-7"),
        ("mirror", "max_delete"): (7, lambda r: r.mirror.max_delete, 7),
        ("mirror", "no_perms"): (True, lambda r: r.mirror.no_perms, True),
        ("alerts", "ntfy_server"): (
            "https://ntfy-7.example",
            lambda r: r.alerts.ntfy_server,
            "https://ntfy-7.example",
        ),
        ("plaud", "auto_private_patterns"): (
            ["^\\s*secret-7"],
            lambda r: (r.patterns, r.plaud.auto_private_patterns),
            (("^\\s*secret-7",), ("^\\s*secret-7",)),
        ),
        ("plaud", "consent_list"): (
            str(tmp / "consent-7.md"),
            lambda r: (r.consent, r.plaud.consent_list),
            (tmp / "consent-7.md", tmp / "consent-7.md"),
        ),
        ("plaud", "schedule_minutes"): (37, lambda r: r.plaud.schedule_minutes, 37),
        ("plaud", "auto_import"): (False, lambda r: r.plaud.auto_import, False),
        ("plaud", "full_sweep_days"): (3, lambda r: r.plaud.full_sweep_days, 3),
        ("plaud", "keep_out_word"): (
            "skip-7",
            lambda r: (r.plaud.keep_out_word, r.plaud.keep_out_word_ok),
            ("skip-7", True),
        ),
        ("plaud", "token_store"): (
            str(tmp / "tokens-7.json"),
            lambda r: r.plaud.token_store,
            tmp / "tokens-7.json",
        ),
        # Commented out in the template, so the key check doesn't ask for it: set here.
        ("plaud", "api_base"): (
            "https://plaud-7.example/api",
            lambda r: r.plaud.api_base,
            "https://plaud-7.example/api",
        ),
    }


def write_config(tmp: Path, rows: dict) -> Path:
    """config.toml from the table. JSON's strings, numbers, booleans and lists are valid TOML."""
    (tmp / "rest-server-7.crt").write_text("a test certificate\n", encoding="utf-8")
    sections: dict[str, list[str]] = {}
    for (section, key), (value, _read, _want) in rows.items():
        sections.setdefault(section, []).append(f"{key} = {json.dumps(value)}")
    path = tmp / "config.toml"
    path.write_text(
        "".join(f"[{name}]\n" + "\n".join(lines) + "\n" for name, lines in sections.items()),
        encoding="utf-8",
    )
    return path


def read_all(config: Path, environ: dict[str, str]) -> SimpleNamespace:
    env = {"RECORDINGS_CONFIG": str(config), **environ}
    cfg = load_config(env)
    return SimpleNamespace(
        env=env,
        cfg=cfg,
        status=status_settings(cfg),
        disk=disk_thresholds(cfg),
        patterns=patterns_from_config(cfg.data),
        consent=consent_list_path(cfg.data),
        plaud=plaud_settings(cfg),
        backup=backup.from_config(cfg, env),
        mirror=mirror.from_config(cfg, env),
        alerts=alerts.from_config(cfg, env),
    )


def test_every_template_setting_reaches_its_reader(tmp_path):
    rows = table(tmp_path)
    template = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    keys = {
        (section, key)
        for section, body in template.items()
        if section not in LATER_SECTIONS
        for key in body
    }
    missing = sorted(keys - rows.keys() - LATER.keys())
    assert missing == [], "give each a row in table(), with its reader, or list it in LATER"
    known_hosts = tmp_path / "known_hosts"  # [nas] ssh and a remote mirror need the pinned host key
    known_hosts.write_text("nas-7 ssh-ed25519 AAAAexample\n", encoding="utf-8")
    r = read_all(
        write_config(tmp_path, rows), {"RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(known_hosts)}
    )
    wrong = {
        f"[{section}] {key}": read(r)
        for (section, key), (_value, read, want) in rows.items()
        if read(r) != want
    }
    assert wrong == {}


def test_every_secret_reaches_its_reader(tmp_path):
    files, values = {}, {}
    for name in SECRETS:
        values[name] = SECRET_VALUES.get(name, f"{name}-7")
        files[name] = tmp_path / f"{name}.secret"
        files[name].write_text(values[name] + "\n", encoding="utf-8")
    env = {f"{spec.env}_FILE": str(files[name]) for name, spec in SECRETS.items()}
    for name, spec in SECRETS.items():  # config's own readers first
        if spec.file_only:
            assert secret_path(name, env) == files[name], name
        else:
            assert secret(name, env) == values[name], name
    r = read_all(write_config(tmp_path, table(tmp_path)), env)
    restic = backup.restic_env(r.backup, r.env)
    reaches = {
        "restic_password": restic.get("RESTIC_PASSWORD_FILE") == str(files["restic_password"]),
        "rest_password": restic.get("RESTIC_REST_PASSWORD") == values["rest_password"],
        "nas_ssh_key": r.backup.nas.key == files["nas_ssh_key"],
        "nas_known_hosts": r.mirror.nas.known_hosts == files["nas_known_hosts"],
        "ntfy_topic": r.alerts.topic == values["ntfy_topic"],
        "ntfy_token": r.alerts.token == values["ntfy_token"],
        "deadman_url": r.alerts.deadman_url == values["deadman_url"],
        "deadman_sync_url": r.alerts.deadman_sync_url == values["deadman_sync_url"],
    }
    assert set(SECRETS) == reaches.keys() | LATER_SECRETS.keys(), (
        "give each secret a reader check here, or list it in LATER_SECRETS with its stage"
    )
    assert [name for name, ok in reaches.items() if not ok] == []


def test_the_schedule_is_20_minutes_in_the_template_and_when_unset(tmp_path):
    # why: stage 2b §17.4, §11.6. The schedule starts by itself after the first catch-up, with no
    # config edit or restart: 20 in the template, and 20 when unset.
    assert tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))["plaud"]["schedule_minutes"] == 20
    path = tmp_path / "config.toml"
    path.write_text("[plaud]\n", encoding="utf-8")
    assert plaud_settings(load_config({"RECORDINGS_CONFIG": str(path)})).schedule_minutes == 20
