import time
from pathlib import Path

import pytest
import shinyreact
from recordings.archive import Archive
from recordings.models import TagRef
from recordings.tagops import AddTags, Recorded
from recordings_ui import runtime, views
from recordings_ui.tagging import SNIPPETS_FAILED, TOO_MANY_SNIPPETS, MemoryStore, TagService
from shiny.testserver import TestServerSession

SHINY_APP = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "shiny_app.py"
pytestmark = pytest.mark.parametrize("local_server", [str(SHINY_APP)], indirect=True)


@pytest.fixture(autouse=True)
def _archive(request, demo_archive):
    # A test can pass a media base with @pytest.mark.parametrize("_archive", [...], indirect=True).
    archive = Archive(demo_archive)
    service = TagService(MemoryStore(archive), progress_every=0)
    runtime.configure(archive, media_base=getattr(request, "param", None), tagging=service)
    yield service
    runtime.configure(None)


class Sent(list):
    """Records what shinyreact.send_message would send: (message id, data)."""

    async def __call__(self, session, message_id, data):
        self.append((message_id, data))


@pytest.fixture
def sent(monkeypatch) -> Sent:
    recorder = Sent()
    monkeypatch.setattr(shinyreact, "send_message", recorder)
    return recorder


def wait_for(local_server, sent: Sent, message_id: str, request_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = [
            d
            for i, d in sent
            if i == message_id and d["request_id"] == request_id and d.get("status") != "progress"
        ]
        if found:
            return found[-1]
        local_server.flush()  # runs the loop, where the extended task does its work
        time.sleep(0.02)
    raise AssertionError(f"no {message_id} reply to {request_id}: {sent}")


def tag_request(request_id: str, rid: str) -> dict:
    return {
        "request_id": request_id,
        "action": "add",
        "ids": [rid],
        "tags": ["talks"],
        "confirm_ids": [],
    }


def test_library_is_published(local_server: TestServerSession):
    assert local_server.get_output("library").value["counts"] == {"all": 4, "untagged": 1}


def test_nothing_selected_is_silent(local_server: TestServerSession):
    assert local_server.get_output("recording").status == "silent"


def test_selecting_publishes_the_recording(local_server: TestServerSession, demo_ids):
    local_server.set_inputs(selected_id=demo_ids["jfk-rice"])
    assert local_server.get_output("recording").value["id"] == demo_ids["jfk-rice"]


def test_clearing_the_selection_publishes_none(local_server: TestServerSession):
    local_server.set_inputs(selected_id=None)
    assert local_server.get_output("recording").value is None


def test_the_server_route_is_the_default_media_url(local_server: TestServerSession, demo_ids):
    local_server.set_inputs(selected_id=demo_ids["jfk-rice"])
    assert (
        local_server.get_output("recording").value["media_url"] == f"/media/{demo_ids['jfk-rice']}"
    )


@pytest.mark.parametrize("_archive", ["../media/"], indirect=True)
def test_the_configured_media_base_reaches_the_recording(local_server: TestServerSession, demo_ids):
    local_server.set_inputs(selected_id=demo_ids["jfk-rice"])
    url = local_server.get_output("recording").value["media_url"]
    assert url == f"../media/{demo_ids['jfk-rice']}.mp3"


def test_status_is_published_and_unconfigured_in_the_demo(local_server: TestServerSession):
    status = local_server.get_output("status").value
    assert status["configured"] is False and status["disk"] is None  # demo mode: no disk figure


def test_the_library_says_whether_tagging_works_and_saves(local_server: TestServerSession):
    view = local_server.get_output("library").value
    assert view["can_tag"] is True and view["saves"] is False  # the memory store


def test_a_tag_request_is_applied_and_answered_by_message(local_server, sent, demo_ids):
    rid = demo_ids["apollo13-problem"]
    local_server.set_inputs(tag_request=None)  # the mount default: skipped (ignore_none)
    local_server.set_inputs(tag_request=tag_request("wire0001test", rid))
    reply = wait_for(local_server, sent, "tag_result", "wire0001test")
    assert reply["status"] == "done" and reply["rows"][0]["id"] == rid
    assert reply["rows"][0]["tags"] == [{"tag": "talks", "by": "you"}]
    assert [i for i, _ in sent] == ["tag_result"]


def test_a_request_that_is_the_inputs_first_value_still_runs(local_server, sent, demo_ids):
    # why: spec §5. A first request that coalesced with the mount default reaches the server as
    # the input's first value, with no null before it: ignore_init would swallow it; ignore_none
    # doesn't.
    local_server.set_inputs(tag_request=tag_request("wire0002test", demo_ids["apollo13-problem"]))
    assert wait_for(local_server, sent, "tag_result", "wire0002test")["status"] == "done"


def test_refresh_rebuilds_the_view_and_its_mount_default_does_not(local_server, _archive, demo_ids):
    first = local_server.get_output("library").value
    rid = demo_ids["apollo13-problem"]
    _archive.store.apply(rid, Recorded(AddTags((TagRef(tag="talks", by="you"),)), "elsewhere"))
    local_server.set_inputs(refresh=None)
    assert local_server.get_output("library").value == first
    local_server.set_inputs(refresh=1)
    assert local_server.get_output("library").value["counts"]["untagged"] == 0


def test_snippets_are_answered_as_plain_text(local_server, sent, demo_ids):
    jfk = demo_ids["jfk-rice"]
    local_server.set_inputs(snippet_request={"request_id": "snip0001test", "ids": [jfk]})
    reply = wait_for(local_server, sent, "snippets", "snip0001test")
    assert reply["message"] is None
    text = reply["snippets"][jfk]
    assert text and len(text) <= 160 and "\n" not in text


def test_too_many_snippet_ids_are_refused(local_server, sent):
    ids = [f"20200101T000000+0000_{i:08x}" for i in range(201)]
    local_server.set_inputs(snippet_request={"request_id": "snip0002test", "ids": ids})
    reply = wait_for(local_server, sent, "snippets", "snip0002test")
    assert reply["snippets"] == {} and reply["message"] == TOO_MANY_SNIPPETS


def test_a_snippet_read_that_fails_still_replies(local_server, sent, demo_ids, monkeypatch):
    # why: the browser asks for one batch of snippets at a time and waits for its reply, so a
    # lost reply would stop every later snippet on that page.
    def broken(*_args, **_kwargs):
        raise RuntimeError("Week 4's transcript is unreadable")

    monkeypatch.setattr(views, "snippet", broken)
    jfk = demo_ids["jfk-rice"]
    local_server.set_inputs(snippet_request={"request_id": "snip0003test", "ids": [jfk]})
    reply = wait_for(local_server, sent, "snippets", "snip0003test")
    assert reply["snippets"] == {} and reply["message"] == SNIPPETS_FAILED
