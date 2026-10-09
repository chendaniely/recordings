"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import itertools
import json
from datetime import datetime, timezone

import pytest

from recordings.archive import RawSource
from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LOCAL = datetime.fromisoformat("2026-10-06T14:00:03-07:00")


@pytest.fixture
def new_archive(tmp_path):
    """An initialised, empty archive and its state folder: (root, state_path)."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return root, state


@pytest.fixture
def make_writer(tmp_path):
    def make(name: str = "archive", writer_id: str = "test") -> Writer:
        root, state = tmp_path / name, tmp_path / f"{name}-state"
        init_archive(root, state, writer_id=writer_id)
        return Writer(root, State.open(state), Index(tmp_path / f"{name}-index" / "index.db"),
                      writer_id=writer_id)
    return make


@pytest.fixture
def writer(make_writer):
    return make_writer()


@pytest.fixture
def make_incoming(tmp_path):
    """An Incoming with a media file of its own. Pass `content` to make duplicates on purpose."""
    counter = itertools.count()

    def make(content: bytes | None = None, *, name: str = "clip.MP3", **over) -> Incoming:
        n = next(counter)
        path = tmp_path / "in" / f"{n}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else f"ID3 fake audio {n}".encode())
        fields = dict(
            media=path, recorded_at=LOCAL, timezone_name="America/Vancouver",
            time_source="plaud", title="Week 4", kind="audio",
            sources=(RawSource(kind="plaud", ref="of_" + "a" * 32, added_at=T0,
                               payload=b'{"id": 1}'),),
        )
        fields.update(over)
        return Incoming(**fields)
    return make


HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def plaud_env():
    """A synthetic Plaud `files/{id}` envelope, shaped like audio-router's raw snapshots: invented
    names, invented text, a rotating presigned URL and audio-router's own `_meta`. `polish` adds a
    `transaction_polish` block with the same rows, as Plaud's polished transcript has."""
    def make(fid: str = HEX, *, name: str = "Week 4", segments=None, notes=None,
             outline: bool = True, polish: bool = False, duration: int = 2600,
             link_error: bool = False, presigned: str = "abc") -> dict:
        rows = segments if segments is not None else [
            {"content": "Hello there.", "start_time": 0, "end_time": 1500, "speaker": "Ada",
             "original_speaker": "Speaker 1", "embeddingKey": "ek-1"},
            {"content": "Hi.", "start_time": 1500, "end_time": 2600, "speaker": "Speaker 2",
             "original_speaker": "Speaker 2"},
        ]
        source_list = [{"data_type": "transaction", "data_id": "t-1", "data_tab_name": None,
                        "data_content": json.dumps(rows, ensure_ascii=False)}]
        if outline:
            source_list.append({"data_type": "outline", "data_id": "o-1", "data_tab_name": None,
                                "data_content": json.dumps(
                                    [{"topic": "Greetings", "start_time": 0, "end_time": 2600}])})
        if polish:
            source_list.append({"data_type": "transaction_polish", "data_id": "p-1",
                                "data_tab_name": None,
                                "data_content": json.dumps(rows, ensure_ascii=False)})
        note_list = notes if notes is not None else [
            {"data_type": "auto_sum_note", "data_id": "n-1", "data_tab_name": "Summary",
             "data_content": "# Summary\n\nTwo people say hello."},
            {"data_type": "high_light", "data_id": "h-1", "data_tab_name": None,
             "data_content": json.dumps([{"timestamp": 1000, "title": "Key moment",
                                          "content": "A highlight"}])},
        ]
        if link_error:
            note_list = [*note_list, {
                "data_type": "auto_sum_note", "data_id": "n-2", "data_tab_name": "Action items",
                "data_content": "", "data_link": "https://notes.example/n2?X-Amz-Expires=300",
                "_data_link_error": "HTTP Error 403: Forbidden"}]
        return {
            "id": fid, "name": name, "duration": duration,
            "start_at": "2026-08-01T16:00:00Z", "created_at": "2026-08-01T16:05:00Z",
            "serial_number": "TEST0001",
            "presigned_url": f"https://audio.example/x.mp3?X-Amz-Expires=300&X-Amz-Signature={presigned}",
            "source_list": source_list, "note_list": note_list,
            "_meta": {"tool": "audio-router", "enriched_from_links": []},
        }
    return make
