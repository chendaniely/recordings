"""Requests from the UI (spec §9.3): the rows, and the worker's claims and handlers. Phase 1's
worker handles Keep it here alone; every other kind comes with a later phase. Synthetic IDs only;
nothing here reaches Plaud."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from recordings import worker as worker_module
from recordings.archive import RawSource
from recordings.config import load_config
from recordings.init import init_archive
from recordings.plaud import requests
from recordings.plaud.checks import K_CATCH_UP_DONE
from recordings.plaud.flags import KEPT_HERE, NOTHING_TO_KEEP, record_late_keep_out
from recordings.plaud.gate import GATE_REFUSAL
from recordings.plaud.requests import (
    HANDLED,
    INTERRUPTED,
    KINDS,
    NOT_YET,
    QUEUED,
    PlaudRequest,
    queued_after,
    worker_down,
)
from recordings.plaud.runlock import plaud_run
from recordings.state import State
from recordings.worker import Progress, Worker, heartbeat_once
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
TZ = "America/Vancouver"  # 12:00 UTC is 05:00 there in October
PLAUD_A = "of_" + f"{1:032x}"  # synthetic Plaud IDs
PLAUD_C = "of_" + f"{3:032x}"
R1, R2, R3 = (f"20261006T14000{n}-0700_{n:08x}" for n in (1, 2, 3))  # synthetic recording IDs


def req(kind: str, n: int, **fields) -> PlaudRequest:
    return PlaudRequest(request_id=f"request-{n:04d}", kind=kind, **fields)


def up(state: State, now: datetime = T0) -> None:
    """The worker reports in: a fresh heartbeat, as its thread writes one every 30 seconds."""
    heartbeat_once(state, Progress("idle", 1, now), now=now)


def insert(state: State, request: PlaudRequest, now: datetime = T0) -> requests.Reply:
    return requests.insert(state, request, now=now, timezone_name=TZ)


def rows(state: State) -> list[tuple]:
    with state.db() as db:
        return db.execute("SELECT kind, state FROM plaud_requests ORDER BY id").fetchall()


@pytest.fixture
def state(tmp_path) -> State:
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    return State.open(tmp_path / "state")


@pytest.fixture
def setup(tmp_path, ntfy):
    """An archive whose consent list reads, a config.toml, and a worker's environment."""
    root, state_dir = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state_dir, writer_id="test")
    consent = tmp_path / "consent"
    consent.mkdir()
    (consent / "consent-list.md").write_text(f"- `{PLAUD_C}` — synthetic\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{state_dir}"\n[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{consent / "consent-list.md"}"\n',
        encoding="utf-8",
    )
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_DEADMAN_SYNC_URL": f"{ntfy.url}/s"}
    return SimpleNamespace(root=root, tmp=tmp_path, environ=environ)


@pytest.fixture
def make_worker(setup):
    made: list[Worker] = []

    def make() -> Worker:
        w = Worker(load_config(setup.environ), setup.environ, clock=lambda: T0, heartbeat=3600)
        w.start()
        up(w.state)
        made.append(w)
        return w

    yield make
    for w in made:
        w.stopping.set()


def add_recording(setup, plaud_id: str) -> str:
    writer = Writer.open(load_config(setup.environ), docs=False)
    media = setup.tmp / "in.mp3"
    media.write_bytes(b"ID3 synthetic audio")
    return writer.add(
        Incoming(
            media=media,
            recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
            timezone_name="America/Vancouver",
            time_source="plaud",
            title="Week 4",
            kind="audio",
            sources=(RawSource(kind="plaud", ref=plaud_id, added_at=T0, payload=b'{"id": 1}'),),
        )
    ).recording.id


def answer(state: State, n: int) -> tuple:
    reply = requests.read(state, f"request-{n:04d}")
    return reply.state, reply.message


def test_phase_1_handles_keep_it_here_alone():
    # why: decision 2. A button whose handler comes in a later phase is disabled with NOT_YET,
    # and a crafted request for it is refused before any row is inserted.
    assert HANDLED == {"keep-here"} == set(worker_module.HANDLERS)
    assert HANDLED.issubset(KINDS)
    assert NOT_YET == "Comes with the next part of stage 2b."


def test_a_repeated_request_id_is_that_row(state):
    up(state)
    first = insert(state, req("keep-here", 1, recording_ids=(R1,)))
    again = insert(state, req("keep-here", 1, recording_ids=(R2,)))
    assert again == first == requests.Reply("request-0001", "keep-here", "queued", QUEUED)
    with state.db() as db:
        assert db.execute("SELECT recording_ids FROM plaud_requests").fetchall() == [
            (json.dumps([R1]),)
        ]


SESSION = """
import sys
from datetime import datetime
from recordings.plaud import requests
from recordings.state import State
state = State.open(sys.argv[1])
sys.stdin.read()  # both are started; they insert together once stdin closes
request = requests.PlaudRequest(sys.argv[2], "keep-here", recording_ids=(sys.argv[3],))
now = datetime.fromisoformat(sys.argv[4])
print(requests.insert(state, request, now=now, timezone_name="UTC").message, flush=True)
"""


def test_one_request_id_sent_by_two_sessions_at_once_is_one_row(state):
    # why: spec §9.3. A browser that reconnects can send its request again while the first is
    # being saved: BEGIN IMMEDIATE makes it one row, and both get that row's reply.
    up(state)
    sessions = [
        subprocess.Popen(
            [sys.executable, "-c", SESSION, str(state.path), "request-0007", rid, T0.isoformat()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=os.environ,
        )
        for rid in (R1, R2)
    ]
    for session in sessions:
        session.stdin.close()
    replies = [session.communicate(timeout=30)[0].strip() for session in sessions]
    assert [session.returncode for session in sessions] == [0, 0]
    assert replies == [QUEUED, QUEUED]
    assert rows(state) == [("keep-here", "queued")]


def test_a_request_while_a_run_holds_plaud_says_it_waits_for_it(state):
    up(state)
    with plaud_run(state, "plaud-baseline", timeout=0, now=T0 - timedelta(minutes=40)):
        reply = insert(state, req("keep-here", 1, recording_ids=(R1,)))
    assert (reply.state, reply.message) == ("queued", queued_after("04:20"))


def test_with_the_worker_down_the_reply_says_so_and_the_row_expires_after_an_hour(state):
    assert insert(state, req("keep-here", 1, recording_ids=(R1,))).message == worker_down("never")
    up(state, T0 - timedelta(minutes=5))  # its last heartbeat, at 04:55 in Vancouver
    reply = insert(state, req("keep-here", 2, recording_ids=(R2,)))
    assert (reply.state, reply.message) == ("queued", worker_down("04:55"))
    assert requests.expire(state, now=T0 + timedelta(minutes=59)) == 0
    assert requests.expire(state, now=T0 + timedelta(minutes=61)) == 2
    assert answer(state, 2) == ("expired", worker_down("04:55"))


def test_at_start_running_rows_fail_as_interrupted_and_old_queued_rows_expire(state):
    up(state)
    insert(state, req("keep-here", 1, recording_ids=(R1,)))
    requests.claim_next(state, now=T0)
    insert(state, req("keep-here", 2, recording_ids=(R2,)))
    soon = T0 + timedelta(minutes=50)
    up(state, soon)
    insert(state, req("keep-here", 3, recording_ids=(R3,)), now=soon)
    assert requests.at_start(state, now=T0 + timedelta(minutes=70)) == (1, 1)
    assert answer(state, 1) == ("failed", INTERRUPTED)
    assert answer(state, 2)[0] == "expired"
    assert answer(state, 3)[0] == "queued"


def test_the_worker_claims_the_oldest_and_keeps_a_late_keep_out_here(setup, make_worker):
    # C5.18 and C5.21's other half: before the first catch-up, Keep it here still runs (it writes
    # no snapshot). It resolves the open late keep-out; with none open, there is nothing to keep.
    rid = add_recording(setup, PLAUD_A)
    w = make_worker()
    assert not w.gate.open
    record_late_keep_out(w.db, rid, [PLAUD_A], "noarchive", now=T0)
    insert(w.state, req("keep-here", 1, recording_ids=(rid,)))
    insert(w.state, req("keep-here", 2, recording_ids=(rid,)))
    w.tick()
    assert answer(w.state, 1) == ("done", KEPT_HERE)
    assert answer(w.state, 2) == ("queued", QUEUED)  # one request per tick, the oldest first
    w.tick()
    assert answer(w.state, 2) == ("failed", NOTHING_TO_KEEP)
    assert requests.read(w.state, "request-0001").result == {"kept": [rid], "nothing_to_keep": []}


def test_a_kind_the_worker_cant_handle_yet_is_refused_never_run(setup, make_worker):
    # C5.18 and C5.21: web refuses these before inserting; a row that got in anyway (a hand-made
    # row) is refused by the worker with the same phrase, and nothing runs, gate open or closed.
    # No request ever answers "error: `NotImplementedError`".
    w = make_worker()
    later = [k for k in KINDS if k not in HANDLED]
    for n, kind in enumerate(later, start=10):
        insert(w.state, req(kind, n, plaud_ids=(PLAUD_A,)))
        w.tick()
        assert answer(w.state, n) == ("refused", NOT_YET), kind
    w.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)  # the gate opens
    insert(w.state, req("import", 99, plaud_ids=(PLAUD_A,)))
    w.tick()
    assert answer(w.state, 99) == ("refused", NOT_YET)


def test_the_worker_checks_the_gate_again_before_a_handler_runs(setup, make_worker, monkeypatch):
    # C5.21: every kind that could write a snapshot waits for the first catch-up, whatever the
    # browser sent. Phase 1 handles none of them, so a stand-in handler for `import` shows the
    # check Phase 2's handlers rely on.
    ran = []

    def stand_in(worker, claimed):
        ran.append(claimed.request.request_id)
        return requests.Reply(claimed.request.request_id, "import", "done", "Imported: 1")

    monkeypatch.setitem(worker_module.HANDLERS, "import", stand_in)
    w = make_worker()
    insert(w.state, req("import", 1, plaud_ids=(PLAUD_A,)))
    w.tick()
    assert answer(w.state, 1) == ("refused", GATE_REFUSAL) and ran == []
    w.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    insert(w.state, req("import", 2, plaud_ids=(PLAUD_A,)))
    w.tick()
    assert answer(w.state, 2) == ("done", "Imported: 1") and ran == ["request-0002"]


def test_finished_and_refused_rows_are_pruned_after_7_days(state):
    up(state)
    for n in (1, 2):
        insert(state, req("keep-here", n, recording_ids=(R1,)))
    requests.finish(state, "request-0001", ok=True, message=KEPT_HERE, now=T0)
    requests.refuse(state, "request-0002", message=NOT_YET, now=T0)
    assert requests.prune(state, now=T0 + timedelta(days=6)) == 0
    assert requests.prune(state, now=T0 + timedelta(days=8)) == 2
    assert rows(state) == []
