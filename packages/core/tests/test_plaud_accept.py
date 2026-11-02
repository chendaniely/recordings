"""Accept this listing's two doors (stage 2b spec §11.4 step 4, §9.6; area A3): the worker's
`accept` request handler and `recordings plaud accept-listing`; and the worker's tick hiding the
Plaud cache's titles the consent list or a keep-out word now catches (§9.5, the outline's P1).
Every ID and title is synthetic; nothing here reaches Plaud."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from recordings.cli import main
from recordings.config import load_config
from recordings.init import init_archive
from recordings.plaud import requests
from recordings.plaud.cache import Cache, CompareRow
from recordings.plaud.checks import (
    K_CATCH_UP_DONE,
    K_HOLD,
    K_LAST_GOOD_LISTING,
    LISTED_GOOD,
    PlaudDB,
)
from recordings.plaud.compare import Here
from recordings.plaud.gate import GATE_REFUSAL
from recordings.plaud.hold import (
    ACCEPT_REFUSED,
    CODE_FIX,
    NOTHING_HELD,
    measure_against,
    record_verdict,
)
from recordings.plaud.requests import PlaudRequest
from recordings.plaud.runlock import plaud_run
from recordings.state import State
from recordings.worker import Progress, Worker, heartbeat_once

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
TZ = "America/Vancouver"


def pid(n: int) -> str:
    return f"{0xACCE0000 + n:032x}"


CONSENTED = pid(99)


@pytest.fixture
def setup(tmp_path, ntfy):
    root, state_dir = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state_dir, writer_id="test")
    consent = tmp_path / "consent" / "consent-list.md"
    consent.parent.mkdir()
    consent.write_text(f"- `of_{CONSENTED}` — synthetic\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{state_dir}"\n[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{consent}"\n',
        encoding="utf-8",
    )
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_DEADMAN_SYNC_URL": f"{ntfy.url}/s"}
    db = PlaudDB(State.open(state_dir))
    return SimpleNamespace(tmp=tmp_path, environ=environ, db=db, consent=consent, state=state_dir)


@pytest.fixture
def worker(setup):
    w = Worker(load_config(setup.environ), setup.environ, clock=lambda: T0, heartbeat=3600)
    w.start()
    heartbeat_once(w.state, Progress("idle", 1, T0), now=T0)
    yield w
    w.stopping.set()


def hold_a_listing(db: PlaudDB, *, flip: bool = False, drop_consented: bool = False) -> None:
    """A good listing of 40 recordings here and the consent-listed ID, then one 6 fewer (a large
    shrink), or (flip) in the other form with the consent-listed ID gone too."""
    here = Here(by_id={pid(n): frozenset({f"rec-{n:03d}"}) for n in range(40)})
    first = {pid(n): "of_" for n in range(40)} | {CONSENTED: "of_"}
    m = measure_against(first, last_good=None, here=here, excluded=frozenset({CONSENTED}))
    assert record_verdict(db, first, m, at=T0, now=T0) == "good"
    form = "hex" if flip else "of_"
    later = {pid(n): form for n in range(6, 40)}
    if not drop_consented:
        later[CONSENTED] = form
    m = measure_against(later, last_good=first, here=here, excluded=frozenset({CONSENTED}))
    assert record_verdict(db, later, m, at=T0 + timedelta(minutes=20), now=T0) == "held"


def ask(w: Worker, n: int = 1) -> requests.Reply:
    requests.insert(w.state, PlaudRequest(f"request-{n:04d}", "accept"), now=T0, timezone_name=TZ)
    w.tick()
    return requests.read(w.state, f"request-{n:04d}")


def test_the_ui_accept_waits_for_the_first_catch_up(setup, worker):
    # §11.6: "the UI's Accept stays refused before the first catch-up"; the CLI accepts it.
    hold_a_listing(setup.db)
    reply = ask(worker)
    assert (reply.state, reply.message) == ("refused", GATE_REFUSAL)
    assert setup.db.get(K_HOLD) is not None


def test_accept_clears_a_hold_and_marks_its_vanished_recordings_gone(setup, worker):
    setup.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    hold_a_listing(setup.db)
    reply = ask(worker)
    assert reply.state == "done" and reply.message.startswith("Accepted this listing: 6 ")
    assert sorted(reply.result["gone"]) == [f"rec-{n:03d}" for n in range(6)]
    assert setup.db.get(K_HOLD) is None and len(setup.db.listed(LISTED_GOOD)) == 35
    again = ask(worker, 2)
    assert (again.state, again.message) == ("done", NOTHING_HELD)


def test_accept_is_refused_when_a_consent_listed_id_vanished_with_an_id_change(setup, worker):
    setup.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    hold_a_listing(setup.db, flip=True, drop_consented=True)
    reply = ask(worker)
    assert (reply.state, reply.message) == ("failed", ACCEPT_REFUSED)
    assert reply.result == {
        "gone": [],
        "why": "Fix this first: 1 consent-listed or kept-out ID vanished while Plaud's IDs "
        "changed (runbook)",
    }
    assert setup.db.get(K_HOLD) is not None


def test_accept_answers_busy_while_another_run_holds_plaud(setup, worker):
    setup.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    hold_a_listing(setup.db)
    with plaud_run(worker.state, "plaud-catch-up", timeout=0, now=T0):
        reply = ask(worker)
    assert (reply.state, reply.message) == ("failed", "skipped: busy (`plaud-catch-up`)")
    assert setup.db.get(K_HOLD) is not None


def test_the_cli_accepts_the_catch_ups_held_listing(setup, monkeypatch, capsys):
    # §11.6: the catch-up's listing, held against the archive, is accepted at the server.
    monkeypatch.setenv("RECORDINGS_CONFIG", setup.environ["RECORDINGS_CONFIG"])
    assert main(["plaud", "accept-listing"]) == 0
    assert capsys.readouterr().out.strip() == NOTHING_HELD
    hold_a_listing(setup.db)
    assert main(["plaud", "accept-listing", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "accepted" and len(out["gone"]) == 6
    assert setup.db.get(K_LAST_GOOD_LISTING)["count"] == 35


def test_the_cli_refuses_on_the_same_terms(setup, monkeypatch, capsys):
    monkeypatch.setenv("RECORDINGS_CONFIG", setup.environ["RECORDINGS_CONFIG"])
    hold_a_listing(setup.db, flip=True, drop_consented=True)
    assert main(["plaud", "accept-listing"]) == 1
    out = capsys.readouterr().out
    assert ACCEPT_REFUSED in out and "Fix this first: 1 consent-listed" in out
    setup.consent.write_text("", encoding="utf-8")  # a consent stop: nothing can be checked
    assert main(["plaud", "accept-listing", "--json"]) == 78
    assert json.loads(capsys.readouterr().out)["result"] == "refused"
    with plaud_run(State.open(setup.state), "plaud-sync", timeout=0, now=T0):
        monkeypatch.setattr("recordings.plaud.runlock.CLI_WAIT", 0.1)
        assert main(["plaud", "accept-listing"]) == 75


def test_an_unreadable_form_needs_a_code_fix_at_the_cli_too(setup, monkeypatch, capsys):
    monkeypatch.setenv("RECORDINGS_CONFIG", setup.environ["RECORDINGS_CONFIG"])
    forms = {pid(n): "of_" for n in range(10)} | {"synthetic-other-1": "other"}
    here = Here(by_id={pid(n): frozenset({f"rec-{n}"}) for n in range(10)})
    m = measure_against(forms, last_good=None, here=here, excluded=frozenset())
    assert record_verdict(setup.db, forms, m, at=T0, now=T0) == "held"
    assert main(["plaud", "accept-listing"]) == 1
    assert CODE_FIX in capsys.readouterr().out


def test_the_tick_hides_cache_titles_the_consent_list_now_names(setup, worker):
    # The outline's P1 (C5.15's cache half): a title stored by a compare, of an ID the consent
    # list names since, is nulled at the worker's next tick, before any view reads it.
    cache = Cache(setup.state)
    cache.replace_compare(
        [
            CompareRow(
                pid(n),
                "of_" + pid(n),
                "waiting",
                "imports at the next sync",
                None,
                T0,
                60_000,
                f"Synthetic meeting {n}",
                False,
                False,
            )
            for n in (1, 2)
        ],
        at=T0,
    )
    worker.tick()
    assert [r.title for r in cache.compare_rows()] == ["Synthetic meeting 1", "Synthetic meeting 2"]
    setup.consent.write_text(
        f"- `of_{CONSENTED}` — synthetic\n- `{pid(2)}` — added later\n", encoding="utf-8"
    )
    worker.tick()
    titles = {r.canonical_plaud_id: r.title for r in cache.compare_rows()}
    assert titles == {pid(1): "Synthetic meeting 1", pid(2): None}
    setup.consent.write_text("- `" + "a" * 31 + "` — a typo\n", encoding="utf-8")
    worker.tick()  # a consent stop: no title of a recording not here is kept
    assert {r.title for r in cache.compare_rows()} == {None}
