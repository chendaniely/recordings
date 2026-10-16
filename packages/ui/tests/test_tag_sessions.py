"""Two windows on one event loop (tagging spec §9, "The writer thread"): a batch blocked on the
writer thread never holds up another window's reactive work. And one window's requests run one at
a time, and a request cancelled at shutdown ends cancelled with what landed (Task 5's carries)."""

import asyncio
import threading
from pathlib import Path

import pytest
import shinyreact
from recordings.archive import Archive
from recordings_ui import runtime
from recordings_ui.tagging import STOP_CANCELLED, ArchiveStore, TagService, TagSession
from shiny.testserver import test_server_async

SHINY_APP = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "shiny_app.py"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class BlockingStore(ArchiveStore):
    """Its writes wait for `release`, as one recording of a long batch would."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def apply(self, recording_id, op):
        self.entered.set()
        self.release.wait(30)
        return super().apply(recording_id, op)


@pytest.mark.anyio
async def test_a_blocked_batch_in_one_window_never_holds_up_another(tag_archive, monkeypatch):
    store = BlockingStore(Archive(tag_archive.root), tag_archive.open_writer)
    archive = Archive(tag_archive.root)
    runtime.configure(archive, tagging=TagService(store, progress_every=0))
    sent: list[tuple[str, dict]] = []

    async def record(session, message_id, data):
        sent.append((message_id, data))

    monkeypatch.setattr(shinyreact, "send_message", record)
    request = {
        "request_id": "block0001test",
        "action": "add",
        "ids": [tag_archive.ids["untagged"]],
        "tags": ["work"],
        "confirm_ids": [],
    }
    try:
        async with (
            test_server_async(SHINY_APP, timeout_secs=10) as a,
            test_server_async(SHINY_APP, timeout_secs=10) as b,
        ):
            await a.set_inputs(tag_request=request)
            assert await asyncio.to_thread(store.entered.wait, 10)  # A's batch is on the thread
            rid = tag_archive.ids["plain"]
            await b.set_inputs(selected_id=rid)
            assert b.get_output("recording").value["id"] == rid  # B is not held up
            store.release.set()
            for _ in range(500):
                if any(d.get("status") == "done" for _, d in sent):
                    break
                await asyncio.sleep(0.02)
            assert [d["status"] for _, d in sent][-1] == "done"
    finally:
        store.release.set()
        store.close()
        runtime.configure(None)


class Blocked:
    """A BlockingStore behind the runtime's tag service, what it sent, and the asyncio task each
    TagSession.run ran in (the session's extended task), in the order they started."""

    def __init__(self, tag_archive, monkeypatch) -> None:
        self.store = BlockingStore(Archive(tag_archive.root), tag_archive.open_writer)
        runtime.configure(
            Archive(tag_archive.root), tagging=TagService(self.store, progress_every=0)
        )
        self.sent: list[tuple[str, dict]] = []
        self.started: list[tuple[str, asyncio.Task]] = []
        self.running = self.most = 0

        async def record(session, message_id, data):
            self.sent.append((message_id, data))

        run = TagSession.run

        async def counted(session, job, send):
            self.started.append((job.request_id, asyncio.current_task()))
            self.running += 1
            self.most = max(self.most, self.running)
            try:
                await run(session, job, send)
            finally:
                self.running -= 1

        monkeypatch.setattr(shinyreact, "send_message", record)
        monkeypatch.setattr(TagSession, "run", counted)

    def done(self) -> list[str]:
        return [
            d["request_id"] for i, d in self.sent if i == "tag_result" and d["status"] == "done"
        ]


@pytest.fixture
def blocked(tag_archive, monkeypatch):
    made = Blocked(tag_archive, monkeypatch)
    yield made
    made.store.release.set()
    made.store.close()
    runtime.configure(None)


def add(request_id: str, rid: str) -> dict:
    return {"request_id": request_id, "action": "add", "ids": [rid], "tags": ["work"]}


@pytest.mark.anyio
async def test_one_windows_requests_run_one_at_a_time_in_order(tag_archive, blocked):
    # why: a session's state (its rows, the IDs its last confirm reply named, its Undo tokens) is
    # read and written by TagSession.run without a lock, so two runs at once would interleave.
    # shiny's extended task queues an invocation behind a running one (_extended_task.py).
    async with test_server_async(SHINY_APP, timeout_secs=10) as a:
        await a.set_inputs(tag_request=add("order0001test", tag_archive.ids["untagged"]))
        assert await asyncio.to_thread(blocked.store.entered.wait, 10)
        await a.set_inputs(tag_request=add("order0002test", tag_archive.ids["plain"]))
        await asyncio.sleep(0.1)
        assert [rid for rid, _ in blocked.started] == ["order0001test"]  # the second waits
        blocked.store.release.set()
        for _ in range(500):
            if len(blocked.done()) == 2:
                break
            await asyncio.sleep(0.02)
    assert blocked.done() == ["order0001test", "order0002test"]
    assert blocked.most == 1


@pytest.mark.anyio
async def test_a_request_cancelled_at_shutdown_ends_cancelled_with_what_landed(
    tag_archive, blocked
):
    # why: at shutdown the event loop cancels what still runs. The service keeps what landed and
    # re-raises; the wiring must let the CancelledError through, so the extended task ends
    # cancelled, not as an error, and nothing in it waits on.
    rid = tag_archive.ids["untagged"]
    async with test_server_async(SHINY_APP, timeout_secs=10) as a:
        await a.set_inputs(tag_request=add("stop0001test", rid))
        assert await asyncio.to_thread(blocked.store.entered.wait, 10)
        ((_, task),) = blocked.started
        task.cancel()  # as asyncio.run does to the tasks left at shutdown
        threading.Timer(0.2, blocked.store.release.set).start()  # the write ends after the cancel
        await asyncio.wait({task}, timeout=10)
        assert task.cancelled()
    ((message_id, reply),) = blocked.sent
    assert message_id == "tag_result" and reply["status"] == "failed"
    assert reply["message"] == STOP_CANCELLED and reply["undo"]
    assert [r["id"] for r in reply["rows"]] == [rid]
    assert tag_archive.tags("untagged") == ["work"]
