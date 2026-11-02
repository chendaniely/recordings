"""Release (stage 2b spec §12.2, §11.4 step 6; area A4): its preview, written to the cache only and
shown once; Let it in, checked again against the consent list and the current title; Undo; the
refusals, each saying why; and `recordings plaud release` with its y/N. Through the worker's
request handlers and the CLI, against the fake Plaud server. Every ID and title is synthetic."""

import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from plaud_scene import Tokens
from recordings.cli import main
from recordings.config import load_config
from recordings.init import init_archive
from recordings.plaud import requests
from recordings.plaud.cache import Cache, take_preview
from recordings.plaud.checks import K_CATCH_UP_DONE, K_COMPARE, K_HOLD, PlaudDB
from recordings.plaud.client import PlaudClient
from recordings.plaud.compare import TITLES_HIDDEN_IDS, Conditions, Here, classify
from recordings.plaud.gate import GATE_REFUSAL
from recordings.plaud.importer import record_kept_out
from recordings.plaud.keepout import parse_consent
from recordings.plaud.release import (
    ASK,
    NO_TITLE_HELD,
    NO_TITLE_REKEYED,
    NOTHING,
    REFUSED_CONSENT,
    REFUSED_STOP,
    TOO_LATE,
    UNDONE,
    refused_word,
)
from recordings.plaud.requests import PlaudRequest
from recordings.state import State
from recordings.worker import Progress, Worker, heartbeat_once

# Before the real clock: the CLI releases at `datetime.now`, which must be after the keep-out.
T0 = datetime(2026, 10, 2, 14, 20, tzinfo=UTC)
TZ = "America/Vancouver"
NEVER_LISTED = "f" * 32


@pytest.fixture
def setup(tmp_path, ntfy, plaud_server):
    root, state_dir = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state_dir, writer_id="test")
    consent = tmp_path / "consent" / "consent-list.md"
    consent.parent.mkdir()
    consent.write_text(f"- `of_{NEVER_LISTED}` — synthetic\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\ndefault_timezone = "{TZ}"\n'
        f'[state]\npath = "{state_dir}"\n[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{consent}"\napi_base = "{plaud_server.api_url}"\n',
        encoding="utf-8",
    )
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_DEADMAN_SYNC_URL": f"{ntfy.url}/s"}
    db = PlaudDB(State.open(state_dir))
    db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    account = plaud_server.account()
    return SimpleNamespace(
        tmp=tmp_path,
        environ=environ,
        db=db,
        consent=consent,
        state=state_dir,
        account=account,
        server=plaud_server,
    )


@pytest.fixture
def worker(setup):
    w = Worker(
        load_config(setup.environ),
        setup.environ,
        clock=lambda: T0,
        heartbeat=3600,
        client_factory=lambda: PlaudClient(Tokens(), api_base=setup.server.api_url),
    )
    w.start()
    heartbeat_once(w.state, Progress("idle", 1, T0), now=T0)
    yield w
    w.stopping.set()


def kept_out(setup, name: str = "noarchive synthetic grant chat", *, now_title: str | None = None):
    """A recording kept out by its title, which (now_title) has since lost the word."""
    rec = setup.account.add(name)
    record_kept_out(setup.db, f"of_{rec.plaud_id}", "noarchive", now=T0)
    if now_title is not None:
        setup.account.rename(rec.plaud_id, now_title)
    return rec


def ask(w: Worker, kind: str, rec, n: int) -> requests.Reply:
    request_id = f"request-{n:04d}"
    req = PlaudRequest(request_id, kind, plaud_ids=(f"of_{rec.plaud_id}",))
    requests.insert(w.state, req, now=T0, timezone_name=TZ)
    w.tick()
    return requests.read(w.state, request_id)


def envelope_requests(setup) -> int:
    """`files/{id}` requests: the listing's own path is the bare prefix."""
    prefix = "/open/third-party/files/"
    return sum(1 for r in setup.server.requests if r.path.startswith(prefix) and r.path != prefix)


def previews(setup) -> int:
    with Cache(setup.state).db() as conn:
        return conn.execute("SELECT count(*) FROM release_preview").fetchone()[0]


def test_the_preview_shows_the_title_once_and_only_through_the_cache(setup, worker):
    # §12.2 step 1 (C5.06's server half): the title is written to the cache only, keyed by the
    # request's own ID, and the web app's read deletes it.
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message, reply.result) == ("done", ASK, {"plaud_id": rec.plaud_id})
    assert "Synthetic" not in json.dumps(reply.to_dict())
    with setup.db.state.db() as conn:
        rows = conn.execute("SELECT message, result FROM plaud_requests").fetchall()
    assert "Synthetic" not in repr(rows)
    preview = take_preview(setup.state, "request-0001", now=T0)
    assert (preview.title, preview.canonical_plaud_id) == (
        "Synthetic grant timelines",
        rec.plaud_id,
    )
    assert take_preview(setup.state, "request-0001", now=T0) is None  # shown once
    assert envelope_requests(setup) == 0  # read from the listing, never from its envelope


def test_an_unanswered_preview_goes_after_ten_minutes(setup, worker):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    ask(worker, "release-preview", rec, 1)
    worker.clock = lambda: T0 + timedelta(minutes=9)
    worker._expire_previews()
    assert previews(setup) == 1
    worker.clock = lambda: T0 + timedelta(minutes=11)
    worker._expire_previews()
    assert previews(setup) == 0
    assert take_preview(setup.state, "request-0001", now=T0 + timedelta(minutes=11)) is None


def test_the_tick_deletes_a_preview_the_consent_list_names_since(setup, worker):
    # §9.5: the preview's title goes as soon as the list or a word catches it.
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    ask(worker, "release-preview", rec, 1)
    assert previews(setup) == 1
    setup.consent.write_text(f"- `of_{rec.plaud_id}` — synthetic\n", encoding="utf-8")
    worker._purged = None  # a list read again at this tick
    worker.tick()
    assert previews(setup) == 0


def test_let_it_in_releases_it_and_undo_keeps_it_out_again(setup, worker):
    # §12.2 steps 3 and 4; C5.05: it stays out until Release, then the compare reads it waiting.
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    reply = ask(worker, "release", rec, 1)
    assert reply.state == "done" and reply.message.startswith(
        "Released · comes in at the next sync"
    )
    row = setup.db.listing(rec.plaud_id)
    assert row.released_at == T0
    listed = worker.listed_now(rec.plaud_id)
    consent = parse_consent(setup.consent.read_text(encoding="utf-8"))
    (classified,) = classify(
        [listed],
        here=Here(by_id={}),
        consent=consent,
        words=("noarchive",),
        listing={rec.plaud_id: row},
        keyed_out={},
        patterns=(),
        conditions=Conditions(),
    )
    assert classified.bucket == "waiting"
    undo = ask(worker, "unrelease", rec, 2)
    assert (undo.state, undo.message) == ("done", UNDONE)
    assert setup.db.listing(rec.plaud_id).released_at is None


@pytest.mark.parametrize("kind", ["release-preview", "release"])
def test_release_is_refused_while_the_title_still_carries_the_word(setup, worker, kind):
    # C5.07: refused, saying why; the preview never shows such a title.
    rec = kept_out(setup)  # its title still starts with the word
    reply = ask(worker, kind, rec, 1)
    assert (reply.state, reply.message) == ("failed", refused_word("noarchive"))
    assert take_preview(setup.state, "request-0001", now=T0) is None
    assert setup.db.listing(rec.plaud_id).released_at is None


@pytest.mark.parametrize("kind", ["release-preview", "release"])
def test_release_is_refused_for_a_consent_listed_id_with_nothing_fetched(setup, worker, kind):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.consent.write_text(f"- `of_{rec.plaud_id}` — synthetic\n", encoding="utf-8")
    before = len(setup.server.requests)
    reply = ask(worker, kind, rec, 1)
    assert (reply.state, reply.message) == ("failed", REFUSED_CONSENT)
    assert len(setup.server.requests) == before  # no listing, so no title, was fetched


def test_release_is_refused_during_a_consent_stop(setup, worker):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.consent.write_text("- `of_" + "a" * 31 + "` — a typo\n", encoding="utf-8")
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message) == ("failed", REFUSED_STOP)


def test_release_waits_for_the_first_catch_up(setup, worker):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.db.delete(K_CATCH_UP_DONE)
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message) == ("refused", GATE_REFUSAL)


def test_nothing_to_release_for_an_id_not_kept_out(setup, worker):
    rec = setup.account.add("Synthetic standup")
    reply = ask(worker, "release", rec, 1)
    assert (reply.state, reply.message) == ("failed", NOTHING)


def test_a_re_keyed_match_released_resolves_its_flag(setup, worker):
    # §11.4 step 6: a possible re-keyed recording that is a different one: Release lets it in.
    rec = setup.account.add("Synthetic retro")
    setup.db.set_listing(f"of_{rec.plaud_id}", rekey_of="a" * 32, first_seen=T0)
    setup.db.add_event(
        "rekeyed", now=T0, plaud_id=rec.plaud_id, detail={"of": "kept-out"}, open_flag=True
    )
    assert ask(worker, "release", rec, 1).state == "done"
    assert setup.db.open_flags(("rekeyed",)) == []
    assert ask(worker, "unrelease", rec, 2).message == UNDONE
    (flag,) = setup.db.open_flags(("rekeyed",))
    assert flag.canonical_plaud_id == rec.plaud_id


def test_undo_is_too_late_once_it_came_in(setup, worker, monkeypatch):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    ask(worker, "release", rec, 1)
    monkeypatch.setattr(Worker, "_here", lambda self, plaud_id: True)
    assert ask(worker, "unrelease", rec, 2).message == TOO_LATE


def test_a_released_id_whose_title_takes_the_word_again_is_kept_out_again(setup):
    # Kept out stays kept out: after Release, a title with the word keeps it out again, and
    # losing the word once more doesn't let it in without another Release.
    from recordings.plaud.compare import BY_TITLE, BY_WAS_TITLE, compare, record_compare

    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.db.set_listing(f"of_{rec.plaud_id}", released_at=T0 + timedelta(seconds=1))
    cache = Cache(setup.state)
    consent = parse_consent(setup.consent.read_text(encoding="utf-8"))

    def listing_with(title: str, at: datetime):
        setup.account.rename(rec.plaud_id, title)
        client = PlaudClient(Tokens(), api_base=setup.server.api_url)
        from recordings.sources.plaud import PlaudSource

        rows = PlaudSource(client, clock=lambda: at).list_all().rows
        before = setup.db.listing_rows()
        classified = classify(
            rows,
            here=Here(by_id={}),
            consent=consent,
            words=("noarchive",),
            listing=before,
            keyed_out={},
            patterns=(),
            conditions=Conditions(),
        )
        result = compare(
            classified, at=at, here=Here(by_id={}), consent=consent, conditions=Conditions()
        )
        record_compare(setup.db, cache, result, consent=consent, listing=before, now=at)
        return classified[0]

    assert listing_with("noarchive: synthetic, again", T0 + timedelta(minutes=20)).excluded_by == (
        BY_TITLE
    )
    assert listing_with("Synthetic, without it", T0 + timedelta(minutes=40)).excluded_by == (
        BY_WAS_TITLE
    )


def run_cli(setup, monkeypatch, *args: str, answer: str = "y\n") -> tuple[int, str, str]:
    import contextlib

    for key, value in setup.environ.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    monkeypatch.setattr(
        "recordings.plaud.commands._release_client",
        lambda settings: PlaudClient(Tokens(), api_base=setup.server.api_url),
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["plaud", "release", *args])
    return code, out.getvalue(), err.getvalue()


def test_the_cli_asks_y_n_showing_no_title_then_releases(setup, monkeypatch):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    code, out, err = run_cli(setup, monkeypatch, f"of_{rec.plaud_id}")
    assert code == 0, err
    assert "Synthetic" not in out + err and rec.plaud_id in out
    assert "Released" in out
    assert setup.db.listing(rec.plaud_id).released_at is not None
    code, out, err = run_cli(setup, monkeypatch, f"of_{rec.plaud_id}", "--undo")
    assert code == 0 and UNDONE in out
    assert setup.db.listing(rec.plaud_id).released_at is None


def test_the_cli_changes_nothing_without_a_yes(setup, monkeypatch):
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    code, out, _err = run_cli(setup, monkeypatch, f"of_{rec.plaud_id}", answer="\n")
    assert code == 1 and "Nothing changed." in out
    assert setup.db.listing(rec.plaud_id).released_at is None


def test_the_cli_refuses_a_title_that_still_carries_the_word(setup, monkeypatch):
    rec = kept_out(setup)
    code, out, err = run_cli(setup, monkeypatch, f"of_{rec.plaud_id}")
    assert code == 1 and refused_word("noarchive") in out + err
    assert "synthetic grant chat" not in out + err


# ---- the end-of-branch privacy review (2026-11-02): a re-keyed match, and an IDs-changed hold ---
def rekeyed(setup, name: str = "Synthetic retro"):
    """A listed ID the compare matched to an absent consent-listed one by start time and duration
    (§11.4 step 6): flagged, kept out, its title never stored or shown."""
    rec = setup.account.add(name)
    setup.db.set_listing(f"of_{rec.plaud_id}", rekey_of="a" * 32, first_seen=T0)
    setup.db.add_event(
        "rekeyed", now=T0, plaud_id=rec.plaud_id, detail={"of": "consent"}, open_flag=True
    )
    return rec


def test_a_re_keyed_match_s_preview_is_refused_and_nothing_is_cached(setup, worker):
    # §11.4 step 6: a re-keyed match's "title is never stored or shown"; Release's question then
    # shows no title. So its preview fetches none, writes no cache row and its reply carries none.
    rec = rekeyed(setup)
    before = len(setup.server.requests)
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message) == ("failed", NO_TITLE_REKEYED)
    assert len(setup.server.requests) == before  # no listing read for it
    assert previews(setup) == 0
    assert take_preview(setup.state, "request-0001", now=T0) is None
    assert "Synthetic" not in json.dumps(reply.to_dict())


def test_a_re_keyed_match_is_let_in_without_its_title_and_still_checked(setup, worker):
    # Let it in on a re-keyed row goes straight to `release`: still checked against the consent
    # list as it is now and its current title against every enforced word (§12.2 step 3), and the
    # reply names no title either way.
    rec = rekeyed(setup, "noarchive synthetic retro")
    reply = ask(worker, "release", rec, 1)
    assert (reply.state, reply.message) == ("failed", refused_word("noarchive"))
    assert "synthetic retro" not in json.dumps(reply.to_dict())
    assert setup.db.listing(rec.plaud_id).released_at is None
    setup.account.rename(rec.plaud_id, "Synthetic retro")
    worker.newest_listing = None  # the listing read again
    setup.consent.write_text(f"- `of_{rec.plaud_id}` — synthetic\n", encoding="utf-8")
    reply = ask(worker, "release", rec, 2)
    assert (reply.state, reply.message) == ("failed", REFUSED_CONSENT)
    setup.consent.write_text(f"- `of_{NEVER_LISTED}` — synthetic\n", encoding="utf-8")
    reply = ask(worker, "release", rec, 3)
    assert reply.state == "done" and "Synthetic" not in json.dumps(reply.to_dict())
    assert setup.db.listing(rec.plaud_id).released_at == T0
    assert previews(setup) == 0


@pytest.mark.parametrize("ids_changed", [True, "unreadable"])
def test_no_preview_while_a_held_listing_shows_plauds_ids_changed(setup, worker, ids_changed):
    # §11.4 step 3: while a held listing shows a census change or under 80% overlap, no title of a
    # recording not here is stored or shown, a review row's Show title included. A record that
    # can't say reads as changed (fail closed); so does the newest Compare's own measure.
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.db.put(K_HOLD, {"ids_changed": ids_changed, "sentence": "held"}, now=T0)
    before = len(setup.server.requests)
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message) == ("failed", NO_TITLE_HELD)
    assert len(setup.server.requests) == before
    assert previews(setup) == 0
    setup.db.delete(K_HOLD)
    setup.db.put(K_COMPARE, {"titles_hidden": TITLES_HIDDEN_IDS}, now=T0)
    reply = ask(worker, "release-preview", rec, 2)
    assert (reply.state, reply.message) == ("failed", NO_TITLE_HELD)
    assert previews(setup) == 0


def test_a_large_shrink_hold_still_previews(setup, worker):
    # Only a hold that shows Plaud's IDs changed hides titles (§11.4 step 3).
    rec = kept_out(setup, now_title="Synthetic grant timelines")
    setup.db.put(K_HOLD, {"ids_changed": False, "sentence": "held"}, now=T0)
    reply = ask(worker, "release-preview", rec, 1)
    assert (reply.state, reply.message) == ("done", ASK)
