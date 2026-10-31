"""scripts/plaud_spike.py (stage 2b spec §18): its guard refuses consent-listed, private and
kept-out recordings and counts them, a consent stop or a guard it can't build refuses before any
call, and nothing it prints holds a title, a token or a whole Plaud ID. Run against the fake Plaud
server only; every ID, title, token and text here is synthetic."""

import collections
import importlib.util
import json
import os
import sqlite3
import stat
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_plaud import Response
from recordings.archive import Archive, RawSource
from recordings.config import load_config
from recordings.init import init_archive
from recordings.models import TagRef
from recordings.plaud.client import FILE_PATH, LIST_PATH, USER_AGENT
from recordings.plaud.reconcile import DEFAULT_AUTO_PRIVATE

REPO = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
NORMAL = f"{0x42:032x}"  # in the archive, untagged
PRIVATE = f"{0xA7:032x}"  # in the archive, tagged private
CONSENTED = f"{0xC0:032x}"  # on the consent list
CLIP = f"{0xC1:032x}"  # S9's throwaway clip: listed, not in the archive
TOKEN = "synthetic-access-token-0042"
REFRESH = "synthetic-refresh-token-0042"
TITLES = ("Synthetic 042 meeting with Ada", "Synthetic consent-listed", "Synthetic 7 private")
WORDS_SAID = "Synthetic words nobody may print"


@pytest.fixture(scope="module")
def spike():
    """The script, loaded by path. It is registered first: its dataclasses look their module up."""
    path = REPO / "scripts" / "plaud_spike.py"
    spec = importlib.util.spec_from_file_location("plaud_spike", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["plaud_spike"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["plaud_spike"]


def _plaud(ref: str) -> tuple[RawSource, ...]:
    return (RawSource(kind="plaud", ref=ref, added_at=T0, payload=b'{"id": 1}'),)


@pytest.fixture
def env(tmp_path, writer, make_incoming, plaud_server, monkeypatch, spike):
    """A synthetic archive (one plain recording, one private), a consent list, a session config
    pointing the spike at both and at the fake, the deployment's config.toml in $REPO_DIR, and a
    session token file in the spike folder."""
    writer.add(make_incoming(sources=_plaud("of_" + NORMAL)))
    writer.add(make_incoming(sources=_plaud(PRIVATE.upper()), tags=(TagRef(tag="private"),)))
    consent = tmp_path / "consent" / "consent-list.md"
    consent.parent.mkdir()
    consent.write_text(f"# Never mirror\n- `of_{CONSENTED}` — asked\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"  # the deployment's checkout: only its config.toml is read
    repo.mkdir()
    (repo / "config.toml").write_text("[plaud]\n", encoding="utf-8")
    monkeypatch.setenv("REPO_DIR", str(repo))

    def configure(extra: str = "", consent_text: str | None = None, archive=None) -> None:
        if consent_text is not None:
            consent.write_text(consent_text, encoding="utf-8")
        config = tmp_path / "config.toml"
        config.write_text(
            f'[archive]\npath = "{archive or writer.root}"\n[state]\npath = "{writer.state.path}"\n'
            f'[plaud]\nconsent_list = "{consent}"\napi_base = "{plaud_server.api_url}"\n{extra}',
            encoding="utf-8",
        )
        monkeypatch.setenv("RECORDINGS_CONFIG", str(config))

    configure()
    tokens = home / "spike" / "home" / ".plaud" / "tokens.json"
    tokens.parent.mkdir(parents=True)
    tokens.write_text(json.dumps({"access_token": TOKEN}), encoding="utf-8")
    (home / "spike").chmod(0o700)
    monkeypatch.setattr(spike, "sleep", lambda seconds: None)
    return collections.namedtuple("Env", "home configure fake tmp writer repo tokens")(
        home, configure, plaud_server, tmp_path, writer, repo, tokens
    )


def _guard(spike):
    cfg = load_config(os.environ)
    return spike.build_guard(
        cfg, Archive(cfg.archive_path), Path(os.environ["REPO_DIR"]) / "config.toml"
    )


def test_the_guard_refuses_a_consent_listed_id_and_counts_it(env, spike):
    guard = _guard(spike)
    assert guard.consent == {CONSENTED}
    assert not guard.allows("OF_" + CONSENTED.upper(), "Week 4")
    assert guard.allows(NORMAL, "Week 4")
    assert guard.refused == collections.Counter(consent=1)
    assert guard.report() == "refused: 1 (consent 1)"


def test_the_guard_refuses_a_private_tagged_recording_and_counts_it(env, spike):
    guard = _guard(spike)
    assert guard.private_ids == {PRIVATE}  # the recording's ref was upper case: canonical here
    assert not guard.allows(PRIVATE, "Week 4") and not guard.allows("of_" + PRIVATE, "Week 4")
    assert guard.report() == "refused: 2 (private 2)"


def test_the_guard_refuses_a_keep_out_title_unless_it_is_the_own_clip(env, spike):
    guard = _guard(spike)
    assert not guard.allows(NORMAL, "NoArchive: synthetic")
    assert guard.allows(NORMAL, "NoArchive: synthetic", own_clip=True)
    env.configure('keep_out_word = "skipme"\n')
    guard = _guard(spike)
    assert guard.words == ("skipme", "noarchive")  # the default stays: erring towards refusing
    assert not guard.allows(NORMAL, "skipme 7") and not guard.allows(NORMAL, "noarchive 7")
    assert guard.report() == "refused: 2 (keep-out 2)"


def test_the_guard_refuses_a_pattern_title_even_for_the_own_clip(env, spike):
    # why: decision 24. --own-clip skips the keep-out check and nothing else.
    guard = _guard(spike)
    assert guard.patterns == DEFAULT_AUTO_PRIVATE
    assert not guard.allows(NORMAL, "Private: synthetic", own_clip=True)
    assert not guard.allows(NORMAL, "ｐｒｉｖａｔｅ notes", own_clip=True)  # folded, as §13.1 says
    assert not guard.allows(NORMAL, None, own_clip=True)  # no title: it can't be checked
    assert guard.report() == "refused: 3 (no title 1, pattern 2)"


@pytest.mark.parametrize(
    "consent_text",
    [f"- `{CONSENTED[:31]}` — a typo\n", "", f"We kept {CONSENTED} on purpose.\n"],
)
def test_a_consent_stop_refuses_before_any_call(env, spike, capsys, consent_text):
    env.configure(consent_text=consent_text)
    assert spike.main(["s5", "--home", str(env.home)]) == 78
    out = capsys.readouterr().out
    assert out.startswith("refused before any call: the consent list")
    assert env.fake.requests == []
    assert CONSENTED not in out and CONSENTED[:31] not in out


def _serve_account(fake, envelope_title=TITLES[0]) -> None:
    """`envelope_title` is a string, or a function called for each files/{id} request."""
    rows = [
        {"id": "of_" + NORMAL, "name": TITLES[0], "start_at": "2026-08-01T16:00:00Z"},
        {"id": CONSENTED.upper(), "name": TITLES[1], "start_at": "2026-08-02T16:00:00Z"},
        {"id": "of_" + PRIVATE, "name": TITLES[2], "start_at": "2026-08-03T16:00:00Z"},
    ]

    def listing(query):
        return Response(
            200, json.dumps({"data_list": rows if query["page"] == ["1"] else []}).encode()
        )

    fake.route("GET", LIST_PATH, listing)
    segments = [{"content": WORDS_SAID, "start_time": 0, "end_time": 900, "embeddingKey": "ek-1"}]
    block = fake.link("transcript", json.dumps(segments).encode())

    def envelope(query):
        title = envelope_title() if callable(envelope_title) else envelope_title
        body = {
            "id": "of_" + NORMAL,
            "name": title,
            "presigned_url": f"{fake.storage_url}/audio.mp3?X-Amz-Expires=300",
            "source_list": [{"data_type": "transaction", "data_content": "", "data_link": block}],
            "note_list": [
                {
                    "data_type": "auto_sum_note",
                    "data_tab_name": "Summary",
                    "data_content": WORDS_SAID,
                }
            ],
        }
        return Response(200, json.dumps(body).encode())

    fake.route("GET", FILE_PATH.format("of_" + NORMAL), envelope)


def test_nothing_printed_holds_a_title_a_token_or_a_whole_id(env, spike, capsys):
    # why: §18's print list. A spike's output is read aloud in a session and may be pasted
    # anywhere: counts, yes or no, times, field names and 8-hex prefixes only.
    _serve_account(env.fake)
    assert spike.main(["s5", "--home", str(env.home)]) == 0
    s5 = capsys.readouterr().out
    assert "pass 1: 3 IDs, 0 repeats, 0 malformed, 2 pages" in s5
    assert "same ID set: yes" in s5
    assert spike.main(["s8", "--home", str(env.home), "--id", NORMAL, "--id", CONSENTED]) == 0
    s8 = capsys.readouterr().out
    assert f"{spike.short(NORMAL)}: part transcript: matched yes" in s8
    assert f"{spike.short(CONSENTED)}: refused by the guard" in s8
    assert s8.rstrip().endswith("refused: 1 (consent 1)")
    out = s5 + s8
    for secret in (*TITLES, TOKEN, WORDS_SAID, "Summary", "ek-1", "X-Amz", env.fake.storage_url):
        assert secret not in out, secret
    for whole in (NORMAL, PRIVATE, CONSENTED):
        assert whole not in out.lower(), whole
    assert {r.method for r in env.fake.requests} == {"GET"}


def test_the_spike_folder_must_be_0700_and_outside_the_archive_state_and_plaud(
    env, spike, monkeypatch
):
    folder = spike.spike_folder(env.home)
    assert folder == env.home / "spike" and stat.S_IMODE(folder.stat().st_mode) == 0o700
    folder.chmod(0o755)
    with pytest.raises(spike.SpikeRefused, match="must be 0700"):
        spike.spike_folder(env.home)
    folder.chmod(0o700)
    fresh = env.tmp / "fresh-home"
    assert stat.S_IMODE(spike.spike_folder(fresh).stat().st_mode) == 0o700
    plaud = env.tmp / "plaud"
    monkeypatch.setattr(spike, "PLAUD_MOUNT", plaud)
    env.configure(f'[backup]\nextra_paths = ["{env.tmp / "backed-up"}"]\n')
    for home in (env.writer.root, env.writer.state.path, plaud, env.tmp / "backed-up"):
        with pytest.raises(spike.SpikeRefused, match="must be outside"):
            spike.spike_folder(home)
        assert not (home / "spike").exists()


def test_the_token_file_must_be_inside_the_spike_folder(env, spike, capsys):
    outside = env.tmp / "tokens.json"
    outside.write_text(json.dumps({"access_token": TOKEN}), encoding="utf-8")
    assert spike.main(["s3", "--home", str(env.home), "--token-file", str(outside)]) == 78
    assert "--token-file must be inside the spike folder" in capsys.readouterr().out
    assert env.fake.requests == []


@pytest.mark.parametrize(
    "args",
    [
        ["s2", "--token-url", "http://127.0.0.1:9/token"],
        ["s4", "--token-url", "ftp://auth.example/token"],
        ["s2"],
        ["s4", "--token-url", "https:///token"],
    ],
)
def test_s2_and_s4_refuse_a_token_url_that_isnt_https(env, spike, capsys, args):
    assert spike.main([*args, "--home", str(env.home)]) == 78
    assert "--token-url must be the https token URL" in capsys.readouterr().out
    assert env.fake.requests == []


def test_only_s2_and_s4_take_a_token_url_and_only_s9_the_own_clip(env, spike, capsys):
    assert spike.main(["s5", "--home", str(env.home), "--token-url", "https://a.example/t"]) == 78
    assert spike.main(["s8", "--home", str(env.home), "--own-clip", "--id", NORMAL]) == 78
    out = capsys.readouterr().out
    assert "--token-url is for S2 and S4 only" in out and "--own-clip is for S9 only" in out
    assert env.fake.requests == []


@pytest.mark.parametrize("which", ["no archive.json", "no recordings"])
def test_an_archive_that_cant_say_what_is_private_refuses_before_any_call(
    env, spike, capsys, which
):
    # why: S3. A wrong [archive] path, an unmounted disk or an empty archive would give an empty
    # set of private IDs, and a recording Dan tagged private would pass the guard.
    folder = env.tmp / "not-the-archive"
    if which == "no recordings":
        init_archive(folder, env.tmp / "not-the-archive-state", writer_id="test")
    else:
        folder.mkdir()
    env.configure(archive=folder)
    assert spike.main(["s5", "--home", str(env.home)]) == 78
    out = capsys.readouterr().out
    assert out.startswith("refused before any call:")
    assert env.fake.requests == []


def test_the_guard_adds_the_deployments_patterns_and_word_and_the_enforced_words(env, spike):
    # why: S3. The session's config is a hand copy, which can drift: the deployment's own patterns
    # and word, and every word the worker has enforced (state.db), only ever add to it.
    (env.repo / "config.toml").write_text(
        '[plaud]\nkeep_out_word = "prodword"\nauto_private_patterns = ["^synthetic-prod"]\n',
        encoding="utf-8",
    )
    with sqlite3.connect(env.writer.state.db_path) as db:  # what the worker records (Task 10)
        db.execute(
            "CREATE TABLE IF NOT EXISTS plaud_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT OR REPLACE INTO plaud_state VALUES ('keep_out_words', ?, ?)",
            (json.dumps(["oldword", "bad word!"]), T0.isoformat()),
        )
    guard = _guard(spike)
    assert guard.words == ("noarchive", "prodword", "oldword")  # an invalid stored word is skipped
    assert guard.patterns == (*DEFAULT_AUTO_PRIVATE, "^synthetic-prod")
    assert not guard.allows(NORMAL, "prodword: Week 4") and not guard.allows(NORMAL, "oldword 7")
    assert not guard.allows(NORMAL, "synthetic-prod notes")
    assert guard.report() == "refused: 3 (keep-out 2, pattern 1)"


def test_the_deployments_config_must_be_read_before_any_call(env, spike, capsys, monkeypatch):
    monkeypatch.delenv("REPO_DIR")
    assert spike.main(["s5", "--home", str(env.home)]) == 78
    assert "give --production-config" in capsys.readouterr().out
    missing = env.tmp / "absent.toml"
    assert spike.main(["s5", "--home", str(env.home), "--production-config", str(missing)]) == 78
    assert "can't be read" in capsys.readouterr().out
    assert env.fake.requests == []


def test_the_envelopes_own_title_is_checked_before_any_block(env, spike, capsys):
    # why: S7, §11.5. A title changed since the listing (or one the listing didn't give) is
    # checked on the envelope itself, before any block behind a link is fetched.
    _serve_account(env.fake, envelope_title="Private: synthetic")
    assert spike.main(["s8", "--home", str(env.home), "--id", NORMAL]) == 0
    out = capsys.readouterr().out
    assert f"{spike.short(NORMAL)}: refused by the guard" in out
    assert out.rstrip().endswith("refused: 1 (pattern 1)")
    assert [r.host for r in env.fake.requests if r.host == "storage"] == []


def test_the_own_clip_is_never_a_recording_in_the_archive(env, spike, capsys):
    # why: S8. --own-clip skips the keep-out check, so a mistyped --id must not reach an older
    # recording kept out by its word: the clip is new, so it isn't in the archive.
    _serve_account(env.fake)
    # why: exit 1, not 78: the listing was already read, and 78 means nothing was sent.
    assert spike.main(["s9", "--home", str(env.home), "--id", NORMAL, "--own-clip"]) == 1
    assert "--own-clip is for a clip that isn't in the archive" in capsys.readouterr().out
    assert FILE_PATH.format("of_" + NORMAL) not in [r.path for r in env.fake.requests]


def test_s9_finds_the_clip_by_the_name_it_printed(env, spike, capsys):
    # why: G4. The clip isn't in the archive and nothing prints a whole ID, so S9 takes the 8-hex
    # name its run without --id printed, and keeps the clip's ID in the spike folder for the runs
    # after it leaves the listing.
    now = datetime.now(UTC).isoformat()  # --own-clip needs a clip made in the last 24 hours
    clip = {"id": "of_" + CLIP, "name": "NoArchive synthetic clip", "start_at": now}
    older = {"id": "of_" + NORMAL, "name": TITLES[0], "start_at": "2026-08-01T16:00:00Z"}
    rows = [clip, older]  # the archive's recording is listed too: the two share an ID
    listed = {"rows": rows}
    env.fake.route(
        "GET",
        LIST_PATH,
        lambda q: Response(
            200, json.dumps({"data_list": listed["rows"] if q["page"] == ["1"] else []}).encode()
        ),
    )
    envelope = {"id": "of_" + CLIP, "name": clip["name"], "source_list": [], "note_list": []}
    body = json.dumps(envelope).encode()
    env.fake.route("GET", FILE_PATH.format("of_" + CLIP), lambda q: Response(200, body))
    name = spike.short(CLIP)
    assert spike.main(["s9", "--home", str(env.home)]) == 0
    assert f"{name}: keep-out word: yes" in capsys.readouterr().out
    assert spike.main(["s9", "--home", str(env.home), "--id", name, "--own-clip"]) == 0
    assert f"{name}: listed: yes" in capsys.readouterr().out
    listed["rows"] = [older]  # deleted to the trash: no longer listed
    assert spike.main(["s9", "--home", str(env.home), "--id", name, "--own-clip"]) == 0
    out = capsys.readouterr().out
    assert f"{name}: listed: no" in out and f"{name}: files/{{id}}: ok" in out
    assert CLIP not in out.lower() and "NoArchive" not in out


def test_s2_sends_the_client_id_and_reads_a_refusal_by_its_error_code(
    env, spike, capsys, monkeypatch
):
    # why: G3. A refresh missing a field Plaud's endpoint needs is refused with an OAuth error
    # code: that is a request to compare with the vendor CLI's own, not the design failing.
    env.tokens.write_text(
        json.dumps({"access_token": TOKEN, "refresh_token": REFRESH, "client_id": "synthetic-app"}),
        encoding="utf-8",
    )
    sent, answers = [], [(400, {"error": "invalid_client"}), (401, {})]
    monkeypatch.setattr(
        spike, "_post_form", lambda url, fields: sent.append(fields) or answers.pop(0)
    )
    args = ["s2", "--home", str(env.home), "--token-url", "https://auth.example/token"]
    assert spike.main(args) == 0
    first = capsys.readouterr().out
    assert sent == [
        {"grant_type": "refresh_token", "refresh_token": REFRESH, "client_id": "synthetic-app"}
    ]
    assert "error invalid_client" in first
    assert "compare this request with the vendor CLI's own refresh" in first
    assert spike.main(args) == 0
    second = capsys.readouterr().out
    # why: a refusal without a code is also read as "compare first": a request that differs from the
    # vendor CLI's own is the likelier cause. "Stop and ask Dan" is the runbook's, once they match.
    assert "compare this request with the vendor CLI's own refresh" in second
    assert "stop and ask Dan" not in first + second
    assert TOKEN not in first and REFRESH not in first and env.fake.requests == []


def test_an_unexpected_error_prints_its_class_only(env, spike, capsys, monkeypatch):
    # why: S9. Any exception, not only the script's own, prints its class and never its text,
    # which could hold a title or a URL.
    def boom(session):
        raise RuntimeError(WORDS_SAID)

    monkeypatch.setitem(spike.SPIKE_FUNCTIONS, "s5", boom)
    assert spike.main(["s5", "--home", str(env.home)]) == 1
    out = capsys.readouterr().out
    assert "stopped: RuntimeError" in out and WORDS_SAID not in out


# -- the fix round (Task 5's Tier 1 review) --------------------------------------------------------
def _serve_rows(fake, rows: list[dict]) -> None:
    fake.route(
        "GET",
        LIST_PATH,
        lambda q: Response(
            200, json.dumps({"data_list": rows if q["page"] == ["1"] else []}).encode()
        ),
    )


def _clip_rows(start_at: str, title: str = "NoArchive synthetic clip") -> list[dict]:
    """The throwaway clip, and one recording the archive holds too, so the two share an ID."""
    return [
        {"id": "of_" + CLIP, "name": title, "start_at": start_at},
        {"id": "of_" + NORMAL, "name": TITLES[0], "start_at": "2026-08-01T16:00:00Z"},
    ]


def test_s7_fetches_the_id_forms_only_for_a_recording_whose_envelope_title_passed(
    env, spike, capsys
):
    # why: §18 never fetches a private recording. The envelope's own title is what refuses it
    # here, and the ID-form step must not fetch files/{id} for the row it just refused.
    _serve_account(env.fake, envelope_title="Private: synthetic")
    assert spike.main(["s7", "--home", str(env.home), "--id", NORMAL]) == 0
    out = capsys.readouterr().out
    assert f"{spike.short(NORMAL)}: refused by the guard" in out
    assert "by its" not in out
    paths = [r.path for r in env.fake.requests]
    assert paths.count(FILE_PATH.format("of_" + NORMAL)) == 1  # the title check, and no more
    assert FILE_PATH.format(NORMAL) not in paths  # the bare form was never asked for


def test_the_own_clip_must_be_recent(env, spike, capsys):
    # why: --own-clip skips the keep-out check, and an old row kept out by its word was never
    # imported, so "not in the archive" doesn't tell it from Dan's new clip. It must be recent.
    _serve_rows(env.fake, _clip_rows(T0.isoformat(), title="NoArchive old recording"))
    body = json.dumps({"id": "of_" + CLIP, "name": "NoArchive old recording"}).encode()
    env.fake.route("GET", FILE_PATH.format("of_" + CLIP), lambda q: Response(200, body))
    args = ["s9", "--home", str(env.home), "--id", spike.short(CLIP), "--own-clip"]
    assert spike.main(args) == 1
    out = capsys.readouterr().out
    assert "stopped: --own-clip is for a clip that isn't in the archive" in out
    assert "last 24 hours" in out
    assert [r.path for r in env.fake.requests if r.path != LIST_PATH] == []


def test_s9_never_prints_a_note_tabs_name(env, spike, capsys):
    # why: a note tab's name or slug is Dan's. S8 prints "notes" for it, and so must S9.
    _serve_rows(env.fake, _clip_rows(datetime.now(UTC).isoformat()))
    calls = []

    def answer(query):
        calls.append(1)
        note = {
            "data_type": "auto_sum_note",
            "data_tab_name": "Counselling Notes",
            "data_content": "first" if len(calls) <= 2 else "second",  # checked, fetched, ...
        }
        return Response(
            200, json.dumps({"name": "x", "source_list": [], "note_list": [note]}).encode()
        )

    env.fake.route("GET", FILE_PATH.format("of_" + CLIP), answer)
    args = ["s9", "--home", str(env.home), "--id", spike.short(CLIP), "--own-clip"]
    assert spike.main(args) == 0
    out = capsys.readouterr().out
    assert "parts that differ a moment apart: notes" in out
    assert "counselling" not in out.lower() and "notes:" not in out


def test_the_token_url_posts_send_the_clients_user_agent(spike, monkeypatch):
    # why: bare urllib's default User-Agent gets a flat 403 from Plaud's API (client.py), which S2
    # and S4 would read as the design failing.
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            raise urllib.error.URLError("down")

    monkeypatch.setattr(spike.urllib.request, "build_opener", lambda *handlers: Opener())
    assert spike._post_form("https://auth.example/token", {"a": "b"}) == (None, {})
    assert seen[0].get_header("User-agent") == USER_AGENT


def test_s2_and_s4_say_unreachable_when_the_token_url_gives_no_status(
    env, spike, capsys, monkeypatch
):
    env.tokens.write_text(
        json.dumps({"access_token": TOKEN, "refresh_token": REFRESH}), encoding="utf-8"
    )
    monkeypatch.setattr(spike, "_post_form", lambda url, fields: (None, {}))
    for which in ("s2", "s4"):
        args = [which, "--home", str(env.home), "--token-url", "https://auth.example/token"]
        assert spike.main(args) == 0
        out = capsys.readouterr().out
        assert "unreachable" in out and "stop and ask Dan" not in out and "HTTP None" not in out


def test_a_listing_that_shares_no_recording_with_the_archive_stops_before_any_files_call(
    env, spike, capsys
):
    # why: the consent and private checks match by ID. A valid but wrong archive, or an ID form
    # that differs, would match nothing, and S8 and S11 would then run on titles alone.
    other = f"{0x99:032x}"
    _serve_rows(
        env.fake, [{"id": "of_" + other, "name": TITLES[0], "start_at": "2026-08-01T16:00:00Z"}]
    )
    for spike_name in ("s7", "s8", "s11"):
        args = [spike_name, "--home", str(env.home), "--id", other]
        assert spike.main(args) == 1
        out = capsys.readouterr().out
        assert "share no recording" in out and "refused before any call" not in out
    assert {r.path for r in env.fake.requests} == {LIST_PATH}


def test_s11_reports_content_length_from_the_range_probe(env, spike, capsys, monkeypatch):
    # why: §18 asks whether Plaud sends Content-Length. A missing one stops the download as
    # IncompleteDownload, the same as a stall, so the probe's header names are what say it.
    _serve_account(env.fake)
    env.fake.link("audio.mp3", b"synthetic audio " * 8)
    assert spike.main(["s11", "--home", str(env.home), "--id", NORMAL]) == 0
    assert "Content-Length: present" in capsys.readouterr().out
    monkeypatch.setattr(spike, "_raw_get", lambda url, headers: (200, ["Content-Type"]))
    assert spike.main(["s11", "--home", str(env.home), "--id", NORMAL]) == 0
    assert "Content-Length: absent" in capsys.readouterr().out


def test_exit_78_means_nothing_was_sent_and_1_means_something_was(env, spike, capsys):
    _serve_account(env.fake)
    for name in ("s7", "s8", "s11", "s12"):  # these need --id: checked before any call
        assert spike.main([name, "--home", str(env.home)]) == 78
        assert "--id" in capsys.readouterr().out
    assert env.fake.requests == []
    args = ["s9", "--home", str(env.home), "--id", NORMAL, "--own-clip"]
    assert spike.main(args) == 1  # the listing was read first: this is a stop, not a refusal
    out = capsys.readouterr().out
    assert out.startswith("stopped:") and "refused before any call" not in out


def test_s8_checks_the_title_on_every_envelope_it_fetches(env, spike, capsys):
    # why: one fetch's check never covers a later fetch: the second can have a new title, and its
    # blocks are fetched from the links in it.
    fetched = []

    def title():
        fetched.append(1)
        return TITLES[0] if len(fetched) <= 2 else "Private: synthetic"  # check, fetch, check

    _serve_account(env.fake, envelope_title=title)
    assert spike.main(["s8", "--home", str(env.home), "--id", NORMAL]) == 0
    out = capsys.readouterr().out
    assert f"{spike.short(NORMAL)}: refused by the guard" in out
    assert out.rstrip().endswith("refused: 1 (pattern 1)")
    assert len([r for r in env.fake.requests if r.host == "storage"]) == 1  # the first block only


def test_an_enforced_words_row_that_cant_be_read_refuses_before_any_call(env, spike, capsys):
    # why: unreadable words are unknown words, not none: the sync enforces them, the spike can't.
    with sqlite3.connect(env.writer.state.db_path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS plaud_state "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT OR REPLACE INTO plaud_state VALUES ('keep_out_words', 'not json', ?)",
            (T0.isoformat(),),
        )
    assert spike.main(["s5", "--home", str(env.home)]) == 78
    assert capsys.readouterr().out.startswith("refused before any call: the keep-out words")
    assert env.fake.requests == []
