"""The `[plaud]` settings and the sync's secrets (stage 2b spec §17.4)."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from recordings.config import SECRETS, Config, ConfigError
from recordings.plaud.endpoint import API_BASE
from recordings.plaud.reconcile import DEFAULT_AUTO_PRIVATE
from recordings.plaud.settings import BAD_WORD, PlaudSettings, plaud_settings

REPO = Path(__file__).resolve().parents[3]


def cfg(plaud: object = None) -> Config:
    data = {} if plaud is None else {"plaud": plaud}
    return Config(
        path=None,
        archive_path=None,
        writer_id=None,
        default_timezone="America/Vancouver",
        base_url=None,
        data=data,
    )


def test_unset_plaud_keys_take_the_spec_defaults():
    # why: §17.4, "Unset means". 20 minutes, so the schedule starts by itself after the catch-up
    # (§11.6); no consent list is a consent stop, never "exclude nothing".
    assert plaud_settings(cfg()) == PlaudSettings(
        schedule_minutes=20,
        auto_import=True,
        full_sweep_days=7,
        keep_out_word="noarchive",
        keep_out_word_ok=True,
        auto_private_patterns=DEFAULT_AUTO_PRIVATE,
        consent_list=None,
        token_store=Path("/plaud/tokens.json"),
        api_base=API_BASE,
    )
    assert plaud_settings(cfg({"consent_list": "  "})).consent_list is None


def test_set_plaud_keys_are_read():
    got = plaud_settings(
        cfg(
            {
                "schedule_minutes": 0,
                "auto_import": False,
                "full_sweep_days": 1,
                "keep_out_word": "skip-me_2",
                "consent_list": "/consent/consent-list.md",
                "token_store": "/plaud/other.json",
                "api_base": "http://127.0.0.1:8123",
            }
        )
    )
    assert (got.schedule_minutes, got.auto_import, got.full_sweep_days) == (0, False, 1)
    assert (got.keep_out_word, got.keep_out_word_ok) == ("skip-me_2", True)
    assert got.consent_list == Path("/consent/consent-list.md")
    assert (got.token_store, got.api_base) == (Path("/plaud/other.json"), "http://127.0.0.1:8123")


@pytest.mark.parametrize("word", ["no archive", "x" * 33, "", "naïve", 42, ["noarchive"]])
def test_an_invalid_keep_out_word_is_flagged_not_raised(word):
    # why: §12.2. The worker idles and says why; the web app and the backup go on.
    got = plaud_settings(cfg({"keep_out_word": word}))
    assert got.keep_out_word_ok is False
    assert BAD_WORD == "configuration: `keep_out_word` is invalid"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schedule_minutes", "20"),
        ("schedule_minutes", -1),
        ("schedule_minutes", True),
        ("auto_import", "yes"),
        ("full_sweep_days", 0),
        ("consent_list", 7),
        ("token_store", "tokens.json"),
        ("auto_private_patterns", ["("]),
    ],
)
def test_a_wrong_value_names_its_key(key, value):
    with pytest.raises(ConfigError, match=rf"\[plaud\] {key}"):
        plaud_settings(cfg({key: value}))
    with pytest.raises(ConfigError, match=r"\[plaud\] must be a table"):
        plaud_settings(cfg("not a table"))


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://plaud.example/api", True),
        ("http://127.0.0.1:8123", True),
        ("http://plaud.example/api", False),
        ("ftp://plaud.example", False),
    ],
)
def test_api_base_must_be_https_or_loopback_http(url, ok):
    if ok:
        assert plaud_settings(cfg({"api_base": url})).api_base == url
        return
    with pytest.raises(ConfigError, match=r"\[plaud\] api_base") as caught:
        plaud_settings(cfg({"api_base": url}))
    assert "plaud.example" not in str(caught.value)  # the setting is named, never its value


def test_recheck_days_and_the_static_token_are_gone():
    # why: §17.4. The rolling sweep replaced recheck_days' hot window, and the sign-in is a file
    # the worker owns (token_store), never a secret in the environment.
    template = tomllib.loads((REPO / "config.example.toml").read_text(encoding="utf-8"))
    assert "recheck_days" not in template["plaud"]
    assert "plaud_token" not in SECRETS
    assert "RECORDINGS_PLAUD_TOKEN" not in (REPO / "config.example.toml").read_text("utf-8")
    assert SECRETS["deadman_sync_url"].env == "RECORDINGS_DEADMAN_SYNC_URL"


def test_the_sign_in_and_local_mk_are_never_committed_or_built_in():
    # why: §17.4. The sign-in is a secret; local.mk names the server. Docker's patterns match from
    # the build context's root, so a token file in a subfolder needs `**/` there; git's match at
    # any depth.
    wanted = {
        ".gitignore": ("tokens*.json", "**/.plaud/", "local.mk"),
        ".dockerignore": ("**/tokens*.json", "**/.plaud/", "local.mk"),
    }
    for name, patterns in wanted.items():
        lines = (REPO / name).read_text(encoding="utf-8").splitlines()
        for pattern in patterns:
            assert pattern in lines, (name, pattern)


def test_reading_the_settings_imports_no_client_even_when_called():
    # why: spec §9.1 ("It imports no client, token or sync module"), the plan's decision 31. The
    # web app imports BAD_WORD and the defaults, and calls plaud_settings for its views (Task 19),
    # so neither the import nor the call may load the Plaud client.
    code = (
        "import sys\n"
        "from recordings.config import Config\n"
        "from recordings.plaud.settings import plaud_settings\n"
        "plaud = {'api_base': 'https://plaud.example/api'}\n"
        "plaud_settings(Config(path=None, archive_path=None, writer_id=None,\n"
        "    default_timezone='UTC', base_url=None, data={'plaud': plaud}))\n"
        "print('recordings.plaud.client' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
