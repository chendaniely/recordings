"""`recordings plaud token-import -` and `recordings plaud check` (stage 2b spec §10.3, §10.5,
§21's `token-import` item), against the fake Plaud server's sign-in. Every token is invented."""

import io
import json
import re
import socket
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fake_plaud import Response
from recordings.cli import main
from recordings.init import init_archive
from recordings.plaud import signin
from recordings.plaud.checks import BLOCKER_PREFIX, K_SIGN_IN, PlaudDB
from recordings.plaud.signin import NO_SIGN_IN, paused, record_refused
from recordings.plaud.tokens import TokenStore, from_vendor
from recordings.state import State

LINE = (
    r"signed in: yes · access token expires \d{4}-\d{2}-\d{2} \d{2}:\d{2} · refresh token: present"
    r" · account [0-9a-f]{8}"
)


@pytest.fixture
def env(tmp_path, monkeypatch, plaud_server):
    """A state.db, an empty sign-in folder (0700) and config.toml pointing [plaud] at the fake."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    folder = tmp_path / "plaud"
    folder.mkdir(mode=0o700)
    folder.chmod(0o700)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\ndefault_timezone = "UTC"\n'
        f'[state]\npath = "{state}"\n'
        f'[plaud]\ntoken_store = "{folder / "tokens.json"}"\napi_base = "{plaud_server.api_url}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    fake = plaud_server.signed_in()  # the real clock: the CLI reads it too
    return SimpleNamespace(
        folder=folder,
        token=folder / "tokens.json",
        db=PlaudDB(State.open(state)),
        fake=fake,
        server=plaud_server,
        monkeypatch=monkeypatch,
    )


def stdin(env, data: bytes) -> None:
    env.monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(data)))


def token_import(env, vendor: object, *extra: str) -> int:
    raw = vendor if isinstance(vendor, bytes) else json.dumps(vendor).encode()
    stdin(env, raw)
    return main(["plaud", "token-import", "-", *extra])


def no_secret(text: str, vendor: dict | None = None) -> None:
    assert "fake-access-" not in text and "fake-refresh-" not in text
    assert "someone@example.invalid" not in text and "synthetic-user" not in text
    assert "synthetic-account" not in text
    for value in (vendor or {}).values():
        assert str(value) not in text or len(str(value)) < 8


def test_token_import_installs_a_sign_in_that_works_and_prints_one_line(env, capsys):
    # §10.5 step 4: the keys checked, one cheap call, installed atomically at 0600; it prints
    # only the line, with the account as 8 hex.
    vendor = env.fake.issue()
    assert token_import(env, vendor) == 0
    out = capsys.readouterr()
    assert out.err == "" and len(out.out.splitlines()) == 1

    assert re.fullmatch(LINE, out.out.strip()), out.out
    no_secret(out.out, vendor)
    assert stat.S_IMODE(env.token.stat().st_mode) == 0o600
    data = json.loads(env.token.read_text(encoding="utf-8"))
    assert data["access_token"] == vendor["access_token"]
    assert "email" not in data and "user_id" not in data and "token_type" not in data
    assert [r.path for r in env.server.requests] == ["/open/third-party/users/me"]
    state = env.db.get(K_SIGN_IN)
    assert state["refresh_token"] is True and "refused_at" not in state
    assert json.loads(json.dumps(state)) == state
    no_secret(json.dumps(state))


@pytest.mark.parametrize(
    "vendor",
    [
        b"not json at all",
        b"[]",
        b'{"refresh_token": "fake-refresh-x"}',
        b"x" * (64 * 1024 + 1),
        b'{"access_token": "fake-access with a space"}',
    ],
)
def test_a_bad_token_file_installs_nothing_and_exits_77(env, vendor, capsys):
    # §21: a bad file installs nothing and exits 77, before any call.
    assert token_import(env, vendor) == 77
    err = capsys.readouterr().err
    assert "nothing installed" in err
    no_secret(err)
    assert not env.token.exists() and env.server.requests == []


def test_a_sign_in_plaud_refuses_installs_nothing_and_exits_77(env, capsys):
    vendor = env.fake.issue()
    env.fake.revoke()
    assert token_import(env, vendor) == 77
    err = capsys.readouterr().err
    assert "signed in: no" in err and "nothing installed" in err and "make plaud-sign-in" in err
    assert not env.token.exists()
    assert env.fake.forms == []  # never refreshed: the import has nothing installed to refresh


def test_plaud_unreachable_installs_nothing_and_exits_69(env, capsys):
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    cfg = env.token.parent.parent / "config.toml"
    text = cfg.read_text(encoding="utf-8").replace(env.server.api_url, f"http://127.0.0.1:{port}")
    cfg.write_text(text, encoding="utf-8")
    assert token_import(env, env.fake.issue()) == 69
    assert "nothing installed" in capsys.readouterr().err
    assert not env.token.exists()


def test_a_missing_or_open_folder_is_refused_before_any_call(env, capsys):
    env.folder.chmod(0o755)
    assert token_import(env, env.fake.issue()) == 78
    assert "chmod 700" in capsys.readouterr().err
    env.folder.chmod(0o700)
    env.folder.rmdir()
    assert token_import(env, env.fake.issue()) == 78
    assert "install -d -m 700" in capsys.readouterr().err
    assert env.server.requests == []


def test_a_new_sign_in_lifts_the_pause_and_the_blocker(env, capsys):
    # §10.5: "the new file clears the worker's sign-in pause".
    assert token_import(env, env.fake.issue()) == 0
    record_refused(env.db, env.token, now=datetime.now(UTC))
    assert paused(env.db, env.token) == signin.REFUSED
    assert token_import(env, env.fake.issue()) == 0
    assert paused(env.db, env.token) is None
    assert env.db.get(BLOCKER_PREFIX + "sign-in") is None


def test_json_output_has_the_same_facts_and_no_token(env, capsys):
    vendor = env.fake.issue()
    assert token_import(env, vendor, "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["signed_in"] is True and out["installed"] is True and out["refresh_token"] is True
    assert len(out["account"]) == 8
    no_secret(json.dumps(out), vendor)


def test_check_reads_the_current_user_and_records_a_good_sign_in(env, capsys):

    assert token_import(env, env.fake.issue()) == 0
    capsys.readouterr()
    record_refused(env.db, env.token, now=datetime.now(UTC))  # a pause the check may lift
    assert main(["plaud", "check"]) == 0
    out = capsys.readouterr().out.strip()
    assert re.fullmatch(LINE, out), out
    assert env.db.get(BLOCKER_PREFIX + "sign-in") is None and paused(env.db, env.token) is None


def test_check_refreshes_a_token_near_its_expiry_first(env, capsys):
    vendor = env.fake.issue(life=timedelta(seconds=90))
    store = TokenStore(env.token, token_url=env.server.api_url + "/oauth/token")
    store.install(from_vendor(vendor, now=datetime.now(UTC)))
    assert main(["plaud", "check"]) == 0
    assert env.fake.refreshes == 1
    assert "signed in: yes" in capsys.readouterr().out


def test_check_says_sign_in_needed_and_records_it(env, capsys):
    assert main(["plaud", "check"]) == 77
    out = capsys.readouterr().out
    assert "signed in: no" in out and "no sign-in yet" in out and "`make plaud-sign-in`" in out
    assert paused(env.db, env.token) == NO_SIGN_IN
    assert token_import(env, env.fake.issue()) == 0
    capsys.readouterr()
    env.fake.revoke()
    env.fake.refuse_refresh()
    assert main(["plaud", "check", "--json"]) == 77
    out = json.loads(capsys.readouterr().out)
    assert out["signed_in"] is False and out["sign_in_needed"] == "Plaud refused the sign-in"
    assert env.db.get(BLOCKER_PREFIX + "sign-in") is not None
    assert paused(env.db, env.token) == signin.REFUSED


def test_check_says_an_outage_is_not_a_sign_in_problem(env, capsys):
    assert token_import(env, env.fake.issue()) == 0
    capsys.readouterr()
    env.fake.revoke()

    env.fake.fault = Response(503, b"")
    assert main(["plaud", "check"]) == 69
    assert "Plaud unavailable" in capsys.readouterr().err
    assert env.db.get(BLOCKER_PREFIX + "sign-in") is None
