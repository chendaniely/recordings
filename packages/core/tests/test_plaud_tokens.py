"""The Plaud token store (stage 2b spec §10.3, §10.4, §21's token-store and token-endpoint items),
against the fake Plaud server's sign-in. Every token here is invented (`fake-…`, `synthetic-…`)."""

import base64
import json
import os
import stat
import subprocess
import sys
import textwrap
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fake_plaud import Response, json_response
from recordings.init import init_archive
from recordings.locks import LockTimeout
from recordings.plaud import client as client_module
from recordings.plaud import signin
from recordings.plaud import tokens as tokens_module
from recordings.plaud.checks import BLOCKER_PREFIX, K_SIGN_IN, PlaudDB
from recordings.plaud.client import (
    LIST_PATH,
    USER_PATH,
    PlaudAuthError,
    PlaudBadResponse,
    PlaudCallRefused,
    PlaudClient,
    PlaudRateLimited,
    PlaudUnavailable,
)
from recordings.plaud.endpoint import TOKEN_PATH
from recordings.plaud.tokens import (
    StaticTokens,
    TokenImportError,
    Tokens,
    TokenStore,
    TokenStoreUnsafe,
    check_sign_in,
    due,
    from_vendor,
    write_tokens,
)
from recordings.state import State

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.synthetic-signature"


@pytest.fixture
def plaud_folder(tmp_path) -> Path:
    folder = tmp_path / "plaud"
    folder.mkdir(mode=0o700)
    folder.chmod(0o700)
    return folder


@pytest.fixture
def signed(plaud_server, plaud_folder):
    """A sign-in issued by the fake, installed in the store at T0, and the store on a fake clock."""
    clock = Clock()
    fake = plaud_server.signed_in(clock)
    store = TokenStore(
        plaud_folder / "tokens.json", token_url=plaud_server.api_url + TOKEN_PATH, clock=clock
    )
    store.install(from_vendor(fake.issue(), now=clock()))
    return store, fake, clock


def _client(plaud_server, store) -> PlaudClient:
    return PlaudClient(store, api_base=plaud_server.api_url)


def _no_secret_in(text: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in text
    assert "fake-access-" not in text and "fake-refresh-" not in text


# ---- the file ------------------------------------------------------------------------------


def test_token_import_keeps_only_its_own_keys_and_installs_at_0600(signed, plaud_folder):
    # why: §10.4. The vendor's file may hold an e-mail or a user ID; ours holds the sign-in alone.
    store, _fake, _clock = signed
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(data) == {
        "version",
        "access_token",
        "refresh_token",
        "expires_at",
        "refresh_expires_at",
        "obtained_at",
        "refreshed_at",
    }
    assert data["expires_at"] == (T0 + timedelta(hours=1)).isoformat()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert sorted(p.name for p in plaud_folder.iterdir()) == ["tokens.json", "tokens.lock"]


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary(signed, monkeypatch, plaud_folder):
    store, _fake, _clock = signed
    before = store.path.read_bytes()

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(tokens_module.os, "replace", broken)
    with pytest.raises(OSError):
        write_tokens(store.path, store.read())
    assert store.path.read_bytes() == before
    assert sorted(p.name for p in plaud_folder.iterdir()) == ["tokens.json", "tokens.lock"]


def test_a_folder_or_file_wider_than_its_owner_is_refused_naming_the_chmod(signed, plaud_folder):
    # why: §10.3. The worker refuses a folder wider than 0700 or a token file wider than 0600.
    store, _fake, _clock = signed
    plaud_folder.chmod(0o750)
    with pytest.raises(TokenStoreUnsafe) as caught:
        store.access_token()
    assert "chmod 700" in str(caught.value) and str(plaud_folder) in str(caught.value)
    plaud_folder.chmod(0o700)
    store.path.chmod(0o640)
    with pytest.raises(TokenStoreUnsafe) as caught:
        store.access_token()
    assert "chmod 600" in str(caught.value)
    _no_secret_in(str(caught.value))


def test_no_file_or_one_this_store_cant_use_is_sign_in_needed(plaud_folder):
    store = TokenStore(plaud_folder / "tokens.json", token_url="https://plaud.example/t")
    with pytest.raises(PlaudAuthError):
        store.access_token()
    for text in ("not json", json.dumps({"version": 99}), json.dumps({"version": 1})):
        store.path.write_text(text, encoding="utf-8")
        store.path.chmod(0o600)
        with pytest.raises(PlaudAuthError):
            store.access_token()


def test_tokens_never_show_in_a_repr_or_an_error(signed):
    store, _fake, _clock = signed
    tokens = store.read()
    _no_secret_in(repr(tokens) + str(tokens), tokens.access_token, tokens.refresh_token)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"expires_at": int((T0 + timedelta(hours=2)).timestamp())}, T0 + timedelta(hours=2)),
        (
            {"expires_at": int((T0 + timedelta(hours=2)).timestamp()) * 1000},
            T0 + timedelta(hours=2),
        ),
        ({"expires_at": (T0 + timedelta(hours=2)).isoformat()}, T0 + timedelta(hours=2)),
        ({"expires_in": 3600}, T0 + timedelta(hours=1)),
        (
            {"expires_in": 3600, "obtained_at": (T0 - timedelta(minutes=30)).isoformat()},
            T0 + timedelta(minutes=30),
        ),
        ({}, None),
    ],
)
def test_the_vendor_files_expiry_is_read_in_any_of_its_likely_forms(extra, expected):
    # why: a guess (spike S1 was skipped): whichever form the vendor CLI writes, the earliest wins.
    got = from_vendor({"access_token": "synthetic-access", **extra}, now=T0)
    assert got.expires_at == expected


def test_a_jwt_access_token_brings_its_own_expiry_and_the_earliest_wins():
    exp = int((T0 + timedelta(minutes=40)).timestamp())
    got = from_vendor({"access_token": _jwt(exp), "expires_in": 7200}, now=T0)
    assert got.expires_at == T0 + timedelta(minutes=40)
    refresh = _jwt(int((T0 + timedelta(days=30)).timestamp()))
    got = from_vendor({"access_token": "synthetic-access", "refresh_token": refresh}, now=T0)
    assert got.refresh_expires_at == T0 + timedelta(days=30)


@pytest.mark.parametrize(
    ("data", "named"),
    [
        ([], "isn't a JSON object"),
        ({}, "access_token"),
        ({"access_token": ""}, "access_token"),
        ({"access_token": "synthetic access"}, "access_token"),
        ({"access_token": "synthetic\naccess"}, "access_token"),
        ({"access_token": "synthetic-access", "refresh_token": 7}, "refresh_token"),
        ({"access_token": "synthetic-access", "client_id": "a b"}, "client_id"),
    ],
)
def test_a_vendor_file_it_cant_use_is_refused_naming_the_key_never_a_value(data, named):
    with pytest.raises(TokenImportError) as caught:
        from_vendor(data, now=T0)
    assert named in str(caught.value) and "synthetic" not in str(caught.value)


def test_client_id_and_secret_are_kept_and_sent_with_the_refresh(signed, plaud_server):
    # why: §10.3's "a client ID or secret if S1 shows the refresh needs one".
    store, fake, _clock = signed
    tokens = store.read()
    data = {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token}
    store.install(
        from_vendor({**data, "client_id": "synthetic-client", "client_secret": "s3"}, now=T0)
    )
    assert store.refresh(tokens.access_token)
    assert fake.forms == [("client_id", "client_secret", "grant_type", "refresh_token")]
    assert store.read().client_id == "synthetic-client"


# ---- refreshing ---------------------------------------------------------------------------------


def test_due_is_5_minutes_or_10_percent_of_the_life_left():
    hour = Tokens("a", "r", expires_at=T0 + timedelta(hours=1), obtained_at=T0)
    assert not due(hour, T0 + timedelta(minutes=53))
    assert due(hour, T0 + timedelta(minutes=54))  # 6 minutes left: under 10% of an hour
    day = Tokens("a", "r", expires_at=T0 + timedelta(days=1), obtained_at=T0)
    assert due(day, T0 + timedelta(hours=21, minutes=37))  # 2h23m left: under 2h24m
    short = Tokens("a", "r", expires_at=T0 + timedelta(minutes=20), obtained_at=T0)
    assert not due(short, T0 + timedelta(minutes=14)) and due(short, T0 + timedelta(minutes=15))
    assert not due(Tokens("a", "r", expires_at=None, obtained_at=T0), T0 + timedelta(days=99))


def test_an_access_token_near_its_expiry_is_refreshed_before_use(signed, plaud_server):
    # C4.01's store half: the expired token is never sent; the new one is, after it is written.
    store, fake, clock = signed
    old = store.read()
    clock.advance(minutes=56)
    token = store.access_token()
    assert token != old.access_token and fake.refreshes == 1
    on_disk = store.read()
    assert on_disk.access_token == token and on_disk.refreshed_at == clock()
    assert on_disk.expires_at == clock() + timedelta(hours=1)
    assert on_disk.refresh_token != old.refresh_token  # rotated
    assert on_disk.obtained_at == T0
    assert _client(plaud_server, store).current_user() is not None
    assert fake.refreshes == 1  # not due again


def test_the_new_file_is_written_before_the_new_token_is_used(signed, monkeypatch):
    store, fake, clock = signed
    clock.advance(minutes=56)

    def fails(path, tokens):
        raise OSError("disk full")

    monkeypatch.setattr(tokens_module, "write_tokens", fails)
    with pytest.raises(OSError):
        store.access_token()
    assert fake.refreshes == 1  # Plaud answered, but its token was never handed out


def test_a_reply_without_a_new_refresh_token_keeps_the_old_one(signed):
    store, fake, clock = signed
    fake.rotate = False
    old = store.read()
    clock.advance(minutes=56)
    store.access_token()
    assert store.read().refresh_token == old.refresh_token


def test_a_401_refreshes_once_then_the_call_goes_through(signed, plaud_server):
    store, fake, _clock = signed
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    fake.revoke()  # Plaud stops taking the access token before its expiry
    assert _client(plaud_server, store).list_page(1).rows == ()
    assert fake.refreshes == 1
    assert [r.path for r in plaud_server.requests if r.path == LIST_PATH] == [LIST_PATH] * 2


def test_a_sign_in_without_a_refresh_token_needs_a_new_sign_in_at_its_first_401(signed):
    store, fake, _clock = signed
    tokens = store.read()
    store.install(from_vendor({"access_token": tokens.access_token}, now=T0))
    with pytest.raises(PlaudAuthError):
        store.refresh(tokens.access_token)
    assert fake.forms == []  # nothing to refresh with: nothing sent


def test_a_refresh_another_process_already_made_is_used_not_repeated(signed):
    store, fake, clock = signed
    old = store.read().access_token
    other = TokenStore(store.path, token_url=store.token_url, clock=clock)
    clock.advance(minutes=56)
    new = other.access_token()
    assert store.refresh(old) == new  # the 401 was on the old token: the file has a newer one
    assert fake.refreshes == 1


def test_one_refresh_across_two_processes(signed, plaud_server):
    # §21: the CLI and the daemon can both refresh. tokens.lock lets one through; the other reads
    # the file again under the lock and uses its token. With rotation, a second refresh with the
    # old refresh token would be refused, so two successes prove one refresh.
    store, fake, _clock = signed
    store.install(from_vendor(fake.issue(life=timedelta(seconds=30)), now=datetime.now(UTC)))
    fake.delay = 0.5
    code = textwrap.dedent(
        f"""
        from pathlib import Path
        from recordings.plaud.tokens import TokenStore
        store = TokenStore(Path({str(store.path)!r}), token_url={store.token_url!r})
        print(len(store.access_token()))
        """
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=60) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    assert fake.refreshes == 1


def test_a_refresh_lock_held_too_long_is_a_lock_timeout(signed):
    store, _fake, clock = signed
    clock.advance(minutes=56)
    impatient = TokenStore(store.path, token_url=store.token_url, clock=clock, lock_wait=0.2)
    with store._lock(), pytest.raises(LockTimeout):
        impatient.access_token()


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (json_response({"error": "invalid_grant"}, 400), PlaudAuthError),
        (json_response({"error": "invalid_grant"}, 401), PlaudAuthError),
        (json_response({}, 403), PlaudAuthError),
        (json_response({}, 404), PlaudAuthError),
        (json_response({"error": "invalid_grant"}, 200), PlaudAuthError),
        (json_response({}, 408), PlaudUnavailable),
        (json_response({}, 500), PlaudUnavailable),
        (json_response({}, 503), PlaudUnavailable),
        (Response(429, b"", {"Retry-After": "30"}), PlaudRateLimited),
        (Response(200, b"<html>sign in</html>", {"Content-Type": "text/html"}), PlaudBadResponse),
        (json_response({"token_type": "Bearer"}), PlaudBadResponse),
    ],
)
def test_the_token_endpoints_answers(signed, answer, error):
    # §21's token-endpoint faults; C4.04: a 429 from it is an outage, never `sign-in`. A refusal is
    # any 4xx but 408 and 429 (S4's codes unknown); the outages carry no sign-in meaning.
    store, fake, clock = signed
    fake.fault = answer
    clock.advance(minutes=56)
    with pytest.raises(error) as caught:
        store.access_token()
    assert type(caught.value) is error
    text = str(caught.value) + "".join(traceback.format_exception(caught.value))
    _no_secret_in(text, store.token_url)
    assert caught.value.__cause__ is None


def test_a_token_endpoint_that_times_out_is_unavailable(signed, monkeypatch):
    store, fake, clock = signed
    monkeypatch.setattr(client_module, "READ_TIMEOUT", 0.2)
    fake.fault = Response(200, b"{}", delay=1.0)
    clock.advance(minutes=56)
    with pytest.raises(PlaudUnavailable):
        store.access_token()


def test_a_crash_between_reply_and_write_with_rotation_costs_a_sign_in(signed, monkeypatch):
    # §21: with rotation and no grace, the next start finds the old file and its refresh is
    # refused; with a grace (the old refresh token still good), it recovers.
    for rotate, recovers in ((True, False), (False, True)):
        store, fake, clock = signed
        store.install(from_vendor(fake.issue(), now=clock()))
        fake.rotate = rotate
        clock.advance(minutes=56)
        with monkeypatch.context() as patch:
            patch.setattr(tokens_module, "write_tokens", _crash)
            with pytest.raises(OSError):
                store.access_token()
        if recovers:
            assert store.access_token()
        else:
            with pytest.raises(PlaudAuthError):
                store.access_token()
        clock.now = T0


def _crash(path, tokens):
    raise OSError("the process died here")


# ---- the client's one POST, and the current-user read -----------------------------------------


def test_only_get_and_the_refresh_post_reach_the_fake(signed, plaud_server):
    # §10.1: one request function sends GET, plus the refresh POST, which carries no Authorization.
    store, _fake, clock = signed
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    client = _client(plaud_server, store)
    client.current_user()
    clock.advance(minutes=56)
    client.list_page(1)
    assert {r.method for r in plaud_server.requests} == {"GET", "POST"}
    posts = [r for r in plaud_server.requests if r.method == "POST"]
    assert [r.path for r in posts] == [TOKEN_PATH]
    assert "authorization" not in posts[0].header_names
    assert all(
        "authorization" in r.header_names for r in plaud_server.requests if r.method == "GET"
    )


def test_the_current_user_read_names_the_account_by_8_hex(signed, plaud_server):
    store, fake, _clock = signed
    account = _client(plaud_server, store).current_user()
    assert account is not None and len(account) == 8 and int(account, 16) >= 0
    assert fake.account_id not in account


def test_a_current_user_path_that_isnt_there_falls_back_to_one_listing_page(signed, plaud_server):
    # why: USER_PATH is a guess; a 404 there mustn't stop every sign-in.
    store, fake, _clock = signed
    fake.user_path_missing = True
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    assert _client(plaud_server, store).current_user() is None
    assert [r.path for r in plaud_server.requests][-2:] == [USER_PATH, LIST_PATH]


def test_a_401_after_a_good_refresh_goes_through_the_current_user_read(signed, plaud_server):
    # C4.05: if the current-user read works, only that call failed; if it is a 401 too, the
    # sign-in is gone.
    store, fake, _clock = signed
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    fake.refused_paths = {LIST_PATH}
    fake.revoke()
    with pytest.raises(PlaudCallRefused):
        _client(plaud_server, store).list_page(1)
    assert fake.refreshes == 1
    assert [r.path for r in plaud_server.requests][-4:] == [
        LIST_PATH,
        TOKEN_PATH,
        LIST_PATH,
        USER_PATH,
    ]
    fake.refused_paths = set()
    fake.reject_new = True
    fake.revoke()
    with pytest.raises(PlaudAuthError):
        _client(plaud_server, store).list_page(1)
    assert [r.path for r in plaud_server.requests][-4:] == [
        LIST_PATH,
        TOKEN_PATH,
        LIST_PATH,
        USER_PATH,
    ]


def test_static_tokens_never_refresh(signed, plaud_server):
    store, fake, _clock = signed
    tokens = store.read()
    fake.revoke()
    with pytest.raises(PlaudAuthError):
        PlaudClient(StaticTokens(tokens), api_base=plaud_server.api_url).current_user()
    assert fake.forms == []


# ---- the sign-in's state: the pause and the blocker -------------------------------------------


@pytest.fixture
def db(tmp_path) -> PlaudDB:
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return PlaudDB(State.open(state))


def test_a_refused_sign_in_pauses_until_the_token_file_changes(signed, plaud_server, db):
    # C4.02 and C4.03's store half: a refused refresh opens the blocker and the pause; scheduled
    # runs then wait for a new token file; a run after it succeeds ends both.
    store, fake, clock = signed
    client = _client(plaud_server, store)
    assert check_sign_in(store, client, db, now=clock()) is None
    assert signin.paused(db, store.path) is None
    assert db.get(K_SIGN_IN)["expires_at"] == (T0 + timedelta(hours=1)).isoformat()
    fake.refuse_refresh()
    clock.advance(minutes=56)
    with pytest.raises(PlaudAuthError):
        check_sign_in(store, client, db, now=clock())
    assert signin.paused(db, store.path) == signin.REFUSED
    assert db.get(BLOCKER_PREFIX + "sign-in")["since"] == clock().isoformat()
    first = db.get(K_SIGN_IN)["refused_at"]
    clock.advance(minutes=20)
    with pytest.raises(PlaudAuthError):  # a Sync now tries once anyway: the same file, refused
        check_sign_in(store, client, db, now=clock())
    assert db.get(K_SIGN_IN)["refused_at"] == first  # the first refusal's time is kept
    fake.refuse = None
    store.install(from_vendor(fake.issue(), now=clock()))  # make plaud-sign-in
    assert signin.paused(db, store.path) is None  # the next scheduled run tries
    assert check_sign_in(store, client, db, now=clock()) is not None  # the current-user read
    assert db.get(BLOCKER_PREFIX + "sign-in") is None
    assert "refused_at" not in db.get(K_SIGN_IN)


def test_no_token_file_is_its_own_pause_and_a_refusal_at_a_run(plaud_folder, plaud_server, db):
    store = TokenStore(plaud_folder / "tokens.json", token_url="https://plaud.example/t")
    assert signin.paused(db, store.path) == signin.NO_SIGN_IN
    with pytest.raises(PlaudAuthError):
        check_sign_in(store, _client(plaud_server, store), db, now=T0)
    assert db.get(BLOCKER_PREFIX + "sign-in") is not None
    assert plaud_server.requests == []  # no token, no call


def test_the_sign_in_state_holds_no_token(signed, plaud_server, db):
    store, _fake, clock = signed
    tokens = store.read()
    check_sign_in(store, _client(plaud_server, store), db, now=clock(), probe=True)
    text = json.dumps(db.with_prefix(""))
    _no_secret_in(text, tokens.access_token, tokens.refresh_token)


def test_unsafe_modes_is_doctors_rule_too(plaud_folder):
    # one rule for the store and doctor (§10.3, §17.2)
    (plaud_folder / "tokens.json").write_text("{}", encoding="utf-8")
    (plaud_folder / "tokens.json").chmod(0o644)
    plaud_folder.chmod(0o755)
    lines = signin.unsafe_modes(plaud_folder / "tokens.json")
    assert len(lines) == 2 and "chmod 700" in lines[0] and "chmod 600" in lines[1]
    os.chmod(plaud_folder, 0o700)
