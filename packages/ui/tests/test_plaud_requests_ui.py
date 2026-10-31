"""The web app's side of Plaud requests (stage 2b spec §9.3): the checks, the reasons it can't
ask, and the insert off the event loop. Synthetic IDs only."""

import asyncio
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime

import pytest
from recordings.config import load_config
from recordings.init import init_archive
from recordings.plaud import requests
from recordings.plaud.requests import KINDS, MAX_PLAUD_IDS, MAX_RESYNC_IDS, NOT_YET, PlaudRequest
from recordings.sentinel import MIRROR
from recordings.state import State
from recordings_ui.plaud import (
    BAD_ID,
    DEMO_REASON,
    MIRROR_REASON,
    NO_CONFIG_REASON,
    NO_STATE_REASON,
    NOT_SAVED,
    TOO_MANY_PLAUD,
    TOO_MANY_RESYNC,
    UNKNOWN_KIND,
    UNREADABLE,
    Refusal,
    cannot_request_reason,
    check_request,
    submit,
)
from recordings_ui.settings import Settings

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
RID = "20261006T140003-0700_3fa91c2e"  # synthetic
PLAUD_A = "of_" + f"{1:032x}"


def raw(**over) -> dict:
    return {
        "request_id": "abcdef12",
        "kind": "keep-here",
        "plaud_ids": [],
        "recording_ids": [RID],
        "all": False,
        **over,
    }


def count(state: State) -> int:
    with state.db() as db:
        return db.execute("SELECT COUNT(*) FROM plaud_requests").fetchone()[0]


@pytest.fixture
def archive(tmp_path):
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\n[state]\npath = "{state}"\n',
        encoding="utf-8",
    )
    return root, State.open(state), load_config({"RECORDINGS_CONFIG": str(cfg_file)})


def test_check_request_refuses_unknown_kinds_bad_ids_caps_and_extra_keys():
    assert check_request(raw(recording_ids=[RID, RID], plaud_ids=[PLAUD_A, PLAUD_A])) == (
        PlaudRequest("abcdef12", "keep-here", (PLAUD_A,), (RID,), False)
    )
    assert check_request(raw(kind="delete")) == Refusal("abcdef12", UNKNOWN_KIND)
    assert check_request(raw(kind=["sync"])) == Refusal("abcdef12", UNKNOWN_KIND)
    assert check_request(raw(title="Week 4")) == Refusal("abcdef12", UNREADABLE)  # never a title
    assert check_request(raw(request_id="short")) == Refusal(None, UNREADABLE)
    assert check_request("not a request") == Refusal(None, UNREADABLE)
    assert check_request(raw(all="yes")) == Refusal("abcdef12", UNREADABLE)
    # a Plaud ID is opaque: visible ASCII and no space, so a title never fits in one
    for bad in ([""], ["x" * 129], [7], ["of_\n1"], ["Week 4: private"], ["of_é1"]):
        assert check_request(raw(plaud_ids=bad)) == Refusal("abcdef12", BAD_ID), bad
    for bad in (["not-an-id"], [None]):
        assert check_request(raw(recording_ids=bad)) == Refusal("abcdef12", BAD_ID)
    many = [f"of_{n:032x}" for n in range(MAX_PLAUD_IDS + 1)]
    assert check_request(raw(kind="import", plaud_ids=many)) == Refusal("abcdef12", TOO_MANY_PLAUD)
    resync = raw(kind="resync", recording_ids=[RID] * (MAX_RESYNC_IDS + 1))
    assert check_request(resync) == Refusal("abcdef12", TOO_MANY_RESYNC)


def test_id_lists_shiny_hands_over_as_tuples_are_read():
    # why: Shiny hands an array inside an input value over as a tuple (its lists_to_tuples), so a
    # real browser's request holds tuples where the tests above send lists. A lone string isn't a
    # list of IDs.
    sent = raw(recording_ids=(RID, RID), plaud_ids=(PLAUD_A,))
    assert check_request(sent) == PlaudRequest("abcdef12", "keep-here", (PLAUD_A,), (RID,), False)
    assert check_request(raw(recording_ids=RID)) == Refusal("abcdef12", UNREADABLE)
    assert check_request(raw(plaud_ids=PLAUD_A)) == Refusal("abcdef12", UNREADABLE)


def test_a_kind_with_no_handler_yet_is_refused_before_any_row(archive, run):
    # C5.18: before the first catch-up an import request is refused; in Phase 1 every kind but
    # Keep it here waits for a later phase, so web refuses it with NOT_YET and inserts nothing.
    _, state, _ = archive
    later = [k for k in KINDS if k != "keep-here"]
    assert "import" in later and len(later) == 12
    for kind in later:
        assert check_request(raw(kind=kind)) == Refusal("abcdef12", NOT_YET), kind
        reply = run(submit(state, raw(kind=kind), now=T0, timezone_name="UTC", reason=None))
        assert (reply["state"], reply["message"]) == ("refused", NOT_YET), kind
    assert count(state) == 0


def test_no_writable_state_refuses_with_a_reason(archive, run):
    root, state, cfg = archive
    real = Settings(archive=root, demo=False, config=cfg)
    assert cannot_request_reason(Settings(archive=root, demo=True), state) == DEMO_REASON
    assert cannot_request_reason(Settings(archive=root, demo=False), state) == NO_CONFIG_REASON
    assert cannot_request_reason(real, None) == NO_STATE_REASON
    assert cannot_request_reason(real, state) is None
    (root / MIRROR).write_text("synthetic: this folder is the mirror's copy\n", encoding="utf-8")
    assert cannot_request_reason(real, state) == MIRROR_REASON
    reply = run(submit(state, raw(), now=T0, timezone_name="UTC", reason=DEMO_REASON))
    assert (reply["state"], reply["message"]) == ("refused", DEMO_REASON)
    reply = run(submit(None, raw(), now=T0, timezone_name="UTC", reason=None))
    assert (reply["state"], reply["message"]) == ("refused", NO_STATE_REASON)
    # the demo's reason comes before NOT_YET, as the buttons show it
    reply = run(submit(state, raw(kind="compare"), now=T0, timezone_name="UTC", reason=DEMO_REASON))
    assert (reply["state"], reply["message"]) == ("refused", DEMO_REASON)
    assert count(state) == 0


def test_a_request_that_cant_be_saved_answers_at_once(tmp_path, run):
    # Review Focus 5: a lost mount or a full disk answers within a second (spec P6), with a fixed
    # phrase: never a spinner or a traceback.
    gone = State(tmp_path / "unmounted")
    reply = run(
        asyncio.wait_for(submit(gone, raw(), now=T0, timezone_name="UTC", reason=None), 1.0)
    )
    assert reply == {
        "request_id": "abcdef12",
        "kind": "keep-here",
        "state": "refused",
        "message": NOT_SAVED,
        "progress": None,
        "result": None,
    }


def test_a_busy_state_db_answers_within_a_second_and_saves_nothing(archive, run):
    # why: spec P6. Another writer holding state.db's lock (the worker, a migration) would make
    # the insert wait 30 seconds; it gives up within the second instead, and no row lands later.
    _, state, _ = archive
    with closing(sqlite3.connect(state.db_path, isolation_level=None)) as other:
        other.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        reply = run(submit(state, raw(), now=T0, timezone_name="UTC", reason=None))
        took = time.monotonic() - started
        other.execute("ROLLBACK")
    assert (reply["state"], reply["message"]) == ("refused", NOT_SAVED)
    assert took < 1.5
    assert count(state) == 0


def test_an_insert_that_hangs_answers_within_a_second(archive, run, monkeypatch):
    # why: spec P6. Whatever holds the insert up (a stalled disk), the press is answered.
    _, state, _ = archive
    release = threading.Event()

    def stalled(*args, **kwargs):
        release.wait(10)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(requests, "insert", stalled)

    async def press():
        started = time.monotonic()
        reply = await submit(state, raw(), now=T0, timezone_name="UTC", reason=None)
        took = time.monotonic() - started
        release.set()  # the stalled thread may end now (asyncio.run waits for it)
        return reply, took

    reply, took = run(press())
    assert (reply["state"], reply["message"]) == ("refused", NOT_SAVED)
    assert took < 1.5


def test_submit_inserts_off_the_event_loop_never_on_the_tag_threads(archive, run, monkeypatch):
    _, state, _ = archive
    seen = {}
    real_insert = requests.insert

    def spy(state, request, **kwargs):
        seen["thread"] = threading.current_thread()
        return real_insert(state, request, **kwargs)

    monkeypatch.setattr(requests, "insert", spy)
    reply = run(submit(state, raw(), now=T0, timezone_name="UTC", reason=None))
    assert reply["state"] == "queued"
    assert seen["thread"] is not threading.main_thread()
    assert not seen["thread"].name.startswith("recordings-")  # the tag service's threads
    assert requests.read(state, "abcdef12").kind == "keep-here"


def test_the_request_checks_import_without_sqlite3():
    # why: the Pages demo's Pyodide has no sqlite3, fcntl or threads (stage-2a decision 6), and
    # its shiny_app imports this module. (CPython's own asyncio imports fcntl, so the check names
    # our modules that would bring it: recordings.locks, and the worker.)
    code = (
        "import sys, recordings_ui.plaud\n"
        "names = ('sqlite3', 'recordings.state', 'recordings.locks', 'recordings.worker')\n"
        "print([m for m in names if m in sys.modules])\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"
