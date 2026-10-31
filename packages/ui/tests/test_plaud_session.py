"""The plaud_request input and its plaud_result replies (2b spec §9.3; phase 1 plan, Task 20),
through shiny's test server against a real state.db. The worker's side is done by hand with
recordings.plaud.requests, as the worker would: claim, progress, finish. Phase 1's worker
handles Keep it here alone, so that is the kind these requests use."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import shinyreact
from recordings.archive import Archive
from recordings.plaud import requests
from recordings.plaud.flags import KEPT_HERE
from recordings.plaud.requests import NOT_YET
from recordings.state import State
from recordings.worker import Progress, heartbeat_once
from recordings_ui import plaud, runtime
from recordings_ui.app import NO_CONFIG
from recordings_ui.plaud import PlaudViewSettings
from recordings_ui.tagging import MemoryStore, TagService
from shiny.testserver import test_server_async

SHINY_APP = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "shiny_app.py"
T0 = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)
RID = "20261023T090500+0000_0000beef"  # synthetic


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ThreadState(State):
    """Remembers the thread each of its state.db connections was opened on."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.threads: set[int] = set()

    def db(self, *args, **kwargs):
        self.threads.add(threading.get_ident())
        return super().db(*args, **kwargs)


def configure(writer, demo_archive, *, state, reason) -> PlaudViewSettings:
    """The runtime as create_app leaves it on a server (or, with no state, where it can't ask)."""
    settings = PlaudViewSettings(
        state_path=writer.state.path,
        timezone_name="UTC",
        schedule_minutes=20,
        full_sweep_days=7,
        auto_import=True,
        keep_out_word_ok=True,
        demo_view=None,
    )
    archive = Archive(demo_archive)
    runtime.configure(
        archive,
        tagging=TagService(MemoryStore(archive), progress_every=0),
        plaud=settings,
        plaud_state=state,
        plaud_reason=reason,
    )
    return settings


@pytest.fixture
def wired(writer, demo_archive, monkeypatch):
    state = ThreadState(writer.state.path)
    configure(writer, demo_archive, state=state, reason=None)
    monkeypatch.setattr(plaud, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(plaud, "clock", lambda: T0)
    sent: list[tuple[object, str, dict]] = []

    async def record(session, message_id, data):
        sent.append((session, message_id, data))

    monkeypatch.setattr(shinyreact, "send_message", record)
    yield SimpleNamespace(state=state, path=writer.state.path, sent=sent)
    runtime.configure(None)


def request(request_id: str, kind: str, **over) -> dict:
    return {
        "request_id": request_id,
        "kind": kind,
        "plaud_ids": [],
        "recording_ids": [],
        "all": False,
        **over,
    }


def replies(sent, request_id: str) -> list[dict]:
    return [d for _, i, d in sent if i == "plaud_result" and d["request_id"] == request_id]


async def until(check, what: str) -> None:
    for _ in range(500):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"never: {what}")


def rows(path: Path) -> int:
    with State(path).db() as db:
        return db.execute("SELECT COUNT(*) FROM plaud_requests").fetchone()[0]


def test_a_pending_row_is_read_every_two_seconds():
    assert plaud.POLL_SECONDS == 2.0  # spec §9.3; the session tests shorten it


@pytest.mark.anyio
async def test_a_request_is_inserted_off_the_event_loop(wired):
    # why: §9.3. The insert waits on SQLite's lock; on the event loop that would freeze every
    # window, and on the tag service's threads it would wait behind a tag batch (P6).
    loop_thread = threading.get_ident()
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("ins00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "ins00001test"), "a reply")
    threads = set(wired.state.threads)
    assert replies(wired.sent, "ins00001test")[0]["state"] == "queued"
    assert requests.read(State(wired.path), "ins00001test") is not None  # the row
    assert threads and loop_thread not in threads


@pytest.mark.anyio
async def test_a_pending_row_is_read_every_two_seconds_and_its_replies_sent(wired):
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("poll0001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "poll0001test"), "the first reply")
        worker = State(wired.path)
        assert requests.claim_next(worker, now=T0) is not None
        requests.progress(worker, "poll0001test", done=1, total=2)
        await until(
            lambda: any(r["progress"] for r in replies(wired.sent, "poll0001test")), "progress"
        )
        requests.finish(
            worker, "poll0001test", ok=True, message=KEPT_HERE, result={"kept": 1}, now=T0
        )
        await until(lambda: replies(wired.sent, "poll0001test")[-1]["state"] == "done", "the end")
        await asyncio.sleep(0.1)  # ten more polls' worth: the poll has stopped
    states = [r["state"] for r in replies(wired.sent, "poll0001test")]
    assert states[0] == "queued" and "running" in states and states.count("done") == 1
    assert states[-1] == "done"
    running = next(r for r in replies(wired.sent, "poll0001test") if r["state"] == "running")
    assert running["progress"] == {"done": 1, "total": 2}
    final = replies(wired.sent, "poll0001test")[-1]
    # No Release preview is built in Phase 1: the key is there, empty (types.ts's PlaudResult).
    assert (final["message"], final["result"], final["preview"]) == (KEPT_HERE, {"kept": 1}, None)


@pytest.mark.anyio
async def test_a_kind_the_worker_cant_handle_yet_is_refused_at_once_and_writes_nothing(wired):
    # why: Phase 1's worker handles Keep it here alone. Any other kind, crafted past the
    # disabled buttons, is answered "Comes with the next part of stage 2b." at once, and no
    # row is inserted, so nothing comes back later as an error.
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("not00001test", "compare"))
        await until(lambda: replies(wired.sent, "not00001test"), "a reply")
        await asyncio.sleep(0.1)
    (reply,) = replies(wired.sent, "not00001test")
    assert (reply["state"], reply["message"], reply["preview"]) == ("refused", NOT_YET, None)
    assert rows(wired.path) == 0


@pytest.mark.anyio
async def test_a_request_the_backstop_answered_not_saved_is_still_followed(wired, monkeypatch):
    # why: Task 14's backstop answers NOT_SAVED for a commit that hangs past the deadline, and
    # that one row may still land: the session looks for it and shows it if it does.
    real = plaud.submit

    async def hung(state, raw, **kw):
        reply = await real(state, raw, **kw)  # the row lands
        return {**reply, "state": "refused", "message": plaud.NOT_SAVED}  # the press heard this

    monkeypatch.setattr(plaud, "submit", hung)
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("late0001test", "keep-here", recording_ids=[RID]))
        await until(lambda: len(replies(wired.sent, "late0001test")) >= 2, "the landed row")
    first, landed, *_ = replies(wired.sent, "late0001test")
    assert (first["state"], first["message"]) == ("refused", plaud.NOT_SAVED)
    assert landed["state"] == "queued"


@pytest.mark.anyio
async def test_a_crafted_request_without_a_writable_state_is_refused(
    writer, demo_archive, monkeypatch
):
    # why: §9.3. With no config.toml (or in the demos, or the mirror's copy) there is no writable
    # state.db: the buttons are off, and a request crafted anyway is refused, writing nothing.
    configure(writer, demo_archive, state=None, reason=NO_CONFIG)
    sent: list[tuple[object, str, dict]] = []

    async def record(session, message_id, data):
        sent.append((session, message_id, data))

    monkeypatch.setattr(shinyreact, "send_message", record)
    try:
        async with test_server_async(SHINY_APP, timeout_secs=10) as s:
            await s.set_inputs(
                plaud_request=request("cra00001test", "keep-here", recording_ids=[RID])
            )
            await until(lambda: replies(sent, "cra00001test"), "a reply")
            await asyncio.sleep(0.1)
    finally:
        runtime.configure(None)
    (reply,) = replies(sent, "cra00001test")
    assert (reply["state"], reply["message"]) == ("refused", NO_CONFIG)
    assert rows(writer.state.path) == 0


def beat(state, at: datetime) -> None:
    heartbeat_once(state, Progress("idle", 1, at), now=at)


@pytest.mark.anyio
async def test_running_and_expired_replies_carry_the_workers_phrases(wired):
    # why: the worker writes the phrase in the same UPDATE, so a running reply no longer says
    # "The Plaud worker isn't running…" and an expired one no longer says "Queued" (review I1).
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("run00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "run00001test"), "the first queued")
        await s.set_inputs(plaud_request=request("exp00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "exp00001test"), "both queued")
        worker = State(wired.path)
        assert requests.claim_next(worker, now=T0) is not None  # the oldest
        await until(
            lambda: any(r["state"] == "running" for r in replies(wired.sent, "run00001test")),
            "running",
        )
        assert requests.expire(worker, now=T0 + timedelta(minutes=61)) == 1
        await until(lambda: replies(wired.sent, "exp00001test")[-1]["state"] == "expired", "expiry")
    running = next(r for r in replies(wired.sent, "run00001test") if r["state"] == "running")
    assert running["message"] == requests.RUNNING
    assert replies(wired.sent, "exp00001test")[-1]["message"] == requests.EXPIRED


@pytest.mark.anyio
async def test_a_worker_that_stops_after_the_press_is_said_once_and_unsaid_when_back(
    wired, monkeypatch
):
    # why: §9.3. The insert's "Queued" would stay until the tab closed (review I2).
    now = [T0]
    monkeypatch.setattr(plaud, "clock", lambda: now[0])
    beat(wired.state, T0)
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("down0001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "down0001test"), "queued")
        assert replies(wired.sent, "down0001test")[0]["message"] == requests.QUEUED
        now[0] = T0 + timedelta(minutes=5)  # its heartbeat is five minutes old
        await until(lambda: len(replies(wired.sent, "down0001test")) >= 2, "the worker down")
        await asyncio.sleep(0.1)  # many polls: the sentence is sent once, not every tick
        assert [r["message"] for r in replies(wired.sent, "down0001test")] == [
            requests.QUEUED,
            requests.worker_down("14:20"),
        ]
        beat(wired.state, now[0])
        await until(lambda: len(replies(wired.sent, "down0001test")) >= 3, "the worker back")
    assert replies(wired.sent, "down0001test")[-1]["message"] == requests.QUEUED


@pytest.mark.anyio
async def test_one_failed_read_does_not_end_the_poll(wired, monkeypatch, caplog):
    # why: review I3. A busy or moved state.db for one tick must not lose the final reply, the
    # toast or the redraw. The class is logged once when reads fail and once when they recover.
    real = requests.read
    calls = [0]

    def flaky(state, request_id):
        calls[0] += 1
        if calls[0] in (1, 2):
            raise sqlite3.OperationalError("database is locked")
        return real(state, request_id)

    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        await s.set_inputs(plaud_request=request("flk00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "flk00001test"), "queued")
        monkeypatch.setattr(requests, "read", flaky)
        worker = State(wired.path)
        assert requests.claim_next(worker, now=T0) is not None
        requests.finish(worker, "flk00001test", ok=True, message=KEPT_HERE, now=T0)
        await until(lambda: replies(wired.sent, "flk00001test")[-1]["state"] == "done", "the end")
    assert calls[0] >= 3
    failures = [r.getMessage() for r in caplog.records if "reading its row" in r.getMessage()]
    assert failures == [
        "plaud request: reading its row failed (OperationalError)",
        "plaud request: reading its row works again",
    ]


@pytest.mark.anyio
async def test_arrivals_count_from_the_later_of_the_start_and_the_last_refresh(wired, monkeypatch):
    # why: decision 22.
    now = [T0]
    monkeypatch.setattr(plaud, "clock", lambda: now[0])
    seen: list[datetime] = []

    def spy(settings, *, since, now):
        seen.append(since)
        return {"new": 0, "private": 0, "updated": 0, "kept_out_late": 0, "consent_late": 0}

    monkeypatch.setattr(plaud, "arrivals", spy)
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        s.get_output("plaud_arrivals")
        assert seen[-1] == T0
        now[0] = T0 + timedelta(minutes=10)
        await s.set_inputs(refresh=1)
        s.get_output("plaud_arrivals")
        await until(lambda: seen[-1] == T0 + timedelta(minutes=10), "since moves to the Refresh")
    assert seen[0] == T0


@pytest.mark.anyio
async def test_plaud_sync_is_drawn_again_when_a_request_ends(wired, monkeypatch):
    # why: so SyncView.last_result follows this window's requests (spec §9.3).
    drawn = [0]

    def spy(settings, *, now, can_request):
        drawn[0] += 1
        return {"n": drawn[0]}

    monkeypatch.setattr(plaud, "sync_view", spy)
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        s.get_output("plaud_sync")
        await s.set_inputs(plaud_request=request("syn00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "syn00001test"), "queued")
        assert drawn[0] == 1  # pending: not drawn again
        worker = State(wired.path)
        assert requests.claim_next(worker, now=T0) is not None
        requests.finish(worker, "syn00001test", ok=True, message=KEPT_HERE, now=T0)
        await until(lambda: replies(wired.sent, "syn00001test")[-1]["state"] == "done", "the end")
        await until(lambda: drawn[0] > 1, "the redraw")


@pytest.mark.anyio
async def test_arrivals_are_drawn_again_when_a_request_ends(wired, monkeypatch):
    # why: P2. After a Keep it here the header's flags line must not still count the flag it
    # cleared; the output reads the same signal `recording` and `plaud_sync` do.
    drawn = [0]

    def spy(settings, *, since, now):
        drawn[0] += 1
        return {"new": 0, "private": 0, "updated": 0, "kept_out_late": 0, "consent_late": 0}

    monkeypatch.setattr(plaud, "arrivals", spy)
    async with test_server_async(SHINY_APP, timeout_secs=10) as s:
        s.get_output("plaud_arrivals")
        await s.set_inputs(plaud_request=request("arr00001test", "keep-here", recording_ids=[RID]))
        await until(lambda: replies(wired.sent, "arr00001test"), "queued")
        assert drawn[0] == 1  # pending: not drawn again
        worker = State(wired.path)
        assert requests.claim_next(worker, now=T0) is not None
        requests.finish(worker, "arr00001test", ok=True, message=KEPT_HERE, now=T0)
        await until(lambda: replies(wired.sent, "arr00001test")[-1]["state"] == "done", "the end")
        await until(lambda: drawn[0] > 1, "the redraw")
