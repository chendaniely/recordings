"""The plaud_request input and its plaud_result replies (2b spec §9.3; phase 1 plan, Task 20),
through shiny's test server against a real state.db. The worker's side is done by hand with
recordings.plaud.requests, as the worker would: claim, progress, finish. Phase 1's worker
handles Keep it here alone, so that is the kind these requests use."""

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import shinyreact
from recordings.archive import Archive
from recordings.plaud import requests
from recordings.plaud.flags import KEPT_HERE
from recordings.plaud.requests import NOT_YET
from recordings.state import State
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
