"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import itertools
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
